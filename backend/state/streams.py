"""Stream + pub/sub channel constants and small helpers.

Single source of truth: `docs/Schema.md` §1 (Streams + Pub/Sub rows under
each top-level namespace).

Every Redis stream / pub/sub channel used by any engine MUST be referenced
through a constant here — same discipline as `keys.py`.

This module also exposes lightweight async helpers for stream consumer
groups, since every engine that consumes a stream needs the same idempotent
`XGROUP CREATE` + `XREADGROUP` boilerplate.
"""

from __future__ import annotations

from typing import Any, Final

# ---------------------------------------------------------------------------
# Stream names (mirror Schema.md §1.1-§1.6)
# ---------------------------------------------------------------------------

# system:*
STREAM_SYSTEM_CONTROL: Final[str] = "system:stream:control"

# market_data:* (per-index tick streams; helper below)
# strategy:*
STREAM_STRATEGY_SIGNALS: Final[str] = "strategy:stream:signals"
STREAM_STRATEGY_REJECTED_SIGNALS: Final[str] = "strategy:stream:rejected_signals"

# orders:*
STREAM_ORDERS_ORDER_EVENTS: Final[str] = "orders:stream:order_events"
STREAM_ORDERS_MANUAL_EXIT: Final[str] = "orders:stream:manual_exit"

# ui:*
STREAM_UI_HEALTH_ALERTS: Final[str] = "ui:stream:health_alerts"

# system:health:alerts is also a STREAM (Schema.md §1.1)
STREAM_SYSTEM_HEALTH_ALERTS: Final[str] = "system:health:alerts"


# ---------------------------------------------------------------------------
# Pub/Sub channels
# ---------------------------------------------------------------------------
PUB_SYSTEM_EVENT: Final[str] = "system:pub:system_event"
PUB_UI_VIEW: Final[str] = "ui:pub:view"


# ---------------------------------------------------------------------------
# Consumer-group conventions
# ---------------------------------------------------------------------------
# One group per consuming engine. Multiple workers within an engine share
# the group and use distinct consumer names.

GROUP_STRATEGY_NIFTY50: Final[str] = "strategy:nifty50"
GROUP_STRATEGY_BANKNIFTY: Final[str] = "strategy:banknifty"
GROUP_ORDER_EXEC: Final[str] = "exec"
GROUP_BACKGROUND_ORDER_EVENTS: Final[str] = "background"
GROUP_API_ORDER_EVENTS: Final[str] = "api_gateway"
GROUP_API_HEALTH_ALERTS: Final[str] = "api_gateway"


# ---------------------------------------------------------------------------
# Stream MAXLEN caps (approximate, ~ count) per Schema.md
# ---------------------------------------------------------------------------
MAXLEN_TICK_STREAM: Final[int] = 10_000
MAXLEN_SIGNALS: Final[int] = 5_000
MAXLEN_ORDER_EVENTS: Final[int] = 5_000
MAXLEN_HEALTH_ALERTS: Final[int] = 1_000
MAXLEN_CONTROL: Final[int] = 1_000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def market_data_tick_stream(index: str) -> str:
    """Return the per-index tick stream name (Schema.md §1.3)."""
    if index not in ("nifty50", "banknifty"):
        raise ValueError(f"unknown index {index!r}")
    return f"market_data:stream:tick:{index}"


def strategy_group_for(index: str) -> str:
    """Consumer group name for the strategy thread of `index`."""
    if index not in ("nifty50", "banknifty"):
        raise ValueError(f"unknown index {index!r}")
    return f"strategy:{index}"


async def ensure_consumer_group(
    redis: Any,
    stream: str,
    group: str,
    *,
    start_id: str = "$",
    mkstream: bool = True,
) -> None:
    """Create `group` on `stream` if missing.

    Idempotent: a `BUSYGROUP` reply means the group already exists, which is
    the desired state.

    Args:
        redis: an `redis.asyncio.Redis` instance.
        stream: stream key.
        group: consumer-group name.
        start_id: where the group starts reading. `$` means "only new
            entries"; `0` means "from the beginning".
        mkstream: if True, create the stream too (avoids an XADD bootstrap).
    """
    try:
        await redis.xgroup_create(name=stream, groupname=group, id=start_id, mkstream=mkstream)
    except Exception as exc:
        if "BUSYGROUP" in str(exc):
            return
        raise


async def xreadgroup_one(
    redis: Any,
    stream: str,
    group: str,
    consumer: str,
    *,
    block_ms: int = 0,
    count: int = 1,
) -> tuple[str, dict[str, Any]] | None:
    """Read up to `count` entries from `stream` for `(group, consumer)`.

    Returns a single (entry_id, fields) tuple for convenience when count=1.
    Returns None on timeout when block_ms > 0.
    """
    res = await redis.xreadgroup(
        groupname=group,
        consumername=consumer,
        streams={stream: ">"},
        count=count,
        block=block_ms,
    )
    if not res:
        return None
    # res = [(stream_name, [(entry_id, fields_dict), ...])]
    _, entries = res[0]
    if not entries:
        return None
    entry_id, fields = entries[0]
    return (
        entry_id.decode() if isinstance(entry_id, bytes) else entry_id,
        {
            (k.decode() if isinstance(k, bytes) else k): (
                v.decode() if isinstance(v, bytes) else v
            )
            for k, v in fields.items()
        },
    )


async def ack(redis: Any, stream: str, group: str, *entry_ids: str) -> int:
    """ACK one or more entry IDs on a consumer group; returns count ACKed."""
    return int(await redis.xack(stream, group, *entry_ids))
