"""Attack-response classifier over an /api/snapshot payload.

Stage 3 of the chart-first overhaul (plan:
reports/pax-ai-attack-response-plan-2026-05-27.md). Pure function. No
Claude. No I/O. Reads the existing institutional_chart_events plus
tape_flow / pull_stack / book / session / health, and emits one closed-
vocabulary state per qualifying level.

The key distinction the operator wants:

    below OR-L + accepted selling          -> OR_L_BREAK_ACCEPT / BEAR
    below OR-L + failed selling / absorb   -> OR_L_SWEEP_RECLAIM / BULL
    above OR-H + accepted buying           -> OR_H_BREAK_ACCEPT / BULL
    above OR-H + failed buying / absorb    -> OR_H_SWEEP_FAIL    / BEAR

Location alone NEVER decides bias. The classifier fuses attack +
response + passive liquidity + book intent + active flow into one
deterministic state.

WATCH only. EDGE promotion (proven_edge=true) is gated on Stage 6
report statistics; this module always emits proven_edge=false.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Set, Tuple


# Closed vocabulary.

STATE_OR_L_SWEEP_RECLAIM = "OR_L_SWEEP_RECLAIM"
STATE_OR_H_SWEEP_FAIL    = "OR_H_SWEEP_FAIL"
STATE_OR_L_ABSORB_HOLD   = "OR_L_ABSORB_HOLD"
STATE_OR_H_ABSORB_HOLD   = "OR_H_ABSORB_HOLD"
STATE_OR_L_BREAK_ACCEPT  = "OR_L_BREAK_ACCEPT"
STATE_OR_H_BREAK_ACCEPT  = "OR_H_BREAK_ACCEPT"
STATE_EXT_LOW_EXHAUST    = "EXT_LOW_EXHAUST"
STATE_EXT_HIGH_EXHAUST   = "EXT_HIGH_EXHAUST"
STATE_NO_EDGE            = "NO_EDGE"

BIAS_BULL    = "BULL_WATCH"
BIAS_BEAR    = "BEAR_WATCH"
BIAS_NEUTRAL = "NEUTRAL"

# Allowed enumerations (exposed for tests + endpoint contract).
ALLOWED_STATES = frozenset([
    STATE_OR_L_SWEEP_RECLAIM, STATE_OR_H_SWEEP_FAIL,
    STATE_OR_L_ABSORB_HOLD,   STATE_OR_H_ABSORB_HOLD,
    STATE_OR_L_BREAK_ACCEPT,  STATE_OR_H_BREAK_ACCEPT,
    STATE_EXT_LOW_EXHAUST,    STATE_EXT_HIGH_EXHAUST,
    STATE_NO_EDGE,
])
ALLOWED_BIASES = frozenset([BIAS_BULL, BIAS_BEAR, BIAS_NEUTRAL])

# Attack / response / passive / book / tape tokens.
ATTACK_SWEEP_HIGH = "SWEEP_HIGH"
ATTACK_SWEEP_LOW  = "SWEEP_LOW"
ATTACK_BREAK_UP   = "BREAK_UP"
ATTACK_BREAK_DOWN = "BREAK_DOWN"
ATTACK_TOUCH      = "TOUCH"

RESPONSE_ACCEPTED         = "ACCEPTED"
RESPONSE_REJECTED         = "REJECTED"
RESPONSE_RECLAIMED        = "RECLAIMED"
RESPONSE_FAILED_CONT      = "FAILED_CONTINUATION"
RESPONSE_HOLDING          = "HOLDING"

PASSIVE_BID_ICEBERG = "bid_iceberg"
PASSIVE_ASK_ICEBERG = "ask_iceberg"
PASSIVE_BID_ABSORB  = "bid_absorb"
PASSIVE_ASK_ABSORB  = "ask_absorb"

BOOK_BID_STACK = "bid_stack"
BOOK_ASK_STACK = "ask_stack"
BOOK_BID_PULL  = "bid_pull"
BOOK_ASK_PULL  = "ask_pull"

TAPE_BUY   = "buy_tape"
TAPE_SELL  = "sell_tape"
TAPE_MIXED = "mixed_tape"
TAPE_THIN  = "thin_tape"


DEFAULT_STALE_MS = 5_000

_TAPE_BUY_THRESHOLD  = 0.50
_TAPE_SELL_THRESHOLD = -0.50
_TAPE_MIXED_BAND     = 0.10
_TAPE_THIN_FLOOR     = 5

# Confidence stays modest. The system reports WATCH, not measured edge.
_CONF_BASE     = 0.40
_CONF_PER_EVID = 0.10
_CONF_MAX      = 0.85


# --- pure helpers --------------------------------------------------------

def _label_is_extension(label: str) -> bool:
    if not label:
        return False
    label = label.strip().upper()
    if label in ("OR-H", "OR-L"):
        return False
    if label.startswith(("+", "-")):
        rest = label[1:]
        return rest.isdigit() and rest != "0"
    return False


def _label_is_below_side(label: str) -> bool:
    """True for OR-L and any -N extension. Used to anchor BID/SELL roles."""
    if not label:
        return False
    L = label.strip().upper()
    if L == "OR-L":
        return True
    if L.startswith("-") and L[1:].isdigit():
        return True
    return False


def _label_is_above_side(label: str) -> bool:
    if not label:
        return False
    L = label.strip().upper()
    if L == "OR-H":
        return True
    if L.startswith("+") and L[1:].isdigit():
        return True
    return False


def _is_above_marker(marker_text: str, side: str) -> bool:
    """Same bid/ask side inference the Java glyph layer uses. ICE/ABS
    suffix is authoritative; otherwise side governs."""
    mt = (marker_text or "").upper()
    if "ICE-A" in mt or "ABS-A" in mt:
        return True
    if "ICE-B" in mt or "ABS-B" in mt:
        return False
    return side == "above"


def _classify_tape_flow(tape_obj: Optional[Dict[str, Any]]) -> str:
    if not isinstance(tape_obj, dict):
        return TAPE_THIN
    if "_error" in tape_obj:
        return TAPE_THIN
    delta = tape_obj.get("deltaScore")
    if not isinstance(delta, (int, float)):
        return TAPE_THIN
    n30 = 0
    for key in ("prints30s", "n30", "n_30s", "prints"):
        v = tape_obj.get(key)
        if isinstance(v, (int, float)) and v > n30:
            n30 = int(v)
    if n30 > 0 and n30 < _TAPE_THIN_FLOOR:
        return TAPE_THIN
    d = float(delta)
    if d >= _TAPE_BUY_THRESHOLD:
        return TAPE_BUY
    if d <= _TAPE_SELL_THRESHOLD:
        return TAPE_SELL
    if abs(d) < _TAPE_MIXED_BAND:
        return TAPE_MIXED
    if d > 0:
        return TAPE_BUY
    return TAPE_SELL


def _label_events(events_by_label: Dict[str, List[Dict[str, Any]]],
                  label: str) -> List[Dict[str, Any]]:
    return events_by_label.get(label) or []


def _attack_from_events(events: List[Dict[str, Any]], side: str) -> Optional[str]:
    """Reduce institutional_chart_events at the level to one attack token.

    Priority: SWEEP > BREAK (ACCEPTANCE) > TOUCH. If both directions of a
    sweep are present (rare), the side context wins.
    """
    has_sweep = False
    has_acc_long = False
    has_acc_short = False
    has_touch = False
    for ev in events:
        et = (ev.get("event_type") or "").upper()
        direction = (ev.get("direction") or "").upper()
        if et == "LIQUIDITY_SWEEP":
            has_sweep = True
        elif et == "ACCEPTANCE":
            if direction == "LONG":
                has_acc_long = True
            elif direction == "SHORT":
                has_acc_short = True
        elif et == "TOUCHED_LEVEL":
            has_touch = True
    if has_sweep:
        return ATTACK_SWEEP_HIGH if side == "above" else ATTACK_SWEEP_LOW
    if has_acc_long:
        return ATTACK_BREAK_UP
    if has_acc_short:
        return ATTACK_BREAK_DOWN
    if has_touch:
        return ATTACK_TOUCH
    return None


def _passive_tokens(events: List[Dict[str, Any]], side: str) -> Set[str]:
    out: Set[str] = set()
    for ev in events:
        et = (ev.get("event_type") or "").upper()
        if et not in ("ICEBERG_DEFENSE", "ABSORPTION"):
            continue
        above = _is_above_marker(ev.get("marker_text"), side)
        if et == "ICEBERG_DEFENSE":
            out.add(PASSIVE_ASK_ICEBERG if above else PASSIVE_BID_ICEBERG)
        else:
            out.add(PASSIVE_ASK_ABSORB if above else PASSIVE_BID_ABSORB)
    return out


def _book_tokens(events: List[Dict[str, Any]], side: str) -> Set[str]:
    out: Set[str] = set()
    for ev in events:
        et = (ev.get("event_type") or "").upper()
        if et == "STACKING":
            out.add(BOOK_ASK_STACK if side == "above" else BOOK_BID_STACK)
        elif et == "PULLING":
            out.add(BOOK_ASK_PULL if side == "above" else BOOK_BID_PULL)
    return out


def _confidence(drivers: List[str]) -> float:
    if not drivers:
        return 0.0
    raw = _CONF_BASE + _CONF_PER_EVID * len(drivers)
    if raw > _CONF_MAX:
        raw = _CONF_MAX
    return round(raw, 3)


# --- per-level classifier ------------------------------------------------

def _classify_level(level: Dict[str, Any],
                    events: List[Dict[str, Any]],
                    tape: str,
                    now_ms: int) -> Optional[Dict[str, Any]]:
    label = (level.get("label") or "").strip()
    if not label:
        return None
    side = level.get("side") or ("above" if _label_is_above_side(label) else "below")

    attack = _attack_from_events(events, side)
    if attack is None:
        return None
    passive = _passive_tokens(events, side)
    book = _book_tokens(events, side)

    drivers: List[str] = []
    state: Optional[str] = None
    bias: Optional[str] = None
    response: Optional[str] = None
    needed: Optional[str] = None
    invalid: Optional[str] = None

    bull_passive = bool({PASSIVE_BID_ICEBERG, PASSIVE_BID_ABSORB} & passive)
    bear_passive = bool({PASSIVE_ASK_ICEBERG, PASSIVE_ASK_ABSORB} & passive)
    bid_stack = BOOK_BID_STACK in book
    ask_stack = BOOK_ASK_STACK in book
    is_below = _label_is_below_side(label)
    is_above = _label_is_above_side(label)
    is_ext = _label_is_extension(label)

    # 1) Sweep + defense at OR or extension.
    if attack == ATTACK_SWEEP_LOW and is_below:
        bull_evidence_n = (1 if bull_passive else 0) + (1 if bid_stack else 0) \
                          + (1 if tape == TAPE_BUY else 0)
        bear_evidence_n = (1 if tape == TAPE_SELL else 0) + (1 if ask_stack else 0)
        if bull_evidence_n >= 1 and bear_evidence_n == 0:
            drivers.append("sweep_low")
            if bull_passive:
                drivers.append(PASSIVE_BID_ICEBERG if PASSIVE_BID_ICEBERG in passive
                               else PASSIVE_BID_ABSORB)
            if bid_stack:
                drivers.append(BOOK_BID_STACK)
            if tape == TAPE_BUY:
                drivers.append(TAPE_BUY)
            response = RESPONSE_RECLAIMED
            if is_ext:
                state = STATE_EXT_LOW_EXHAUST
                bias = BIAS_BULL
                needed = (f"reclaim toward OR-L; hold above sweep low and "
                          f"the {label} rung")
                invalid = "new low below sweep low + tape sells through"
            else:
                state = STATE_OR_L_SWEEP_RECLAIM
                bias = BIAS_BULL
                needed = "reclaim OR-L and hold above sweep low"
                invalid = "new low through sweep low"
        elif bear_evidence_n >= 1 and bull_evidence_n == 0:
            drivers.append("sweep_low")
            drivers.append(TAPE_SELL if tape == TAPE_SELL else BOOK_ASK_STACK)
            response = RESPONSE_ACCEPTED
            state = STATE_OR_L_BREAK_ACCEPT
            bias = BIAS_BEAR
            needed = "second push lower with sustained sell tape"
            invalid = "reclaim of OR-L + buy tape absorbs"
        else:
            drivers.append("sweep_low")
            if bull_passive: drivers.append("conflicting_bid_evidence")
            if tape == TAPE_SELL: drivers.append("conflicting_sell_tape")
            state = STATE_NO_EDGE
            bias = BIAS_NEUTRAL
            response = RESPONSE_HOLDING
            needed = "wait for unambiguous response"
            invalid = "either side resolves"

    elif attack == ATTACK_SWEEP_HIGH and is_above:
        bear_evidence_n = (1 if bear_passive else 0) + (1 if ask_stack else 0) \
                          + (1 if tape == TAPE_SELL else 0)
        bull_evidence_n = (1 if tape == TAPE_BUY else 0) + (1 if bid_stack else 0)
        if bear_evidence_n >= 1 and bull_evidence_n == 0:
            drivers.append("sweep_high")
            if bear_passive:
                drivers.append(PASSIVE_ASK_ICEBERG if PASSIVE_ASK_ICEBERG in passive
                               else PASSIVE_ASK_ABSORB)
            if ask_stack:
                drivers.append(BOOK_ASK_STACK)
            if tape == TAPE_SELL:
                drivers.append(TAPE_SELL)
            response = RESPONSE_FAILED_CONT
            if is_ext:
                state = STATE_EXT_HIGH_EXHAUST
                bias = BIAS_BEAR
                needed = (f"reclaim toward OR-H; reject above sweep high "
                          f"around {label}")
                invalid = "new high through sweep + sustained buy tape"
            else:
                state = STATE_OR_H_SWEEP_FAIL
                bias = BIAS_BEAR
                needed = "lose OR-H and reject the sweep high"
                invalid = "new high through sweep high"
        elif bull_evidence_n >= 1 and bear_evidence_n == 0:
            drivers.append("sweep_high")
            drivers.append(TAPE_BUY if tape == TAPE_BUY else BOOK_BID_STACK)
            response = RESPONSE_ACCEPTED
            state = STATE_OR_H_BREAK_ACCEPT
            bias = BIAS_BULL
            needed = "second push higher with sustained buy tape"
            invalid = "reclaim of OR-H + sell tape absorbs"
        else:
            drivers.append("sweep_high")
            if bear_passive: drivers.append("conflicting_ask_evidence")
            if tape == TAPE_BUY: drivers.append("conflicting_buy_tape")
            state = STATE_NO_EDGE
            bias = BIAS_NEUTRAL
            response = RESPONSE_HOLDING
            needed = "wait for unambiguous response"
            invalid = "either side resolves"

    elif attack == ATTACK_BREAK_UP and is_above:
        if tape == TAPE_BUY and not bear_passive:
            drivers.append(ATTACK_BREAK_UP.lower())
            drivers.append(TAPE_BUY)
            if bid_stack: drivers.append(BOOK_BID_STACK)
            response = RESPONSE_ACCEPTED
            state = STATE_OR_H_BREAK_ACCEPT
            bias = BIAS_BULL
            needed = "hold above the level on retest with buy tape"
            invalid = "lose level + sell tape"
        elif bear_passive or tape == TAPE_SELL:
            drivers.append(ATTACK_BREAK_UP.lower())
            if bear_passive:
                drivers.append(PASSIVE_ASK_ICEBERG if PASSIVE_ASK_ICEBERG in passive
                               else PASSIVE_ASK_ABSORB)
            if tape == TAPE_SELL: drivers.append(TAPE_SELL)
            response = RESPONSE_REJECTED
            state = STATE_OR_H_SWEEP_FAIL
            bias = BIAS_BEAR
            needed = "lose the level and reject"
            invalid = "new high + buy tape continues"
        else:
            drivers.append(ATTACK_BREAK_UP.lower())
            state = STATE_NO_EDGE
            bias = BIAS_NEUTRAL
            response = RESPONSE_HOLDING
            needed = "wait for tape confirmation"
            invalid = "either side resolves"

    elif attack == ATTACK_BREAK_DOWN and is_below:
        if tape == TAPE_SELL and not bull_passive:
            drivers.append(ATTACK_BREAK_DOWN.lower())
            drivers.append(TAPE_SELL)
            if ask_stack: drivers.append(BOOK_ASK_STACK)
            response = RESPONSE_ACCEPTED
            state = STATE_OR_L_BREAK_ACCEPT
            bias = BIAS_BEAR
            needed = "hold below the level on retest with sell tape"
            invalid = "reclaim + buy tape"
        elif bull_passive or tape == TAPE_BUY:
            drivers.append(ATTACK_BREAK_DOWN.lower())
            if bull_passive:
                drivers.append(PASSIVE_BID_ICEBERG if PASSIVE_BID_ICEBERG in passive
                               else PASSIVE_BID_ABSORB)
            if tape == TAPE_BUY: drivers.append(TAPE_BUY)
            response = RESPONSE_RECLAIMED
            state = STATE_OR_L_SWEEP_RECLAIM
            bias = BIAS_BULL
            needed = "reclaim and hold above the level"
            invalid = "new low + sell tape continues"
        else:
            drivers.append(ATTACK_BREAK_DOWN.lower())
            state = STATE_NO_EDGE
            bias = BIAS_NEUTRAL
            response = RESPONSE_HOLDING
            needed = "wait for tape confirmation"
            invalid = "either side resolves"

    elif attack == ATTACK_TOUCH:
        if is_below and bull_passive:
            drivers.append("touch")
            drivers.append(PASSIVE_BID_ICEBERG if PASSIVE_BID_ICEBERG in passive
                           else PASSIVE_BID_ABSORB)
            if bid_stack: drivers.append(BOOK_BID_STACK)
            response = RESPONSE_HOLDING
            state = STATE_OR_L_ABSORB_HOLD if not is_ext else STATE_EXT_LOW_EXHAUST
            bias = BIAS_BULL
            needed = "hold above the level on next test"
            invalid = "level fails + sell tape accepts"
        elif is_above and bear_passive:
            drivers.append("touch")
            drivers.append(PASSIVE_ASK_ICEBERG if PASSIVE_ASK_ICEBERG in passive
                           else PASSIVE_ASK_ABSORB)
            if ask_stack: drivers.append(BOOK_ASK_STACK)
            response = RESPONSE_HOLDING
            state = STATE_OR_H_ABSORB_HOLD if not is_ext else STATE_EXT_HIGH_EXHAUST
            bias = BIAS_BEAR
            needed = "reject the level on next test"
            invalid = "level breaks + buy tape accepts"
        else:
            return None

    else:
        # Attack vocabulary did not match the level side (e.g. SWEEP_LOW
        # at an above-side level). Treat as no actionable evidence here.
        return None

    if state is None:
        return None

    try:
        level_price = float(level.get("price"))
    except (TypeError, ValueError):
        level_price = 0.0

    # Stable id: per (alias|label|state|now_ms_bucket). The endpoint
    # caller supplies now_ms; we floor to the nearest second so a single
    # state's id is stable across rapid re-polls within that bucket.
    alias = level.get("_alias", "")
    bucket = int(now_ms // 1000)
    state_id = f"{alias}|{label}|{state}|{bucket}"

    return {
        "id":            state_id,
        "location":      label,
        "level_price":   round(level_price, 2),
        "state":         state,
        "bias":          bias,
        "attack":        attack,
        "response":      response,
        "drivers":       drivers,
        "needed":        needed,
        "invalid":       invalid,
        "confidence":    _confidence(drivers),
        "sample_n":      None,
        "edge_R_60s":    None,
        "proven_edge":   False,
        "timestamp_ms":  int(now_ms),
    }


# --- public API ----------------------------------------------------------

def compute_attack_response(snap: Optional[Dict[str, Any]],
                            now_ms: Optional[int] = None,
                            as_of_ms: Optional[int] = None,
                            age_ms: Optional[int] = None,
                            stale_threshold_ms: int = DEFAULT_STALE_MS
                            ) -> Dict[str, Any]:
    """Compute the attack-response state list for one snapshot.

    Pure function. Returns:

        {
          "alias": str | None,
          "asOfMs": int | None,
          "health": "ok" | "offline" | "stale",
          "states": [ ... ],
          "blocked": {"health": bool, "stale": bool, "anchor": bool},
        }

    When any gate (health / stale / anchor) is flipped, `states` is
    empty and `health` reflects the first blocker (offline > stale >
    anchor). Anchor-only blocks still report `health="ok"` because the
    bridge is healthy; the dedicated `blocked.anchor` flag is what
    consumers read.
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)

    if not isinstance(snap, dict):
        return {
            "alias": None,
            "asOfMs": as_of_ms,
            "health": "offline",
            "states": [],
            "blocked": {"health": True, "stale": True, "anchor": True},
        }

    alias = snap.get("alias")
    session = snap.get("session") or {}
    anchor_mode = (session.get("anchorMode") or "FALLBACK").upper()

    health_ok = (snap.get("health") or "").lower() == "ok"
    stale = False
    if age_ms is not None:
        try:
            stale = float(age_ms) > float(stale_threshold_ms)
        except (TypeError, ValueError):
            stale = False
    anchor_blocked = anchor_mode != "LIVE"

    blocked = {"health": not health_ok, "stale": bool(stale), "anchor": anchor_blocked}

    if not health_ok:
        return {
            "alias": alias,
            "asOfMs": as_of_ms,
            "health": "offline",
            "states": [],
            "blocked": blocked,
        }
    if stale:
        return {
            "alias": alias,
            "asOfMs": as_of_ms,
            "health": "stale",
            "states": [],
            "blocked": blocked,
        }
    if anchor_blocked:
        return {
            "alias": alias,
            "asOfMs": as_of_ms,
            "health": "ok",
            "states": [],
            "blocked": blocked,
        }

    or_levels = snap.get("or_levels") or {}
    levels = or_levels.get("levels") if isinstance(or_levels, dict) else None
    if not isinstance(levels, list) or not levels:
        return {
            "alias": alias,
            "asOfMs": as_of_ms,
            "health": "ok",
            "states": [],
            "blocked": blocked,
        }

    events = snap.get("institutional_chart_events") or []
    if not isinstance(events, list):
        events = []
    events_by_label: Dict[str, List[Dict[str, Any]]] = {}
    for ev in events:
        if not isinstance(ev, dict):
            continue
        lbl = (ev.get("label") or "").strip()
        if not lbl:
            continue
        events_by_label.setdefault(lbl, []).append(ev)

    tape = _classify_tape_flow(snap.get("tape_flow"))

    out: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()
    for level in levels:
        if not isinstance(level, dict):
            continue
        label = (level.get("label") or "").strip()
        if not label:
            continue
        level_events = _label_events(events_by_label, label)
        if not level_events:
            continue
        level_with_alias = dict(level)
        level_with_alias["_alias"] = alias or ""
        row = _classify_level(level_with_alias, level_events, tape, now_ms)
        if row is None:
            continue
        if row["id"] in seen_ids:
            continue
        seen_ids.add(row["id"])
        # Closed-vocab guard (defense in depth).
        if row["state"] not in ALLOWED_STATES or row["bias"] not in ALLOWED_BIASES:
            continue
        out.append(row)

    return {
        "alias":   alias,
        "asOfMs":  as_of_ms,
        "health":  "ok",
        "states":  out,
        "blocked": blocked,
    }
