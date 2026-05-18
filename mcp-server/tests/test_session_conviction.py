"""Tests for the v2 anchored multi-source conviction engine.

The engine maintains a per-alias session anchor at 08:30 CT. Each tick it pulls
fifteen explicit source signals from the snapshot, pushes the instantaneous
value into a per-source ring bounded by the medium window, and aggregates a
score from the short rolling SMA, the medium rolling SMA, and the session SMA.
Each source declares a reliability in [0,1] (availability × sample-confidence ×
freshness × regime gate). Effective weight = base × reliability, scaled inside
correlation clusters whose summed absolute weight exceeds the cluster cap.

Composite = Σ(eff_w · src_score) / Σ|eff_w|, clipped to [-1,+1].
Trajectory = SMA slope of the composite over the last 30s vs 30-90s.

These tests verify:
  * legacy helper-signal sign conventions still hold
  * v2 output is backward-compatible with the dashboard's component keys
  * a fully aligned bull/bear stack clears the TREND threshold within 5 min
  * mixed/missing sources do not produce false trend
  * pull-stack sign, micro-event sign (iceberg, spoof), session reset, and
    SMA-slope trajectory all behave as documented
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest                                                                    # noqa: E402

import bookmap_mcp.dashboard as d                                                # noqa: E402


# ─── Test harness ───────────────────────────────────────────────────────────

def _reset_state():
    d._CONVICTION_STATE.clear()


def _slide_state_back(alias: str, dt_ms: int) -> None:
    """Pretend the engine's prior bookkeeping is older by dt_ms, so the next
    compute call sees a fresh delta. This lets us drive N ticks deterministically
    without sleeping on the wall clock.
    """
    st = d._CONVICTION_STATE.get(alias)
    if not st: return
    st["lastUpdateMs"] -= dt_ms
    for src in st["sources"].values():
        src["ring"] = [(ts - dt_ms, v) for ts, v in src["ring"]]
        src["lastUpdateMs"] -= dt_ms
    st["scoreRing"] = [(ts - dt_ms, v) for ts, v in st["scoreRing"]]


def _drive(snap: dict, ticks: int, dt_sec: float = 1.0) -> dict:
    """Run compute_session_conviction `ticks` times with a fake 1s step.
    Returns the final result dict.
    """
    _reset_state()
    result = None
    alias = snap["alias"]
    for _ in range(ticks):
        result = d.compute_session_conviction(snap)
        _slide_state_back(alias, int(dt_sec * 1000))
    return result


def _full_bull_snap(alias: str = "NQM6") -> dict:
    """Snapshot with every source firing bullish at near-max strength."""
    return {
        "health": "ok",
        "alias":  alias,
        "ts":     dt.datetime.now(d.ET).isoformat(timespec="seconds"),
        "flow": {
            "regime":           "TRENDING_UP",
            "regimeConfidence": 0.9,
            "biasScore":        0.7,
            "ofi":              500.0,
            "ofiZ":             3.0,
            "cvdDelta":         500.0,
            "cvdDeltaZ":        3.0,
            "vpt":              200.0,
            "vptZ":             2.0,
            "vwapSlope":        {"label": "STRONG_UP", "slopeZ": 2.0},
            "ib":               {"dayType": "TREND"},
        },
        "book":      {"mid": 20005.0},
        "vwap_obj":  {"vwap": 20000.0, "stddev": 10.0, "lastTradePrice": 20005.0},
        "vp_bias":   {"score": 0.7, "label": "BULLISH"},
        "vwap_bias": {"score": 0.5, "label": "BULLISH"},
        "pull_stack": {"aggregateZ": 3.0, "rotation": "ROTATION_UP", "windows": [{"zScore": 1.8}]},
        "tape_buckets": {"biasScore": 0.7, "bias": "BULLISH"},
        "lt_liquidity": {"bidSize": 1000.0, "askSize": 200.0},
        "micro_events": {"events": [
            # Bull cluster: bid-defended icebergs + an ASK-swept (isBid=False)
            # STOP_SWEEP. Under canonical convention asks-swept = buy pressure.
            {"kind": "ICEBERG",   "isBid": True,  "price": 20005.0, "timeMs": 0},
            {"kind": "STOP_SWEEP","isBid": False, "price": 20006.0, "timeMs": 0},
            {"kind": "ICEBERG",   "isBid": True,  "price": 20004.0, "timeMs": 0},
            {"kind": "ICEBERG",   "isBid": True,  "price": 20003.0, "timeMs": 0},
            {"kind": "ICEBERG",   "isBid": True,  "price": 20002.0, "timeMs": 0},
        ]},
        "or_levels": {
            "inProximity": True,
            "middleLock":  False,
            "levels": [
                {"label": "OR-H", "proximity": True,
                 "decision": "ENTER_LONG_FOLLOW", "confidence": 0.8},
            ],
        },
    }


def _full_bear_snap(alias: str = "NQM6") -> dict:
    bull = _full_bull_snap(alias)
    bull["flow"]["regime"]    = "TRENDING_DOWN"
    bull["flow"]["biasScore"] = -0.7
    bull["flow"]["ofi"]       = -500.0
    bull["flow"]["ofiZ"]      = -3.0
    bull["flow"]["cvdDelta"]  = -500.0
    bull["flow"]["cvdDeltaZ"] = -3.0
    bull["flow"]["vptZ"]      = -2.0
    bull["flow"]["vwapSlope"] = {"label": "STRONG_DOWN", "slopeZ": -2.0}
    bull["book"]["mid"]       = 19995.0
    bull["vwap_obj"]["lastTradePrice"] = 19995.0
    bull["vp_bias"]   = {"score": -0.7, "label": "BEARISH"}
    bull["vwap_bias"] = {"score": -0.5, "label": "BEARISH"}
    bull["pull_stack"] = {"aggregateZ": -3.0, "rotation": "ROTATION_DN",
                          "windows": [{"zScore": -1.8}]}
    bull["tape_buckets"]   = {"biasScore": -0.7, "bias": "BEARISH"}
    bull["lt_liquidity"]   = {"bidSize": 200.0, "askSize": 1000.0}
    bull["micro_events"]   = {"events": [
        # Bear cluster: ask-defended icebergs + a BID-swept (isBid=True)
        # STOP_SWEEP. Under canonical convention bids-swept = sell pressure.
        {"kind": "ICEBERG",   "isBid": False, "price": 19995.0, "timeMs": 0},
        {"kind": "STOP_SWEEP","isBid": True,  "price": 19994.0, "timeMs": 0},
        {"kind": "ICEBERG",   "isBid": False, "price": 19996.0, "timeMs": 0},
        {"kind": "ICEBERG",   "isBid": False, "price": 19997.0, "timeMs": 0},
        {"kind": "ICEBERG",   "isBid": False, "price": 19998.0, "timeMs": 0},
    ]}
    bull["or_levels"] = {
        "inProximity": True,
        "middleLock":  False,
        "levels": [{"label": "OR-L", "proximity": True,
                    "decision": "ENTER_SHORT_FOLLOW", "confidence": 0.8}],
    }
    return bull


def _empty_snap(alias: str = "NQM6") -> dict:
    """Snapshot with every source absent. Engine must run without crashing
    and every source's reliability must be zero."""
    return {
        "health": "ok",
        "alias":  alias,
        "ts":     dt.datetime.now(d.ET).isoformat(timespec="seconds"),
        "flow":   {},
        "book":   {},
    }


