"""Read-only daily report over closed attack-response JSONL rows.

Stage 6 of the chart-first overhaul (plan
reports/pax-ai-attack-response-plan-2026-05-27.md).

Reads `%LOCALAPPDATA%\\pax-ai\\attack-response-log\\YYYY-MM-DD.closed.jsonl`
and summarizes hit-rates per bucket. NEVER mutates logs. Never calls
Claude. Pure file -> stats pipeline.

CLI:
    python -m pax_ai.attack_response_report --date 2026-05-27
    python -m pax_ai.attack_response_report --date 2026-05-27 --json
    python -m pax_ai.attack_response_report --date 2026-05-27 --root C:\\path

`hit_rate` is `pts > 0` per horizon. `mean_R` / `median_R` are only
present when stop_price was set (today: never).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import attack_response_log


_HORIZON_PTS_KEYS = ("realized_pts_15s", "realized_pts_60s", "realized_pts_300s")
_HORIZON_R_KEYS   = ("realized_R_15s",   "realized_R_60s",   "realized_R_300s")
_HORIZON_LABELS   = ("15s",              "60s",              "300s")


def _or_width_bucket(width: Any) -> str:
    if width is None:
        return "unknown"
    try:
        w = float(width)
    except (TypeError, ValueError):
        return "unknown"
    if w < 5:    return "<5"
    if w < 10:   return "5-10"
    if w < 20:   return "10-20"
    if w < 30:   return "20-30"
    if w < 50:   return "30-50"
    return ">=50"


def _confidence_bucket(conf: Any) -> str:
    if conf is None:
        return "unknown"
    try:
        c = float(conf)
    except (TypeError, ValueError):
        return "unknown"
    if c < 0.40:  return "<0.40"
    if c < 0.55:  return "0.40-0.55"
    if c < 0.70:  return "0.55-0.70"
    return ">=0.70"


def _drivers_bundle(drivers: Any) -> str:
    if not isinstance(drivers, list) or not drivers:
        return "none"
    seen: List[str] = []
    for d in drivers[:5]:
        if isinstance(d, str) and d:
            seen.append(d)
    if not seen:
        return "none"
    seen.sort()
    return "+".join(seen)


def _time_bucket(ts_ms: Any) -> str:
    if ts_ms is None:
        return "unknown"
    try:
        ts = int(ts_ms)
    except (TypeError, ValueError):
        return "unknown"
    minutes_into_day = (ts // 60_000) % (24 * 60)
    hour = minutes_into_day // 60
    return f"H{hour:02d}"


_GROUP_BY: Tuple[Tuple[str, Any], ...] = (
    ("state",             lambda r: r.get("state") or "UNKNOWN"),
    ("bias",              lambda r: r.get("bias") or "UNKNOWN"),
    ("location",          lambda r: r.get("location") or "UNKNOWN"),
    ("drivers",           lambda r: _drivers_bundle(r.get("drivers"))),
    ("or_width",          lambda r: _or_width_bucket(r.get("or_width_pts"))),
    ("vwap_regime",       lambda r: r.get("vwap_regime") or "UNKNOWN"),
    ("flow_regime",       lambda r: r.get("flow_regime") or "UNKNOWN"),
    ("confidence_bucket", lambda r: _confidence_bucket(r.get("confidence"))),
    ("time_bucket",       lambda r: _time_bucket(r.get("ts_ms"))),
)


# --- I/O ----------------------------------------------------------------

def read_closed(path: Path) -> Tuple[List[Dict[str, Any]], int]:
    if not path.exists():
        return [], 0
    rows: List[Dict[str, Any]] = []
    skipped = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                skipped += 1
                continue
            if not isinstance(obj, dict):
                skipped += 1
                continue
            rows.append(obj)
    return rows, skipped


# --- pure stat helpers --------------------------------------------------

def _numeric(values: Iterable[Any]) -> List[float]:
    out: List[float] = []
    for v in values:
        if v is None:
            continue
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out


def _hit_rate(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return sum(1 for v in values if v > 0.0) / float(len(values))


def _mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return statistics.fmean(values)


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return statistics.median(values)


def _horizon_stats(rows: List[Dict[str, Any]], pts_key: str,
                    r_key: str) -> Dict[str, Any]:
    pts = _numeric(r.get(pts_key) for r in rows)
    r_values = _numeric(r.get(r_key) for r in rows)
    return {
        "n":          len(pts),
        "hit_rate":   _hit_rate(pts),
        "mean_pts":   _mean(pts),
        "median_pts": _median(pts),
        "mean_R":     _mean(r_values) if r_values else None,
        "median_R":   _median(r_values) if r_values else None,
    }


def _group_summary(rows: List[Dict[str, Any]],
                    name_of: Any) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        name = name_of(r)
        if not isinstance(name, str) or not name:
            name = "UNKNOWN"
        buckets.setdefault(name, []).append(r)
    out: List[Dict[str, Any]] = []
    for name, group_rows in buckets.items():
        pts60 = _numeric(r.get("realized_pts_60s") for r in group_rows)
        out.append({
            "name":           name,
            "n":              len(group_rows),
            "hit_rate_60s":   _hit_rate(pts60),
            "mean_pts_60s":   _mean(pts60),
            "median_pts_60s": _median(pts60),
        })
    out.sort(key=lambda d: (-d["n"], d["name"]))
    return out


# --- top-level summarizer -----------------------------------------------

def summarize(rows: List[Dict[str, Any]], *,
              date: str, path: Path, n_skipped: int = 0) -> Dict[str, Any]:
    horizons: Dict[str, Dict[str, Any]] = {}
    for label, pkey, rkey in zip(_HORIZON_LABELS, _HORIZON_PTS_KEYS,
                                  _HORIZON_R_KEYS):
        horizons[label] = _horizon_stats(rows, pkey, rkey)
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for gname, key_fn in _GROUP_BY:
        groups[gname] = _group_summary(rows, key_fn)
    return {
        "date":      date,
        "path":      str(path),
        "n_signals": len(rows),
        "n_skipped": n_skipped,
        "horizons":  horizons,
        "groups":    groups,
        "_warning":  "WATCH evidence only - NOT measured EDGE",
    }


# --- formatters ---------------------------------------------------------

def _fmt_num(v: Optional[float], width: int = 7) -> str:
    if v is None:
        return "--".rjust(width)
    return f"{v:+.2f}".rjust(width)


def _fmt_rate(v: Optional[float]) -> str:
    if v is None:
        return "--"
    return f"{v:.2f}"


def format_text(summary: Dict[str, Any]) -> str:
    lines: List[str] = []
    date = summary.get("date", "?")
    path = summary.get("path", "?")
    n_signals = summary.get("n_signals", 0)
    n_skipped = summary.get("n_skipped", 0)
    lines.append(f"Pax AI attack-response report {date}")
    lines.append("=" * 40)
    lines.append("WATCH evidence only - NOT measured EDGE")
    if n_signals == 0:
        lines.append("")
        lines.append(f"No closed attack-response signals for {date}")
        lines.append(f"Path: {path}")
        if n_skipped:
            lines.append(f"Skipped malformed rows: {n_skipped}")
        return "\n".join(lines) + "\n"
    lines.append("")
    lines.append(f"Closed signals: {n_signals}")
    lines.append(f"Skipped malformed: {n_skipped}")
    lines.append(f"Source: {path}")
    lines.append("")
    lines.append("Horizons:")
    for label in _HORIZON_LABELS:
        h = summary["horizons"][label]
        lines.append(
            f"  +{label:<5} n={h['n']:<3} hit_rate={_fmt_rate(h['hit_rate'])}"
            f"  mean_pts={_fmt_num(h['mean_pts'])}"
            f"  median_pts={_fmt_num(h['median_pts'])}"
        )

    titles = {
        "state":             "By state",
        "bias":              "By bias",
        "location":          "By location",
        "drivers":           "By driver bundle",
        "or_width":          "By OR width bucket",
        "vwap_regime":       "By VWAP regime",
        "flow_regime":       "By flow regime",
        "confidence_bucket": "By confidence bucket",
        "time_bucket":       "By time bucket",
    }
    for gname, _ in _GROUP_BY:
        rows = summary["groups"][gname]
        lines.append("")
        lines.append(f"{titles[gname]}:")
        if not rows:
            lines.append("  (none)")
            continue
        for row in rows:
            lines.append(
                f"  {row['name']:<28} n={row['n']:<3}"
                f" hit_rate_60s={_fmt_rate(row['hit_rate_60s'])}"
                f" mean_pts_60s={_fmt_num(row['mean_pts_60s'])}"
                f" median_pts_60s={_fmt_num(row['median_pts_60s'])}"
            )
    return "\n".join(lines) + "\n"


def format_json(summary: Dict[str, Any]) -> str:
    return json.dumps(summary, indent=2, sort_keys=False) + "\n"


# --- CLI ----------------------------------------------------------------

def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pax_ai.attack_response_report",
        description="Daily report over closed attack-response JSONL rows.")
    parser.add_argument("--date", required=True,
                        help="UTC date YYYY-MM-DD whose closed JSONL to read.")
    parser.add_argument("--root", default=None,
                        help="Override log root (default: "
                              "%%LOCALAPPDATA%%\\pax-ai\\attack-response-log).")
    parser.add_argument("--json", action="store_true",
                        help="Emit JSON instead of human text.")
    return parser.parse_args(argv)


def run(date: str, *, root: Optional[Path] = None,
         json_mode: bool = False) -> Tuple[int, str]:
    closed_path = attack_response_log._closed_path(date, root)
    rows, n_skipped = read_closed(closed_path)
    summary = summarize(rows, date=date, path=closed_path,
                          n_skipped=n_skipped)
    text = format_json(summary) if json_mode else format_text(summary)
    return 0, text


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    root = Path(args.root) if args.root else None
    code, text = run(args.date, root=root, json_mode=args.json)
    sys.stdout.write(text)
    sys.stdout.flush()
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
