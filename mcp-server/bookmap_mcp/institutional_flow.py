"""Continuous institutional flow tracker.

Computes a deterministic regime label (ACCUMULATION / DISTRIBUTION / BALANCED
/ TRANSITION) from per-tick book/tape primitives. Per-alias state cached in
module-level dict. No LLM. Contract + research grounding for weights:
docs/superpowers/specs/2026-05-27-institutional-flow-tracker-design.md
"""

from __future__ import annotations

import math
import time
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
    _CT = ZoneInfo("America/Chicago")
except Exception:
    _CT = None


_NQ_ROTATION_PTS = 65.0
_ES_ROTATION_PTS = 15.0

_REGIME_VOTE_THRESHOLD = 0.20
_REGIME_VOTE_THRESHOLD_ALIGNED = 0.15  # lower threshold when trend filter ALIGNED
# Minimum hold time after firing ACCUMULATION/DISTRIBUTION before the
# OPPOSITE regime can fire. Stops the whipsaw cluster (e.g. 09:40-09:49
# CT 2026-05-28 fired BEAR/BULL/BEAR/BULL in rapid sequence, each one
# chopped). After regime fires, opposite-direction signals just demote
# to BALANCED until this many seconds elapse.
_REGIME_OPPOSITE_HOLD_SEC = 60.0
# TRANSITION threshold raised from 0.10 to 0.20 (2026-05-28): the lower
# value was closing every directional episode on a tiny sign-flip within
# 30s, killing 20-of-20 episodes today as MISS/STALE despite 570 pts of
# session range. Now TRANSITION only fires when the new vote is at least
# regime-strong AND the prior sign was opposite. Noise around zero
# doesn't close episodes anymore.
_TRANSITION_VOTE_THRESHOLD = 0.20
_TREND_OPPOSED_DAMPEN = 0.5
_TEXTBOOK_MICRO_THRESHOLD = 0.7   # |micro_events_signed| >= this AND trend ALIGNED -> force regime
_NO_OPPOSING_WINDOW_SEC = 60.0
_TRANSITION_FLIP_WINDOW_SEC = 30.0
_BREAKOUT_CONVICTION_MIN = 0.70
_OPPOSING_MICRO_EVENT_WINDOW_SEC = 60.0
_ROTATION_STALL_WINDOW_SEC = 300.0
_DIVERGENCE_PRICE_SLOPE_WINDOW_SEC = 300.0
_DIVERGENCE_PRICE_SLOPE_MIN_PTS = 2.0
_HISTORY_MAX = 600
_REGIME_TRAIL_MAX = 600

# Whipsaw lockout (added 2026-05-28 dial-in).
# After 3 directional eps close with peak_favorable < 5pt within 15 min,
# suppress new directional regime fires for 10 min idle. Today's 12:55-13:13
# CT cluster (5/5 STALE/MISS with peaks 0.125, 0, 0, 1.0, 0.125) would have
# triggered this and blocked trades #4 and #6 entries -- correct outcome.
_WHIPSAW_LOOKBACK_MIN = 15.0
_WHIPSAW_PEAK_THRESHOLD_PTS = 5.0
_WHIPSAW_MIN_FAILED_EPS = 3
_WHIPSAW_LOCKOUT_MIN = 10.0
_WHIPSAW_CLOSED_EPS_MAX = 32   # ring buffer for retention beyond lookback

# LT signal quality (added 2026-05-28 dial-in).
# Operator (with MBO eye) observed LT-Ask/LT-Bid flipping sign 3+ times
# in 5 min during low-volume chop while regime + tape disagreed. This is
# active institutional spoofing - they place fake large orders to flip
# the LT bias, the algos chase, then the orders get pulled. To avoid
# trading on spoofed bias, LT contribution is downweighted when:
#   - DEAD          : 30s total tape volume below noise floor.
#   - LIKELY_SPOOFED: LT sign flipped >= N times within 5 min.
#   - RELIABLE      : otherwise.
# When DEAD/LIKELY_SPOOFED, the lt_liquidity_slope reliability is forced
# to 0 so its contribution drops out of the weighted vote.
_LT_QUALITY_WINDOW_SEC = 300.0
_LT_QUALITY_DEAD_VOL_30S = 100      # total contracts in 30s across all buckets
_LT_QUALITY_SPOOF_FLIPS = 3         # sign flips in window -> spoofed
_LT_QUALITY_SIGN_DEADZONE = 0.01    # |ratio| < this counts as zero (not a flip)
_LT_HISTORY_MAX = 64

_CHOP_WINDOWS_CT: Tuple[Tuple[Tuple[int, int], Tuple[int, int], str], ...] = (
    ((11, 0), (12, 30), "us_lunch"),
    ((14, 30), (15, 0), "close_risk"),
    ((16, 0), (17, 0), "cme_maintenance"),
    ((22, 0), (2, 0), "asia_eu_dead_zone"),
)

_WEIGHTS: Tuple[Tuple[str, float], ...] = (
    ("micro_events",        0.18),  # institutional fingerprints (iceberg/absorption/stack/pull) - core per operator thesis
    ("flow_ofi",            0.18),  # Cont/Kukanov OFI z, research-grounded primary
    ("lt_liquidity_slope",  0.15),  # resting liquidity - "where institutions need to fill"
    ("pull_stack_3m",       0.13),  # sustained book-staging window
    ("pull_stack_1m",       0.12),  # mid book-staging window
    ("flow_cvd",            0.10),  # noisier than OFI - operator said delta flips on pullbacks
    ("flow_bias",           0.06),  # derived integrative summary
    ("flow_regime",         0.05),  # slowest classifier label
    ("pull_stack_bbo",      0.03),  # BBO scale dominated by HFT noise
)
assert abs(sum(w for _, w in _WEIGHTS) - 1.0) < 1e-9, "weights must sum to 1.0"


