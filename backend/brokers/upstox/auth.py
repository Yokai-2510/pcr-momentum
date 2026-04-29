"""
brokers.upstox.auth — Upstox OAuth2 + access-token-request + token-validity probe.

Stateless. All functions take literal arguments and return either standard
envelopes (REST helpers) or plain values (cache + predicate helpers).

Endpoints:
  POST /v2/login/authorization/token        — exchange auth_code → access_token
  POST /v3/login/auth/token/request/{cid}   — initiate user-approved flow
  GET  /v2/user/profile                     — cheap remote-validity probe

Notes:
  - Long-lived "Analytics Token" (1y, read-only) is generated manually from
    the Upstox Developer Apps → Analytics tab; this module's `is_token_valid_remote`
    works on that token too. Trading writes still require the daily OAuth token.
  - Playwright + pyotp imports are deferred so unit tests don't need them.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from datetime import time as dt_time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse
from zoneinfo import ZoneInfo

from brokers.upstox._http import bearer_form, bearer_headers
from brokers.upstox._http import request as _req
from brokers.upstox.envelopes import fail, ok

_LOGIN_URL = "https://api.upstox.com/v2/login/authorization/dialog"
_TOKEN_URL = "https://api-v2.upstox.com/login/authorization/token"
_TOKEN_REQUEST_URL_TPL = "https://api.upstox.com/v3/login/auth/token/request/{client_id}"
_TOKEN_VALIDATE_URL = "https://api.upstox.com/v2/user/profile"

_IST = ZoneInfo("Asia/Kolkata")


# ── Token cache helpers ─────────────────────────────────────────────────


def load_token_cache(cache_path: Path) -> dict[str, Any] | None:
    try:
        with open(cache_path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def is_token_valid(cache: dict[str, Any] | None) -> bool:
    if not cache or "access_token" not in cache:
        return False
    valid_until_str = cache.get("valid_until_ist")
    if not valid_until_str:
        return False
    try:
        valid_until = datetime.strptime(valid_until_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_IST)
        return datetime.now(_IST) < valid_until
    except Exception:
        return False


def save_token_cache(cache_path: Path, token: str, reset_time: str = "03:30") -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    valid_until = _compute_valid_until_ist(reset_time)
    now_ist = datetime.now(_IST)
    with open(cache_path, "w") as f:
        json.dump(
            {
                "access_token": token,
                "created_at_ist": now_ist.isoformat(timespec="seconds"),
                "valid_until_ist": valid_until.isoformat(timespec="seconds"),
            },
            f,
            indent=2,
        )


def _compute_valid_until_ist(reset_time_str: str) -> datetime:
    hh, mm = reset_time_str.split(":")
    reset_t = dt_time(int(hh), int(mm), 0)
    now_ist = datetime.now(_IST)
    today_reset = datetime.combine(now_ist.date(), reset_t, tzinfo=_IST)
    return today_reset if now_ist < today_reset else today_reset + timedelta(days=1)


# ── Server-side validity probe ──────────────────────────────────────────


def is_token_valid_remote(token: str, timeout: int = 5) -> bool:
    """True iff Upstox accepts the token (HTTP 200 + status==success)."""
    if not token:
        return False
    try:
        code, parsed, _, _ = _req(
            "GET",
            _TOKEN_VALIDATE_URL,
            headers=bearer_headers(token, v=2),
            timeout=timeout,
        )
    except Exception:
        return False
    if code != 200 or not isinstance(parsed, dict):
        return False
    return parsed.get("status") == "success"


# ── Token exchange (OAuth2 v2) ──────────────────────────────────────────


def exchange_code_for_token(
    creds: dict[str, Any],
    auth_code: str,
    token_url: str = _TOKEN_URL,
    timeout: int = 30,
) -> str:
    """Exchange an auth_code for an access_token. Raises RuntimeError on failure."""
    payload = {
        "code": auth_code,
        "client_id": creds["api_key"],
        "client_secret": creds["api_secret"],
        "redirect_uri": creds["redirect_uri"],
        "grant_type": "authorization_code",
    }
    headers = bearer_form()
    headers["Api-Version"] = "2.0"
    code, parsed, text, _ = _req("POST", token_url, headers=headers, data=payload, timeout=timeout)
    if code != 200:
        raise RuntimeError(f"token_exchange_http_{code}: {text}")
    if not isinstance(parsed, dict):
        raise RuntimeError(f"token_exchange_bad_body: {text}")
    token = parsed.get("access_token") or (parsed.get("data") or {}).get("access_token")
    if not token:
        raise RuntimeError("token_exchange_no_token")
    return str(token)


# ── v3 user-approved access-token request ───────────────────────────────


def request_access_token(creds: dict[str, Any], timeout: int = 15) -> dict[str, Any]:
    """
    POST /v3/login/auth/token/request/{client_id}
        body: {"client_secret": "<api_secret>"}

    On success the broker triggers an in-app + WhatsApp notification; the
    actual access_token is delivered async to the registered notifier
    webhook. data on success: {authorization_expiry, notifier_url}.
    """
    url = _TOKEN_REQUEST_URL_TPL.format(client_id=creds["api_key"])
    body = {"client_secret": creds["api_secret"]}
    try:
        code, parsed, text, _ = _req(
            "POST",
            url,
            headers=bearer_headers(None, v=2, content_type="application/json"),
            json=body,
            timeout=timeout,
        )
    except Exception as e:
        return fail(f"REQUEST_EXCEPTION: {e}")
    if code == 200 and isinstance(parsed, dict) and parsed.get("status") == "success":
        d = parsed.get("data") or {}
        return ok(
            {
                "authorization_expiry": d.get("authorization_expiry"),
                "notifier_url": d.get("notifier_url"),
            },
            code=code,
            raw=parsed,
        )
    return fail(
        f"HTTP {code}: {parsed if parsed is not None else text}",
        code=code,
        raw=parsed if isinstance(parsed, dict) else None,
    )


# ── Playwright login automation (lazy import) ───────────────────────────


def fetch_auth_code(creds: dict[str, Any], auth_cfg: dict[str, Any] | None = None) -> str:
    """
    Drive Upstox login via Playwright + TOTP + PIN. Returns the auth_code
    string captured at the redirect_uri. Raises RuntimeError after retries.

    Lazy-imports playwright + pyotp so the module loads in environments
    without browser support (CI unit tests).
    """
    import pyotp
    from loguru import logger
    from playwright.sync_api import sync_playwright

    cfg = auth_cfg or {}
    log = logger.bind(module="AUTH")
    max_retries = int(cfg.get("max_retries", 3))
    last_error: BaseException | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return _do_fetch_auth_code(creds, cfg, attempt, log, sync_playwright, pyotp)
        except Exception as e:
            last_error = e
            log.warning(f"auth attempt {attempt}/{max_retries} failed: {e}")
            if attempt < max_retries:
                import time as _t

                _t.sleep(5 * attempt)
    raise RuntimeError(f"auth_failed_after_{max_retries}: {last_error}")


def _do_fetch_auth_code(
    creds: dict[str, Any],
    cfg: dict[str, Any],
    attempt: int,
    log: Any,
    sync_playwright: Any,
    pyotp: Any,
) -> str:
    api_key = creds["api_key"]
    redirect_uri = creds["redirect_uri"]
    redirect_enc = quote(redirect_uri, safe="")
    login_url = cfg.get("login_url", _LOGIN_URL)
    headless = bool(cfg.get("headless", True))
    pw_args = list(cfg.get("playwright_args", []))
    auth_url = f"{login_url}?response_type=code&client_id={api_key}&redirect_uri={redirect_enc}"
    auth_code: str | None = None

    def handle_request(req: Any) -> None:
        nonlocal auth_code
        if auth_code is None and redirect_uri in req.url and "code=" in req.url:
            parsed = parse_qs(urlparse(req.url).query)
            auth_code = parsed.get("code", [None])[0]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=pw_args)
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()
        page.on("request", handle_request)
        try:
            page.goto(auth_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector("#mobileNum", state="visible", timeout=30000)
            page.locator("#mobileNum").fill(creds["mobile_no"])
            page.get_by_role("button", name="Get OTP").click()
            page.wait_for_selector("#otpNum", timeout=30000)
            otp = pyotp.TOTP(creds["totp_key"]).now()
            page.locator("#otpNum").fill(otp)
            page.get_by_role("button", name="Continue").click()
            page.wait_for_selector("input[type='password']", timeout=30000)
            page.get_by_label("Enter 6-digit PIN").fill(creds["pin"])
            page.get_by_role("button", name="Continue").click()
            page.wait_for_timeout(5000)
            if auth_code is None and redirect_uri in page.url and "code=" in page.url:
                parsed = parse_qs(urlparse(page.url).query)
                auth_code = parsed.get("code", [None])[0]
        except Exception:
            ss_dir = Path(cfg.get("screenshot_dir", "/tmp/upstox_auth"))
            try:
                ss_dir.mkdir(parents=True, exist_ok=True)
                page.screenshot(
                    path=str(ss_dir / f"auth_fail_attempt{attempt}.png"), full_page=True
                )
            except Exception:
                pass
            raise
        finally:
            context.close()
            browser.close()
    if not auth_code:
        raise RuntimeError("no_auth_code_captured")
    log.debug(f"auth_code obtained (attempt {attempt})")
    return auth_code


# ── Standalone CLI ──────────────────────────────────────────────────────


def test() -> None:
    import os
    import sys

    token = os.environ.get("UPSTOX_ACCESS_TOKEN", "")
    if not token:
        cache = load_token_cache(Path("data/cache/access_token.json"))
        token = (cache or {}).get("access_token", "") if cache else ""
    if not token:
        print("[auth] set UPSTOX_ACCESS_TOKEN or place data/cache/access_token.json")
        sys.exit(1)
    valid_remote = is_token_valid_remote(token)
    print(f"[auth] remote validity: {valid_remote}")
    sys.exit(0 if valid_remote else 1)


if __name__ == "__main__":
    test()
