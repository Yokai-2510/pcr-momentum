"""Tiny in-process rate limiter for the single-instance gateway."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

from engines.api_gateway.errors import error_payload


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in {"/health", "/docs", "/openapi.json", "/redoc"}:
            return await call_next(request)

        limit = self._limit_for(request.url.path, request.method)
        key = f"{request.client.host if request.client else 'unknown'}:{request.url.path}:{request.method}"
        now = time.monotonic()
        bucket = self._hits[key]
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        if len(bucket) >= limit:
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": "60"},
                content=error_payload("RATE_LIMITED", "Too many requests"),
            )
        bucket.append(now)
        return await call_next(request)

    @staticmethod
    def _limit_for(path: str, method: str) -> int:
        if path == "/auth/login" and method.upper() == "POST":
            return 5
        if path.startswith("/commands/") and method.upper() == "POST":
            return 10
        return 60
