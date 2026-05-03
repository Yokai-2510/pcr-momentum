"""Map frontend view names to Redis keys and build snapshots."""

from __future__ import annotations

from typing import Any

from engines.api_gateway.util import json_loads_maybe
from state import keys as K


def view_key(view: str) -> str:
    if view == "dashboard":
        return K.UI_VIEW_DASHBOARD
    if view == "positions:closed_today":
        return K.UI_VIEW_POSITIONS_CLOSED_TODAY
    if view == "pnl":
        return K.UI_VIEW_PNL
    if view == "capital":
        return K.UI_VIEW_CAPITAL
    if view == "health":
        return K.UI_VIEW_HEALTH
    if view == "configs":
        return K.UI_VIEW_CONFIGS
    if view.startswith("strategy:"):
        return K.ui_view_strategy(view.split(":", 1)[1])
    if view.startswith("position:"):
        return K.ui_view_position(view.split(":", 1)[1])
    if view.startswith("delta_pcr:"):
        return K.ui_view_delta_pcr(view.split(":", 1)[1])
    raise ValueError(f"unknown view {view!r}")


def view_name_from_key(key: str) -> str | None:
    if key == K.UI_VIEW_DASHBOARD:
        return "dashboard"
    if key == K.UI_VIEW_POSITIONS_CLOSED_TODAY:
        return "positions:closed_today"
    if key == K.UI_VIEW_PNL:
        return "pnl"
    if key == K.UI_VIEW_CAPITAL:
        return "capital"
    if key == K.UI_VIEW_HEALTH:
        return "health"
    if key == K.UI_VIEW_CONFIGS:
        return "configs"
    prefix = "ui:views:strategy:"
    if key.startswith(prefix):
        return "strategy:" + key[len(prefix) :]
    prefix = "ui:views:position:"
    if key.startswith(prefix):
        return "position:" + key[len(prefix) :]
    prefix = "ui:views:delta_pcr:"
    if key.startswith(prefix):
        return "delta_pcr:" + key[len(prefix) :]
    return None


def validate_views(views: list[str]) -> list[str]:
    clean: list[str] = []
    for view in views:
        view_key(view)
        if view not in clean:
            clean.append(view)
    return clean


async def read_view(redis: Any, view: str) -> Any:
    raw = await redis.get(view_key(view))
    return json_loads_maybe(raw, None)


async def snapshot(redis: Any, views: list[str]) -> dict[str, Any]:
    valid = validate_views(views)
    pipe = redis.pipeline(transaction=False)
    for view in valid:
        pipe.get(view_key(view))
    raws = await pipe.execute()
    return {view: json_loads_maybe(raw, None) for view, raw in zip(valid, raws, strict=True)}

