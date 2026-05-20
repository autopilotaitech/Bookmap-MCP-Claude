"""Phase 4 trigger engine.

Pure-ish: given the latest snapshot + module-local "previous state" cache,
emit the current list of active triggers. State held in `_STATE` (single
process, single instrument MVP). Dedup by (kind, label, bucket_ms).

All numeric thresholds come from pax_ai_config.json (hot-reloaded). No
LLM math anywhere. Trigger payloads carry headline + details so the UI
can show them as WHY-NOW chips without a Claude round-trip.

Spec: docs/superpowers/specs/2026-05-19-pax-ai-design.md section 6.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from . import config


# ---------------------------------------------------------------------------
# State (in-process)
# ---------------------------------------------------------------------------

_STATE_LOCK = threading.Lock()
_STATE: Dict[str, Any] = {
    # Last value seen per per-kind context. Used for delta detection.
    "prev_middle_lock":       None,        # bool | None
    "prev_trend_kind":        None,        # str  | None
    "prev_conviction_sign":   None,        # +1 / -1 / 0 / None
    "prev_regime":            None,        # str  | None
    "prev_session_code":      None,        # str  | None
    "prev_anchor_mode":       None,        # str  | None
    # Dedup memory: (kind, label) -> last_fire_epoch_ms
    "last_fire_ms":           {},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEDUP_BUCKET_MS_DEFAULT = 60_000
CONV_HYSTERESIS = 0.10
MICRO_EVENT_LOOKBACK_MS = 5_000
ABSORPTION_REGIMES = {"ABSORPTION_BID", "ABSORPTION_ASK",
                      "EXHAUSTION_UP", "EXHAUSTION_DOWN"}


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


def _dedup_ok(kind: str, label: str, now_ms: int, bucket_ms: int) -> bool:
    """Return True if (kind, label) hasn't fired within bucket_ms."""
    key = (kind, label)
    last = _STATE["last_fire_ms"].get(key, 0)
    if now_ms - last < bucket_ms:
        return False
    _STATE["last_fire_ms"][key] = now_ms
    return True


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
    out: List[Dict[str, Any]] = []
    or_levels = snap.get("or_levels") or {}
    levels = or_levels.get("levels") or []
    if not levels:
        return out
    prox_ticks = float(config.get("prox_ticks", 8))
    tick_size = float(config.get(f"tick_size.{_root_symbol(snap.get('alias'))}", 0.25))
    bucket_ms = DEDUP_BUCKET_MS_DEFAULT
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
        if not _dedup_ok("LEVEL_APPROACH", label, now_ms, bucket_ms):
            continue
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
            "bucketMs": bucket_ms,
            "asOfMs":   now_ms,
        })
    return out


