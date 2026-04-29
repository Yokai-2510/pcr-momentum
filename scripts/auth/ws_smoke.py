"""
scripts/ws_smoke.py — vanilla Upstox SDK WebSocket smoke test.

Exact structure from the Upstox SDK docs. ONE instrument, no auto-reconnect,
no engine wiring. Runs the streamer on its own thread, prints the first N
messages to stdout, then disconnects.

Usage:
    UPSTOX_ACCESS_TOKEN=eyJ... python scripts/ws_smoke.py
    # or
    python scripts/ws_smoke.py NSE_INDEX|"Nifty 50"

Defaults:
    instrument: NSE_INDEX|Nifty 50
    mode:       ltpc
    duration:   60s OR until 10 messages received
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time

import upstox_client


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "instrument",
        nargs="?",
        default="NSE_INDEX|Nifty 50",
        help="Upstox instrument key, e.g. 'NSE_INDEX|Nifty 50' or 'NSE_FO|49520'",
    )
    parser.add_argument("--mode", default="ltpc", choices=["ltpc", "full", "option_greeks", "full_d30"])
    parser.add_argument("--duration", type=int, default=60, help="seconds to listen")
    parser.add_argument("--max-msgs", type=int, default=10, help="stop after this many messages")
    args = parser.parse_args()

    token = os.environ.get("UPSTOX_ACCESS_TOKEN", "").strip()
    if not token:
        print("ERROR: UPSTOX_ACCESS_TOKEN env var not set", file=sys.stderr)
        return 1

    print(f"[ws_smoke] instrument={args.instrument} mode={args.mode}")

    config = upstox_client.Configuration()
    config.access_token = token
    api_client = upstox_client.ApiClient(config)
    streamer = upstox_client.MarketDataStreamerV3(api_client, [args.instrument], args.mode)

    msgs: list = []
    stop_evt = threading.Event()

    def on_open(*_a, **_kw) -> None:
        print("[ws_smoke] open")

    def on_message(message=None, *_a, **_kw) -> None:
        if message is None:
            return
        msgs.append(message)
        feeds = message.get("feeds") if isinstance(message, dict) else None
        keys = list((feeds or {}).keys())
        print(f"[ws_smoke] msg #{len(msgs)} feeds={keys}")
        if len(msgs) >= args.max_msgs:
            stop_evt.set()

    def on_error(err=None, *_a, **_kw) -> None:
        print(f"[ws_smoke] error: {err!r}", file=sys.stderr)

    def on_close(code=None, reason=None, *_a, **_kw) -> None:
        print(f"[ws_smoke] close code={code} reason={reason}")
        stop_evt.set()

    streamer.on("open", on_open)
    streamer.on("message", on_message)
    streamer.on("error", on_error)
    streamer.on("close", on_close)

    # No auto-reconnect (per user direction: keep simple).
    streamer.auto_reconnect(False, 0, 0)

    # SDK runs its own I/O thread under .connect(); we just block here until done.
    def _connect() -> None:
        try:
            streamer.connect()
        except Exception as e:
            print(f"[ws_smoke] connect raised: {e!r}", file=sys.stderr)
            stop_evt.set()

    t = threading.Thread(target=_connect, name="ws-smoke", daemon=True)
    t.start()

    deadline = time.time() + args.duration
    while time.time() < deadline and not stop_evt.is_set():
        time.sleep(0.5)

    try:
        streamer.disconnect()
    except Exception as e:
        print(f"[ws_smoke] disconnect raised: {e!r}", file=sys.stderr)

    print(f"[ws_smoke] received {len(msgs)} messages in {args.duration}s window")
    return 0 if msgs else 2


if __name__ == "__main__":
    sys.exit(main())
