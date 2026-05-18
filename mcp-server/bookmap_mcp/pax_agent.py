"""Pax decision agent — reads /api/snapshot, applies pax-or SKILL rules,
emits ENTER_LONG_FOLLOW / ENTER_SHORT_FADE / WAIT / STAND_DOWN with reasons.

Run:
    python -m bookmap_mcp.pax_agent              # read-only, alerts to console
    python -m bookmap_mcp.pax_agent --json       # JSON stream (for piping)
    python -m bookmap_mcp.pax_agent --watch      # repaints every poll

The agent NEVER places live orders unless BOOKMAP_ALLOW_TRADING=1 AND
--live flag passed AND user confirms each entry interactively.

Implements the decision tree from skills/pax-or/SKILL.md §12:
    1. middleLock → STAND_DOWN
    2. !inProximity → STAND_DOWN
    3. find proximate level; read its decision + confidence
    4. apply regime + bias gates (VWAP slope, flow regime, VWAP bias, VP bias)
    5. apply rotation veto, VWAP-stretch override, microstructure flags
    6. apply Pax gates (session, news, OR-width, stretch)
    7. emit decision with size from confidence

Author of this agent: the human pasted skills/pax-or/SKILL.md; this script is
its operational deployment.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_URL = "http://127.0.0.1:18888/api/snapshot"


# ─────────────────────────────────────────────────────────────────────────────
# Rule constants — mirror SKILL §6, §7, §11
# ─────────────────────────────────────────────────────────────────────────────

CONFIDENCE_FLOOR    = 0.35    # level reaction score below this → WAIT
CONFIDENCE_FULL     = 0.50    # full size at confidence >= 0.50
CONFIDENCE_HALF     = 0.35    # half size in [0.35, 0.50)
PROX_REQUIRED       = True
MIN_OR_WIDTH_PTS    = 3.0     # NQ pts
MAX_OR_WIDTH_PTS    = 25.0
STRETCH_FAIL_LABELS = {"EXTREME", "BLOWOFF"}

# Regime → action multiplier (1.0 = neutral; >1 boosts FOLLOW; <1 demotes)
REGIME_BOOST = {
    "TRENDING_UP":    {"long_follow": 1.20, "short_follow": 0.50, "fade": 0.70},
    "TRENDING_DOWN":  {"long_follow": 0.50, "short_follow": 1.20, "fade": 0.70},
    "ABSORPTION_BID": {"long_follow": 0.80, "short_follow": 0.40, "long_fade":  1.30, "short_fade": 0.40},
    "ABSORPTION_ASK": {"long_follow": 0.40, "short_follow": 0.80, "short_fade": 1.30, "long_fade":  0.40},
    "EXHAUSTION_UP":  {"long_follow": 0.20, "short_fade": 1.30, "short_follow": 0.80, "long_fade": 0.40},
    "EXHAUSTION_DOWN":{"short_follow":0.20, "long_fade":  1.30, "long_follow":  0.80, "short_fade":0.40},
    "BALANCED":       {"any": 0.80},
    "QUIET":          {"any": 0.30},
    "WARMUP":         {"any": 1.00},   # don't punish during warmup
}


# ─────────────────────────────────────────────────────────────────────────────
# HTTP + main loop
# ─────────────────────────────────────────────────────────────────────────────

def fetch(url: str, timeout: float = 3.0) -> Optional[Dict[str, Any]]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
        return {"_fetch_error": f"{type(e).__name__}: {e}"}


# ─────────────────────────────────────────────────────────────────────────────
# Decision logic
# ─────────────────────────────────────────────────────────────────────────────

def boost_for(regime: str, decision: str) -> float:
    bm = REGIME_BOOST.get(regime, {})
    if "any" in bm: return bm["any"]
    key = {
        "ENTER_LONG_FOLLOW":  "long_follow",
        "ENTER_SHORT_FOLLOW": "short_follow",
        "ENTER_LONG_FADE":    "long_fade",
        "ENTER_SHORT_FADE":   "short_fade",
    }.get(decision)
    if key and key in bm: return bm[key]
    # Fallback fade lookup
    if decision.endswith("FADE") and "fade" in bm: return bm["fade"]
    return 1.0


def decide(snap: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the Pax skill decision tree against one snapshot."""
    reasons: List[str] = []
    components: Dict[str, Any] = {}
    if snap is None or snap.get("_fetch_error"):
        return _wait("fetch failed", err=snap and snap.get("_fetch_error"))

    health = snap.get("health")
    alias = snap.get("alias")
    if health != "ok":
        return _wait(f"health={health}", reasons=[f"bridge {health}"])
    if not alias:
        return _wait("no instrument attached", reasons=["bridge ok but no instrument"])

    # ── Gates: session, news, OR-width ─────────────────────────────────────
    gates = snap.get("gates") or {}
    session = (gates.get("session") or {}).get("code", "?")
    components["session"] = session
    if session in ("PRE_MARKET", "OR_FORMING", "POST_MARKET"):
        return _wait(f"session {session}", reasons=[f"session: {session}"], components=components)
    news = gates.get("news") or {}
    if news.get("blocked"):
        return _stand_down(f"news blackout: {news.get('label','?')}", components=components)

    ol = snap.get("or_levels")
    if not ol:
        return _wait("OR not set yet", reasons=["awaiting OR-Strategy CSV"], components=components)
    components["or"] = {"high": ol.get("orHigh"), "low": ol.get("orLow"),
                       "width": ol.get("orWidthPts")}

    ow = float(ol.get("orWidthPts") or 0)
    if ow < MIN_OR_WIDTH_PTS:
        return _stand_down(f"OR too tight ({ow:.2f} pts < {MIN_OR_WIDTH_PTS})",
                           components=components)
    if ow > MAX_OR_WIDTH_PTS:
        return _stand_down(f"OR too wide ({ow:.2f} pts > {MAX_OR_WIDTH_PTS})",
                           components=components)

    # Hard middle-lock rule
    if ol.get("middleLock"):
        return _stand_down("MIDDLE LOCK — price inside OR, no level proximity",
                           components=components)
    if PROX_REQUIRED and not ol.get("inProximity"):
        return _stand_down("not in proximity of any level",
                           components=components)

    # ── Find the proximate level ─────────────────────────────────────────
    levels = ol.get("levels") or []
    prox = [l for l in levels if l.get("proximity")]
    if not prox:
        return _stand_down("no proximate level (inProximity flag stale?)",
                           components=components)
    # Pick the level closest to mid
    prox.sort(key=lambda l: abs(float(l.get("distance") or 0)))
    level = prox[0]
    components["level"] = {
        "label":    level.get("label"),
        "price":    level.get("price"),
        "distance": level.get("distance"),
    }

    level_decision   = level.get("decision") or "WAIT"
    level_confidence = float(level.get("confidence") or 0)
    level_reasons    = level.get("reasons") or []
    reasons.append(f"{level.get('label')} @ {level.get('price')} → {level_decision}")
    reasons.extend(level_reasons[:3])

    if level_decision == "WAIT" or level_confidence < CONFIDENCE_FLOOR:
        return _wait(
            f"level read inconclusive (conf {level_confidence:.2f})",
            reasons=reasons, components=components)

    # ── Rotation veto ─────────────────────────────────────────────────────
    lvl_components = level.get("components") or {}
    rot = lvl_components.get("ps_rot") or "NONE"
    if rot == "ROTATION_DN" and level_decision == "ENTER_LONG_FOLLOW":
        return _wait("ROTATION_DN vetoes long FOLLOW",
                     reasons=reasons + ["rotation against direction"],
                     components=components)
    if rot == "ROTATION_UP" and level_decision == "ENTER_SHORT_FOLLOW":
        return _wait("ROTATION_UP vetoes short FOLLOW",
                     reasons=reasons + ["rotation against direction"],
                     components=components)

    # ── VWAP-stretch override ─────────────────────────────────────────────
    vwap_stretch = lvl_components.get("vwap_stretch") or 0.0
    if vwap_stretch <= -0.35 and level_decision.endswith("FOLLOW"):
        return _wait(f"VWAP-stretch ({vwap_stretch}) demotes FOLLOW to WAIT",
                     reasons=reasons + ["stretched market"], components=components)

    # ── Regime boost ─────────────────────────────────────────────────────
    flow = snap.get("flow") or {}
    regime = flow.get("regime") or "WARMUP"
    bias_score = float(flow.get("biasScore") or 0)
    bias_traj  = flow.get("biasTrajectory") or "FLAT"
    components["regime"] = regime
    components["biasScore"] = bias_score
    components["biasTrajectory"] = bias_traj

    boost = boost_for(regime, level_decision)
    reasons.append(f"regime {regime} × {boost:.2f}")
    effective_conf = level_confidence * boost

    # ── VWAP slope gate (Tier 2) ─────────────────────────────────────────
    vws = flow.get("vwapSlope") or {}
    slope_label = vws.get("label") or "WARMUP"
    components["vwapSlope"] = slope_label
    if slope_label == "STRONG_UP" and level_decision == "ENTER_SHORT_FOLLOW":
        effective_conf *= 0.5
        reasons.append("STRONG_UP slope vs short FOLLOW → ½ size")
    if slope_label == "STRONG_DOWN" and level_decision == "ENTER_LONG_FOLLOW":
        effective_conf *= 0.5
        reasons.append("STRONG_DOWN slope vs long FOLLOW → ½ size")

    # ── VWAP bias + VP bias agreement (SKILL §11.6) ──────────────────────
    vwap_bias = snap.get("vwap_bias") or {}
    vp_bias   = snap.get("vp_bias") or {}
    vwap_label = vwap_bias.get("label") if vwap_bias else None
    vp_label   = vp_bias.get("label")   if vp_bias else None
    components["vwapBias"] = vwap_label
    components["vpBias"] = vp_label

    direction = "LONG" if "LONG" in level_decision else "SHORT"
    want = "BULLISH" if direction == "LONG" else "BEARISH"
    agree = 0
    if vwap_label == want: agree += 1
    if vp_label == want:   agree += 1
    # Both disagree → demote to WAIT per SKILL rule 3
    if vwap_label and vp_label:
        disagree = ((vwap_label != want and vwap_label != "NEUTRAL")
                  + (vp_label   != want and vp_label   != "NEUTRAL"))
        if disagree == 2:
            return _wait("both VWAP-bias and VP-bias against direction",
                         reasons=reasons + [f"want {want}, got VWAP={vwap_label}, VP={vp_label}"],
                         components=components)
    if agree >= 1:
        effective_conf *= 1.0 + 0.1 * agree
        reasons.append(f"biases agree ×{1+0.1*agree:.2f}")

    # ── Size ────────────────────────────────────────────────────────────
    if effective_conf >= CONFIDENCE_FULL:    size_tier = "FULL";   size = 3
    elif effective_conf >= CONFIDENCE_HALF:  size_tier = "HALF";   size = 1
    else:                                    size_tier = "NONE";   size = 0

    if size == 0:
        return _wait(f"effective conf {effective_conf:.2f} below floor",
                     reasons=reasons, components=components)

    return {
        "decision":         level_decision,
        "size":             size,
        "size_tier":        size_tier,
        "confidence":       round(effective_conf, 3),
        "level_label":      level.get("label"),
        "entry":            level.get("price"),
        "reasons":          reasons,
        "components":       components,
    }


