"""Pin CURRENT behavior for the wide-OR playback case (2026-05-29 NQM6).

The OR data was correct (real 40pt opening range: OR-H 30404.75 / OR-L 30364.75).
PAX skipped the OR-H long because of an INTENTIONAL OR-width veto, not a data bug:

  - dashboard.pax_decision uses a GLOBAL width band settings[pax_min/max_or_width_pts]
    = [3.0, 25.0] (NOT session-aware) -> STAND_DOWN at width 40.
  - pax_loop.decide uses a SESSION-AWARE band PROFILE[session]["width"]
    (ETH (3.0,25.0) / RTH (3.0,60.0)).

These are CHARACTERIZATION tests: they pin the gate as-is so any future policy
change (regime-aware width, advisory OpenRange, etc.) is a deliberate, reviewed
edit -- they are NOT an endorsement of the threshold. No strategy change here.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bookmap_mcp.dashboard as d        # noqa: E402
from bookmap_mcp import pax_loop          # noqa: E402

OR_HIGH = 30404.75
OR_LOW = 30364.75
MID = 30404.875        # at OR-H, distance -0.12 -> proximity true
WIDTH = 40.0


def _or_levels(mid=MID):
    return {
        "orHigh": OR_HIGH, "orLow": OR_LOW, "orWidthPts": WIDTH, "mid": mid,
        "inProximity": True, "middleLock": False,
        "levels": [{"label": "OR-H", "price": OR_HIGH, "side": "above",
                    "distance": round(OR_HIGH - mid, 2), "proximity": True,
                    "decision": "ENTER_LONG_FOLLOW", "confidence": 0.6,
                    "components": {"ps_rot": "NONE"}}],
    }


def _dashboard_snap():
    return {
        "health": "ok", "alias": "NQM6.CME@RITHMIC",
        "gates": {"session": {"code": "ACTIVE", "anchorMode": "LIVE"},
                  "news": {"blocked": False}},
        "book": {"mid": MID},
        "or_levels": _or_levels(),
    }


# ── dashboard.pax_decision: global width gate ──────────────────────────────

def test_dashboard_pax_decision_stands_down_on_wide_or():
    res = d.pax_decision(_dashboard_snap())
    assert res["decision"] == "STAND_DOWN"
    assert "OR width 40.0 out of [3.0,25.0]" in res["reason"]


def test_dashboard_width_gate_is_independent_of_or_csv_action():
    # PAX does not read the OpenRange CSV `action`; the same STAND_DOWN fires
    # whether OpenRange said WAIT or BLOCK_SIGNAL. (or_row is not even in this
    # snap.) This pins that the veto is PAX's own width gate, not the CSV.
    snap = _dashboard_snap()
    snap["or_row"] = {"action": "BLOCK_SIGNAL", "bias": "LONG",
                      "reason": "Wide opening range blocks signal."}
    res = d.pax_decision(snap)
    assert res["decision"] == "STAND_DOWN"
    assert "OR width 40.0" in res["reason"]


# ── pax_loop.decide: session-aware width band ──────────────────────────────

def _loop_snap(session_type):
    return {
        "health": "ok",
        "session": {"anchorMode": "LIVE", "code": "ACTIVE"},
        "book": {"mid": MID},
        "or_day_ledger": {"session_type": session_type},
        "gates": {"news": {"blocked": False}},
        "flow": {}, "or_levels": _or_levels(),
    }


def _flat_status():
    return {"position": {"size": 0}, "losers_today": 0, "working": [],
            "fills_today": [], "realized_today_usd": 0.0}


def test_pax_loop_eth_blocks_wide_or():
    now = dt.datetime(2026, 5, 29, 8, 35, tzinfo=dt.timezone.utc)
    plan = pax_loop.decide(_loop_snap("ETH"), _flat_status(), now,
                           int(now.timestamp() * 1000))
    assert not str(plan["action"]).startswith("PLACE_")
    assert plan.get("order") is None
    assert "OR width 40.0 out of [3.0,25.0]" in plan["reason"]


# ── Option C: dashboard width gate is session-aware (matches pax_loop) ──────

def _dashboard_snap_session(session_type, mid=MID, level_conf=0.6,
                            level_decision="ENTER_LONG_FOLLOW"):
    snap = _dashboard_snap()
    snap["or_day_ledger"] = {"session_type": session_type}
    snap["book"] = {"mid": mid}
    ol = _or_levels(mid)
    ol["levels"][0]["confidence"] = level_conf
    ol["levels"][0]["decision"] = level_decision
    snap["or_levels"] = ol
    return snap


def test_pax_loop_exposes_session_width_band():
    assert pax_loop.or_width_band("ETH") == (3.0, 25.0)
    assert pax_loop.or_width_band("RTH") == (3.0, 60.0)
    # unknown session falls back to the ETH band (same as decide()).
    assert pax_loop.or_width_band("WHATEVER") == (3.0, 25.0)


def test_dashboard_eth_width_40_still_blocks():
    res = d.pax_decision(_dashboard_snap_session("ETH"))
    assert res["decision"] == "STAND_DOWN"
    assert "OR width 40.0 out of [3.0,25.0]" in res["reason"]


def test_dashboard_rth_width_40_passes_width_gate():
    # RTH band is (3.0,60.0): a 40pt OR must NOT be vetoed on width anymore.
    res = d.pax_decision(_dashboard_snap_session("RTH"))
    assert "OR width" not in (res.get("reason") or ""), res


def test_dashboard_and_pax_loop_width_gate_agree_eth_rth():
    now = dt.datetime(2026, 5, 29, 9, 35, tzinfo=dt.timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    for stype in ("ETH", "RTH"):
        dash = d.pax_decision(_dashboard_snap_session(stype))
        loop = pax_loop.decide(_loop_snap(stype), _flat_status(), now, now_ms)
        dash_width_block = "OR width" in (dash.get("reason") or "")
        loop_width_block = "OR width" in (loop.get("reason") or "")
        assert dash_width_block == loop_width_block, (stype, dash, loop)


def test_dashboard_rth_weak_evidence_passes_width_but_no_trade():
    # Width passes under RTH, but a weak OR-H level (WAIT, conf 0.05) must NOT
    # produce an entry -- it stays WAIT/STAND_DOWN for a non-width reason.
    res = d.pax_decision(_dashboard_snap_session(
        "RTH", level_conf=0.046, level_decision="WAIT"))
    assert "OR width" not in (res.get("reason") or "")
    assert not str(res["decision"]).startswith("ENTER")
    assert res["size"] == 0


# ── compose-order regression: pax_decision sees the real session_type ──────

MAY29_GEN_NANOS = 1780061710808058400  # 2026-05-29T13:35:10Z (RTH, after 08:30 CT)

_HEADER = ("time,symbol,price,orHigh,orLow,cvdDelta,bidDepthDelta,askDepthDelta,"
           "netDepthDelta,cvdZ,psZ,cvdPercentile,psPercentile,location,distanceTicks,"
           "rangeWidth,rangeQuality,ageSeconds,bias,action,confidence,score,maxScore,"
           "evidence,reason")


def _csv_row(sym, orh, orl, t):
    return (f"{t},{sym},{(orh+orl)/2:.2f},{orh:.2f},{orl:.2f},0,0,0,0,0,0,0,0,IN,"
            f"0,0,WIDE,30,LONG,BLOCK_SIGNAL,LOW,0,4,\"\",\"Wide opening range blocks signal.\"")


class _Client:
    def __init__(self, *_a, **_kw):
        self.posts = []
        self._instruments = None
    def __enter__(self): return self
    def __exit__(self, *e): return None
    def get_json(self, path, params=None):
        params = params or {}
        a = params.get("alias", "")
        if path == "/ping": return {"ok": True}
        if path == "/instruments": return self._instruments
        if path == "/orderbook":
            return {"alias": a, "bestBid": MID - 0.25, "bestAsk": MID + 0.25,
                    "mid": MID, "bids": [], "asks": [],
                    "generatedNanos": MAY29_GEN_NANOS}
        if path == "/recent_trades":
            return {"alias": a, "trades": [
                {"price": MID, "size": 1, "side": "buy", "nanos": MAY29_GEN_NANOS}]}
        return {"alias": a}
    def post_json(self, path, payload):
        self.posts.append((path, dict(payload))); return {}


def test_compose_pax_decision_uses_real_session_type_not_default_eth(monkeypatch, tmp_path):
    """Regression: pax_decision must gate on the SAME session_type the composed
    snapshot reports (or_day_ledger), not the ETH default. The 2026-05-29 OR is
    RTH (08:30 CT) at width 40 -> RTH band (3,60) -> width must NOT veto."""
    from bookmap_mcp.config import BridgeConfig
    nq = tmp_path / "openrange-signals-NQM6.csv"
    nq.write_text(_HEADER + "\n" +
                  _csv_row("NQM6", OR_HIGH, OR_LOW, "2026-05-29T08:35:43") + "\n",
                  encoding="utf-8")
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])
    fake_cfg = BridgeConfig(url="http://127.0.0.1:8765", token="t")
    monkeypatch.setattr(d.BridgeConfig, "load", classmethod(lambda cls: fake_cfg))
    holder = {"instruments": {"instruments": [
        {"alias": "NQM6.CME@RITHMIC", "symbol": "NQM6"}]}}
    def _factory(*a, **kw):
        c = _Client(*a, **kw); c._instruments = holder["instruments"]; return c
    monkeypatch.setattr(d, "BridgeClient", _factory)
    monkeypatch.setattr(d, "_get_pax_collector", lambda: None)
    monkeypatch.setattr(d, "pax_record", lambda *a, **kw: None)
    monkeypatch.setattr(d, "_sync_bridge_config", lambda *a, **kw: None)
    with d._LAST_MAGNETS_LOCK:
        d._LAST_MAGNETS.clear()

    snap = d.fetch_snapshot()
    nq_snap = snap["aliases"]["NQM6.CME@RITHMIC"]
    assert nq_snap["or_day_ledger"]["session_type"] == "RTH"
    pax = nq_snap["pax"]
    # pax_decision must have seen RTH (proves or_day_ledger computed before pax).
    assert pax["components"]["or"]["session_type"] == "RTH"
    # RTH band (3,60): a 40pt OR is NOT vetoed on width.
    assert "OR width" not in (pax.get("reason") or ""), pax


def test_pax_loop_rth_passes_width_gate_at_40():
    # Documents the divergence: under RTH the width band is (3.0,60.0), so a
    # 40pt OR PASSES the width gate (the block reason is never the width one).
    # With this synthetic strong OR-H level the long even proceeds -- proving
    # the width band is the deciding factor between ETH-block and RTH-allow, and
    # why the dashboard HUD (global 25) and the SIM autopilot (RTH 60) can
    # disagree on the same 40pt OR. Pinned so a width-gate change is deliberate;
    # in the REAL 2026-05-29 tape the OR-H conviction was only ~0.05 so no long
    # would have fired regardless (see evidence report).
    now = dt.datetime(2026, 5, 29, 9, 35, tzinfo=dt.timezone.utc)
    plan = pax_loop.decide(_loop_snap("RTH"), _flat_status(), now,
                           int(now.timestamp() * 1000))
    assert "OR width" not in plan["reason"]   # width gate did NOT block under RTH