_MICRO_EVENT_WINDOW_SEC = 60.0   # hard cutoff (was 180s — micro lagged ~3 min behind reversals)
_MICRO_EVENT_HALFLIFE_SEC = 30.0  # exp-decay half-life; pairs with freshness scaling below
_MICRO_EVENT_SIGN_MAP: Dict[Tuple[str, bool], float] = {
    # (kind, isBid) -> signed contribution
    ("ICEBERG",    True):  +1.0,  # bid iceberg defending support -> bullish
    ("ICEBERG",    False): -1.0,  # ask iceberg defending resistance -> bearish
    ("ABSORPTION", True):  +1.0,
    ("ABSORPTION", False): -1.0,
    ("STACK",      True):  +1.0,
    ("STACK",      False): -1.0,
    ("PULL",       True):  -1.0,  # pulling bids -> support weakening
    ("PULL",       False): +1.0,  # pulling asks -> resistance weakening
    # STOP_SWEEP: textbook liquidity-grab reversal pattern. When bid-side
    # stops get hit (isBid=true), aggressive sellers just exhausted; the
    # liquidity they used is gone; smart money positions for the bounce.
    # Symmetric for ask-side. To flip the sign if live outcomes disagree,
    # just invert these two lines.
    ("STOP_SWEEP", True):  +1.0,  # bid-stops swept -> reversal up expected
    ("STOP_SWEEP", False): -1.0,  # ask-stops swept -> reversal down expected
    ("SWEEP",      True):  +1.0,  # alias if bridge emits "SWEEP"
    ("SWEEP",      False): -1.0,
    # SPOOF: fake order placed then cancelled = head-fake. Operator
    # directive 2026-05-28 ("they start spoofing sign also"). The
    # institution that placed the fake order revealed intent OPPOSITE
    # to the fake side: fake bid (lured buyers) -> they wanted to SELL
    # into those buyers -> BEARISH; fake ask (lured sellers) ->
    # BULLISH. Weighted at half strength via the same 0.18 input slot
    # because spoof intent is read-through, not direct flow.
    ("SPOOF",      True):  -1.0,  # bid spoofed (pulled) -> bearish intent
    ("SPOOF",      False): +1.0,  # ask spoofed (pulled) -> bullish intent
}


_STATE: Dict[str, Dict[str, Any]] = {}


def _get_state(alias: str) -> Dict[str, Any]:
    s = _STATE.get(alias)
    if s is None:
        s = {
            "history": deque(maxlen=_HISTORY_MAX),
            "regime_trail": deque(maxlen=_REGIME_TRAIL_MAX),
            "rotation": _empty_rotation_state(),
            "last_emitted_bucket_ms": 0,
            "last_trend_dir": None,
            "whipsaw_open_ep": None,
            "whipsaw_closed_eps": deque(maxlen=_WHIPSAW_CLOSED_EPS_MAX),
            "whipsaw_lockout_until_ms": 0,
            "whipsaw_lockout_reason": "",
            "lt_history": deque(maxlen=_LT_HISTORY_MAX),
        }
        _STATE[alias] = s
    return s


def _empty_rotation_state() -> Dict[str, Any]:
    return {
        "commit_level": None,
        "commit_price": None,
        "commit_time_ms": None,
        "rotations_completed": 0,
        "current_extreme_price": None,
        "next_rotation_target": None,
        "_last_rotation_advance_ms": None,
        "_sign": 0,
    }


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def _scaled_z(z: Optional[float]) -> float:
    if z is None:
        return 0.0
    s = z / 2.0
    if s > 1.0:
        return 1.0
    if s < -1.0:
        return -1.0
    return s


def _now_ms() -> int:
    return int(time.time() * 1000)


def _snapshot_ts_ms(snap: Mapping[str, Any]) -> int:
    ts_ms = _safe_float(snap.get("ts_ms"))
    if ts_ms is not None:
        return int(ts_ms)
    ts_str = snap.get("ts")
    if isinstance(ts_str, str):
        try:
            dt = datetime.fromisoformat(ts_str)
            return int(dt.timestamp() * 1000)
        except (ValueError, TypeError):
            pass
    return _now_ms()


def _regime_label_signed(label: Optional[str]) -> float:
    return {
        "TRENDING_UP": 1.0,
        "TRENDING_DOWN": -1.0,
        "EXHAUSTION_UP": -0.5,
        "EXHAUSTION_DOWN": 0.5,
        "ABSORPTION_BID": 0.5,
        "ABSORPTION_ASK": -0.5,
    }.get(label or "", 0.0)


def _extract_inputs(snap: Mapping[str, Any]) -> Dict[str, Tuple[float, float]]:
    out: Dict[str, Tuple[float, float]] = {}

    flow = snap.get("flow") or {}

    ofi_z = _safe_float(flow.get("ofiZ"))
    if ofi_z is None:
        ofi_z = _safe_float(flow.get("ofi"))
    out["flow_ofi"] = (_scaled_z(ofi_z), 0.0 if ofi_z is None else 1.0)

    cvd_z = _safe_float(flow.get("cvdDeltaZ"))
    out["flow_cvd"] = (_scaled_z(cvd_z), 0.0 if cvd_z is None else 1.0)

    ps = snap.get("pull_stack") or {}
    windows = ps.get("windows") or []
    for label_key, name in (
        ("BBO", "pull_stack_bbo"),
        ("1m", "pull_stack_1m"),
        ("3m", "pull_stack_3m"),
    ):
        win = next(
            (w for w in windows if isinstance(w, dict) and w.get("label") == label_key),
            None,
        )
        if win is None:
            out[name] = (0.0, 0.0)
            continue
        bias = win.get("bias")
        z = _safe_float(win.get("zScore"))
        bias_sign = 1.0 if bias == "BULLISH" else (-1.0 if bias == "BEARISH" else 0.0)
        magnitude = abs(_scaled_z(z))
        out[name] = (
            bias_sign * magnitude,
            0.0 if (bias is None and z is None) else 1.0,
        )

    bias_score = _safe_float(flow.get("biasScore"))
    if bias_score is None:
        out["flow_bias"] = (0.0, 0.0)
    else:
        clamped = max(-1.0, min(1.0, bias_score))
        out["flow_bias"] = (clamped, 1.0)

    regime_label = flow.get("regime")
    regime_signed = _regime_label_signed(regime_label)
    out["flow_regime"] = (regime_signed, 0.0 if regime_label is None else 1.0)

    lt = snap.get("lt_liquidity") or {}
    lt_ratio = _safe_float(lt.get("ratio"))
    if lt_ratio is None:
        out["lt_liquidity_slope"] = (0.0, 0.0)
    else:
        clamped = max(-1.0, min(1.0, lt_ratio * 4.0))
        out["lt_liquidity_slope"] = (clamped, 1.0)

    out["micro_events"] = _extract_micro_events_signal(snap)

    return out


