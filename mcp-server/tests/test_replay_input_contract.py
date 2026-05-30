"""STAGE 4: replay_input schema drift guard.

The compact replay_input must keep emitting the keys pax_agent_replay needs to
re-run pax_loop.decide + pax_risk_gate. These tests catch obvious drift -- not a
perfect static contract, just a tripwire so a future builder change cannot
silently break live-log replay.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bookmap_mcp import pax_sim_agent as A          # noqa: E402
from bookmap_mcp import pax_agent_replay as R        # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "pax_replay"


def _snap():
    return {"health": "ok", "book": {"mid": 30339.0},
            "marketDataAsOfMs": 1779900000000, "composedAtMs": 1779900000000,
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


def _status():
    return {"position": {"size": 0}, "working": [], "fills_today": [],
            "losers_today": 0, "realized_today_usd": 0.0}


# --- builder emits the required contract -----------------------------------

def test_builder_emits_all_required_top_keys():
    ri = A.build_replay_input(_snap(), _status(), 1779900000000,
                              market_age_sec=1.0, heartbeat_age_sec=2.0,
                              sim_broker_ok=True, kill_switch_active=False)
    for k in A.REQUIRED_REPLAY_INPUT_KEYS:
        assert k in ri, f"replay_input missing required key: {k}"


def test_builder_emits_required_snapshot_keys():
    ri = A.build_replay_input(_snap(), _status(), 1779900000000,
                              market_age_sec=1.0, heartbeat_age_sec=2.0,
                              sim_broker_ok=True, kill_switch_active=False)
    for k in A.REQUIRED_REPLAY_SNAPSHOT_KEYS:
        assert k in ri["snapshot"], f"compact snapshot missing: {k}"
    # a real market-freshness timestamp must survive (gate depends on it).
    assert "marketDataAsOfMs" in ri["snapshot"]


def test_builder_emits_required_status_keys():
    ri = A.build_replay_input(_snap(), _status(), 1779900000000,
                              market_age_sec=1.0, heartbeat_age_sec=2.0,
                              sim_broker_ok=True, kill_switch_active=False)
    for k in A.REQUIRED_REPLAY_STATUS_KEYS:
        assert k in ri["status"], f"compact status missing: {k}"


def test_replay_input_is_json_serializable():
    ri = A.build_replay_input(_snap(), _status(), 1779900000000,
                              market_age_sec=1.0, heartbeat_age_sec=2.0,
                              sim_broker_ok=True, kill_switch_active=False)
    json.loads(json.dumps(ri))   # must not raise


# --- fixtures replay cleanly (no missing required fields) ------------------

def test_realistic_heartbeat_fixture_fully_replays():
    rep = R.replay_file(FIX / "replay_input_heartbeat.jsonl", now_ms=1)
    assert rep["replay_input_count"] == 1
    assert rep["replay_input_version_counts"] == {"1": 1}
    assert rep["usable_snapshot_count"] == 1
    assert rep["action_counts"] == {"PLACE_LONG": 1}     # decision replayed
    assert rep["op_gate_replayed_count"] == 1            # op gate replayed
    assert rep["op_gate_missing_fields_count"] == 0      # nothing missing


def test_all_replay_input_fixtures_have_required_keys():
    for fx in sorted(FIX.glob("replay_input_*.jsonl")):
        for line in fx.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            ri = rec.get("replay_input")
            assert isinstance(ri, dict), f"{fx.name}: replay_input not a dict"
            for k in A.REQUIRED_REPLAY_INPUT_KEYS:
                assert k in ri, f"{fx.name}: replay_input missing {k}"
            for k in A.REQUIRED_REPLAY_SNAPSHOT_KEYS:
                assert k in ri["snapshot"], f"{fx.name}: snapshot missing {k}"
