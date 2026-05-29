"""Pins pax_loop.decide() — the deterministic baseline policy + risk governor.

Sign conventions and geometry are load-bearing; the agentic sim trader uses
this as its advisory baseline and the live loop executes it.
"""
import datetime

from bookmap_mcp import pax_loop

NOW = datetime.datetime(2026, 5, 28, 19, 30, 0)
NOW_MS = 1_900_000_000_000


def snap(dec="ENTER_LONG_FOLLOW", conf=0.6, label="OR-H", price=30340.0,
         prox=True, orH=30340.0, orL=30325.5, mid=30339.0, ps_rot="NONE",
         health="ok", anchor="LIVE", code="ACTIVE", stype="ETH",
         middleLock=False, news=False, tape=None, trend=None, conviction=None,
         flow=None):
    lvl = {"label": label, "price": price, "distance": price - mid,
           "proximity": prox, "decision": dec, "confidence": conf,
           "components": {"ps_rot": ps_rot}}
    return {"health": health, "book": {"mid": mid},
            "session": {"anchorMode": anchor, "code": code},
            "or_day_ledger": {"session_type": stype},
            "gates": {"news": {"blocked": news}},
            "tape_flow": tape or {},
            "trend_signal": trend or {},
            "conviction": conviction or {},
            "flow": flow or {},
            "or_levels": {"orHigh": orH, "orLow": orL,
                          "orWidthPts": round(orH - orL, 2),
                          "inProximity": prox, "middleLock": middleLock,
                          "levels": [lvl]}}


def status(size=0, losers=0, working=None, last_exit_ms=None):
    fills = [{"role": "STOP", "filled_ms": last_exit_ms}] if last_exit_ms else []
    return {"position": {"size": size}, "losers_today": losers,
            "working": working or [], "fills_today": fills}


def D(s, st):
    return pax_loop.decide(s, st, NOW, NOW_MS)


def test_follow_long_breakout_places_stop_limit_at_orh():
    p = D(snap(), status())
    mid_or = (30340.0 + 30325.5) / 2
    assert p["state"] == "PLACE" and p["action"] == "PLACE_LONG"
    o = p["order"]
    assert o["entry_stop"] == 30340.0           # trigger at the level
    assert o["entry_limit"] == 30340.5          # +2 ticks slippage cap
    assert o["stop_loss"] == round(mid_or - pax_loop.STOP_BREATHING_PTS, 2)
    assert o["stop_loss"] < o["entry_stop"]
    assert o["tps"] == [30350.0, 30405.0]       # level +10, level +65


def test_follow_short_breakdown_at_orl():
    p = D(snap(dec="ENTER_SHORT_FOLLOW", label="OR-L", price=30325.5, mid=30326.5),
          status())
    mid_or = (30340.0 + 30325.5) / 2
    assert p["state"] == "PLACE" and p["action"] == "PLACE_SHORT"
    assert p["order"]["stop_loss"] == round(mid_or + pax_loop.STOP_BREATHING_PTS, 2)
    assert p["order"]["stop_loss"] > p["order"]["entry_stop"]


def test_below_floor_armed():
    assert D(snap(conf=0.2), status())["state"] == "ARMED"


def test_fade_deferred():
    p = D(snap(dec="ENTER_SHORT_FADE"), status())
    assert p["state"] == "ARMED" and p["order"] is None


def test_daily_stop_is_loose_backstop():
    # SIM-aggressive: a couple losses must NOT halt (re-entry model needs room).
    assert D(snap(), status(losers=2))["state"] == "PLACE"
    # The backstop still fires at the (loose) DAILY_STOP_LOSERS threshold.
    p = D(snap(), status(losers=pax_loop.DAILY_STOP_LOSERS))
    assert p["state"] == "SIT" and "daily stop" in p["reason"]


def test_no_cooldown_allows_reentry():
    # SIM-aggressive: COOLDOWN_MIN=0 -> a recent exit does NOT block re-entry.
    p = D(snap(), status(last_exit_ms=NOW_MS - 2 * 60_000))
    assert p["state"] == "PLACE"


