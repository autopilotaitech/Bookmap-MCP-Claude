"""P4 — Autonomous Pax sim trader.

  python -m bookmap_mcp.pax_trader              # default: poll dashboard, run forever
  python -m bookmap_mcp.pax_trader --once       # single tick, exit (for tests)
  python -m bookmap_mcp.pax_trader --no-claude  # skip Claude CLI subprocess

Wiring:
  every poll →
    fetch snapshot (from dashboard's /api/snapshot)
      → SimEngine.tick(snap)            (fills/triggers/TIF)
      → PaxCollector.tick(snap)         (proximity recordings)
      → if snap.pax says ENTER + matches active level + we're flat
          → optionally ask Claude on ambiguous cases
          → place bracket (entry stop-limit + SL + 2 TPs)
      → log decisions to D:\\BookmapLogs\\pax-trader.log

SIM ONLY — there is NO code path here that talks to a live broker. The
SimEngine is a local in-process simulator; orders never leave the dashboard.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .sim_engine import SimEngine, SIZE_BY_TIER, S_BUY, S_SELL, NQ_TICK_PRICE
from .pax_collector import PaxCollector
from . import cache as cache_mod

LOG_DIR = Path(os.environ.get("PAX_LOG_DIR", r"D:\BookmapLogs"))
DASHBOARD_URL = os.environ.get("PAX_SNAPSHOT_URL", "http://127.0.0.1:18888/api/snapshot")

# Tick offset for stop-limit entries: stop = trigger, limit = trigger + 2 ticks slip
ENTRY_LIMIT_SLIP_TICKS = 2
ENTRY_TIF_SEC = 90.0


def fetch_snap() -> Optional[Dict[str, Any]]:
    try:
        with urllib.request.urlopen(DASHBOARD_URL, timeout=3.0) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError):
        return None


def has_open_position(eng: SimEngine) -> bool:
    snap = eng.snapshot()
    return abs((snap.get("position") or {}).get("size", 0)) > 0


def has_pending_entry(eng: SimEngine) -> bool:
    snap = eng.snapshot()
    return any(o.get("role") == "ENTRY" and o.get("status") == "WORKING"
               for o in (snap.get("working") or []))


def find_target_level(snap: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ol = (snap or {}).get("or_levels") or {}
    levels = [l for l in (ol.get("levels") or []) if l.get("proximity")]
    if not levels: return None
    levels.sort(key=lambda l: abs(float(l.get("distance") or 0)))
    return levels[0]


def compute_brackets(side: str, entry_px: float, level_px: float,
                     pax: Dict[str, Any], snap: Dict[str, Any],
                     is_follow: bool = True
                    ) -> Tuple[float, float, list]:
    """Return (stop_loss, entry_limit, take_profit_list).

    Two regimes:
      FOLLOW (break-out): STOP-LIMIT entry, slip 2 ticks PAST the trigger so a
                          fast move doesn't print right through the limit.
      FADE   (rotation):  passive LIMIT entry AT the level — we want the queue,
                          not the market. Adding slip would make us marketable
                          and we'd fill immediately at the wrong price.

    Stop:  opposite side of OR ± 2 ticks (canonical Pax rule).
    TPs:   snap to whole numbers / HVNs near the rung extensions.
    """
    ol = snap.get("or_levels") or {}
    or_h = float(ol.get("orHigh") or 0)
    or_l = float(ol.get("orLow")  or 0)
    rung = float(ol.get("rungPts") or 65.0)
    slip = ENTRY_LIMIT_SLIP_TICKS * NQ_TICK_PRICE

    if side == S_BUY:
        # FOLLOW long  → STOP-LIMIT above entry_px, limit = entry_px + slip
        # FADE   long  → LIMIT at entry_px (no slip; we sit and wait)
        entry_limit = (entry_px + slip) if is_follow else entry_px
        stop_loss = or_l - 2 * NQ_TICK_PRICE if or_l > 0 else entry_px - 8.0
        tps_raw = [entry_px + 10.0, entry_px + rung, entry_px + 2 * rung]
    else:
        # FOLLOW short → STOP-LIMIT below entry_px, limit = entry_px - slip
        # FADE   short → LIMIT at entry_px
        entry_limit = (entry_px - slip) if is_follow else entry_px
        stop_loss = or_h + 2 * NQ_TICK_PRICE if or_h > 0 else entry_px + 8.0
        tps_raw = [entry_px - 10.0, entry_px - rung, entry_px - 2 * rung]
    tps = [_snap_to_round(p) for p in tps_raw]
    return stop_loss, entry_limit, tps


def _snap_to_round(p: float) -> float:
    """Magnetize TPs near whole numbers. Within 2pts of a multiple of 25 →
    snap 1 tick BEFORE so we queue ahead of the cluster."""
    for unit in (25.0, 10.0):
        nearest = round(p / unit) * unit
        if abs(p - nearest) <= 2.0:
            # 1 tick before for shorts (sell limit), 1 tick before for longs
            # (TP is a sell limit when long): subtract 1 tick to queue ahead
            return round(nearest - NQ_TICK_PRICE * 4, 2)   # 1 pt ahead
    return round(p, 2)


def decide_and_act(snap: Dict[str, Any], eng: SimEngine,
                   use_claude: bool = True) -> Dict[str, Any]:
    """Core decision loop. Returns a status dict for logging."""
    pax = snap.get("pax") or {}
    dec = pax.get("decision") or "WAIT"
    size_tier = pax.get("size_tier") or "NONE"
    qty = SIZE_BY_TIER.get(size_tier, 0)

    # Bail-outs
    if dec in ("WAIT", "STAND_DOWN") or qty == 0:
        return {"action": "no-op", "reason": dec}
    if has_open_position(eng):
        return {"action": "no-op", "reason": "position already open"}
    if has_pending_entry(eng):
        return {"action": "no-op", "reason": "entry already pending"}

    level = find_target_level(snap)
    if level is None:
        return {"action": "no-op", "reason": "no proximate level"}

    # Determine side from decision
    if "LONG" in dec:  side = S_BUY
    elif "SHORT" in dec: side = S_SELL
    else: return {"action": "no-op", "reason": f"unknown decision {dec}"}

    entry_px = float(pax.get("entry") or level.get("price"))
    level_px = float(level.get("price"))

    # Ambiguity check — escalate to Claude when:
    #   - bias score in [-0.10, +0.10] (no conviction)
    #   - or vwap_bias and vp_bias disagree
    #   - or level confidence is in HALF tier
    flow = snap.get("flow") or {}
    biasScore = float(flow.get("biasScore") or 0)
    vw = (snap.get("vwap_bias") or {}).get("label")
    vp = (snap.get("vp_bias") or {}).get("label")
    ambiguous = (
        size_tier == "HALF"
        or abs(biasScore) < 0.10
        or (vw and vp and vw != "NEUTRAL" and vp != "NEUTRAL" and vw != vp)
    )

    claude_verdict: Optional[Dict[str, Any]] = None
    if ambiguous and use_claude:
        try:
            from .claude_cli import ask_claude
            claude_verdict = ask_claude(snap, level, agent_state={
                "position_size": 0, "pending_orders": 0,
                "qty_about_to_place": qty,
            })
            if claude_verdict.get("action") == "NO_TRADE":
                return {"action": "no-op", "reason": "Claude vetoed",
                        "claude": claude_verdict}
            if claude_verdict.get("size_tier") == "NONE":
                return {"action": "no-op", "reason": "Claude size NONE",
                        "claude": claude_verdict}
            # Allow Claude to upgrade or downgrade tier
            new_tier = claude_verdict.get("size_tier")
            if new_tier in SIZE_BY_TIER and new_tier != size_tier:
                qty = SIZE_BY_TIER[new_tier]
                size_tier = new_tier
        except Exception as e:
            sys.stderr.write(f"[trader] claude call failed: {e}\n")

    # Compute brackets — FOLLOW vs FADE dictates entry price + order type
    use_stop = "FOLLOW" in dec
    stop_loss, entry_limit, tps = compute_brackets(side, entry_px, level_px,
                                                    pax, snap, is_follow=use_stop)
    # FOLLOW = STOP-LIMIT (entry_stop = level price, limit = level + slip)
    # FADE   = pure LIMIT at entry_px (no stop_price)
    entry_stop = entry_px if use_stop else None
    bracket = eng.place_bracket(
        side=side, qty=qty,
        entry_stop=entry_stop,
        entry_limit=entry_limit,
        stop_loss=stop_loss,
        take_profits=tps,
        reason=f"{dec} @ {level.get('label')} | conv={pax.get('confidence')}",
        decision_tag=f"{dec}@{level.get('label')}",
    )

    return {
        "action": "placed_bracket",
        "decision": dec, "size_tier": size_tier, "qty": qty,
        "side": side, "entry_px": entry_px, "entry_limit": entry_limit,
        "stop_loss": stop_loss, "take_profits": tps,
        "level": level.get("label"), "claude": claude_verdict,
        "ids": bracket,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Autonomous Pax sim trader")
    ap.add_argument("--alias",      default=None,
                    help="Instrument alias (default: first attached)")
    ap.add_argument("--poll",       type=float, default=1.0)
    ap.add_argument("--once",       action="store_true",
                    help="Single tick then exit (for tests)")
    ap.add_argument("--no-claude",  action="store_true",
                    help="Skip Claude CLI subprocess on ambiguous cases")
    args = ap.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "pax-trader.log"
    eng: Optional[SimEngine] = None
    coll = PaxCollector()

    print(f"[pax_trader] starting · log={log_path} · cache_db={cache_mod.CACHE_DB}", flush=True)
    while True:
        snap = fetch_snap()
        if snap and snap.get("health") == "ok" and snap.get("alias"):
            alias = args.alias or snap["alias"]
            if eng is None or eng.alias != alias:
                eng = SimEngine(alias=alias)
                print(f"[pax_trader] sim engine attached to {alias}", flush=True)
            # SimEngine — process fills & TIF
            tick_result = eng.tick(snap)
            # Proximity collector
            coll_result = coll.tick(snap)
            # Decision + place new bracket if appropriate
            decision = decide_and_act(snap, eng, use_claude=not args.no_claude)
            # Log every non-trivial event
            if (tick_result.get("actions") or
                coll_result.get("summary") or
                decision.get("action") != "no-op"):
                row = {
                    "ts": dt.datetime.now().isoformat(timespec="seconds"),
                    "tick": tick_result, "collector": coll_result,
                    "decision": decision,
                }
                try:
                    with open(log_path, "a", encoding="utf-8") as fh:
                        fh.write(json.dumps(row, default=str) + "\n")
                except Exception: pass

        # Periodic cache maintenance. maintenance_tick() is itself rate-limited
        # (default 5 min between SQLite vacuums) so calling it every poll is
        # cheap; vacuum only fires on the slow cadence.
        try: cache_mod.maintenance_tick()
        except Exception: pass

        if args.once: break
        try: time.sleep(args.poll)
        except KeyboardInterrupt: break


if __name__ == "__main__":
    main()
