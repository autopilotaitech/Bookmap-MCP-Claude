"""Phase B — Forward-return outcome tracker for Pax agent signals.

Reads a `pax-agent-signals-YYYYMMDD.csv` and appends outcome columns by
joining each ENTER_* decision against subsequent prints in either:
  (a) a tick log if available, OR
  (b) the same CSV's own subsequent rows (decision context already has mid)

The output file `pax-agent-outcomes-YYYYMMDD.csv` adds:
  r5m, r10m, r30m, r1h   — price delta in NQ pts from entry to t+N
  win5m, win10m, ...     — 1 if delta aligned with decision direction, else 0
  maxFav5m, maxAdv5m     — max favorable / adverse excursion within 5min

Usage:
  python -m bookmap_mcp.pax_outcomes                     # today's CSV
  python -m bookmap_mcp.pax_outcomes 20260517            # specific date
  python -m bookmap_mcp.pax_outcomes --all               # every CSV in dir
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Same default as dashboard.py
PAX_LOG_DIR = Path(os.environ.get("PAX_LOG_DIR", r"D:\BookmapLogs"))

# Horizons we score
HORIZONS = [("5m", 5), ("10m", 10), ("30m", 30), ("1h", 60)]


def parse_iso(ts: str) -> Optional[dt.datetime]:
    try: return dt.datetime.fromisoformat(ts)
    except Exception: return None


def find_price_at(rows: List[Dict[str, str]], anchor_idx: int,
                  target_dt: dt.datetime, mid_field: str = "mid") -> Optional[float]:
    """Return the row's mid closest in time to target_dt, scanning forward from anchor_idx."""
    best_idx = None
    best_dt  = None
    for i in range(anchor_idx, len(rows)):
        rd = parse_iso(rows[i].get("ts_utc", ""))
        if rd is None: continue
        if rd >= target_dt:
            return _safe_float(rows[i].get(mid_field))
        best_idx = i; best_dt = rd
    if best_idx is not None and best_dt is not None:
        # Past EOF — use last available row only if it's "close" (within horizon × 1.5)
        gap = abs((target_dt - best_dt).total_seconds())
        if gap < 1800:   # within 30 min
            return _safe_float(rows[best_idx].get(mid_field))
    return None


def _safe_float(x: Any) -> Optional[float]:
    try:
        f = float(x)
        if f != f: return None
        return f
    except (TypeError, ValueError):
        return None


def process_csv(in_path: Path, out_path: Path) -> Dict[str, Any]:
    """Read input CSV, append forward-return columns, write to out_path."""
    if not in_path.exists():
        return {"_error": f"input not found: {in_path}"}

    with open(in_path, "r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    if not rows:
        return {"_error": "empty CSV", "input": str(in_path)}

    new_cols: List[str] = []
    for lbl, _ in HORIZONS:
        new_cols += [f"r{lbl}", f"win{lbl}"]
    new_cols += ["maxFav5m", "maxAdv5m", "outcomeNote"]

    out_rows = []
    n_scored = 0
    win_counts = {lbl: [0, 0] for lbl, _ in HORIZONS}    # [wins, total]

    for i, row in enumerate(rows):
        new_row = dict(row)
        for c in new_cols: new_row.setdefault(c, "")

        dec = (row.get("decision") or "").upper()
        if not dec.startswith("ENTER_"):
            out_rows.append(new_row); continue

        anchor_dt = parse_iso(row.get("ts_utc", ""))
        entry_px  = _safe_float(row.get("entry"))
        mid_px    = _safe_float(row.get("mid"))
        if anchor_dt is None or entry_px is None:
            new_row["outcomeNote"] = "missing anchor/entry"
            out_rows.append(new_row); continue

        direction = +1 if "LONG" in dec else (-1 if "SHORT" in dec else 0)
        if direction == 0:
            out_rows.append(new_row); continue

        # Forward returns
        scored_any = False
        for lbl, mins in HORIZONS:
            tgt = anchor_dt + dt.timedelta(minutes=mins)
            tgt_px = find_price_at(rows, i, tgt, "mid")
            if tgt_px is None: continue
            delta = tgt_px - entry_px
            new_row[f"r{lbl}"]   = round(delta, 2)
            new_row[f"win{lbl}"] = int((delta * direction) > 0)
            win_counts[lbl][1] += 1
            win_counts[lbl][0] += new_row[f"win{lbl}"]
            scored_any = True

        # Max favorable / adverse excursion over 5 min
        end5 = anchor_dt + dt.timedelta(minutes=5)
        fav = 0.0; adv = 0.0
        for j in range(i, len(rows)):
            jd = parse_iso(rows[j].get("ts_utc", ""))
            if jd is None or jd > end5: break
            jpx = _safe_float(rows[j].get("mid"))
            if jpx is None: continue
            d = (jpx - entry_px) * direction
            if d > fav: fav = d
            if d < adv: adv = d
        new_row["maxFav5m"] = round(fav, 2)
        new_row["maxAdv5m"] = round(adv, 2)

        if scored_any:
            n_scored += 1
        else:
            new_row["outcomeNote"] = "no subsequent rows in horizon"

        out_rows.append(new_row)

    fieldnames = list(rows[0].keys()) + [c for c in new_cols if c not in rows[0].keys()]
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for r in out_rows: w.writerow(r)

    summary = {
        "input":  str(in_path),
        "output": str(out_path),
        "rows": len(rows),
        "entries_scored": n_scored,
        "winrates": {lbl: (wt[0] / wt[1] if wt[1] else 0.0, wt[1]) for lbl, wt in win_counts.items()},
    }
    return summary


def render_summary(s: Dict[str, Any]) -> str:
    if s.get("_error"): return "ERROR: " + s["_error"]
    out = [f"Read {s['input']}", f"  rows: {s['rows']}, ENTER_* scored: {s['entries_scored']}"]
    for lbl, (rate, n) in s["winrates"].items():
        bar = "█" * int(rate * 20) + "·" * (20 - int(rate * 20))
        out.append(f"  win{lbl:<3} {bar} {rate:.0%}  (n={n})")
    out.append(f"  → wrote {s['output']}")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="Pax outcomes — backfill forward returns")
    ap.add_argument("date", nargs="?", default=None, help="YYYYMMDD; default today")
    ap.add_argument("--all", action="store_true",
                    help="process every pax-agent-signals-*.csv in PAX_LOG_DIR")
    args = ap.parse_args()

    if args.all:
        files = sorted(PAX_LOG_DIR.glob("pax-agent-signals-*.csv"))
        if not files:
            print(f"No CSVs in {PAX_LOG_DIR}"); return
        for f in files:
            m = re.search(r"-(\d{8})\.csv$", f.name)
            if not m: continue
            out = PAX_LOG_DIR / f"pax-agent-outcomes-{m.group(1)}.csv"
            print(render_summary(process_csv(f, out))); print()
        return

    date = args.date or dt.datetime.now().strftime("%Y%m%d")
    in_path  = PAX_LOG_DIR / f"pax-agent-signals-{date}.csv"
    out_path = PAX_LOG_DIR / f"pax-agent-outcomes-{date}.csv"
    print(render_summary(process_csv(in_path, out_path)))


if __name__ == "__main__":
    main()
