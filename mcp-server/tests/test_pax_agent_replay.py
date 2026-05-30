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


# ── TASK 3: optional operational risk-gate replay ──────────────────────────

def _entry_record(*, as_of, ts=1779900000000, status=None, **extra):
    snap = {
        "health": "ok", "book": {"mid": 30339.0},
        "marketDataAsOfMs": as_of, "composedAtMs": as_of,
        "session": {"anchorMode": "LIVE", "code": "ACTIVE"},
        "or_day_ledger": {"session_type": "ETH"},
        "gates": {"news": {"blocked": False}}, "flow": {},
        "or_levels": {"orHigh": 30340.0, "orLow": 30325.5, "orWidthPts": 14.5,
                      "inProximity": True, "middleLock": False,
                      "levels": [{"label": "OR-H", "price": 30340.0,
                                  "distance": 1.0, "proximity": True,
                                  "decision": "ENTER_LONG_FOLLOW",
                                  "confidence": 0.6,
                                  "components": {"ps_rot": "NONE"}}]}}
    rec = {"ts_ms": ts, "snapshot": snap,
           "status": status if status is not None
           else {"position": {"size": 0}, "fills_today": [],
                 "realized_today_usd": 0.0},
           "action": "PLACE_LONG"}
    rec.update(extra)
    return rec


def test_op_gate_replays_stale_market_from_fixture():
    # stale_market_blocked.jsonl: marketDataAsOfMs is 60s before ts_ms -> stale.
    rep = R.replay_file(FIX / "stale_market_blocked.jsonl", now_ms=1)
    assert rep["replayed_risk_halt_counts"].get("stale_market_data") == 1
    assert rep["op_gate_replayed_count"] == 1
    # recorded == replayed here -> no divergence.
    assert rep["risk_halt_divergence_count"] == 0


def test_op_gate_replays_max_trades_with_status_and_config():
    from bookmap_mcp import pax_risk_gate as RG
    ts = 1779900000000
    rec = _entry_record(
        as_of=ts,                                   # fresh market
        status={"position": {"size": 0},
                "fills_today": [{"role": "ENTRY"}, {"role": "ENTRY"}],
                "realized_today_usd": 0.0})
    cfg = RG.RiskGateConfig(max_trades_per_session=2)
    rep = R.replay_records([rec], now_ms=1, risk_config=cfg)
    assert rep["replayed_risk_halt_counts"].get("max_trades_reached") == 1
    assert rep["op_gate_replayed_count"] == 1


def test_op_gate_missing_market_field_is_limitation_not_fake():
    ts = 1779900000000
    rec = _entry_record(as_of=ts)
    rec["snapshot"].pop("marketDataAsOfMs")
    rec["snapshot"].pop("composedAtMs")            # no real market timestamp
    rep = R.replay_records([rec], now_ms=1)
    assert rep["op_gate_replayed_count"] == 0
    assert rep["op_gate_missing_fields_count"] == 1
    assert rep["replayed_risk_halt_counts"] == {}   # nothing faked
    assert any("operational risk gate not replayed" in s
               for s in rep["limitations"])


def test_op_gate_divergence_recorded_vs_replayed():
    # Recorded stale_heartbeat, but the gate (fresh market, no heartbeat_age
    # field -> bootstrap allow, no kill switch) replays as ALLOWED -> divergence.
    rep = R.replay_file(FIX / "stale_heartbeat_blocked.jsonl", now_ms=1)
    assert rep["recorded_risk_halt_counts"].get("stale_heartbeat") == 1
    assert rep["replayed_risk_halt_counts"] == {}
    assert rep["risk_halt_divergence_count"] == 1
    d = rep["risk_halt_divergences"][0]
    assert d["recorded_risk_halt"] == "stale_heartbeat"
    assert d["replayed_risk_halt"] is None


def test_op_gate_uses_record_kill_switch_and_broker_fields():
    ts = 1779900000000
    rec = _entry_record(as_of=ts, kill_switch_active=True)
    rep = R.replay_records([rec], now_ms=1)
    assert rep["replayed_risk_halt_counts"].get("kill_switch_active") == 1
    rec2 = _entry_record(as_of=ts, sim_broker_ok=False)
    rep2 = R.replay_records([rec2], now_ms=1)
    assert rep2["replayed_risk_halt_counts"].get("sim_broker_unavailable") == 1


def test_op_gate_not_run_for_non_entry_plans():
    # out-of-proximity snapshot -> decide() NONE -> no entry -> gate not run.
    rep = R.replay_file(FIX / "divergence.jsonl", now_ms=1)
    assert rep["op_gate_replayed_count"] == 0
    assert rep["op_gate_missing_fields_count"] == 0
    assert rep["replayed_risk_halt_counts"] == {}