def _trig_middle_lock(snap: Dict[str, Any], now_ms: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    cur = bool((snap.get("or_levels") or {}).get("middleLock"))
    prev = _STATE.get("prev_middle_lock")
    _STATE["prev_middle_lock"] = cur
    if prev is None or prev == cur:
        return out
    if cur and _dedup_ok("MIDDLE_LOCK_ENTER", "-", now_ms, DEDUP_BUCKET_MS_DEFAULT):
        out.append({"kind": "MIDDLE_LOCK_ENTER", "severity": "MED", "label": "-",
                     "headline": "mid is inside OR -> STAND DOWN",
                     "details": "no entry while middleLock is true; wait for proximity to OR-H/OR-L",
                     "bucketMs": DEDUP_BUCKET_MS_DEFAULT, "asOfMs": now_ms})
    if (not cur) and _dedup_ok("MIDDLE_LOCK_EXIT", "-", now_ms, DEDUP_BUCKET_MS_DEFAULT):
        out.append({"kind": "MIDDLE_LOCK_EXIT", "severity": "MED", "label": "-",
                     "headline": "mid left the OR interior",
                     "details": "middleLock cleared; level proximity re-enabled",
                     "bucketMs": DEDUP_BUCKET_MS_DEFAULT, "asOfMs": now_ms})
    return out


def _trig_trend_signal_fire(snap: Dict[str, Any], now_ms: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    ts = snap.get("trend_signal") or {}
    cur = ts.get("kind") or "NONE"
    eligible = bool(ts.get("eligible"))
    prev = _STATE.get("prev_trend_kind")
    _STATE["prev_trend_kind"] = cur
    if not eligible:
        return out
    if cur not in ("STRONG_BULL", "STRONG_BEAR"):
        return out
    if cur == prev:
        return out
    if not _dedup_ok("TREND_SIGNAL_FIRE", cur, now_ms, DEDUP_BUCKET_MS_DEFAULT):
        return out
    direction = "long" if cur == "STRONG_BULL" else "short"
    out.append({
        "kind":     "TREND_SIGNAL_FIRE",
        "severity": "HIGH",
        "label":    cur,
        "headline": f"{cur} just fired -- {direction} bias",
        "details":  f"trend_signal eligible at mid {ts.get('mid')}, "
                      f"eventMsSource={ts.get('eventMsSource')}",
        "bucketMs": DEDUP_BUCKET_MS_DEFAULT, "asOfMs": now_ms,
    })
    return out


def _trig_conviction_flip(snap: Dict[str, Any], now_ms: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    conv = snap.get("conviction") or {}
    score = conv.get("score")
    prev_sign = _STATE.get("prev_conviction_sign")
    new_sign = _sign_with_hysteresis(score, prev_sign)
    _STATE["prev_conviction_sign"] = new_sign
    if prev_sign is None or new_sign is None or prev_sign == new_sign:
        return out
    # Only fire on a genuine cross (e.g. +1 -> -1, or 0 -> +/-1).
    if prev_sign * new_sign >= 0 and not (prev_sign == 0 and new_sign != 0):
        return out
    if not _dedup_ok("CONVICTION_FLIP", str(new_sign), now_ms, DEDUP_BUCKET_MS_DEFAULT):
        return out
    direction = "bull" if new_sign > 0 else ("bear" if new_sign < 0 else "neutral")
    out.append({
        "kind":     "CONVICTION_FLIP",
        "severity": "HIGH",
        "label":    str(new_sign),
        "headline": f"conviction crossed to {direction} (score {score:.2f})"
                      if score is not None else f"conviction crossed to {direction}",
        "details":  f"prev_sign={prev_sign} new_sign={new_sign} "
                      f"trend={conv.get('trend')}",
        "bucketMs": DEDUP_BUCKET_MS_DEFAULT, "asOfMs": now_ms,
    })
    return out


def _trig_regime_change(snap: Dict[str, Any], now_ms: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    flow = snap.get("flow") or {}
    cur = flow.get("regime")
    prev = _STATE.get("prev_regime")
    _STATE["prev_regime"] = cur
    if cur is None or cur == prev:
        return out
    if cur not in ABSORPTION_REGIMES:
        return out
    if not _dedup_ok("REGIME_CHANGE", cur, now_ms, DEDUP_BUCKET_MS_DEFAULT):
        return out
    conf = flow.get("regimeConfidence")
    out.append({
        "kind":     "REGIME_CHANGE",
        "severity": "HIGH",
        "label":    cur,
        "headline": f"regime -> {cur}" + (f" (conf {conf:.2f})" if conf is not None else ""),
        "details":  "absorption/exhaustion entered -- per Pax SKILL this is a FADE setup at the active level",
        "bucketMs": DEDUP_BUCKET_MS_DEFAULT, "asOfMs": now_ms,
    })
    return out


def _trig_micro_event(snap: Dict[str, Any], now_ms: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    me = (snap.get("micro_events") or {}).get("events") or []
    if not isinstance(me, list):
        return out
    relevant = {"SPOOF", "ICEBERG", "STOP_SWEEP"}
    # Only consider events within the lookback window
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
        # Dedup by type+price (so we don't re-fire the same iceberg every poll)
        price = ev.get("price")
        dedup_label = f"{t}@{price}" if price is not None else t
        if not _dedup_ok("MICRO_EVENT", dedup_label, now_ms, DEDUP_BUCKET_MS_DEFAULT):
            continue
        side = ev.get("side")
        out.append({
            "kind":     "MICRO_EVENT",
            "severity": "MED",
            "label":    t,
            "headline": f"{t}" + (f" {side}" if side else "") +
                            (f" @ {price}" if price is not None else ""),
            "details":  f"event ts={ts_ms} age={now_ms - int(ts_ms)}ms",
            "bucketMs": DEDUP_BUCKET_MS_DEFAULT, "asOfMs": now_ms,
        })
    return out


def _trig_bridge_degraded(snap: Dict[str, Any], now_ms: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    health = snap.get("health")
    session = (snap.get("gates") or {}).get("session") or snap.get("session") or {}
    anchor_mode = session.get("anchorMode")
    if health == "ok" and anchor_mode == "LIVE":
        # Reset dedup so a future degrade fires again
        return out
    if health != "ok":
        if _dedup_ok("BRIDGE_DEGRADED", "offline", now_ms, DEDUP_BUCKET_MS_DEFAULT):
            out.append({
                "kind":     "BRIDGE_DEGRADED",
                "severity": "MED",
                "label":    "offline",
                "headline": "dashboard offline -- live context unavailable",
                "details":  str(snap.get("bridgeError") or "no detail"),
                "bucketMs": DEDUP_BUCKET_MS_DEFAULT, "asOfMs": now_ms,
            })
    elif anchor_mode and anchor_mode != "LIVE":
        if _dedup_ok("BRIDGE_DEGRADED", anchor_mode, now_ms, DEDUP_BUCKET_MS_DEFAULT):
            out.append({
                "kind":     "BRIDGE_DEGRADED",
                "severity": "MED",
                "label":    anchor_mode,
                "headline": f"OR anchor is {anchor_mode} (not LIVE)",
                "details":  "all trigger entry signals downgraded to WAIT",
                "bucketMs": DEDUP_BUCKET_MS_DEFAULT, "asOfMs": now_ms,
            })
    return out


def _trig_eod_risk(snap: Dict[str, Any], now_ms: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    session = (snap.get("gates") or {}).get("session") or snap.get("session") or {}
    code = session.get("code")
    if code not in ("CLOSE_RISK", "POST_MARKET"):
        return out
    if not _dedup_ok("EOD_RISK", code, now_ms, DEDUP_BUCKET_MS_DEFAULT):
        return out
    out.append({
        "kind":     "EOD_RISK",
        "severity": "LOW",
        "label":    code,
        "headline": f"session={code} -- end-of-day risk window",
        "details":  "avoid initiating new positions; manage runners only",
        "bucketMs": DEDUP_BUCKET_MS_DEFAULT, "asOfMs": now_ms,
    })
    return out


def _trig_news_t_minus(snap: Dict[str, Any], now_ms: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    news = (snap.get("gates") or {}).get("news") or {}
    if news.get("blocked"):
        lbl = news.get("label") or "blackout"
        if _dedup_ok("NEWS_T_MINUS_5", lbl, now_ms, DEDUP_BUCKET_MS_DEFAULT):
            out.append({
                "kind":     "NEWS_T_MINUS_5",
                "severity": "HIGH",
                "label":    lbl,
                "headline": f"news blackout active: {lbl}",
                "details":  "stand down for new entries inside the blackout window",
                "bucketMs": DEDUP_BUCKET_MS_DEFAULT, "asOfMs": now_ms,
            })
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

SEVERITY_RANK = {"HIGH": 0, "MED": 1, "LOW": 2}


def compute_triggers(snap: Optional[Dict[str, Any]], snap_age_ms: int) -> List[Dict[str, Any]]:
    """Run all detectors on a snapshot. Returns up to 5 active triggers,
    sorted HIGH > MED > LOW, then newest first.

    Stale gate: if snap is None OR snap_age_ms > stale_snapshot_ms, only
    BRIDGE_DEGRADED is allowed to fire so the user still sees that the
    pipe is broken.
    """
    now_ms = int(time.time() * 1000)
    stale_ms = int(config.get("stale_snapshot_ms", 5000))

    if snap is None or snap_age_ms > stale_ms or snap.get("health") != "ok":
        # Synthesize a minimal envelope for bridge-degraded detection.
        env = snap if snap is not None else {"health": "offline",
                                              "bridgeError": "no snapshot yet"}
        with _STATE_LOCK:
            triggers = _trig_bridge_degraded(env, now_ms)
    else:
        with _STATE_LOCK:
            triggers: List[Dict[str, Any]] = []
            triggers += _trig_level_approach(snap, now_ms)
            triggers += _trig_middle_lock(snap, now_ms)
            triggers += _trig_trend_signal_fire(snap, now_ms)
            triggers += _trig_conviction_flip(snap, now_ms)
            triggers += _trig_regime_change(snap, now_ms)
            triggers += _trig_micro_event(snap, now_ms)
            triggers += _trig_news_t_minus(snap, now_ms)
            triggers += _trig_bridge_degraded(snap, now_ms)
            triggers += _trig_eod_risk(snap, now_ms)

    # Severity-rank then newest first
    triggers.sort(key=lambda t: (SEVERITY_RANK.get(t.get("severity"), 9),
                                   -int(t.get("asOfMs") or 0)))
    return triggers[:5]


def reset_state_for_tests() -> None:
    """Wipe in-memory state. Test-only helper."""
    with _STATE_LOCK:
        _STATE["prev_middle_lock"] = None
        _STATE["prev_trend_kind"] = None
        _STATE["prev_conviction_sign"] = None
        _STATE["prev_regime"] = None
        _STATE["prev_session_code"] = None
        _STATE["prev_anchor_mode"] = None
        _STATE["last_fire_ms"] = {}
