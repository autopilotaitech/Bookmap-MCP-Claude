"""Trigger engine (Phase 4 + linger refactor).

Two trigger categories:

1. EDGE events -- fire ONCE on a state transition, then linger on screen
   for LINGER_MS so the user has time to see them. Cached in
   _ACTIVE_EDGE_TRIGGERS keyed by (kind, label). A new fire of the same
   (kind, label) refreshes the linger window.
     * TREND_SIGNAL_FIRE        (bucket advanced)
     * CONVICTION_FLIP          (sign cross)
     * REGIME_CHANGE            (entered absorption/exhaustion)
     * MICRO_EVENT              (new SPOOF/ICEBERG/STOP_SWEEP)
     * MIDDLE_LOCK_ENTER / EXIT (edge change)

2. STATE conditions -- the chip should be visible AS LONG AS the
   condition holds. Re-emitted every tick the condition is true, NO
   dedup-induced disappearance.
     * LEVEL_APPROACH           (price within prox_ticks)
     * BRIDGE_DEGRADED          (health!=ok or anchor!=LIVE)
     * EOD_RISK                 (session in close/post)
     * NEWS_T_MINUS_5           (news.blocked)

The "flicker like HFT" bug came from treating EDGE events as one-shot
emits: dashboard set changedSinceLastTick=True for a single 1Hz poll,
chip appeared for 1 second, then the next poll showed True->False and
the chip vanished. With the linger cache, an edge fire renders for
LINGER_MS regardless of subsequent ticks.

Spec: docs/superpowers/specs/2026-05-19-pax-ai-design.md section 6.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from . import config


# ---------------------------------------------------------------------------
# State (in-process, ALIAS-SCOPED)
# ---------------------------------------------------------------------------
#
# Pax AI may be pointed at multiple Bookmap aliases over the lifetime of a
# process (NQM6 -> MNQM6 -> ESM6, etc.). Each alias has its own trend
# bucket, its own middle-lock edge, its own regime transitions, and its
# own linger cache. Storing transition / linger state globally meant
# switching aliases inherited or suppressed triggers that belonged to a
# different instrument. The audit explicitly calls this out as a bug.
#
# Each alias has its own _AliasState. The default fallback key is
# "__default__" (used when snap["alias"] is missing or empty).

ALIAS_DEFAULT = "__default__"


def _new_alias_state() -> Dict[str, Any]:
    return {
        "prev_middle_lock":     None,        # bool | None
        "prev_trend_kind":      None,        # str  | None
        "prev_trend_bucket_ms": 0,           # int (last seen bucketEnteredMs)
        "prev_conviction_sign": None,        # +1 / -1 / 0 / None
        "prev_regime":          None,        # str  | None
        "prev_session_code":    None,        # str  | None
        "prev_anchor_mode":     None,        # str  | None
        # Linger cache for EDGE triggers: (kind, label) -> trigger dict
        # with firstSeenMs + lingerUntilMs. Pruned on each
        # compute_triggers call. Per-alias so a chip on NQ never bleeds
        # into MNQ.
        "active_edges":         {},
    }


_STATE_LOCK = threading.Lock()
_PER_ALIAS_STATE: Dict[str, Dict[str, Any]] = {}


def _state_for(alias: Optional[str]) -> Dict[str, Any]:
    """Return the per-alias state dict, creating it on first access.

    Caller MUST already hold _STATE_LOCK.
    """
    key = (alias or "").strip() or ALIAS_DEFAULT
    s = _PER_ALIAS_STATE.get(key)
    if s is None:
        s = _new_alias_state()
        _PER_ALIAS_STATE[key] = s
    return s

# Default linger window for edge events. Overridable via pax_ai_config.json
# (`linger_ms`) for taste.
LINGER_MS_DEFAULT = 15_000

# Per-kind micro_event lookback (how far back into snap["micro_events"] we
# consider events fresh enough to surface). Independent of LINGER_MS_DEFAULT
# -- once an event is too old to be in the lookback window it is also
# dropped from the linger cache via the edge prune.
MICRO_EVENT_LOOKBACK_MS = 5_000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEDUP_BUCKET_MS_DEFAULT = 60_000
CONV_HYSTERESIS = 0.10
ABSORPTION_REGIMES = {"ABSORPTION_BID", "ABSORPTION_ASK",
                      "EXHAUSTION_UP", "EXHAUSTION_DOWN"}


def _linger_ms() -> int:
    """Hot-reloaded linger window. Clamped to a sane range."""
    try:
        v = int(config.get("linger_ms", LINGER_MS_DEFAULT))
    except (TypeError, ValueError):
        return LINGER_MS_DEFAULT
    return max(3_000, min(60_000, v))


def _emit_edge(state: Dict[str, Any], trig: Dict[str, Any], now_ms: int) -> None:
    """Insert / refresh an edge trigger in the per-alias linger cache.

    Same (kind, label) refreshes lingerUntilMs but preserves the original
    firstSeenMs so the UI can sort / age-out consistently.
    """
    key = (trig["kind"], trig.get("label") or "-")
    cache = state["active_edges"]
    prior = cache.get(key)
    trig["firstSeenMs"] = (prior or {}).get("firstSeenMs", now_ms)
    trig["lingerUntilMs"] = now_ms + _linger_ms()
    trig["bucketMs"] = trig["lingerUntilMs"] - trig["firstSeenMs"]
    cache[key] = trig


def _prune_expired_edges(state: Dict[str, Any], now_ms: int) -> None:
    cache = state["active_edges"]
    expired = [k for k, t in cache.items()
                 if now_ms >= int(t.get("lingerUntilMs") or 0)]
    for k in expired:
        cache.pop(k, None)


def _sign_with_hysteresis(score: Optional[float], prev: Optional[int]) -> Optional[int]:
    """Return +1 / -1 / 0 for the current sign with hysteresis. None if input None.

    prev is the last reported sign. We require the score to cross BOTH the
    zero line AND the hysteresis band before flipping the reported sign.
    """
    if score is None:
        return prev
    if prev == 1:
        return -1 if score <= -CONV_HYSTERESIS else 1
    if prev == -1:
        return 1 if score >= CONV_HYSTERESIS else -1
    # First reading: just take the score sign
    if score >= CONV_HYSTERESIS:  return 1
    if score <= -CONV_HYSTERESIS: return -1
    return 0


def _has_fresh_micro_event(events: List[Dict[str, Any]], now_ms: int) -> bool:
    """Backwards-compat helper -- not currently used but kept for tests."""
    for ev in events or []:
        ts = ev.get("ts") or ev.get("tsMs")
        if isinstance(ts, (int, float)) and (now_ms - int(ts)) <= MICRO_EVENT_LOOKBACK_MS:
            return True
    return False


def _ticks_from_pts(pts: Optional[float], tick_size: float) -> Optional[float]:
    if pts is None or tick_size <= 0:
        return None
    return abs(pts) / tick_size


def _root_symbol(alias: Optional[str]) -> str:
    if not alias:
        return ""
    head = alias.split(".")[0].upper()
    i = len(head)
    while i > 0 and head[i - 1].isdigit():
        i -= 1
    stripped = head[:i]
    if i < len(head) and len(stripped) >= 3 and stripped[-1].isalpha():
        return stripped[:-1]
    return stripped or head


# ---------------------------------------------------------------------------
# Trigger detectors (each returns a list of trigger dicts to emit)
# ---------------------------------------------------------------------------

def _trig_level_approach(snap: Dict[str, Any], now_ms: int) -> List[Dict[str, Any]]:
    """STATE condition: chip is visible while ticks_away <= prox_ticks.

    Re-emitted every tick the condition holds -- no dedup. The chip just
    naturally disappears the moment price moves out of proximity.
    """
    out: List[Dict[str, Any]] = []
    or_levels = snap.get("or_levels") or {}
    levels = or_levels.get("levels") or []
    if not levels:
        return out
    prox_ticks = float(config.get("prox_ticks", 8))
    tick_size = float(config.get(f"tick_size.{_root_symbol(snap.get('alias'))}", 0.25))
    for L in levels:
        if not isinstance(L, dict):
            continue
        d = L.get("distance")
        if d is None: continue
        try:
            ticks_away = abs(float(d)) / tick_size
        except (TypeError, ValueError):
            continue
        if ticks_away > prox_ticks:
            continue
        label = L.get("label") or "?"
        conf = L.get("confidence") or 0.0
        severity = "HIGH" if conf >= 0.5 else "MED"
        decision = L.get("decision") or "WAIT"
        out.append({
            "kind":     "LEVEL_APPROACH",
            "severity": severity,
            "label":    label,
            "headline": f"approaching {label} ({d:+.2f}p), {decision}, conf {conf:.2f}",
            "details":  f"price within {ticks_away:.1f} ticks of {label}; "
                          f"composite_score={L.get('composite_score')}",
            "firstSeenMs": now_ms,
            "asOfMs":   now_ms,
        })
    return out


def _trig_middle_lock(state: Dict[str, Any], snap: Dict[str, Any], now_ms: int) -> None:
    """EDGE: fires on middleLock TRANSITION. Linger-cached for visibility."""
    cur = bool((snap.get("or_levels") or {}).get("middleLock"))
    prev = state.get("prev_middle_lock")
    state["prev_middle_lock"] = cur
    if prev is None or prev == cur:
        return
    if cur:
        _emit_edge(state, {
            "kind": "MIDDLE_LOCK_ENTER", "severity": "MED", "label": "-",
            "headline": "mid is inside OR -> STAND DOWN",
            "details": "no entry while middleLock is true; wait for proximity to OR-H/OR-L",
            "asOfMs": now_ms,
        }, now_ms)
    else:
        _emit_edge(state, {
            "kind": "MIDDLE_LOCK_EXIT", "severity": "MED", "label": "-",
            "headline": "mid left the OR interior",
            "details": "middleLock cleared; level proximity re-enabled",
            "asOfMs": now_ms,
        }, now_ms)


_RENDERABLE_TREND_KINDS = ("STRONG_BULL", "WEAK_BULL", "STRONG_BEAR", "WEAK_BEAR")


def _trig_trend_signal_fire(state: Dict[str, Any], snap: Dict[str, Any], now_ms: int) -> None:
    """EDGE: fires on a new bucketEnteredMs that comes with changedSinceLastTick=True.

    The dashboard's compute_trend_signal sets changedSinceLastTick=True
    only on the SINGLE poll where the renderable bucket advanced. Our
    detector catches that one poll and writes the trigger into the
    per-alias linger cache; the chip stays visible for LINGER_MS
    regardless of subsequent False ticks. Per-alias state means the same
    bucketEnteredMs on a *different* alias still fires fresh.
    """
    ts = snap.get("trend_signal") or {}
    cur_kind = ts.get("kind") or "NONE"
    eligible = bool(ts.get("eligible"))
    changed  = bool(ts.get("changedSinceLastTick"))
    bucket   = int(ts.get("bucketEnteredMs") or 0)
    prev_bucket = int(state.get("prev_trend_bucket_ms") or 0)
    state["prev_trend_kind"] = cur_kind
    state["prev_trend_bucket_ms"] = bucket
    if not eligible:
        return
    if cur_kind not in _RENDERABLE_TREND_KINDS:
        return
    if not changed:
        return
    if bucket > 0 and bucket == prev_bucket:
        return
    direction = "long" if "BULL" in cur_kind else "short"
    severity = "HIGH" if cur_kind.startswith("STRONG_") else "MED"
    _emit_edge(state, {
        "kind":     "TREND_SIGNAL_FIRE",
        "severity": severity,
        "label":    cur_kind,
        "headline": f"{cur_kind} fired -- {direction} bias",
        "details":  f"trend_signal mid={ts.get('mid')} "
                      f"bucketEnteredMs={bucket} "
                      f"eventMsSource={ts.get('eventMsSource')}",
        "asOfMs": now_ms,
    }, now_ms)


def _trig_conviction_flip(state: Dict[str, Any], snap: Dict[str, Any], now_ms: int) -> None:
    """EDGE: fires on conviction sign cross past hysteresis band."""
    conv = snap.get("conviction") or {}
    score = conv.get("score")
    prev_sign = state.get("prev_conviction_sign")
    new_sign = _sign_with_hysteresis(score, prev_sign)
    state["prev_conviction_sign"] = new_sign
    if prev_sign is None or new_sign is None or prev_sign == new_sign:
        return
    if prev_sign * new_sign >= 0 and not (prev_sign == 0 and new_sign != 0):
        return
    direction = "bull" if new_sign > 0 else ("bear" if new_sign < 0 else "neutral")
    _emit_edge(state, {
        "kind":     "CONVICTION_FLIP",
        "severity": "HIGH",
        "label":    str(new_sign),
        "headline": (f"conviction crossed to {direction} (score {score:.2f})"
                       if score is not None else f"conviction crossed to {direction}"),
        "details":  f"prev_sign={prev_sign} new_sign={new_sign} "
                      f"trend={conv.get('trend')}",
        "asOfMs": now_ms,
    }, now_ms)


def _trig_regime_change(state: Dict[str, Any], snap: Dict[str, Any], now_ms: int) -> None:
    """EDGE: fires when flow.regime transitions INTO an absorption/exhaustion regime."""
    flow = snap.get("flow") or {}
    cur = flow.get("regime")
    prev = state.get("prev_regime")
    state["prev_regime"] = cur
    if cur is None or cur == prev:
        return
    if cur not in ABSORPTION_REGIMES:
        return
    conf = flow.get("regimeConfidence")
    _emit_edge(state, {
        "kind":     "REGIME_CHANGE",
        "severity": "HIGH",
        "label":    cur,
        "headline": f"regime -> {cur}" + (f" (conf {conf:.2f})" if conf is not None else ""),
        "details":  "absorption/exhaustion entered -- per Pax SKILL this is a FADE setup at the active level",
        "asOfMs": now_ms,
    }, now_ms)


def _trig_micro_event(state: Dict[str, Any], snap: Dict[str, Any], now_ms: int) -> None:
    """EDGE: fires once per new SPOOF/ICEBERG/STOP_SWEEP in the lookback window.

    Linger cache dedups (type, price) tuples so a single iceberg held in
    the snapshot's events ring for several polls only produces one chip.
    """
    me = (snap.get("micro_events") or {}).get("events") or []
    if not isinstance(me, list):
        return
    relevant = {"SPOOF", "ICEBERG", "STOP_SWEEP"}
    for ev in reversed(me):
        if not isinstance(ev, dict):
            continue
        t = ev.get("type")
        if t not in relevant:
            continue
        ts_ms = ev.get("ts") or ev.get("tsMs")
        if not isinstance(ts_ms, (int, float)):
            continue
        if (now_ms - int(ts_ms)) > MICRO_EVENT_LOOKBACK_MS:
            continue
        price = ev.get("price")
        label = f"{t}@{price}" if price is not None else t
        # _emit_edge already dedups by (kind, label) and refreshes linger
        # on identical key -- exactly the iceberg-held-for-many-polls case.
        side = ev.get("side")
        _emit_edge(state, {
            "kind":     "MICRO_EVENT",
            "severity": "MED",
            "label":    label,
            "headline": f"{t}" + (f" {side}" if side else "") +
                            (f" @ {price}" if price is not None else ""),
            "details":  f"event ts={ts_ms} age={now_ms - int(ts_ms)}ms",
            "asOfMs": now_ms,
        }, now_ms)


def _trig_bridge_degraded(snap: Dict[str, Any], now_ms: int) -> List[Dict[str, Any]]:
    """STATE: chip is visible while health!=ok OR anchor!=LIVE."""
    out: List[Dict[str, Any]] = []
    health = snap.get("health")
    session = (snap.get("gates") or {}).get("session") or snap.get("session") or {}
    anchor_mode = session.get("anchorMode")
    if health == "ok" and anchor_mode == "LIVE":
        return out
    if health != "ok":
        out.append({
            "kind":     "BRIDGE_DEGRADED",
            "severity": "MED",
            "label":    "offline",
            "headline": "dashboard offline -- live context unavailable",
            "details":  str(snap.get("bridgeError") or "no detail"),
            "firstSeenMs": now_ms,
            "asOfMs":   now_ms,
        })
    elif anchor_mode and anchor_mode != "LIVE":
        out.append({
            "kind":     "BRIDGE_DEGRADED",
            "severity": "MED",
            "label":    anchor_mode,
            "headline": f"OR anchor is {anchor_mode} (not LIVE)",
            "details":  "all trigger entry signals downgraded to WAIT",
            "firstSeenMs": now_ms,
            "asOfMs":   now_ms,
        })
    return out


def _trig_eod_risk(snap: Dict[str, Any], now_ms: int) -> List[Dict[str, Any]]:
    """STATE: chip is visible while session is in CLOSE_RISK / POST_MARKET."""
    out: List[Dict[str, Any]] = []
    session = (snap.get("gates") or {}).get("session") or snap.get("session") or {}
    code = session.get("code")
    if code not in ("CLOSE_RISK", "POST_MARKET"):
        return out
    out.append({
        "kind":     "EOD_RISK",
        "severity": "LOW",
        "label":    code,
        "headline": f"session={code} -- end-of-day risk window",
        "details":  "avoid initiating new positions; manage runners only",
        "firstSeenMs": now_ms,
        "asOfMs":   now_ms,
    })
    return out


def _trig_news_t_minus(snap: Dict[str, Any], now_ms: int) -> List[Dict[str, Any]]:
    """STATE: chip is visible while news.blocked is true."""
    out: List[Dict[str, Any]] = []
    news = (snap.get("gates") or {}).get("news") or {}
    if news.get("blocked"):
        lbl = news.get("label") or "blackout"
        out.append({
            "kind":     "NEWS_T_MINUS_5",
            "severity": "HIGH",
            "label":    lbl,
            "headline": f"news blackout active: {lbl}",
            "details":  "stand down for new entries inside the blackout window",
            "firstSeenMs": now_ms,
            "asOfMs":   now_ms,
        })
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

SEVERITY_RANK = {"HIGH": 0, "MED": 1, "LOW": 2}


def compute_triggers(snap: Optional[Dict[str, Any]], snap_age_ms: int) -> List[Dict[str, Any]]:
    """Run all detectors on a snapshot. Returns up to 5 active triggers,
    sorted HIGH > MED > LOW, then newest first.

    Two sources combined:
      (a) edge-event linger cache (_STATE["active_edges"]) -- written to
          by _trig_*_fire detectors when a transition is detected; entries
          live for LINGER_MS regardless of subsequent snapshots.
      (b) state-condition detectors -- return their current chips inline
          and they appear/disappear with the underlying condition.

    Stale gate: if snap is None OR snap_age_ms > stale_snapshot_ms OR
    health != "ok", only BRIDGE_DEGRADED is allowed to surface so the
    user still sees that the pipe is broken. Edge-cache entries from
    before the degrade continue to linger (informational), but no new
    edge detection runs against the stale snapshot.
    """
    now_ms = int(time.time() * 1000)
    stale_ms = int(config.get("stale_snapshot_ms", 5000))

    triggers: List[Dict[str, Any]] = []

    # Alias used to scope state. If the snapshot has no alias (offline /
    # cold-start), all stateful detectors share the "__default__" bucket
    # -- consistent with the prior behavior for the single-instrument case.
    alias = (snap or {}).get("alias") if isinstance(snap, dict) else None

    if snap is None or snap_age_ms > stale_ms or snap.get("health") != "ok":
        env = snap if snap is not None else {"health": "offline",
                                              "bridgeError": "no snapshot yet"}
        with _STATE_LOCK:
            state = _state_for(alias)
            _prune_expired_edges(state, now_ms)
            triggers += list(state["active_edges"].values())
            triggers += _trig_bridge_degraded(env, now_ms)
    else:
        with _STATE_LOCK:
            state = _state_for(alias)
            # 1. Run edge detectors -- they write into the per-alias
            #    linger cache.
            _trig_middle_lock(state, snap, now_ms)
            _trig_trend_signal_fire(state, snap, now_ms)
            _trig_conviction_flip(state, snap, now_ms)
            _trig_regime_change(state, snap, now_ms)
            _trig_micro_event(state, snap, now_ms)
            # 2. Prune expired edges.
            _prune_expired_edges(state, now_ms)
            # 3. Compose: lingering edges + state conditions.
            triggers += list(state["active_edges"].values())
            triggers += _trig_level_approach(snap, now_ms)
            triggers += _trig_news_t_minus(snap, now_ms)
            triggers += _trig_bridge_degraded(snap, now_ms)
            triggers += _trig_eod_risk(snap, now_ms)

    # Severity-rank then newest first (firstSeenMs).
    triggers.sort(key=lambda t: (SEVERITY_RANK.get(t.get("severity"), 9),
                                   -int(t.get("firstSeenMs") or t.get("asOfMs") or 0)))
    return triggers[:5]


def reset_state_for_tests() -> None:
    """Wipe ALL alias state. Test-only helper."""
    with _STATE_LOCK:
        _PER_ALIAS_STATE.clear()


def known_aliases_for_tests() -> List[str]:
    """Return the list of alias keys with cached state. Test-only helper."""
    with _STATE_LOCK:
        return list(_PER_ALIAS_STATE.keys())
