from bookmap_mcp import pax_brain as B
from bookmap_mcp import pax_expectancy as E
from bookmap_mcp import pax_loop


def snap(dec="ENTER_LONG_FOLLOW", conf=0.6, label="OR-H", price=30340.0,
         prox=True, orH=30340.0, orL=30325.5, mid=30339.0,
         tape=None, trend=None, flow=None, ifl=None):
    lvl = {"label": label, "price": price, "distance": price - mid,
           "proximity": prox, "decision": dec, "confidence": conf,
           "components": {"ps_rot": "NONE"}}
    return {"health": "ok", "book": {"mid": mid},
            "tape_flow": tape or {}, "trend_signal": trend or {},
            "flow": flow or {}, "institutional_flow": ifl or {},
            "or_levels": {"orHigh": orH, "orLow": orL,
                          "orWidthPts": round(orH - orL, 2),
                          "inProximity": prox,
                          "levels": [lvl] if prox else []}}


def test_market_state_inside_or():
    st = B.classify_market_state(snap(mid=30330.0))
    assert st.code == "INSIDE_OR"


def test_money_score_uses_multiple_sources():
    s = snap(
        tape={"deltaScore": 0.5, "deltaLabel": "BUY"},
        trend={"kind": "STRONG_BULL"},
        flow={"ofiZ": 1.0, "cvdDeltaZ": 0.5, "biasScore": 0.3,
              "regime": "TRENDING_UP"},
        ifl={"weighted_vote": 0.25, "regime": "ACCUMULATION"},
    )
    assert B.money_score(s) > 0.4
    assert B.directional_evidence_count(s, "LONG") >= 4


def test_detects_or_break_accept():
    setups = B.detect_setups(snap())
    assert setups[0].kind == "OR_BREAK_ACCEPT"
    assert setups[0].side == "LONG"


def test_detects_or_sweep_reclaim_only_when_money_agrees():
    weak = snap(dec="ENTER_LONG_FADE", label="OR-L", price=30325.5,
                mid=30326.0, flow={"ofiZ": -1.0})
    assert B.detect_setups(weak) == []
    strong = snap(dec="ENTER_LONG_FADE", label="OR-L", price=30325.5,
                  mid=30326.0, flow={"ofiZ": 1.2, "cvdDeltaZ": 0.7})
    setups = B.detect_setups(strong)
    assert setups and setups[0].kind == "OR_SWEEP_RECLAIM"


def test_off_level_drive_requires_more_than_one_source():
    tape_only = snap(prox=False, mid=30395.0,
                     tape={"deltaScore": 0.8, "deltaLabel": "BUY"})
    assert B.detect_setups(tape_only) == []
    aligned = snap(prox=False, mid=30395.0,
                   tape={"deltaScore": 0.8, "deltaLabel": "BUY"},
                   trend={"kind": "STRONG_BULL"})
    setups = B.detect_setups(aligned)
    assert setups and setups[0].kind == "OFF_LEVEL_AUCTION_DRIVE"


def test_best_thesis_carries_invalidation_and_expectancy():
    th = B.best_thesis(
        snap(),
        session_type="RTH",
        payline=pax_loop.PAYLINE,
        rung=pax_loop.RUNG,
        stop_breathing_pts=pax_loop.STOP_BREATHING_PTS,
        entry_slip_ticks=pax_loop.ENTRY_SLIP_TICKS,
        tick=pax_loop.TICK,
        trend_entry_offset_ticks=pax_loop.TREND_ENTRY_OFFSET_TICKS,
    )
    assert th is not None
    assert th.setup.kind == "OR_BREAK_ACCEPT"
    assert th.invalidation
    assert th.expectancy > 0
    assert th.expectancy_source == "heuristic"


def test_best_thesis_uses_learned_expectancy_overlay():
    baseline = B.best_thesis(
        snap(),
        session_type="RTH",
        payline=pax_loop.PAYLINE,
        rung=pax_loop.RUNG,
        stop_breathing_pts=pax_loop.STOP_BREATHING_PTS,
        entry_slip_ticks=pax_loop.ENTRY_SLIP_TICKS,
        tick=pax_loop.TICK,
        trend_entry_offset_ticks=pax_loop.TREND_ENTRY_OFFSET_TICKS,
    )
    stats = {
        E.setup_key("*", "LONG", "OR-H", "*"):
            E.ExpectancyStats(n=12, avg_r=-0.8, hit_rate=0.1,
                              partial_rate=0.1, miss_rate=0.8)
    }
    learned = B.best_thesis(
        snap(),
        session_type="RTH",
        payline=pax_loop.PAYLINE,
        rung=pax_loop.RUNG,
        stop_breathing_pts=pax_loop.STOP_BREATHING_PTS,
        entry_slip_ticks=pax_loop.ENTRY_SLIP_TICKS,
        tick=pax_loop.TICK,
        trend_entry_offset_ticks=pax_loop.TREND_ENTRY_OFFSET_TICKS,
        expectancy_stats=stats,
    )
    assert baseline is not None and learned is not None
    assert learned.expectancy < baseline.expectancy
    assert learned.expectancy_source.startswith("learned:n=12")