def _extract_micro_events_signal(
    snap: Mapping[str, Any],
) -> Tuple[float, float]:
    """Aggregate recent ICEBERG/ABSORPTION/STACK/PULL events into a signed
    [-1, +1] value with reliability {0, 1}.

    Exponential recency decay (30s half-life) inside a 60s hard cutoff,
    then freshness-scaled: the magnitude is multiplied by the freshest
    contributing event's recency. Without that scaling a lone stale
    fingerprint normalizes back to +-1 and pins the signal until the hard
    cutoff -- the live failure where micro stayed +0.85 for ~3 min after
    the last fingerprint while trend had already flipped. SPOOF and SWEEP
    events intentionally NOT consumed in v1 (weak / context-dependent).
    """
    micro = snap.get("micro_events") or snap.get("microstructure_events") or {}
    events = micro.get("events") or []
    if not events:
        return (0.0, 0.0)

    now_ms = _snapshot_ts_ms(snap)
    cutoff_ms = now_ms - int(_MICRO_EVENT_WINDOW_SEC * 1000)

    signed_total = 0.0
    weight_total = 0.0
    max_recency = 0.0
    for ev in events:
        if not isinstance(ev, dict):
            continue
        ts = _safe_float(ev.get("timeMs") or ev.get("timestampMs") or ev.get("ts_ms"))
        if ts is None or ts < cutoff_ms:
            continue
        kind = (ev.get("kind") or ev.get("type") or "").upper()
        is_bid = bool(ev.get("isBid"))
        sign = _MICRO_EVENT_SIGN_MAP.get((kind, is_bid))
        if sign is None:
            continue
        age_sec = max(0.0, (now_ms - ts) / 1000.0)
        recency = 0.5 ** (age_sec / _MICRO_EVENT_HALFLIFE_SEC)
        signed_total += sign * recency
        weight_total += recency
        if recency > max_recency:
            max_recency = recency

    if weight_total == 0.0:
        return (0.0, 0.0)
    avg = signed_total / weight_total
    avg = max(-1.0, min(1.0, avg))
    return (avg * max_recency, 1.0)


def _compute_vote(
    inputs: Mapping[str, Tuple[float, float]],
) -> Tuple[float, List[Dict[str, Any]]]:
    numerator = 0.0
    denominator = 0.0
    contribs: List[Tuple[float, str, float, float]] = []
    for name, weight in _WEIGHTS:
        signed, reliability = inputs.get(name, (0.0, 0.0))
        effective = weight * reliability
        numerator += signed * effective
        denominator += effective
        contribs.append((abs(signed * effective), name, signed, weight))

    vote = numerator / max(1e-9, denominator)
    contribs.sort(key=lambda x: -x[0])
    drivers = [
        {"name": name, "weight": weight, "signed": round(signed, 4)}
        for _, name, signed, weight in contribs
    ]
    return vote, drivers


def _check_opposing_label(
    state: Mapping[str, Any], vote: float, now_ms: int
) -> bool:
    if vote == 0:
        return False
    history = state.get("history") or ()
    cutoff_ms = now_ms - int(_NO_OPPOSING_WINDOW_SEC * 1000)
    sign = 1 if vote > 0 else -1
    for ts_ms, inputs in history:
        if ts_ms < cutoff_ms:
            continue
        regime_signed, _r = inputs.get("flow_regime", (0.0, 0.0))
        if sign > 0 and regime_signed < -0.25:
            return True
        if sign < 0 and regime_signed > 0.25:
            return True
    return False


def _check_transition(
    state: Mapping[str, Any], vote: float, now_ms: int
) -> bool:
    if abs(vote) <= _TRANSITION_VOTE_THRESHOLD:
        return False
    trail = state.get("regime_trail") or ()
    cutoff_ms = now_ms - int(_TRANSITION_FLIP_WINDOW_SEC * 1000)
    current_sign = 1 if vote > 0 else -1
    for ts_ms, _regime, prev_vote in reversed(trail):
        if ts_ms < cutoff_ms:
            break
        prev_sign = 1 if prev_vote > 0 else (-1 if prev_vote < 0 else 0)
        if prev_sign != 0 and prev_sign != current_sign:
            return True
    return False