# ─── Legacy helper-signal contract (unchanged) ──────────────────────────────

def test_legacy_constants_still_present():
    """The v2 engine retains the legacy EMA constants for backwards compat
    with existing pax tooling and pinned helper-signal tests."""
    assert d.CONVICTION_WEIGHTS["ib"] == 0.0
    assert sum(d.CONVICTION_WEIGHTS.values()) == pytest.approx(1.0, abs=1e-6)
    hl = d.CONVICTION_HALFLIFE_SEC
    assert hl["level"] <= 120
    assert hl["regime"] <= 180


def test_regime_signal_trending_up_strong_conf():
    s = d._regime_to_signal("TRENDING_UP", 0.8)
    assert 0.6 < s < 0.95


def test_regime_signal_warmup_is_zero():
    assert d._regime_to_signal("WARMUP", 0.5) == 0.0
    assert d._regime_to_signal("QUIET", 1.0) == 0.0


def test_slope_signal_strong_up_with_z():
    s = d._slope_to_signal("STRONG_UP", 2.0)
    assert s == pytest.approx(0.9, abs=0.01)


def test_level_signal_aggregates_proximity_only():
    ol = {"levels": [
        {"proximity": True,  "decision": "ENTER_LONG_FOLLOW",  "confidence": 0.7},
        {"proximity": False, "decision": "ENTER_SHORT_FOLLOW", "confidence": 0.9},
        {"proximity": True,  "decision": "WAIT",               "confidence": 0.5},
    ]}
    assert d._level_to_signal(ol) == pytest.approx(0.7)


