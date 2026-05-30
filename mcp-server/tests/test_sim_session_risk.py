"""STAGE 2: honestly-derived session-risk counters from the SIM close stream.

session_risk_from_deltas is pure; SimEngine.snapshot exposes the derived
counters; pax_risk_gate consumes them. R is never fabricated.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bookmap_mcp import sim_engine as se          # noqa: E402
from bookmap_mcp import pax_risk_gate as RG        # noqa: E402


# --- pure math -------------------------------------------------------------

def test_session_risk_streak_and_drawdown():
    # cum: -10,-30,0,-5,-10,-15 ; peak stays 0 ; worst dd = -30
    r = se.session_risk_from_deltas([-10, -20, 30, -5, -5, -5])
    assert r["consecutive_losses"] == 3
    assert r["drawdown_usd"] == -30.0
    assert r["peak_equity"] == 0.0


def test_session_risk_scratch_is_neutral():
    # a 0 (scratch) neither extends nor resets the trailing loss streak.
    assert se.session_risk_from_deltas([-5, 0, -5])["consecutive_losses"] == 2
    # a winner resets it.
    assert se.session_risk_from_deltas([-5, 5, -5])["consecutive_losses"] == 1


def test_session_risk_peak_and_drawdown_from_peak():
    # cum: 50,20,30 ; peak=50 ; worst dd = 20-50 = -30 ; last is a winner
    r = se.session_risk_from_deltas([50, -30, 10])
    assert r["peak_equity"] == 50.0
    assert r["drawdown_usd"] == -30.0
    assert r["consecutive_losses"] == 0


def test_session_risk_empty_is_zero():
    r = se.session_risk_from_deltas([])
    assert r == {"consecutive_losses": 0, "peak_equity": 0.0, "drawdown_usd": 0.0}


def test_session_risk_ignores_garbage():
    r = se.session_risk_from_deltas([-10, None, "x", -10])
    assert r["consecutive_losses"] == 2
    assert r["drawdown_usd"] == -20.0


# --- snapshot integration --------------------------------------------------

def test_snapshot_exposes_session_risk_counters(tmp_path):
    db = tmp_path / "sim.db"
    eng = se.SimEngine(alias="TEST", db_path=db, eod_close_hour_ct=None)
    anchor = se._today_rth_anchor_ms()
    deltas = [-10.0, -20.0, -30.0]   # three consecutive losing closes today
    with eng._conn() as c:
        for i, d in enumerate(deltas):
            c.execute(
                "INSERT INTO events(ts_ms, alias, kind, order_id, payload) "
                "VALUES(?,?,?,?,?)",
                (anchor + 1000 * (i + 1), "TEST", "POSITION_UPDATE", None,
                 json.dumps({"realized_delta": d})))

    snap = eng.snapshot()
    assert snap["consecutive_losses_today"] == 3
    assert snap["session_drawdown_usd"] == -60.0
    assert snap["session_peak_equity"] == 0.0
    assert snap["realized_today_usd"] == -60.0
    assert snap["losers_today"] == 3
    # R fields are honestly unavailable, never fabricated.
    assert snap["realized_today_r"] is None
    assert snap["session_drawdown_r"] is None
    assert snap["r_source"].startswith("unavailable")

    # ...and the gate consumes the derived counters honestly.
    counters = RG.session_counters_from_status(snap)
    assert counters["consecutive_losses"] == 3
    assert counters["drawdown_usd"] == -60.0
    assert counters["realized_usd"] == -60.0
    assert counters["realized_r"] is None


def test_snapshot_no_events_is_clean(tmp_path):
    eng = se.SimEngine(alias="TEST", db_path=tmp_path / "sim.db",
                       eod_close_hour_ct=None)
    snap = eng.snapshot()
    assert snap["consecutive_losses_today"] == 0
    assert snap["session_drawdown_usd"] == 0.0
    counters = RG.session_counters_from_status(snap)
    assert counters["consecutive_losses"] == 0
    assert counters["drawdown_usd"] == 0.0