def test_working_entry_held_while_valid():
    # SimEngine stores side uppercase ("BUY") -- the hold check must match it.
    p = D(snap(), status(working=[{"role": "ENTRY", "side": "BUY"}]))
    assert p["state"] == "WORKING" and p["order"] is None


def test_working_entry_held_through_weak_read():
    # A weak/WAIT level must NOT cancel a resting entry (that was the churn that
    # never let it fill). Hold it so price can reach the order.
    p = D(snap(dec="WAIT", conf=0.0),
          status(working=[{"role": "ENTRY", "side": "BUY"}]))
    assert p["state"] == "WORKING"


def test_working_entry_cancelled_on_opposite_reversal():
    # A clear OPPOSITE read (SHORT conf>=floor) cancels a resting BUY entry.
    p = D(snap(dec="ENTER_SHORT_FOLLOW", conf=0.6, label="OR-L",
               price=30325.5, mid=30326.5),
          status(working=[{"role": "ENTRY", "side": "BUY"}]))
    assert p["state"] == "CANCEL" and p["cancel"] is True


def test_in_position_flatten_on_reentry():
    p = D(snap(mid=30332.0), status(size=2))
    assert p["state"] == "ROTATION" and p["flatten"] is True


def test_in_position_manage_outside_or():
    p = D(snap(mid=30360.0), status(size=2))
    assert p["state"] == "MANAGE" and p["flatten"] is False


def test_stale_anchor_cancels_working_entry():
    p = D(snap(anchor="LAST_KNOWN_STALE"),
          status(working=[{"role": "ENTRY", "side": "buy"}]))
    assert p["cancel"] is True


def test_rotation_veto():
    p = D(snap(ps_rot="ROTATION_DN"), status())
    assert p["state"] == "ARMED" and "vetoed" in p["reason"]


def test_rth_floor_and_width():
    rf = pax_loop.PROFILE["RTH"]["floor"]
    assert D(snap(conf=rf - 0.1, stype="RTH"), status())["state"] == "ARMED"
    assert D(snap(conf=rf + 0.1, stype="RTH"), status())["state"] == "PLACE"
    assert D(snap(conf=0.6, stype="RTH", orH=30380.0, orL=30340.0,
                  price=30380.0, mid=30379.0), status())["state"] == "PLACE"


def test_eth_wide_or_blocked():
    assert D(snap(orH=30380.0, orL=30340.0, price=30380.0, mid=30379.0),
             status())["state"] == "SIT"


def test_off_level_trend_follow_long_places_stop_limit():
    s = snap(
        prox=False, label="+1", price=30405.0, mid=30395.0,
        tape={"deltaScore": 0.35, "deltaLabel": "BUY"},
        trend={"kind": "STRONG_BULL", "eligible": True},
        conviction={"score": 0.48, "trend": "BULLISH_TREND"},
        flow={"biasScore": 0.30, "regime": "TRENDING_UP"},
    )
    p = D(s, status())
    assert p["state"] == "PLACE" and p["action"] == "PLACE_LONG"
    assert p["level"] == "TREND"
    assert p["order"]["entry_stop"] == 30395.5
    assert p["order"]["entry_limit"] == 30396.0
    assert "TREND_FOLLOW LONG" in p["order"]["reason"]


def test_off_level_without_aligned_trend_still_waits():
    p = D(snap(prox=False, label="+1", price=30405.0, mid=30395.0,
               tape={"deltaScore": 0.35, "deltaLabel": "BUY"}),
          status())
    assert p["state"] == "SIT"
    assert "no aligned trend-follow read" in p["reason"]


def test_off_level_inside_or_middle_lock_still_blocks():
    s = snap(prox=False, mid=30332.0, middleLock=True,
             tape={"deltaScore": 0.8, "deltaLabel": "STRONG_BUY"},
             trend={"kind": "STRONG_BULL", "eligible": True},
             conviction={"score": 0.7, "trend": "BULLISH_TREND"},
             flow={"biasScore": 0.7, "regime": "TRENDING_UP"})
    p = D(s, status())
    assert p["state"] == "SIT"
    assert "middleLock" in p["reason"]
