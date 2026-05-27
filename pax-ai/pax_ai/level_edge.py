"""Aggregate per-level edge cards for the chart overlay.

Pure function. No Claude. No I/O. Composes edge_calculus.level_edge over
every row in snap["or_levels"]["levels"], applies a uniform set of gates,
and returns one compact card per level.

Honesty contract: this module DOES NOT expose `expected_R`,
`prob_pay_for_trade`, or `prob_reach_next_rung`. Per the edge-calculus
audit (reports/pax-ai-level-edge-audit-2026-05-26.md), those fields are
`confidence * hand-tuned constant` and linear transforms of the same
confidence. The single derived number this endpoint surfaces is
`score_R` -- explicitly NOT measured expected value.

Direction source priority (Slice 1, 2026-05-26):
  1. level.decision via edge_calculus.composite_dir_from_decision()
     when it returns one of FOLLOW_LONG / FOLLOW_SHORT / FADE_LONG /
     FADE_SHORT.
  2. level.composite.direction when it is one of the same four codes.
  3. otherwise the level has no direction.
The chart-facing `direction` is "WAIT" whenever the level is not
actionable; `raw_direction` always preserves the computed LONG/SHORT/WAIT
so the outcome log can audit suppressed signals.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from . import edge_calculus


_BLOCKING_SESSION_CODES = frozenset([
    "PRE_MARKET", "POST_MARKET", "OR_FORMING", "CLOSE_RISK",
])
_DIRECTIONAL_COMPOSITES = frozenset([
    "FOLLOW_LONG", "FOLLOW_SHORT", "FADE_LONG", "FADE_SHORT",
])
_ACTIONABLE_TIERS = frozenset(["HALF", "FULL"])

# Closed vocabulary of driver names allowed on the chart overlay. Mirrors
# the per-level composite driver names emitted by dashboard.py. Anything
# outside the set is dropped so the chart never renders unexpected labels.
_DRIVER_NAME_WHITELIST = frozenset([
    "pull_stack", "tape", "vwap", "micro", "orderbook",
    "volume_profile", "session_conviction", "lt_liquidity",
])

DEFAULT_STALE_MS = 5000
_MAX_REASONS = 4
_MAX_TOP_DRIVERS = 3
_MIN_ACTIONABLE_CONFIDENCE = 0.35


def _direction_from_composite(comp_dir: Optional[str]) -> str:
    if comp_dir in ("FOLLOW_LONG", "FADE_LONG"):
        return "LONG"
    if comp_dir in ("FOLLOW_SHORT", "FADE_SHORT"):
        return "SHORT"
    return "WAIT"


def _color_hint(direction: str, actionable: bool) -> str:
    if not actionable:
        return "neutral"
    if direction == "LONG":
        return "positive"
    if direction == "SHORT":
        return "negative"
    return "neutral"


def _round_or_none(v: Any, ndigits: int) -> Optional[float]:
    try:
        return round(float(v), ndigits)
    except (TypeError, ValueError):
        return None


def _label_side(label: Optional[str]) -> str:
    """Classify an OR-level label by which side of the OR it sits on.

    OR-H and +1/+2/+3/... -> ABOVE. OR-L and -1/-2/-3/... -> BELOW.
    Anything else (or missing label) -> UNKNOWN.
    """
    if not label:
        return "UNKNOWN"
    L = label.strip().upper()
    if L == "OR-H":
        return "ABOVE"
    if L == "OR-L":
        return "BELOW"
    if L.startswith("+") and L[1:].isdigit():
        return "ABOVE"
    if L.startswith("-") and L[1:].isdigit():
        return "BELOW"
    return "UNKNOWN"


def _setup_from_direction_and_label(comp_dir: Optional[str],
                                     label: Optional[str]) -> str:
    """Deterministic setup-name mapping for the chart overlay.

    FOLLOW_LONG at OR-H or any +N  -> OR_BREAK_FOLLOW
    FOLLOW_SHORT at OR-L or any -N -> OR_BREAK_FOLLOW
    FADE_LONG at OR-L or any -N    -> LEVEL_FADE_LONG
    FADE_SHORT at OR-H or any +N   -> LEVEL_FADE_SHORT
    anything else                  -> UNKNOWN_SETUP
    """
    side = _label_side(label)
    if comp_dir == "FOLLOW_LONG" and side == "ABOVE":
        return "OR_BREAK_FOLLOW"
    if comp_dir == "FOLLOW_SHORT" and side == "BELOW":
        return "OR_BREAK_FOLLOW"
    if comp_dir == "FADE_LONG" and side == "BELOW":
        return "LEVEL_FADE_LONG"
    if comp_dir == "FADE_SHORT" and side == "ABOVE":
        return "LEVEL_FADE_SHORT"
    return "UNKNOWN_SETUP"


def _top_drivers(composite: Optional[Dict[str, Any]]) -> List[str]:
    """Return up to 3 most-impactful driver names from composite.drivers.

    Ranked by `abs(score * weight)` so chart shows the largest contributors
    regardless of sign. Whitelist-filtered: unknown names are dropped.
    """
    if not isinstance(composite, dict):
        return []
    drivers = composite.get("drivers") or []
    if not isinstance(drivers, list):
        return []
    scored: List[tuple] = []
    for d in drivers:
        if not isinstance(d, dict):
            continue
        name = d.get("name")
        if name not in _DRIVER_NAME_WHITELIST:
            continue
        try:
            score = float(d.get("score") or 0.0)
            weight = float(d.get("weight") or 0.0)
        except (TypeError, ValueError):
            continue
        contrib = abs(score * weight)
        if contrib == 0.0:
            continue
        scored.append((contrib, name))
    if not scored:
        return []
    scored.sort(key=lambda x: x[0], reverse=True)
    return [name for _, name in scored[:_MAX_TOP_DRIVERS]]


def _select_composite_dir(ec_comp_dir: Optional[str],
                           level: Dict[str, Any]) -> Optional[str]:
    """Direction source priority: decision -> composite.direction -> None."""
    if ec_comp_dir in _DIRECTIONAL_COMPOSITES:
        return ec_comp_dir
    comp = level.get("composite") or {}
    fallback = comp.get("direction") if isinstance(comp, dict) else None
    if fallback in _DIRECTIONAL_COMPOSITES:
        return fallback
    return None


def _blocked_reason(*,
                    health_ok: bool,
                    stale: bool,
                    anchor_blocked: bool,
                    news_blocked: bool,
                    session_blocked: bool,
                    comp_dir: Optional[str],
                    level_relevant: bool,
                    confidence_f: Optional[float],
                    raw_tier: str,
                    thesis_gated: Optional[str],
                    stop_price: Optional[float]) -> Optional[str]:
    """First applicable blocker, or None when fully actionable."""
    if not health_ok:
        return "health"
    if stale:
        return "stale"
    if anchor_blocked:
        return "anchor"
    if news_blocked:
        return "news"
    if session_blocked:
        return "session"
    if comp_dir not in _DIRECTIONAL_COMPOSITES:
        return "no_direction"
    if not level_relevant:
        return "not_near_level"
    if confidence_f is None or confidence_f < _MIN_ACTIONABLE_CONFIDENCE:
        return "low_confidence"
    if raw_tier not in _ACTIONABLE_TIERS:
        return "size_tier"
    if thesis_gated == "NONE":
        return "thesis_gate"
    if stop_price is None:
        return "missing_stop"
    return None


def _empty_payload(alias: Optional[str], as_of_ms: Optional[int],
                   age_ms: Optional[int], mid: Optional[float],
                   anchor_mode: Optional[str], blocked: Dict[str, bool],
                   stale: bool) -> Dict[str, Any]:
    return {
        "alias":      alias,
        "asOfMs":     as_of_ms,
        "ageMs":      age_ms,
        "stale":      stale,
        "mid":        mid,
        "anchorMode": anchor_mode,
        "blocked":    blocked,
        "levels":     [],
    }


def compute_level_edge_payload(
    snap: Optional[Dict[str, Any]],
    *,
    as_of_ms: Optional[int] = None,
    age_ms: Optional[int] = None,
    now_ms: Optional[int] = None,
    stale_threshold_ms: int = DEFAULT_STALE_MS,
) -> Dict[str, Any]:
    """Build the /api/pax/levels/edge payload from a dashboard snapshot.

    Args:
        snap: dashboard snapshot dict, or None if the poller has no payload yet.
        as_of_ms: snapshot acquisition wall-clock ms (from poller.latest()).
        age_ms: snapshot age in ms at request time (from poller.latest()).
        now_ms: optional override for wall-clock; only used when age_ms is None.
        stale_threshold_ms: ms after which the snapshot is considered stale.
    """
    if snap is None:
        return _empty_payload(
            None, as_of_ms, age_ms, None, None,
            {"health_ok": False, "news": False, "session": False,
             "stale": True, "anchor": True},
            True,
        )

    alias = snap.get("alias")
    book = snap.get("book") or {}
    mid = _round_or_none(book.get("mid"), 2)

    session = snap.get("session") or {}
    conviction = snap.get("conviction") or {}
    anchor_mode = session.get("anchorMode") or conviction.get("anchorMode")

    health_ok = (snap.get("health") or "").lower() == "ok"
    news_blocked = bool((snap.get("news") or {}).get("blocked"))
    code = (session.get("code") or "").upper()
    session_blocked = code in _BLOCKING_SESSION_CODES

    now = now_ms if now_ms is not None else int(time.time() * 1000)
    effective_age = age_ms
    if effective_age is None and as_of_ms is not None:
        effective_age = now - int(as_of_ms)
    stale = effective_age is not None and effective_age > stale_threshold_ms
    anchor_blocked = anchor_mode != "LIVE"

    blocked = {
        "health_ok": health_ok,
        "news":      news_blocked,
        "session":   session_blocked,
        "stale":     bool(stale),
        "anchor":    anchor_blocked,
    }

    or_levels = snap.get("or_levels") or {}
    levels_in = or_levels.get("levels") or []
    if not isinstance(levels_in, list) or not levels_in:
        return _empty_payload(alias, as_of_ms, age_ms, mid, anchor_mode,
                              blocked, bool(stale))

    global_gate_ok = (
        health_ok and not news_blocked and not session_blocked
        and not stale and not anchor_blocked
    )

    out_levels: List[Dict[str, Any]] = []
    for L in levels_in:
        if not isinstance(L, dict):
            continue
        try:
            ec = edge_calculus.level_edge(L, snap)
        except Exception:
            # edge_calculus should be defensive; if one level row is malformed
            # enough to throw, skip it rather than 500 the whole endpoint.
            continue

        # Direction-source priority: decision -> composite.direction -> None.
        comp_dir = _select_composite_dir(ec.get("composite_dir"), L)

        confidence = L.get("confidence")
        try:
            confidence_f = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence_f = None

        thesis_gated = ec.get("thesis_gated_size_tier")
        raw_tier = ec.get("size_tier") or "NONE"
        effective_tier = thesis_gated if thesis_gated else raw_tier

        # `score_R` IS the expected_R number from edge_calculus, RENAMED.
        # Underlying math is `confidence * directional_R_constant`. Not EV.
        # The rename is the entire honesty contract of this endpoint -- so
        # downstream consumers (chart, log, report) never see the
        # "expected" label and mistake it for measured EV.
        score_R = ec.get("expected_R")
        stop_price = ec.get("invalidation_price")
        # edge_calculus.invalidation_price keys off `level.decision`. When the
        # direction came from the composite.direction fallback (decision=WAIT
        # but composite says FOLLOW_LONG, etc.), no stop is computed upstream.
        # Reproduce the simple OR-edge stop locally so the level can still be
        # actionable. Long -> 1 tick below OR-L; short -> 1 tick above OR-H.
        if stop_price is None and comp_dir in _DIRECTIONAL_COMPOSITES:
            or_high = or_levels.get("orHigh")
            or_low = or_levels.get("orLow")
            try:
                tick = float(ec.get("tick_size") or 0.25)
            except (TypeError, ValueError):
                tick = 0.25
            if comp_dir in ("FOLLOW_LONG", "FADE_LONG") and or_low is not None:
                stop_price = round(float(or_low) - tick, 2)
            elif comp_dir in ("FOLLOW_SHORT", "FADE_SHORT") and or_high is not None:
                stop_price = round(float(or_high) + tick, 2)

        # Relevance gate: a level far from the current price is never an
        # entry signal even if its composite says LONG/SHORT. Prefer the
        # snapshot-provided `proximity` boolean (dashboard already computes
        # it against tick-based prox config); missing proximity defaults to
        # not-relevant. The raw_direction / setup / top_drivers fields are
        # left intact so the outcome log and chart still see the upstream
        # signal classification.
        level_relevant = bool(L.get("proximity"))
        try:
            distance_abs = (
                abs(float(L.get("distance")))
                if L.get("distance") is not None else None
            )
        except (TypeError, ValueError):
            distance_abs = None

        per_level_ok = (
            comp_dir in _DIRECTIONAL_COMPOSITES
            and level_relevant
            and effective_tier in _ACTIONABLE_TIERS
            and thesis_gated != "NONE"
            and confidence_f is not None
            and confidence_f >= _MIN_ACTIONABLE_CONFIDENCE
            and stop_price is not None
        )
        actionable = bool(global_gate_ok and per_level_ok)

        raw_direction = _direction_from_composite(comp_dir)
        direction = raw_direction if actionable else "WAIT"
        color_hint = _color_hint(direction, actionable)

        setup = _setup_from_direction_and_label(comp_dir, L.get("label"))
        top_drivers = _top_drivers(L.get("composite"))

        reasons = ec.get("reasons") or []
        if not isinstance(reasons, list):
            reasons = []
        reasons = [str(r) for r in reasons[:_MAX_REASONS]]

        blocked_reason = _blocked_reason(
            health_ok=health_ok,
            stale=bool(stale),
            anchor_blocked=anchor_blocked,
            news_blocked=news_blocked,
            session_blocked=session_blocked,
            comp_dir=comp_dir,
            level_relevant=level_relevant,
            confidence_f=confidence_f,
            raw_tier=raw_tier,
            thesis_gated=thesis_gated,
            stop_price=stop_price,
        )

        out_levels.append({
            "label":          L.get("label"),
            "price":          L.get("price"),
            "side":           L.get("side"),
            "distance":       L.get("distance"),
            "distance_abs":   distance_abs,
            "proximity":      bool(L.get("proximity")),
            "level_relevant": level_relevant,
            "direction":      direction,
            "raw_direction":  raw_direction,
            "composite_dir":  comp_dir,
            "confidence":     round(confidence_f, 3) if confidence_f is not None else None,
            "score_R":        score_R,
            "stop_price":     stop_price,
            "size_tier":      effective_tier,
            "actionable":     actionable,
            "color_hint":     color_hint,
            "reasons":        reasons,
            "setup":          setup,
            "top_drivers":    top_drivers,
            "blocked_reason": blocked_reason,
            "snapshot_ts_ms": as_of_ms,
            "mid_at_signal":  mid,
        })

    return {
        "alias":      alias,
        "asOfMs":     as_of_ms,
        "ageMs":      age_ms,
        "stale":      bool(stale),
        "mid":        mid,
        "anchorMode": anchor_mode,
        "blocked":    blocked,
        "levels":     out_levels,
    }
