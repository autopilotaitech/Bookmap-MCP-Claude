"""pax_algo_v1 — shared deterministic baseline policy + risk governor.

This is the pure, importable core lifted out of the session loop script. It has
NO I/O and NO side effects: given a dashboard snapshot + a SimEngine status dict
+ the current time, `decide()` returns a plan dict describing what the
deterministic Pax OR rule would do.

Consumers:
- `_session_snapshots/eth_loop_tick.py` — the live sim loop (executes the plan).
- the agentic sim trader (P2+) — runs `decide()` as the ADVISORY baseline it is
  measured against (the agent may override, but the learning loop compares
  agent-vs-rule expectancy).
- tests — `mcp-server/tests/test_pax_loop.py`.

The risk GOVERNOR is the set of deterministic gates inside `decide()` (sim-only
session-stop, in-position management, anchor/news/session gates, daily-stop,
proximity/width gates, cooldown, no-stack-while-resting). The agent cannot
disable these — they bound how much it can hurt the sim, not what it trades.

Rule summary (operator-locked 2026-05-28): react to `snap.or_levels` (the
dashboard already resolved FOLLOW/FADE -> decision per level). Entry = resting
order AT the level, let price come to it. FOLLOW breakout only (OR-H long /
OR-L short); FADE + rung deferred. Stop = OR midpoint +/- breathing room. TP1 =
level +/- payline, TP2 = level +/- first 65pt rung. Confidence floor by
session_type. Full spec: _session_snapshots/eth_trade_loop_spec.md.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from . import pax_brain, pax_runtime_policy
from .pax_expectancy import ExpectancyStats

# --- rule profile (Phase 1 will externalize to pax_rules.json) ---------------
# Floors LOWERED for the sim-aggressive mandate (operator 2026-05-29: "trade the
# fuck out of it, more data = better trader, don't be conservative"). Live RTH
# per-level conviction ran 0.10-0.41, so the old 0.50/0.35 floors never fired.
# These are deliberately loose LEARNING floors -> more trades + more data; the
# re-entry / tight-stop model absorbs the extra losers. Tune from data (P3).
PROFILE = {
    "ETH": {"floor": 0.25, "width": (3.0, 25.0)},
    "RTH": {"floor": 0.30, "width": (3.0, 60.0)},
}
PAYLINE = 10.0            # TP1 distance from level
RUNG = 65.0              # TP2 = first NQ rotation rung from level
TICK = 0.25
ENTRY_SLIP_TICKS = 2     # stop-limit entry limit = trigger +/- this (slippage cap)
STOP_BREATHING_PTS = 15.0  # stop placed this far beyond OR mid. NQ needs real
                           # room (operator: >=5pt, "maybe even 50 until data
                           # shows what works"). Generous default while learning;
                           # this is THE param the calibration sweep tunes (P3).
                           # Tradeoff: too wide vs the 65pt rung target hurts R:R.
# SIM-AGGRESSIVE (operator 2026-05-28: "it's a sim, trade the fuck out of it,
# more data = better trader, don't be conservative"). Re-entry IS the edge
# (tight stops, re-enter to catch; 1 rotation covers ~3 scratch losses). So no
# cooldown, and the daily stand-down is a LOOSE backstop, not a 2-loss halt.
# These are tunable from data (P2 will make the stand-down net-R based).
COOLDOWN_MIN = 0         # 0 = re-enter freely (one-position-at-a-time still holds)
DAILY_STOP_LOSERS = 8    # loose backstop only (was 2); lets the re-entry model run
STOP_CT_HOUR = None      # auto session-stop hour CT; None = operator stops manually
BLOCK_CODES = ("PRE_MARKET", "OR_FORMING", "CLOSE_RISK", "POST_MARKET")
EXIT_ROLES = ("STOP", "TP", "FLATTEN", "EOD")
TREND_MIN_TAPE = 0.25
TREND_MIN_CONVICTION = 0.35
TREND_ENTRY_OFFSET_TICKS = 2


def working_entry(status: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for w in (status.get("working") or []):
        if w.get("role") == "ENTRY":
            return w
    return None


def last_exit_ms(status: Dict[str, Any]) -> Optional[int]:
    ms = [f.get("filled_ms") for f in (status.get("fills_today") or [])
          if f.get("role") in EXIT_ROLES and f.get("filled_ms")]
    return max(ms) if ms else None


def _f(x: Any) -> Optional[float]:
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _trend_follow_side(snap: Dict[str, Any], mid: Optional[float],
                       orH: Optional[float], orL: Optional[float]) -> Optional[str]:
    """Return LONG/SHORT when non-level trend context is strong enough.

    This is intentionally separate from the OR/rung reaction path. It only
    applies when price has already escaped the OR, so it does not turn the
    middle of the opening range into a chase zone.
    """
    if mid is None or orH is None or orL is None:
        return None
    outside_up = mid > orH
    outside_dn = mid < orL
    if not outside_up and not outside_dn:
        return None

    tape = snap.get("tape_flow") or {}
    tape_score = _f(tape.get("deltaScore")) or 0.0
    tape_label = str(tape.get("deltaLabel") or "").upper()
    tape_buy = tape_score >= TREND_MIN_TAPE or tape_label in ("BUY", "STRONG_BUY")
    tape_sell = tape_score <= -TREND_MIN_TAPE or tape_label in ("SELL", "STRONG_SELL")

    trend_signal = snap.get("trend_signal") or {}
    trend_kind = str(trend_signal.get("kind") or "").upper()
    trend_bull = trend_kind in ("WEAK_BULL", "STRONG_BULL")
    trend_bear = trend_kind in ("WEAK_BEAR", "STRONG_BEAR")

    conv = snap.get("conviction") or {}
    conv_score = _f(conv.get("score")) or 0.0
    conv_trend = str(conv.get("trend") or "").upper()
    conv_bull = conv_score >= TREND_MIN_CONVICTION or conv_trend in (
        "BULL_LEAN", "BULLISH_TREND")
    conv_bear = conv_score <= -TREND_MIN_CONVICTION or conv_trend in (
        "BEAR_LEAN", "BEARISH_TREND")

    flow = snap.get("flow") or {}
    flow_score = _f(flow.get("biasScore")) or 0.0
    flow_regime = str(flow.get("regime") or "").upper()
    flow_bull = flow_score >= TREND_MIN_CONVICTION or flow_regime in (
        "TRENDING_UP", "ACCUMULATION")
    flow_bear = flow_score <= -TREND_MIN_CONVICTION or flow_regime in (
        "TRENDING_DOWN", "DISTRIBUTION")

    bull_votes = sum(1 for v in (tape_buy, trend_bull or conv_bull, flow_bull) if v)
    bear_votes = sum(1 for v in (tape_sell, trend_bear or conv_bear, flow_bear) if v)
    if outside_up and bull_votes >= 2 and bull_votes > bear_votes:
        return "LONG"
    if outside_dn and bear_votes >= 2 and bear_votes > bull_votes:
        return "SHORT"
    return None


def _order_from_thesis(thesis: pax_brain.Thesis) -> Dict[str, Any]:
    slip = ENTRY_SLIP_TICKS * TICK
    if thesis.setup.side == "LONG":
        entry_limit = round(thesis.entry + slip, 2)
        sidecmd = "long"
    else:
        entry_limit = round(thesis.entry - slip, 2)
        sidecmd = "short"
    reason = thesis.why_now
    if thesis.setup.kind == "OFF_LEVEL_AUCTION_DRIVE":
        reason = f"v6 TREND_FOLLOW {thesis.setup.side} {reason}"
    else:
        reason = f"v6 {reason}"
    return {
        "sidecmd": sidecmd,
        "qty": 2,
        "entry_stop": thesis.entry,
        "entry_limit": entry_limit,
        "stop_loss": thesis.stop,
        "tps": thesis.targets,
        "level": thesis.setup.level,
        "side": thesis.setup.side,
        "setup_type": thesis.setup.kind,
        "expectancy": thesis.expectancy,
        "expectancy_source": thesis.expectancy_source,
        "invalidation": thesis.invalidation,
        "reason": reason,
        "tag": thesis.tag,
    }


def decide(snap: Dict[str, Any], status: Dict[str, Any],
           now_dt, now_ms: int,
           expectancy_stats: Optional[Mapping[str, ExpectancyStats]] = None,
           runtime_policy: Optional[Mapping[str, Any]] = None,
           ) -> Dict[str, Any]:
    """Pure decision. Returns a plan dict (no side effects)."""
    ol = snap.get("or_levels") or {}
    ses = snap.get("session") or {}
    mid = (snap.get("book") or {}).get("mid")
    orH, orL = ol.get("orHigh"), ol.get("orLow")
    orW = ol.get("orWidthPts")
    anchor, code = ses.get("anchorMode"), ses.get("code")
    stype = (snap.get("or_day_ledger") or {}).get("session_type") or "ETH"
    prof = PROFILE.get(stype, PROFILE["ETH"])
    floor, (wlo, whi) = prof["floor"], prof["width"]
    news_blocked = bool(((snap.get("gates") or {}).get("news") or {}).get("blocked"))

    pos = (status.get("position") or {}).get("size") or 0
    losers = status.get("losers_today") or 0
    wentry = working_entry(status)
    last_exit = last_exit_ms(status)

    # proximate level (dashboard already resolved FOLLOW/FADE -> decision/side)
    prox = None
    for l in (ol.get("levels") or []):
        if l.get("proximity"):
            if prox is None or abs(l.get("distance") or 9e9) < abs(prox.get("distance") or 9e9):
                prox = l

    p = {"state": "SIT", "action": "NONE", "side": None, "level": None,
         "reason": "", "order": None, "flatten": False, "cancel": False,
         "floor": floor, "stype": stype, "mid": mid, "orH": orH, "orL": orL,
         "orW": orW, "code": code, "anchorMode": anchor,
         "inProx": ol.get("inProximity"),
         "prox_decision": (prox or {}).get("decision"),
         "prox_conf": (prox or {}).get("confidence"), "pos_size": pos,
         "market_state": pax_brain.classify_market_state(snap).code,
         "money_score": pax_brain.money_score(snap),
         "setup_type": None, "expectancy": None, "expectancy_source": None,
         "invalidation": None, "runtime_policy": None}

    def cancel_stale(why):
        if wentry:
            p.update(state="CANCEL", action="CANCEL_ENTRY", cancel=True,
                     reason=f"resting entry cancelled: {why}")
        else:
            p["reason"] = why

    # 0) session auto-stop (disabled unless STOP_CT_HOUR set)
    if STOP_CT_HOUR is not None and now_dt.hour >= STOP_CT_HOUR:
        p.update(state="STOP", reason=f"session stop {STOP_CT_HOUR}:00 CT")
        if pos:
            p.update(action="FLATTEN", flatten=True)
        return p

    # 1) IN POSITION -> manage first, above all entry gates
    if pos != 0:
        if snap.get("health") == "ok" and mid and orL is not None and orH is not None \
                and orL < mid < orH:
            p.update(state="ROTATION", action="FLATTEN", flatten=True,
                     reason="price re-entered OR (failed break)")
        else:
            p.update(state="MANAGE", reason=f"in position {pos}; bracket riding")
        return p

    # 2) entry gates (flat). Each gate cancels a stale resting entry.
    if snap.get("health") != "ok":
        p["reason"] = "bridge not ok"; return p
    if anchor != "LIVE":
        cancel_stale(f"anchorMode={anchor} (informational only)"); return p
    if code in BLOCK_CODES:
        cancel_stale(f"session code={code}"); return p
    if news_blocked:
        cancel_stale("news blackout"); return p
    if losers >= DAILY_STOP_LOSERS:
        cancel_stale(f"daily stop ({losers} losers)"); return p
    if orW is not None and not (wlo <= orW <= whi):
        cancel_stale(f"OR width {orW} out of [{wlo},{whi}]"); return p
    if ol.get("middleLock"):
        cancel_stale("middleLock (inside OR, no level)"); return p

    # A resting entry already works -> HOLD it so price can actually reach it.
    # The fast heartbeat re-decides every ~15s; cancelling on every neutral
    # flicker / not-in-proximity / conf dip is what churned place->cancel->place
    # and never let the order fill. Cancel ONLY on a clear OPPOSITE reversal.
    if wentry:
        wside = str(wentry.get("side") or "").lower()   # "buy" / "sell"
        tside = _trend_follow_side(snap, _f(mid), _f(orH), _f(orL))
        pdec = (prox or {}).get("decision") or ""
        pconf = (prox or {}).get("confidence") or 0.0
        opp = ((wside == "buy" and (tside == "SHORT"
                or ("SHORT" in pdec and pconf >= floor)))
               or (wside == "sell" and (tside == "LONG"
                or ("LONG" in pdec and pconf >= floor))))
        if opp:
            cancel_stale(f"reversal against resting {wside} entry")
        else:
            p.update(state="WORKING", side=wside,
                     level=(prox or {}).get("label") or "TREND",
                     reason=f"resting {wside} entry working; let price reach it")
        return p

    thesis = pax_brain.best_thesis(
        snap,
        session_type=stype,
        payline=PAYLINE,
        rung=RUNG,
        stop_breathing_pts=STOP_BREATHING_PTS,
        entry_slip_ticks=ENTRY_SLIP_TICKS,
        tick=TICK,
        trend_entry_offset_ticks=TREND_ENTRY_OFFSET_TICKS,
        expectancy_stats=expectancy_stats,
    )
    if thesis is None:
        if prox is None or not ol.get("inProximity"):
            cancel_stale("not in proximity of any level and no aligned trend-follow read")
        else:
            dec = prox.get("decision") or "WAIT"
            conf = prox.get("confidence") or 0.0
            kind = "fade (deferred v6)" if "FADE" in str(dec) else "no actionable setup"
            p.update(state="ARMED", level=prox.get("label"),
                     reason=f"{prox.get('label')} dec={dec} conf={round(conf,2)} ({kind})")
        return p

    pol = pax_runtime_policy.lookup_policy(
        runtime_policy,
        setup_type=thesis.setup.kind,
        side=thesis.setup.side,
        level=thesis.setup.level,
        session_type=stype,
    )
    p["runtime_policy"] = pol

    rot = ((prox or {}).get("components") or {}).get("ps_rot")
    if (thesis.setup.side == "LONG" and rot == "ROTATION_DN") or \
            (thesis.setup.side == "SHORT" and rot == "ROTATION_UP"):
        p.update(state="ARMED", level=thesis.setup.level,
                 setup_type=thesis.setup.kind, expectancy=thesis.expectancy,
                 expectancy_source=thesis.expectancy_source,
                 invalidation=thesis.invalidation,
                 reason=f"{thesis.setup.level} {thesis.setup.kind} "
                        f"conf={round(thesis.setup.confidence,2)} vetoed by {rot}")
        return p

    if pol.get("action") == "THROTTLE":
        p.update(state="ARMED", level=thesis.setup.level,
                 setup_type=thesis.setup.kind, expectancy=thesis.expectancy,
                 expectancy_source=thesis.expectancy_source,
                 invalidation=thesis.invalidation,
                 reason=f"{thesis.setup.level} {thesis.setup.kind} "
                        f"blocked by runtime THROTTLE: {pol.get('reason')}")
        return p

    effective_floor = pax_runtime_policy.adjusted_floor(
        float(floor), str(pol.get("action") or "KEEP"))
    if thesis.setup.confidence < effective_floor:
        p.update(state="ARMED", level=thesis.setup.level,
                 setup_type=thesis.setup.kind, expectancy=thesis.expectancy,
                 expectancy_source=thesis.expectancy_source,
                 invalidation=thesis.invalidation,
                 reason=f"{thesis.setup.level} {thesis.setup.kind} "
                        f"conf={round(thesis.setup.confidence,2)} < floor {effective_floor}")
        return p

    if last_exit is not None and (now_ms - last_exit) < COOLDOWN_MIN * 60_000:
        mins = round((now_ms - last_exit) / 60_000, 1)
        p.update(state="COOLDOWN", level=thesis.setup.level,
                 setup_type=thesis.setup.kind, expectancy=thesis.expectancy,
                 expectancy_source=thesis.expectancy_source,
                 invalidation=thesis.invalidation,
                 reason=f"post-trade cooldown ({mins}/{COOLDOWN_MIN} min)")
        return p

    order = _order_from_thesis(thesis)
    p.update(state="PLACE", action=f"PLACE_{thesis.setup.side}",
             side=thesis.setup.side, level=thesis.setup.level, order=order,
             setup_type=thesis.setup.kind, expectancy=thesis.expectancy,
             expectancy_source=thesis.expectancy_source,
             invalidation=thesis.invalidation,
             reason=f"place {thesis.setup.kind} {order['sidecmd']} stop-limit "
                    f"@ {thesis.setup.level} trig={order['entry_stop']} "
                    f"stop={order['stop_loss']} exp={thesis.expectancy}")
    return p