def _compute_regime_duration(
    state: Mapping[str, Any], current_regime: str, now_ms: int
) -> int:
    trail = list(state.get("regime_trail") or ())
    earliest_same_ms = now_ms
    for ts_ms, regime, _v in reversed(trail):
        if regime == current_regime:
            earliest_same_ms = ts_ms
        else:
            break
    return max(0, (now_ms - earliest_same_ms) // 1000)


def _begin_commit(
    state: Dict[str, Any],
    level_label: str,
    commit_price: float,
    mid: float,
    now_ms: int,
    sign: int,
    rotation_unit: float,
) -> None:
    rot = state["rotation"]
    rot["commit_level"] = level_label
    rot["commit_price"] = commit_price
    rot["commit_time_ms"] = now_ms
    rot["rotations_completed"] = 0
    rot["current_extreme_price"] = mid
    rot["_last_rotation_advance_ms"] = now_ms
    rot["_sign"] = sign
    rot["next_rotation_target"] = commit_price + rotation_unit * sign


def _public_rotation_state(rot: Mapping[str, Any], now_ms: int) -> Dict[str, Any]:
    last_advance = rot.get("_last_rotation_advance_ms")
    time_since = None if last_advance is None else max(0, (now_ms - last_advance) // 1000)
    return {
        "commit_level": rot["commit_level"],
        "commit_price": rot["commit_price"],
        "commit_time_ms": rot["commit_time_ms"],
        "rotations_completed": rot["rotations_completed"],
        "current_extreme_price": rot["current_extreme_price"],
        "next_rotation_target": rot["next_rotation_target"],
        "time_since_last_rotation_sec": time_since,
    }


def _update_rotation_state(
    state: Dict[str, Any],
    mid: Optional[float],
    or_high: Optional[float],
    or_low: Optional[float],
    now_ms: int,
    rotation_unit: float = _NQ_ROTATION_PTS,
) -> Dict[str, Any]:
    rot = state["rotation"]

    if mid is None:
        return _public_rotation_state(rot, now_ms)

    if rot["commit_price"] is None:
        if or_high is not None and mid > or_high + 0.01:
            _begin_commit(state, "OR-H", or_high, mid, now_ms, +1, rotation_unit)
        elif or_low is not None and mid < or_low - 0.01:
            _begin_commit(state, "OR-L", or_low, mid, now_ms, -1, rotation_unit)
        return _public_rotation_state(rot, now_ms)

    sign = rot["_sign"]
    commit_price = rot["commit_price"]

    retraced = (
        (sign > 0 and or_high is not None and mid <= or_high - 0.01)
        or (sign < 0 and or_low is not None and mid >= or_low + 0.01)
    )
    if retraced:
        state["rotation"] = _empty_rotation_state()
        return _public_rotation_state(state["rotation"], now_ms)

    extreme = rot["current_extreme_price"]
    if sign > 0:
        extreme = mid if extreme is None else max(extreme, mid)
    else:
        extreme = mid if extreme is None else min(extreme, mid)
    rot["current_extreme_price"] = extreme

    distance = abs(extreme - commit_price)
    new_rotations = int(distance // rotation_unit)
    if new_rotations > rot["rotations_completed"]:
        rot["rotations_completed"] = new_rotations
        rot["_last_rotation_advance_ms"] = now_ms

    rot["next_rotation_target"] = (
        commit_price + (rot["rotations_completed"] + 1) * rotation_unit * sign
    )

    return _public_rotation_state(rot, now_ms)


def _compute_mid_slope(
    state: Mapping[str, Any], current_mid: Optional[float], now_ms: int
) -> Optional[float]:
    if current_mid is None:
        return None
    history = list(state.get("history") or ())
    if not history:
        return None
    cutoff_ms = now_ms - int(_DIVERGENCE_PRICE_SLOPE_WINDOW_SEC * 1000)
    earliest = None
    for ts_ms, inputs in history:
        if ts_ms < cutoff_ms:
            continue
        m = inputs.get("_mid")
        if m is None:
            continue
        if earliest is None or ts_ms < earliest[0]:
            earliest = (ts_ms, m)
    if earliest is None:
        return None
    return current_mid - earliest[1]


def _compute_divergence(
    state: Mapping[str, Any],
    regime: str,
    vote: float,
    rotation_state: Mapping[str, Any],
    snap: Mapping[str, Any],
    now_ms: int,
) -> Dict[str, bool]:
    mid = _safe_float((snap.get("book") or {}).get("mid"))
    slope = _compute_mid_slope(state, mid, now_ms) or 0.0
    regime_sign = 1 if vote > 0 else (-1 if vote < 0 else 0)
    if slope > _DIVERGENCE_PRICE_SLOPE_MIN_PTS:
        price_sign = 1
    elif slope < -_DIVERGENCE_PRICE_SLOPE_MIN_PTS:
        price_sign = -1
    else:
        price_sign = 0

    regime_vs_price = regime_sign != 0 and price_sign != 0 and regime_sign != price_sign

    flow = snap.get("flow") or {}
    cvd_z = _safe_float(flow.get("cvdDeltaZ")) or 0.0
    cvd_sign = 1 if cvd_z > 0.5 else (-1 if cvd_z < -0.5 else 0)
    cvd_vs_price = cvd_sign != 0 and price_sign != 0 and cvd_sign != price_sign

    time_since = rotation_state.get("time_since_last_rotation_sec")
    rotation_stall = bool(
        regime in ("ACCUMULATION", "DISTRIBUTION")
        and time_since is not None
        and time_since > _ROTATION_STALL_WINDOW_SEC
    )

    return {
        "regime_vs_price": bool(regime_vs_price),
        "cvd_vs_price": bool(cvd_vs_price),
        "rotation_stall": rotation_stall,
    }


def _has_recent_opposing_micro_event(snap: Mapping[str, Any]) -> bool:
    micro = snap.get("micro_events") or snap.get("microstructure_events") or {}
    events = micro.get("events") or []
    if not events:
        return False
    cutoff_ms = (snap.get("ts_ms") or _now_ms()) - int(_OPPOSING_MICRO_EVENT_WINDOW_SEC * 1000)
    for ev in events:
        if not isinstance(ev, dict):
            continue
        ts = _safe_float(ev.get("timestampMs") or ev.get("ts_ms"))
        if ts is None or ts < cutoff_ms:
            continue
        etype = (ev.get("type") or "").upper()
        if "ABSORPTION" in etype or "ICEBERG" in etype:
            return True
    return False


def _stance_for(
    side: Optional[str],
    approaching_from_below: bool,
    regime: str,
    conviction: float,
    micro_opposing: bool,
) -> str:
    if regime in ("BALANCED", "TRANSITION"):
        return "WAIT"
    is_above = side in ("above", "boundary_high")
    is_below = side in ("below", "boundary_low")

    if is_above:
        if approaching_from_below:
            if regime == "ACCUMULATION":
                if conviction > _BREAKOUT_CONVICTION_MIN and not micro_opposing:
                    return "BREAKOUT_LEAN"
                return "ACCEPT_LEAN"
            if regime == "DISTRIBUTION":
                return "FADE_LEAN"
        else:
            if regime == "ACCUMULATION":
                return "ACCEPT_LEAN"
            if regime == "DISTRIBUTION":
                return "FADE_LEAN"
    if is_below:
        if not approaching_from_below:
            if regime == "DISTRIBUTION":
                if conviction > _BREAKOUT_CONVICTION_MIN and not micro_opposing:
                    return "BREAKOUT_LEAN"
                return "ACCEPT_LEAN"
            if regime == "ACCUMULATION":
                return "FADE_LEAN"
        else:
            if regime == "DISTRIBUTION":
                return "ACCEPT_LEAN"
            if regime == "ACCUMULATION":
                return "FADE_LEAN"
    return "WAIT"


def _compute_level_stance(
    or_levels: Mapping[str, Any],
    mid: Optional[float],
    regime: str,
    conviction: float,
    snap: Mapping[str, Any],
) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if mid is None:
        return out
    levels = or_levels.get("levels") or []
    micro_opposing = _has_recent_opposing_micro_event(snap)
    for level in levels:
        if not isinstance(level, dict):
            continue
        label = level.get("label")
        price = _safe_float(level.get("price"))
        if label is None or price is None:
            continue
        side = level.get("side")
        approaching_from_below = mid < price
        out[label] = _stance_for(side, approaching_from_below, regime, conviction, micro_opposing)
    return out


def _apply_trend_filter(
    snap: Mapping[str, Any], raw_vote: float
) -> Tuple[float, str, int]:
    """Dampen the vote when it opposes the filtered trend.

    Trend (with confirm candles -- 2 for Trend #1, 5 for Trend #2 per
    operator's config) is the filtered, low-noise signal. The raw vote
    is built from noisy primaries (OFI, CVD, pull_stack, etc.) and CVD
    in particular flips on every pullback. Trend filters which way the
    sustained move is going; vote tells us how strong that conviction is.

    Returns (filtered_vote, trend_filter_state, trend_sign) where
    trend_filter_state is one of:
      ALIGNED  - raw vote sign matches trend sign -> trust as-is
      OPPOSED  - raw vote sign opposes trend -> dampen by half
                 (likely a CVD pullback that won't sustain)
      NEUTRAL  - trend not warmed, chop, or sign == 0 -> pass through

    The filter never amplifies. Magnitude is set by the underlying inputs;
    trend just gates whether to trust the direction.
    """
    ta = snap.get("trend_analyzer") or {}
    if not ta.get("warmedUp"):
        return raw_vote, "NEUTRAL", 0
    fast = ta.get("fast") or {}
    if not isinstance(fast, dict):
        return raw_vote, "NEUTRAL", 0
    chop = fast.get("chop")
    if chop is True:
        return raw_vote, "NEUTRAL", 0

    trend_sign_raw = _safe_float(fast.get("directionSign"))
    trend_sign = int(trend_sign_raw) if trend_sign_raw is not None else 0
    if trend_sign == 0:
        return raw_vote, "NEUTRAL", 0

    vote_sign = 1 if raw_vote > 0 else (-1 if raw_vote < 0 else 0)
    if vote_sign == 0:
        return raw_vote, "NEUTRAL", trend_sign
    if vote_sign == trend_sign:
        return raw_vote, "ALIGNED", trend_sign
    return raw_vote * _TREND_OPPOSED_DAMPEN, "OPPOSED", trend_sign


def _check_chop_window(now_ms: int) -> Optional[str]:
    if _CT is None:
        return None
    dt = datetime.fromtimestamp(now_ms / 1000.0, tz=_CT)
    now_minutes = dt.hour * 60 + dt.minute
    for (sh, sm), (eh, em), name in _CHOP_WINDOWS_CT:
        start_min = sh * 60 + sm
        end_min = eh * 60 + em
        if start_min < end_min:
            if start_min <= now_minutes < end_min:
                return name
        else:
            if now_minutes >= start_min or now_minutes < end_min:
                return name
    return None


def _compute_lt_signal_quality(
    state: Dict[str, Any],
    snap: Mapping[str, Any],
    now_ms: int,
) -> str:
    """Classify the lt_liquidity bias as RELIABLE / LIKELY_SPOOFED / DEAD.

    Operator-stated philosophy: institutional algos watch LT shifts and
    chase them. Spoofers exploit this by placing fake resting orders,
    flipping the LT bias, then cancelling before fills. During low-volume
    chop, LT signals are most likely to be spoofed.

    Returns one of:
      - "DEAD"            : 30s total tape volume < noise floor
      - "LIKELY_SPOOFED"  : LT sign flipped >= _LT_QUALITY_SPOOF_FLIPS in
                            the last _LT_QUALITY_WINDOW_SEC seconds
      - "RELIABLE"        : otherwise
    """
    # Total 30s tape volume across all buckets. Missing data -> DEAD.
    tape = snap.get("tape_buckets") or {}
    buckets = tape.get("buckets") or []
    total_vol_30s = 0
    for b in buckets:
        if not isinstance(b, dict):
            continue
        try:
            total_vol_30s += int(b.get("buyVol30s") or 0)
            total_vol_30s += int(b.get("sellVol30s") or 0)
        except (TypeError, ValueError):
            continue
    if total_vol_30s < _LT_QUALITY_DEAD_VOL_30S:
        return "DEAD"

    lt = snap.get("lt_liquidity") or {}
    lt_ratio = _safe_float(lt.get("ratio"))
    if lt_ratio is None:
        return "DEAD"

    history = state["lt_history"]
    history.append((now_ms, lt_ratio))
    cutoff_ms = now_ms - int(_LT_QUALITY_WINDOW_SEC * 1000)
    while history and history[0][0] < cutoff_ms:
        history.popleft()

    flip_count = 0
    prev_sign = 0
    for _, r in history:
        if r > _LT_QUALITY_SIGN_DEADZONE:
            sign = 1
        elif r < -_LT_QUALITY_SIGN_DEADZONE:
            sign = -1
        else:
            sign = 0
        if sign != 0 and prev_sign != 0 and sign != prev_sign:
            flip_count += 1
        if sign != 0:
            prev_sign = sign
    if flip_count >= _LT_QUALITY_SPOOF_FLIPS:
        return "LIKELY_SPOOFED"
    return "RELIABLE"


def _compute_trade_eligibility(
    lt_quality: str,
    chop: Optional[str],
    lockout_active: bool,
) -> Tuple[str, str]:
    """Deterministic stand-aside gate, mirroring lt_signal_quality's shape.

    Encodes the operator's discipline rule (2026-05-28): when conditions are
    "bullshit" -- dead tape, a whipsaw lockout, or spoof-heavy chop -- the
    answer is NO_TRADE. Marginal conditions are DIAL_IN_ONLY (observe / paper
    only). Otherwise LIVE_OK. Most-restrictive class wins.

    Pure function of already-computed signals; no LLM, no new inputs.
    """
    if lt_quality == "DEAD":
        return ("NO_TRADE", "tape below noise floor (DEAD lt_signal_quality)")
    if lockout_active:
        return ("NO_TRADE", "whipsaw lockout active")
    if chop and lt_quality == "LIKELY_SPOOFED":
        return ("NO_TRADE",
                f"{chop} chop window + likely-spoofed LT "
                "(low-edge time + manipulation)")
    if lt_quality == "LIKELY_SPOOFED":
        return ("DIAL_IN_ONLY", "LT bias likely spoofed")
    if chop:
        return ("DIAL_IN_ONLY", f"{chop} chop window (low-edge time-of-day)")
    return ("LIVE_OK", "")


def _track_episode_for_whipsaw(
    state: Dict[str, Any],
    regime: str,
    mid: Optional[float],
    now_ms: int,
) -> None:
    """Maintain a per-alias rolling history of CLOSED directional episodes
    and update whipsaw_lockout_until_ms when 3 failed eps accumulate in
    _WHIPSAW_LOOKBACK_MIN.

    Episode = consecutive ticks with the SAME directional regime
    (ACCUMULATION or DISTRIBUTION). Closes when regime transitions to
    BALANCED/TRANSITION or to the OPPOSITE directional. peak_favorable_pts
    is the largest mid-vs-start delta in the direction of the regime sign.

    Called AFTER any lockout suppression has been applied so eps tracked
    here reflect what was actually fired.
    """
    open_ep = state.get("whipsaw_open_ep")

    if regime in ("ACCUMULATION", "DISTRIBUTION"):
        if open_ep is None:
            state["whipsaw_open_ep"] = {
                "regime": regime,
                "start_ms": now_ms,
                "start_mid": mid,
                "peak_favorable_pts": 0.0,
            }
            return
        if open_ep["regime"] == regime:
            if mid is not None and open_ep.get("start_mid") is not None:
                sign = 1.0 if regime == "ACCUMULATION" else -1.0
                fav = (mid - open_ep["start_mid"]) * sign
                if fav > open_ep["peak_favorable_pts"]:
                    open_ep["peak_favorable_pts"] = fav
            return
        # Opposite directional: close prior, open new.
        state["whipsaw_closed_eps"].append({
            "regime": open_ep["regime"],
            "start_ms": open_ep["start_ms"],
            "end_ms": now_ms,
            "peak_favorable_pts": open_ep["peak_favorable_pts"],
        })
        state["whipsaw_open_ep"] = {
            "regime": regime,
            "start_ms": now_ms,
            "start_mid": mid,
            "peak_favorable_pts": 0.0,
        }
    else:
        # Non-directional: close any open episode.
        if open_ep is not None:
            state["whipsaw_closed_eps"].append({
                "regime": open_ep["regime"],
                "start_ms": open_ep["start_ms"],
                "end_ms": now_ms,
                "peak_favorable_pts": open_ep["peak_favorable_pts"],
            })
            state["whipsaw_open_ep"] = None

    # If already in lockout, leave it alone.
    if state.get("whipsaw_lockout_until_ms", 0) > now_ms:
        return

    lookback_ms = now_ms - int(_WHIPSAW_LOOKBACK_MIN * 60_000)
    recent_failed = [
        ep for ep in state["whipsaw_closed_eps"]
        if ep["end_ms"] >= lookback_ms
        and ep["peak_favorable_pts"] < _WHIPSAW_PEAK_THRESHOLD_PTS
    ]
    if len(recent_failed) >= _WHIPSAW_MIN_FAILED_EPS:
        state["whipsaw_lockout_until_ms"] = now_ms + int(_WHIPSAW_LOCKOUT_MIN * 60_000)
        peaks = [round(ep["peak_favorable_pts"], 2)
                 for ep in recent_failed[-_WHIPSAW_MIN_FAILED_EPS:]]
        state["whipsaw_lockout_reason"] = (
            f"{len(recent_failed)} directional eps closed with "
            f"peak<{_WHIPSAW_PEAK_THRESHOLD_PTS}pt in last "
            f"{int(_WHIPSAW_LOOKBACK_MIN)}min; last peaks={peaks}"
        )


def compute_institutional_flow(
    snap: Mapping[str, Any], alias: Optional[str] = None
) -> Dict[str, Any]:
    if alias is None:
        alias = snap.get("alias") or "unknown"
    state = _get_state(alias)
    now_ms = _snapshot_ts_ms(snap)

    inputs = _extract_inputs(snap)
    mid = _safe_float((snap.get("book") or {}).get("mid"))
    inputs["_mid"] = mid

    # LT signal quality (added 2026-05-28). When LT bias is being spoofed
    # (low-volume chop + multiple sign flips in 5 min) OR tape volume is
    # below noise floor, force LT contribution to a NULL OPINION (signed=0,
    # reliability=1). This dilutes the vote toward zero while keeping LT's
    # weight in the denominator -- other inputs must work harder to cross
    # threshold. Critically NOT (0, 0) -- that would REMOVE LT's weight from
    # the denominator and AMPLIFY other inputs, the opposite of the goal.
    lt_signal_quality = _compute_lt_signal_quality(state, snap, now_ms)
    if lt_signal_quality in ("LIKELY_SPOOFED", "DEAD"):
        prior = inputs.get("lt_liquidity_slope")
        if prior is not None and prior[1] > 0.0:
            inputs["lt_liquidity_slope"] = (0.0, prior[1])

    state["history"].append((now_ms, inputs))

    # chop_window is INFORMATIONAL only -- it tags the time-of-day context
    # but does NOT suppress the regime call. Operator decides whether to
    # act on a regime fire during chop. (Earlier behavior force-suppressed
    # everything during lunch, which hid real moves like the under-EXT
    # retest at 11:33 CT 2026-05-28.)
    chop = _check_chop_window(now_ms)
    raw_vote, all_drivers = _compute_vote(inputs)
    vote, trend_filter_state, trend_sign = _apply_trend_filter(snap, raw_vote)
    is_transition = _check_transition(state, vote, now_ms)
    opposing = _check_opposing_label(state, vote, now_ms)
    # Trend-confirmation override: when the trend filter says the vote
    # is ALIGNED with the filtered trend, allow the regime to fire even
    # if recent flow.regime labels were opposite. This captures the
    # "trend flips inside OR + institutional flow stab" setup -- the
    # history WILL have opposing labels because the prior regime was
    # the opposite, but the trend flip is the entry trigger.
    gate = (not opposing) or (trend_filter_state == "ALIGNED")

    # Textbook-setup override: when institutional fingerprints
    # (ICEBERG/ABSORPTION/STACK/PULL/SWEEP) are firing strongly in one
    # direction AND the trend agrees (ALIGNED), force the regime even
    # if the broader weighted vote hasn't crossed threshold. The other
    # 8 inputs (OFI/CVD/regime label/etc.) may lag the institutional
    # footprint; the operator's eye reads the footprint as the trigger.
    # Pinned by tests in test_institutional_flow.py.
    micro_signed = inputs.get("micro_events", (0.0, 0.0))[0]
    textbook_long = (
        micro_signed >= _TEXTBOOK_MICRO_THRESHOLD
        and trend_filter_state == "ALIGNED"
        and trend_sign > 0
    )
    textbook_short = (
        micro_signed <= -_TEXTBOOK_MICRO_THRESHOLD
        and trend_filter_state == "ALIGNED"
        and trend_sign < 0
    )

    # When trend filter is ALIGNED, lower the threshold from 0.20 to
    # 0.15. The bridge's micro-event detector is stricter than the
    # operator's third-party detectors (FV Absorption Alert, Liquidity
    # Tracker Pro), so micro_events alone won't always reach the 0.7
    # textbook threshold even during clear trends. The aligned-threshold
    # path catches these by trusting trend confirmation as enough.
    effective_threshold = (
        _REGIME_VOTE_THRESHOLD_ALIGNED
        if trend_filter_state == "ALIGNED"
        else _REGIME_VOTE_THRESHOLD
    )

    if is_transition:
        regime = "TRANSITION"
    elif textbook_long:
        regime = "ACCUMULATION"
    elif textbook_short:
        regime = "DISTRIBUTION"
    elif vote > effective_threshold and gate:
        regime = "ACCUMULATION"
    elif vote < -effective_threshold and gate:
        regime = "DISTRIBUTION"
    else:
        regime = "BALANCED"

    # Opposite-direction hold: if the most-recent directional regime fired
    # within _REGIME_OPPOSITE_HOLD_SEC, the OPPOSITE regime is demoted to
    # BALANCED. Prevents whipsaw side-to-side regime fires that chopped
    # 09:40-09:49 CT today (4 fires in 9 min, all MISS).
    if regime in ("ACCUMULATION", "DISTRIBUTION"):
        trail = list(state.get("regime_trail") or ())
        hold_cutoff_ms = now_ms - int(_REGIME_OPPOSITE_HOLD_SEC * 1000)
        opposite = "DISTRIBUTION" if regime == "ACCUMULATION" else "ACCUMULATION"
        for past_ts, past_regime, _v in reversed(trail):
            if past_ts < hold_cutoff_ms:
                break
            if past_regime == opposite:
                regime = "BALANCED"
                break

    # Whipsaw lockout: when 3 directional eps have closed STALE-like
    # (peak_favorable < 5pt) within 15 min, suppress new directional
    # fires for 10 min idle. Applied AFTER opposite-hold so the trail
    # reflects the firing intent before suppression.
    lockout_until_ms = int(state.get("whipsaw_lockout_until_ms", 0) or 0)
    lockout_active = now_ms < lockout_until_ms
    if lockout_active and regime in ("ACCUMULATION", "DISTRIBUTION"):
        regime = "BALANCED"

    # Episode tracking + lockout-set check runs against the FINAL regime
    # so eps tracked reflect what actually fired (lockout-suppressed ticks
    # do not open new eps).
    _track_episode_for_whipsaw(state, regime, mid, now_ms)

    conviction = min(1.0, abs(vote) / 0.50)
    drivers = []
    for d in all_drivers[:3]:
        drivers.append({**d, "evidence": f"signed={d['signed']:+.2f}"})

    state["regime_trail"].append((now_ms, regime, vote))

    or_levels = snap.get("or_levels") or {}
    or_high = _safe_float(or_levels.get("orHigh"))
    or_low = _safe_float(or_levels.get("orLow"))
    rotation_state = _update_rotation_state(state, mid, or_high, or_low, now_ms)

    divergence = _compute_divergence(state, regime, vote, rotation_state, snap, now_ms)
    level_stance = _compute_level_stance(or_levels, mid, regime, conviction, snap)

    duration_sec = _compute_regime_duration(state, regime, now_ms)

    trade_eligibility, trade_eligibility_reason = _compute_trade_eligibility(
        lt_signal_quality, chop, lockout_active)

    return {
        "alias": alias,
        "asOfMs": now_ms,
        "regime": regime,
        "conviction": round(conviction, 4),
        "weighted_vote": round(vote, 4),
        "raw_vote_pre_trend_filter": round(raw_vote, 4),
        "trend_filter": trend_filter_state,
        "trend_sign": trend_sign,
        "duration_sec": int(duration_sec),
        "drivers": drivers,
        "divergence": divergence,
        "rotation_state": rotation_state,
        "level_stance": level_stance,
        "chop_window": chop,
        "whipsaw_lockout_until_ms": int(state.get("whipsaw_lockout_until_ms", 0) or 0),
        "whipsaw_lockout_reason": state.get("whipsaw_lockout_reason", "") or "",
        "lt_signal_quality": lt_signal_quality,
        "trade_eligibility": trade_eligibility,
        "trade_eligibility_reason": trade_eligibility_reason,
    }


def build_flow_chart_events(
    flow: Mapping[str, Any], snap: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    regime = flow.get("regime")
    if regime not in ("ACCUMULATION", "DISTRIBUTION"):
        return []

    alias = flow.get("alias") or snap.get("alias") or "unknown"
    ts = _safe_float(flow.get("asOfMs"))
    now_ms = int(ts) if ts is not None else _snapshot_ts_ms(snap)
    # Bucket size must be smaller than the Java painter's
    # CHART_EVENT_RENDER_TTL_MS (4000 ms in PaxOpeningRangeModule). With
    # 1s bucket emission and 4s TTL, ~4 markers overlap at any time -- the
    # chain reads as continuous on chart. The real fix (Java TTL bump)
    # is queued as a follow-up.
    bucket_ms = (now_ms // 1_000) * 1_000

    state = _get_state(alias)
    if bucket_ms <= (state.get("last_emitted_bucket_ms") or 0):
        return []
    state["last_emitted_bucket_ms"] = bucket_ms

    mid = _safe_float((snap.get("book") or {}).get("mid"))
    if mid is None:
        return []

    # Anchor marker at the broken OR boundary (commit_price) when available
    # so successive markers stack at the same y-position instead of drifting
    # with mid. Falls back to mid when no commit yet this session.
    rot = (flow.get("rotation_state") or {})
    commit_price = _safe_float(rot.get("commit_price"))
    anchor_price = commit_price if commit_price is not None else mid

    or_levels = snap.get("or_levels") or {}
    or_high = _safe_float(or_levels.get("orHigh"))
    or_low = _safe_float(or_levels.get("orLow"))
    if or_high is not None and anchor_price > or_high:
        side = "above"
    elif or_low is not None and anchor_price < or_low:
        side = "below"
    else:
        side = "inside"

    direction = "LONG" if regime == "ACCUMULATION" else "SHORT"
    arrow = "^" if direction == "LONG" else "v"
    color = "#2BD25B" if direction == "LONG" else "#FF4D4D"
    conf = float(flow.get("conviction") or 0.0)
    severity = "WARNING" if conf > _BREAKOUT_CONVICTION_MIN else "WATCH"

    drivers = flow.get("drivers") or []
    top_drivers_text = " | ".join(
        f"{d.get('name','')}{d.get('signed', 0):+.2f}"
        for d in drivers[:3]
        if isinstance(d, dict)
    )
    rot_state = flow.get("rotation_state") or {}
    rotations = rot_state.get("rotations_completed") or 0
    next_tgt = rot_state.get("next_rotation_target")
    extreme = rot_state.get("current_extreme_price")
    trend_f = flow.get("trend_filter") or ""
    vote = flow.get("weighted_vote") or 0.0
    # Richer reason packs more context into the 4-second visibility window.
    reason_parts = [
        f"{regime}",
        f"conv={conf:.2f} vote={vote:+.2f} trend={trend_f}",
        f"rot={rotations}",
    ]
    if extreme is not None:
        reason_parts.append(f"ext={extreme:.1f}")
    if next_tgt is not None:
        reason_parts.append(f"tgt={next_tgt:.1f}")
    reason_parts.append(f"[{top_drivers_text}]")
    reason = " ".join(reason_parts)[:240]

    return [
        {
            "id": f"instflow:{alias}:{regime}:{bucket_ms}",
            "alias": alias,
            "label": "IFL",
            "price": anchor_price,
            "side": side,
            "event_type": "LOCAL_ANCHORED",
            "action": "BIAS_SIGNAL",
            "direction": direction,
            "marker_text": f"L{arrow}IFL{int(round(conf * 100))}",
            "marker_color_hint": color,
            "severity": severity,
            "timestamp_ms": now_ms,
            "source": "institutional_flow",
            "confidence": round(conf, 4),
            "reason": reason,
        }
    ]


def reset_state(alias: Optional[str] = None) -> None:
    if alias is None:
        _STATE.clear()
    else:
        _STATE.pop(alias, None)
