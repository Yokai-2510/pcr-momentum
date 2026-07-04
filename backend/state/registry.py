"""Registered-vessel enumeration — shared by init, api_gateway, order-exec.

Source of truth: `strategy:registry` SET (entries "{strategy_id}:{instrument}").
The strategy engine resolves classes on top of this (engines.strategy.registry);
everything else only needs the (sid, instrument) pairs, which live here so
non-strategy engines never import strategy code.
"""

from __future__ import annotations

from typing import Any

from state import keys as K


def parse_entry(raw: Any) -> tuple[str, str] | None:
    entry = raw.decode() if isinstance(raw, bytes) else str(raw)
    if ":" not in entry:
        return None
    sid, _, instrument = entry.partition(":")
    if not sid or not instrument:
        return None
    return sid, instrument


def list_vessels_sync(redis_sync: Any) -> list[tuple[str, str]]:
    """All registered (strategy_id, instrument) pairs — sync client."""
    out = []
    for raw in redis_sync.smembers(K.STRATEGY_REGISTRY) or []:
        parsed = parse_entry(raw)
        if parsed:
            out.append(parsed)
    return sorted(out)


async def list_vessels(redis: Any) -> list[tuple[str, str]]:
    """All registered (strategy_id, instrument) pairs — async client."""
    out = []
    for raw in await redis.smembers(K.STRATEGY_REGISTRY) or []:
        parsed = parse_entry(raw)
        if parsed:
            out.append(parsed)
    return sorted(out)


async def vessels_for_instrument(redis: Any, instrument: str) -> list[tuple[str, str]]:
    return [(s, i) for s, i in await list_vessels(redis) if i == instrument]