# ─── Config + new constants ─────────────────────────────────────────────────

def test_v2_source_weights_loaded_from_config():
    """pax_weights.json must expose the new keys the engine reads."""
    cfg = d._load_pax_weights()
    sw = cfg.get("conviction_source_weights")
    assert sw, "conviction_source_weights missing from pax_weights.json"
    # Every registered source must have a base weight.
    for name in d._CONVICTION_SOURCES:
        assert name in sw, f"missing weight for source {name}"
    # And every cluster must reference real sources.
    clusters = cfg.get("conviction_clusters")
    for cname, members in clusters.items():
        for m in members:
            assert m in d._CONVICTION_SOURCES, f"cluster {cname} refs unknown source {m}"


def test_v2_cluster_caps_sane():
    caps = d._load_pax_weights().get("conviction_cluster_caps") or {}
    for name in ("flow", "vwap", "structure", "microstructure"):
        assert 0.0 < caps[name] <= 0.5, f"cluster cap for {name} out of range"


# ─── Output shape & backward compat ─────────────────────────────────────────

def test_output_has_legacy_and_v2_blocks():
    snap = _full_bull_snap()
    out = _drive(snap, ticks=5)
    # Legacy keys (dashboard.js iterates these)
    for k in ("score", "trajectory", "trend", "durationSec",
              "anchorMs", "anchorIso", "components", "instantaneous", "weights"):
        assert k in out, f"missing legacy key {k}"
    for legacy_key in ("regime", "bias", "vwap", "vp", "slope", "level", "ib"):
        assert legacy_key in out["components"]
        assert legacy_key in out["instantaneous"]
        assert legacy_key in out["weights"]
    # v2 detail blocks
    for k in ("sourceScores", "sourceReliability", "effectiveWeights",
              "rawSources", "method"):
        assert k in out, f"missing v2 key {k}"
    assert out["method"] == d.CONVICTION_METHOD_VERSION


def test_empty_snapshot_does_not_crash():
    _reset_state()
    out = d.compute_session_conviction(_empty_snap())
    assert out is not None
    assert out["score"] == 0.0
    # Every source must have zero reliability when nothing is plumbed.
    for n, r in out["sourceReliability"].items():
        assert r == 0.0, f"source {n} should have reliability 0 on empty snap, got {r}"


def test_missing_alias_returns_none():
    _reset_state()
    snap = _full_bull_snap()
    snap["alias"] = ""
    assert d.compute_session_conviction(snap) is None


def test_unhealthy_snapshot_returns_none():
    _reset_state()
    snap = _full_bull_snap()
    snap["health"] = "down"
    assert d.compute_session_conviction(snap) is None


# ─── Aligned bull / bear stacks ─────────────────────────────────────────────

