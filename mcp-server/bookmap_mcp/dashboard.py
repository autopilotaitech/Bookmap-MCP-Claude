"""Live trading HUD — back to the version that was working before the momentum redesign.

Run:  python -m bookmap_mcp.dashboard
Open: http://localhost:18888
"""

from __future__ import annotations

import csv
import datetime as dt
import glob
import json
import logging
import math
import os
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from .bridge_client import BridgeClient, BridgeError
from .config import BridgeConfig, MissingTokenError

log = logging.getLogger("bookmap_dashboard")

ET = ZoneInfo("America/New_York")
DISPLAY_TZ = ZoneInfo(os.environ.get("BOOKMAP_DISPLAY_TZ", "America/Chicago"))
DISPLAY_TZ_LABEL = os.environ.get("BOOKMAP_DISPLAY_TZ_LABEL", "CT")
NEWS_CALENDAR_PATH = Path(os.environ.get(
    "BOOKMAP_NEWS_CALENDAR",
    r"C:\Bookmap\addons\MCP\Bookmap\news-calendar.json"))
OR_SIGNAL_GLOBS = [
    r"D:\BookmapLogs\openrange-signals-*.csv",
    r"C:\Bookmap\addons\OR-Strategy\Reference-Indicators\OpenRange\build\logs\openrange-signals-*.csv",
    r"C:\Bookmap\build\logs\openrange-signals-*.csv",
]

# Magnet-levels sync cache.
# Value: (sorted tuple of rounded prices, monotonic seconds of last 2xx post).
# Refresh TTL bounds the "Bookmap restart wiped magnets but dashboard cache
# still thinks they're set" failure mode to _MAGNET_REFRESH_SECS.
_LAST_MAGNETS: Dict[str, Tuple[Tuple[float, ...], float]] = {}
_LAST_MAGNETS_LOCK = threading.Lock()
_MAGNET_REFRESH_SECS = 60.0


def session_state(now_et: dt.datetime) -> Tuple[str, str]:
    minutes = now_et.hour * 60 + now_et.minute
    if minutes < 9*60+30:   return ("PRE_MARKET",   "PRE-MARKET")
    if minutes < 9*60+45:   return ("OR_FORMING",   "OR FORMING (no entry)")
    if minutes < 11*60+30:  return ("ACTIVE",       "RTH ACTIVE")
    if minutes < 12*60:     return ("LATE_MORNING", "Late morning")
    if minutes < 13*60+30:  return ("CHOP",         "CHOP ZONE (no new entries)")
    if minutes < 15*60+30:  return ("AFTERNOON",    "RTH afternoon")
    if minutes < 16*60:     return ("CLOSE_RISK",   "CLOSE RISK (no new entries)")
    return ("POST_MARKET", "POST-MARKET")


