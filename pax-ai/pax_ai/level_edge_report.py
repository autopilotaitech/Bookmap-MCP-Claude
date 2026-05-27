"""Read-only daily report over closed level-edge JSONL rows.

Slice 4 of the live edge loop (reports/pax-ai-full-project-audit-2026-05-26.md).
Reads `%LOCALAPPDATA%\\pax-ai\\level-edge-log\\YYYY-MM-DD.closed.jsonl` and
summarizes hit rates, R-multiples, and per-bucket breakdowns. Pure read --
NEVER mutates logs, never applies training changes, never calls Claude.

CLI:
    python -m pax_ai.level_edge_report --date 2026-05-27
    python -m pax_ai.level_edge_report --date 2026-05-27 --json
    python -m pax_ai.level_edge_report --date 2026-05-27 --root C:\\path
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import level_edge_log


_HORIZON_KEYS = ("realized_R_15s", "realized_R_60s", "realized_R_300s")
_HORIZON_LABELS = ("15s", "60s", "300s")

_GROUP_BY = (
    ("setup",             lambda r: r.get("setup") or "UNKNOWN_SETUP"),
    ("direction",         lambda r: r.get("direction") or "UNKNOWN"),
    ("size_tier",         lambda r: r.get("size_tier") or "UNKNOWN"),
    ("level_label",       lambda r: r.get("level_label") or "UNKNOWN"),
    ("confidence_bucket", lambda r: _confidence_bucket(r.get("confidence"))),
    ("top_drivers",       lambda r: _top_drivers_bundle(r.get("top_drivers"))),
)


# ---------------------------------------------------------------------------
# Bucket helpers
# ---------------------------------------------------------------------------

def _confidence_bucket(conf: Any) -> str:
    if conf is None:
        return "unknown"
    try:
        c = float(conf)
    except (TypeError, ValueError):
        return "unknown"
    if c < 0.35:
        return "<0.35"
    if c < 0.50:
        return "0.35-0.50"
    if c < 0.65:
        return "0.50-0.65"
    return ">=0.65"


def _top_drivers_bundle(drivers: Any) -> str:
    """Stable analytical key for a top_drivers list.

    Slice 5 normalization: the live `top_drivers` list is ranked by
    `|score x weight|`, which jitters poll-to-poll, so the same triplet
    can appear in several raw orderings. For reporting purposes we cap
    at the first 3 and then sort alphabetically so the report groups by
    the *set* of impact drivers, not their per-poll impact order. This
    affects the report only -- live signal, JSONL log, and chart
    rendering all preserve the impact order from the source row.
    """
    if not isinstance(drivers, list) or not drivers:
        return "none"
    parts: List[str] = []
    for d in drivers[:3]:
        if isinstance(d, str) and d:
            parts.append(d)
    if not parts:
        return "none"
    parts.sort()
    return "+".join(parts)


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def read_closed(path: Path) -> Tuple[List[Dict[str, Any]], int]:
    """Read a closed JSONL file. Returns (valid rows, malformed count).

    A row is malformed when (a) the line is not parseable as JSON, or
    (b) the parsed value is not a JSON object. Missing fields on an
    otherwise-well-formed dict are NOT malformed; downstream stats simply
    treat the missing horizon as null.
    """
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


# ---------------------------------------------------------------------------
# Pure stat helpers
# ---------------------------------------------------------------------------

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


def _horizon_stats(rows: List[Dict[str, Any]], key: str) -> Dict[str, Any]:
    vals = _numeric(r.get(key) for r in rows)
    return {
        "n":        len(vals),
        "hit_rate": _hit_rate(vals),
        "mean_R":   _mean(vals),
        "median_R": _median(vals),
    }


def _invalidated_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    flags = [bool(r.get("invalidated")) for r in rows if "invalidated" in r]
    count = sum(1 for f in flags if f)
    rate = (count / float(len(flags))) if flags else None
    return {"count": count, "rate": rate, "n": len(flags)}


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
        r60 = _numeric(r.get("realized_R_60s") for r in group_rows)
        inv = _invalidated_stats(group_rows)
        out.append({
            "name":             name,
            "n":                len(group_rows),
            "hit_rate_60s":     _hit_rate(r60),
            "mean_R_60s":       _mean(r60),
            "median_R_60s":     _median(r60),
            "invalidated_rate": inv["rate"],
        })
    # Sort by descending n, then ascending name.
    out.sort(key=lambda d: (-d["n"], d["name"]))
    return out


# ---------------------------------------------------------------------------
# Top-level summarizer
# ---------------------------------------------------------------------------

def summarize(rows: List[Dict[str, Any]], *,
              date: str, path: Path, n_skipped: int = 0) -> Dict[str, Any]:
    horizons: Dict[str, Dict[str, Any]] = {}
    for key, label in zip(_HORIZON_KEYS, _HORIZON_LABELS):
        horizons[label] = _horizon_stats(rows, key)
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for gname, key_fn in _GROUP_BY:
        groups[gname] = _group_summary(rows, key_fn)
    return {
        "date":        date,
        "path":        str(path),
        "n_signals":   len(rows),
        "n_skipped":   n_skipped,
        "horizons":    horizons,
        "invalidated": _invalidated_stats(rows),
        "groups":      groups,
    }


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _fmt_num(v: Optional[float], width: int = 5) -> str:
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
    lines.append(f"Pax AI level-edge report {date}")
    lines.append("=" * 36)
    if n_signals == 0:
        lines.append(f"No closed level-edge signals for {date}")
        lines.append(f"Path: {path}")
        if n_skipped:
            lines.append(f"Skipped malformed rows: {n_skipped}")
        return "\n".join(lines) + "\n"

    lines.append(f"Closed signals: {n_signals}")
    lines.append(f"Skipped malformed: {n_skipped}")
    lines.append(f"Source: {path}")
    lines.append("")
    lines.append("Horizons:")
    for label in _HORIZON_LABELS:
        h = summary["horizons"][label]
        lines.append(
            f"  +{label:<5} n={h['n']:<3} hit_rate={_fmt_rate(h['hit_rate'])}"
            f"  mean_R={_fmt_num(h['mean_R'])}"
            f"  median_R={_fmt_num(h['median_R'])}"
        )
    inv = summary["invalidated"]
    inv_rate = _fmt_rate(inv.get("rate"))
    lines.append("")
    lines.append(f"Invalidated: {inv['count']} / {inv['n']}  ({inv_rate})")

    group_titles = {
        "setup":             "By setup",
        "direction":         "By direction",
        "size_tier":         "By size_tier",
        "level_label":       "By level_label",
        "confidence_bucket": "By confidence bucket",
        "top_drivers":       "By top_driver bundle",
    }
    for gname, _ in _GROUP_BY:
        rows = summary["groups"][gname]
        lines.append("")
        lines.append(f"{group_titles[gname]}:")
        if not rows:
            lines.append("  (none)")
            continue
        for row in rows:
            lines.append(
                f"  {row['name']:<28} n={row['n']:<3}"
                f" hit_rate_60s={_fmt_rate(row['hit_rate_60s'])}"
                f" mean_R_60s={_fmt_num(row['mean_R_60s'])}"
                f" median_R_60s={_fmt_num(row['median_R_60s'])}"
                f" invalidated_rate={_fmt_rate(row['invalidated_rate'])}"
            )
    return "\n".join(lines) + "\n"


def format_json(summary: Dict[str, Any]) -> str:
    return json.dumps(summary, indent=2, sort_keys=False) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pax_ai.level_edge_report",
        description="Daily report over closed level-edge JSONL outcomes.")
    parser.add_argument("--date", required=True,
                        help="UTC date YYYY-MM-DD whose closed JSONL to read.")
    parser.add_argument("--root", default=None,
                        help="Override log root (default: "
                              "%%LOCALAPPDATA%%\\pax-ai\\level-edge-log).")
    parser.add_argument("--json", action="store_true",
                        help="Emit JSON summary instead of human text.")
    return parser.parse_args(argv)


def run(date: str, *, root: Optional[Path] = None,
         json_mode: bool = False) -> Tuple[int, str]:
    """Pure-by-(argument) entrypoint suitable for tests. Returns
    (exit_code, output_text). Never raises on missing files; returns
    exit 0 + empty-summary text/JSON."""
    closed_path = level_edge_log._closed_path(date, root)
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