def test_strong_bull_stack_reaches_bullish_trend_within_5_min():
    out = _drive(_full_bull_snap(), ticks=300)
    assert out is not None
    assert out["score"] > 0.35, (
        f"strong bull stack should clear TREND threshold — got {out['score']:+.3f} "
        f"trend={out['trend']} sources={out['sourceScores']}"
    )
    assert out["trend"] == "BULLISH_TREND", (
        f"expected BULLISH_TREND, got {out['trend']} score={out['score']:+.3f}"
    )


def test_strong_bear_stack_reaches_bearish_trend_within_5_min():
    out = _drive(_full_bear_snap(), ticks=300)
    assert out["score"] < -0.35, f"got {out['score']:+.3f}"
    assert out["trend"] == "BEARISH_TREND", (
        f"expected BEARISH_TREND, got {out['trend']} score={out['score']:+.3f}"
    )


def test_mixed_sources_do_not_produce_false_trend():
    """Half the cluster bull, half bear → composite must stay in CHOP/MIXED, never
    cross either TREND threshold."""
    snap = _full_bull_snap()
    # Flip flow cluster bearish, keep microstructure / vwap / structure / level bull
    snap["flow"]["regime"]      = "TRENDING_DOWN"
    snap["flow"]["biasScore"]   = -0.7
    snap["flow"]["ofiZ"]        = -3.0
    snap["flow"]["cvdDeltaZ"]   = -3.0
    snap["flow"]["vptZ"]        = -2.0
    out = _drive(snap, ticks=300)
    assert abs(out["score"]) < 0.35, (
        f"mixed stack should NOT clear TREND threshold — got {out['score']:+.3f} "
        f"trend={out['trend']}"
    )
    assert out["trend"] not in ("BULLISH_TREND", "BEARISH_TREND")


def test_flat_snapshot_stays_chop():
    """All sources near zero → composite near zero → CHOP."""
    snap = _empty_snap()
    snap["flow"] = {
        "regime": "BALANCED", "regimeConfidence": 0.5,
        "biasScore": 0.02, "ofiZ": 0.0, "cvdDeltaZ": 0.0, "vptZ": 0.0,
        "vwapSlope": {"label": "FLAT", "slopeZ": 0.0},
        "ib": {"dayType": "UNKNOWN"},
    }
    snap["book"]      = {"mid": 20000.0}
    snap["vwap_obj"]  = {"vwap": 20000.0, "stddev": 10.0, "lastTradePrice": 20000.0}
    snap["vp_bias"]   = {"score": 0.0, "label": "NEUTRAL"}
    snap["pull_stack"]   = {"aggregateZ": 0.0, "rotation": "NONE"}
    snap["lt_liquidity"] = {"bidSize": 500.0, "askSize": 500.0}
    out = _drive(snap, ticks=300)
    assert abs(out["score"]) < 0.10, f"got score {out['score']:+.3f}"
    assert out["trend"] == "CHOP"


# ─── Single-source contributions ────────────────────────────────────────────

def test_pull_stack_positive_aggz_contributes_bullish():
    bull = d._source_pull_stack({"pull_stack":
        {"aggregateZ": 2.0, "rotation": "ROTATION_UP"}})
    assert bull["score"] > 0.5
    assert bull["reliability"] == 1.0
    bear = d._source_pull_stack({"pull_stack":
        {"aggregateZ": -2.0, "rotation": "ROTATION_DN"}})
    assert bear["score"] < -0.5


def test_pull_stack_missing_is_inert():
    assert d._source_pull_stack({"pull_stack": None})["reliability"] == 0.0
    assert d._source_pull_stack({})["reliability"] == 0.0
    err = d._source_pull_stack({"pull_stack": {"_error": "down"}})
    assert err["reliability"] == 0.0


def test_micro_events_iceberg_bid_is_bullish():
    out = d._source_micro_events({"micro_events":
        {"events": [{"kind": "ICEBERG", "isBid": True, "price": 100.0}]}})
    assert out["score"] > 0.0