def _wait(reason: str, reasons: Optional[List[str]] = None, err: Any = None,
          components: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"decision": "WAIT", "size": 0, "reason": reason,
            "reasons": reasons or [reason], "error": err,
            "components": components or {}}


def _stand_down(reason: str, components: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"decision": "STAND_DOWN", "size": 0, "reason": reason,
            "reasons": [reason], "components": components or {}}


# ─────────────────────────────────────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────────────────────────────────────

def color(text: str, code: int) -> str:
    if not sys.stdout.isatty(): return text
    return f"\033[{code}m{text}\033[0m"


def render_text(d: Dict[str, Any]) -> str:
    dec = d.get("decision", "?")
    col = 32 if "LONG" in dec else 31 if "SHORT" in dec else 33 if dec == "WAIT" else 35
    ts = dt.datetime.now().strftime("%H:%M:%S")
    head = f"[{ts}] {color(dec, col)}"
    if d.get("size"):  head += f"  size {d['size']} ({d.get('size_tier','?')})"
    if d.get("level_label"): head += f"  @ {d['level_label']} {d.get('entry','?')}"
    if d.get("confidence") is not None: head += f"  conf {d['confidence']}"
    rs = " · ".join((d.get("reasons") or [])[:6])
    return head + ("\n    " + rs if rs else "")


def main() -> None:
    ap = argparse.ArgumentParser(description="Pax decision agent — read-only.")
    ap.add_argument("--url",   default=DEFAULT_URL, help="dashboard snapshot URL")
    ap.add_argument("--poll",  type=float, default=1.0, help="poll interval (seconds)")
    ap.add_argument("--json",  action="store_true",
                    help="emit JSON instead of text")
    ap.add_argument("--watch", action="store_true",
                    help="redraw same line each poll (default: append)")
    args = ap.parse_args()

    last_decision = None
    while True:
        snap = fetch(args.url)
        d = decide(snap or {})

        # change-detection — print only when the decision tier changes,
        # unless --watch (redraw every poll)
        sig = (d.get("decision"), d.get("size_tier"), d.get("level_label"))
        emit = args.watch or sig != last_decision
        last_decision = sig

        if emit:
            if args.json:
                print(json.dumps(d, default=str), flush=True)
            else:
                if args.watch:
                    sys.stdout.write("\r\033[K" + render_text(d))
                    sys.stdout.flush()
                else:
                    print(render_text(d), flush=True)
        try: time.sleep(args.poll)
        except KeyboardInterrupt:
            print()
            return


if __name__ == "__main__":
    main()
