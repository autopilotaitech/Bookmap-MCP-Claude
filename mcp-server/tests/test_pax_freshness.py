"""Tests for the data-truth / freshness envelope (Stage 1)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bookmap_mcp import pax_freshness as fr  # noqa: E402


def test_fresh_heartbeat_not_stale():
    now = 1_000_000
    m = fr.staleness(now - 5_000, "heartbeat", now=now)  # 5s old, budget 30s
    assert m["is_stale"] is False
    assert m["age_sec"] == 5.0
    assert m["stale_reason"] is None


def test_old_heartbeat_is_stale():
    now = 1_000_000
    m = fr.staleness(now - 600_000, "heartbeat", now=now)  # 600s old
    assert m["is_stale"] is True
    assert "exceeds" in m["stale_reason"]


def test_never_updated_is_stale():
    m = fr.staleness(None, "sim_db", now=1)
    assert m["is_stale"] is True
    assert m["stale_reason"] == "never_updated"
    assert m["age_sec"] is None


def test_env_override_threshold(monkeypatch):
    monkeypatch.setenv("PAX_STALE_SEC_HEARTBEAT", "5")
    assert fr.stale_threshold_sec("heartbeat") == 5.0
    monkeypatch.setenv("PAX_STALE_SEC_HEARTBEAT", "garbage")
    assert fr.stale_threshold_sec("heartbeat") == 30.0  # default fallback


def test_envelope_list_uses_items():
    now = 100_000
    env = fr.envelope([1, 2, 3], source="journal", updated_at_ms=now - 1000,
                      now=now)
    assert env["items"] == [1, 2, 3]
    assert env["_meta"]["source"] == "journal"
    assert env["_meta"]["is_stale"] is False


def test_envelope_dict_merges_meta():
    now = 100_000
    env = fr.envelope({"a": 1}, source="sim_db", updated_at_ms=now, now=now)
    assert env["a"] == 1
    assert env["_meta"]["source"] == "sim_db"


def test_envelope_none_carries_meta():
    env = fr.envelope(None, source="sim_db", updated_at_ms=None, now=1)
    assert env["_meta"]["is_stale"] is True


def test_learn_file_is_historical_not_failure():
    now = 10_000_000
    # very old learn file -> is_stale True but historical True
    env = fr.envelope({"x": 1}, source="learn_file",
                      updated_at_ms=now - 5_000_000, now=now)
    assert env["_meta"]["historical"] is True
    agg = fr.worst_of(env["_meta"])
    # historical stale must NOT count as a freshness failure in aggregate
    assert agg["is_stale"] is False


def test_worst_of_flags_nonhistorical_stale():
    stale_market = fr.staleness(None, "market", now=1)
    stale_market["source"] = "market"
    agg = fr.worst_of(stale_market)
    assert agg["is_stale"] is True
    assert "market" in agg["stale_sources"]
    assert agg["any_never_updated"] is True