def test_recorded_counts_alias_preserved():
    # back-compat: risk_halt_counts == recorded_risk_halt_counts.
    rep = R.replay_file(FIX / "stale_market_blocked.jsonl", now_ms=1)
    assert rep["risk_halt_counts"] == rep["recorded_risk_halt_counts"]


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


# ── STAGE 2: replay consumes embedded replay_input ─────────────────────────

def test_replay_input_fixture_fully_replays_decision_and_op_gate():
    rep = R.replay_file(FIX / "replay_input_entry.jsonl", now_ms=1)
    assert rep["replay_input_count"] == 1
    assert rep["replay_input_version_counts"] == {"1": 1}
    assert rep["usable_snapshot_count"] == 1
    assert rep["action_counts"] == {"PLACE_LONG": 1}
    assert rep["op_gate_replayed_count"] == 1          # gate ran from replay_input
    assert rep["op_gate_missing_fields_count"] == 0
    # fresh embedded market_age -> gate allows -> no replayed halt.
    assert rep["replayed_risk_halt_counts"] == {}


def test_replay_input_stale_market_replays_from_embedded_age():
    rep = R.replay_file(FIX / "replay_input_stale_market.jsonl", now_ms=1)
    # the embedded market_age_sec=60 (not a snapshot ts diff) drives the gate.
    assert rep["replayed_risk_halt_counts"].get("stale_market_data") == 1
    assert rep["op_gate_replayed_count"] == 1
    assert rep["risk_halt_divergence_count"] == 0      # recorded == replayed


def test_old_fixture_still_works_without_replay_input():
    rep = R.replay_file(FIX / "clean_eligible_entry.jsonl", now_ms=1)
    assert rep["replay_input_count"] == 0
    assert rep["action_counts"] == {"PLACE_LONG": 1}
    assert any("pre-replay-input logs" in s for s in rep["limitations"])


def test_malformed_replay_input_counted_not_crashed():
    recs = [
        {"ts_ms": 1, "replay_input": "not-a-dict", "snapshot": {"health": "ok"},
         "action": "NONE"},
        {"ts_ms": 2, "replay_input": [1, 2, 3], "snapshot": {"health": "ok"}},
    ]
    rep = R.replay_records(recs, now_ms=1)
    assert rep["malformed_replay_input"] == 2
    assert rep["replay_input_count"] == 0
    # fell back to the top-level snapshot, so decisions still ran.
    assert rep["decisions_generated"] == 2
    assert any("malformed replay_input" in s for s in rep["limitations"])


def test_op_gate_uses_replay_input_heartbeat_and_market_age():
    from bookmap_mcp import pax_sim_agent as AGENT
    snap = {"health": "ok", "book": {"mid": 30339.0},
            "marketDataAsOfMs": 1779900000000,
            "session": {"anchorMode": "LIVE", "code": "ACTIVE"},
            "or_day_ledger": {"session_type": "ETH"},
            "gates": {"news": {"blocked": False}}, "flow": {},
            "or_levels": {"orHigh": 30340.0, "orLow": 30325.5, "orWidthPts": 14.5,
                          "inProximity": True, "middleLock": False,
                          "levels": [{"label": "OR-H", "price": 30340.0,
                                      "distance": 1.0, "proximity": True,
                                      "decision": "ENTER_LONG_FOLLOW",
                                      "confidence": 0.6,
                                      "components": {"ps_rot": "NONE"}}]}}
    flat = {"position": {"size": 0}, "fills_today": [], "working": [],
            "realized_today_usd": 0.0}
    # Embedded heartbeat_age is stale -> gate replays stale_heartbeat even though
    # the snapshot market timestamp is fresh.
    ri = AGENT.build_replay_input(snap, flat, 1779900000000,
                                  market_age_sec=1.0, heartbeat_age_sec=90.0,
                                  sim_broker_ok=True, kill_switch_active=False)
    rec = {"ts_ms": 1779900000000, "action": "PLACE_LONG", "replay_input": ri}
    rep = R.replay_records([rec], now_ms=1)
    assert rep["replayed_risk_halt_counts"].get("stale_heartbeat") == 1


def test_replay_input_output_deterministic_except_generated_ms():
    a = R.replay_file(FIX / "replay_input_entry.jsonl", now_ms=11)
    b = R.replay_file(FIX / "replay_input_entry.jsonl", now_ms=22)
    a.pop("generated_ms"); b.pop("generated_ms")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