def test_micro_events_iceberg_ask_is_bearish():
    out = d._source_micro_events({"micro_events":
        {"events": [{"kind": "ICEBERG", "isBid": False, "price": 100.0}]}})
    assert out["score"] < 0.0


def test_micro_events_spoof_is_contrarian():
    """Bid spoof = fake support being pulled → bearish. Ask spoof = bullish."""
    bid_spoof = d._source_micro_events({"micro_events":
        {"events": [{"kind": "SPOOF", "isBid": True, "price": 100.0}]}})
    ask_spoof = d._source_micro_events({"micro_events":
        {"events": [{"kind": "SPOOF", "isBid": False, "price": 100.0}]}})
    assert bid_spoof["score"] < 0.0, f"bid spoof should be bearish, got {bid_spoof['score']}"
    assert ask_spoof["score"] > 0.0, f"ask spoof should be bullish, got {ask_spoof['score']}"


def test_micro_events_stop_sweep_directional():
    # Canonical: STOP_SWEEP isBid=True means bids were swept (sell pressure, bearish).
    bid_sweep = d._source_micro_events({"micro_events":
        {"events": [{"kind": "STOP_SWEEP", "isBid": True, "price": 100.0}]}})
    ask_sweep = d._source_micro_events({"micro_events":
        {"events": [{"kind": "STOP_SWEEP", "isBid": False, "price": 100.0}]}})
    assert bid_sweep["score"] < 0.0, f"bids swept must score bearish, got {bid_sweep['score']}"
    assert ask_sweep["score"] > 0.0, f"asks swept must score bullish, got {ask_sweep['score']}"


def test_source_micro_events_stop_sweep_matches_micro_at_level():
    """_source_micro_events and _micro_at_level must agree on STOP_SWEEP
    direction. Regression: _source_micro_events previously inverted the sign."""
    bid_evt = {"micro_events":
        {"events": [{"kind": "STOP_SWEEP", "isBid": True,
                     "price": 100.0, "timeMs": 0}]}}
    src = d._source_micro_events(bid_evt)["score"]
    lvl, _ = d._micro_at_level(bid_evt["micro_events"], 100.0)
    assert src < 0 and lvl < 0, (
        f"bids swept must be bearish from both sources; "
        f"_source_micro_events={src:+.2f}, _micro_at_level={lvl:+.2f}")
    ask_evt = {"micro_events":
        {"events": [{"kind": "STOP_SWEEP", "isBid": False,
                     "price": 100.0, "timeMs": 0}]}}
    src = d._source_micro_events(ask_evt)["score"]
    lvl, _ = d._micro_at_level(ask_evt["micro_events"], 100.0)
    assert src > 0 and lvl > 0, (
        f"asks swept must be bullish from both sources; "
        f"_source_micro_events={src:+.2f}, _micro_at_level={lvl:+.2f}")


# ─── Orderbook source (V6) ──────────────────────────────────────────────────

def test_source_orderbook_positive_when_bid_heavier():
    out = d._source_orderbook({"flow":
        {"bookPressureTop5": 0.6, "bookPressureTop25": 0.4}})
    # 0.6 * 0.6 + 0.4 * 0.4 = 0.36 + 0.16 = 0.52
    assert out["score"] == pytest.approx(0.52, abs=1e-6)
    assert out["reliability"] == 1.0


def test_source_orderbook_negative_when_ask_heavier():
    out = d._source_orderbook({"flow":
        {"bookPressureTop5": -0.7, "bookPressureTop25": -0.3}})
    assert out["score"] < 0
    assert out["reliability"] == 1.0


def test_source_orderbook_partial_reliability_when_only_top5():
    out = d._source_orderbook({"flow":
        {"bookPressureTop5": 0.5}})
    assert out["score"] == pytest.approx(0.5, abs=1e-6)
    assert out["reliability"] == 0.5


def test_source_orderbook_zero_reliability_when_missing():
    assert d._source_orderbook({})["reliability"] == 0.0
    assert d._source_orderbook({"flow": {}})["reliability"] == 0.0
    assert d._source_orderbook({"flow": {"_error": "boom"}})["reliability"] == 0.0


