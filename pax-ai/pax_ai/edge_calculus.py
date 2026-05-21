"""Pure functions: Jane-Street-style edge calculus per OR level.

All inputs come from the dashboard snapshot. All outputs are deterministic
functions of those inputs + pax_ai_config.json constants. NO LLM math.

Public surface:
  * size_tier(confidence)           -> "FULL" | "HALF" | "NONE"
  * directional_r(composite_dir, regime, level_kind) -> float
  * expected_r(level, regime, regime_confidence) -> float
  * prob_pay_for_trade(confidence) -> float in [0.30, 0.80]
  * prob_reach_next_rung(confidence, regime_confidence) -> float in [0.10, 0.50]
  * max_heat_pts(level, or_width_pts, tick_size_pts) -> float
  * invalidation_price(level, or_high, or_low, tick_size_pts) -> float
  * payline_price(level, alias) -> float
  * rung1_price(level, alias) -> float
  * level_edge(level, snap) -> full edge dict (per spec section 5.3)

Spec: docs/superpowers/specs/2026-05-19-pax-ai-design.md section 5.3.1.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from . import config


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _root_symbol(alias: Optional[str]) -> str:
    """Extract the product root from a Bookmap alias.

    Examples:
      "NQM6.CME@RITHMIC"  -> "NQ"
      "MNQH7.CME"         -> "MNQ"
      "ESM6"              -> "ES"
      "ES.GLOBEX"         -> "ES"
      "ES"                -> "ES"

    Rule: take the prefix before the first '.', strip trailing year digits,
    then strip exactly one trailing month-code letter (if any letters remain
    after that). Falls back to the full uppercased prefix when stripping
    would empty the string.
    """
    if not alias:
        return ""
    head = alias.split(".")[0].upper()
    if not head:
        return ""
    # 1) Strip trailing digits (the year, e.g. 6, 25)
    i = len(head)
    while i > 0 and head[i - 1].isdigit():
        i -= 1
    stripped_digits = head[:i]
    # 2) If we stripped any digits AND the remaining ends with one letter
    # that is plausibly a month code, drop that one letter too. Be
    # conservative: only strip the month letter if there are at least two
    # letters left so we don't turn "ES" into "E".
    if i < len(head) and len(stripped_digits) >= 3 and stripped_digits[-1].isalpha():
        return stripped_digits[:-1]
    return stripped_digits or head


def _is_extension(label: Optional[str]) -> bool:
    """OR-H / OR-L are OR levels; +1/+2/+3/-1/-2/-3 are extension rungs."""
    if not label:
        return False
    L = label.strip().upper()
    return L not in ("OR-H", "OR-L")


def _level_kind(label: Optional[str]) -> str:
    return "EXT_LEVEL" if _is_extension(label) else "OR_LEVEL"


def _clamp(x: float, lo: float, hi: float) -> float:
    if x < lo: return lo
    if x > hi: return hi
    return x


# ---------------------------------------------------------------------------
# Size tier
# ---------------------------------------------------------------------------

def size_tier(confidence: Optional[float]) -> str:
    """Map level composite confidence to a sizing bucket.

    Defaults (overridable via pax_ai_config.json::size_tiers):
      confidence >= 0.50 -> FULL
      0.35 <= confidence < 0.50 -> HALF
      confidence < 0.35 -> NONE
    """
    if confidence is None:
        return "NONE"
    full_min = config.get("size_tiers.FULL_min_confidence", 0.50)
    half_min = config.get("size_tiers.HALF_min_confidence", 0.35)
    if confidence >= full_min:
        return "FULL"
    if confidence >= half_min:
        return "HALF"
    return "NONE"


# ---------------------------------------------------------------------------
# Directional R / EV
# ---------------------------------------------------------------------------

def directional_r(composite_dir: Optional[str], regime: Optional[str],
                  level_kind: str) -> float:
    """Lookup median R-multiple for the (direction, regime, level kind) cell.

    The table is a flat dict whose KEYS contain dots (e.g.
    "FADE_LONG.ABSORPTION_BID.OR_LEVEL"). We resolve the table object via
    config.get and look up the composite key on it directly -- we do NOT
    pass the composite key through config.get's dotted-path walker, which
    would (incorrectly) try to treat each segment as a separate nesting
    level. Falls back to the DEFAULT entry (1.0 R).
    """
    table = config.get("directional_R_table", {}) or {}
    default = table.get("DEFAULT", 1.0)
    if not composite_dir or not regime:
        return float(default)
    key = f"{composite_dir}.{regime}.{level_kind}"
    val = table.get(key, default)
    try:
        return float(val)
    except (TypeError, ValueError):
        return 1.0


def expected_r(confidence: Optional[float], composite_dir: Optional[str],
               regime: Optional[str], level_kind: str) -> float:
    """EV in R-multiples = confidence * directional_R.

    Capped at directional_R (perfect confidence == directional_R, no leverage
    over the median outcome).
    """
    if confidence is None:
        return 0.0
    base = directional_r(composite_dir, regime, level_kind)
    return _clamp(confidence * base, 0.0, base)


# ---------------------------------------------------------------------------
# Probabilities
# ---------------------------------------------------------------------------

def prob_pay_for_trade(confidence: Optional[float]) -> float:
    """P(at least 10 pts in our favor before stop) ~= 0.30 + 0.50 * confidence.

    Bounded [0.30, 0.80]. Linear in level composite confidence.
    """
    if confidence is None:
        return 0.30
    return _clamp(0.30 + 0.50 * confidence, 0.30, 0.80)


def prob_reach_next_rung(confidence: Optional[float],
                          regime_confidence: Optional[float]) -> float:
    """P(reach next rung before stop) ~= 0.10 + 0.40 * conf * regime_conf.

    Bounded [0.10, 0.50]. Requires BOTH level conviction and regime stability.
    """
    if confidence is None or regime_confidence is None:
        return 0.10
    return _clamp(0.10 + 0.40 * confidence * regime_confidence, 0.10, 0.50)


# ---------------------------------------------------------------------------
# Heat / invalidation / payline / rung
# ---------------------------------------------------------------------------

def max_heat_pts(level: Dict[str, Any], or_width_pts: Optional[float],
                  tick_size_pts: float) -> float:
    """Worst expected adverse excursion before payline (in points).

    For initial-stop trade (FOLLOW from OR level): OR width + 1 tick.
    For FOLLOW from extension or FADE: 1 tick (scratch-stop tight to entry).
    """
    label = level.get("label", "")
    decision = (level.get("decision") or "").upper()
    if "FADE" in decision or _is_extension(label):
        return tick_size_pts
    if or_width_pts is None or or_width_pts <= 0:
        return 8.0   # NQ historical median OR width fallback
    return float(or_width_pts) + tick_size_pts


def invalidation_price(level: Dict[str, Any], or_high: Optional[float],
                        or_low: Optional[float], tick_size_pts: float) -> Optional[float]:
    """Initial-stop price.

    FOLLOW long from OR-H or +N: 1 tick below OR-L.
    FOLLOW short from OR-L or -N: 1 tick above OR-H.
    FADE inside OR: 1 tick beyond OR boundary that was rotated.
    Returns None if the OR boundary side needed is missing.
    """
    decision = (level.get("decision") or "").upper()
    if "LONG" in decision:
        if or_low is None: return None
        return float(or_low) - tick_size_pts
    if "SHORT" in decision:
        if or_high is None: return None
        return float(or_high) + tick_size_pts
    return None


def payline_price(level: Dict[str, Any], alias: Optional[str]) -> Optional[float]:
    """Entry + payline_pts (long) / Entry - payline_pts (short)."""
    price = level.get("price")
    if price is None:
        return None
    decision = (level.get("decision") or "").upper()
    sym = _root_symbol(alias)
    payline = config.get(f"payline_pts.{sym}", 10.0)
    if "LONG" in decision:
        return float(price) + float(payline)
    if "SHORT" in decision:
        return float(price) - float(payline)
    return None


def rung1_price(level: Dict[str, Any], alias: Optional[str]) -> Optional[float]:
    """Entry + rung_pts (long) / Entry - rung_pts (short)."""
    price = level.get("price")
    if price is None:
        return None
    decision = (level.get("decision") or "").upper()
    sym = _root_symbol(alias)
    rung = config.get(f"rung_pts.{sym}", 65.0)
    if "LONG" in decision:
        return float(price) + float(rung)
    if "SHORT" in decision:
        return float(price) - float(rung)
    return None


# ---------------------------------------------------------------------------
# Composite direction derivation
# ---------------------------------------------------------------------------

def composite_dir_from_decision(decision: Optional[str]) -> Optional[str]:
    """Map an or_levels decision string to a composite_dir tag.

    ENTER_LONG_FOLLOW  -> FOLLOW_LONG
    ENTER_LONG_FADE    -> FADE_LONG
    ENTER_SHORT_FOLLOW -> FOLLOW_SHORT
    ENTER_SHORT_FADE   -> FADE_SHORT
    WAIT / None        -> None
    """
    if not decision:
        return None
    d = decision.upper()
    if "LONG" in d and "FOLLOW" in d:  return "FOLLOW_LONG"
    if "LONG" in d and "FADE" in d:    return "FADE_LONG"
    if "SHORT" in d and "FOLLOW" in d: return "FOLLOW_SHORT"
    if "SHORT" in d and "FADE" in d:   return "FADE_SHORT"
    return None


# ---------------------------------------------------------------------------
# Full edge object for /api/pax/level/{label}
# ---------------------------------------------------------------------------

def level_edge(level: Dict[str, Any], snap: Dict[str, Any]) -> Dict[str, Any]:
    """Build the edge_calculus payload for one level row.

    Pure function: same (level, snap) -> same output. No I/O. Used by both
    /api/pax/level/{label} and /api/pax/playbook.
    """
    label = level.get("label")
    decision = level.get("decision")
    confidence = level.get("confidence")

    flow = snap.get("flow") or {}
    or_levels = snap.get("or_levels") or {}
    alias = snap.get("alias")
    sym = _root_symbol(alias)

    regime = flow.get("regime")
    regime_conf = flow.get("regimeConfidence")
    or_high = or_levels.get("orHigh")
    or_low = or_levels.get("orLow")
    or_width = or_levels.get("orWidthPts")
    tick_size = float(config.get(f"tick_size.{sym}", 0.25))

    comp_dir = composite_dir_from_decision(decision)
    lvl_kind = _level_kind(label)

    reasons = []
    if comp_dir:
        reasons.append(f"composite_dir={comp_dir}")
    if confidence is not None:
        reasons.append(f"composite_score in [-1,+1], confidence={confidence:.2f}")
    ts_kind = size_tier(confidence)
    reasons.append(f"size_tier={ts_kind} per confidence vs config thresholds")
    if regime:
        reasons.append(f"flow.regime={regime}"
                        + (f" (conf {regime_conf:.2f})" if regime_conf is not None else ""))
    if regime in ("ABSORPTION_BID", "ABSORPTION_ASK") and lvl_kind == "OR_LEVEL":
        reasons.append("absorption at OR -> highest-conviction FADE bucket")
    if regime in ("EXHAUSTION_UP", "EXHAUSTION_DOWN") and lvl_kind == "EXT_LEVEL":
        reasons.append("exhaustion at extension -> fade-the-rung bucket")

    # Institutional-thesis gate on the size_tier. Mirrors pax_decision exactly:
    # STAND_DOWN / WAIT_FOR_CONFIRM / SCRATCH_READY all collapse to NONE.
    # Only PAY_FOR_TRADE (or missing thesis) preserves the legacy size_tier.
    # Additive — size_tier itself is unchanged so legacy consumers still see
    # the pre-gate tier.
    ith = level.get("institutional_thesis") or {}
    exec_read = ith.get("execution_read")
    if exec_read in ("STAND_DOWN", "WAIT_FOR_CONFIRM", "SCRATCH_READY"):
        thesis_gated_size_tier = "NONE"
    else:
        thesis_gated_size_tier = ts_kind
    if exec_read:
        reasons.append(f"execution_read={exec_read} -> thesis_gated_size_tier={thesis_gated_size_tier}")

    return {
        "expected_R":           round(expected_r(confidence, comp_dir, regime, lvl_kind), 3),
        "prob_pay_for_trade":   round(prob_pay_for_trade(confidence), 3),
        "prob_reach_next_rung": round(prob_reach_next_rung(confidence, regime_conf), 3),
        "max_heat_pts":         round(max_heat_pts(level, or_width, tick_size), 2),
        "invalidation_price":   _round_or_none(invalidation_price(level, or_high, or_low, tick_size), 2),
        "scratch_price":        _round_or_none(level.get("price"), 2),
        "payline_price":        _round_or_none(payline_price(level, alias), 2),
        "rung1_price":          _round_or_none(rung1_price(level, alias), 2),
        "size_tier":            ts_kind,
        "thesis_gated_size_tier": thesis_gated_size_tier,
        "composite_dir":        comp_dir,
        "level_kind":           lvl_kind,
        "tick_size":            tick_size,
        "reasons":              reasons,
        "institutional_thesis_summary": {
            "state":             ith.get("state"),
            "thesis":            ith.get("thesis"),
            "execution_read":    exec_read,
            "liquidity_quality": ith.get("liquidity_quality"),
            "aggressor_flow":    ith.get("aggressor_flow"),
        },
    }


def _round_or_none(v: Optional[float], n: int) -> Optional[float]:
    return None if v is None else round(float(v), n)
