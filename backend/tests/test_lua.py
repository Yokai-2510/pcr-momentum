"""Tests for the three canonical Lua scripts.

Uses fakeredis-with-lua. Each test exercises one full happy + one negative
path so regressions show up immediately when a script is touched.
"""

from __future__ import annotations

import json
from pathlib import Path

LUA_DIR = Path(__file__).resolve().parents[1] / "state" / "lua"


def _read(name: str) -> str:
    return (LUA_DIR / f"{name}.lua").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# config_write_through
# ---------------------------------------------------------------------------
class TestConfigWriteThrough:
    def test_set_and_publish(self, fake_redis_sync: object) -> None:  # type: ignore[type-arg]
        r = fake_redis_sync
        script = r.register_script(_read("config_write_through"))

        keys = ["strategy:configs:risk", "ui:dirty", "ui:pub:view"]
        payload = json.dumps({"daily_loss_circuit_pct": 0.08})
        result = script(keys=keys, args=[payload, "configs"])

        assert result == 1
        assert r.get("strategy:configs:risk") == payload
        assert bool(r.sismember("ui:dirty", "configs")) is True


# ---------------------------------------------------------------------------
# cleanup_position
# ---------------------------------------------------------------------------
class TestCleanupPosition:
    def test_cleanup_drops_all_artifacts(self, fake_redis_sync: object) -> None:
        r = fake_redis_sync
        script = r.register_script(_read("cleanup_position"))

        # Seed state
        pos_id, sig_id, idx = "p1", "nifty50_1", "nifty50"
        r.hset(f"orders:positions:{pos_id}", "side", "CE")
        r.sadd("orders:positions:open", pos_id)
        r.sadd(f"orders:positions:open_by_index:{idx}", pos_id)
        r.hset(f"orders:status:{pos_id}", "stage", "ENTRY_FILLED")
        r.set(f"strategy:{idx}:current_position_id", pos_id)
        r.sadd("strategy:signals:active", sig_id)
        r.set(f"strategy:signals:{sig_id}", "{}")
        r.hset("orders:orders:O1", "status", "FILLED")
        r.hset("orders:broker:pos:O1", "filled_qty", "75")
        r.sadd("orders:broker:open_orders", "O1")

        keys = [
            f"orders:positions:{pos_id}",
            "orders:positions:open",
            f"orders:positions:open_by_index:{idx}",
            "orders:positions:closed_today",
            f"orders:status:{pos_id}",
            f"strategy:{idx}:current_position_id",
            "strategy:signals:active",
            f"strategy:signals:{sig_id}",
        ]
        deleted = script(keys=keys, args=[pos_id, sig_id, "O1"])
        assert deleted >= 4  # pos + status + signal + cur_pos + order_id artifacts

        assert not r.exists(f"orders:positions:{pos_id}")
        assert not r.exists(f"orders:status:{pos_id}")
        assert not r.exists(f"strategy:signals:{sig_id}")
        assert not r.exists(f"strategy:{idx}:current_position_id")
        assert not r.exists("orders:orders:O1")
        assert not r.exists("orders:broker:pos:O1")

        assert bool(r.sismember("orders:positions:open", pos_id)) is False
        assert bool(r.sismember(f"orders:positions:open_by_index:{idx}", pos_id)) is False
        assert bool(r.sismember("strategy:signals:active", sig_id)) is False
        assert bool(r.sismember("orders:broker:open_orders", "O1")) is False
        assert bool(r.sismember("orders:positions:closed_today", pos_id)) is True


# ---------------------------------------------------------------------------
# capital_allocator_check_and_reserve
# ---------------------------------------------------------------------------
class TestCapitalAllocator:
    def _seed(self, r: object) -> object:
        return r.register_script(_read("capital_allocator_check_and_reserve"))  # type: ignore[union-attr]

    def test_first_reservation_succeeds(self, fake_redis_sync: object) -> None:
        r = fake_redis_sync
        script = self._seed(r)
        result = script(
            keys=[
                "orders:allocator:deployed",
                "orders:allocator:open_count",
                "orders:allocator:open_symbols",
            ],
            args=["nifty50", 5000, 200000, 2],
        )
        ok, reason, _, cnt = result
        assert ok == 1
        assert reason == b"OK" or reason == "OK"
        assert int(cnt) == 1
        assert bool(r.sismember("orders:allocator:open_symbols", "nifty50")) is True

    def test_second_reservation_on_same_index_rejected(self, fake_redis_sync: object) -> None:
        r = fake_redis_sync
        script = self._seed(r)
        keys = [
            "orders:allocator:deployed",
            "orders:allocator:open_count",
            "orders:allocator:open_symbols",
        ]
        script(keys=keys, args=["nifty50", 5000, 200000, 2])
        result = script(keys=keys, args=["nifty50", 5000, 200000, 2])
        ok, reason, _, _ = result
        assert ok == 0
        assert (reason if isinstance(reason, str) else reason.decode()) == ("ALREADY_OPEN_ON_INDEX")

    def test_max_concurrent_reached(self, fake_redis_sync: object) -> None:
        r = fake_redis_sync
        script = self._seed(r)
        keys = [
            "orders:allocator:deployed",
            "orders:allocator:open_count",
            "orders:allocator:open_symbols",
        ]
        script(keys=keys, args=["nifty50", 5000, 200000, 1])
        result = script(keys=keys, args=["banknifty", 5000, 200000, 1])
        ok, reason, _, _ = result
        assert ok == 0
        assert (reason if isinstance(reason, str) else reason.decode()) == (
            "MAX_CONCURRENT_REACHED"
        )

    def test_insufficient_capital(self, fake_redis_sync: object) -> None:
        r = fake_redis_sync
        script = self._seed(r)
        keys = [
            "orders:allocator:deployed",
            "orders:allocator:open_count",
            "orders:allocator:open_symbols",
        ]
        result = script(keys=keys, args=["nifty50", 250000, 200000, 2])
        ok, reason, _, _ = result
        assert ok == 0
        assert (reason if isinstance(reason, str) else reason.decode()) == ("INSUFFICIENT_CAPITAL")