def test_source_orderbook_score_is_clamped():
    # Synthetic out-of-band input should still clamp to [-1, +1].
    out = d._source_orderbook({"flow":
        {"bookPressureTop5": 5.0, "bookPressureTop25": 5.0}})
    assert -1.0 <= out["score"] <= 1.0


def test_orderbook_is_registered_in_conviction_registry():
    assert "orderbook" in d._CONVICTION_SOURCES
    # Must be callable and obey the source contract.
    out = d._CONVICTION_SOURCES["orderbook"]({"flow":
        {"bookPressureTop5": 0.3, "bookPressureTop25": 0.2}})
    for k in ("score", "reliability", "raw", "reason"):
        assert k in out


def test_orderbook_has_weight_in_microstructure_cluster():
    cfg = d._load_pax_weights()
    sw = cfg.get("conviction_source_weights") or {}
    clusters = cfg.get("conviction_clusters") or {}
    assert sw.get("orderbook", 0.0) > 0.0, "orderbook missing from source weights"
    assert "orderbook" in (clusters.get("microstructure") or []), (
        "orderbook missing from microstructure cluster")


def test_flow_ofi_sign_matches_z():
    assert d._source_flow_ofi({"flow": {"ofiZ": 2.0}})["score"] > 0.0
    assert d._source_flow_ofi({"flow": {"ofiZ": -2.0}})["score"] < 0.0
    assert d._source_flow_ofi({"flow": {}})["reliability"] == 0.0


def test_flow_cvd_sign_matches_z():
    assert d._source_flow_cvd({"flow": {"cvdDeltaZ": 2.0}})["score"] > 0.0
    assert d._source_flow_cvd({"flow": {"cvdDeltaZ": -2.0}})["score"] < 0.0
    assert d._source_flow_cvd({"flow": {}})["reliability"] == 0.0


def test_flow_vpt_absorption_bid_is_bullish():
    out = d._source_flow_vpt_absorption({"flow":
        {"regime": "ABSORPTION_BID", "vptZ": 2.0, "biasScore": 0.0}})
    assert out["score"] > 0.4


def test_flow_vpt_absorption_ask_is_bearish():
    out = d._source_flow_vpt_absorption({"flow":
        {"regime": "ABSORPTION_ASK", "vptZ": 2.0, "biasScore": 0.0}})
    assert out["score"] < -0.4


def test_vwap_dislocation_blowoff_is_mean_revert():
    snap = {"flow": {"vwapSlope": {"label": "FLAT", "slopeZ": 0.0}},
            "book": {"mid": 20040.0},
            "vwap_obj": {"vwap": 20000.0, "stddev": 10.0, "lastTradePrice": 20040.0}}
    out = d._source_vwap_dislocation(snap)
    # +4σ above VWAP without strong slope → exhaustion → bearish
    assert out["score"] < 0.0


def test_vwap_dislocation_strong_trend_aligned_is_bull():
    snap = {"flow": {"vwapSlope": {"label": "STRONG_UP", "slopeZ": 2.5}},
            "book": {"mid": 20040.0},
            "vwap_obj": {"vwap": 20000.0, "stddev": 10.0, "lastTradePrice": 20040.0}}
    out = d._source_vwap_dislocation(snap)
    # +4σ but strong-up slope → trend continuation → modestly bull
    assert out["score"] > 0.0


def test_avwap_missing_zero_reliability():
    """The bridge does not ship avwap yet → reliability must be 0 so the source
    drops cleanly out of the composite."""
    out = d._source_anchored_vwap_opening_drive({"flow": {}})
    assert out["reliability"] == 0.0


def test_ib_context_no_break_direction_zero():
    out = d._source_ib_context({"flow": {"ib": {"dayType": "TREND"}}})
    assert out["reliability"] == 0.0


