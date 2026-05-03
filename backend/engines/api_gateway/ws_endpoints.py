"""Push-only WebSocket endpoint."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from engines.api_gateway.auth import decode_token
from engines.api_gateway.util import decode
from engines.api_gateway.view_router import read_view, snapshot, validate_views, view_name_from_key
from state import keys as K
from state.config_loader import get_settings

router = APIRouter(tags=["websocket"])

PING_INTERVAL_SEC = 20.0
PONG_TIMEOUT_SEC = 30.0


def _ts() -> str:
    return datetime.now(UTC).isoformat()


@router.websocket("/stream")
async def stream(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=1008, reason="Auth invalid")
        return
    try:
        decode_token(token, get_settings())
    except jwt.PyJWTError:
        await websocket.close(code=1008, reason="Auth invalid")
        return

    await websocket.accept()
    redis: Any = getattr(websocket.app.state, "redis", None)
    if redis is None:
        await websocket.close(code=1011, reason="Redis unavailable")
        return

    subscribed: set[str] = set()
    last_pong = asyncio.get_running_loop().time()
    last_ping = 0.0
    pubsub = redis.pubsub()
    await pubsub.subscribe(K.UI_PUB_VIEW)

    try:
        while True:
            now = asyncio.get_running_loop().time()
            if now - last_ping >= PING_INTERVAL_SEC:
                await websocket.send_json({"type": "ping", "ts": _ts()})
                last_ping = now
            if now - last_pong > PONG_TIMEOUT_SEC:
                await websocket.close(code=4000, reason="Heartbeat timeout")
                return

            try:
                msg = await asyncio.wait_for(websocket.receive_json(), timeout=0.2)
            except TimeoutError:
                msg = None
            except WebSocketDisconnect:
                return

            if isinstance(msg, dict):
                msg_type = msg.get("type")
                if msg_type == "pong":
                    last_pong = asyncio.get_running_loop().time()
                elif msg_type == "subscribe":
                    try:
                        views = validate_views([str(v) for v in msg.get("views", [])])
                    except Exception:
                        await websocket.close(code=4001, reason="Subscription validation failed")
                        return
                    subscribed = set(views)
                    await websocket.send_json(
                        {"type": "snapshot", "ts": _ts(), "data": await snapshot(redis, views)}
                    )

            pubmsg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.01)
            if not pubmsg:
                continue
            key = decode(pubmsg.get("data"))
            view = view_name_from_key(key)
            if view is None or view not in subscribed:
                continue
            await websocket.send_json(
                {"type": "update", "ts": _ts(), "view": view, "data": await read_view(redis, view)}
            )
    finally:
        await pubsub.unsubscribe(K.UI_PUB_VIEW)
        await pubsub.close()

