"""Deterministic decision-path replay + regression fixtures (STAGE 1 + 2).

Replay re-runs pax_loop.decide over saved JSONL; it must place no orders, call
no LLM, touch no live Bookmap, and produce byte-stable output.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bookmap_mcp import pax_agent_replay as R   # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "pax_replay"


# ── unit: parsing + replay ─────────────────────────────────────────────────

def test_iter_records_skips_malformed():
    text = ('{"a": 1}\n'
            'broken json\n'
            '\n'                       # blank ignored, not malformed
            '[1,2,3]\n'                # non-dict -> malformed
            '{"b": 2}\n')
    recs, malformed = R.iter_records(text)
    assert len(recs) == 2
    assert malformed == 2


def test_clean_fixture_replays_place_long():
    rep = R.replay_file(FIX / "clean_eligible_entry.jsonl", now_ms=1)
    assert rep["event_count"] == 1
    assert rep["usable_snapshot_count"] == 1
    assert rep["decisions_generated"] == 1
    assert rep["action_counts"] == {"PLACE_LONG": 1}
    assert rep["setup_counts"] == {"OR_BREAK_ACCEPT": 1}
    assert rep["divergence_count"] == 0


def test_divergence_detection():
    rep = R.replay_file(FIX / "divergence.jsonl", now_ms=1)
    assert rep["divergence_count"] == 1
    d = rep["divergences"][0]
    assert d["recorded_action"] == "PLACE_LONG"
    assert d["replay_action"] == "NONE"


def test_malformed_fixture_does_not_crash_and_reports_honestly():
    rep = R.replay_file(FIX / "malformed_missing.jsonl", now_ms=1)
    # broken JSON line + non-dict line are malformed; heartbeat + null-snapshot
    # records parse but carry no usable snapshot.
    assert rep["skipped_malformed_count"] >= 2
    assert rep["usable_snapshot_count"] == 0
    assert rep["decisions_generated"] == 0
    assert any("no usable snapshots" in s for s in rep["limitations"])


def test_missing_input_is_safe():
    rep = R.replay_file(FIX / "does_not_exist.jsonl", now_ms=1)
    assert rep["event_count"] == 0
    assert any("unreadable" in s for s in rep["limitations"])


def test_output_is_deterministic_except_generated_ms():
    a = R.replay_file(FIX / "clean_eligible_entry.jsonl", now_ms=111)
    b = R.replay_file(FIX / "clean_eligible_entry.jsonl", now_ms=222)
    a.pop("generated_ms"); b.pop("generated_ms")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    # generated_ms is the only wall-clock field and is pinned by now_ms.
    c = R.replay_file(FIX / "clean_eligible_entry.jsonl", now_ms=111)
    assert c["generated_ms"] == 111


def test_limit_truncates():
    recs = [{"ts_ms": i, "snapshot": {"health": "ok"}, "action": "NONE"}
            for i in range(5)]
    rep = R.replay_records(recs, limit=2, now_ms=1)
    assert rep["event_count"] == 2


# ── regression: risk-halt fixtures stay pinned ─────────────────────────────

@pytest.mark.parametrize("fname,code", [
    ("stale_market_blocked.jsonl", "stale_market_data"),
    ("stale_heartbeat_blocked.jsonl", "stale_heartbeat"),
    ("kill_switch_blocked.jsonl", "kill_switch_active"),
])
def test_recorded_risk_halts_are_counted(fname, code):
    rep = R.replay_file(FIX / fname, now_ms=1)
    assert rep["risk_halt_counts"].get(code) == 1
    # the snapshot is still present, so the decision path is also replayed.
    assert rep["usable_snapshot_count"] == 1


def test_summary_shape_is_stable():
    rep = R.replay_file(FIX / "clean_eligible_entry.jsonl", now_ms=1)
    for k in ("input", "event_count", "usable_snapshot_count",
              "skipped_malformed_count", "decisions_generated", "action_counts",
              "setup_counts", "risk_halt_counts", "divergence_count",
              "deterministic_config", "limitations"):
        assert k in rep
    assert rep["deterministic_config"]["policy"] == "pax_loop.decide"


# ── safety: no broker / no Claude / no live ────────────────────────────────

def test_module_has_no_broker_or_llm_path():
    src = Path(R.__file__).read_text(encoding="utf-8")
    for forbidden in ("sim_place_bracket", "place_bracket", "bookmap_place",
                      "claude", "BridgeClient", "urllib.request", "subprocess"):
        assert forbidden not in src, f"{forbidden} must not be in pax_agent_replay"


def test_replay_does_not_call_broker(monkeypatch):
    # If replay ever reached the SIM broker, this would trip.
    import bookmap_mcp.pax_sim_tools as T

    def boom(*a, **k):
        raise AssertionError("replay must not place orders")

    monkeypatch.setattr(T, "sim_place_bracket", boom, raising=False)
    rep = R.replay_file(FIX / "clean_eligible_entry.jsonl", now_ms=1)
    assert rep["decisions_generated"] == 1