def test_ib_context_with_break_above_is_bullish():
    out = d._source_ib_context({
        "flow": {"ib": {"high": 20000.0, "low": 19980.0, "complete": True,
                        "dayType": "TREND"}},
        "book": {"mid": 20010.0},
    })
    assert out["score"] > 0.0
    assert out["reliability"] == 1.0


def test_lt_liquidity_bid_heavy_is_bullish():
    out = d._source_lt_liquidity({"lt_liquidity": {"bidSize": 1000, "askSize": 200}})
    assert out["score"] > 0.0


def test_lt_liquidity_ask_heavy_is_bearish():
    out = d._source_lt_liquidity({"lt_liquidity": {"bidSize": 200, "askSize": 1000}})
    assert out["score"] < 0.0


def test_tape_large_lot_uses_tape_flow_when_present():
    """The source prefers snap['tape_flow'].deltaScore (computed by
    compute_tape_flow from the /tape_buckets bucket array). Full coverage
    of compute_tape_flow lives in test_tape_flow.py — this just pins the
    source's contract with the conviction engine."""
    out = d._source_tape_large_lot({"tape_flow": {
        "deltaScore": 0.55, "deltaLabel": "STRONG_BUY",
        "largePrints30s": 12, "totalPrints30s": 60,
        "deltaReason": "test"}})
    assert out["score"] > 0.0
    assert out["reliability"] == 1.0   # 12 large prints → saturated


def test_tape_large_lot_falls_back_to_bucket_array_at_capped_reliability():
    """When tape_flow is absent, the source recomputes from tape_buckets
    directly at <= 0.7 reliability (fallback cap)."""
    snap = {"tape_buckets": {"buckets": [
        {"label": "100+", "minSize": 0, "maxSize": 0,
         "buyVol30s": 1500, "sellVol30s": 100, "prints30s": 20,
         "buyVol5m":  1500, "sellVol5m":  100, "prints5m":  20,
         "imbalance30s": 0.0, "imbalance5m": 0.0},
    ]}}
    out = d._source_tape_large_lot(snap)
    assert out["score"] > 0.0
    assert out["reliability"] <= 0.7


def test_level_reaction_no_proximity_is_low_reliability():
    out = d._source_level_reaction({"or_levels": {
        "inProximity": False,
        "levels": [{"proximity": False, "decision": "ENTER_LONG_FOLLOW", "confidence": 0.8}],
    }})
    assert out["reliability"] <= 0.5


def test_regime_balanced_is_low_reliability():
    out = d._source_regime({"flow": {"regime": "BALANCED", "regimeConfidence": 1.0}})
    assert out["score"] == 0.0
    assert out["reliability"] <= 0.5


# ─── Cluster caps & weight normalization ────────────────────────────────────

def test_cluster_caps_limit_correlated_sources():
    """The flow cluster's four sources sum to 0.42 raw. Effective cluster
    weight after caps must be at most the configured cap (0.35)."""
    out = _drive(_full_bull_snap(), ticks=30)
    eff = out["effectiveWeights"]
    caps = d._load_pax_weights().get("conviction_cluster_caps") or {}
    flow_members = d._load_pax_weights()["conviction_clusters"]["flow"]
    flow_total = sum(abs(eff[m]) for m in flow_members)
    assert flow_total <= caps["flow"] + 1e-6, (
        f"flow cluster {flow_total:.4f} exceeds cap {caps['flow']}"
    )


def test_unavailable_source_has_zero_effective_weight():
    out = _drive(_full_bull_snap(), ticks=30)
    # avwap and ib_context are not plumbed in the test snap → eff weight zero.
    assert out["effectiveWeights"]["anchored_vwap_opening_drive"] == 0.0
    assert out["effectiveWeights"]["ib_context"] == 0.0
    assert out["sourceReliability"]["anchored_vwap_opening_drive"] == 0.0


def test_score_is_clipped_to_unit_interval():
    out = _drive(_full_bull_snap(), ticks=300)
    assert -1.0 <= out["score"] <= 1.0


# ─── Trajectory & session reset ─────────────────────────────────────────────