def news_blackout(now_et: dt.datetime) -> Tuple[bool, str]:
    try:
        data = json.loads(NEWS_CALENDAR_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return (False, "no calendar configured")
    except Exception as exc:
        return (False, f"calendar read error: {exc}")
    today = now_et.date().isoformat()
    now_hm = now_et.hour * 60 + now_et.minute
    for ev in data.get("blackouts", []):
        if ev.get("date") != today:
            continue
        try:
            sh, sm = map(int, ev["blackout_start"].split(":"))
            eh, em = map(int, ev["blackout_end"].split(":"))
        except Exception:
            continue
        if sh*60 + sm <= now_hm <= eh*60 + em:
            return (True, f"{ev.get('type','EVENT')} {ev['blackout_start']}-{ev['blackout_end']} ET")
    return (False, "clear")


def vwap_from_trades(trades: List[Dict[str, Any]]) -> Optional[float]:
    if not trades: return None
    n, d = 0.0, 0
    for t in trades:
        price, size = t.get("price"), t.get("size", 0)
        if price is None or size <= 0: continue
        n += price * size
        d += size
    return n / d if d else None


def imbalance(trades: List[Dict[str, Any]], window: int) -> Optional[Dict[str, Any]]:
    if not trades: return None
    sub = trades[:window]
    if not sub: return None
    buy = sum(t["size"] for t in sub if t.get("side") == "buy")
    sell = sum(t["size"] for t in sub if t.get("side") == "sell")
    total = buy + sell
    if total == 0: return None
    return {"buy": buy, "sell": sell, "imbalance": (buy - sell) / total}


def momentum_flag(i10, i50, i200) -> str:
    if not (i10 and i50 and i200): return "thin"
    if i10["imbalance"] > 0.2 and i50["imbalance"] > 0.2 and i200["imbalance"] > 0.2:    return "ALIGNED BULL"
    if i10["imbalance"] < -0.2 and i50["imbalance"] < -0.2 and i200["imbalance"] < -0.2: return "ALIGNED BEAR"
    if i200["imbalance"] < -0.1 and i10["imbalance"] > 0.3:                              return "INFLECTION UP"
    if i200["imbalance"] > 0.1 and i10["imbalance"] < -0.3:                              return "INFLECTION DOWN"
    return "neutral"


def or_latest_row() -> Optional[Dict[str, Any]]:
    candidates: List[str] = []
    for pat in OR_SIGNAL_GLOBS:
        candidates.extend(glob.glob(pat))
    if not candidates: return None
    candidates.sort(key=os.path.getmtime, reverse=True)
    path = candidates[0]
    try:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        if not rows: return None
        last = rows[-1]
        last["_csv_path"] = path
        last["_csv_mtime"] = dt.datetime.fromtimestamp(os.path.getmtime(path), ET).isoformat()
        return last
    except Exception:
        return None


def compute_stretch(vwap_obj: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """σ-stretch label from vwap_obj. Returns None if data is missing."""
    if not vwap_obj or "_error" in vwap_obj:
        return None
    vwap = vwap_obj.get("vwap")
    stddev = vwap_obj.get("stddev")
    last = vwap_obj.get("lastTradePrice")
    if not (vwap and stddev and last) or stddev <= 0:
        return None
    dev = (last - vwap) / stddev
    a = abs(dev)
    if a <= 1.0:  label = "FAIR_VALUE"
    elif a <= 2.0: label = "STRETCHED"
    elif a <= 3.0: label = "EXTREME"
    else:          label = "BLOWOFF"
    return {"dev_sigma": dev, "label": label}


def _demote(conf: str) -> str:
    return {"HIGH": "MEDIUM", "MEDIUM": "LOW", "LOW": "LOW"}.get(conf, "LOW")


# ─────────────────────────────────────────────────────────────────────────────
# OR Levels + Jane-Street-style per-level reaction model
#
# Pax canon: enter ONLY at OR-H, OR-L, or extension rungs (+1/+2/+3, -1/-2/-3).
# Never in the middle. At each level, two flavors are legal:
#   FOLLOW  → clean break with confirmation, ride the trend (continuation)
#   FADE    → rotation against the level (price hits → reverses, exhaustion)
# Anywhere else: WAIT or STAND_DOWN.
#
# Per-level score in [-1, +1] where + = FOLLOW-bull / FADE-bear (depending on
# side), magnitude = confidence. Built from six sub-signals:
#   ps_bbo, ps_rotation, lt_lean, tape_size, micro_event, vwap_stretch.
# Plus VP HVN/LVN context as a context flag (not in score, but in reasons).
# ─────────────────────────────────────────────────────────────────────────────

NQ_RUNG_PTS = 65.0          # Pax canon NQ extension rung
NQ_TICK     = 0.25          # NQ tick size
PROX_TICKS  = 50            # 50 ticks = 12.5 pts proximity zone


def _safe_num(d: Any, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict): return default
        cur = cur.get(k)
        if cur is None: return default
    try:
        return float(cur)
    except (TypeError, ValueError):
        return default


def _ps_bbo_bias(ps_obj: Optional[Dict[str, Any]]) -> Tuple[float, str]:
    """BBO pull/stack bias in [-1,+1]: +z normalized = bullish."""
    if not ps_obj or "_error" in ps_obj: return 0.0, "no PS"
    wins = ps_obj.get("windows") or []
    if not wins: return 0.0, "no PS windows"
    bbo = wins[0]
    z = bbo.get("zScore") or 0.0
    # clamp z to [-2, 2] → [-1, +1]
    score = max(-1.0, min(1.0, z / 2.0))
    return score, f"BBO z={z:+.2f}"


def _ps_rotation(ps_obj: Optional[Dict[str, Any]]) -> Tuple[str, float]:
    """Rotation flag → (direction, magnitude). magnitude = |aggregateZ|/2 clamped."""
    if not ps_obj or "_error" in ps_obj: return "NONE", 0.0
    rot = ps_obj.get("rotation") or "NONE"
    aggZ = abs(float(ps_obj.get("aggregateZ") or 0.0))
    return rot, min(1.0, aggZ / 2.0)


def _lt_lean(lt_obj: Optional[Dict[str, Any]]) -> Tuple[float, str]:
    """LT bias in [-1,+1]: +1 = ASK HEAVY (bullish magnet)."""
    if not lt_obj or "_error" in lt_obj: return 0.0, "no LT"
    bid = float(lt_obj.get("bidSize") or 0.0)
    ask = float(lt_obj.get("askSize") or 0.0)
    if bid + ask <= 0: return 0.0, "LT empty"
    ratio = (ask - bid) / (ask + bid)        # +1 = ask heavy = bullish magnet
    return max(-1.0, min(1.0, ratio)), f"LT ratio={ratio:+.2f}"


def _tape_bias(tape_obj: Optional[Dict[str, Any]]) -> Tuple[float, str]:
    """Per-level tape bias in [-1,+1].

    Prefers the institutional-flow delta from `compute_tape_flow` (the new
    size-weighted 30s/5m score) when present. Falls back to the legacy
    biasScore / bias-string shape (currently dead, kept defensively).
    """
    if not tape_obj or "_error" in tape_obj:
        return 0.0, "no tape"
    if "deltaScore" in tape_obj:
        label = tape_obj.get("deltaLabel", "")
        if label == "THIN":
            n30 = tape_obj.get("totalPrints30s", 0)
            return 0.0, f"tape THIN n30={n30}"
        s, _ = _as_float(tape_obj.get("deltaScore"))
        s = _clip(s)
        return s, f"tape Δ={s:+.2f} {label}"
    # Legacy fallback (biasScore / bias string) — kept defensively in case an
    # older bridge build ships without compute_tape_flow upstream.
    score = tape_obj.get("biasScore")
    if score is None:
        b = (tape_obj.get("bias") or "").upper()
        m = {"BULL":0.6,"BULLISH":0.6,"BEAR":-0.6,"BEARISH":-0.6,"NEUTRAL":0.0,"QUIET":0.0}
        return m.get(b, 0.0), f"tape={b or '?'}"
    s = float(score)
    return max(-1.0, min(1.0, s)), f"tape score={s:+.2f}"


def _micro_at_level(me_obj: Optional[Dict[str, Any]], price: float,
                    window_ticks: float = 4.0) -> Tuple[float, str]:
    """Scan recent microstructure events; defender or sweep near price → directional bias."""
    if not me_obj or "_error" in me_obj: return 0.0, "no micro"
    events = me_obj.get("events") or []
    if not events: return 0.0, "no events"
    band = window_ticks * NQ_TICK
    score = 0.0
    hits = []
    for ev in events[-30:]:                  # last 30 events most relevant
        ep = ev.get("price")
        if ep is None: continue
        try: ep = float(ep)
        except (TypeError, ValueError): continue
        if abs(ep - price) > band: continue
        et = (ev.get("kind") or ev.get("type") or "").upper()
        # Java emits isBid:bool; fall back to side string for old clients.
        is_bid = ev.get("isBid")
        if isinstance(is_bid, bool):
            side_str = "BID" if is_bid else "ASK"
        else:
            side_str = (ev.get("side") or "").upper()
        is_ask_like = side_str in ("ASK", "SELL")
        is_bid_like = side_str in ("BID", "BUY")
        # ICEBERG = real defender at level — on ask above pulls bias bearish
        if et == "ICEBERG":
            score += (-0.4 if is_ask_like else +0.4 if is_bid_like else 0.0)
            hits.append(f"ICEBERG@{ep:.2f}/{side_str}")
        elif et == "SPOOF":
            score += (+0.3 if is_ask_like else -0.3 if is_bid_like else 0.0)
            hits.append(f"SPOOF@{ep:.2f}/{side_str}")
        elif et == "STOP_SWEEP":
            score += (+0.5 if is_ask_like else -0.5 if is_bid_like else 0.0)
            hits.append(f"SWEEP@{ep:.2f}/{side_str}")
    return max(-1.0, min(1.0, score)), (", ".join(hits) if hits else "no events at level")


def _vwap_stretch_penalty(vwap_obj: Optional[Dict[str, Any]], price: float) -> Tuple[float, str]:
    """Distance from VWAP in σ. Returns penalty in [-1,0] applied to FOLLOW signals."""
    stretch = compute_stretch(vwap_obj)
    if not stretch: return 0.0, ""
    dev = stretch["dev_sigma"]
    label = stretch["label"]
    a = abs(dev)
    if a <= 1.0: return 0.0, f"VWAP {dev:+.1f}σ"
    if a <= 2.0: return -0.15, f"VWAP {dev:+.1f}σ (stretched)"
    if a <= 3.0: return -0.35, f"VWAP {dev:+.1f}σ (extreme)"
    return -0.60, f"VWAP {dev:+.1f}σ (blowoff)"


def _vp_context(vp_obj: Optional[Dict[str, Any]], price: float) -> str:
    """Cheap HVN/LVN tag for the price level. Returns context string."""
    if not vp_obj or "_error" in vp_obj: return ""
    poc = vp_obj.get("poc")
    vah = vp_obj.get("vah")
    val = vp_obj.get("val")
    try:
        if poc is not None and abs(price - float(poc)) <= 5 * NQ_TICK:
            return "near POC (HVN)"
        if vah is not None and abs(price - float(vah)) <= 5 * NQ_TICK:
            return "near VAH"
        if val is not None and abs(price - float(val)) <= 5 * NQ_TICK:
            return "near VAL"
    except (TypeError, ValueError):
        pass
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Per-level composite — V5.
#
# At each OR/extension magnet, every conviction source contributes a per-level
# directional score. Composite = sum(score × base_weight × reliability) / sum
# (|effective_weight|), clipped to [-1,+1]. Positive = bullish pressure;
# negative = bearish pressure. Direction map by row side:
#   above-side magnets (OR-H, +1/+2/+3):
#     positive → FOLLOW_LONG    negative → FADE_SHORT
#   below-side magnets (OR-L, -1/-2/-3):
#     negative → FOLLOW_SHORT   positive → FADE_LONG
# Composite is ADDITIVE to existing level fields — `score`/`decision`/
# `components`/`reasons` from `_score_level` are NOT removed.
# ─────────────────────────────────────────────────────────────────────────────

# Slow-prior cache: previous poll's conviction per alias. compute_or_levels
# runs before compute_session_conviction in a single fetch_snapshot pass, so
# the composite reads conviction from this cache (populated at the end of the
# prior poll). First-poll-after-startup → reliability 0 for the conviction
# driver, which is correct.
_LAST_CONVICTION: Dict[str, Dict[str, Any]] = {}

# Base weights for the per-magnet composite. Effective = base × reliability.
_LVL_W = {
    "pull_stack":          0.22,
    "tape":                0.18,
    "micro":               0.16,
    "lt_liquidity":        0.12,
    "orderbook":           0.12,
    "vwap":                0.08,    # vwap_stretch + vwap_or gate combined
    "volume_profile":      0.07,
    "session_conviction":  0.05,
}
_LVL_THR_DIRECTIONAL = 0.20   # |composite_score| below this → WAIT
_LVL_THIN_COVERAGE_FRAC = 0.30   # eff_weight/base_weight ratio below this → warn


def _book_at_level(book: Optional[Dict[str, Any]], price: float,
                   side: str) -> Tuple[float, float, str]:
    """Net bid/ask imbalance within ±5 ticks of the level price. Positive =
    bid pressure dominates near the level (bullish); negative = ask pressure
    dominates (bearish). `side` is used only in the reason string."""
    if not book or not isinstance(book, dict) or "_error" in book:
        return 0.0, 0.0, "no book"
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    if not bids and not asks:
        return 0.0, 0.0, "empty book"
    NEAR = 5 * NQ_TICK
    bid_vol = 0
    for b in bids:
        if not isinstance(b, dict):
            continue
        try:
            bp = float(b.get("price"))
        except (TypeError, ValueError):
            continue
        if abs(bp - price) <= NEAR:
            bid_vol += int(b.get("size") or 0)
    ask_vol = 0
    for a in asks:
        if not isinstance(a, dict):
            continue
        try:
            ap = float(a.get("price"))
        except (TypeError, ValueError):
            continue
        if abs(ap - price) <= NEAR:
            ask_vol += int(a.get("size") or 0)
    total = bid_vol + ask_vol
    if total <= 0:
        return 0.0, 0.2, f"no depth ±{NEAR:.2f}pts of {price:.2f}"
    imb = (bid_vol - ask_vol) / total
    score = _clip(_tanh(imb * 1.5))
    return score, 1.0, f"near-level imb={imb:+.2f} (bid={bid_vol}/ask={ask_vol}, {side})"


def _vwap_or_at_level(gate: Optional[Dict[str, Any]],
                       side: str) -> Tuple[float, float, str]:
    """vwap_or gate is global directional context, not price-specific.
    ALLOW_LONG → +0.5; ALLOW_SHORT → -0.5; BLOCKED → 0; UNKNOWN → reliability 0."""
    if not gate or not isinstance(gate, dict):
        return 0.0, 0.0, "no vwap_or gate"
    state = gate.get("state", "UNKNOWN")
    reason = gate.get("reason", "")
    if state == "ALLOW_LONG":
        return +0.5, 1.0, f"vwap_or ALLOW_LONG ({reason})"
    if state == "ALLOW_SHORT":
        return -0.5, 1.0, f"vwap_or ALLOW_SHORT ({reason})"
    if state == "BLOCKED":
        return 0.0, 1.0, f"vwap_or BLOCKED ({reason})"
    return 0.0, 0.0, f"vwap_or {state}"


def _vp_at_level(vp_obj: Optional[Dict[str, Any]], price: float,
                  side: str) -> Tuple[float, float, str]:
    """Volume profile context as a directional score.
    HVN near the level (top-quartile bin within 5 ticks):
      above-side → mild bearish (resistance is real, fade favored)
      below-side → mild bullish (support is real, fade favored)
    LVN near the level (bottom-quartile bin): direction-neutral, lower reliability
      (signals fast-continuation potential but no inherent bias).
    Far from any node → 0 score, reduced reliability."""
    if not vp_obj or not isinstance(vp_obj, dict) or "_error" in vp_obj:
        return 0.0, 0.0, "no volume_profile"
    levels = vp_obj.get("levels") or []
    if not levels:
        return 0.0, 0.0, "vp empty"
    total = vp_obj.get("totalVolume") or 0
    if total <= 0:
        return 0.0, 0.0, "vp no volume"
    nearest = None
    nearest_dist = float("inf")
    for l in levels:
        if not isinstance(l, dict):
            continue
        try:
            lp = float(l.get("price"))
        except (TypeError, ValueError):
            continue
        d = abs(lp - price)
        if d < nearest_dist:
            nearest_dist = d
            nearest = l
    if nearest is None:
        return 0.0, 0.0, "vp no levels"
    if nearest_dist > 5 * NQ_TICK:
        return 0.0, 0.3, f"vp far from nodes (Δ={nearest_dist:.2f}pts)"
    bin_vol = float(nearest.get("volume", 0))
    vols = sorted([float(l.get("volume", 0))
                   for l in levels if isinstance(l, dict)])
    n = len(vols)
    if n < 4:
        return 0.0, 0.3, "vp too few bins"
    p75 = vols[min(n - 1, int(n * 0.75))]
    p25 = vols[max(0, int(n * 0.25))]
    if bin_vol >= p75 and p75 > 0:
        bias = -0.2 if side == "above" else +0.2
        return bias, 1.0, f"HVN at level (vol={int(bin_vol)}, p75={int(p75)})"
    if bin_vol <= p25:
        return 0.0, 0.5, f"LVN at level (vol={int(bin_vol)}, p25={int(p25)})"
    return 0.0, 0.5, f"vp neutral (vol={int(bin_vol)})"


_CONVICTION_TRAJ_NUDGE = {
    "RISING_STRONG":  +0.10,
    "RISING":         +0.05,
    "FLAT":            0.00,
    "FALLING":        -0.05,
    "FALLING_STRONG": -0.10,
}


def _conviction_at_level(conv_obj: Optional[Dict[str, Any]],
                          side: str) -> Tuple[float, float, str]:
    """Slow prior with trajectory modulation.

    Reliability:
    - Ramps in linearly over the first 30 min (0 for first 5 min, full at 30 min).
    - CHOP / MIXED / WARMUP regimes capped at 0.3 so the slow prior can never
      override fast at-level evidence in chop.
    - Divergence cut: when score sign disagrees with trajectory direction
      (e.g. positive score + FALLING trajectory = exhausting bullish run),
      reliability is cut to 0.5 (mild divergence) or 0.5 of normal (strong
      divergence with RISING_STRONG / FALLING_STRONG). This expresses
      "the slow score is losing coherence — weight it less."

    Score:
    - Trajectory adds a small additive nudge (±0.10 max) so a near-zero
      score can still register a directional read when momentum is strong.
    - Final score is clamped to [-1,+1].
    """
    if not conv_obj or not isinstance(conv_obj, dict) or "_error" in conv_obj:
        return 0.0, 0.0, "no conviction (cold start)"
    raw = conv_obj.get("score")
    if not isinstance(raw, (int, float)):
        return 0.0, 0.0, "conviction missing score"
    trend = conv_obj.get("trend", "?")
    traj = (conv_obj.get("trajectory") or "").upper()
    duration_sec = conv_obj.get("durationSec", 0) or 0

    if duration_sec < 300:
        rel = 0.0
    elif duration_sec < 1800:
        rel = (duration_sec - 300) / 1500.0
    else:
        rel = 1.0
    if trend in ("CHOP", "MIXED", "WARMUP"):
        rel = min(rel, 0.3)

    nudge = _CONVICTION_TRAJ_NUDGE.get(traj, 0.0)
    final_score = _clip(float(raw) + nudge)

    # Divergence: score sign vs trajectory sign. A persistent bullish score
    # paired with a falling trajectory is the classic top-out tell — slow
    # prior was right, but momentum is rolling over. Cut reliability.
    score_sign = 1 if float(raw) > 0.05 else (-1 if float(raw) < -0.05 else 0)
    traj_sign = (1 if traj in ("RISING", "RISING_STRONG")
                  else -1 if traj in ("FALLING", "FALLING_STRONG")
                  else 0)
    diverged = (score_sign != 0 and traj_sign != 0 and score_sign != traj_sign)
    if diverged:
        if traj in ("RISING_STRONG", "FALLING_STRONG"):
            rel *= 0.5     # strong divergence: momentum is hard against the score
        else:
            rel *= 0.75    # mild divergence

    reason = f"conviction {trend} score={float(raw):+.2f} traj={traj or '?'}"
    if abs(nudge) > 0:
        reason += f" nudge={nudge:+.2f}"
    if diverged:
        reason += " (DIVERGED)"
    return final_score, rel, reason


def _vwap_stretch_directional(vwap_obj: Optional[Dict[str, Any]],
                              price: float) -> Tuple[float, float, str]:
    """Mean-reversion bias at the LEVEL's price. >+2σ above VWAP → bearish
    (fade short favored); <-2σ → bullish (fade long favored). Near fair value
    → 0. Replaces the FOLLOW-penalty-only legacy helper for composite use."""
    if not vwap_obj or not isinstance(vwap_obj, dict) or "_error" in vwap_obj:
        return 0.0, 0.0, "no vwap"
    vwap = vwap_obj.get("vwap")
    stddev = vwap_obj.get("stddev")
    if vwap is None or stddev is None:
        return 0.0, 0.0, "vwap no σ"
    try:
        sigma = float(stddev)
        if sigma <= 0:
            return 0.0, 0.0, "vwap σ=0"
        dev = (price - float(vwap)) / sigma
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0, 0.0, "vwap bad"
    a = abs(dev)
    if a < 1.0:
        return 0.0, 0.3, f"VWAP {dev:+.1f}σ (fair)"
    sign = -1.0 if dev > 0 else +1.0   # mean-revert direction
    if a < 2.0:
        return _clip(sign * 0.2), 0.7, f"VWAP {dev:+.1f}σ (stretched)"
    if a < 3.0:
        return _clip(sign * 0.5), 1.0, f"VWAP {dev:+.1f}σ (extreme)"
    return _clip(sign * 0.8), 1.0, f"VWAP {dev:+.1f}σ (blowoff)"


def _level_composite(side: str, price: float, mid: float,
                      snap: Dict[str, Any]) -> Dict[str, Any]:
    """Per-magnet composite. Returns the composite block per V5 spec."""
    drivers: List[Dict[str, Any]] = []

    # ----- pull_stack: BBO bias + rotation combined -----
    ps_obj = snap.get("pull_stack")
    ps_bbo_s, ps_bbo_r = _ps_bbo_bias(ps_obj)
    rot_dir, rot_mag = _ps_rotation(ps_obj)
    rot_score = (rot_mag if rot_dir == "ROTATION_UP"
                 else -rot_mag if rot_dir == "ROTATION_DN" else 0.0)
    ps_score = _clip(0.7 * ps_bbo_s + 0.3 * rot_score)
    ps_rel = 1.0 if (isinstance(ps_obj, dict) and "_error" not in ps_obj
                     and "no ps" not in ps_bbo_r) else 0.0
    drivers.append({"name": "pull_stack", "_base_weight": _LVL_W["pull_stack"],
                    "score": ps_score, "reliability": ps_rel,
                    "reason": f"bbo={ps_bbo_s:+.2f} rot={rot_dir}({rot_mag:.2f})"})

    # ----- institutional tape (prefers tape_flow) -----
    tape_obj = snap.get("tape_flow") or snap.get("tape_buckets")
    tape_s, tape_reason = _tape_bias(tape_obj)
    if not tape_obj or (isinstance(tape_obj, dict) and "_error" in tape_obj):
        tape_rel = 0.0
    elif "THIN" in tape_reason:
        tape_rel = 0.2
    elif "no tape" in tape_reason:
        tape_rel = 0.0
    else:
        tape_rel = 1.0
    drivers.append({"name": "tape", "_base_weight": _LVL_W["tape"],
                    "score": tape_s, "reliability": tape_rel, "reason": tape_reason})

    # ----- micro events at level -----
    me_obj = snap.get("micro_events")
    micro_s, micro_reason = _micro_at_level(me_obj, price)
    if not me_obj or (isinstance(me_obj, dict) and "_error" in me_obj):
        micro_rel = 0.0
    elif "no events at level" in micro_reason:
        micro_rel = 0.3
    else:
        micro_rel = 1.0
    drivers.append({"name": "micro", "_base_weight": _LVL_W["micro"],
                    "score": micro_s, "reliability": micro_rel, "reason": micro_reason})

    # ----- LT liquidity -----
    lt_obj = snap.get("lt_liquidity")
    lt_s, lt_reason = _lt_lean(lt_obj)
    lt_rel = 0.8 if (isinstance(lt_obj, dict) and "_error" not in lt_obj
                     and "lt empty" not in lt_reason) else 0.0
    drivers.append({"name": "lt_liquidity", "_base_weight": _LVL_W["lt_liquidity"],
                    "score": lt_s, "reliability": lt_rel, "reason": lt_reason})

    # ----- orderbook at level (new) -----
    book = snap.get("book")
    bk_s, bk_rel, bk_reason = _book_at_level(book, price, side)
    drivers.append({"name": "orderbook", "_base_weight": _LVL_W["orderbook"],
                    "score": bk_s, "reliability": bk_rel, "reason": bk_reason})

    # ----- vwap: stretch + or-gate combined -----
    vwap_obj = snap.get("vwap_obj")
    vw_s, vw_rel, vw_reason = _vwap_stretch_directional(vwap_obj, price)
    gate = (snap.get("gates") or {}).get("vwap_or") if isinstance(snap.get("gates"), dict) else None
    gate_s, gate_rel, gate_reason = _vwap_or_at_level(gate, side)
    vwap_combined = _clip(0.6 * vw_s + 0.4 * gate_s)
    vwap_rel = max(vw_rel, gate_rel)
    drivers.append({"name": "vwap", "_base_weight": _LVL_W["vwap"],
                    "score": vwap_combined, "reliability": vwap_rel,
                    "reason": f"{vw_reason}; {gate_reason}"})

    # ----- volume profile at level (new) -----
    vp_obj = snap.get("volume_profile")
    vp_s, vp_rel, vp_reason = _vp_at_level(vp_obj, price, side)
    drivers.append({"name": "volume_profile", "_base_weight": _LVL_W["volume_profile"],
                    "score": vp_s, "reliability": vp_rel, "reason": vp_reason})

    # ----- session conviction (slow prior, last-poll cache) -----
    alias = snap.get("alias")
    conv_obj = snap.get("conviction")
    if (not conv_obj or (isinstance(conv_obj, dict) and "_error" in conv_obj)) and alias:
        conv_obj = _LAST_CONVICTION.get(alias)
    conv_s, conv_rel, conv_reason = _conviction_at_level(conv_obj, side)
    drivers.append({"name": "session_conviction",
                    "_base_weight": _LVL_W["session_conviction"],
                    "score": conv_s, "reliability": conv_rel, "reason": conv_reason})

    # ----- aggregate -----
    num = 0.0
    eff_total = 0.0
    base_total = 0.0
    for d in drivers:
        bw = d["_base_weight"]
        eff = bw * d["reliability"]
        num += d["score"] * eff
        eff_total += eff
        base_total += bw
    composite_score = _clip(num / eff_total) if eff_total > 0 else 0.0
    # Confidence: signal magnitude × coverage fraction, both in [0,1].
    coverage = (eff_total / base_total) if base_total > 0 else 0.0
    confidence = _clip(abs(composite_score) * coverage, 0.0, 1.0)

    # ----- direction mapping by row side -----
    warnings: List[str] = []
    if abs(composite_score) < _LVL_THR_DIRECTIONAL:
        direction = "WAIT"
    elif side == "above":
        direction = "FOLLOW_LONG" if composite_score > 0 else "FADE_SHORT"
    else:
        direction = "FOLLOW_SHORT" if composite_score < 0 else "FADE_LONG"

    # Gate-conflict warning: directional gate disagrees with composite direction.
    if gate_rel > 0 and gate_s != 0:
        if direction.endswith("_LONG") and gate_s < 0:
            warnings.append(f"vwap_or gate prefers short — {gate_reason}")
        elif direction.endswith("_SHORT") and gate_s > 0:
            warnings.append(f"vwap_or gate prefers long — {gate_reason}")
    # Coverage warning: too many sources missing.
    if coverage < _LVL_THIN_COVERAGE_FRAC:
        warnings.append(
            f"thin coverage: eff_weight={eff_total:.2f}/{base_total:.2f}")
    # Per-driver low-reliability hints (not full warnings; debugging aid).
    missing = [d["name"] for d in drivers if d["reliability"] <= 0.05]
    if len(missing) >= 4:
        warnings.append(f"sources unavailable: {','.join(missing)}")

    return {
        "score":      round(composite_score, 3),
        "direction":  direction,
        "confidence": round(confidence, 3),
        "drivers": [
            {"name": d["name"],
             "score": round(d["score"], 3),
             "weight": round(d["_base_weight"] * d["reliability"], 4),
             "reason": d["reason"]}
            for d in drivers
        ],
        "warnings": warnings,
    }


def _score_level(side: str, price: float, mid: float,
                 ps_obj, lt_obj, tape_obj, me_obj, vwap_obj, vp_obj) -> Dict[str, Any]:
    """Compute FOLLOW vs FADE bias at one level.

    `side` is 'above' (resistance) or 'below' (support) relative to current mid.
    Returns a dict with bias, score, confidence, decision, reasons.

    Sign convention for score (always in [-1, +1]):
      +1 = strongly bullish at this level (long-favored regardless of side)
      -1 = strongly bearish
    The decision then maps to FOLLOW or FADE based on which side the level is.
    """
    reasons: List[str] = []

    ps_bbo_s, ps_bbo_r = _ps_bbo_bias(ps_obj)
    rot_dir, rot_mag   = _ps_rotation(ps_obj)
    lt_s,    lt_r      = _lt_lean(lt_obj)
    tape_s,  tape_r    = _tape_bias(tape_obj)
    micro_s, micro_r   = _micro_at_level(me_obj, price)
    stretch_pen, stretch_r = _vwap_stretch_penalty(vwap_obj, mid)
    vp_ctx = _vp_context(vp_obj, price)

    # Weighted sum — these weights are the model's opinion of signal-to-noise.
    # PS BBO carries the most weight because it's z-scored over the last N min
    # and is the live read of order placement at the BBO.
    w = {"ps_bbo": 0.30, "ps_rot": 0.20, "lt": 0.20, "tape": 0.15, "micro": 0.15}

    score = (
        w["ps_bbo"] * ps_bbo_s
      + w["lt"]    * lt_s
      + w["tape"]  * tape_s
      + w["micro"] * micro_s
    )
    if rot_dir == "ROTATION_UP":
        score += w["ps_rot"] * rot_mag
        reasons.append(f"ROTATION_UP mag={rot_mag:.2f}")
    elif rot_dir == "ROTATION_DN":
        score -= w["ps_rot"] * rot_mag
        reasons.append(f"ROTATION_DN mag={rot_mag:.2f}")

    reasons.extend([ps_bbo_r, lt_r, tape_r, micro_r])
    if stretch_r: reasons.append(stretch_r)
    if vp_ctx:    reasons.append(vp_ctx)

    score = max(-1.0, min(1.0, score))

    # Now map score → decision based on which side the level is.
    # FOLLOW = breakout direction (continuation through the level)
    # FADE   = rotation against the level (rejection)
    if side == "above":
        # Resistance: long FOLLOW if bull score above; short FADE if bear score
        if score >= 0.35:
            decision, label = "ENTER_LONG_FOLLOW", "FOLLOW ▲"
        elif score <= -0.35:
            decision, label = "ENTER_SHORT_FADE", "FADE ▼"
        else:
            decision, label = "WAIT", "WAIT"
    else:
        # Support: short FOLLOW if bear score below; long FADE if bull score
        if score <= -0.35:
            decision, label = "ENTER_SHORT_FOLLOW", "FOLLOW ▼"
        elif score >= 0.35:
            decision, label = "ENTER_LONG_FADE", "FADE ▲"
        else:
            decision, label = "WAIT", "WAIT"

    # VWAP stretch is a penalty on FOLLOW (continuation gets harder when extended)
    if decision.endswith("FOLLOW") and stretch_pen < 0:
        # if signal is on the cusp, demote to WAIT under blowoff conditions
        if abs(score) + stretch_pen < 0.35:
            decision, label = "WAIT", "WAIT (stretched)"

    confidence = abs(score)

    return {
        "decision":     decision,
        "decisionLabel": label,
        "score":        round(score, 3),
        "confidence":   round(confidence, 3),
        "reasons":      [r for r in reasons if r],
        "components": {
            "ps_bbo": round(ps_bbo_s, 2),
            "ps_rot": rot_dir,
            "ps_rot_mag": round(rot_mag, 2),
            "lt":     round(lt_s, 2),
            "tape":   round(tape_s, 2),
            "micro":  round(micro_s, 2),
            "vwap_stretch": round(stretch_pen, 2),
            "vp_ctx": vp_ctx,
        },
    }


def compute_or_levels(snap: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Build the OR + extension level grid with per-level reaction bias.

    Returns None if the OR isn't set yet (CSV missing, before 08:30:30 CT, etc).
    """
    or_row = snap.get("or_row")
    if not or_row: return None
    try:
        or_high = float(or_row.get("orHigh"))
        or_low  = float(or_row.get("orLow"))
    except (TypeError, ValueError):
        return None
    if math.isnan(or_high) or math.isnan(or_low) or or_high <= or_low:
        return None

    book = snap.get("book") or {}
    mid = book.get("mid")
    if mid is None or (isinstance(mid, float) and math.isnan(mid)):
        # fall back to last trade price
        last = (snap.get("vwap_obj") or {}).get("lastTradePrice")
        mid = last if last else (or_high + or_low) / 2.0
    mid = float(mid)

    ps_obj   = snap.get("pull_stack")
    lt_obj   = snap.get("lt_liquidity")
    # Per-level tape must use the same institutional-flow delta that the tape
    # panel and conviction registry use. fetch_snapshot computes tape_flow
    # before OR levels, but this fallback keeps direct compute_or_levels tests
    # and older snapshot callers from silently reading raw bucket arrays as
    # neutral tape.
    tape_obj = snap.get("tape_flow")
    if not isinstance(tape_obj, dict) or "_error" in tape_obj or "deltaScore" not in tape_obj:
        computed_tape = compute_tape_flow(snap)
        if computed_tape is not None:
            snap["tape_flow"] = computed_tape
            tape_obj = computed_tape
        else:
            tape_obj = snap.get("tape_buckets")
    me_obj   = snap.get("micro_events")
    vwap_obj = snap.get("vwap_obj")
    vp_obj   = snap.get("volume_profile")

    rung = NQ_RUNG_PTS
    raw_levels = [
        ("+3",  or_high + 3 * rung, "above"),
        ("+2",  or_high + 2 * rung, "above"),
        ("+1",  or_high + 1 * rung, "above"),
        ("OR-H", or_high,           "above"),
        ("OR-L", or_low,            "below"),
        ("-1",  or_low - 1 * rung,  "below"),
        ("-2",  or_low - 2 * rung,  "below"),
        ("-3",  or_low - 3 * rung,  "below"),
    ]
    prox_pts = PROX_TICKS * NQ_TICK  # 12.5 pts

    levels = []
    for lbl, price, side in raw_levels:
        dist_pts = price - mid
        proximity = abs(dist_pts) <= prox_pts
        reaction = _score_level(side, price, mid,
                                ps_obj, lt_obj, tape_obj, me_obj, vwap_obj, vp_obj)
        composite = _level_composite(side, price, mid, snap)
        levels.append({
            "label":     lbl,
            "price":     round(price, 2),
            "side":      side,
            "distance":  round(dist_pts, 2),
            "proximity": proximity,
            **reaction,
            "composite": composite,
        })

    # Pax discipline check: is price currently in the middle (i.e., not in proximity
    # of any level)? If so, the day's verdict is STAND_DOWN regardless of components.
    in_proximity = any(l["proximity"] for l in levels)
    middle_lock = not in_proximity and (or_low <= mid <= or_high)

    return {
        "anchor":      "RTH 08:30 CT (30s)",
        "orHigh":      round(or_high, 2),
        "orLow":       round(or_low, 2),
        "orWidthPts":  round(or_high - or_low, 2),
        "rungPts":     rung,
        "mid":         round(mid, 2),
        "proxTicks":   PROX_TICKS,
        "proxPts":     prox_pts,
        "inProximity": in_proximity,
        "middleLock":  middle_lock,
        "levels":      levels,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Institutional tape flow — time/volume-aware delta from /tape_buckets.
#
# Bucket size weights (1-10..100+): 0.10 / 0.20 / 0.40 / 0.80 / 1.00.
# Per-window weighted imbalance fed through tanh(2x); 0.65 fast (30s) + 0.35
# slow (5m); +/-0.10 alignment bonus when both windows agree directionally;
# thin-sample guard (floor 5 prints / hedge 15 prints in 30s).
#
# Bookmap trade records do NOT carry account or counterparty information.
# This is a probabilistic institutional proxy via size-weighted aggressor
# imbalance, not a label. The deltaReason field always cites raw counts.
# ─────────────────────────────────────────────────────────────────────────────

_TAPE_BUCKET_WEIGHTS = {
    "1-10":   0.10,
    "11-25":  0.20,
    "26-50":  0.40,
    "51-99":  0.80,
    "100+":   1.00,
}
_TAPE_LARGE_LABELS = ("51-99", "100+")
_TAPE_BLOCK_LABELS = ("100+",)
_TAPE_THIN_FLOOR_PRINTS = 5     # n30 < this → THIN, score=0
_TAPE_THIN_HEDGE_PRINTS = 15    # n30 between FLOOR..HEDGE → linear shrink
_TAPE_ALIGN_BONUS = 0.10        # signed bonus when fast/slow both directional + agree
_TAPE_ALIGN_THRESHOLD = 0.25    # min |fast|, |slow| to trigger alignment


def _imb(buy: float, sell: float) -> float:
    t = buy + sell
    return ((buy - sell) / t) if t > 0 else 0.0


def compute_tape_flow(snap: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Size-weighted institutional flow delta over 30s + 5m windows from
    /tape_buckets. Returns None when tape_buckets is missing or shapeless.
    """
    tape = snap.get("tape_buckets")
    if not isinstance(tape, dict) or "_error" in tape:
        return None
    buckets = tape.get("buckets")
    if not isinstance(buckets, list) or not buckets:
        return None

    tot_buy30 = tot_sell30 = tot_prints30 = 0
    tot_buy5  = tot_sell5  = tot_prints5  = 0
    lg_buy30  = lg_sell30  = lg_prints30  = 0
    lg_buy5   = lg_sell5   = lg_prints5   = 0
    bk_buy30  = bk_sell30  = bk_prints30  = 0
    bk_buy5   = bk_sell5   = bk_prints5   = 0
    wnum30 = wden30 = 0.0
    wnum5  = wden5  = 0.0

    for b in buckets:
        if not isinstance(b, dict):
            continue
        label = b.get("label", "")
        w = _TAPE_BUCKET_WEIGHTS.get(label, 0.5)
        bv30, _ = _as_float(b.get("buyVol30s"));   bv30 = int(bv30)
        sv30, _ = _as_float(b.get("sellVol30s"));  sv30 = int(sv30)
        pn30, _ = _as_float(b.get("prints30s"));   pn30 = int(pn30)
        bv5,  _ = _as_float(b.get("buyVol5m"));    bv5  = int(bv5)
        sv5,  _ = _as_float(b.get("sellVol5m"));   sv5  = int(sv5)
        pn5,  _ = _as_float(b.get("prints5m"));    pn5  = int(pn5)

        tot_buy30  += bv30; tot_sell30 += sv30; tot_prints30 += pn30
        tot_buy5   += bv5;  tot_sell5  += sv5;  tot_prints5  += pn5
        if label in _TAPE_LARGE_LABELS:
            lg_buy30 += bv30; lg_sell30 += sv30; lg_prints30 += pn30
            lg_buy5  += bv5;  lg_sell5  += sv5;  lg_prints5  += pn5
        if label in _TAPE_BLOCK_LABELS:
            bk_buy30 += bv30; bk_sell30 += sv30; bk_prints30 += pn30
            bk_buy5  += bv5;  bk_sell5  += sv5;  bk_prints5  += pn5

        wnum30 += w * (bv30 - sv30)
        wden30 += w * (bv30 + sv30)
        wnum5  += w * (bv5  - sv5)
        wden5  += w * (bv5  + sv5)

    w_imb_30 = (wnum30 / wden30) if wden30 > 0 else 0.0
    w_imb_5  = (wnum5  / wden5)  if wden5  > 0 else 0.0
    fast = _tanh(2.0 * w_imb_30)
    slow = _tanh(2.0 * w_imb_5)
    base = 0.65 * fast + 0.35 * slow

    aligned = (abs(fast) >= _TAPE_ALIGN_THRESHOLD
               and abs(slow) >= _TAPE_ALIGN_THRESHOLD
               and ((fast > 0) == (slow > 0))
               and fast != 0.0)
    align = _TAPE_ALIGN_BONUS * (1.0 if fast > 0 else -1.0) if aligned else 0.0

    base_payload = {
        "totalBuyVol30s": tot_buy30, "totalSellVol30s": tot_sell30, "totalPrints30s": tot_prints30,
        "totalBuyVol5m":  tot_buy5,  "totalSellVol5m":  tot_sell5,  "totalPrints5m":  tot_prints5,
        "largeBuyVol30s": lg_buy30,  "largeSellVol30s": lg_sell30,  "largePrints30s": lg_prints30,
        "largeBuyVol5m":  lg_buy5,   "largeSellVol5m":  lg_sell5,   "largePrints5m":  lg_prints5,
        "blockBuyVol30s": bk_buy30,  "blockSellVol30s": bk_sell30,  "blockPrints30s": bk_prints30,
        "blockBuyVol5m":  bk_buy5,   "blockSellVol5m":  bk_sell5,   "blockPrints5m":  bk_prints5,
        "largeImbalance30s": _imb(lg_buy30, lg_sell30),
        "largeImbalance5m":  _imb(lg_buy5,  lg_sell5),
        "blockImbalance30s": _imb(bk_buy30, bk_sell30),
        "blockImbalance5m":  _imb(bk_buy5,  bk_sell5),
        "fast": fast, "slow": slow, "aligned": aligned,
    }

    if tot_prints30 < _TAPE_THIN_FLOOR_PRINTS:
        return {
            "deltaScore":  0.0,
            "deltaLabel":  "THIN",
            "deltaReason": f"thin tape: n30={tot_prints30}",
            "shrink":      0.0,
            **base_payload,
        }

    shrink = min(1.0, tot_prints30 / float(_TAPE_THIN_HEDGE_PRINTS))
    score = _clip(shrink * (base + align), -1.0, 1.0)

    abs_s = abs(score)
    if abs_s < 0.15:
        label = "BALANCED"
    elif abs_s < 0.50:
        label = "BUY" if score > 0 else "SELL"
    else:
        label = "STRONG_BUY" if score > 0 else "STRONG_SELL"

    reason = (
        f"30s wImb={w_imb_30:+.2f}, 5m wImb={w_imb_5:+.2f}, "
        f"{'aligned' if aligned else 'mixed'}, n30={tot_prints30}, "
        f"large30s ▲{lg_buy30}/▼{lg_sell30}"
    )
    return {
        "deltaScore":  score,
        "deltaLabel":  label,
        "deltaReason": reason,
        "shrink":      shrink,
        **base_payload,
    }


# ─────────────────────────────────────────────────────────────────────────────
# VWAP bias and Volume Profile bias  (Tier 1: pure-Python over existing snapshot)
#
# Each returns a dict shaped like:
#   { score: float in [-1,+1], label: BULLISH|BEARISH|NEUTRAL,
#     components: {...},  reasons: [...] }
#
# Components are based on what a Jane-Street-style desk would compute against
# the data we already have in the snapshot:
#   - VWAP: σ-distance, regime (mean-revert <1σ, revert 1-2σ, continuation >2σ),
#           RTH/ETH divergence, volume-weighted distance (uses VP histogram)
#   - VP:   POC distance (σ-normalized), Value-Area position, HVN/LVN magnet
#           from peak detection on the levels histogram.
#
# Bands: |score| > 0.25 → directional, else NEUTRAL.
# ─────────────────────────────────────────────────────────────────────────────

def _tanh(x: float) -> float:
    try: return math.tanh(x)
    except (OverflowError, ValueError): return 0.0


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    if x < lo: return lo
    if x > hi: return hi
    return x


def compute_vwap_bias(snap: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Composite VWAP-based bias score in [-1,+1].

    Reads vwap_obj (RTH-anchored with σ-bands) + ETH overlay + volume_profile.
    Returns None when there's no usable VWAP data yet (no RTH trades).
    """
    vobj = snap.get("vwap_obj") or {}
    if not vobj or "_error" in vobj: return None
    vwap   = vobj.get("vwap")
    stddev = vobj.get("stddev")
    last   = vobj.get("lastTradePrice")
    if vwap is None or stddev is None or stddev <= 0 or last is None:
        return None

    book = snap.get("book") or {}
    mid = book.get("mid")
    if mid is None or (isinstance(mid, float) and math.isnan(mid)):
        mid = last
    try: mid = float(mid)
    except (TypeError, ValueError): mid = float(last)

    components: Dict[str, Any] = {}
    reasons: List[str] = []

    # 1) σ-distance from VWAP
    z = (mid - float(vwap)) / float(stddev)
    s_sigma = _tanh(z / 2.0)        # squash so ±2σ → ~±0.76
    components["sigma_z"] = round(z, 2)
    reasons.append(f"σ-z {z:+.2f}")

    # 2) Regime: where on the band-ladder
    az = abs(z)
    if az < 1.0:
        s_regime = 0.0
        reg_label = "INSIDE_BAND"
    elif az < 2.0:
        # Mean-revert pressure toward VWAP — sign opposite price displacement
        s_regime = -0.6 * (1.0 if z > 0 else -1.0)
        reg_label = "MEAN_REVERT"
    elif az < 3.0:
        # Without a slope gate (Tier 2 work), treat ±2-3σ as exhaustion bias
        # leaning back toward VWAP, but weaker than the 1-2σ revert zone.
        s_regime = -0.45 * (1.0 if z > 0 else -1.0)
        reg_label = "STRETCHED_REVERT"
    else:
        # ±3σ blowoff: strong mean-revert signal
        s_regime = -0.9 * (1.0 if z > 0 else -1.0)
        reg_label = "BLOWOFF_REVERT"
    components["regime"] = reg_label
    reasons.append(reg_label)

    # 3) RTH vs ETH VWAP divergence — uses ETH overlay if present
    eth = vobj.get("eth") if isinstance(vobj.get("eth"), dict) else None
    s_div = 0.0
    if eth and eth.get("vwap"):
        try:
            div = float(vwap) - float(eth["vwap"])
            # Normalize by stddev — gives σ-units of RTH-vs-ETH dislocation
            s_div = _clip(div / float(stddev) / 1.5)
            components["rth_eth_div_sigma"] = round(div / float(stddev), 2)
            reasons.append(f"RTH-ETH {div:+.2f} ({components['rth_eth_div_sigma']:+.2f}σ)")
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    # 4) Volume-weighted distance (uses VP histogram)
    s_vwd = 0.0
    vp = snap.get("volume_profile")
    if vp and not vp.get("_error"):
        levels = vp.get("levels") or []
        if levels:
            # crude: find volume at mid and at VWAP tick
            try:
                tick_size = 0.25     # NQ tick
                v_at_mid  = 0.0
                v_at_vwap = 0.0
                for lvl in levels:
                    lp = float(lvl.get("price", 0.0))
                    lv = float(lvl.get("volume", 0.0))
                    if abs(lp - mid)  <= tick_size: v_at_mid  += lv
                    if abs(lp - vwap) <= tick_size: v_at_vwap += lv
                if v_at_mid > 0 and v_at_vwap > 0:
                    ratio = math.sqrt(v_at_vwap / max(v_at_mid, 1.0))
                    s_vwd = _clip((z * ratio) / 3.0)
                    components["vol_ratio"] = round(ratio, 2)
                    reasons.append(f"V@VWAP/V@px ratio {ratio:.2f}")
            except (TypeError, ValueError, ZeroDivisionError):
                pass

    components.update({
        "sigma":        round(s_sigma, 2),
        "regime_score": round(s_regime, 2),
        "rth_eth":      round(s_div, 2),
        "vwd":          round(s_vwd, 2),
    })

    w = {"sigma": 0.20, "regime": 0.35, "div": 0.20, "vwd": 0.25}
    score = (w["sigma"]*s_sigma + w["regime"]*s_regime
           + w["div"]*s_div     + w["vwd"]*s_vwd)
    score = _clip(score)

    if   score >  0.25: label = "BULLISH"
    elif score < -0.25: label = "BEARISH"
    else:               label = "NEUTRAL"

    return {
        "score": round(score, 3),
        "label": label,
        "components": components,
        "reasons": reasons,
        "vwap": float(vwap),
        "mid":  mid,
    }


def _detect_hvn_lvn(levels: List[Dict[str, Any]],
                    prominence_ratio: float = 1.5,
                    smooth: int = 3) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Lightweight HVN / LVN detection from a sorted-by-price levels list."""
    if not levels or len(levels) < 5:
        return [], []
    vols = [float(l.get("volume", 0.0)) for l in levels]
    sm: List[float] = []
    for i in range(len(vols)):
        a = max(0, i - smooth)
        b = min(len(vols), i + smooth + 1)
        sm.append(sum(vols[a:b]) / (b - a))
    mean_v = sum(sm) / len(sm)
    var_v  = sum((v - mean_v) ** 2 for v in sm) / max(1, (len(sm) - 1))
    sd_v   = math.sqrt(max(1e-9, var_v))
    hvn, lvn = [], []
    for i in range(1, len(sm) - 1):
        if sm[i] > sm[i-1] and sm[i] > sm[i+1] and (sm[i] - mean_v) > prominence_ratio * sd_v:
            hvn.append({"price": float(levels[i]["price"]),
                        "volume": vols[i],
                        "strength": round((sm[i] - mean_v) / sd_v, 2)})
        if sm[i] < sm[i-1] and sm[i] < sm[i+1] and (mean_v - sm[i]) > prominence_ratio * sd_v:
            lvn.append({"price": float(levels[i]["price"]),
                        "volume": vols[i],
                        "strength": round((mean_v - sm[i]) / sd_v, 2)})
    return hvn, lvn


def compute_vp_bias(snap: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Composite Volume Profile bias in [-1,+1]."""
    vp = snap.get("volume_profile")
    if not vp or vp.get("_error"): return None
    poc = vp.get("vpoc") or vp.get("poc")
    vah = vp.get("vah"); val = vp.get("val")
    if poc is None or vah is None or val is None: return None

    book = snap.get("book") or {}
    mid = book.get("mid")
    if mid is None or (isinstance(mid, float) and math.isnan(mid)):
        v = snap.get("vwap_obj") or {}
        mid = v.get("lastTradePrice")
    try: mid = float(mid)
    except (TypeError, ValueError): return None

    vobj = snap.get("vwap_obj") or {}
    sigma_proxy = vobj.get("stddev") or max((float(vah) - float(val)) / 2.0, 1.0)
    sigma_proxy = float(sigma_proxy) if sigma_proxy else 1.0

    components: Dict[str, Any] = {}
    reasons: List[str] = []

    poc_dist_sigma = (mid - float(poc)) / sigma_proxy
    s_poc = _tanh(poc_dist_sigma / 2.0)
    components["poc_z"] = round(poc_dist_sigma, 2)
    reasons.append(f"POC z {poc_dist_sigma:+.2f}")

    vah_f, val_f = float(vah), float(val)
    va_width = max(vah_f - val_f, 1e-9)
    if mid > vah_f:
        s_va = _clip((mid - vah_f) / va_width)
        va_state = "ABOVE_VAH"
    elif mid < val_f:
        s_va = -_clip((val_f - mid) / va_width)
        va_state = "BELOW_VAL"
    else:
        s_va = 0.3 * ((mid - float(poc)) / va_width)
        s_va = _clip(s_va, -0.4, 0.4)
        va_state = "INSIDE_VA"
    components["va_state"] = va_state
    components["va"]       = round(s_va, 2)
    reasons.append(va_state)

    levels = vp.get("levels") or []
    hvn, lvn = _detect_hvn_lvn(levels)
    s_hvn = 0.0
    nearest_hvn = None
    if hvn:
        candidates = sorted(hvn, key=lambda h: abs(h["price"] - mid))[:3]
        best = None
        for h in candidates:
            if abs(h["price"] - mid) <= 4.0 * sigma_proxy and (best is None or h["strength"] > best["strength"]):
                best = h
        if best:
            direction = 1.0 if best["price"] < mid else -1.0
            decay = math.exp(-abs(best["price"] - mid) / max(sigma_proxy * 2.0, 1.0))
            s_hvn = _clip(direction * decay * (best["strength"] / 3.0))
            nearest_hvn = best
            reasons.append(f"HVN @ {best['price']:.2f} str {best['strength']:.1f} {'below' if direction>0 else 'above'}")
    components["hvn"] = round(s_hvn, 2)
    components["nearest_hvn"]  = nearest_hvn
    components["hvn_count"]    = len(hvn)
    components["lvn_count"]    = len(lvn)

    w = {"poc": 0.25, "va": 0.45, "hvn": 0.30}
    score = w["poc"]*s_poc + w["va"]*s_va + w["hvn"]*s_hvn

    # Tier 2: Steidlmayer day-type multiplier from /momentum.ib.dayType
    flow = snap.get("flow") or {}
    ib   = flow.get("ib") or {}
    day_type = ib.get("dayType") or "UNKNOWN"
    ib_size  = ib.get("ibSizeTag") or "UNKNOWN"
    gate_map = {"TREND": 1.2, "NORMAL_VAR": 1.0, "NORMAL": 0.9,
                "NEUTRAL": 0.6, "NON_TREND": 0.4, "UNKNOWN": 1.0}
    gate = gate_map.get(day_type, 1.0)
    score = _clip(score * gate)
    components["day_type"] = day_type
    components["ib_size"]  = ib_size
    components["gate"]     = round(gate, 2)
    if day_type != "UNKNOWN":
        reasons.append(f"day {day_type} × {gate:.2f}")
    if ib_size in ("NARROW", "WIDE"):
        reasons.append(f"IB {ib_size}")

    if   score >  0.25: label = "BULLISH"
    elif score < -0.25: label = "BEARISH"
    else:               label = "NEUTRAL"

    return {
        "score": round(score, 3),
        "label": label,
        "components": components,
        "reasons": reasons,
        "poc": float(poc), "vah": vah_f, "val": val_f,
        "mid": mid,
        "hvn": hvn[:5],
        "lvn": lvn[:5],
    }


def _demote_conf(conf: str) -> str:
    return {"HIGH": "MEDIUM", "MEDIUM": "LOW", "LOW": "LOW"}.get(conf, "LOW")


def vwap_or_gate(book: Dict, vwap: Optional[float], or_row: Optional[Dict]) -> Dict[str, Any]:
    if not or_row or vwap is None:
        return {"state": "UNKNOWN", "reason": "missing OR row or VWAP"}
    try:
        or_high = float(or_row.get("orHigh", "nan"))
        or_low = float(or_row.get("orLow", "nan"))
    except Exception:
        return {"state": "UNKNOWN", "reason": "or_row parse error"}
    mid = book.get("mid") if book else None
    if mid is None or (isinstance(mid, float) and math.isnan(mid)):
        return {"state": "UNKNOWN", "reason": "no live mid"}
    if math.isnan(or_high) or math.isnan(or_low):
        return {"state": "UNKNOWN", "reason": "no OR range yet"}
    or_center = (or_high + or_low) / 2.0
    if or_center > vwap and mid >= vwap:
        return {"state": "ALLOW_LONG", "reason": f"OR {or_center:.2f} > VWAP {vwap:.2f}, mid {mid:.2f} >= VWAP"}
    if or_center < vwap and mid <= vwap:
        return {"state": "ALLOW_SHORT", "reason": f"OR {or_center:.2f} < VWAP {vwap:.2f}, mid {mid:.2f} <= VWAP"}
    return {"state": "BLOCKED", "reason": f"OR {or_center:.2f}, VWAP {vwap:.2f}, mid {mid:.2f}"}


# ─────────────────────────────────────────────────────────────────────────────
# Phase A: Session Conviction Accumulator
#
# Starts at 0 at 08:30 CT every day. On each poll, integrates the latest
# values of regime / bias / VWAP slope / level reaction / VP day-type into a
# slow-moving conviction number in [-1, +1]. Each component has its own time
# decay (fast for microstructure, slow for day-type), so the score is robust
# to noise but responsive to actual institutional flow.
#
# Outputs a single number you can watch all day: when it's been rising for 30
# min, you're following a real accumulation. When it whipsaws, the day is
# chop — stand down. This is the "follow $" trend signal.
#
# State is per-alias (one accumulator per attached instrument). Resets at
# 08:30 CT every day automatically.
# ─────────────────────────────────────────────────────────────────────────────

# Component weights — tuned in Phase D from the outcome tracker. Hot-reloaded
# from pax_weights.json (next to this file). Edit that file + restart to update.

_PAX_WEIGHTS_PATH = Path(__file__).parent / "pax_weights.json"
_PAX_WEIGHTS_CACHE: Dict[str, Any] = {}
_PAX_WEIGHTS_MTIME: float = 0.0

def _strip_meta(d: Any) -> Any:
    """Recursively strip JSON metadata keys (those starting with '_') from a config
    object so they can't leak into the math. The conviction weight/halflife loops
    iterate over keys, and a stray '_comment' entry would crash with KeyError on
    the EMA lookup. JSON spec doesn't allow comments, so we conventionally embed
    them as '_comment' keys — those must be filtered on load.
    """
    if isinstance(d, dict):
        return {k: _strip_meta(v) for k, v in d.items() if not (isinstance(k, str) and k.startswith("_"))}
    if isinstance(d, list):
        return [_strip_meta(x) for x in d]
    return d


def _load_pax_weights() -> Dict[str, Any]:
    """Lazy hot-reload of pax_weights.json. Falls back to defaults on any error.

    Strips '_comment'-style metadata keys at every level so they cannot leak
    into the conviction math (the weight-dict iteration would otherwise raise
    KeyError on st['ema']['_comment']).
    """
    global _PAX_WEIGHTS_CACHE, _PAX_WEIGHTS_MTIME
    defaults = {
        "conviction_weights": {"regime":0.25,"bias":0.15,"vwap":0.15,"vp":0.15,
                                "slope":0.15,"level":0.15,"ib":0.00},
        "conviction_halflife_sec": {"regime":120,"bias":120,"vwap":240,"vp":360,
                                     "slope":180,"level":90,"ib":7200},
        "regime_boost": {},
        "confidence_thresholds": {"floor":0.35,"full":0.50,"half":0.35},
        "subgroup_overrides": {},
    }
    try:
        mt = _PAX_WEIGHTS_PATH.stat().st_mtime
        if mt != _PAX_WEIGHTS_MTIME or not _PAX_WEIGHTS_CACHE:
            with open(_PAX_WEIGHTS_PATH, "r", encoding="utf-8") as fh:
                loaded = _strip_meta(json.load(fh))
            for k, v in defaults.items(): loaded.setdefault(k, v)
            _PAX_WEIGHTS_CACHE = loaded
            _PAX_WEIGHTS_MTIME = mt
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        if not _PAX_WEIGHTS_CACHE: _PAX_WEIGHTS_CACHE = defaults
    return _PAX_WEIGHTS_CACHE

# Legacy EMA-model constants — kept for backward compatibility with the
# helper-signal unit tests (_regime_to_signal / _slope_to_signal / _level_to_signal
# sign conventions are still used by the v2 engine's `regime`, `vwap_slope`, and
# `level_reaction` sources). No live code path reads these dicts directly; the
# v2 engine reads `conviction_source_weights` / `conviction_cluster_caps` from
# pax_weights.json instead.
CONVICTION_WEIGHTS = {"regime":0.25,"bias":0.15,"vwap":0.15,"vp":0.15,
                      "slope":0.15,"level":0.15,"ib":0.00}
CONVICTION_HALFLIFE_SEC = {"regime":120,"bias":120,"vwap":240,"vp":360,
                           "slope":180,"level":90,"ib":7200}

# ─── v2 conviction engine — anchored multi-source ───────────────────────────
#
# Replaces the EMA-over-7-labels model. Per source we maintain a per-alias
# ring of (ts_ms, instantaneous_value) bounded by the medium window, plus a
# session-anchored running sum/count. The per-source score blends a short
# rolling SMA, a medium rolling SMA, and the session SMA. Each source declares
# a reliability in [0,1] derived from availability, sample count, freshness,
# and a source-specific regime gate. Effective weight = base × reliability,
# scaled down inside correlation clusters that exceed their cap.
#
# Composite score = Σ(eff_w · src_score) / Σ|eff_w|, clipped to [-1,+1].
# Trajectory = SMA slope of the composite over the last 30s vs 30-90s.
CONVICTION_METHOD_VERSION = "anchored_multi_source_v2"

CONVICTION_SOURCE_WEIGHTS = {
    "flow_ofi":                    0.14,
    "flow_cvd":                    0.12,
    "flow_vpt_absorption":         0.08,
    "regime":                      0.08,
    "bias_score":                  0.08,
    "vwap_dislocation":            0.08,
    "vwap_slope":                  0.08,
    "volume_profile":              0.08,
    "pull_stack":                  0.10,
    "tape_large_lot":              0.06,
    "lt_liquidity":                0.04,
    "micro_events":                0.04,
    "level_reaction":              0.08,
    "anchored_vwap_opening_drive": 0.08,
    "ib_context":                  0.04,
}

CONVICTION_CLUSTERS = {
    "flow":           ["flow_ofi", "flow_cvd", "bias_score", "regime"],
    "vwap":           ["vwap_dislocation", "vwap_slope", "anchored_vwap_opening_drive"],
    "structure":      ["volume_profile", "ib_context"],
    "microstructure": ["pull_stack", "tape_large_lot", "lt_liquidity", "micro_events"],
}

CONVICTION_CLUSTER_CAPS = {
    "flow":           0.35,
    "vwap":           0.25,
    "structure":      0.20,
    "microstructure": 0.30,
}

CONVICTION_WINDOWS_SEC = {
    "short":                  30,
    "medium":                 120,
    "freshness_halflife_sec": 8,
    "required_samples":       10,
}

CONVICTION_AGG_WEIGHTS = {"sma_medium": 0.50, "sma_short": 0.30, "sma_session": 0.20}

CONVICTION_THRESHOLDS = {
    "trend":             0.35,
    "lean":              0.18,
    "chop":              0.10,
    "trajectory_strong": 0.08,
    "trajectory_normal": 0.02,
    "min_total_weight":  0.05,
}

# Maps the 7 legacy component keys (still consumed by dashboard.js) to the v2
# source names that supersede them. Used to populate `components` and
# `instantaneous` blocks in the output for backward compatibility.
_CONVICTION_LEGACY_KEY_MAP = {
    "regime": "regime",
    "bias":   "bias_score",
    "vwap":   "vwap_dislocation",
    "vp":     "volume_profile",
    "slope":  "vwap_slope",
    "level":  "level_reaction",
    "ib":     "ib_context",
}

_CONVICTION_STATE: Dict[str, Dict[str, Any]] = {}


def _rolling_sma(ring: List[Tuple[int, float]], now_ms: int, window_sec: float
                 ) -> Tuple[float, int]:
    """Mean of values whose timestamp is within `window_sec` of `now_ms`.

    Returns (mean, count). count==0 → mean==0.0.
    """
    cutoff = now_ms - int(window_sec * 1000)
    s, n = 0.0, 0
    for ts, v in ring:
        if ts >= cutoff:
            s += v
            n += 1
    return (s / n if n else 0.0, n)


def _prune_ring(ring: List[Tuple[int, float]], now_ms: int, window_sec: float
                ) -> List[Tuple[int, float]]:
    cutoff = now_ms - int(window_sec * 1000)
    return [(ts, v) for ts, v in ring if ts >= cutoff]


def _conv_session_anchor(now_et: dt.datetime) -> Tuple[int, dt.datetime]:
    """Return the most recent 08:30 CT anchor before now_et."""
    now_ct = now_et.astimezone(DISPLAY_TZ)
    anchor = now_ct.replace(hour=8, minute=30, second=0, microsecond=0)
    if now_ct < anchor:
        anchor = anchor - dt.timedelta(days=1)
    return int(anchor.timestamp() * 1000), anchor


def _regime_to_signal(regime: str, confidence: float) -> float:
    """Map regime label to a directional contribution in [-1,+1] with magnitude scaled by confidence."""
    base = {
        "TRENDING_UP":     +0.9,
        "TRENDING_DOWN":   -0.9,
        "ABSORPTION_BID":  +0.7,    # absorption = forming the floor, bullish
        "ABSORPTION_ASK":  -0.7,
        "EXHAUSTION_UP":   -0.5,    # up move out of steam — slight bearish bias
        "EXHAUSTION_DOWN": +0.5,
        "BALANCED":         0.0,
        "QUIET":            0.0,
        "WARMUP":           0.0,
    }
    return base.get(regime, 0.0) * max(0.2, min(1.0, confidence or 0.0))


def _slope_to_signal(label: str, slope_z: float) -> float:
    base = {"STRONG_UP": 0.9, "RISING": 0.5, "FLAT": 0.0,
            "FALLING": -0.5, "STRONG_DOWN": -0.9, "WARMUP": 0.0}.get(label, 0.0)
    # Modulate by z-score magnitude so we don't double-count
    return _clip(base * (0.5 + 0.5 * min(1.0, abs(slope_z or 0.0))))


def _level_to_signal(or_levels: Optional[Dict[str, Any]]) -> float:
    """Proximity-weighted bias from the OR level reaction model."""
    if not or_levels: return 0.0
    levels = or_levels.get("levels") or []
    contrib = 0.0
    for l in levels:
        if not l.get("proximity"): continue
        d = l.get("decision") or "WAIT"
        score = float(l.get("confidence") or 0.0)
        if "LONG" in d:  contrib += score
        if "SHORT" in d: contrib -= score
    return _clip(contrib)


# ─── v2 source helpers ──────────────────────────────────────────────────────
# Every helper accepts the live snapshot and returns:
#   {"score": float in [-1,+1], "reliability": float in [0,1],
#    "raw": {…},  "reason": str}
#
# Sources should never raise. If their fields are missing, return
# reliability=0 with score=0 so they drop cleanly out of the composite.


def _as_float(x: Any, default: float = 0.0) -> Tuple[float, bool]:
    """Tolerant float coercion. Returns (value, ok)."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return default, False
    if math.isnan(f) or math.isinf(f):
        return default, False
    return f, True


def _source_flow_ofi(snap: Dict[str, Any]) -> Dict[str, Any]:
    flow = snap.get("flow") or {}
    ofiz = flow.get("ofiZ")
    if ofiz is None:
        ofi, ok = _as_float(flow.get("ofi"))
        if not ok:
            return {"score": 0.0, "reliability": 0.0, "raw": {"ofiZ": None}, "reason": "no OFI"}
        return {"score": _clip(_tanh(ofi / 1000.0)), "reliability": 0.5,
                "raw": {"ofi": ofi}, "reason": f"OFI={ofi:+.0f} (no z)"}
    z, ok = _as_float(ofiz)
    if not ok:
        return {"score": 0.0, "reliability": 0.0, "raw": {"ofiZ": ofiz}, "reason": "OFI parse err"}
    return {"score": _clip(_tanh(z / 2.0)), "reliability": 1.0,
            "raw": {"ofiZ": z}, "reason": f"OFI z={z:+.2f}"}


def _source_flow_cvd(snap: Dict[str, Any]) -> Dict[str, Any]:
    flow = snap.get("flow") or {}
    cvdz = flow.get("cvdDeltaZ")
    if cvdz is None:
        return {"score": 0.0, "reliability": 0.0, "raw": {"cvdDeltaZ": None}, "reason": "no CVD"}
    z, ok = _as_float(cvdz)
    if not ok:
        return {"score": 0.0, "reliability": 0.0, "raw": {"cvdDeltaZ": cvdz}, "reason": "CVD parse err"}
    return {"score": _clip(_tanh(z / 2.0)), "reliability": 1.0,
            "raw": {"cvdDeltaZ": z}, "reason": f"CVD z={z:+.2f}"}


def _source_flow_vpt_absorption(snap: Dict[str, Any]) -> Dict[str, Any]:
    flow = snap.get("flow") or {}
    regime = (flow.get("regime") or "").upper()
    vptz, vpt_ok = _as_float(flow.get("vptZ"))
    bias, bias_ok = _as_float(flow.get("biasScore"))
    mag = _clip(_tanh(abs(vptz) / 2.0)) if vpt_ok else 0.0
    if regime == "ABSORPTION_BID":
        return {"score": _clip(0.4 + 0.6 * mag), "reliability": 1.0,
                "raw": {"vptZ": vptz, "regime": regime},
                "reason": f"ABS_BID vptZ={vptz:+.2f}"}
    if regime == "ABSORPTION_ASK":
        return {"score": -_clip(0.4 + 0.6 * mag), "reliability": 1.0,
                "raw": {"vptZ": vptz, "regime": regime},
                "reason": f"ABS_ASK vptZ={vptz:+.2f}"}
    # Non-absorption regime: small, signed contribution from biasScore × |vptZ|
    if vpt_ok and bias_ok and abs(bias) > 0.05:
        sign = 1.0 if bias > 0 else -1.0
        return {"score": _clip(sign * 0.35 * mag), "reliability": 0.55,
                "raw": {"vptZ": vptz, "biasScore": bias, "regime": regime},
                "reason": f"vptZ={vptz:+.2f} bias={bias:+.2f}"}
    if vpt_ok:
        return {"score": 0.0, "reliability": 0.30,
                "raw": {"vptZ": vptz, "regime": regime},
                "reason": f"vptZ={vptz:+.2f} no direction"}
    return {"score": 0.0, "reliability": 0.0, "raw": {}, "reason": "no VPT data"}


def _source_regime(snap: Dict[str, Any]) -> Dict[str, Any]:
    flow = snap.get("flow") or {}
    regime = (flow.get("regime") or "WARMUP").upper()
    conf, conf_ok = _as_float(flow.get("regimeConfidence"))
    if regime in ("WARMUP", ""):
        return {"score": 0.0, "reliability": 0.0,
                "raw": {"regime": regime}, "reason": "regime warmup"}
    score = _regime_to_signal(regime, conf if conf_ok else 0.0)
    # Reliability gated: BALANCED / QUIET carry less weight than active regimes.
    if regime in ("BALANCED", "QUIET"):
        reliability = 0.4
    else:
        reliability = max(0.3, min(1.0, conf if conf_ok else 0.5))
    return {"score": _clip(score), "reliability": reliability,
            "raw": {"regime": regime, "conf": conf},
            "reason": f"{regime} conf={conf:.2f}" if conf_ok else regime}


# V10: bias_score trajectory cache. Same shape as V9's level_reaction cache.
# bias_score rolls every 30s when FlowRegime closes a window, so most polls
# see delta=0 and trajectory FLAT — meaningful trajectory fires precisely
# when the FlowRegime window rolls with a different bias.
_LAST_BIAS_SCORE: Dict[str, Tuple[float, float]] = {}
_BIAS_SCORE_TRAJ_NUDGE = {
    "RISING_STRONG":  +0.10,
    "RISING":         +0.05,
    "FLAT":            0.00,
    "FALLING":        -0.05,
    "FALLING_STRONG": -0.10,
}
_BIAS_SCORE_TRAJ_STRONG_DELTA = 0.10
_BIAS_SCORE_TRAJ_NORMAL_DELTA = 0.03
_BIAS_SCORE_MAX_DT_SEC = 60.0


def _source_bias_score(snap: Dict[str, Any]) -> Dict[str, Any]:
    flow = snap.get("flow") or {}
    bs, ok = _as_float(flow.get("biasScore"))
    if not ok:
        return {"score": 0.0, "reliability": 0.0,
                "raw": {"biasScore": flow.get("biasScore")}, "reason": "no biasScore"}

    raw_score = _clip(bs)
    reliability = 1.0

    # ----- trajectory awareness (V10) -----
    alias = snap.get("alias")
    now_ts = time.monotonic()
    traj = "FLAT"
    delta = 0.0
    if alias:
        prev = _LAST_BIAS_SCORE.get(alias)
        if prev is not None:
            prev_score, prev_ts = prev
            dt_sec = now_ts - prev_ts
            if 0 < dt_sec <= _BIAS_SCORE_MAX_DT_SEC:
                delta = raw_score - prev_score
                a = abs(delta)
                if a > _BIAS_SCORE_TRAJ_STRONG_DELTA:
                    traj = "RISING_STRONG" if delta > 0 else "FALLING_STRONG"
                elif a > _BIAS_SCORE_TRAJ_NORMAL_DELTA:
                    traj = "RISING" if delta > 0 else "FALLING"
        _LAST_BIAS_SCORE[alias] = (raw_score, now_ts)

    nudge = _BIAS_SCORE_TRAJ_NUDGE.get(traj, 0.0)
    final_score = _clip(raw_score + nudge)

    score_sign = 1 if raw_score > 0.05 else (-1 if raw_score < -0.05 else 0)
    traj_sign = (1 if traj in ("RISING", "RISING_STRONG")
                  else -1 if traj in ("FALLING", "FALLING_STRONG")
                  else 0)
    diverged = (score_sign != 0 and traj_sign != 0 and score_sign != traj_sign)
    if diverged:
        if traj in ("RISING_STRONG", "FALLING_STRONG"):
            reliability *= 0.5
        else:
            reliability *= 0.75

    reason = f"bias={bs:+.2f} traj={traj}"
    if abs(nudge) > 0:
        reason += f" nudge={nudge:+.2f}"
    if diverged:
        reason += " (DIVERGED)"

    return {"score": final_score,
            "reliability": reliability,
            "raw": {"biasScore": bs, "trajectory": traj,
                    "delta": round(delta, 3)},
            "reason": reason}


def _source_vwap_dislocation(snap: Dict[str, Any]) -> Dict[str, Any]:
    vobj = snap.get("vwap_obj") or {}
    if not vobj or "_error" in vobj:
        return {"score": 0.0, "reliability": 0.0, "raw": {}, "reason": "no vwap_obj"}
    vwap, vwap_ok = _as_float(vobj.get("vwap"))
    stddev, sd_ok = _as_float(vobj.get("stddev"))
    last, last_ok = _as_float(vobj.get("lastTradePrice"))
    book = snap.get("book") or {}
    mid, mid_ok = _as_float(book.get("mid"), default=last)
    if not mid_ok and last_ok:
        mid, mid_ok = last, True
    if not (vwap_ok and sd_ok and mid_ok) or stddev <= 0:
        return {"score": 0.0, "reliability": 0.0, "raw": {}, "reason": "vwap fields missing"}
    z = (mid - vwap) / stddev
    az = abs(z)
    flow = snap.get("flow") or {}
    slope_lbl = (flow.get("vwapSlope") or {}).get("label", "")
    slope_z, _ = _as_float((flow.get("vwapSlope") or {}).get("slopeZ"))
    sign = 1.0 if z > 0 else -1.0
    if az < 1.0:
        score = _clip(z / 1.5)
        reason = f"σ={z:+.2f} INSIDE"
    elif az < 2.0:
        trend_aligned = (z > 0 and slope_lbl in ("STRONG_UP", "RISING")) \
                     or (z < 0 and slope_lbl in ("STRONG_DOWN", "FALLING"))
        if trend_aligned:
            score = 0.4 * sign
            reason = f"σ={z:+.2f} TREND-CONFIRMED"
        else:
            score = -0.5 * sign
            reason = f"σ={z:+.2f} MEAN_REVERT"
    else:
        strong_trend = (z > 0 and slope_z > 1.5) or (z < 0 and slope_z < -1.5)
        if strong_trend:
            score = 0.3 * sign
            reason = f"σ={z:+.2f} STRONG-TREND"
        else:
            score = -0.8 * sign
            reason = f"σ={z:+.2f} EXHAUSTION"
    return {"score": _clip(score), "reliability": 1.0,
            "raw": {"sigma_z": z, "slope": slope_lbl}, "reason": reason}


def _source_vwap_slope(snap: Dict[str, Any]) -> Dict[str, Any]:
    flow = snap.get("flow") or {}
    sl = flow.get("vwapSlope") or {}
    label = sl.get("label") or "WARMUP"
    slope_z, ok = _as_float(sl.get("slopeZ"))
    if label == "WARMUP":
        return {"score": 0.0, "reliability": 0.0,
                "raw": {"label": label}, "reason": "slope warmup"}
    score = _slope_to_signal(label, slope_z if ok else 0.0)
    return {"score": _clip(score), "reliability": 1.0,
            "raw": {"label": label, "slopeZ": slope_z},
            "reason": f"slope {label} z={slope_z:+.2f}" if ok else f"slope {label}"}


def _source_volume_profile(snap: Dict[str, Any]) -> Dict[str, Any]:
    vp = snap.get("vp_bias") or {}
    if not vp or "_error" in vp:
        return {"score": 0.0, "reliability": 0.0, "raw": {}, "reason": "no vp_bias"}
    s, ok = _as_float(vp.get("score"))
    if not ok:
        return {"score": 0.0, "reliability": 0.0, "raw": {}, "reason": "vp score missing"}
    return {"score": _clip(s), "reliability": 1.0,
            "raw": {"score": s, "label": vp.get("label")},
            "reason": f"vp={s:+.2f}"}


def _source_pull_stack(snap: Dict[str, Any]) -> Dict[str, Any]:
    ps = snap.get("pull_stack") or {}
    if not ps or "_error" in ps:
        return {"score": 0.0, "reliability": 0.0, "raw": {}, "reason": "no pull_stack"}
    aggz, ok = _as_float(ps.get("aggregateZ"))
    rotation = (ps.get("rotation") or "NONE").upper()
    if not ok and rotation == "NONE":
        return {"score": 0.0, "reliability": 0.0, "raw": ps,
                "reason": "no aggregateZ / rotation"}
    base = _tanh(aggz / 2.0) if ok else 0.0
    if rotation == "ROTATION_UP":
        score = _clip(base + 0.20)
    elif rotation == "ROTATION_DN":
        score = _clip(base - 0.20)
    else:
        score = _clip(base)
    return {"score": score, "reliability": 1.0,
            "raw": {"aggregateZ": aggz, "rotation": rotation},
            "reason": f"aggZ={aggz:+.2f} rot={rotation}"}


def _source_tape_large_lot(snap: Dict[str, Any]) -> Dict[str, Any]:
    """Institutional tape-flow source. Primary read is `snap['tape_flow']`
    produced by `compute_tape_flow`. Falls back to recomputing from the raw
    /tape_buckets payload if tape_flow is absent (cold start, error path)."""
    tf = snap.get("tape_flow")
    if isinstance(tf, dict) and "_error" not in tf and "deltaScore" in tf:
        return _tape_source_from_flow(tf, fallback=False)
    recomputed = compute_tape_flow(snap)
    if recomputed is None:
        return {"score": 0.0, "reliability": 0.0, "raw": {},
                "reason": "no tape_buckets"}
    return _tape_source_from_flow(recomputed, fallback=True)


def _tape_source_from_flow(tf: Dict[str, Any], *, fallback: bool) -> Dict[str, Any]:
    score, _ = _as_float(tf.get("deltaScore"))
    label = tf.get("deltaLabel", "")
    lp30, _ = _as_float(tf.get("largePrints30s"))
    tp30, _ = _as_float(tf.get("totalPrints30s"))
    if label == "THIN":
        return {"score": 0.0, "reliability": 0.05,
                "raw": {"deltaLabel": label, "largePrints30s": lp30,
                        "totalPrints30s": tp30, "fallback": fallback},
                "reason": tf.get("deltaReason", "thin tape")}
    if lp30 >= 4:
        rel = lp30 / 12.0
        if fallback:
            rel = min(rel, 0.7)
        reliability = _clip(rel, 0.0, 1.0)
    elif tp30 >= 10:
        reliability = _clip(tp30 / 40.0, 0.0, 0.6)
    else:
        reliability = 0.1
    return {"score": _clip(score),
            "reliability": reliability,
            "raw": {"deltaScore": score, "deltaLabel": label,
                    "largePrints30s": lp30, "totalPrints30s": tp30,
                    "fallback": fallback},
            "reason": tf.get("deltaReason", f"tape delta={score:+.2f}")}


def _source_vwap_or_gate(snap: Dict[str, Any]) -> Dict[str, Any]:
    """Directional gate from the VWAP/OR alignment check. Reads
    snap['gates']['vwap_or'] computed by vwap_or_gate(): the gate fires
    ALLOW_LONG when OR center > session VWAP AND mid >= VWAP, ALLOW_SHORT
    on the symmetric short setup, BLOCKED when they disagree, UNKNOWN when
    inputs are missing.

    Used here as a context prior — a coarse directional sanity check on
    the day's regime, not a fast signal. Score magnitude is fixed (±0.5)
    so it can never dominate continuous signals; cluster cap further
    normalizes against the other vwap sources.
    """
    gates = snap.get("gates")
    if not isinstance(gates, dict):
        return {"score": 0.0, "reliability": 0.0,
                "raw": {}, "reason": "no gates"}
    gate = gates.get("vwap_or")
    if not isinstance(gate, dict):
        return {"score": 0.0, "reliability": 0.0,
                "raw": {"vwap_or": gate}, "reason": "no vwap_or gate"}
    state = (gate.get("state") or "UNKNOWN").upper()
    why = gate.get("reason", "")
    if state == "ALLOW_LONG":
        return {"score": 0.5, "reliability": 1.0,
                "raw": {"state": state, "reason": why},
                "reason": f"vwap_or ALLOW_LONG ({why})"}
    if state == "ALLOW_SHORT":
        return {"score": -0.5, "reliability": 1.0,
                "raw": {"state": state, "reason": why},
                "reason": f"vwap_or ALLOW_SHORT ({why})"}
    if state == "BLOCKED":
        # Gate has a definite read (data present, OR and mid disagree on the
        # VWAP side). Reliability stays at 1.0 — but score is 0, no
        # directional contribution. Communicates "regime is mixed".
        return {"score": 0.0, "reliability": 1.0,
                "raw": {"state": state, "reason": why},
                "reason": f"vwap_or BLOCKED ({why})"}
    # UNKNOWN: gate inputs are missing (no OR, no VWAP, no live mid).
    return {"score": 0.0, "reliability": 0.0,
            "raw": {"state": state, "reason": why},
            "reason": f"vwap_or {state}"}


def _source_orderbook(snap: Dict[str, Any]) -> Dict[str, Any]:
    """Static orderbook depth pressure. Reads bookPressureTop5/25 from the
    /momentum payload — both are (bid_vol - ask_vol) / total in [-1,+1].
    Positive = bid side heavier near BBO → bullish.

    Distinct from `flow_ofi` (Cont/Kukanov/Stoikov event-driven OFI delta)
    and `lt_liquidity` (slow EWMA of resting top-25 liquidity). Captures
    the instantaneous depth posture at the moment of the snapshot.
    """
    flow = snap.get("flow") or {}
    if "_error" in flow:
        return {"score": 0.0, "reliability": 0.0,
                "raw": {"_error": flow.get("_error")},
                "reason": "flow error"}
    p5, p5_ok   = _as_float(flow.get("bookPressureTop5"))
    p25, p25_ok = _as_float(flow.get("bookPressureTop25"))
    if not p5_ok and not p25_ok:
        return {"score": 0.0, "reliability": 0.0,
                "raw": {"bookPressureTop5": None, "bookPressureTop25": None},
                "reason": "no book pressure"}
    if p5_ok and p25_ok:
        # 0.6 fast (top 5) + 0.4 slow (top 25) — close-in matters more for
        # short-horizon direction, but the wider read stabilizes against
        # spoof/flicker right at the inside.
        score = _clip(0.6 * p5 + 0.4 * p25)
        return {"score": score, "reliability": 1.0,
                "raw": {"bookPressureTop5": p5, "bookPressureTop25": p25},
                "reason": f"book p5={p5:+.2f} p25={p25:+.2f}"}
    # Partial data: one band missing.
    score = _clip(p5 if p5_ok else p25)
    return {"score": score, "reliability": 0.5,
            "raw": {"bookPressureTop5":  p5  if p5_ok  else None,
                    "bookPressureTop25": p25 if p25_ok else None},
            "reason": f"book p{'5' if p5_ok else '25'}={score:+.2f} (partial)"}


def _source_lt_liquidity(snap: Dict[str, Any]) -> Dict[str, Any]:
    lt = snap.get("lt_liquidity") or {}
    if not lt or "_error" in lt:
        return {"score": 0.0, "reliability": 0.0, "raw": {}, "reason": "no lt_liquidity"}
    bid, bok = _as_float(lt.get("bidSize"))
    ask, aok = _as_float(lt.get("askSize"))
    total = bid + ask
    if not (bok and aok) or total <= 0:
        return {"score": 0.0, "reliability": 0.0,
                "raw": {"bid": bid, "ask": ask}, "reason": "lt empty"}
    # Bid > ask → support / bullish magnet. Squash so 80/20 ≈ 0.6.
    imb = (bid - ask) / total
    return {"score": _clip(_tanh(imb * 1.5)), "reliability": 0.8,
            "raw": {"bid": bid, "ask": ask, "imb": imb},
            "reason": f"LT bid/ask imb={imb:+.2f}"}


def _source_micro_events(snap: Dict[str, Any]) -> Dict[str, Any]:
    me = snap.get("micro_events") or {}
    if not me or "_error" in me:
        return {"score": 0.0, "reliability": 0.0, "raw": {}, "reason": "no micro_events"}
    events = me.get("events") or []
    if not events:
        return {"score": 0.0, "reliability": 0.2, "raw": {}, "reason": "no events"}
    now_ms = int(dt.datetime.now(ET).timestamp() * 1000)
    score = 0.0
    hits: List[str] = []
    for ev in events[-30:]:
        kind = (ev.get("kind") or ev.get("type") or "").upper()
        is_bid = ev.get("isBid")
        if isinstance(is_bid, bool):
            bid_like = is_bid
            ask_like = not is_bid
        else:
            side = (ev.get("side") or "").upper()
            bid_like = side in ("BID", "BUY")
            ask_like = side in ("ASK", "SELL")
        if not (bid_like or ask_like):
            continue
        # Event-age decay so stale events fall out of the signal.
        ev_ms, ok = _as_float(ev.get("timeMs"))
        if ok and ev_ms > 0:
            age_sec = max(0.0, (now_ms - ev_ms) / 1000.0)
        else:
            age_sec = 0.0
        decay = math.exp(-age_sec / 30.0)
        if kind == "ICEBERG":
            score += (+0.4 if bid_like else -0.4) * decay
            hits.append(f"ICE{'B' if bid_like else 'A'}")
        elif kind == "STOP_SWEEP":
            # Canonical: isBid=True = bids were swept = sell pressure / bearish.
            # Matches _micro_at_level (above) and the browser micro badge.
            score += (-0.5 if bid_like else +0.5) * decay
            hits.append(f"SWEEP{'B' if bid_like else 'A'}")
        elif kind == "SPOOF":
            # Spoof is contrarian: bid spoof bearish, ask spoof bullish.
            score += (-0.3 if bid_like else +0.3) * decay
            hits.append(f"SPOOF{'B' if bid_like else 'A'}")
    return {"score": _clip(score), "reliability": min(1.0, len(events) / 5.0),
            "raw": {"event_count": len(events), "hits": hits},
            "reason": ",".join(hits[:5]) if hits else "no signed events"}


# V9: level_reaction trajectory cache. Tracks the source's own score delta
# poll-over-poll so the registry contribution gets the same nudge/divergence
# treatment as the conviction slow prior in V8.
_LAST_LEVEL_REACTION: Dict[str, Tuple[float, float]] = {}     # alias → (score, monotonic_ts)
_LEVEL_REACTION_TRAJ_NUDGE = {
    "RISING_STRONG":  +0.10,
    "RISING":         +0.05,
    "FLAT":            0.00,
    "FALLING":        -0.05,
    "FALLING_STRONG": -0.10,
}
_LEVEL_REACTION_TRAJ_STRONG_DELTA = 0.10
_LEVEL_REACTION_TRAJ_NORMAL_DELTA = 0.03
_LEVEL_REACTION_MAX_DT_SEC = 60.0


def _source_level_reaction(snap: Dict[str, Any]) -> Dict[str, Any]:
    or_levels = snap.get("or_levels")
    if not or_levels or (isinstance(or_levels, dict) and "_error" in or_levels):
        return {"score": 0.0, "reliability": 0.0, "raw": {}, "reason": "no or_levels"}
    raw_score = _level_to_signal(or_levels)
    in_prox = bool(or_levels.get("inProximity"))
    # Without proximity the level model has no edge — keep reliability low.
    reliability = 1.0 if in_prox else 0.3

    # ----- trajectory awareness (V9) -----
    alias = snap.get("alias")
    now_ts = time.monotonic()
    traj = "FLAT"
    delta = 0.0
    if alias:
        prev = _LAST_LEVEL_REACTION.get(alias)
        if prev is not None:
            prev_score, prev_ts = prev
            dt_sec = now_ts - prev_ts
            if 0 < dt_sec <= _LEVEL_REACTION_MAX_DT_SEC:
                delta = raw_score - prev_score
                a = abs(delta)
                if a > _LEVEL_REACTION_TRAJ_STRONG_DELTA:
                    traj = "RISING_STRONG" if delta > 0 else "FALLING_STRONG"
                elif a > _LEVEL_REACTION_TRAJ_NORMAL_DELTA:
                    traj = "RISING" if delta > 0 else "FALLING"
        _LAST_LEVEL_REACTION[alias] = (raw_score, now_ts)

    nudge = _LEVEL_REACTION_TRAJ_NUDGE.get(traj, 0.0)
    final_score = _clip(raw_score + nudge)

    # Divergence: raw score sign vs trajectory sign.
    score_sign = 1 if raw_score > 0.05 else (-1 if raw_score < -0.05 else 0)
    traj_sign = (1 if traj in ("RISING", "RISING_STRONG")
                  else -1 if traj in ("FALLING", "FALLING_STRONG")
                  else 0)
    diverged = (score_sign != 0 and traj_sign != 0 and score_sign != traj_sign)
    if diverged:
        if traj in ("RISING_STRONG", "FALLING_STRONG"):
            reliability *= 0.5
        else:
            reliability *= 0.75

    reason = f"level reaction={raw_score:+.2f} prox={in_prox} traj={traj}"
    if abs(nudge) > 0:
        reason += f" nudge={nudge:+.2f}"
    if diverged:
        reason += " (DIVERGED)"

    return {"score": final_score,
            "reliability": reliability,
            "raw": {"inProximity": in_prox,
                    "middleLock": or_levels.get("middleLock"),
                    "trajectory": traj,
                    "delta": round(delta, 3),
                    "raw_score": round(raw_score, 3)},
            "reason": reason}


def _source_anchored_vwap_opening_drive(snap: Dict[str, Any]) -> Dict[str, Any]:
    """Opening-drive anchored VWAP. The /momentum payload exposed by the bridge
    does NOT currently produce an avwap block, so this source always returns
    reliability=0. Wired here so it activates the moment the bridge ships the
    field — no code change needed downstream.
    """
    flow = snap.get("flow") or {}
    avwap = flow.get("avwap")
    if not isinstance(avwap, dict):
        return {"score": 0.0, "reliability": 0.0,
                "raw": {"avwap": None}, "reason": "no avwap"}
    top, t_ok = _as_float(avwap.get("topVwap"))
    bot, b_ok = _as_float(avwap.get("botVwap"))
    dhi, dh_ok = _as_float(avwap.get("driveHigh"))
    dlo, dl_ok = _as_float(avwap.get("driveLow"))
    book = snap.get("book") or {}
    mid, m_ok = _as_float(book.get("mid"))
    if not (t_ok and b_ok and m_ok):
        return {"score": 0.0, "reliability": 0.0,
                "raw": dict(avwap), "reason": "avwap fields missing"}
    span = max(0.5, (dhi - dlo) if (dh_ok and dl_ok and dhi > dlo) else (top - bot))
    if mid > top:
        score = _clip((mid - top) / span)
        reason = f"mid {mid:.2f} > topAVWAP {top:.2f}"
    elif mid < bot:
        score = -_clip((bot - mid) / span)
        reason = f"mid {mid:.2f} < botAVWAP {bot:.2f}"
    else:
        score = 0.0
        reason = "inside avwap channel"
    return {"score": _clip(score), "reliability": 1.0,
            "raw": {"topVwap": top, "botVwap": bot, "mid": mid}, "reason": reason}


def _source_ib_context(snap: Dict[str, Any]) -> Dict[str, Any]:
    """IB-breakout context. The /momentum payload exposes ib.dayType / ibSizeTag
    but no live IB-high/low or break-direction field. We treat this as a context
    flag with no usable sign until IB break direction is plumbed through.
    """
    flow = snap.get("flow") or {}
    ib = flow.get("ib") or {}
    if not ib:
        return {"score": 0.0, "reliability": 0.0, "raw": {}, "reason": "no ib"}
    ib_high, hok = _as_float(ib.get("high"))
    ib_low, lok = _as_float(ib.get("low"))
    complete = ib.get("complete")
    book = snap.get("book") or {}
    mid, mok = _as_float(book.get("mid"))
    if hok and lok and mok and ib_high > ib_low and complete:
        if mid > ib_high:
            return {"score": _clip((mid - ib_high) / max(1.0, ib_high - ib_low)),
                    "reliability": 1.0,
                    "raw": {"ib_high": ib_high, "mid": mid},
                    "reason": f"mid {mid:.2f} > IB-H {ib_high:.2f}"}
        if mid < ib_low:
            return {"score": -_clip((ib_low - mid) / max(1.0, ib_high - ib_low)),
                    "reliability": 1.0,
                    "raw": {"ib_low": ib_low, "mid": mid},
                    "reason": f"mid {mid:.2f} < IB-L {ib_low:.2f}"}
        return {"score": 0.0, "reliability": 0.6,
                "raw": {"ib_high": ib_high, "ib_low": ib_low, "mid": mid},
                "reason": "inside IB range"}
    return {"score": 0.0, "reliability": 0.0,
            "raw": {"dayType": ib.get("dayType")}, "reason": "ib direction unknown"}


_CONVICTION_SOURCES: Dict[str, Any] = {
    "flow_ofi":                    _source_flow_ofi,
    "flow_cvd":                    _source_flow_cvd,
    "flow_vpt_absorption":         _source_flow_vpt_absorption,
    "regime":                      _source_regime,
    "bias_score":                  _source_bias_score,
    "vwap_dislocation":            _source_vwap_dislocation,
    "vwap_slope":                  _source_vwap_slope,
    "vwap_or_gate":                _source_vwap_or_gate,
    "volume_profile":              _source_volume_profile,
    "pull_stack":                  _source_pull_stack,
    "tape_large_lot":              _source_tape_large_lot,
    "orderbook":                   _source_orderbook,
    "lt_liquidity":                _source_lt_liquidity,
    "micro_events":                _source_micro_events,
    "level_reaction":              _source_level_reaction,
    "anchored_vwap_opening_drive": _source_anchored_vwap_opening_drive,
    "ib_context":                  _source_ib_context,
}


def _conv_init_source_state(now_ms: int) -> Dict[str, Any]:
    return {
        "ring":           [],   # [(ts_ms, value), ...] bounded by medium window
        "sessionSum":     0.0,
        "sessionCount":   0,
        "lastValue":      0.0,
        "lastUpdateMs":   now_ms,
        "available":      False,
        "reliability":    0.0,
    }


def _conv_init_alias_state(anchor_ms: int, anchor_iso: str, now_ms: int) -> Dict[str, Any]:
    return {
        "anchorMs":     anchor_ms,
        "anchorIso":    anchor_iso,
        "lastUpdateMs": now_ms,
        "sources":      {name: _conv_init_source_state(now_ms)
                         for name in _CONVICTION_SOURCES},
        "scoreRing":    [],     # [(ts_ms, score), ...] bounded by 90s
    }


def _conv_aggregate_source(src_state: Dict[str, Any], now_ms: int,
                           win_short: float, win_medium: float,
                           agg_w: Dict[str, float]) -> Tuple[float, int]:
    """Combine short SMA + medium SMA + session SMA per the aggregation weights.
    Returns (source_score_in_[-1,+1], samples_in_medium)."""
    short_avg, n_short = _rolling_sma(src_state["ring"], now_ms, win_short)
    med_avg,   n_med   = _rolling_sma(src_state["ring"], now_ms, win_medium)
    n_sess = src_state["sessionCount"]
    sess_avg = (src_state["sessionSum"] / n_sess) if n_sess > 0 else 0.0
    # Pull weights for available components; fall back proportionally when one
    # window has no samples (still ramping up).
    parts: List[Tuple[float, float]] = []
    if n_med > 0:    parts.append((agg_w.get("sma_medium",  0.50), med_avg))
    if n_short > 0:  parts.append((agg_w.get("sma_short",   0.30), short_avg))
    if n_sess > 0:   parts.append((agg_w.get("sma_session", 0.20), sess_avg))
    if not parts:
        return 0.0, n_med
    w_sum = sum(w for w, _ in parts) or 1e-9
    score = sum(w * v for w, v in parts) / w_sum
    return _clip(score), n_med


def _conv_apply_cluster_caps(effective: Dict[str, float],
                             clusters: Dict[str, List[str]],
                             caps: Dict[str, float]) -> Dict[str, float]:
    """Scale effective weights down inside each cluster so the cluster's total
    absolute weight cannot exceed its cap. Returns a NEW dict; unclustered
    sources are unchanged.
    """
    out = dict(effective)
    for name, members in clusters.items():
        cap = caps.get(name)
        if cap is None: continue
        total = sum(abs(out.get(m, 0.0)) for m in members)
        if total > cap and total > 0:
            scale = cap / total
            for m in members:
                if m in out: out[m] = out[m] * scale
    return out


def compute_session_conviction(snap: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Session-anchored multi-source weighted conviction.

    Per-source rolling SMAs over short (30s) and medium (120s) windows plus a
    session-anchored SMA from 08:30 CT, combined into a single source_score in
    [-1,+1]. Each source declares a reliability that gates its base weight;
    correlated sources are capped per cluster. The composite is the normalized
    weighted sum, clipped to [-1,+1].

    Returns a dict with the legacy keys still consumed by dashboard.js
    (score / trajectory / trend / durationSec / anchorMs / anchorIso /
    components / instantaneous / weights) plus the v2 detail blocks
    (sourceScores / sourceReliability / effectiveWeights / rawSources / method).
    Returns None only when there is no snapshot health or no alias.
    """
    if snap.get("health") != "ok": return None
    alias = snap.get("alias")
    if not alias: return None

    now_et = dt.datetime.now(ET)
    now_ms = int(now_et.timestamp() * 1000)
    anchor_ms, anchor_dt = _conv_session_anchor(now_et)

    cfg = _load_pax_weights()
    base_weights = cfg.get("conviction_source_weights") or CONVICTION_SOURCE_WEIGHTS
    clusters     = cfg.get("conviction_clusters")       or CONVICTION_CLUSTERS
    caps         = cfg.get("conviction_cluster_caps")   or CONVICTION_CLUSTER_CAPS
    win_cfg      = cfg.get("conviction_windows_sec")    or CONVICTION_WINDOWS_SEC
    agg_w        = cfg.get("conviction_aggregation_weights") or CONVICTION_AGG_WEIGHTS
    thresh       = cfg.get("conviction_thresholds")     or CONVICTION_THRESHOLDS
    win_short  = float(win_cfg.get("short", 30))
    win_medium = float(win_cfg.get("medium", 120))
    fresh_hl   = float(win_cfg.get("freshness_halflife_sec", 8))
    req_n      = int(win_cfg.get("required_samples", 10))

    st = _CONVICTION_STATE.get(alias)
    if not st or st.get("anchorMs") != anchor_ms:
        st = _conv_init_alias_state(anchor_ms, anchor_dt.isoformat(), now_ms)
        _CONVICTION_STATE[alias] = st
    # Defensive: a brand-new source added to the registry mid-session.
    for name in _CONVICTION_SOURCES:
        if name not in st["sources"]:
            st["sources"][name] = _conv_init_source_state(now_ms)

    st["lastUpdateMs"] = now_ms

    # 1. Pull each source's instantaneous reading, push into rings, compute
    #    its time-aggregated source_score and reliability.
    instantaneous: Dict[str, float] = {}
    source_scores: Dict[str, float] = {}
    source_reliability: Dict[str, float] = {}
    raw_sources: Dict[str, Any] = {}
    reasons: Dict[str, str] = {}

    for name, helper in _CONVICTION_SOURCES.items():
        try:
            result = helper(snap)
        except Exception as exc:
            # Source helpers must not crash the engine. Surface and skip.
            sys.stderr.write(f"[conviction] source {name} raised: "
                             f"{type(exc).__name__}: {exc}\n")
            result = {"score": 0.0, "reliability": 0.0,
                      "raw": {"_error": f"{type(exc).__name__}: {exc}"},
                      "reason": "source crashed"}
        inst_score = _clip(float(result.get("score") or 0.0))
        availability = float(result.get("reliability") or 0.0)
        src_st = st["sources"][name]
        # Only feed the rings when the source is actually available this tick.
        # An unavailable source must not drag the SMA back toward 0; instead
        # we just inherit its previous ring state.
        if availability > 0.0:
            src_st["ring"].append((now_ms, inst_score))
            src_st["sessionSum"]   += inst_score
            src_st["sessionCount"] += 1
            src_st["lastValue"]     = inst_score
            src_st["lastUpdateMs"]  = now_ms
            src_st["available"]     = True
        # Prune by medium window so the ring never grows without bound.
        src_st["ring"] = _prune_ring(src_st["ring"], now_ms, win_medium)

        instantaneous[name] = round(inst_score, 4)
        raw_sources[name]   = result.get("raw") or {}
        reasons[name]       = result.get("reason") or ""

        # Aggregate across windows.
        src_score, n_med = _conv_aggregate_source(src_st, now_ms,
                                                  win_short, win_medium, agg_w)
        source_scores[name] = src_score

        # Reliability: availability × sample_conf × freshness.
        sample_conf = min(1.0, n_med / float(req_n)) if req_n > 0 else 1.0
        age_sec = max(0.0, (now_ms - src_st["lastUpdateMs"]) / 1000.0)
        freshness = math.exp(-age_sec / max(0.5, fresh_hl)) if src_st["available"] else 0.0
        reliability = availability * sample_conf * freshness
        source_reliability[name] = max(0.0, min(1.0, reliability))

    # 2. Effective weights = base × reliability, then apply cluster caps.
    effective: Dict[str, float] = {
        name: base_weights.get(name, 0.0) * source_reliability[name]
        for name in _CONVICTION_SOURCES
    }
    effective = _conv_apply_cluster_caps(effective, clusters, caps)

    # 3. Composite score = normalized weighted sum.
    num = sum(effective[n] * source_scores[n] for n in effective)
    den = sum(abs(effective[n]) for n in effective)
    min_w = float(thresh.get("min_total_weight", 0.05))
    score = _clip(num / den) if den >= min_w else 0.0

    # 4. Trajectory — SMA slope of the composite over 30s vs 30-90s.
    st["scoreRing"].append((now_ms, score))
    st["scoreRing"] = _prune_ring(st["scoreRing"], now_ms, 90.0)
    recent_avg, n_recent = _rolling_sma(st["scoreRing"], now_ms, win_short)
    prior_cutoff = now_ms - int(win_short * 1000)
    prior_window = [(ts, v) for ts, v in st["scoreRing"] if ts < prior_cutoff]
    if prior_window:
        prior_avg = sum(v for _, v in prior_window) / len(prior_window)
        n_prior = len(prior_window)
    else:
        prior_avg, n_prior = 0.0, 0
    if n_recent >= 3 and n_prior >= 3:
        slope = recent_avg - prior_avg
        tr_strong = float(thresh.get("trajectory_strong", 0.08))
        tr_norm   = float(thresh.get("trajectory_normal", 0.02))
        if   slope >  tr_strong: traj = "RISING_STRONG"
        elif slope >  tr_norm:   traj = "RISING"
        elif slope < -tr_strong: traj = "FALLING_STRONG"
        elif slope < -tr_norm:   traj = "FALLING"
        else:                    traj = "FLAT"
    else:
        traj = "WARMUP"

    # 5. Trend label — same vocabulary as v1, thresholds from config.
    t_trend = float(thresh.get("trend", 0.35))
    t_lean  = float(thresh.get("lean",  0.18))
    t_chop  = float(thresh.get("chop",  0.10))
    nUP   = traj in ("RISING_STRONG", "RISING", "FLAT")
    nDOWN = traj in ("FALLING_STRONG", "FALLING", "FLAT")
    if   score >  t_trend and nUP:                       trend = "BULLISH_TREND"
    elif score < -t_trend and nDOWN:                     trend = "BEARISH_TREND"
    elif score >  t_lean  and nUP:                       trend = "BULL_LEAN"
    elif score < -t_lean  and nDOWN:                     trend = "BEAR_LEAN"
    elif score >  t_lean  and traj == "FALLING_STRONG":  trend = "BULL_FADING"
    elif score < -t_lean  and traj == "RISING_STRONG":   trend = "BEAR_FADING"
    elif abs(score) < t_chop:                            trend = "CHOP"
    else:                                                trend = "MIXED"

    # 6. Backward-compat blocks: dashboard.js iterates the 7 legacy keys on
    #    components / instantaneous. Populate them from the v2 sources.
    legacy_components = {
        k: round(source_scores.get(v, 0.0), 3)
        for k, v in _CONVICTION_LEGACY_KEY_MAP.items()
    }
    legacy_instant = {
        k: round(instantaneous.get(v, 0.0), 3)
        for k, v in _CONVICTION_LEGACY_KEY_MAP.items()
    }
    legacy_weights = {
        k: round(effective.get(v, 0.0), 3)
        for k, v in _CONVICTION_LEGACY_KEY_MAP.items()
    }

    duration_sec = (now_ms - anchor_ms) // 1000
    result = {
        "score":             round(score, 3),
        "trajectory":        traj,
        "trend":             trend,
        "durationSec":       int(duration_sec),
        "anchorMs":          anchor_ms,
        "anchorIso":         st["anchorIso"],
        "components":        legacy_components,
        "instantaneous":     legacy_instant,
        "weights":           legacy_weights,
        # v2 detail
        "sourceScores":      {n: round(v, 3) for n, v in source_scores.items()},
        "sourceReliability": {n: round(v, 3) for n, v in source_reliability.items()},
        "effectiveWeights":  {n: round(v, 4) for n, v in effective.items()},
        "rawSources":        raw_sources,
        "reasons":           reasons,
        "method":            CONVICTION_METHOD_VERSION,
    }
    # Cache for the NEXT poll's _level_composite slow-prior read. or_levels
    # runs before conviction in a single fetch_snapshot pass, so the composite
    # cannot see this poll's conviction — it reads the previous poll's from here.
    alias_key = snap.get("alias")
    if alias_key:
        _LAST_CONVICTION[alias_key] = result
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Pax decision agent (in-process — sim-only, CSV-logged, never places orders)
#
# This is the operational deployment of skills/pax-or/SKILL.md, computed every
# poll on the same snapshot the UI sees. There is NO live-trading code path
# anywhere in this function — by design. Live execution would require a
# separate explicit module that this codebase does not contain.
#
# CSV log:  D:/BookmapLogs/pax-agent-signals-YYYYMMDD.csv
#   columns: ts_utc, ts_ct, decision, size, size_tier, level_label, entry_price,
#            confidence, regime, biasScore, biasTraj, vwapSlope, vwapBias, vpBias,
#            session, or_high, or_low, or_width, mid, reasons
# ─────────────────────────────────────────────────────────────────────────────

PAX_LOG_DIR = Path(os.environ.get("PAX_LOG_DIR", r"D:\BookmapLogs"))
PAX_CONFIDENCE_FLOOR = 0.35
PAX_CONFIDENCE_FULL  = 0.50
PAX_MIN_OR_WIDTH_PTS = 3.0
PAX_MAX_OR_WIDTH_PTS = 25.0

# Legacy defaults — pax_decision now reads regime_boost from pax_weights.json
_PAX_REGIME_BOOST_DEFAULT = {
    "TRENDING_UP":    {"long_follow": 1.20, "short_follow": 0.50, "fade": 0.70},
    "TRENDING_DOWN":  {"long_follow": 0.50, "short_follow": 1.20, "fade": 0.70},
    "ABSORPTION_BID": {"long_follow": 0.80, "short_follow": 0.40, "long_fade":  1.30, "short_fade": 0.40},
    "ABSORPTION_ASK": {"long_follow": 0.40, "short_follow": 0.80, "short_fade": 1.30, "long_fade":  0.40},
    "EXHAUSTION_UP":  {"long_follow": 0.20, "short_fade": 1.30, "short_follow": 0.80, "long_fade": 0.40},
    "EXHAUSTION_DOWN":{"short_follow":0.20, "long_fade":  1.30, "long_follow":  0.80, "short_fade":0.40},
    "BALANCED":       {"any": 0.80},
    "QUIET":          {"any": 0.30},
    "WARMUP":         {"any": 1.00},
}


def _pax_boost(regime: str, decision: str) -> float:
    bm = _load_pax_weights().get("regime_boost", {}).get(regime, _PAX_REGIME_BOOST_DEFAULT.get(regime, {}))
    if "any" in bm: return bm["any"]
    key = {"ENTER_LONG_FOLLOW": "long_follow", "ENTER_SHORT_FOLLOW": "short_follow",
           "ENTER_LONG_FADE":   "long_fade",   "ENTER_SHORT_FADE":   "short_fade"}.get(decision)
    if key and key in bm: return bm[key]
    if decision.endswith("FADE") and "fade" in bm: return bm["fade"]
    return 1.0


def pax_decision(snap: Dict[str, Any]) -> Dict[str, Any]:
    """In-process Pax agent. Identical logic to pax_agent.decide() — sim only."""
    reasons: List[str] = []
    components: Dict[str, Any] = {}

    if snap.get("health") != "ok":
        return {"decision": "STAND_DOWN", "size": 0,
                "reason": f"health={snap.get('health')}",
                "reasons": [f"bridge {snap.get('health')}"], "components": components}
    if not snap.get("alias"):
        return {"decision": "STAND_DOWN", "size": 0,
                "reason": "no instrument attached", "reasons": ["bridge ok but no instrument"],
                "components": components}

    gates = snap.get("gates") or {}
    session = (gates.get("session") or {}).get("code", "?")
    components["session"] = session
    if session in ("PRE_MARKET", "OR_FORMING", "POST_MARKET"):
        return {"decision": "WAIT", "size": 0, "reason": f"session {session}",
                "reasons": [f"session: {session}"], "components": components}
    if (gates.get("news") or {}).get("blocked"):
        return {"decision": "STAND_DOWN", "size": 0,
                "reason": f"news blackout: {(gates.get('news') or {}).get('label','?')}",
                "reasons": ["news blackout"], "components": components}

    ol = snap.get("or_levels")
    if not ol:
        return {"decision": "WAIT", "size": 0, "reason": "OR not set yet",
                "reasons": ["awaiting OR-Strategy CSV"], "components": components}

    ow = float(ol.get("orWidthPts") or 0)
    components["or"] = {"high": ol.get("orHigh"), "low": ol.get("orLow"), "width": ow}
    if ow < PAX_MIN_OR_WIDTH_PTS or ow > PAX_MAX_OR_WIDTH_PTS:
        return {"decision": "STAND_DOWN", "size": 0,
                "reason": f"OR width {ow:.1f} out of [{PAX_MIN_OR_WIDTH_PTS},{PAX_MAX_OR_WIDTH_PTS}]",
                "reasons": [f"OR width {ow:.1f}"], "components": components}
    if ol.get("middleLock"):
        return {"decision": "STAND_DOWN", "size": 0, "reason": "MIDDLE LOCK",
                "reasons": ["price inside OR, no level proximity"], "components": components}
    if not ol.get("inProximity"):
        return {"decision": "STAND_DOWN", "size": 0, "reason": "no level in proximity",
                "reasons": ["not within 50 ticks of any level"], "components": components}

    levels = ol.get("levels") or []
    prox = sorted([l for l in levels if l.get("proximity")],
                  key=lambda l: abs(float(l.get("distance") or 0)))
    if not prox:
        return {"decision": "STAND_DOWN", "size": 0, "reason": "no proximate level",
                "reasons": ["proximity flag stale"], "components": components}
    level = prox[0]
    components["level"] = {"label": level.get("label"), "price": level.get("price"),
                           "distance": level.get("distance")}

    ldec  = level.get("decision") or "WAIT"
    lconf = float(level.get("confidence") or 0)
    reasons.append(f"{level.get('label')} @ {level.get('price')} → {ldec}")
    reasons.extend((level.get("reasons") or [])[:3])

    if ldec == "WAIT" or lconf < PAX_CONFIDENCE_FLOOR:
        return {"decision": "WAIT", "size": 0,
                "reason": f"level conf {lconf:.2f} below floor",
                "reasons": reasons, "components": components}

    lcomp = level.get("components") or {}
    rot = lcomp.get("ps_rot") or "NONE"
    if rot == "ROTATION_DN" and ldec == "ENTER_LONG_FOLLOW":
        return {"decision": "WAIT", "size": 0, "reason": "ROTATION_DN vetoes long FOLLOW",
                "reasons": reasons + ["rotation against direction"], "components": components}
    if rot == "ROTATION_UP" and ldec == "ENTER_SHORT_FOLLOW":
        return {"decision": "WAIT", "size": 0, "reason": "ROTATION_UP vetoes short FOLLOW",
                "reasons": reasons + ["rotation against direction"], "components": components}

    flow = snap.get("flow") or {}
    regime = flow.get("regime") or "WARMUP"
    components["regime"] = regime
    components["biasScore"] = flow.get("biasScore")
    components["biasTrajectory"] = flow.get("biasTrajectory")
    boost = _pax_boost(regime, ldec)
    reasons.append(f"regime {regime} × {boost:.2f}")
    eff = lconf * boost

    vws = flow.get("vwapSlope") or {}
    slope_label = vws.get("label") or "WARMUP"
    components["vwapSlope"] = slope_label
    if slope_label == "STRONG_UP" and ldec == "ENTER_SHORT_FOLLOW":
        eff *= 0.5; reasons.append("STRONG_UP vs short FOLLOW → ½")
    if slope_label == "STRONG_DOWN" and ldec == "ENTER_LONG_FOLLOW":
        eff *= 0.5; reasons.append("STRONG_DOWN vs long FOLLOW → ½")

    vw = (snap.get("vwap_bias") or {}).get("label")
    vp = (snap.get("vp_bias")   or {}).get("label")
    components["vwapBias"] = vw
    components["vpBias"]   = vp
    direction = "LONG" if "LONG" in ldec else "SHORT"
    want = "BULLISH" if direction == "LONG" else "BEARISH"
    if vw and vp and vw != "NEUTRAL" and vp != "NEUTRAL" and vw != want and vp != want:
        return {"decision": "WAIT", "size": 0,
                "reason": "both VWAP-bias and VP-bias against direction",
                "reasons": reasons + [f"want {want}, got VWAP={vw} VP={vp}"],
                "components": components}
    agree = int(vw == want) + int(vp == want)
    if agree:
        eff *= 1.0 + 0.10 * agree
        reasons.append(f"biases agree ×{1+0.10*agree:.2f}")

    # Subgroup override (Phase D): apply per-key multipliers learned from replay
    overrides = _load_pax_weights().get("subgroup_overrides", {}) or {}
    if isinstance(overrides, dict) and overrides:
        # Build candidate keys to look up — match the format used by pax_replay --recommend
        base_key = f"{ldec}@{level.get('label')}"
        candidates = []
        if vw: candidates.append(f"{base_key}|vwapBias={vw}")
        if vp: candidates.append(f"{base_key}|vpBias={vp}")
        candidates.append(f"{base_key}|regime={regime}")
        if slope_label: candidates.append(f"{base_key}|vwapSlope={slope_label}")
        for k in candidates:
            ov = overrides.get(k)
            if isinstance(ov, dict) and "multiplier" in ov:
                eff *= float(ov["multiplier"])
                reasons.append(f"override {k} ×{ov['multiplier']}")
                components.setdefault("overrides", []).append({"key": k, "mult": ov["multiplier"]})

    th = _load_pax_weights().get("confidence_thresholds", {})
    full = th.get("full", PAX_CONFIDENCE_FULL); half = th.get("half", PAX_CONFIDENCE_FLOOR)
    if eff >= full:    size_tier = "FULL"; size = 3
    elif eff >= half:  size_tier = "HALF"; size = 1
    else: size_tier = "NONE"; size = 0
    if size == 0:
        return {"decision": "WAIT", "size": 0,
                "reason": f"effective conf {eff:.2f} below floor",
                "reasons": reasons, "components": components}

    return {
        "decision": ldec, "size": size, "size_tier": size_tier,
        "confidence": round(eff, 3),
        "level_label": level.get("label"),
        "entry": level.get("price"),
        "reasons": reasons, "components": components,
    }


# ─── CSV recorder for Pax decisions (sim-only data collection) ──────────────
_PAX_LAST_SIG: Tuple[Any, ...] = ()
_PAX_LOG_FH = None
_PAX_LOG_DATE = ""


def _pax_log_path(now_et: dt.datetime) -> Path:
    return PAX_LOG_DIR / f"pax-agent-signals-{now_et.strftime('%Y%m%d')}.csv"


def _pax_open_log(now_et: dt.datetime):
    global _PAX_LOG_FH, _PAX_LOG_DATE
    today = now_et.strftime("%Y%m%d")
    if _PAX_LOG_FH and _PAX_LOG_DATE == today:
        return _PAX_LOG_FH
    if _PAX_LOG_FH:
        try: _PAX_LOG_FH.close()
        except Exception: pass
        _PAX_LOG_FH = None
    try:
        PAX_LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = _pax_log_path(now_et)
        is_new = not path.exists()
        _PAX_LOG_FH = open(path, "a", encoding="utf-8", newline="")
        _PAX_LOG_DATE = today
        if is_new:
            _PAX_LOG_FH.write("ts_utc,ts_ct,decision,size,size_tier,level,entry,"
                              "confidence,regime,biasScore,biasTraj,vwapSlope,"
                              "vwapBias,vpBias,session,or_high,or_low,or_width,"
                              "mid,reasons\n")
            _PAX_LOG_FH.flush()
    except Exception as e:
        sys.stderr.write(f"[pax] CSV open failed: {e}\n")
        _PAX_LOG_FH = None
    return _PAX_LOG_FH


def pax_record(snap: Dict[str, Any], decision: Dict[str, Any]) -> None:
    """Append non-trivial decisions to today's CSV. Sim only."""
    global _PAX_LAST_SIG
    dec = decision.get("decision", "WAIT")
    # Only record state changes, not every WAIT
    sig = (dec, decision.get("size_tier"), decision.get("level_label"))
    if sig == _PAX_LAST_SIG: return
    _PAX_LAST_SIG = sig

    now_et = dt.datetime.now(ET)
    fh = _pax_open_log(now_et)
    if not fh: return

    c = decision.get("components") or {}
    or_ = c.get("or") or {}
    flow = snap.get("flow") or {}
    book = snap.get("book") or {}
    reasons = " | ".join((decision.get("reasons") or [])[:6]).replace(",", ";")
    row = [
        now_et.astimezone(dt.timezone.utc).isoformat(timespec="seconds"),
        now_et.isoformat(timespec="seconds"),
        dec, str(decision.get("size") or 0), str(decision.get("size_tier") or ""),
        str(decision.get("level_label") or ""), str(decision.get("entry") or ""),
        str(decision.get("confidence") or ""), str(flow.get("regime") or ""),
        str(flow.get("biasScore") or ""), str(flow.get("biasTrajectory") or ""),
        str((flow.get("vwapSlope") or {}).get("label") or ""),
        str(c.get("vwapBias") or ""), str(c.get("vpBias") or ""),
        str(c.get("session") or ""), str(or_.get("high") or ""),
        str(or_.get("low") or ""), str(or_.get("width") or ""),
        str(book.get("mid") or ""), f'"{reasons}"',
    ]
    try:
        fh.write(",".join(row) + "\n"); fh.flush()
    except Exception as e:
        sys.stderr.write(f"[pax] CSV write failed: {e}\n")


def trade_decision(snap: Dict[str, Any]) -> Dict[str, Any]:
    """Composite ENTER_LONG / ENTER_SHORT / WAIT / STAND_DOWN / EXIT verdict."""
    reasons: List[str] = []
    gates: Dict[str, Any] = {}
    ses = snap.get("gates", {}).get("session", {})
    gates["session"] = ses.get("code", "?")
    if ses.get("code") in ("PRE_MARKET", "OR_FORMING", "CHOP", "CLOSE_RISK", "POST_MARKET"):
        return {"decision": "WAIT", "confidence": "LOW", "size": 0,
                "entry": None, "stop": None, "target1": None, "target2": None,
                "reasons": [f"session: {ses.get('label','?')}"], "gates": gates}
    reasons.append(f"session {ses.get('label','?')}")
    news = snap.get("gates", {}).get("news", {})
    gates["news"] = news.get("label", "?")
    if news.get("blocked"):
        return {"decision": "STAND_DOWN", "confidence": "LOW", "size": 0,
                "entry": None, "stop": None, "target1": None, "target2": None,
                "reasons": reasons + [f"news blackout: {news.get('label','?')}"], "gates": gates}
    or_row = snap.get("or_row") or {}
    bias = (or_row.get("bias") or "NEUTRAL").upper()
    confidence = (or_row.get("confidence") or "LOW").upper()
    gates["or_bias"] = bias
    if bias == "BULLISH":
        direction = "LONG"
    elif bias == "BEARISH":
        direction = "SHORT"
    else:
        return {"decision": "WAIT", "confidence": "LOW", "size": 0,
                "entry": None, "stop": None, "target1": None, "target2": None,
                "reasons": reasons + ["OR bias neutral"], "gates": gates}
    reasons.append(f"OR {bias.lower()} ({confidence})")
    book = snap.get("book") or {}
    vwap = snap.get("vwap")
    vwap_or = vwap_or_gate(book if isinstance(book, dict) and "_error" not in book else {}, vwap, or_row)
    gates["vwap_or"] = vwap_or.get("state", "?")
    if vwap_or.get("state") == "BLOCKED":
        return {"decision": "WAIT", "confidence": _demote_conf(confidence), "size": 0,
                "entry": None, "stop": None, "target1": None, "target2": None,
                "reasons": reasons + ["VWAP/OR blocked"], "gates": gates}
    stretch = compute_stretch(snap.get("vwap_obj"))
    if stretch:
        gates["stretch"] = stretch["label"]
        if stretch["label"] in ("EXTREME", "BLOWOFF"):
            return {"decision": "STAND_DOWN", "confidence": "LOW", "size": 0,
                    "entry": None, "stop": None, "target1": None, "target2": None,
                    "reasons": reasons + [f"σ-stretch {stretch['label']}"], "gates": gates}
        reasons.append(f"{stretch['label']} ({stretch['dev_sigma']:+.1f}σ)")
    mom_flag = (snap.get("momentum") or {}).get("flag", "")
    gates["momentum"] = mom_flag
    if direction == "LONG":
        if mom_flag == "ALIGNED BEAR":
            return {"decision": "STAND_DOWN", "confidence": "LOW", "size": 0,
                    "entry": None, "stop": None, "target1": None, "target2": None,
                    "reasons": reasons + [f"tape against thesis ({mom_flag})"], "gates": gates}
        if mom_flag == "INFLECTION DOWN":
            confidence = _demote_conf(confidence)
    else:
        if mom_flag == "ALIGNED BULL":
            return {"decision": "STAND_DOWN", "confidence": "LOW", "size": 0,
                    "entry": None, "stop": None, "target1": None, "target2": None,
                    "reasons": reasons + [f"tape against thesis ({mom_flag})"], "gates": gates}
        if mom_flag == "INFLECTION UP":
            confidence = _demote_conf(confidence)
    reasons.append(f"momentum {mom_flag}")
    pos = snap.get("position") or {}
    cur_pos = pos.get("position", 0) if not pos.get("_error") else 0
    gates["position"] = cur_pos
    if cur_pos != 0:
        if (cur_pos > 0 and direction == "LONG") or (cur_pos < 0 and direction == "SHORT"):
            return {"decision": "WAIT", "confidence": confidence, "size": 0,
                    "entry": None, "stop": None, "target1": None, "target2": None,
                    "reasons": reasons + [f"already {direction.lower()} {abs(cur_pos)} — no pyramid"],
                    "gates": gates}
        reasons.append(f"reverses existing {cur_pos:+d}")
    vobj = snap.get("vwap_obj") or {}
    try:
        or_high = float(or_row.get("orHigh", "nan"))
        or_low = float(or_row.get("orLow", "nan"))
    except Exception:
        or_high = or_low = float("nan")
    book_mid = (snap.get("book") or {}).get("mid")
    tick = 0.25
    if direction == "LONG":
        entry = book_mid; stop = or_low - 2 * tick if not math.isnan(or_low) else None
        t1 = vobj.get("upper1"); t2 = vobj.get("upper2")
    else:
        entry = book_mid; stop = or_high + 2 * tick if not math.isnan(or_high) else None
        t1 = vobj.get("lower1"); t2 = vobj.get("lower2")
    size = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}.get(confidence, 1)
    return {
        "decision": "ENTER_LONG" if direction == "LONG" else "ENTER_SHORT",
        "confidence": confidence, "size": size,
        "entry": entry, "stop": stop, "target1": t1, "target2": t2,
        "reasons": reasons, "gates": gates,
    }


# Lazily-instantiated PaxCollector. Lives for the lifetime of the dashboard
# process. Audit HIGH 2.2: collector must spool tick state even when
# pax_trader is not running, otherwise users who run only the dashboard
# never produce pax-recordings.
_PAX_COLLECTOR = None
def _get_pax_collector():
    global _PAX_COLLECTOR
    if _PAX_COLLECTOR is None:
        try:
            from .pax_collector import PaxCollector
            _PAX_COLLECTOR = PaxCollector()
        except Exception as e:
            sys.stderr.write(f"[dashboard] PaxCollector init failed: {e}\n")
            _PAX_COLLECTOR = False     # poison so we don't retry every poll
    return _PAX_COLLECTOR if _PAX_COLLECTOR is not False else None


def _sync_magnet_levels(cfg: BridgeConfig, alias: Optional[str],
                        or_levels: Optional[Dict[str, Any]]) -> None:
    """Push the OR-Strategy level grid to the bridge as stop-sweep magnets.

    Idempotent across snapshots: only posts when the level set changes OR the
    cached set is older than _MAGNET_REFRESH_SECS. Catches every exception so a
    transient bridge failure cannot break fetch_snapshot.
    """
    if not alias:
        return
    if not isinstance(or_levels, dict) or "_error" in or_levels:
        return
    raw_levels = or_levels.get("levels")
    if not isinstance(raw_levels, list) or not raw_levels:
        return

    prices: List[float] = []
    for entry in raw_levels:
        if not isinstance(entry, dict):
            continue
        p = entry.get("price")
        if isinstance(p, bool):
            # bool is an int subclass — exclude explicitly to avoid silently coercing True/False.
            continue
        if not isinstance(p, (int, float)):
            continue
        try:
            fp = float(p)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(fp):
            continue
        prices.append(round(fp, 2))
    if not prices:
        return

    new_tuple: Tuple[float, ...] = tuple(sorted(prices))
    now_mono = time.monotonic()

    with _LAST_MAGNETS_LOCK:
        cached = _LAST_MAGNETS.get(alias)
        if cached is not None:
            cached_tuple, cached_ts = cached
            if cached_tuple == new_tuple and (now_mono - cached_ts) < _MAGNET_REFRESH_SECS:
                return

    payload = ",".join(f"{p:g}" for p in new_tuple)
    try:
        with BridgeClient(cfg, timeout_s=2.0) as client:
            client.post_json("/magnet_levels", {"alias": alias, "levels": payload})
    except BridgeError as exc:
        sys.stderr.write(f"[dashboard] magnet_levels POST failed: {exc}\n")
        return
    except Exception as exc:
        sys.stderr.write(f"[dashboard] magnet_levels POST crashed: "
                         f"{type(exc).__name__}: {exc}\n")
        return

    with _LAST_MAGNETS_LOCK:
        _LAST_MAGNETS[alias] = (new_tuple, time.monotonic())


def fetch_snapshot() -> Dict[str, Any]:
    try:
        cfg = BridgeConfig.load()
    except MissingTokenError as exc:
        return {"health": "error", "error": str(exc)}
    try:
        with BridgeClient(cfg, timeout_s=3.0) as c:
            ping = c.get_json("/ping")
            instruments = c.get_json("/instruments")
    except BridgeError as exc:
        return {"health": "offline", "error": str(exc)}
    insts = instruments.get("instruments", [])
    if not insts:
        return {"health": "ok", "ping": ping, "alias": None, "instruments": instruments,
                "note": "Bridge alive but no instrument attached."}
    alias = insts[0]["alias"]
    def safe(fn):
        try: return fn()
        except Exception as e: return {"_error": f"{type(e).__name__}: {e}"}
    with BridgeClient(cfg, timeout_s=3.0) as c:
        # depth=300 ticks (~75 pts each side at NQ 0.25/tick) gives ~15 five-point
        # buckets per side — supports the 12-per-side visible window with headroom
        # so out-of-band orders still contribute to the EMA accumulator.
        book     = safe(lambda: c.get_json("/orderbook",      {"alias": alias, "depth": 300}))
        trades   = safe(lambda: c.get_json("/recent_trades",  {"alias": alias, "count": 200}))
        position = safe(lambda: c.get_json("/position",       {"alias": alias}))
        working  = safe(lambda: c.get_json("/working_orders", {"alias": alias}))
        balance  = safe(lambda: c.get_json("/balance",        {"alias": alias}))
        fills    = safe(lambda: c.get_json("/recent_fills",   {"alias": alias, "count": 20}))
        vwap_obj = safe(lambda: c.get_json("/vwap",           {"alias": alias}))
        flow_obj = safe(lambda: c.get_json("/momentum",       {"alias": alias, "windows": "30,120,600"}))
        vp_obj   = safe(lambda: c.get_json("/volume_profile", {"alias": alias}))
        tape_obj = safe(lambda: c.get_json("/tape_buckets",   {"alias": alias}))
        lt_obj   = safe(lambda: c.get_json("/lt_liquidity",   {"alias": alias}))
        ps_obj   = safe(lambda: c.get_json("/pull_stack",     {"alias": alias}))
        me_obj   = safe(lambda: c.get_json("/microstructure_events", {"alias": alias, "max": 30}))
    now_et = dt.datetime.now(ET)
    state, label = session_state(now_et)
    blocked, news_label = news_blackout(now_et)
    or_row = or_latest_row()
    trade_list = trades.get("trades", []) if isinstance(trades, dict) and "_error" not in trades else []
    if isinstance(vwap_obj, dict) and "_error" not in vwap_obj and vwap_obj.get("vwap") is not None:
        vwap = vwap_obj.get("vwap"); vwap_source = "session"
    else:
        vwap = vwap_from_trades(trade_list); vwap_source = "ring_buffer_fallback"
    snap = {
        "health": "ok",
        "ts": now_et.isoformat(timespec="seconds"),
        "ping": ping, "alias": alias, "instruments": instruments,
        "book": book, "trades": trade_list,
        "position": position, "working": working, "balance": balance, "fills": fills,
        "or_row": or_row, "vwap": vwap, "vwap_source": vwap_source,
        "vwap_obj": vwap_obj if isinstance(vwap_obj, dict) else None,
        "flow": flow_obj if isinstance(flow_obj, dict) else None,
        "volume_profile": vp_obj if isinstance(vp_obj, dict) else None,
        "tape_buckets": tape_obj if isinstance(tape_obj, dict) else None,
        "lt_liquidity": lt_obj if isinstance(lt_obj, dict) else None,
        "pull_stack": ps_obj if isinstance(ps_obj, dict) else None,
        "micro_events": me_obj if isinstance(me_obj, dict) else None,
        "momentum": {
            "i10":  imbalance(trade_list, 10),
            "i50":  imbalance(trade_list, 50),
            "i200": imbalance(trade_list, 200),
            "flag": momentum_flag(imbalance(trade_list, 10), imbalance(trade_list, 50), imbalance(trade_list, 200)),
        },
        "gates": {
            "session": {
                "code": state, "label": label,
                "now_local": dt.datetime.now(DISPLAY_TZ).strftime("%H:%M:%S ") + DISPLAY_TZ_LABEL,
                "now_et":    now_et.strftime("%H:%M:%S ET"),
            },
            "news":    {"blocked": blocked, "label": news_label},
            "vwap_or": vwap_or_gate(book if isinstance(book, dict) and "_error" not in book else {}, vwap, or_row),
        },
    }
    # Each helper is wrapped so a bug in one doesn't kill the whole snapshot.
    def _safe_call(fn, name):
        try: return fn(snap)
        except Exception as e:
            sys.stderr.write(f"[dashboard] {name} crashed: {type(e).__name__}: {e}\n"
                             + traceback.format_exc() + "\n")
            return {"_error": f"{name}: {type(e).__name__}: {e}"}
    snap["tape_flow"] = _safe_call(compute_tape_flow, "compute_tape_flow")
    snap["or_levels"] = _safe_call(compute_or_levels, "compute_or_levels")
    # Push the OR grid to the bridge as STOP_SWEEP magnets. Failure is logged
    # but never propagates — magnet sync is best-effort, snapshot composition
    # is critical-path.
    try:
        _sync_magnet_levels(cfg, alias, snap["or_levels"])
    except Exception as exc:
        sys.stderr.write(f"[dashboard] _sync_magnet_levels outer guard: "
                         f"{type(exc).__name__}: {exc}\n")
    snap["vwap_bias"] = _safe_call(compute_vwap_bias, "compute_vwap_bias")
    snap["vp_bias"]   = _safe_call(compute_vp_bias,   "compute_vp_bias")
    snap["decision"]  = _safe_call(trade_decision,    "trade_decision")
    snap["conviction"] = _safe_call(compute_session_conviction, "compute_session_conviction")
    snap["pax"]       = _safe_call(pax_decision,      "pax_decision")
    # SIM trades from local sim engine (read-only — agent process owns writes)
    try:
        from .sim_engine import SimEngine
        if alias:
            snap["sim"] = SimEngine(alias=alias).snapshot()
    except Exception as e:
        sys.stderr.write(f"[dashboard] sim snapshot failed: {e}\n")
        snap["sim"] = {"_error": str(e)}
    # Proximity-triggered data collector — produces D:/BookmapLogs/pax-recordings/
    # regardless of whether pax_trader is running.
    try:
        coll = _get_pax_collector()
        if coll is not None:
            coll.tick(snap)
    except Exception as e:
        sys.stderr.write(f"[dashboard] pax_collector tick failed: {e}\n")
    try: pax_record(snap, snap["pax"] or {})
    except Exception as e: sys.stderr.write(f"[pax] record failed: {e}\n")
    return snap


def safe_json(obj: Any) -> str:
    def clean(x):
        if isinstance(x, float) and (math.isnan(x) or math.isinf(x)): return None
        if isinstance(x, dict): return {k: clean(v) for k, v in x.items()}
        if isinstance(x, list): return [clean(v) for v in x]
        return x
    return json.dumps(clean(obj), default=str)


INDEX_HTML = r"""<!doctype html>

<html><head><meta charset="utf-8"><title>Bookmap HUD</title>
<style>
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { background:#0b0e13; color:#cfd6e4; font:13px/1.4 "JetBrains Mono","Consolas",monospace; margin:0; padding:10px; }
h1 { font-size:14px; margin:0 0 8px; color:#7aa2f7; display:flex; align-items:center; gap:8px; }
.dot { width:10px; height:10px; border-radius:50%; background:#666; display:inline-block; }
.dot.ok{background:#9ece6a;} .dot.err{background:#f7768e;} .dot.warn{background:#e0af68;}
.row { display:flex; gap:8px; margin-bottom:8px; }
.card { background:#161a23; border:1px solid #232733; border-radius:6px; padding:8px 10px; flex:1; min-width:0; }
.card h2 { font-size:11px; color:#7aa2f7; text-transform:uppercase; margin:0 0 6px; letter-spacing:1px; font-weight:600; }
.kv { display:flex; justify-content:space-between; padding:1px 0; }
.kv .v { color:#e0e0e0; font-weight:600; font-variant-numeric: tabular-nums; }
.buy{color:#9ece6a;} .sell{color:#f7768e;} .muted{color:#737994;} .err{color:#f7768e;}
.badge { display:inline-block; padding:2px 8px; border-radius:3px; font-weight:600; font-size:11px; }
.b-ok{background:#1f3a23;color:#9ece6a;} .b-warn{background:#3a2f1a;color:#e0af68;}
.b-block{background:#3a1f1f;color:#f7768e;} .b-info{background:#1f2837;color:#7aa2f7;} .b-neutral{background:#232733;color:#a9b1d6;}
table { width:100%; border-collapse:collapse; font-size:12px; font-variant-numeric: tabular-nums; }
td, th { padding:1px 4px; text-align:right; }
th { color:#7aa2f7; font-size:10px; text-transform:uppercase; border-bottom:1px solid #232733; font-weight:600; }
.ladder { table-layout: fixed; width: 100%; }
.ladder td, .ladder th { padding: 1px 4px; font-variant-numeric: tabular-nums; }
.ladder col.c-bid   { width: 33%; }
.ladder col.c-price { width: 34%; }
.ladder col.c-ask   { width: 33%; }
.ladder td.bid   { color:#9ece6a; text-align: right; }
.ladder td.ask   { color:#f7768e; text-align: left;  }
.ladder td.px    { text-align: center; color:#cfd6e4; }
.ladder th.bid, .ladder th.ask, .ladder th.px { color:#7aa2f7; }
.ladder th.bid { text-align: right; }
.ladder th.ask { text-align: left;  }
.ladder th.px  { text-align: center; }
.ladder tr.mid td { text-align: center !important; padding: 2px 4px; }
.bar { display:inline-block; height:8px; border-radius:2px; vertical-align:middle; }
.imb-pos{background:#9ece6a;} .imb-neg{background:#f7768e;}
.macro-banner { background:#3a1f1f; color:#f7768e; padding:6px 10px; border-radius:4px; margin-bottom:8px; text-align:center; font-weight:600; }
.decision-banner { padding:10px 14px; border-radius:6px; margin-bottom:10px; border:2px solid; }
.decision-banner .dec-main { display:flex; gap:14px; align-items:baseline; }
.decision-banner .dec-action { font-size:22px; font-weight:700; letter-spacing:1px; }
.decision-banner .dec-conf { font-size:11px; font-weight:700; padding:2px 8px; border-radius:3px; }
.decision-banner .dec-targets { font-size:12px; color:#cfd6e4; font-variant-numeric:tabular-nums; margin-left:auto; }
.decision-banner .dec-reasons { margin-top:6px; font-size:11px; color:#a9b1d6; }
.decision-banner .dec-gates { margin-top:4px; font-size:10px; color:#737994; font-family:"JetBrains Mono",monospace; }
.d-enter-long  { background:#0f2a14; border-color:#9ece6a; }
.d-enter-long  .dec-action { color:#9ece6a; }
.d-enter-long  .dec-conf   { background:#9ece6a; color:#0f2a14; }
.d-enter-short { background:#2a0f14; border-color:#f7768e; }
.d-enter-short .dec-action { color:#f7768e; }
.d-enter-short .dec-conf   { background:#f7768e; color:#2a0f14; }
.d-wait        { background:#2a2417; border-color:#e0af68; }
.d-wait        .dec-action { color:#e0af68; }
.d-wait        .dec-conf   { background:#e0af68; color:#2a2417; }
.d-stand-down  { background:#2a1717; border-color:#f7768e; }
.d-stand-down  .dec-action { color:#f7768e; }
.d-stand-down  .dec-conf   { background:#f7768e; color:#2a1717; }
.d-exit        { background:#2a0a0a; border-color:#ff5d62; }
.d-exit        .dec-action { color:#ff5d62; }
.d-exit        .dec-conf   { background:#ff5d62; color:#2a0a0a; }

/* OR levels grid */
.lvl-grid { display:flex; flex-direction:column; gap:2px; }
.lvl-row  { display:grid; grid-template-columns:54px 70px 70px 92px 1fr 60px; gap:8px;
            align-items:center; padding:3px 6px; border-radius:3px; font-size:12px;
            font-variant-numeric: tabular-nums; }
.lvl-row.proximity {
    background:#1a2030;
    border-left:3px solid #e0af68;
    padding-left:4px;
    animation: lvl-pulse 1.4s ease-in-out infinite;
}
@keyframes lvl-pulse {
    0%, 100% { background:#1a2030;   border-left-color:#e0af68; }
    50%      { background:#2a3550;   border-left-color:#ffd580; }
}
.lvl-row.proximity .lvl-lbl { text-shadow: 0 0 6px rgba(224,175,104,0.7); }
.live-pulse {
    display:inline-block; padding:2px 8px; border-radius:3px;
    background:#3a2f1a; color:#ffd580;
    font-weight:700; font-size:11px;
    animation: live-blink 1s ease-in-out infinite;
}
@keyframes live-blink {
    0%, 100% { background:#3a2f1a; box-shadow:0 0 0 0 rgba(224,175,104,0.0); }
    50%      { background:#5a4422; box-shadow:0 0 8px 2px rgba(224,175,104,0.4); }
}
.lvl-row.at-mid    { background:#101620; opacity:0.55; }
.lvl-lbl  { font-weight:700; letter-spacing:0.5px; }
.lvl-lbl.bull{color:#9ece6a;} .lvl-lbl.bear{color:#f7768e;} .lvl-lbl.flat{color:#a9b1d6;}
.lvl-price{ color:#cfd6e4; }
.lvl-dist { color:#737994; font-size:11px; }
.lvl-dec  { font-weight:600; padding:2px 8px; border-radius:3px; text-align:center; font-size:11px; }
.lvl-dec.follow-bull{background:#1f3a23;color:#9ece6a;}
.lvl-dec.follow-bear{background:#3a1f1f;color:#f7768e;}
.lvl-dec.fade-bull  {background:#1f2a3a;color:#7aa2f7;border:1px dashed #7aa2f7;}
.lvl-dec.fade-bear  {background:#3a2a1f;color:#e0af68;border:1px dashed #e0af68;}
.lvl-dec.wait       {background:#232733;color:#737994;}
.lvl-rsn  { color:#737994; font-size:10px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.lvl-conf { color:#cfd6e4; font-size:10px; text-align:right; }
.lvl-conf .bar-fill{display:inline-block;height:6px;background:#7aa2f7;border-radius:1px;vertical-align:middle;}
.lvl-detail { grid-column: 1 / -1; padding:6px 4px 4px 8px; color:#a9b1d6; font-size:10px;
              border-top:1px dashed #232733; margin-top:2px;
              display:grid; grid-template-columns:repeat(7, 1fr); gap:6px; }
.lvl-detail .ck { color:#737994; font-size:9px; text-transform:uppercase; }
.lvl-detail .cv { color:#cfd6e4; font-variant-numeric: tabular-nums; }
.middle-lock { background:#2a1717; color:#f7768e; padding:6px 10px; border-radius:4px;
               margin-bottom:6px; font-size:11px; font-weight:600; text-align:center;
               border:1px solid #f7768e; }

/* Trend heatmap — full-width header strip + row background tints */
.trend-bar {
    display:flex; align-items:center; gap:10px;
    margin:4px 0 8px 0; padding:6px 12px; border-radius:4px;
    font-size:11px; font-weight:700; letter-spacing:0.6px; text-transform:uppercase;
    background:linear-gradient(90deg,
        var(--bar-from, #232733) 0%,
        var(--bar-mid, #232733) 50%,
        var(--bar-to, #232733) 100%);
    color:var(--bar-fg, #cfd6e4);
    border:1px solid var(--bar-border, #2a3550);
    box-shadow: 0 0 12px var(--bar-glow, transparent);
}
.trend-bar .tb-arrow  { font-size:18px; line-height:1; }
.trend-bar .tb-label  { flex:1; }
.trend-bar .tb-pct    { font-variant-numeric: tabular-nums; opacity:0.85; }
.trend-bar .tb-meter  { width:140px; height:6px; background:rgba(0,0,0,0.35);
                        border-radius:3px; overflow:hidden; position:relative; }
.trend-bar .tb-meter::after {
    content:''; position:absolute; left:50%; top:0; bottom:0; width:1px;
    background:rgba(255,255,255,0.25);
}
.trend-bar .tb-fill {
    height:100%;
    background:var(--bar-fill, #cfd6e4);
    width:var(--fill-w, 0%);
    margin-left:var(--fill-x, 50%);
    box-shadow:0 0 6px var(--bar-fill, #cfd6e4);
}
/* Row heat — actual background tint with directional gradient */
.lvl-row.heat {
    background: linear-gradient(90deg,
        var(--heat-from, transparent) 0%,
        var(--heat-to,   transparent) 100%);
}
.lvl-row.heat .lvl-rsn { color:#a9b1d6; }
/* Proximity row keeps its pulse but overrides heat background while flashing */
.lvl-row.proximity.heat { background:#1a2030; }
/* Generic <tr> heat — used by Tape / LT / Pull-Stack cards */
tr.row-heat td { position:relative; }
tr.row-heat { background: var(--row-bg, transparent) !important; }
</style></head>
<body>
<h1><span id="dot" class="dot"></span><span id="hdr">connecting…</span><span class="muted" style="font-size:11px;" id="alias-clock"></span></h1>
<div id="news-banner"></div>

<!-- Pax decision banner — HIDDEN 2026-05-17 per user request.
     Code path stays alive (trade_decision still runs, dec-* nodes still
     receive innerHTML updates) so re-enabling is just removing display:none. -->
<div id="decision-banner" class="decision-banner d-wait" style="display:none;">
  <div class="dec-main">
    <span class="dec-action" id="dec-action">…</span>
    <span class="dec-conf"   id="dec-conf"></span>
    <span class="dec-targets" id="dec-targets"></span>
  </div>
  <div class="dec-reasons" id="dec-reasons"></div>
  <div class="dec-gates"   id="dec-gates"></div>
</div>

<!-- Row 1: at-a-glance trading status -->
<div class="row">
  <div class="card"><h2>Session</h2><div id="session-box"></div></div>
  <div class="card"><h2>Balance</h2><div id="bal-box"></div></div>
  <div class="card"><h2>Position / PnL</h2><div id="pos-box"></div></div>
</div>

<!-- Row 2: OR Strategy + extension level grid (the day's playbook) -->
<div class="row">
  <div class="card" style="flex:2">
    <h2 style="display:flex;justify-content:space-between;align-items:center;">
      OR Strategy &amp; extension levels (NQ · 65pt rungs · 50-tick proximity)
      <span id="or-meta" class="muted" style="font-size:11px;font-weight:400;text-transform:none;letter-spacing:0;"></span>
    </h2>
    <div id="or-box"></div>
    <div id="or-levels-box" style="margin-top:8px;"></div>
  </div>
</div>

<!-- Row 2a: Session conviction (Phase A — anchored 08:30 CT, SMA-integrated) -->
<div class="row">
  <div class="card" id="conv-card" style="flex:2;border-left:3px solid #9ece6a;padding:6px 10px;">
    <h2 style="display:flex;justify-content:space-between;align-items:center;margin:0;">
      <span>Session conviction <span class="muted" style="font-size:10px;font-weight:400;text-transform:none;letter-spacing:0;">anchored 08:30 CT · follow the trend</span></span>
      <span id="conv-trend"></span>
    </h2>
    <div id="conv-box" style="margin-top:4px;"></div>
  </div>
</div>

<!-- Row 2b: Pax decision agent — HIDDEN 2026-05-17 per user request.
     Server-side pax_decision() and CSV recorder still run; just no UI. -->
<div class="row" style="display:none;">
  <div class="card" id="pax-card" style="flex:2;border-left:3px solid #7aa2f7;">
    <h2 style="display:flex;justify-content:space-between;align-items:center;">
      <span>Pax agent (SIM) <span class="muted" style="font-size:10px;font-weight:400;text-transform:none;letter-spacing:0;">read-only · CSV logged · never live</span></span>
      <span style="display:flex;gap:8px;align-items:center;">
        <span id="pax-state-badge"></span>
        <label style="display:flex;align-items:center;gap:4px;font-size:10px;font-weight:400;text-transform:none;letter-spacing:0;cursor:pointer;color:#cfd6e4;">
          <input type="checkbox" id="pax-toggle" style="cursor:pointer;" />
          <span>enabled</span>
        </label>
        <label style="display:flex;align-items:center;gap:4px;font-size:10px;font-weight:400;text-transform:none;letter-spacing:0;cursor:pointer;color:#cfd6e4;">
          <input type="checkbox" id="pax-notify" style="cursor:pointer;" />
          <span>notify</span>
        </label>
      </span>
    </h2>
    <div id="pax-box"></div>
  </div>
</div>

<!-- Row 2c: Pax SIM trades — HIDDEN 2026-05-17 per user request.
     Sim engine still runs server-side, just no read-out card. -->
<div class="row" style="display:none;">
  <div class="card" id="sim-card" style="flex:2;border-left:3px solid #e0af68;padding:6px 10px;">
    <h2 style="display:flex;justify-content:space-between;align-items:center;margin:0;">
      <span>Pax SIM trades <span class="muted" style="font-size:10px;font-weight:400;text-transform:none;letter-spacing:0;">local sim engine · no live broker · D:\BookmapLogs\pax-trades.db</span></span>
      <span id="sim-pnl"></span>
    </h2>
    <div id="sim-box" style="margin-top:4px;"></div>
  </div>
</div>

<!-- Row 3: VWAP bands (full width context) -->
<div class="row">
  <div class="card" style="flex:2">
    <h2 style="display:flex;justify-content:space-between;align-items:center;">
      Session VWAP &amp; bands (anchor 08:30 CT)
      <span id="vwap-bias-badge" style="display:flex;gap:6px;align-items:center;"></span>
    </h2>
    <div id="vwap-box"></div>
  </div>
</div>

<!-- Row 4: trade gates — VWAP/OR alignment + flow regime -->
<div class="row">
  <div class="card"><h2>VWAP / OR Gate</h2><div id="gate-box"></div></div>
  <div class="card"><h2>Flow regime</h2><div id="flow-box"></div></div>
</div>

<!-- Row 4: institutional flow signals -->
<div class="row">
  <div class="card" style="flex:1.3">
    <h2 style="display:flex;justify-content:space-between;align-items:center;">Tape buckets (size flow) <span id="tape-buckets-bias"></span></h2>
    <div id="tape-buckets-box"></div>
  </div>
  <div class="card" style="flex:1">
    <h2 style="display:flex;justify-content:space-between;align-items:center;">LT liquidity (30s EWMA) <span id="lt-bias"></span></h2>
    <div id="lt-box"></div>
  </div>
</div>

<!-- Row 5: pull/stack indicator (Engineered Analytics style) -->
<div class="row">
  <div class="card" style="flex:2">
    <h2 style="display:flex;justify-content:space-between;align-items:center;">Pull / Stack (±10 ticks around BBO · BBO-reset + 1m / 3m / 15m) <span id="pull-stack-bias"></span></h2>
    <div id="pull-stack-box"></div>
  </div>
</div>

<!-- Hidden legacy momentum — kept off the screen but JS still populates so we don't break -->
<div id="mom-box" style="display:none;"></div>

<div class="row">
  <div class="card" style="flex:1.2;">
    <h2 style="display:flex;justify-content:space-between;align-items:center;">
      Orderbook · 5-pt buckets · 25 levels
      <span id="book-bias" style="font-size:11px;font-weight:400;letter-spacing:0;text-transform:none;"></span>
    </h2>
    <div id="book-box"></div>
  </div>
  <div class="card">
    <h2 style="display:flex;justify-content:space-between;align-items:center;">
      Tape — institutional flow
      <span id="tape-prints-bias" style="font-size:11px;font-weight:400;letter-spacing:0;text-transform:none;"></span>
    </h2>
    <div id="tape-box"></div>
  </div>
  <div class="card">
    <h2 style="display:flex;justify-content:space-between;align-items:center;">Micro events <span id="micro-bias"></span></h2>
    <div id="micro-box"></div>
  </div>
</div>

<div class="row" id="working-row">
  <div class="card" id="working-card" style="flex:1"><h2>Working orders</h2><div id="working-box"></div></div>
</div>

<div class="card"><h2>Recent fills</h2><div id="fills-box"></div></div>

<script>
const fmtP = (x) => (x === null || x === undefined || Number.isNaN(x)) ? '—' : Number(x).toFixed(2);
const fmtN = (x) => (x === null || x === undefined) ? '—' : Number(x).toLocaleString();
const sgn  = (x) => (x === null || x === undefined || Number.isNaN(x)) ? '—' : ((x > 0 ? '+' : '') + Number(x).toFixed(2));
function badge(state) {
  const m = {ACTIVE:'b-ok',AFTERNOON:'b-ok',ALLOW_LONG:'b-ok',ALLOW_SHORT:'b-block',
             OR_FORMING:'b-warn',PRE_MARKET:'b-warn',LATE_MORNING:'b-warn',CLOSE_RISK:'b-warn',POST_MARKET:'b-warn',
             CHOP:'b-block',BLOCKED:'b-block'};
  return m[state] || 'b-neutral';
}

// ─── Reusable trend-heatmap helpers ──────────────────────────────────────
// Same visual language as the OR Strategy card's header bar + row tints.
//   strength: number in [-1, +1].  +1 = max bull, -1 = max bear, 0 = chop.
//   opts.labels: optional label set { strongUp, up, leanUp, chop, leanDown,
//                                     down, strongDown }
//   opts.title:  prefix string for the tooltip
function mkTrendBar(strength, opts) {
  opts = opts || {};
  const L = Object.assign({
    strongUp:'STRONG BULL', up:'BULL', leanUp:'BULL LEAN',
    chop:'NEUTRAL',
    leanDown:'BEAR LEAN', down:'BEAR', strongDown:'STRONG BEAR'
  }, opts.labels || {});
  const s   = Math.max(-1, Math.min(1, Number(strength) || 0));
  const dir = s > 0.08 ? 1 : s < -0.08 ? -1 : 0;
  const mag = Math.abs(s);
  let from, mid, to, fg, border, glow, fill, label, arrow;
  if (dir > 0) {
    const a = (0.20 + mag*0.55).toFixed(2);
    from='rgba(35,39,51,0.85)';
    mid =`rgba(158,206,106,${(0.18 + mag*0.30).toFixed(2)})`;
    to  =`rgba(158,206,106,${a})`;
    fg  = mag>0.45 ? '#0d1a10' : '#cfd6e4';
    border=`rgba(158,206,106,${(0.45 + mag*0.5).toFixed(2)})`;
    glow  =`rgba(158,206,106,${(mag*0.45).toFixed(2)})`;
    fill='#9ece6a';
    label = mag>0.6 ? L.strongUp : mag>0.25 ? L.up : L.leanUp;
    arrow = mag>0.6 ? '⇈' : '↑';
  } else if (dir < 0) {
    const a = (0.20 + mag*0.55).toFixed(2);
    from=`rgba(247,118,142,${a})`;
    mid =`rgba(247,118,142,${(0.18 + mag*0.30).toFixed(2)})`;
    to  ='rgba(35,39,51,0.85)';
    fg  = mag>0.45 ? '#1a0d10' : '#cfd6e4';
    border=`rgba(247,118,142,${(0.45 + mag*0.5).toFixed(2)})`;
    glow  =`rgba(247,118,142,${(mag*0.45).toFixed(2)})`;
    fill='#f7768e';
    label = mag>0.6 ? L.strongDown : mag>0.25 ? L.down : L.leanDown;
    arrow = mag>0.6 ? '⇊' : '↓';
  } else {
    from='#232733'; mid='#2a3040'; to='#232733'; fg='#a9b1d6';
    border='#2a3550'; glow='transparent'; fill='#737994';
    label=L.chop; arrow='→';
  }
  const meterPct = Math.round(mag * 50);     // 0..50 (half-meter width)
  const fillW = meterPct + '%';
  const fillX = dir >= 0 ? '50%' : (50 - meterPct) + '%';
  const titlePrefix = opts.title || '';
  return (
    `<div class="trend-bar" title="${titlePrefix} strength ${s.toFixed(2)}" ` +
    `style="--bar-from:${from};--bar-mid:${mid};--bar-to:${to};--bar-fg:${fg};` +
    `--bar-border:${border};--bar-glow:${glow};--bar-fill:${fill};">` +
    `<span class="tb-arrow">${arrow}</span>` +
    `<span class="tb-label">${label}</span>` +
    `<span class="tb-meter"><span class="tb-fill" style="--fill-w:${fillW};--fill-x:${fillX};"></span></span>` +
    `<span class="tb-pct">${(s*100).toFixed(0).replace('-','−')}%</span>` +
    `</div>`
  );
}

// ─── Orderbook 5pt bucket collector ──────────────────────────────────────
// Session-anchored EMA per bucket key. Starts collecting when the dashboard
// JS first loads (≈ when the MCP / dashboard process boots, since this script
// only runs in the browser tab that connects to the live server).
//
// Each refresh() call:
//   1) Aggregates the current bk.asks / bk.bids into 5-pt buckets (key = floor(price/5)).
//   2) Decays each bucket's stored EMA by exp(-ln2·Δt/HL).
//   3) Adds the current observed size into the EMA with alpha = 1 - exp(...).
//   4) Tracks first-seen timestamp per bucket so we can show "age" = how long
//      this bucket has been carrying meaningful liquidity. Sticky liquidity =
//      legit support/resistance.  Brand-new liquidity = candidate spoof.
const OB_BUCKET_PTS    = 2.5;
const OB_EMA_HL_MS     = 90 * 1000;   // 90s half-life — long enough for "session" feel
const OB_MIN_SIZE      = 1;           // ignore noise
const OB_VISIBLE_PER_SIDE = 12;       // 12+12 = 24 rows ≈ original "top 25" cadence (~60 pts each side)
const OB_SESSION = {
    startMs:    Date.now(),
    bidEMA:     new Map(),    // bucket key (int) → EMA size
    askEMA:     new Map(),
    bidFirstMs: new Map(),    // bucket key → first observation timestamp
    askFirstMs: new Map(),
    lastUpdateMs: 0,
};

function obBucketKey(price) {
    return Math.floor(price / OB_BUCKET_PTS);
}
function obBucketPrice(key) {
    return key * OB_BUCKET_PTS;   // bottom edge of bucket
}
function obAggregate(levels) {
    // {price,size}[] → Map<bucketKey, size>
    const out = new Map();
    (levels || []).forEach(l => {
        if (!l || !(l.size > 0) || !(l.price > 0)) return;
        const k = obBucketKey(l.price);
        out.set(k, (out.get(k) || 0) + l.size);
    });
    return out;
}
function obUpdateEMA(emaMap, firstMap, currentMap, dtMs, now) {
    const decay = Math.pow(0.5, dtMs / OB_EMA_HL_MS);
    const alpha = 1.0 - decay;
    // Decay all known buckets (including those that disappeared from book)
    for (const [k, v] of emaMap) emaMap.set(k, v * decay);
    // Inject current observations
    for (const [k, sz] of currentMap) {
        if (sz < OB_MIN_SIZE) continue;
        emaMap.set(k, (emaMap.get(k) || 0) + alpha * sz);
        if (!firstMap.has(k)) firstMap.set(k, now);
    }
    // Garbage-collect ONLY buckets that have decayed AND aren't being observed
    // right now. Without the !currentMap.has(k) guard, fresh observations whose
    // first-tick EMA ≈ α·size < 0.5 would be deleted before they can accumulate.
    for (const [k, v] of emaMap) {
        if (v < 0.5 && !currentMap.has(k)) {
            emaMap.delete(k); firstMap.delete(k);
        }
    }
}

// Per-row background tint. Returns the inline style="..." attribute (incl. leading space)
// suitable for <tr ... > or <div ... > tags.  intensity is the magnitude in 0..1.
function rowHeat(rowSign, intensity) {
  if (!rowSign || !(intensity > 0)) return '';
  const m  = Math.min(1, Math.max(0, intensity));
  const a  = (m * 0.55).toFixed(2);
  const af = (m * 0.20).toFixed(2);
  const hue = rowSign > 0 ? '158,206,106' : '247,118,142';
  // Bull rows: tint stronger on LEFT, fade right.  Bear rows: stronger on RIGHT.
  const stops = rowSign > 0
    ? `rgba(${hue},${a}) 0%, rgba(${hue},${af}) 60%, transparent 100%`
    : `transparent 0%, rgba(${hue},${af}) 40%, rgba(${hue},${a}) 100%`;
  return ` style="--row-bg:linear-gradient(90deg, ${stops});"`;
}

async function refresh() {
  let s;
  try {
    const r = await fetch('/api/snapshot', {cache:'no-store'});
    s = await r.json();
  } catch(e) {
    document.getElementById('dot').className = 'dot err';
    document.getElementById('hdr').textContent = 'fetch failed: ' + e.message;
    return;
  }
  const dot = document.getElementById('dot');
  const hdr = document.getElementById('hdr');
  const ac = document.getElementById('alias-clock');
  if (s.health === 'ok' && s.alias) {
    dot.className = 'dot ok';
    hdr.textContent = 'BRIDGE OK';
    ac.textContent = ' · ' + s.alias + ' · ' + (s.ts || '');
  } else if (s.health === 'ok') {
    dot.className = 'dot warn';
    hdr.textContent = 'BRIDGE OK · no instrument attached';
    return;
  } else {
    dot.className = 'dot err';
    hdr.textContent = (s.health || 'error').toUpperCase() + ': ' + (s.error || '');
    return;
  }

  // news banner
  const nb = document.getElementById('news-banner');
  if (s.gates && s.gates.news && s.gates.news.blocked) {
    nb.className = 'macro-banner';
    nb.textContent = '⚠ MACRO BLACKOUT: ' + s.gates.news.label;
  } else { nb.className = ''; nb.textContent = ''; }

  // decision banner
  const d = s.decision || {};
  const decBanner = document.getElementById('decision-banner');
  const actCls = {
    'ENTER_LONG':  'd-enter-long',
    'ENTER_SHORT': 'd-enter-short',
    'WAIT':        'd-wait',
    'STAND_DOWN':  'd-stand-down',
    'EXIT':        'd-exit'
  }[d.decision] || 'd-wait';
  decBanner.className = 'decision-banner ' + actCls;
  document.getElementById('dec-action').textContent = d.decision || '—';
  document.getElementById('dec-conf').textContent = (d.confidence || '') + (d.size ? ' · size ' + d.size : '');
  let tgt = '';
  if (d.entry || d.stop || d.target1) {
    tgt = `entry ${fmtP(d.entry)} · stop ${fmtP(d.stop)} · T1 ${fmtP(d.target1)} · T2 ${fmtP(d.target2)}`;
  }
  document.getElementById('dec-targets').textContent = tgt;
  document.getElementById('dec-reasons').textContent = (d.reasons || []).join(' → ');
  const dgates = d.gates || {};
  const gateLine = ['session','news','or_bias','vwap_or','stretch','momentum','position']
    .map(k => k + ': ' + (dgates[k] === undefined ? '—' : dgates[k]))
    .join('  |  ');
  document.getElementById('dec-gates').textContent = gateLine;

  // session
  const ses = s.gates.session;
  document.getElementById('session-box').innerHTML =
    `<div class="kv"><span>state</span><span class="badge ${badge(ses.code)}">${ses.label}</span></div>` +
    `<div class="kv"><span>now</span><span class="v">${ses.now_local} <span class="muted">(${ses.now_et})</span></span></div>` +
    `<div class="kv"><span>news</span>${s.gates.news.blocked?'<span class="badge b-block">BLOCKED</span>':'<span class="badge b-ok">clear</span>'}</div>`;

  // vwap-or gate — categorical gate state maps to directional strength.
  //   ALLOW_LONG  → +1 (bull permitted)
  //   ALLOW_SHORT → -1 (bear permitted)
  //   BLOCKED     →  0 (neither — display as CHOP)
  //   UNKNOWN     →  0 (no data)
  const g = s.gates.vwap_or;
  const gateStrength = g.state === 'ALLOW_LONG'  ? +1
                     : g.state === 'ALLOW_SHORT' ? -1
                     : 0;
  const gateBar = mkTrendBar(gateStrength, {
    title: `VWAP/OR gate · ${g.state} ·`,
    labels: {
      strongUp:'LONGS PERMITTED', up:'LONGS PERMITTED', leanUp:'LONGS PERMITTED',
      chop: g.state === 'BLOCKED' ? 'GATE BLOCKED' : 'GATE UNKNOWN',
      leanDown:'SHORTS PERMITTED', down:'SHORTS PERMITTED', strongDown:'SHORTS PERMITTED'
    }
  });
  document.getElementById('gate-box').innerHTML =
    gateBar +
    `<div class="kv"><span>VWAP</span><span class="v">${fmtP(s.vwap)}</span></div>` +
    `<div class="kv"><span>gate</span><span class="badge ${badge(g.state)}">${g.state}</span></div>` +
    `<div class="muted" style="font-size:11px;margin-top:4px;">${g.reason || ''}</div>`;

  // session VWAP + bands
  const vobj = s.vwap_obj;
  const vbox = document.getElementById('vwap-box');
  if (!vobj || vobj._error) {
    const msg = (vobj && vobj._error) ? vobj._error : 'no /vwap endpoint — redeploy bridge jar';
    vbox.innerHTML = '<span class="err">'+msg+'</span>';
  } else if (!vobj.samples || vobj.vwap === null || vobj.vwap === undefined) {
    vbox.innerHTML = '<span class="muted">no trades this session yet — waiting on 08:30 CT open</span>';
  } else {
    const last = vobj.lastTradePrice;
    const dev = last !== null && last !== undefined && vobj.stddev > 0
      ? (last - vobj.vwap) / vobj.stddev : null;
    function devBadge(d) {
      if (d === null) return '<span class="badge b-neutral">—</span>';
      const a = Math.abs(d);
      if (a > 3) return `<span class="badge b-block">${d>=0?'+':'-'}${a.toFixed(2)}σ EXTREME</span>`;
      if (a > 2) return `<span class="badge b-warn">${d>=0?'+':'-'}${a.toFixed(2)}σ stretched</span>`;
      if (a > 1) return `<span class="badge b-info">${d>=0?'+':'-'}${a.toFixed(2)}σ outside 1σ</span>`;
      return `<span class="badge b-ok">${d>=0?'+':''}${d.toFixed(2)}σ fair value</span>`;
    }
    function lvl(label, price, cls) {
      const mark = (last !== null && last !== undefined && Math.abs(last - price) < (vobj.stddev*0.05))
        ? ' ← last' : '';
      return `<div class="kv"><span>${label}</span><span class="v ${cls||''}">${fmtP(price)}${mark}</span></div>`;
    }
    const left =
      lvl('+3σ', vobj.upper3, 'sell') +
      lvl('+2σ', vobj.upper2, 'sell') +
      lvl('+1σ', vobj.upper1, 'sell') +
      `<div class="kv"><span><b>VWAP</b></span><span class="v"><b>${fmtP(vobj.vwap)}</b></span></div>` +
      lvl('-1σ', vobj.lower1, 'buy') +
      lvl('-2σ', vobj.lower2, 'buy') +
      lvl('-3σ', vobj.lower3, 'buy');
    const eth = vobj.eth || {};
    const ethDev = (last !== null && last !== undefined && eth.stddev > 0)
      ? (last - eth.vwap) / eth.stddev : null;
    const lastBig = `<div style="font-size:22px;font-weight:700;color:#cfd6e4;margin-bottom:4px;">${fmtP(last)}</div>`;
    const rthLine = `<div class="kv"><span><span style="color:#7aa2f7;font-weight:600;">RTH</span> ${fmtP(vobj.vwap)} ±${fmtP(vobj.stddev)}</span>${devBadge(dev)}</div>`;
    const ethLine = `<div class="kv"><span><span style="color:#e0af68;font-weight:600;">ETH</span> ${fmtP(eth.vwap)} ±${fmtP(eth.stddev)}</span>${devBadge(ethDev)}</div>`;
    const meta = `<div class="muted" style="font-size:10px;margin-top:4px;line-height:1.5;">` +
      `RTH: ${fmtN(vobj.samples)} prints · since ${(vobj.sessionStartCt||'').replace('T',' ').substring(11,16)}<br/>` +
      `ETH: ${fmtN(eth.samples)} prints · since ${(eth.sessionStartCt||'').replace('T',' ').substring(0,16).replace('T',' ')}` +
      `</div>`;
    const right = lastBig + rthLine + ethLine + meta;

    // σ-band sparkline + volume profile histogram on the right edge
    function renderSpark(vo, trades, vp) {
      const W = 780, H = 170;
      const padL = 44, padT = 6, padB = 6;
      const histW = 70;                       // histogram strip width on the right
      const x0 = padL, x1 = W - histW - 8;    // sparkline plot area
      const hx0 = W - histW, hx1 = W;         // histogram bar area
      const y0 = padT, y1 = H - padB;
      const sd = vo.stddev;
      if (!sd || sd <= 0) return '';
      const yLo = vo.vwap - 3.3 * sd, yHi = vo.vwap + 3.3 * sd;
      const yScale = (price) => y0 + ((yHi - price) / (yHi - yLo)) * (y1 - y0);
      const tr = (trades || []).slice(0, 200).slice().reverse();
      const n = tr.length;
      if (n < 2) {
        return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:${H}px;margin-top:8px;">
          <text x="50%" y="50%" fill="#737994" text-anchor="middle" font-size="11">waiting on trades to build sparkline</text></svg>`;
      }
      const xScale = (i) => x0 + (i / (n - 1)) * (x1 - x0);
      function band(price, color, label, weight) {
        const y = yScale(price);
        if (!isFinite(y) || y < y0 - 2 || y > y1 + 2) return '';
        return `<line x1="${x0}" x2="${x1}" y1="${y}" y2="${y}" stroke="${color}" stroke-width="${weight||1}" stroke-dasharray="${weight===1.5?'':'3,3'}" opacity="0.7"/>` +
               `<text x="${x0-4}" y="${y+3}" fill="${color}" text-anchor="end" font-size="9" font-variant-numeric="tabular-nums">${label} ${Number(price).toFixed(2)}</text>`;
      }
      let lines = '';
      lines += band(vo.upper3, '#f7768e', '+3σ');
      lines += band(vo.upper2, '#e0af68', '+2σ');
      lines += band(vo.upper1, '#7aa2f7', '+1σ');
      lines += band(vo.vwap,   '#9ece6a', 'VWAP', 1.5);
      lines += band(vo.lower1, '#7aa2f7', '-1σ');
      lines += band(vo.lower2, '#e0af68', '-2σ');
      lines += band(vo.lower3, '#f7768e', '-3σ');

      // Price polyline
      let pts = '';
      for (let i = 0; i < n; i++) {
        if (i > 0) pts += ' ';
        pts += xScale(i).toFixed(1) + ',' + yScale(tr[i].price).toFixed(1);
      }
      const poly = `<polyline points="${pts}" fill="none" stroke="#cfd6e4" stroke-width="1.3" stroke-linejoin="round"/>`;

      // Last trade marker
      const lt = tr[n - 1];
      const lx = xScale(n - 1), ly = yScale(lt.price);
      const dotColor = lt.side === 'buy' ? '#9ece6a' : '#f7768e';
      const marker = `<circle cx="${lx}" cy="${ly}" r="3.5" fill="${dotColor}" stroke="#0b0e13" stroke-width="1"/>` +
                     `<text x="${lx-6}" y="${ly+3}" fill="${dotColor}" text-anchor="end" font-size="10" font-variant-numeric="tabular-nums">${Number(lt.price).toFixed(2)}</text>`;

      // Volume profile histogram on the right edge — original variable-width
      // bars (bar width ∝ volume) preserves the magnitude info at a glance.
      // VPOC blue, in-VA blue-gray, outside-VA dark gray.
      let histSvg = '';
      if (vp && !vp._error && vp.levels && vp.levels.length > 0) {
        const visible = vp.levels.filter(l => l.price >= yLo && l.price <= yHi);
        let maxVol = 0;
        for (const l of visible) if (l.volume > maxVol) maxVol = l.volume;
        if (maxVol > 0) {
          const totalLevels = visible.length;
          const barH = Math.max(2, ((y1 - y0) / Math.max(totalLevels, 40)));
          let bars = '';
          for (const l of visible) {
            const y = yScale(l.price);
            const w = (l.volume / maxVol) * histW;
            const isVpoc = Math.abs(l.price - vp.vpoc) < 0.001;
            const inVA = l.price >= vp.val && l.price <= vp.vah;
            const fill = isVpoc ? '#7aa2f7' : (inVA ? '#3a4860' : '#232733');
            bars += `<rect x="${hx0}" y="${(y - barH/2).toFixed(1)}" width="${w.toFixed(1)}" height="${barH.toFixed(1)}" fill="${fill}"/>`;
          }
          // VPOC / VAH / VAL labels on the right edge
          function vpLine(price, color, lbl) {
            const y = yScale(price);
            if (!isFinite(y) || y < y0 - 2 || y > y1 + 2) return '';
            return `<line x1="${hx0}" x2="${hx1}" y1="${y}" y2="${y}" stroke="${color}" stroke-width="1" opacity="0.9"/>` +
                   `<text x="${hx1-2}" y="${y+3}" fill="${color}" text-anchor="end" font-size="9" font-variant-numeric="tabular-nums">${lbl}</text>`;
          }
          let vpLines = '';
          vpLines += vpLine(vp.vpoc, '#7aa2f7', 'VPOC ' + Number(vp.vpoc).toFixed(2));
          vpLines += vpLine(vp.vah,  '#a9b1d6', 'VAH ' + Number(vp.vah).toFixed(2));
          vpLines += vpLine(vp.val,  '#a9b1d6', 'VAL ' + Number(vp.val).toFixed(2));
          histSvg = bars + vpLines;
        }
      }

      return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;height:${H}px;margin-top:8px;display:block;">${histSvg}${lines}${poly}${marker}</svg>`;
    }

    const sparkSvg = renderSpark(vobj, s.trades, s.volume_profile);

    // Volume profile summary stats below the chart — RTH + ETH side by side
    let vpSummary = '';
    const vp = s.volume_profile;
    if (vp && !vp._error) {
      const rthLine = (vp.totalVolume > 0)
        ? `<span style="color:#7aa2f7;">RTH</span>: ${fmtN(vp.totalVolume)} ctr · VPOC ${fmtP(vp.vpoc)} · VA ${fmtP(vp.val)}–${fmtP(vp.vah)} (${Math.round((vp.valueAreaPct||0.7)*100)}%)`
        : '<span style="color:#7aa2f7;">RTH</span>: <span class="muted">no RTH trades yet (open 08:30 CT)</span>';
      const ev = vp.eth || {};
      const ethLine = (ev.totalVolume > 0)
        ? `<span style="color:#e0af68;">ETH</span>: ${fmtN(ev.totalVolume)} ctr · VPOC ${fmtP(ev.vpoc)} · VA ${fmtP(ev.val)}–${fmtP(ev.vah)} (${Math.round((ev.valueAreaPct||0.7)*100)}%)`
        : '<span style="color:#e0af68;">ETH</span>: <span class="muted">no overnight trades</span>';
      // Add VP bias badge next to the RTH line
      const vpb = s.vp_bias;
      let vpBadge = '';
      if (vpb && vpb.label) {
        const cls = vpb.label === 'BULLISH' ? 'b-ok' : vpb.label === 'BEARISH' ? 'b-block' : 'b-neutral';
        const sign = vpb.score >= 0 ? '+' : '';
        const c = vpb.components || {};
        const tip = (vpb.reasons || []).join(' · ');
        vpBadge = `<span class="badge ${cls}" style="font-size:10px;margin-left:6px;" title="${tip}">VP ${vpb.label} ${sign}${Math.round((vpb.score||0)*100)}</span>` +
                  ` <span class="muted" style="font-size:10px;">${c.va_state || ''} · HVN ${c.hvn_count||0}/LVN ${c.lvn_count||0}</span>`;
      }
      vpSummary = `<div class="muted" style="font-size:11px;margin-top:4px;line-height:1.6;">${rthLine}${vpBadge}<br/>${ethLine}</div>`;
    }

    vbox.innerHTML = '<div style="display:flex;gap:14px;"><div style="flex:1;">'+left+'</div><div style="flex:1;">'+right+'</div></div>' + sparkSvg + vpSummary;

    // VWAP bias badge in the card title
    const vwb = s.vwap_bias;
    const vwBadgeBox = document.getElementById('vwap-bias-badge');
    if (vwBadgeBox) {
      if (!vwb || !vwb.label) {
        vwBadgeBox.innerHTML = '';
      } else {
        const cls = vwb.label === 'BULLISH' ? 'b-ok' : vwb.label === 'BEARISH' ? 'b-block' : 'b-neutral';
        const sign = vwb.score >= 0 ? '+' : '';
        const tip = (vwb.reasons || []).join(' · ');
        const c = vwb.components || {};
        const sub = `σz ${c.sigma_z>=0?'+':''}${c.sigma_z||0} · ${c.regime||''}`;
        vwBadgeBox.innerHTML =
          `<span class="muted" style="font-size:10px;font-weight:400;text-transform:none;letter-spacing:0;">${sub}</span>` +
          `<span class="badge ${cls}" style="font-size:11px;" title="${tip}">VWAP ${vwb.label} ${sign}${Math.round((vwb.score||0)*100)}</span>`;
      }
    }
  }

  // OR bias header (compact — keeps the upstream CSV summary)
  const or = s.or_row;
  if (!or) {
    document.getElementById('or-box').innerHTML = '<span class="muted">no OR-Strategy CSV found</span>';
  } else {
    document.getElementById('or-box').innerHTML =
      `<div style="display:flex;gap:14px;flex-wrap:wrap;align-items:baseline;font-size:12px;">` +
        `<span class="muted">bias</span><span class="v">${or.bias || '—'}</span>` +
        `<span class="muted">action</span><span class="badge b-info">${or.action || '—'}</span>` +
        `<span class="muted">conf</span><span class="v">${or.confidence || '—'}</span>` +
        `<span class="muted">score</span><span class="v">${or.score || '—'}/${or.maxScore || '—'}</span>` +
        `<span class="muted">loc</span><span class="v">${or.location || '—'}</span>` +
      `</div>`;
  }

  // OR Levels + extension grid (the Pax playbook for the day)
  const ol = s.or_levels;
  const olBox = document.getElementById('or-levels-box');
  const olMeta = document.getElementById('or-meta');
  if (!ol) {
    olBox.innerHTML = '<span class="muted">OR not set yet (need orHigh / orLow from upstream)</span>';
    olMeta.textContent = '';
  } else {
    const inProx = !!ol.inProximity;
    const proxLevel = (ol.levels || []).find(l => l.proximity);
    const proxTag = inProx && proxLevel
        ? ` <span class="live-pulse">⬤ LIVE @ ${proxLevel.label} ${fmtP(proxLevel.price)}</span>`
        : '';

    // ── Trend heatmap: blend bias score (flow) + conviction score (Phase A) ──
    // strength ∈ [-1, +1]; sign = direction, |x| = intensity.
    const _bias = Number((s.flow && s.flow.biasScore) || 0);
    const _conv = Number((s.conviction && s.conviction.score) || 0);
    const trendStrength = Math.max(-1, Math.min(1, (_bias * 0.6) + (_conv * 0.4)));
    const trendDir = trendStrength > 0.08 ? 1 : (trendStrength < -0.08 ? -1 : 0);
    const trendMag = Math.abs(trendStrength);            // 0..1
    // Header band — full-width gradient bar w/ centered meter.
    let barFrom, barMid, barTo, barFg, barBorder, barGlow, barFill;
    let barLabel, barArrow;
    if (trendDir > 0) {
      // bull → fade from neutral on left to saturated green on right
      const a = (0.20 + trendMag * 0.55).toFixed(2);
      barFrom  = 'rgba(35,39,51,0.85)';
      barMid   = `rgba(158,206,106,${(0.18 + trendMag*0.30).toFixed(2)})`;
      barTo    = `rgba(158,206,106,${a})`;
      barFg    = trendMag > 0.45 ? '#0d1a10' : '#cfd6e4';
      barBorder= `rgba(158,206,106,${(0.45 + trendMag*0.5).toFixed(2)})`;
      barGlow  = `rgba(158,206,106,${(trendMag*0.45).toFixed(2)})`;
      barFill  = '#9ece6a';
      barLabel = trendMag > 0.6 ? 'STRONG BULL TREND'
               : trendMag > 0.25 ? 'BULL TREND' : 'BULL LEAN';
      barArrow = trendMag > 0.6 ? '⇈' : '↑';
    } else if (trendDir < 0) {
      const a = (0.20 + trendMag * 0.55).toFixed(2);
      barFrom  = `rgba(247,118,142,${a})`;
      barMid   = `rgba(247,118,142,${(0.18 + trendMag*0.30).toFixed(2)})`;
      barTo    = 'rgba(35,39,51,0.85)';
      barFg    = trendMag > 0.45 ? '#1a0d10' : '#cfd6e4';
      barBorder= `rgba(247,118,142,${(0.45 + trendMag*0.5).toFixed(2)})`;
      barGlow  = `rgba(247,118,142,${(trendMag*0.45).toFixed(2)})`;
      barFill  = '#f7768e';
      barLabel = trendMag > 0.6 ? 'STRONG BEAR TREND'
               : trendMag > 0.25 ? 'BEAR TREND' : 'BEAR LEAN';
      barArrow = trendMag > 0.6 ? '⇊' : '↓';
    } else {
      barFrom='#232733'; barMid='#2a3040'; barTo='#232733';
      barFg='#a9b1d6'; barBorder='#2a3550'; barGlow='transparent';
      barFill='#737994'; barLabel='CHOP / NO TREND'; barArrow='→';
    }
    // meter: bipolar fill — center is 0; fills right for bull, left for bear.
    const meterPctRaw = Math.round(trendMag * 50);    // 0..50 (half-width)
    const fillW = meterPctRaw + '%';
    const fillX = trendDir >= 0 ? '50%' : (50 - meterPctRaw) + '%';

    const headerBar =
      `<div class="trend-bar" title="bias ${_bias.toFixed(2)} · conv ${_conv.toFixed(2)} · strength ${trendStrength.toFixed(2)}" ` +
      `style="--bar-from:${barFrom};--bar-mid:${barMid};--bar-to:${barTo};--bar-fg:${barFg};` +
      `--bar-border:${barBorder};--bar-glow:${barGlow};--bar-fill:${barFill};">` +
      `<span class="tb-arrow">${barArrow}</span>` +
      `<span class="tb-label">${barLabel}</span>` +
      `<span class="tb-meter"><span class="tb-fill" style="--fill-w:${fillW};--fill-x:${fillX};"></span></span>` +
      `<span class="tb-pct">${(trendStrength*100).toFixed(0).replace('-','−')}%</span>` +
      `</div>`;

    // or-meta keeps the OR/width/mid line + LIVE pulse only.
    olMeta.innerHTML =
      `OR ${fmtP(ol.orLow)} ↔ ${fmtP(ol.orHigh)} · width ${ol.orWidthPts.toFixed(2)} pts · mid ${fmtP(ol.mid)} · prox ±${ol.proxPts.toFixed(2)} pts` + proxTag;

    const decClass = (d) => {
      if (d === 'ENTER_LONG_FOLLOW' || d === 'FOLLOW_LONG')   return 'follow-bull';
      if (d === 'ENTER_SHORT_FOLLOW' || d === 'FOLLOW_SHORT') return 'follow-bear';
      if (d === 'ENTER_LONG_FADE' || d === 'FADE_LONG')       return 'fade-bull';
      if (d === 'ENTER_SHORT_FADE' || d === 'FADE_SHORT')     return 'fade-bear';
      return 'wait';
    };
    const lblClass = (lvl) => {
      const dir = (lvl.composite && lvl.composite.direction) || lvl.decision || '';
      if (dir.indexOf('LONG') >= 0) return 'bull';
      if (dir.indexOf('SHORT') >= 0) return 'bear';
      return 'flat';
    };
    const expanded = (lvl) => {
      const comp = lvl.composite || {};
      const drivers = comp.drivers || [];
      const driverHtml = drivers.length
        ? drivers.map(d => {
            const v = Number(d.score || 0);
            const w = Number(d.weight || 0);
            return `<div><div class="ck">${d.name}</div><div class="cv" title="${d.reason || ''}">${v>=0?'+':''}${v.toFixed(2)} @ ${w.toFixed(2)}</div></div>`;
          }).join('')
        : '';
      const c = lvl.components || {};
      return `<div class="lvl-detail">` +
        driverHtml +
        `<div><div class="ck">PS BBO z</div><div class="cv">${c.ps_bbo>=0?'+':''}${c.ps_bbo}</div></div>` +
        `<div><div class="ck">PS rot</div><div class="cv">${c.ps_rot} (${c.ps_rot_mag})</div></div>` +
        `<div><div class="ck">LT lean</div><div class="cv">${c.lt>=0?'+':''}${c.lt}</div></div>` +
        `<div><div class="ck">Tape</div><div class="cv">${c.tape>=0?'+':''}${c.tape}</div></div>` +
        `<div><div class="ck">Micro</div><div class="cv">${c.micro>=0?'+':''}${c.micro}</div></div>` +
        `<div><div class="ck">VWAP pen</div><div class="cv">${c.vwap_stretch}</div></div>` +
        `<div><div class="ck">VP ctx</div><div class="cv">${c.vp_ctx || '—'}</div></div>` +
        `</div>`;
    };

    let html = '';
    // Header trend bar — full width, always visible
    html += headerBar;
    if (ol.middleLock) {
      html += `<div class="middle-lock">⚠ MIDDLE LOCK — price inside OR, no proximity to any level. STAND DOWN.</div>`;
    }
    html += `<div class="lvl-grid">`;
    ol.levels.forEach(lvl => {
      const cls = lvl.proximity ? 'proximity' : '';
      const distSign = lvl.distance >= 0 ? '+' : '';
      const comp = lvl.composite || {};
      const compScore = (typeof comp.score === 'number') ? comp.score : Number(lvl.score || 0);
      const compConf = (typeof comp.confidence === 'number') ? comp.confidence : Number(lvl.confidence || 0);
      const compDir = comp.direction || lvl.decision || 'WAIT';
      const confPct = Math.round(compConf * 100);
      const barW = Math.max(2, Math.round(compConf * 50));
      // Heatmap row background uses the same per-magnet composite as the row text.
      let heatCls = '', heatStyle = '';
      const rowDir = compScore > 0.08 ? 1 : (compScore < -0.08 ? -1 : 0);
      const rowMag = Math.abs(compScore);
      if (rowDir !== 0 && rowMag > 0.08) {
        const alphaN = rowMag * 0.55;
        const a = alphaN.toFixed(2);
        const af = (alphaN * 0.35).toFixed(2);
        const hue = rowDir > 0 ? '158,206,106' : '247,118,142';
        const stops = rowDir > 0
          ? `rgba(${hue},${a}) 0%, rgba(${hue},${af}) 60%, transparent 100%`
          : `transparent 0%, rgba(${hue},${af}) 40%, rgba(${hue},${a}) 100%`;
        heatCls = 'heat';
        heatStyle = ` style="background:linear-gradient(90deg, ${stops});"`;
      }
      html += `<div class="lvl-row ${cls} ${heatCls}"${heatStyle}>`;
      html += `<span class="lvl-lbl ${lblClass(lvl)}">${lvl.label}</span>`;
      html += `<span class="lvl-price">${fmtP(lvl.price)}</span>`;
      html += `<span class="lvl-dist">${distSign}${lvl.distance.toFixed(2)} pts</span>`;
      html += `<span class="lvl-dec ${decClass(compDir)}">${compDir.replace('ENTER_','').replace('_',' ')}</span>`;
      const driverReasons = (comp.drivers || []).map(d => `${d.name}: ${d.reason}`);
      const warnings = comp.warnings || [];
      const rsnParts = driverReasons.length ? driverReasons : (lvl.reasons || []);
      const rsn = rsnParts.slice(0,4).join(' · ');
      html += `<span class="lvl-rsn" title="${rsnParts.concat(warnings).join(' | ')}">${rsn}</span>`;
      html += `<span class="lvl-conf"><span class="bar-fill" style="width:${barW}px;"></span> ${confPct}%</span>`;
      if (lvl.proximity) html += expanded(lvl);
      html += `</div>`;
    });
    html += `</div>`;
    olBox.innerHTML = html;
  }

  // ─── Session conviction (Phase A) ──────────────────────────────────
  const conv = s.conviction;
  const convBox = document.getElementById('conv-box');
  const convTrend = document.getElementById('conv-trend');
  if (!conv || conv._error) {
    convBox.innerHTML = '<span class="muted">conviction not available (need /momentum data)</span>';
    convTrend.innerHTML = '';
  } else {
    const score = conv.score || 0;
    const trend = conv.trend || 'CHOP';
    const traj  = conv.trajectory || 'FLAT';
    const trendCls = trend.indexOf('BULL') >= 0 ? 'b-ok'
                   : trend.indexOf('BEAR') >= 0 ? 'b-block'
                   : trend === 'CHOP' ? 'b-block' : 'b-neutral';
    const trajArrow = ({"RISING_STRONG":"⇈","RISING":"↗","FLAT":"→","FALLING":"↘","FALLING_STRONG":"⇊","WARMUP":"⋯"})[traj] || '·';
    const mins = Math.round((conv.durationSec || 0) / 60);
    convTrend.innerHTML = `<span class="badge ${trendCls}">${trend}</span> <span class="muted" style="font-size:10px;">${trajArrow} ${traj}</span> <span class="muted" style="font-size:10px;">· ${mins}m</span>`;

    // Heatwave header — strength = composite score in [-1,+1]
    const convBar = mkTrendBar(score, {
      title: `session conviction · ${trend} · ${traj} ·`,
      labels: {
        strongUp:'STRONG BULL CONVICTION', up:'BULL CONVICTION', leanUp:'BULL LEAN',
        chop:'CHOP / NO CONVICTION',
        leanDown:'BEAR LEAN', down:'BEAR CONVICTION', strongDown:'STRONG BEAR CONVICTION'
      }
    });

    // Horizontal score bar — center is 0, extents ±1
    const pct = Math.round(score * 100);
    const w = Math.min(Math.abs(pct), 100);
    const cls = score >= 0 ? 'imb-pos' : 'imb-neg';
    let bar = `<div style="display:flex;align-items:center;gap:8px;font-variant-numeric:tabular-nums;">`;
    bar += `<span class="${score>=0?'buy':'sell'}" style="min-width:50px;font-size:13px;font-weight:700;">${score>=0?'+':''}${pct}</span>`;
    bar += `<div style="flex:1;position:relative;height:8px;background:#1a1f2b;border-radius:2px;">`;
    bar += `<div style="position:absolute;left:50%;top:0;bottom:0;width:1px;background:#737994;"></div>`;
    if (score >= 0) {
      bar += `<div style="position:absolute;left:50%;top:0;bottom:0;width:${w/2}%;background:#9ece6a;border-radius:0 2px 2px 0;"></div>`;
    } else {
      bar += `<div style="position:absolute;right:50%;top:0;bottom:0;width:${w/2}%;background:#f7768e;border-radius:2px 0 0 2px;"></div>`;
    }
    bar += `</div>`;
    bar += `</div>`;

    // Component breakdown (compact)
    const c = conv.components || {};
    const cmpLines = ['regime','bias','vwap','vp','slope','level'].map(k => {
      const v = c[k] || 0;
      const sgn = v >= 0 ? '+' : '';
      const col = v > 0.1 ? '#9ece6a' : v < -0.1 ? '#f7768e' : '#737994';
      return `<span title="${k}: ${v}" style="color:${col};margin-right:6px;">${k} ${sgn}${Number(v).toFixed(2)}</span>`;
    }).join('');
    convBox.innerHTML = convBar + bar + `<div class="muted" style="font-size:10px;margin-top:3px;">${cmpLines}</div>`;
  }

  // ─── Pax agent (SIM — read-only, CSV-logged) ──────────────────────────
  const paxToggle = document.getElementById('pax-toggle');
  const paxCard   = document.getElementById('pax-card');
  const paxBox    = document.getElementById('pax-box');
  const paxBadge  = document.getElementById('pax-state-badge');
  // Read persisted toggle state (default ON)
  if (!paxToggle.dataset.init) {
    paxToggle.checked = (localStorage.getItem('paxEnabled') !== '0');
    paxToggle.addEventListener('change', () => {
      localStorage.setItem('paxEnabled', paxToggle.checked ? '1' : '0');
    });
    paxToggle.dataset.init = '1';
  }
  if (!paxToggle.checked) {
    paxBox.innerHTML = '<span class="muted">disabled — re-enable above to resume signal display (CSV recording also paused)</span>';
    paxBadge.innerHTML = '<span class="badge b-neutral">OFF</span>';
  } else {
    const pax = s.pax;
    if (!pax || pax._error) {
      paxBox.innerHTML = '<span class="err">'+((pax && pax._error) ? pax._error : 'no pax data')+'</span>';
      paxBadge.innerHTML = '<span class="badge b-warn">ERR</span>';
    } else {
      const dec = pax.decision || 'WAIT';
      const cls = (dec.indexOf('LONG') >= 0) ? 'b-ok'
                : (dec.indexOf('SHORT') >= 0) ? 'b-block'
                : (dec === 'STAND_DOWN') ? 'b-block' : 'b-warn';
      const tier = pax.size_tier || '';
      const conf = pax.confidence !== undefined ? pax.confidence : 0;
      paxBadge.innerHTML = '<span class="badge ' + cls + '">' + dec
                         + (tier ? ' · ' + tier : '')
                         + (conf  ? ' · ' + Math.round(conf*100) + '%' : '')
                         + '</span>';
      const c = pax.components || {};
      const lvl = c.level || {};
      const rows = [];
      if (lvl.label) rows.push(`<div class="kv"><span>level</span><span class="v">${lvl.label} @ ${fmtP(lvl.price)} (${(lvl.distance>=0?'+':'')+Number(lvl.distance||0).toFixed(2)} pts)</span></div>`);
      if (pax.size !== undefined && pax.size > 0) rows.push(`<div class="kv"><span>size</span><span class="v">${pax.size} (${tier})</span></div>`);
      const ctx = [];
      if (c.regime)      ctx.push(`regime <b>${c.regime}</b>`);
      if (c.biasScore !== undefined && c.biasScore !== null) ctx.push(`bias ${(c.biasScore>=0?'+':'')+Number(c.biasScore).toFixed(2)}`);
      if (c.vwapSlope)   ctx.push(`slope ${c.vwapSlope}`);
      if (c.vwapBias)    ctx.push(`VWAP ${c.vwapBias}`);
      if (c.vpBias)      ctx.push(`VP ${c.vpBias}`);
      if (ctx.length)    rows.push(`<div class="muted" style="font-size:11px;margin-top:4px;">${ctx.join(' · ')}</div>`);
      const reasons = (pax.reasons || []).slice(0, 5).join(' → ');
      if (reasons) rows.push(`<div class="muted" style="font-size:11px;margin-top:2px;">${reasons}</div>`);
      paxBox.innerHTML = rows.join('') || '<span class="muted">no signal</span>';
      // Notification: fire when decision transitions to ENTER_* with FULL or HALF size
      try {
        const notifyEl = document.getElementById('pax-notify');
        if (!notifyEl.dataset.init) {
          notifyEl.checked = (localStorage.getItem('paxNotify') === '1');
          notifyEl.addEventListener('change', () => {
            localStorage.setItem('paxNotify', notifyEl.checked ? '1' : '0');
            if (notifyEl.checked && 'Notification' in window && Notification.permission === 'default') {
              Notification.requestPermission();
            }
          });
          notifyEl.dataset.init = '1';
        }
        if (notifyEl.checked && pax && pax.decision && pax.decision.indexOf('ENTER_') === 0
            && (pax.size_tier === 'FULL' || pax.size_tier === 'HALF')) {
          const sigKey = pax.decision + '|' + pax.level_label + '|' + pax.size_tier;
          if (window._lastPaxNotifySig !== sigKey) {
            window._lastPaxNotifySig = sigKey;
            // Audio beep — two short tones for LONG, two low for SHORT
            try {
              const ctx = window._paxAudio || (window._paxAudio = new (window.AudioContext || window.webkitAudioContext)());
              const isLong = pax.decision.indexOf('LONG') >= 0;
              const freq1 = isLong ? 880 : 440;
              const freq2 = isLong ? 1320 : 330;
              [freq1, freq2].forEach((f, i) => {
                const osc = ctx.createOscillator();
                const gain = ctx.createGain();
                osc.frequency.value = f;
                gain.gain.value = 0.08;
                osc.connect(gain).connect(ctx.destination);
                osc.start(ctx.currentTime + i * 0.18);
                osc.stop(ctx.currentTime + i * 0.18 + 0.15);
              });
            } catch (audioErr) { /* AudioContext blocked until user gesture */ }
            // Desktop notification
            if ('Notification' in window && Notification.permission === 'granted') {
              new Notification('Pax: ' + pax.decision.replace('ENTER_','').replace('_',' '), {
                body: `${pax.level_label} @ ${pax.entry} · ${pax.size_tier} (${Math.round((pax.confidence||0)*100)}%)`,
                tag: 'pax-signal',
              });
            }
          }
        }
      } catch (notifyErr) { /* never let notify errors break the dashboard */ }
    }
  }

  // ─── Pax SIM trades ──────────────────────────────────────────────────
  const sim = s.sim;
  const simBox = document.getElementById('sim-box');
  const simPnl = document.getElementById('sim-pnl');
  if (!sim || sim._error) {
    simBox.innerHTML = '<span class="muted">' + ((sim && sim._error) ? sim._error : 'sim engine not loaded') + '</span>';
    simPnl.innerHTML = '';
  } else {
    const pos = sim.position || {};
    const sz = pos.size || 0;
    const realized = sim.realized_today_usd || 0;
    const cls = realized >= 0 ? 'b-ok' : 'b-block';
    const fillsN = sim.n_fills_today || 0;
    simPnl.innerHTML = `<span class="badge ${cls}" style="font-size:10px;">P&L $${realized.toFixed(2)}</span> <span class="muted" style="font-size:10px;">${fillsN} fills today</span>`;
    let html = `<div class="kv"><span>position</span><span class="v ${sz>0?'buy':sz<0?'sell':'muted'}">${sz>0?'+':''}${sz} @ ${pos.avg_price ? Number(pos.avg_price).toFixed(2) : '—'}</span></div>`;
    const working = sim.working || [];
    if (working.length) {
      html += '<table style="font-size:10px;margin-top:4px;"><tr><th>ID</th><th>Side</th><th>Type</th><th>Qty</th><th>Stop</th><th>Limit</th><th>Role</th></tr>';
      working.forEach(o => {
        const cls = o.side === 'BUY' ? 'buy' : 'sell';
        html += `<tr><td class="muted">${o.id}</td><td class="${cls}">${o.side}</td><td>${o.type}</td><td>${o.qty}</td><td>${o.stop_price ? Number(o.stop_price).toFixed(2) : '—'}</td><td>${o.limit_price ? Number(o.limit_price).toFixed(2) : '—'}</td><td class="muted">${o.role}</td></tr>`;
      });
      html += '</table>';
    } else if (sz === 0) {
      html += '<div class="muted" style="font-size:11px;">no working orders, flat</div>';
    }
    simBox.innerHTML = html;
  }

  // position
  const p = s.position || {};
  if (p._error) {
    document.getElementById('pos-box').innerHTML = '<span class="err">'+p._error+'</span>';
  } else {
    const size = p.position || 0;
    const cls = size > 0 ? 'buy' : (size < 0 ? 'sell' : 'muted');
    const src = p.source || 'flat';
    const pev = p.positionEvents||0, eev = p.executionEvents||0;
    let srcBadge;
    if (src === 'broker') {
      srcBadge = `<span class="badge b-ok">broker (${pev} pos events)</span>`;
    } else if (src === 'shadow') {
      srcBadge = `<span class="badge b-info">shadow from ${eev} fills</span>`;
    } else {
      srcBadge = '<span class="badge b-neutral">flat — no fills yet</span>';
    }
    document.getElementById('pos-box').innerHTML =
      `<div class="kv"><span>position</span><span class="v ${cls}">${size > 0 ? '+' : ''}${size}</span></div>` +
      `<div class="kv"><span>avg</span><span class="v">${fmtP(p.averagePrice)}</span></div>` +
      `<div class="kv"><span>uPnL</span><span class="v ${(p.unrealizedPnl||0)>=0?'buy':'sell'}">${sgn(p.unrealizedPnl)}</span></div>` +
      `<div class="kv"><span>rPnL</span><span class="v ${(p.realizedPnl||0)>=0?'buy':'sell'}">${sgn(p.realizedPnl)}</span></div>` +
      `<div class="kv"><span>working B/S</span><span class="v">${p.workingBuys||0}/${p.workingSells||0}</span></div>` +
      `<div class="kv"><span>source</span>${srcBadge}</div>`;
  }

  // balance
  const b = s.balance || {};
  if (b._error) {
    document.getElementById('bal-box').innerHTML = '<span class="err">'+b._error+'</span>';
  } else if (!b.currencies || !b.currencies.length) {
    document.getElementById('bal-box').innerHTML = '<span class="muted">no broker balance (sim/replay)</span>';
  } else {
    const c = b.currencies[0];
    document.getElementById('bal-box').innerHTML =
      `<div class="kv"><span>account</span><span class="v">${b.accountName || '—'}</span></div>` +
      `<div class="kv"><span>balance</span><span class="v">${fmtP(c.balance)} ${c.currency}</span></div>` +
      `<div class="kv"><span>net liq</span><span class="v">${fmtP(c.netLiquidityValue)}</span></div>` +
      `<div class="kv"><span>day rPnL</span><span class="v ${c.realizedPnl>=0?'buy':'sell'}">${sgn(c.realizedPnl)}</span></div>` +
      `<div class="kv"><span>day uPnL</span><span class="v ${c.unrealizedPnl>=0?'buy':'sell'}">${sgn(c.unrealizedPnl)}</span></div>`;
  }

  // momentum
  const m = s.momentum || {};
  function mom(label, im) {
    if (!im) return `<div class="kv"><span>${label}</span><span class="muted">thin</span></div>`;
    const pct = Math.round(im.imbalance * 100);
    const w = Math.min(Math.abs(pct), 100);
    const cls = pct >= 0 ? 'imb-pos' : 'imb-neg';
    return `<div class="kv"><span>${label}</span><span class="v"><span class="bar ${cls}" style="width:${w}px"></span> ${pct>=0?'+':''}${pct}% (${im.buy}/${im.sell})</span></div>`;
  }
  document.getElementById('mom-box').innerHTML =
    mom('Last 10', m.i10) + mom('Last 50', m.i50) + mom('Last 200', m.i200) +
    `<div class="kv"><span>flag</span><span class="badge ${m.flag && m.flag.startsWith('ALIGNED')?'b-ok':(m.flag && m.flag.startsWith('INFLECTION')?'b-warn':'b-neutral')}">${m.flag || '—'}</span></div>`;

  // flow regime — adaptive quant classifier (OFI + CVD divergence + VPT absorption + bias trajectory)
  const flow = s.flow;
  const fbox = document.getElementById('flow-box');
  if (!flow || flow._error) {
    const msg = (flow && flow._error) ? flow._error : 'no /momentum endpoint — redeploy bridge jar';
    fbox.innerHTML = '<span class="err">'+msg+'</span>';
  } else {
    const regCls = {
      'TRENDING_UP':     'b-ok',
      'TRENDING_DOWN':   'b-block',
      'ABSORPTION_BID':  'b-ok',     // bids absorbing sells = bullish
      'ABSORPTION_ASK':  'b-block',  // asks absorbing buys = bearish
      'EXHAUSTION_UP':   'b-warn',
      'EXHAUSTION_DOWN': 'b-warn',
      'BALANCED':        'b-neutral',
      'QUIET':           'b-neutral',
      'WARMUP':          'b-neutral'
    }[flow.regime] || 'b-neutral';
    const trajArrow = {
      'RISING':  '↗',
      'FALLING': '↘',
      'FLAT':    '→',
      'WARMUP':  '⋯'
    }[flow.biasTrajectory] || '·';
    const trajCls = {
      'RISING':  'buy',
      'FALLING': 'sell',
      'FLAT':    'muted',
      'WARMUP':  'muted'
    }[flow.biasTrajectory] || 'muted';
    const bias = Number(flow.biasScore || 0);
    const biasPct = Math.round(bias * 100);
    const biasBarW = Math.min(Math.abs(biasPct), 100);
    const biasBarCls = biasPct >= 0 ? 'imb-pos' : 'imb-neg';
    // z-score helper: colour + sign formatting
    const zRow = (label, z, raw, fmt) => {
      const zv = (z===null||z===undefined||Number.isNaN(z)) ? 0 : Number(z);
      const cls = zv > 0.5 ? 'buy' : (zv < -0.5 ? 'sell' : 'muted');
      const zStr = (zv>=0?'+':'') + zv.toFixed(2);
      const rawStr = (raw===null||raw===undefined||Number.isNaN(raw)) ? '—' : (fmt ? fmt(raw) : Number(raw).toFixed(1));
      return `<div class="kv"><span>${label}</span><span class="v"><span class="${cls}">${zStr}σ</span> <span class="muted" style="font-weight:400;">${rawStr}</span></span></div>`;
    };
    const conf = Math.round((flow.regimeConfidence||0) * 100);

    // Flow regime heatwave: strength = biasScore, scaled by regime confidence.
    // BiasScore is already clipped to [-1,+1] in Java. Modulating by conf×0.5+0.5
    // keeps a directional signal visible even when conf is low but doesn't let
    // the bar saturate when the classifier is unsure.
    const flowStrength = bias * (0.5 + 0.5 * (flow.regimeConfidence || 0));
    const flowBar = mkTrendBar(flowStrength, {
      title: `flow regime · ${flow.regime} · ${conf}% conf ·`,
      labels: {
        strongUp:'STRONG BULL FLOW', up:'BULL FLOW', leanUp:'BULL LEAN',
        chop: (flow.regime === 'BALANCED' ? 'BALANCED FLOW' :
               flow.regime === 'QUIET'    ? 'QUIET FLOW'    :
               flow.regime === 'WARMUP'   ? 'WARMUP' :
               'NEUTRAL FLOW'),
        leanDown:'BEAR LEAN', down:'BEAR FLOW', strongDown:'STRONG BEAR FLOW'
      }
    });

    fbox.innerHTML =
      flowBar +
      // Regime + confidence
      `<div class="kv"><span>regime</span><span class="v">` +
        `<span class="badge ${regCls}">${flow.regime}</span> ` +
        `<span class="muted" style="font-size:10px;">${conf}% conf</span>` +
      `</span></div>` +
      // Direction bias score with trajectory
      `<div class="kv"><span>bias</span><span class="v">` +
        `<span class="bar ${biasBarCls}" style="width:${biasBarW}px"></span> ` +
        `<span class="${bias>=0?'buy':'sell'}">${biasPct>=0?'+':''}${biasPct}</span> ` +
        `<span class="${trajCls}" title="3-bucket SMA slope of windowed imbalance">${trajArrow} ${flow.biasTrajectory||'·'}</span>` +
      `</span></div>` +
      // Quant feature z-scores
      zRow('OFI',  flow.ofiZ,       flow.ofi,       v => Number(v).toFixed(0)+'/s') +
      zRow('CVD',  flow.cvdDeltaZ,  flow.cvdDelta,  v => (v>=0?'+':'')+Number(v).toFixed(0)) +
      zRow('VPT',  flow.vptZ,       flow.vpt,       v => Number(v).toFixed(0)+'/t') +
      zRow('rvol', flow.rvolZ,      flow.rvolTicks, v => Number(v).toFixed(1)+'t') +
      // Reason
      `<div class="muted" style="font-size:10px;margin-top:4px;">${flow.regimeReason||''}</div>`;

  // D: tape buckets — bucket-weighted aggressor imbalance
  const tb = s.tape_buckets;
  const tbBox = document.getElementById('tape-buckets-box');
  const tbBias = document.getElementById('tape-buckets-bias');
  if (!tb || tb._error) {
    tbBox.innerHTML = '<span class="err">'+((tb && tb._error) ? tb._error : 'no /tape_buckets — bridge old?')+'</span>';
    if (tbBias) tbBias.innerHTML = '';
  } else {
    // Big traders count more — large prints weighted >> small.
    const W = { '1-10':0.5, '11-25':1.0, '26-50':1.5, '51-99':2.5, '100+':4.0 };
    let weighted = 0, totalVol = 0, totalPrints = 0;
    (tb.buckets||[]).forEach(b => {
      const w = W[b.label] || 1.0;
      const bv = b.buyVol30s||0, sv = b.sellVol30s||0;
      weighted += w * (bv - sv);
      totalVol += bv + sv;
      totalPrints += b.prints30s||0;
    });
    const tapeStrength = (totalVol > 0 && totalPrints >= 20) ? weighted / totalVol : 0;
    if (tbBias) {
      let bcls, blbl;
      if (totalPrints < 20) { bcls = 'b-neutral'; blbl = 'THIN'; }
      else {
        const pct = Math.round(tapeStrength * 100);
        if      (tapeStrength >  0.15) { bcls = 'b-ok';     blbl = 'BULLISH +' + pct + '%'; }
        else if (tapeStrength < -0.15) { bcls = 'b-block';  blbl = 'BEARISH '  + pct + '%'; }
        else                           { bcls = 'b-neutral'; blbl = 'BALANCED '+ (pct>=0?'+':'') + pct + '%'; }
      }
      tbBias.innerHTML = `<span class="badge ${bcls}" style="font-size:10px;">${blbl}</span>`;
    }
    // Heat bar header — same visual as the OR card
    let html = mkTrendBar(tapeStrength, {
      title: `tape buckets · ${totalPrints} prints/30s ·`,
      labels: {
        strongUp:'STRONG TAPE BUY', up:'TAPE BUY', leanUp:'BUY LEAN',
        chop: totalPrints < 20 ? 'THIN TAPE' : 'BALANCED',
        leanDown:'SELL LEAN', down:'TAPE SELL', strongDown:'STRONG TAPE SELL'
      }
    });
    html += '<table style="font-size:11px;"><tr><th>Bucket</th><th>30s Buy</th><th>30s Sell</th><th>30s Imb</th><th>5m Buy</th><th>5m Sell</th><th>5m Imb</th></tr>';
    (tb.buckets || []).forEach(b => {
      const imb30 = Math.round((b.imbalance30s||0)*100);
      const imb5  = Math.round((b.imbalance5m||0)*100);
      const c30 = imb30>=0?'buy':'sell';
      const c5  = imb5>=0?'buy':'sell';
      // Row heat: bucket-weight × |imbalance30s|, clamped to 1.
      const w = W[b.label] || 1.0;
      const rowSign = (b.imbalance30s||0) > 0 ? +1 : (b.imbalance30s||0) < 0 ? -1 : 0;
      const intensity = Math.min(1, Math.abs(b.imbalance30s||0) * (w / 4.0));   // 100+ bucket → full intensity at |imb|=1
      const heat = rowHeat(rowSign, intensity);
      html += `<tr class="row-heat"${heat}><td>${b.label}</td><td>${fmtN(b.buyVol30s)}</td><td>${fmtN(b.sellVol30s)}</td>` +
              `<td class="${c30}">${imb30>=0?'+':''}${imb30}%</td>` +
              `<td>${fmtN(b.buyVol5m)}</td><td>${fmtN(b.sellVol5m)}</td>` +
              `<td class="${c5}">${imb5>=0?'+':''}${imb5}%</td></tr>`;
    });
    html += '</table>';
    tbBox.innerHTML = html;
  }

  // C: LT liquidity — long-term liquidity imbalance bid vs ask.
  // Convention: ratio = (bid - ask)/(bid + ask).
  //   ratio > 0  → BID HEAVY → support stacked below → bearish (sellers leaning into bids)
  //   ratio < 0  → ASK HEAVY → resistance built up   → bullish (buyers must chew through asks)
  // So bias strength = -ratio.
  const lt = s.lt_liquidity;
  const ltBox = document.getElementById('lt-box');
  const ltBias = document.getElementById('lt-bias');
  if (!lt || lt._error) {
    ltBox.innerHTML = '<span class="err">'+((lt && lt._error) ? lt._error : 'no /lt_liquidity')+'</span>';
    if (ltBias) ltBias.innerHTML = '';
  } else {
    const ratio = lt.ratio||0;
    const ltStrength = Math.max(-1, Math.min(1, -ratio));   // +bull when asks heavier
    let bcls, blbl;
    if      (ratio < -0.20) { bcls = 'b-ok';     blbl = 'ASK HEAVY ' + Math.round(-ratio*100) + '%'; }
    else if (ratio >  0.20) { bcls = 'b-block';  blbl = 'BID HEAVY ' + Math.round(ratio*100)  + '%'; }
    else                    { bcls = 'b-neutral'; blbl = 'BALANCED '  + (ratio>=0?'+':'') + Math.round(ratio*100) + '%'; }
    if (ltBias) ltBias.innerHTML = `<span class="badge ${bcls}" style="font-size:10px;">${blbl}</span>`;
    const ltBar = mkTrendBar(ltStrength, {
      title: `LT liquidity · ratio ${ratio.toFixed(2)} ·`,
      labels: {
        strongUp:'STRONG ASK HEAVY (BULL)', up:'ASK HEAVY (BULL)', leanUp:'ASK LEAN',
        chop:'LIQ BALANCED',
        leanDown:'BID LEAN', down:'BID HEAVY (BEAR)', strongDown:'STRONG BID HEAVY (BEAR)'
      }
    });
    ltBox.innerHTML =
      ltBar +
      `<div class="kv"><span>LT bid</span><span class="v">${fmtN(Math.round(lt.ltBidSize||0))}</span></div>` +
      `<div class="kv"><span>LT ask</span><span class="v">${fmtN(Math.round(lt.ltAskSize||0))}</span></div>` +
      `<div class="kv"><span>ratio</span><span class="v ${ratio>=0?'sell':'buy'}">${ratio>=0?'+':''}${Number(ratio).toFixed(2)}</span></div>` +
      `<div class="muted" style="font-size:10px;">half-life ${(lt.halfLifeMillis||30000)/1000}s · best bid ${fmtP(lt.bestBid)} / ask ${fmtP(lt.bestAsk)}</div>`;
  }

  // Micro events
  const me = s.micro_events;
  const mb = document.getElementById('micro-box');
  const mBias = document.getElementById('micro-bias');
  if (!me || me._error) {
    mb.innerHTML = '<span class="err">'+((me && me._error) ? me._error : 'no /microstructure_events')+'</span>';
    if (mBias) mBias.innerHTML = '';
  } else {
    const evs = me.events || [];
    // ─── Score the LAST 25 events once. Used for both badge and heatwave. ───
    // Sign convention: +bscore = BULLISH, −bscore = BEARISH.
    //   ICEBERG    isBid=true  → +1   isBid=false → −1
    //   SPOOF      isBid=true  → −1   isBid=false → +1
    //   STOP_SWEEP isBid=true  → −1   isBid=false → +1
    // Decay: exponential half-life of 5 min. Stale events still contribute,
    // so the badge/bar driven by the LAST 25 events never collapses to 0 just
    // because the tape briefly quiets down.
    const HALF_LIFE_MS  = 5 * 60 * 1000;
    const SCORED_EVENTS = 25;
    const BULL_THR = 0.3, BEAR_THR = -0.3;
    const now    = Date.now();
    const recent = (me.events || []).slice(0, SCORED_EVENTS);
    const serverCount = (typeof me.count === 'number') ? me.count : recent.length;
    const sideBias = (kind, isBid, isAsk) => {
      if (kind === 'ICEBERG')    return isBid ? +1 : isAsk ? -1 : 0;
      if (kind === 'SPOOF')      return isBid ? -1 : isAsk ? +1 : 0;
      if (kind === 'STOP_SWEEP') return isBid ? -1 : isAsk ? +1 : 0;
      return 0;
    };
    let bscore = 0, weightSum = 0, bullN = 0, bearN = 0, unsidedN = 0;
    const decayed = [];   // per-event {sign, weight, decay} for row-heat re-use
    recent.forEach(ev => {
      const ageMs = Math.max(0, now - (ev.timeMs || ev.tsMs || now));
      const decay = Math.pow(0.5, ageMs / HALF_LIFE_MS);
      const kind  = ev.kind || ev.type;
      const w     = ({'STOP_SWEEP': 1.0, 'ICEBERG': 0.7, 'SPOOF': 0.5}[kind] || 0.3) * decay;
      const isBid = (ev.isBid === true)  || ev.side === 'BID' || ev.side === 'BUY';
      const isAsk = (ev.isBid === false) || ev.side === 'ASK' || ev.side === 'SELL';
      const sign  = sideBias(kind, isBid, isAsk);
      if      (sign > 0) bullN++;
      else if (sign < 0) bearN++;
      else               unsidedN++;
      bscore    += w * sign;
      weightSum += w;
      decayed.push({sign, decay});
    });
    // Heatwave strength: squash the unbounded bscore through tanh(bscore/2)
    // so a score of 2 maps to 0.76, 4 to 0.96 — bar saturates gracefully.
    const microStrength = Math.tanh(bscore / 2);

    if (mBias) {
      const cls = bscore > BULL_THR ? 'b-ok'
                : bscore < BEAR_THR ? 'b-block'
                : 'b-neutral';
      let lbl;
      if      (bscore >  BULL_THR) lbl = 'BULLISH';
      else if (bscore <  BEAR_THR) lbl = 'BEARISH';
      else if (recent.length === 0) lbl = serverCount > 0 ? `NO RECENT (buf=${serverCount})` : 'NO EVENTS';
      else                          lbl = 'NEUTRAL';
      const counts = (recent.length > 0)
          ? ` <span class="buy"  style="font-size:9px;">▲${bullN}</span>` +
            ` <span class="sell" style="font-size:9px;">▼${bearN}</span>` +
            (unsidedN ? ` <span class="muted" style="font-size:9px;">·${unsidedN}</span>` : '') +
            ` <span class="muted" style="font-size:9px;">/${recent.length}</span>`
          : '';
      mBias.innerHTML =
          `<span class="badge ${cls}" style="font-size:10px;" ` +
          `title="bull=${bullN} bear=${bearN} unsided=${unsidedN} | weighted=${bscore.toFixed(3)} | weightSum=${weightSum.toFixed(2)} | server count=${serverCount} | 5-min half-life">` +
          `${lbl} ${bscore>=0?'+':''}${Number(bscore).toFixed(2)}` +
          `</span>${counts}`;
    }
    if (!evs.length) {
      mb.innerHTML = '<span class="muted">no recent events</span>';
    } else {
      // Heatwave header bar
      const microBar = mkTrendBar(microStrength, {
        title: `micro · bscore ${bscore.toFixed(2)} · ▲${bullN}/▼${bearN}/${recent.length} ·`,
        labels: {
          strongUp:'STRONG MICRO BULL', up:'MICRO BULL', leanUp:'MICRO BULL LEAN',
          chop: recent.length === 0 ? 'NO EVENTS' : 'MICRO NEUTRAL',
          leanDown:'MICRO BEAR LEAN', down:'MICRO BEAR', strongDown:'STRONG MICRO BEAR'
        }
      });
      let html = microBar +
                 '<table style="font-size:11px;"><tr><th>Type</th><th>Side</th><th>Bias</th><th>Price</th><th>Size</th><th>Age</th></tr>';
      evs.slice(0, 25).forEach((ev, i) => {
        const ageS = Math.round((Date.now() - (ev.timeMs || ev.tsMs || Date.now())) / 1000);
        const kind = ev.kind || ev.type || '?';
        const isBidEv = (ev.isBid === true);
        const isAskEv = (ev.isBid === false);
        const side = isBidEv ? 'BID' : isAskEv ? 'ASK' : (ev.side || '—');
        const sb = sideBias(kind, isBidEv, isAskEv);
        const biasTxt = sb > 0 ? 'BULL' : sb < 0 ? 'BEAR' : '·';
        const biasCls = sb > 0 ? 'buy'  : sb < 0 ? 'sell' : 'muted';
        const cls = kind === 'STOP_SWEEP' ? 'sell' : kind === 'ICEBERG' ? 'buy' : 'muted';
        // Row heat: bias direction × decay (fresh events tint brighter)
        const dec = (decayed[i] && decayed[i].decay) || 1;
        const heat = rowHeat(sb, dec);
        html += `<tr class="row-heat"${heat}><td class="${cls}">${kind}</td><td>${side}</td><td class="${biasCls}">${biasTxt}</td><td>${fmtP(ev.price)}</td><td>${fmtN(ev.size)}</td><td class="muted">${ageS}s</td></tr>`;
      });
      html += '</table>';
      mb.innerHTML = html;
    }
  }

  // pull/stack
  const ps = s.pull_stack;
  const psBox = document.getElementById('pull-stack-box');
  if (!ps || ps._error) {
    psBox.innerHTML = '<span class="err">'+((ps && ps._error) ? ps._error : 'no /pull_stack')+'</span>';
    const psBias = document.getElementById('pull-stack-bias');
    if (psBias) psBias.innerHTML = '';
  } else if (!ps.windows || ps.windows.length === 0) {
    psBox.innerHTML = '<span class="muted">no pull/stack data yet</span>';
    const psBias = document.getElementById('pull-stack-bias');
    if (psBias) psBias.innerHTML = '';
  } else {
    const psBias = document.getElementById('pull-stack-bias');
    const aggZ    = ps.aggregateZ || 0;
    const aggBias = ps.aggregateBias || 'QUIET';
    const rot     = ps.rotation || 'NONE';
    // Squash unbounded z-score into [-1,+1]: z≈2 → strength≈0.76, z≈4 → 0.96
    const psStrength = Math.tanh(aggZ / 2);
    if (psBias) {
      const cls    = aggBias.includes('BULL') ? 'b-ok' : aggBias.includes('BEAR') ? 'b-block' : 'b-neutral';
      const rotTag = (rot && rot !== 'NONE') ? ' · <span style="color:#e0af68;">' + rot + '</span>' : '';
      psBias.innerHTML = '<span class="badge ' + cls + '" style="font-size:10px;">' + aggBias + ' z=' + (aggZ>=0?'+':'') + Number(aggZ).toFixed(2) + rotTag + '</span>';
    }
    const psBar = mkTrendBar(psStrength, {
      title: `pull/stack · agg z ${aggZ.toFixed(2)} · ${rot} ·`,
      labels: {
        strongUp:'STRONG STACK BID', up:'STACKING BIDS', leanUp:'BID STACK LEAN',
        chop:'QUIET',
        leanDown:'ASK STACK LEAN', down:'STACKING ASKS', strongDown:'STRONG STACK ASK'
      }
    });
    let psHtml = psBar +
      '<table style="font-size:11px;"><tr><th>Window</th><th>BidStk</th><th>BidPul</th><th>AskStk</th><th>AskPul</th><th>Bias</th><th>z</th></tr>';
    ps.windows.forEach(w => {
      const biasCls = w.bias && w.bias.startsWith('BULL') ? 'buy' : (w.bias && w.bias.startsWith('BEAR') ? 'sell' : 'muted');
      const zStr = (w.zScore===null||w.zScore===undefined) ? '—' : ((w.zScore>=0?'+':'')+Number(w.zScore).toFixed(2));
      // Row heat from z-sign × |z|/3 (saturates at z≈3)
      const zNum = (w.zScore===null||w.zScore===undefined) ? 0 : Number(w.zScore);
      const rowSign = zNum > 0 ? +1 : zNum < 0 ? -1 : 0;
      const intensity = Math.min(1, Math.abs(zNum) / 3);
      const heat = rowHeat(rowSign, intensity);
      psHtml += `<tr class="row-heat"${heat}><td>${w.label}</td>` +
        `<td>${fmtN(w.bidStacked)}</td><td>${fmtN(w.bidPulled)}</td>` +
        `<td>${fmtN(w.askStacked)}</td><td>${fmtN(w.askPulled)}</td>` +
        `<td class="${biasCls}">${w.bias||'—'}</td><td>${zStr}</td></tr>`;
    });
    psHtml += '</table>';
    psBox.innerHTML = psHtml;
  }

  // ─── Orderbook · 5-pt buckets · heatwave · session-weighted EMA ──────
  const bk = s.book;
  const bookBox = document.getElementById('book-box');
  const bookBias = document.getElementById('book-bias');
  if (!bk || bk._error) {
    bookBox.innerHTML = '<span class="err">'+((bk && bk._error) ? bk._error : 'no book')+'</span>';
    if (bookBias) bookBias.innerHTML = '';
  } else {
    // 1) Aggregate raw ladder into 5-pt buckets
    const askCur = obAggregate(bk.asks);
    const bidCur = obAggregate(bk.bids);
    // 2) Step the session-anchored EMA
    const now = Date.now();
    const dtMs = (OB_SESSION.lastUpdateMs > 0) ? Math.min(5000, now - OB_SESSION.lastUpdateMs) : 1000;
    OB_SESSION.lastUpdateMs = now;
    obUpdateEMA(OB_SESSION.askEMA, OB_SESSION.askFirstMs, askCur, dtMs, now);
    obUpdateEMA(OB_SESSION.bidEMA, OB_SESSION.bidFirstMs, bidCur, dtMs, now);

    // 3) Pick visible buckets — N nearest each side of mid.
    //    Use the best-bid / best-ask bucket as the divider so a mid that
    //    lands on a 5-pt boundary doesn't fold both sides into one bucket.
    const tick = 0.25;
    const askDivKey = obBucketKey((bk.mid || 0) + tick);   // first bucket strictly above mid
    const bidDivKey = obBucketKey((bk.mid || 0) - tick);   // last bucket at/below mid
    const askKeys = [...new Set([...askCur.keys(), ...OB_SESSION.askEMA.keys()])]
        .filter(k => k >= askDivKey).sort((a,b) => a-b).slice(0, OB_VISIBLE_PER_SIDE);
    const bidKeys = [...new Set([...bidCur.keys(), ...OB_SESSION.bidEMA.keys()])]
        .filter(k => k <= bidDivKey).sort((a,b) => b-a).slice(0, OB_VISIBLE_PER_SIDE);

    // 4) Liquidity scoring — Jane-Street style:
    //      raw_size   : current snapshot per bucket
    //      ema_size   : session-weighted persistence per bucket
    //      norm       : ema_size / max(ema across visible) ∈ [0,1] → heat intensity
    //      z          : (ema_size - mean) / std across visible → "how unusual"
    //    Highlight: high norm + high z = STICKY heavy resting order.
    //    Brand-new: firstMs within last 5s → spoof candidate (tag with ●NEW).
    const rows = [];
    for (const k of askKeys) rows.push({side:'ASK', key:k,
        raw: askCur.get(k) || 0, ema: OB_SESSION.askEMA.get(k) || 0,
        firstMs: OB_SESSION.askFirstMs.get(k) || now});
    for (const k of bidKeys) rows.push({side:'BID', key:k,
        raw: bidCur.get(k) || 0, ema: OB_SESSION.bidEMA.get(k) || 0,
        firstMs: OB_SESSION.bidFirstMs.get(k) || now});
    const visEmas = rows.map(r => r.ema).filter(v => v > 0);
    const maxEma = visEmas.length ? Math.max(...visEmas) : 1;
    const meanEma = visEmas.length ? visEmas.reduce((a,b)=>a+b,0) / visEmas.length : 0;
    const varEma  = visEmas.length ? visEmas.reduce((a,b)=>a+(b-meanEma)*(b-meanEma),0)/visEmas.length : 0;
    const stdEma  = Math.sqrt(varEma) || 1;
    rows.forEach(r => {
      r.norm = (r.ema > 0 && maxEma > 0) ? r.ema / maxEma : 0;
      r.z    = (r.ema > 0)               ? (r.ema - meanEma) / stdEma : 0;
      r.ageS = Math.round((now - r.firstMs) / 1000);
      r.deltaPct = (r.ema > 0) ? Math.round((r.raw - r.ema) / r.ema * 100) : 0;
    });

    // 5) Header heatwave — net bid/ask EMA balance
    //    Convention (matches LT-liq): ask-heavy = bull (sellers exhausted),
    //    bid-heavy = bear (buyers exhausted into the stack).
    const askTotal = [...OB_SESSION.askEMA.values()].reduce((a,b)=>a+b, 0);
    const bidTotal = [...OB_SESSION.bidEMA.values()].reduce((a,b)=>a+b, 0);
    const tot = askTotal + bidTotal;
    const bookStrength = tot > 0 ? (askTotal - bidTotal) / tot : 0;
    const headerBar = mkTrendBar(bookStrength, {
      title: `book · bid EMA ${Math.round(bidTotal)} · ask EMA ${Math.round(askTotal)} ·`,
      labels: {
        strongUp:'STRONG ASK STACK (BULL)', up:'ASK STACK (BULL)', leanUp:'ASK LEAN',
        chop:'BALANCED BOOK',
        leanDown:'BID LEAN', down:'BID STACK (BEAR)', strongDown:'STRONG BID STACK (BEAR)'
      }
    });
    if (bookBias) {
      const sessSec = Math.round((now - OB_SESSION.startMs) / 1000);
      bookBias.innerHTML =
          `<span class="muted">session ${sessSec}s · ${rows.length} buckets · ` +
          `bid ${Math.round(bidTotal)} / ask ${Math.round(askTotal)}</span>`;
    }

    // 6) HVN/LVN annotations from vp_bias still apply
    const vpb = s.vp_bias || {};
    const hvnArr = vpb.hvn || [];
    const lvnArr = vpb.lvn || [];
    function nodeTag(priceMin, priceMax) {
      const ph = hvnArr.find(n => n.price >= priceMin && n.price < priceMax);
      if (ph) return `<span title="HVN str ${ph.strength}σ" style="color:#9ece6a;font-weight:700;">●</span> `;
      const pl = lvnArr.find(n => n.price >= priceMin && n.price < priceMax);
      if (pl) return `<span title="LVN str ${pl.strength}σ" style="color:#e0af68;">○</span> `;
      return '';
    }

    // 7) Render ladder.  Ask rows top-down (highest price up), bids descending.
    let html = headerBar + '<table class="ladder" style="font-size:11px;">' +
               '<colgroup><col class="c-bid"/><col class="c-price"/><col class="c-ask"/></colgroup>' +
               '<tr><th class="bid">Bid (EMA)</th><th class="px">Price (5pt)</th><th class="ask">Ask (EMA)</th></tr>';
    const askRows = rows.filter(r => r.side === 'ASK').sort((a,b) => b.key - a.key);   // desc
    const bidRows = rows.filter(r => r.side === 'BID').sort((a,b) => b.key - a.key);
    function renderRow(r) {
      const p0 = obBucketPrice(r.key);
      const p1 = p0 + OB_BUCKET_PTS;
      const tag = nodeTag(p0, p1);
      const sz   = Math.round(r.ema);
      const raw  = Math.round(r.raw);
      // Heat: green for ask, red for bid (matches header convention)
      const hueRGB = (r.side === 'ASK') ? '158,206,106' : '247,118,142';
      const alpha = (0.10 + r.norm * 0.55).toFixed(2);
      const alphaF = (0.04 + r.norm * 0.22).toFixed(2);
      // Bar gradient — heaviest side strongest
      const stops = (r.side === 'ASK')
          ? `transparent 0%, rgba(${hueRGB},${alphaF}) 40%, rgba(${hueRGB},${alpha}) 100%`
          : `rgba(${hueRGB},${alpha}) 0%, rgba(${hueRGB},${alphaF}) 60%, transparent 100%`;
      const bg = `background:linear-gradient(90deg, ${stops});`;
      const zTag = r.z > 1.5 ? ` <span title="z=${r.z.toFixed(2)} · sticky heavy" style="color:#e0af68;">★</span>` : '';
      const newTag = r.ageS < 5 ? ` <span title="just appeared — possible spoof" style="color:#f7768e;">⚡</span>` : '';
      const deltaTag = (r.deltaPct >  20 || r.deltaPct < -20)
          ? ` <span title="raw vs EMA Δ=${r.deltaPct>=0?'+':''}${r.deltaPct}%" style="color:#737994;font-size:9px;">Δ${r.deltaPct>=0?'+':''}${r.deltaPct}%</span>`
          : '';
      const ageTag = (r.ageS >= 30)
          ? ` <span title="age=${r.ageS}s" style="color:#737994;font-size:9px;">${r.ageS}s</span>` : '';
      const priceLbl = `${tag}${fmtP(p0)}–${fmtP(p1 - 0.25)}`;
      if (r.side === 'ASK') {
        return `<tr style="${bg}"><td></td><td class="px">${priceLbl}</td>` +
               `<td class="ask" title="raw=${raw} ema=${sz.toFixed(0)} z=${r.z.toFixed(2)} age=${r.ageS}s">${fmtN(sz)}${zTag}${newTag}${deltaTag}${ageTag}</td></tr>`;
      }
      return `<tr style="${bg}"><td class="bid" title="raw=${raw} ema=${sz.toFixed(0)} z=${r.z.toFixed(2)} age=${r.ageS}s">${zTag}${newTag}${deltaTag}${ageTag}${fmtN(sz)}</td>` +
             `<td class="px">${priceLbl}</td><td></td></tr>`;
    }
    askRows.forEach(r => { html += renderRow(r); });
    html += `<tr class="mid" style="background:#1e2230;"><td colspan="3">mid ${fmtP(bk.mid)} · sp ${fmtP(bk.spread)}</td></tr>`;
    bidRows.forEach(r => { html += renderRow(r); });
    html += '</table>';
    const legend = `<div class="muted" style="font-size:10px;margin-top:3px;">` +
        `<span style="color:#e0af68;">★ sticky (z&gt;1.5)</span> · ` +
        `<span style="color:#f7768e;">⚡ just-appeared</span> · ` +
        `Δ raw vs EMA · ` +
        (hvnArr.length || lvnArr.length
          ? `<span style="color:#9ece6a;">● HVN</span> · <span style="color:#e0af68;">○ LVN</span> · `
          : '') +
        `${OB_EMA_HL_MS/1000}s half-life</div>`;
    bookBox.innerHTML = html + legend;
  }

  // ─── Tape — institutional flow ──────────────────────────────────────
  // Heat bar is driven by snap.tape_flow.deltaScore (size-weighted,
  // 30s + 5m windows, computed server-side). The print table below it
  // is display-only — it is NOT the model signal.
  const VISIBLE_RECENT_PRINTS = 25;
  const prints25 = (s.trades || []).slice(0, VISIBLE_RECENT_PRINTS);
  const tapeBox = document.getElementById('tape-box');
  const tapeBiasBox = document.getElementById('tape-prints-bias');
  const tf = s.tape_flow;
  const tfOk = tf && !tf._error && typeof tf.deltaScore === 'number';

  let topHtml = '';
  if (tfOk) {
    const dScore = tf.deltaScore;
    const dLabel = tf.deltaLabel || 'BALANCED';
    const labels = {
      strongUp:'STRONG INSTITUTIONAL BUY', up:'INSTITUTIONAL BUY', leanUp:'BUY LEAN',
      chop: dLabel === 'THIN' ? 'THIN TAPE' : 'BALANCED FLOW',
      leanDown:'SELL LEAN', down:'INSTITUTIONAL SELL', strongDown:'STRONG INSTITUTIONAL SELL'
    };
    const titleTxt =
      `tape · 30s ▲${tf.largeBuyVol30s||0}/▼${tf.largeSellVol30s||0} (51+) · ` +
      `block 30s ▲${tf.blockBuyVol30s||0}/▼${tf.blockSellVol30s||0} (100+) · ` +
      `n30=${tf.totalPrints30s||0} ·`;
    topHtml = mkTrendBar(dScore, {title: titleTxt, labels});
    if (tapeBiasBox) {
      const cls = dScore > 0.15 ? 'buy' : dScore < -0.15 ? 'sell' : 'muted';
      tapeBiasBox.innerHTML =
        `<span class="${cls}" style="font-size:10px;">${dLabel} ${dScore>=0?'+':''}${Number(dScore).toFixed(2)}</span>`;
    }
    topHtml +=
      `<div class="muted" style="font-size:10px;margin-top:2px;">${tf.deltaReason||''}</div>`;
  } else if (prints25.length) {
    // Fallback: legacy in-browser heatwave so the panel doesn't go blank
    // during a rolling deploy or when tape_flow is briefly missing.
    let buyVol = 0, sellVol = 0;
    prints25.forEach(t => {
      const sz = Number(t.size) || 0;
      if (t.side === 'buy') buyVol += sz; else sellVol += sz;
    });
    const total = buyVol + sellVol;
    const strength = total > 0 ? (buyVol - sellVol) / total : 0;
    topHtml = mkTrendBar(strength, {
      title: `tape (fallback) · prints ${prints25.length} · buyVol ${buyVol} / sellVol ${sellVol} ·`,
      labels: {
        strongUp:'STRONG BUYING', up:'BUYING', leanUp:'BUY LEAN',
        chop:'BALANCED (fallback)',
        leanDown:'SELL LEAN', down:'SELLING', strongDown:'STRONG SELLING'
      }
    });
    if (tapeBiasBox) tapeBiasBox.innerHTML =
        '<span class="muted" style="font-size:10px;">(fallback)</span>';
  } else {
    topHtml = '<span class="muted">no prints yet</span>';
    if (tapeBiasBox) tapeBiasBox.innerHTML = '';
  }

  // Recent-prints table — display only, NOT the model signal.
  let tableHtml = '';
  if (prints25.length) {
    let maxSize = 0;
    prints25.forEach(t => { const sz = Number(t.size) || 0; if (sz > maxSize) maxSize = sz; });
    tableHtml += '<div class="muted" style="font-size:10px;margin-top:6px;">' +
                 `Recent prints (display only — model signal is the 30s/5m delta above)` +
                 '</div>';
    tableHtml += '<table style="font-size:11px;"><tr><th>#</th><th>Price</th><th>Size</th><th>Side</th></tr>';
    prints25.forEach((t, i) => {
      const cls = t.side === 'buy' ? 'buy' : 'sell';
      const sz = Number(t.size) || 0;
      const rowSign = (t.side === 'buy') ? +1 : -1;
      const intensity = maxSize > 0 ? Math.min(1, sz / maxSize) : 0;
      const heat = rowHeat(rowSign, intensity);
      tableHtml += `<tr class="row-heat"${heat}><td>${i+1}</td><td class="${cls}">${fmtP(t.price)}</td><td>${sz}</td><td class="${cls}">${t.side}</td></tr>`;
    });
    tableHtml += '</table>';
  }
  tapeBox.innerHTML = topHtml + tableHtml;

  // working orders (collapses when empty)
  const w = s.working || {};
  const workingRow = document.getElementById('working-row');
  if (w._error) {
    if (workingRow) workingRow.style.display = '';
    document.getElementById('working-box').innerHTML = '<span class="err">'+w._error+'</span>';
  } else if (!w.orders || !w.orders.length) {
    if (workingRow) workingRow.style.display = 'none';
  } else {
    if (workingRow) workingRow.style.display = '';
    const oev = (w.orderEvents||0), eev = (w.executionEvents||0);
    const evLine = `<div class="muted" style="font-size:11px;margin-top:4px;">${oev} order events, ${eev} fills received</div>`;
    let html = '<table><tr><th>Side</th><th>Qty</th><th>Price</th><th>Status</th></tr>';
    w.orders.forEach(o => {
      html += `<tr><td class="${o.side}">${o.side}</td><td>${o.unfilled}</td><td>${fmtP(o.limitPrice)}</td><td>${o.status}</td></tr>`;
    });
    html += '</table>';
    document.getElementById('working-box').innerHTML = html + evLine;
  }

  // recent fills
  const f = s.fills || {};
  if (f._error) {
    document.getElementById('fills-box').innerHTML = '<span class="err">'+f._error+'</span>';
  } else if (!f.fills || !f.fills.length) {
    document.getElementById('fills-box').innerHTML = '<span class="muted">no fills yet</span>';
  } else {
    let html = '<table><tr><th>Time</th><th>Side</th><th>Size</th><th>Price</th><th>Order</th><th>Sim?</th></tr>';
    f.fills.forEach(x => {
      const t = new Date(x.timeMillis);
      html += `<tr><td>${t.toLocaleTimeString()}</td><td class="${x.side}">${x.side}</td><td>${x.size}</td>` +
              `<td>${fmtP(x.price)}</td><td class="muted">${(x.orderId||'').substring(0,12)}…</td>` +
              `<td>${x.isSimulated ? 'SIM' : 'LIVE'}</td></tr>`;
    });
    html += '</table>';
    document.getElementById('fills-box').innerHTML = html;
  }
}

}

setInterval(refresh, 1000);
refresh();
</script>
</body></html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/" or path == "/index.html":
                body = INDEX_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/api/snapshot":
                try: snap = fetch_snapshot()
                except Exception as e:
                    sys.stderr.write("[dashboard] snapshot crashed:\n" + traceback.format_exc() + "\n")
                    snap = {"health": "error", "error": f"{type(e).__name__}: {e}"}
                body = safe_json(snap).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404); self.end_headers()
            self.wfile.write(b"not found")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            sys.stderr.write("[dashboard] handler crash:\n" + traceback.format_exc() + "\n")
            try: self.send_response(500); self.end_headers()
            except Exception: pass


def main(port: int = 18888) -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(message)s")
    server = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    print(f"Bookmap HUD on http://localhost:{port}", flush=True)
    sys.stderr.write(f"[dashboard] serve_forever starting (pid={os.getpid()})\n")
    sys.stderr.flush()
    try: server.serve_forever()
    except KeyboardInterrupt: sys.stderr.write("[dashboard] KeyboardInterrupt\n")
    except Exception:
        sys.stderr.write("[dashboard] serve_forever crashed:\n" + traceback.format_exc() + "\n")
    finally:
        sys.stderr.write("[dashboard] serve_forever returned\n"); sys.stderr.flush()


if __name__ == "__main__":
    main()
