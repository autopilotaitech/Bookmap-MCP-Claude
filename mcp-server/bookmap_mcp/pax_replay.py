"""Phase C — Replay analyzer for Claude / human feedback loop.

Reads N days of pax-agent-outcomes-*.csv (produced by pax_outcomes.py) and
produces an analysis report grouped by signal taxonomy. The output is JSON
plus a human-readable summary; Claude (or you) can read it and reason about
which signals are paying.

Example output:

  ABSORPTION_BID @ OR-L (n=24):
     r10m: +12.4 pts avg, hit 17/24 (71%)
     when vwap_bias=BULLISH: 14/16 (88%)  ← strong subgroup
     when ib_size=NARROW:    11/12 (92%)  ← strongest

  TRENDING_UP FOLLOW @ +1 (n=18):
     r10m: -3.2 pts avg, hit 7/18 (39%)
     when regime conf > 0.7:  6/8 (75%)  ← only worth taking with conviction

Output is also written as JSON to pax-replay-{date_range}.json so it can be
loaded by other tools (e.g. an LLM analysis pass).

Usage:
  python -m bookmap_mcp.pax_replay                         # all dates in PAX_LOG_DIR
  python -m bookmap_mcp.pax_replay 20260515 20260517       # date range
  python -m bookmap_mcp.pax_replay --json                  # JSON-only output
  python -m bookmap_mcp.pax_replay --horizon 10m           # subset to one horizon
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PAX_LOG_DIR = Path(os.environ.get("PAX_LOG_DIR", r"D:\BookmapLogs"))

# Subgroup dimensions we slice along
DIMENSIONS = ["regime", "vwapBias", "vpBias", "vwapSlope", "size_tier"]
HORIZONS = ["5m", "10m", "30m", "1h"]


def load_outcomes(date_glob: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for path in sorted(PAX_LOG_DIR.glob(date_glob)):
        try:
            with open(path, "r", encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    if (r.get("decision") or "").startswith("ENTER_"):
                        r["_file"] = path.name
                        rows.append(r)
        except Exception as e:
            sys.stderr.write(f"skip {path}: {e}\n")
    return rows


def _f(x: Any) -> Optional[float]:
    if x is None or x == "": return None
    try:
        v = float(x)
        if v != v: return None
        return v
    except (TypeError, ValueError):
        return None


def aggregate(rows: List[Dict[str, str]], horizon: str = "10m") -> Dict[str, Any]:
    """Group ENTER decisions by decision+level, then by single-dimension subgroups."""
    win_col = f"win{horizon}"
    r_col   = f"r{horizon}"

    # Top-level: by decision × level
    groups: Dict[Tuple[str, str], List[Dict[str, str]]] = defaultdict(list)
    for r in rows:
        key = (r.get("decision", "?"), r.get("level", "?"))
        groups[key].append(r)

    result: Dict[str, Any] = {"horizon": horizon, "n_total": len(rows), "groups": []}

    for (dec, lvl), grp in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        scored = [r for r in grp if _f(r.get(r_col)) is not None]
        n = len(scored)
        if n == 0: continue
        wins = sum(1 for r in scored if _f(r.get(win_col)) == 1.0)
        avg_r = sum(_f(r.get(r_col)) for r in scored) / n
        max_fav = sum(_f(r.get("maxFav5m")) or 0 for r in scored) / n
        max_adv = sum(_f(r.get("maxAdv5m")) or 0 for r in scored) / n

        # Subgroup analysis: for each dimension, find subgroups with materially
        # different winrate from the base (Δ > 15 percentage points and n ≥ 3)
        subgroups: List[Dict[str, Any]] = []
        base_rate = wins / n if n else 0
        for dim in DIMENSIONS:
            buckets: Dict[str, List[Dict[str, str]]] = defaultdict(list)
            for r in scored: buckets[r.get(dim, "") or "(empty)"].append(r)
            for val, sub in buckets.items():
                sn = len(sub)
                if sn < 3: continue
                sw = sum(1 for r in sub if _f(r.get(win_col)) == 1.0)
                sr = sw / sn
                delta = sr - base_rate
                if abs(delta) >= 0.15:
                    sub_avg = sum(_f(r.get(r_col)) or 0 for r in sub) / sn
                    subgroups.append({
                        "dim":      dim,
                        "value":    val,
                        "n":        sn,
                        "winrate":  round(sr, 3),
                        "delta":    round(delta, 3),
                        "avg_r":    round(sub_avg, 2),
                        "tag":      "STRONG" if delta >= 0.15 else "WEAK",
                    })
        # Sort: strongest positive delta first
        subgroups.sort(key=lambda s: -s["delta"])

        result["groups"].append({
            "decision": dec,
            "level":    lvl,
            "n":        n,
            "wins":     wins,
            "winrate":  round(base_rate, 3),
            "avg_r":    round(avg_r, 2),
            "avg_maxFav5m": round(max_fav, 2),
            "avg_maxAdv5m": round(max_adv, 2),
            "subgroups": subgroups,
        })

    return result


def render_text(report: Dict[str, Any]) -> str:
    out = [f"=== Pax replay analysis · horizon {report['horizon']} · n_total {report['n_total']} ===\n"]
    for g in report["groups"]:
        bar = "█" * int(g["winrate"] * 20) + "·" * (20 - int(g["winrate"] * 20))
        out.append(f"{g['decision']} @ {g['level']}  (n={g['n']})")
        out.append(f"  winrate {bar}  {g['winrate']:.0%}   avg r{report['horizon']}={g['avg_r']:+.2f}  "
                   f"maxFav5m={g['avg_maxFav5m']:+.2f} maxAdv5m={g['avg_maxAdv5m']:+.2f}")
        for s in g["subgroups"][:4]:
            arrow = "↑" if s["delta"] > 0 else "↓"
            out.append(f"    {arrow} {s['dim']}={s['value']:<14} n={s['n']:>3}  "
                       f"winrate {s['winrate']:.0%} ({s['delta']:+.0%})  avg_r {s['avg_r']:+.2f}")
        out.append("")
    return "\n".join(out)


def recommend(reports: Dict[str, Any], min_n: int = 5,
              big_winrate: float = 0.65, bad_winrate: float = 0.40) -> Dict[str, Any]:
    """Phase D: generate weight-change recommendations from replay reports.

    Walks every (decision, level) group and every subgroup; emits suggested
    multipliers for the subgroups with material edge (or anti-edge).

    Output structure:
      {
        "horizon": "10m",
        "recommendations": [
          {"key": "ENTER_LONG_FADE@OR-L|vwapBias=BULLISH",
           "current": 1.0, "suggested": 1.30,
           "reason": "90% winrate (n=10) vs base 73%",
           "confidence": "HIGH"},
          ...
        ],
        "summary": "1 strong boost, 1 strong cut"
      }
    """
    recs: List[Dict[str, Any]] = []
    for h, rep in reports.items():
        for g in rep.get("groups", []):
            base_rate = g["winrate"]
            for s in g.get("subgroups", []):
                if s["n"] < min_n: continue
                key = f"{g['decision']}@{g['level']}|{s['dim']}={s['value']}"
                if s["winrate"] >= big_winrate and s["delta"] >= 0.15:
                    mult = round(min(1.5, 1.0 + s["delta"] * 1.5), 2)
                    recs.append({
                        "horizon":   h,
                        "key":       key,
                        "current":   1.0,
                        "suggested": mult,
                        "reason":    f"{s['winrate']:.0%} winrate (n={s['n']}) vs base {base_rate:.0%}; avg r{h}={s['avg_r']:+.2f}",
                        "confidence":"HIGH" if s["n"] >= 10 else "MEDIUM",
                        "action":    "BOOST",
                    })
                elif s["winrate"] <= bad_winrate and s["delta"] <= -0.15:
                    mult = round(max(0.3, 1.0 + s["delta"] * 1.5), 2)
                    recs.append({
                        "horizon":   h,
                        "key":       key,
                        "current":   1.0,
                        "suggested": mult,
                        "reason":    f"{s['winrate']:.0%} winrate (n={s['n']}) vs base {base_rate:.0%}; avg r{h}={s['avg_r']:+.2f}",
                        "confidence":"HIGH" if s["n"] >= 10 else "MEDIUM",
                        "action":    "CUT",
                    })

    boosts = sum(1 for r in recs if r["action"] == "BOOST")
    cuts   = sum(1 for r in recs if r["action"] == "CUT")
    return {
        "recommendations": recs,
        "summary": f"{boosts} boosts, {cuts} cuts based on n>={min_n}",
        "apply_instructions": "Edit pax_weights.json → subgroup_overrides → paste these as {key: {multiplier, reason}}. Restart dashboard.",
    }


def render_recommendations(rec: Dict[str, Any]) -> str:
    out = [f"=== Weight recommendations ({rec['summary']}) ===\n"]
    for r in rec["recommendations"]:
        sym = "↑" if r["action"] == "BOOST" else "↓"
        out.append(f"  {sym} {r['key']}")
        out.append(f"     suggested × {r['suggested']}  ({r['confidence']} conf)  — {r['reason']}")
    if not rec["recommendations"]:
        out.append("  (no subgroups meet thresholds yet — need more data)")
    out.append("")
    out.append("To apply: edit pax_weights.json → subgroup_overrides, then restart dashboard.")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="Pax replay analyzer")
    ap.add_argument("from_date", nargs="?", default=None,  help="YYYYMMDD start")
    ap.add_argument("to_date",   nargs="?", default=None,  help="YYYYMMDD end (inclusive)")
    ap.add_argument("--horizon", default="10m", choices=HORIZONS, help="forward-return horizon")
    ap.add_argument("--json",    action="store_true", help="emit JSON only")
    ap.add_argument("--all-horizons", action="store_true",
                    help="produce a separate report for every horizon")
    ap.add_argument("--recommend", action="store_true",
                    help="emit Phase D weight-change recommendations")
    args = ap.parse_args()

    if args.from_date and args.to_date:
        # Build per-day pattern matching
        try:
            d0 = dt.date.fromisoformat(f"{args.from_date[:4]}-{args.from_date[4:6]}-{args.from_date[6:8]}")
            d1 = dt.date.fromisoformat(f"{args.to_date[:4]}-{args.to_date[4:6]}-{args.to_date[6:8]}")
        except Exception as e:
            sys.stderr.write(f"date parse fail: {e}\n"); sys.exit(2)
        rows: List[Dict[str, str]] = []
        d = d0
        while d <= d1:
            rows += load_outcomes(f"pax-agent-outcomes-{d.strftime('%Y%m%d')}.csv")
            d += dt.timedelta(days=1)
        tag = f"{args.from_date}_{args.to_date}"
    elif args.from_date:
        rows = load_outcomes(f"pax-agent-outcomes-{args.from_date}.csv")
        tag = args.from_date
    else:
        rows = load_outcomes("pax-agent-outcomes-*.csv")
        tag = "all"

    if not rows:
        print(f"No ENTER_* outcomes found in {PAX_LOG_DIR} for {tag}.")
        print("Run pax_outcomes.py first to backfill returns.")
        return

    horizons = HORIZONS if args.all_horizons else [args.horizon]
    reports = {h: aggregate(rows, h) for h in horizons}

    out_path = PAX_LOG_DIR / f"pax-replay-{tag}.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"tag": tag, "n_rows": len(rows), "reports": reports}, fh, indent=2)

    if args.json:
        out = {"reports": reports}
        if args.recommend: out["recommendations"] = recommend(reports)
        print(json.dumps(out, indent=2))
    else:
        for h, rep in reports.items():
            print(render_text(rep))
        if args.recommend:
            print(render_recommendations(recommend(reports)))
        print(f"\n→ JSON written to {out_path}")


if __name__ == "__main__":
    main()