def test_trajectory_warmup_on_first_tick():
    _reset_state()
    out = d.compute_session_conviction(_full_bull_snap())
    assert out["trajectory"] == "WARMUP"


def test_trajectory_rising_after_signal_climbs():
    """Drive a few ticks of flat, then several of strong bull. The recent SMA
    (last 30s) should outrun the older one (30-90s), producing RISING / RISING_STRONG.
    """
    _reset_state()
    flat = _empty_snap()
    flat["flow"]   = {"regime": "BALANCED", "regimeConfidence": 0.5,
                       "biasScore": 0.0, "ofiZ": 0.0, "cvdDeltaZ": 0.0, "vptZ": 0.0,
                       "vwapSlope": {"label": "FLAT", "slopeZ": 0.0}}
    flat["book"]   = {"mid": 20000.0}
    flat["vwap_obj"] = {"vwap": 20000.0, "stddev": 10.0, "lastTradePrice": 20000.0}
    flat["vp_bias"]  = {"score": 0.0, "label": "NEUTRAL"}
    flat["pull_stack"]   = {"aggregateZ": 0.0, "rotation": "NONE"}
    flat["lt_liquidity"] = {"bidSize": 500, "askSize": 500}

    alias = flat["alias"]
    # 40 ticks flat
    for _ in range(40):
        d.compute_session_conviction(flat)
        _slide_state_back(alias, 1000)
    # 40 ticks strong bull
    bull = _full_bull_snap(alias)
    last = None
    for _ in range(40):
        last = d.compute_session_conviction(bull)
        _slide_state_back(alias, 1000)
    assert last["trajectory"] in ("RISING", "RISING_STRONG"), (
        f"expected RISING/RISING_STRONG after flat→bull transition, got {last['trajectory']}"
    )


def test_trajectory_falling_after_signal_drops():
    _reset_state()
    bull = _full_bull_snap()
    alias = bull["alias"]
    for _ in range(40):
        d.compute_session_conviction(bull)
        _slide_state_back(alias, 1000)
    bear = _full_bear_snap(alias)
    last = None
    for _ in range(40):
        last = d.compute_session_conviction(bear)
        _slide_state_back(alias, 1000)
    assert last["trajectory"] in ("FALLING", "FALLING_STRONG")


def test_session_reset_clears_accumulators():
    """When the 08:30 CT anchor advances, every source ring + score ring
    must be wiped."""
    _reset_state()
    snap = _full_bull_snap()
    alias = snap["alias"]
    for _ in range(20):
        d.compute_session_conviction(snap)
        _slide_state_back(alias, 1000)
    assert any(len(s["ring"]) > 0 for s in d._CONVICTION_STATE[alias]["sources"].values())

    # Force stale anchor → simulate the daily 08:30 crossover.
    d._CONVICTION_STATE[alias]["anchorMs"] = 0
    d.compute_session_conviction(snap)
    # After reset, only the just-pushed sample (the current tick) should be in
    # each source ring — far smaller than the pre-reset 20-deep ring.
    for name, src in d._CONVICTION_STATE[alias]["sources"].items():
        assert len(src["ring"]) <= 1, (
            f"source {name} ring not reset, still has {len(src['ring'])} entries"
        )
    # Score ring must also have collapsed to a single sample.
    assert len(d._CONVICTION_STATE[alias]["scoreRing"]) <= 1


def test_session_reset_zeroes_session_sums():
    _reset_state()
    snap = _full_bull_snap()
    alias = snap["alias"]
    for _ in range(20):
        d.compute_session_conviction(snap)
        _slide_state_back(alias, 1000)
    pre = d._CONVICTION_STATE[alias]["sources"]["flow_ofi"]
    assert pre["sessionCount"] > 0
    assert pre["sessionSum"] != 0.0
    d._CONVICTION_STATE[alias]["anchorMs"] = 0
    d.compute_session_conviction(snap)
    post = d._CONVICTION_STATE[alias]["sources"]["flow_ofi"]
    assert post["sessionCount"] == 1
