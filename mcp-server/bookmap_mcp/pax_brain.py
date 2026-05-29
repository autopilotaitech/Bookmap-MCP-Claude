"""Pure market-state / setup / thesis layer for the Pax SIM trader.

This module deliberately has no I/O, no model calls, and no order execution.
It turns one dashboard snapshot into a small trade thesis object that the
existing pax_loop governor can accept or reject.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional

from . import pax_expectancy


@dataclass(frozen=True)
class MarketState:
    code: str
    side: Optional[str]
    reason: str


@dataclass(frozen=True)
class Setup:
    kind: str
    side: str
    level: str
    price: float
    confidence: float
    reason: str


@dataclass(frozen=True)
class Thesis:
    setup: Setup
    entry: float
    stop: float
    targets: List[float]
    invalidation: str
    why_now: str
    expectancy: float
    expectancy_source: str
    tag: str


def fnum(value: Any) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def nearest_proximate_level(or_levels: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    prox = None
    for lvl in (or_levels.get("levels") or []):
        if not isinstance(lvl, dict) or not lvl.get("proximity"):
            continue
        if prox is None:
            prox = lvl
            continue
        dist = abs(fnum(lvl.get("distance")) or 9e9)
        best = abs(fnum(prox.get("distance")) or 9e9)
        if dist < best:
            prox = lvl
    return prox


def _signed_from_label(value: Any) -> float:
    label = str(value or "").upper()
    if label in ("BUY", "STRONG_BUY", "BULL", "BULLISH", "TRENDING_UP",
                 "ACCUMULATION", "WEAK_BULL", "STRONG_BULL"):
        return 1.0
    if label in ("SELL", "STRONG_SELL", "BEAR", "BEARISH", "TRENDING_DOWN",
                 "DISTRIBUTION", "WEAK_BEAR", "STRONG_BEAR"):
        return -1.0
    return 0.0


def money_score(snap: Dict[str, Any]) -> float:
    """Return a compact signed control score in [-1, +1].

    This is not pretend statistical confidence. It is a deterministic read of
    who is pressing right now, made from fields already present in snapshots.
    """
    votes: List[float] = []
    weights: List[float] = []

    flow = snap.get("flow") or {}
    for key, weight in (("ofiZ", 0.22), ("cvdDeltaZ", 0.18),
                        ("biasScore", 0.14)):
        v = fnum(flow.get(key))
        if v is not None:
            votes.append(max(-1.0, min(1.0, v if key == "biasScore" else v / 2.0)))
            weights.append(weight)
    reg = _signed_from_label(flow.get("regime"))
    if reg:
        votes.append(reg)
        weights.append(0.12)

    tape = snap.get("tape_flow") or {}
    tv = fnum(tape.get("deltaScore"))
    if tv is not None:
        votes.append(max(-1.0, min(1.0, tv)))
        weights.append(0.14)
    tl = _signed_from_label(tape.get("deltaLabel"))
    if tl:
        votes.append(tl)
        weights.append(0.08)

    trend = _signed_from_label((snap.get("trend_signal") or {}).get("kind"))
    if trend:
        votes.append(trend)
        weights.append(0.12)

    ifl = snap.get("institutional_flow") or {}
    iv = fnum(ifl.get("weighted_vote"))
    if iv is not None:
        votes.append(max(-1.0, min(1.0, iv * 2.0)))
        weights.append(0.20)
    ireg = _signed_from_label(ifl.get("regime"))
    if ireg:
        votes.append(ireg)
        weights.append(0.12)

    if not votes:
        return 0.0
    numerator = sum(v * w for v, w in zip(votes, weights))
    denom = sum(weights) or 1.0
    return round(max(-1.0, min(1.0, numerator / denom)), 4)


def directional_evidence_count(snap: Dict[str, Any], side: str) -> int:
    sign = 1 if side == "LONG" else -1
    count = 0
    flow = snap.get("flow") or {}
    for key in ("ofiZ", "cvdDeltaZ", "biasScore"):
        v = fnum(flow.get(key))
        if v is not None and v * sign >= 0.25:
            count += 1
            break
    tape = snap.get("tape_flow") or {}
    tv = fnum(tape.get("deltaScore"))
    if (tv is not None and tv * sign >= 0.25) or _signed_from_label(tape.get("deltaLabel")) == sign:
        count += 1
    if _signed_from_label((snap.get("trend_signal") or {}).get("kind")) == sign:
        count += 1
    if _signed_from_label((snap.get("institutional_flow") or {}).get("regime")) == sign:
        count += 1
    iv = fnum((snap.get("institutional_flow") or {}).get("weighted_vote"))
    if iv is not None and iv * sign >= 0.15:
        count += 1
    return count


def classify_market_state(snap: Dict[str, Any]) -> MarketState:
    ol = snap.get("or_levels") or {}
    mid = fnum((snap.get("book") or {}).get("mid"))
    or_h = fnum(ol.get("orHigh"))
    or_l = fnum(ol.get("orLow"))
    if mid is None or or_h is None or or_l is None:
        return MarketState("UNKNOWN", None, "missing mid/OR")
    if or_l < mid < or_h:
        return MarketState("INSIDE_OR", None, "price inside opening range")
    if mid >= or_h:
        dist = mid - or_h
        if dist <= 10.0:
            return MarketState("BREAKOUT_ATTEMPT_UP", "LONG",
                               f"{dist:.2f}pt above OR-H")
        return MarketState("ACCEPTANCE_ABOVE_OR", "LONG",
                           f"{dist:.2f}pt accepted above OR-H")
    dist = or_l - mid
    if dist <= 10.0:
        return MarketState("BREAKOUT_ATTEMPT_DOWN", "SHORT",
                           f"{dist:.2f}pt below OR-L")
    return MarketState("ACCEPTANCE_BELOW_OR", "SHORT",
                       f"{dist:.2f}pt accepted below OR-L")


def detect_setups(snap: Dict[str, Any]) -> List[Setup]:
    ol = snap.get("or_levels") or {}
    prox = nearest_proximate_level(ol)
    score = money_score(snap)
    setups: List[Setup] = []

    if prox is not None:
        label = str(prox.get("label") or "")
        price = fnum(prox.get("price"))
        conf = fnum(prox.get("confidence")) or 0.0
        dec = str(prox.get("decision") or "").upper()
        if price is not None:
            if label == "OR-H" and "LONG" in dec and dec.endswith("FOLLOW"):
                setups.append(Setup("OR_BREAK_ACCEPT", "LONG", label, price,
                                    conf, "OR-H follow break accepted"))
            elif label == "OR-L" and "SHORT" in dec and dec.endswith("FOLLOW"):
                setups.append(Setup("OR_BREAK_ACCEPT", "SHORT", label, price,
                                    conf, "OR-L follow break accepted"))
            elif label == "OR-H" and "SHORT" in dec and "FADE" in dec and score < -0.20:
                setups.append(Setup("OR_SWEEP_REJECT", "SHORT", label, price,
                                    max(conf, abs(score)), "OR-H sweep rejected by money"))
            elif label == "OR-L" and "LONG" in dec and "FADE" in dec and score > 0.20:
                setups.append(Setup("OR_SWEEP_RECLAIM", "LONG", label, price,
                                    max(conf, abs(score)), "OR-L sweep reclaimed by money"))
            elif label not in ("OR-H", "OR-L") and dec.endswith("FOLLOW"):
                side = "LONG" if "LONG" in dec else ("SHORT" if "SHORT" in dec else "")
                if side:
                    setups.append(Setup("EXTENSION_CONTINUATION", side, label,
                                        price, conf, f"{label} continuation"))

    state = classify_market_state(snap)
    if prox is None and state.side and abs(score) >= 0.35:
        side = "LONG" if score > 0 else "SHORT"
        if side == state.side and directional_evidence_count(snap, side) >= 2:
            mid = fnum((snap.get("book") or {}).get("mid"))
            if mid is not None:
                setups.append(Setup("OFF_LEVEL_AUCTION_DRIVE", side, "TREND",
                                    mid, abs(score), state.reason))
    return setups


def _expectancy(setup: Setup, snap: Dict[str, Any], session_type: str) -> float:
    base = {
        "OR_BREAK_ACCEPT": 0.38,
        "OR_SWEEP_RECLAIM": 0.32,
        "OR_SWEEP_REJECT": 0.32,
        "EXTENSION_CONTINUATION": 0.24,
        "OFF_LEVEL_AUCTION_DRIVE": 0.22,
    }.get(setup.kind, 0.10)
    if session_type == "RTH":
        base += 0.05
    score = money_score(snap)
    if setup.side == "LONG":
        base += max(-0.15, min(0.20, score * 0.18))
    else:
        base += max(-0.15, min(0.20, -score * 0.18))
    if setup.confidence >= 0.60:
        base += 0.08
    elif setup.confidence < 0.25:
        base -= 0.06
    return round(max(-1.0, min(1.0, base)), 4)


def build_thesis(
    setup: Setup,
    snap: Dict[str, Any],
    *,
    session_type: str,
    payline: float,
    rung: float,
    stop_breathing_pts: float,
    entry_slip_ticks: int,
    tick: float,
    trend_entry_offset_ticks: int,
    expectancy_stats: Optional[Mapping[str, pax_expectancy.ExpectancyStats]] = None,
) -> Optional[Thesis]:
    ol = snap.get("or_levels") or {}
    or_h = fnum(ol.get("orHigh"))
    or_l = fnum(ol.get("orLow"))
    if or_h is None or or_l is None:
        return None
    mid_or = (or_h + or_l) / 2.0
    slip = entry_slip_ticks * tick

    if setup.kind == "OFF_LEVEL_AUCTION_DRIVE":
        trigger_offset = trend_entry_offset_ticks * tick
        if setup.side == "LONG":
            entry = round(setup.price + trigger_offset, 2)
            stop = round(mid_or - stop_breathing_pts, 2)
            targets = [round(entry + payline, 2), round(entry + rung, 2)]
            invalidation = "price re-enters OR or money score flips bearish"
        else:
            entry = round(setup.price - trigger_offset, 2)
            stop = round(mid_or + stop_breathing_pts, 2)
            targets = [round(entry - payline, 2), round(entry - rung, 2)]
            invalidation = "price re-enters OR or money score flips bullish"
    else:
        entry = round(setup.price, 2)
        if setup.side == "LONG":
            stop = round(mid_or - stop_breathing_pts, 2)
            targets = [round(entry + payline, 2), round(entry + rung, 2)]
            invalidation = "acceptance back below OR mid / failed reclaim"
        else:
            stop = round(mid_or + stop_breathing_pts, 2)
            targets = [round(entry - payline, 2), round(entry - rung, 2)]
            invalidation = "acceptance back above OR mid / failed rejection"

    why = (
        f"{setup.kind} {setup.side} at {setup.level}: {setup.reason}; "
        f"money={money_score(snap):+.2f}; conf={setup.confidence:.2f}"
    )
    heuristic = _expectancy(setup, snap, session_type)
    learned = pax_expectancy.stats_for_setup(
        expectancy_stats or {},
        kind=setup.kind,
        side=setup.side,
        level=setup.level,
        session_type=session_type,
    )
    adjusted, source = pax_expectancy.adjust_expectancy(heuristic, learned)
    tag = "PAXBRAIN"
    return Thesis(
        setup=setup,
        entry=entry,
        stop=stop,
        targets=targets,
        invalidation=invalidation,
        why_now=why,
        expectancy=adjusted,
        expectancy_source=source,
        tag=tag,
    )


def best_thesis(
    snap: Dict[str, Any],
    *,
    session_type: str,
    payline: float,
    rung: float,
    stop_breathing_pts: float,
    entry_slip_ticks: int,
    tick: float,
    trend_entry_offset_ticks: int,
    expectancy_stats: Optional[Mapping[str, pax_expectancy.ExpectancyStats]] = None,
) -> Optional[Thesis]:
    theses = [
        build_thesis(
            s, snap, session_type=session_type, payline=payline, rung=rung,
            stop_breathing_pts=stop_breathing_pts,
            entry_slip_ticks=entry_slip_ticks, tick=tick,
            trend_entry_offset_ticks=trend_entry_offset_ticks,
            expectancy_stats=expectancy_stats,
        )
        for s in detect_setups(snap)
    ]
    valid = [t for t in theses if t is not None]
    if not valid:
        return None
    return max(valid, key=lambda t: (t.expectancy, t.setup.confidence))
