"""Deterministic six-field verdict from a Bookmap snapshot.

Pure function. No Claude call, no I/O, no network. Tolerant of missing
optional fields: only HARD gates (health / stale snapshot / anchorMode /
news / blocking session code) produce SETUP=NO_TRADE_GATE. Missing
optional signals (sigma, micro events, thesis, levels) just skip the
setup that needs them.

Fixed vocabulary:
  SETUP   in {OR_BREAK_FOLLOW, OR_BREAK_FADE, OR_REVERT, LEVEL_DEFEND,
              LEVEL_SWEEP_REV, VWAP_REVERT, NO_TRADE_GATE, NO_TRADE_CHOP}
  BIAS    in {LONG, SHORT, NEUTRAL}
  CONFID  in {LOW, MEDIUM, HIGH}

EVIDENCE entries are `<snapshot.field.path>=<value>` strings only. They
are NOT subject to the vague-language ban (snapshot field names like
`institutional_thesis` are legitimate). Human-text fields (ENTRY,
INVALIDATION, NO_TRADE) are scanned against a phrase blacklist by the
test suite.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


SETUPS = frozenset([
    "OR_BREAK_FOLLOW", "OR_BREAK_FADE", "OR_REVERT",
    "LEVEL_DEFEND", "LEVEL_SWEEP_REV", "VWAP_REVERT",
    "NO_TRADE_GATE", "NO_TRADE_CHOP",
])
BIASES = frozenset(["LONG", "SHORT", "NEUTRAL"])
CONFIDENCES = frozenset(["LOW", "MEDIUM", "HIGH"])

_BLOCKING_SESSION_CODES = frozenset([
    "PRE_MARKET", "POST_MARKET", "OR_FORMING", "CLOSE_RISK",
])
_REVERT_REGIMES = frozenset([
    "MEAN_REVERT", "STRETCHED_REVERT", "BLOWOFF_REVERT",
])
_UNUSABLE_REGIMES = frozenset(["", "NO_SIGMA", "UNAVAILABLE"])

NQ_TICK = 0.25
DEFAULT_STALE_MS = 5000


# ---------------------------------------------------------------------------
# Tolerant extractors
# ---------------------------------------------------------------------------

def _level_direction(lvl: Dict[str, Any]) -> Optional[str]:
    d = lvl.get("composite_dir")
    if not d:
        d = (lvl.get("composite") or {}).get("direction")
    if d in (None, "", "WAIT"):
        return None
    return d


def _level_confidence(lvl: Dict[str, Any]) -> Optional[float]:
    c = lvl.get("confidence")
    if c is None:
        c = (lvl.get("composite") or {}).get("confidence")
    try:
        return float(c) if c is not None else None
    except (TypeError, ValueError):
        return None


def _level_coverage(lvl: Dict[str, Any]) -> Optional[float]:
    c = (lvl.get("composite") or {}).get("coverage")
    try:
        return float(c) if c is not None else None
    except (TypeError, ValueError):
        return None


def _execution_read(lvl: Dict[str, Any]) -> Optional[str]:
    th = lvl.get("institutional_thesis") or {}
    er = th.get("execution_read")
    return er if er else None


def _nearest_level(or_levels: Dict[str, Any], mid: Optional[float]) -> Optional[Dict[str, Any]]:
    levels = or_levels.get("levels") or []
    if not isinstance(levels, list) or not levels or mid is None:
        return None
    best = None
    best_d = None
    for L in levels:
        if not isinstance(L, dict):
            continue
        p = L.get("price")
        try:
            d = abs(float(p) - mid)
        except (TypeError, ValueError):
            continue
        if best_d is None or d < best_d:
            best = L; best_d = d
    return best


def _recent_sweep_at(snap: Dict[str, Any], price: float,
                     band_ticks: int = 2) -> Optional[Dict[str, Any]]:
    me = snap.get("micro_events")
    if not isinstance(me, dict):
        return None
    events = me.get("events")
    if not isinstance(events, list):
        return None
    band = band_ticks * NQ_TICK
    for ev in reversed(events[-30:]):
        if not isinstance(ev, dict):
            continue
        if (ev.get("kind") or "").upper() != "SWEEP":
            continue
        try:
            if abs(float(ev.get("price")) - price) <= band:
                return ev
        except (TypeError, ValueError):
            continue
    return None


def _round2(p: Any) -> Optional[float]:
    try:
        return round(float(p), 2)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# BIAS + CONFIDENCE helpers
# ---------------------------------------------------------------------------

def _bias_from_direction(direction: Optional[str]) -> str:
    if direction in ("FOLLOW_LONG", "FADE_LONG"):
        return "LONG"
    if direction in ("FOLLOW_SHORT", "FADE_SHORT"):
        return "SHORT"
    return "NEUTRAL"


def _confidence_for(snap: Dict[str, Any], lvl: Dict[str, Any], bias: str) -> str:
    if bias == "NEUTRAL":
        return "LOW"
    coverage = _level_coverage(lvl)
    conf = _level_confidence(lvl)
    regime = ((snap.get("vwap_bias") or {}).get("regime") or "").upper()
    trajectory = ((snap.get("conviction") or {}).get("trajectory") or "").upper()
    high_ok = (
        coverage is not None and coverage >= 0.75
        and regime not in _UNUSABLE_REGIMES
        and trajectory != "WARMUP"
        and conf is not None and conf >= 0.6
    )
    if high_ok:
        return "HIGH"
    if conf is not None and conf >= 0.4 and trajectory != "WARMUP":
        return "MEDIUM"
    return "LOW"


# ---------------------------------------------------------------------------
# Verdict builder
# ---------------------------------------------------------------------------

def _base(alias: Optional[str], as_of_ms: Optional[int],
          age_ms: Optional[int]) -> Dict[str, Any]:
    return {
        "alias":         alias,
        "asOfMs":        as_of_ms,
        "ageMs":         age_ms,
        "BIAS":          "NEUTRAL",
        "SETUP":         "NO_TRADE_GATE",
        "ENTRY":         "",
        "INVALIDATION":  "",
        "NO_TRADE":      "",
        "CONFIDENCE":    "LOW",
        "EVIDENCE":      [],
    }


def compute_verdict(
    snap: Optional[Dict[str, Any]],
    *,
    as_of_ms: Optional[int] = None,
    age_ms: Optional[int] = None,
    now_ms: Optional[int] = None,
    stale_threshold_ms: int = DEFAULT_STALE_MS,
) -> Dict[str, Any]:
    """Return the deterministic six-field verdict.

    Args:
        snap: dashboard snapshot dict, or None if the poller has no payload yet.
        as_of_ms: snapshot acquisition wall-clock ms (from poller.latest()).
        age_ms: snapshot age in ms at request time (from poller.latest()).
        now_ms: optional override for the current wall-clock ms; only used to
            compute effective age when age_ms is not supplied.
        stale_threshold_ms: ms after which the snapshot is considered stale.
    """
    now = now_ms if now_ms is not None else int(time.time() * 1000)

    if snap is None:
        v = _base(None, as_of_ms, age_ms)
        v["NO_TRADE"] = "no_snapshot"
        return v

    alias = snap.get("alias")
    v = _base(alias, as_of_ms, age_ms)

    # ---- HARD GATE: bridge health
    health = (snap.get("health") or "").lower()
    if health != "ok":
        v["NO_TRADE"] = f"bridge_offline:{snap.get('health') or 'unknown'}"
        v["EVIDENCE"] = [f"health={snap.get('health')}"]
        return v

    # ---- HARD GATE: snapshot freshness
    effective_age = age_ms
    if effective_age is None and as_of_ms is not None:
        effective_age = now - int(as_of_ms)
    if effective_age is not None and effective_age > stale_threshold_ms:
        v["NO_TRADE"] = f"stale_snapshot:{int(effective_age)}ms"
        v["EVIDENCE"] = [f"ageMs={int(effective_age)}"]
        return v

    # ---- HARD GATE: anchor mode must be LIVE (missing also gates)
    session = snap.get("session") or {}
    conviction = snap.get("conviction") or {}
    anchor_mode = session.get("anchorMode") or conviction.get("anchorMode")
    if anchor_mode != "LIVE":
        v["NO_TRADE"] = f"stale_anchor:{anchor_mode or 'missing'}"
        v["EVIDENCE"] = [f"session.anchorMode={anchor_mode or 'missing'}"]
        return v

    # ---- HARD GATE: news blackout
    news = snap.get("news") or {}
    if news.get("blocked"):
        label = news.get("label") or "BLACKOUT"
        v["NO_TRADE"] = f"news_blackout:{label}"
        v["EVIDENCE"] = ["news.blocked=true", f"news.label={label}"]
        return v

    # ---- HARD GATE: blocking session code
    code = (session.get("code") or "").upper()
    if code in _BLOCKING_SESSION_CODES:
        v["NO_TRADE"] = f"session:{code}"
        v["EVIDENCE"] = [f"session.code={code}"]
        return v

    # ---- SOFT PATH (missing optional fields degrade gracefully)
    book = snap.get("book") or {}
    mid = _round2(book.get("mid"))
    if mid is None:
        v["SETUP"] = "NO_TRADE_CHOP"
        v["NO_TRADE"] = "no_mid"
        v["EVIDENCE"] = ["book.mid=missing"]
        return v

    or_levels = snap.get("or_levels") or {}
    nearest = _nearest_level(or_levels, mid)
    if nearest is None:
        v["SETUP"] = "NO_TRADE_CHOP"
        v["NO_TRADE"] = "no_or_levels"
        v["EVIDENCE"] = [f"book.mid={mid}", "or_levels.levels=missing"]
        return v

    label = nearest.get("label") or "?"
    price = _round2(nearest.get("price"))
    direction = _level_direction(nearest)
    exec_read = _execution_read(nearest)
    or_high = _round2(or_levels.get("orHigh"))
    or_low = _round2(or_levels.get("orLow"))

    # ---- LEVEL_DEFEND (needs institutional_thesis; skip if missing)
    if exec_read == "STAND_DOWN":
        v["SETUP"] = "LEVEL_DEFEND"
        v["BIAS"] = "NEUTRAL"
        v["ENTRY"] = ""
        v["INVALIDATION"] = f"break of {label} at {price}"
        v["NO_TRADE"] = "level_defended"
        v["EVIDENCE"] = [
            f"or_levels.levels[{label}].institutional_thesis.execution_read=STAND_DOWN",
            f"book.mid={mid}",
            f"or_levels.levels[{label}].price={price}",
        ]
        v["CONFIDENCE"] = "LOW"
        return v

    # ---- LEVEL_SWEEP_REV (needs micro_events; skip if missing)
    if direction in ("FADE_LONG", "FADE_SHORT") and price is not None:
        sweep = _recent_sweep_at(snap, float(price))
        if sweep is not None:
            v["SETUP"] = "LEVEL_SWEEP_REV"
            v["BIAS"] = _bias_from_direction(direction)
            v["ENTRY"] = f"price closes back through {price} after sweep"
            offset = (2 * NQ_TICK) if v["BIAS"] == "SHORT" else -(2 * NQ_TICK)
            v["INVALIDATION"] = f"{round(price + offset, 2)}"
            v["NO_TRADE"] = ""
            v["EVIDENCE"] = [
                f"micro_events.events[].kind=SWEEP@{_round2(sweep.get('price'))}",
                f"or_levels.levels[{label}].composite.direction={direction}",
                f"book.mid={mid}",
            ]
            v["CONFIDENCE"] = _confidence_for(snap, nearest, v["BIAS"])
            return v

    # ---- OR_BREAK_FOLLOW (price beyond OR, composite agrees)
    if label in ("OR-H", "OR-L") and direction in ("FOLLOW_LONG", "FOLLOW_SHORT"):
        agree = False
        if direction == "FOLLOW_LONG" and label == "OR-H" and or_high is not None:
            agree = mid > or_high
        elif direction == "FOLLOW_SHORT" and label == "OR-L" and or_low is not None:
            agree = mid < or_low
        if agree:
            ref = or_high if label == "OR-H" else or_low
            v["SETUP"] = "OR_BREAK_FOLLOW"
            v["BIAS"] = _bias_from_direction(direction)
            v["ENTRY"] = f"price holds beyond {ref} on next print"
            invalid = (ref - 2 * NQ_TICK) if v["BIAS"] == "LONG" else (ref + 2 * NQ_TICK)
            v["INVALIDATION"] = f"{round(invalid, 2)}"
            v["NO_TRADE"] = ""
            v["EVIDENCE"] = [
                f"book.mid={mid}",
                f"or_levels.{'orHigh' if label == 'OR-H' else 'orLow'}={ref}",
                f"or_levels.levels[{label}].composite.direction={direction}",
            ]
            v["CONFIDENCE"] = _confidence_for(snap, nearest, v["BIAS"])
            return v

    # ---- OR_BREAK_FADE (price back inside OR after a failed break)
    if label in ("OR-H", "OR-L") and direction in ("FADE_LONG", "FADE_SHORT"):
        back_inside = False
        if label == "OR-H" and or_high is not None:
            back_inside = mid < or_high
        elif label == "OR-L" and or_low is not None:
            back_inside = mid > or_low
        if back_inside:
            ref = or_high if label == "OR-H" else or_low
            v["SETUP"] = "OR_BREAK_FADE"
            v["BIAS"] = _bias_from_direction(direction)
            v["ENTRY"] = f"price closes back inside OR through {ref}"
            invalid = (ref + 2 * NQ_TICK) if v["BIAS"] == "SHORT" else (ref - 2 * NQ_TICK)
            v["INVALIDATION"] = f"{round(invalid, 2)}"
            v["NO_TRADE"] = ""
            v["EVIDENCE"] = [
                f"book.mid={mid}",
                f"or_levels.{'orHigh' if label == 'OR-H' else 'orLow'}={ref}",
                f"or_levels.levels[{label}].composite.direction={direction}",
            ]
            v["CONFIDENCE"] = _confidence_for(snap, nearest, v["BIAS"])
            return v

    # ---- VWAP_REVERT (needs warm sigma + revert regime; skip if missing)
    vb = snap.get("vwap_bias") or {}
    try:
        sz = float(vb.get("sigma_z")) if vb.get("sigma_z") is not None else None
    except (TypeError, ValueError):
        sz = None
    regime = (vb.get("regime") or "").upper()
    if sz is not None and abs(sz) >= 2.0 and regime in _REVERT_REGIMES:
        v["SETUP"] = "VWAP_REVERT"
        v["BIAS"] = "SHORT" if sz > 0 else "LONG"
        v["ENTRY"] = f"next print rejects {sz:.2f}sigma at VWAP"
        ext = (abs(sz) + 0.5) if v["BIAS"] == "SHORT" else -(abs(sz) + 0.5)
        v["INVALIDATION"] = f"VWAP {ext:+.2f}sigma extension"
        v["NO_TRADE"] = ""
        v["EVIDENCE"] = [
            f"vwap_bias.sigma_z={sz:.2f}",
            f"vwap_bias.regime={regime}",
            f"book.mid={mid}",
        ]
        v["CONFIDENCE"] = _confidence_for(snap, nearest, v["BIAS"])
        return v

    # ---- OR_REVERT (inside OR, weak fade at a magnet)
    middle_lock = bool(or_levels.get("middleLock"))
    conf = _level_confidence(nearest)
    if (middle_lock and direction in ("FADE_LONG", "FADE_SHORT")
            and conf is not None and conf >= 0.35):
        v["SETUP"] = "OR_REVERT"
        v["BIAS"] = _bias_from_direction(direction)
        v["ENTRY"] = f"rejection print at {price}"
        if price is not None:
            offset = (3 * NQ_TICK) if v["BIAS"] == "SHORT" else -(3 * NQ_TICK)
            v["INVALIDATION"] = f"{round(price + offset, 2)}"
        v["NO_TRADE"] = ""
        v["EVIDENCE"] = [
            "or_levels.middleLock=true",
            f"or_levels.levels[{label}].composite.direction={direction}",
            f"or_levels.levels[{label}].composite.confidence={round(conf, 3)}",
        ]
        v["CONFIDENCE"] = _confidence_for(snap, nearest, v["BIAS"])
        return v

    # ---- Default: NO_TRADE_CHOP (ambiguous; do not invent a trade)
    v["SETUP"] = "NO_TRADE_CHOP"
    v["BIAS"] = "NEUTRAL"
    reasons: List[str] = []
    if middle_lock:
        reasons.append("middle_lock")
    if direction is None:
        reasons.append("composite_wait")
    if conf is None or conf < 0.35:
        reasons.append("low_confidence")
    v["NO_TRADE"] = "no_clear_setup" + (":" + ",".join(reasons) if reasons else "")
    v["EVIDENCE"] = [
        f"book.mid={mid}",
        f"or_levels.levels[{label}].price={price}",
        f"or_levels.levels[{label}].composite.direction={direction or 'WAIT'}",
    ]
    v["CONFIDENCE"] = "LOW"
    return v
