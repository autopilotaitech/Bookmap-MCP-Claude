"""Learned expectancy overlay for Pax setup selection.

This module is intentionally pure except for the explicit CSV loader. It turns
closed outcome rows into small setup statistics that pax_brain can use without
touching live order routes or model calls.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple


VERDICT_R = {
    "SUSTAINED_HIT": 1.50,
    "HIT": 1.00,
    "PARTIAL_HIT": 0.35,
    "STALE": 0.00,
    "MISS": -1.00,
}


@dataclass(frozen=True)
class ExpectancyStats:
    n: int
    avg_r: float
    hit_rate: float
    partial_rate: float
    miss_rate: float


def verdict_to_r(verdict: Any) -> Optional[float]:
    return VERDICT_R.get(str(verdict or "").upper())


def side_from_regime(regime: Any) -> Optional[str]:
    r = str(regime or "").upper()
    if r == "ACCUMULATION":
        return "LONG"
    if r == "DISTRIBUTION":
        return "SHORT"
    return None


def normalize_level(level: Any) -> str:
    label = str(level or "").upper()
    if label in ("OR-H", "OR_HIGH", "ORHIGH"):
        return "OR-H"
    if label in ("OR-L", "OR_LOW", "ORLOW"):
        return "OR-L"
    if label in ("TREND", "EXT", "EXTENSION"):
        return "TREND"
    return label or "*"


def setup_key(kind: Any = "*", side: Any = "*", level: Any = "*",
              session_type: Any = "*") -> str:
    return "|".join((
        str(session_type or "*").upper(),
        str(kind or "*").upper(),
        str(side or "*").upper(),
        normalize_level(level),
    ))


def _stats(values: List[Tuple[str, float]]) -> Optional[ExpectancyStats]:
    if not values:
        return None
    n = len(values)
    avg = sum(v for _, v in values) / n
    hits = sum(1 for verdict, _ in values if verdict in ("HIT", "SUSTAINED_HIT"))
    partials = sum(1 for verdict, _ in values if verdict == "PARTIAL_HIT")
    misses = sum(1 for verdict, _ in values if verdict == "MISS")
    return ExpectancyStats(
        n=n,
        avg_r=round(avg, 4),
        hit_rate=round(hits / n, 4),
        partial_rate=round(partials / n, 4),
        miss_rate=round(misses / n, 4),
    )


def summarize_ifl_rows(rows: Iterable[Mapping[str, Any]]) -> Dict[str, ExpectancyStats]:
    buckets: Dict[str, List[Tuple[str, float]]] = {}
    for row in rows:
        verdict = str(row.get("verdict") or "").upper()
        r_value = verdict_to_r(verdict)
        side = side_from_regime(row.get("regime"))
        if r_value is None or side is None:
            continue
        level = normalize_level(row.get("commit_level"))
        for key in (
            setup_key("*", side, level, "*"),
            setup_key("*", side, "*", "*"),
            setup_key("*", "*", level, "*"),
            setup_key("*", "*", "*", "*"),
        ):
            buckets.setdefault(key, []).append((verdict, r_value))
    return {key: stat for key, values in buckets.items()
            if (stat := _stats(values)) is not None}


def load_ifl_rows(path: Path) -> List[Dict[str, str]]:
    p = Path(path)
    if not p.exists():
        return []
    with p.open("r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_ifl_stats(path: Path) -> Dict[str, ExpectancyStats]:
    return summarize_ifl_rows(load_ifl_rows(path))


def stats_for_setup(stats: Mapping[str, ExpectancyStats],
                    *, kind: Any, side: Any, level: Any,
                    session_type: Any) -> Optional[ExpectancyStats]:
    candidates = (
        setup_key(kind, side, level, session_type),
        setup_key("*", side, level, session_type),
        setup_key(kind, side, level, "*"),
        setup_key("*", side, level, "*"),
        setup_key("*", side, "*", "*"),
        setup_key("*", "*", level, "*"),
        setup_key("*", "*", "*", "*"),
    )
    for key in candidates:
        found = stats.get(key)
        if found is not None:
            return found
    return None


def blend_expectancy(heuristic: float, observed_avg_r: float, n: int) -> float:
    if n < 3:
        return round(max(-1.0, min(1.0, heuristic)), 4)
    observed = max(-1.0, min(1.0, observed_avg_r))
    weight = 0.30 if n < 10 else 0.55
    blended = heuristic * (1.0 - weight) + observed * weight
    return round(max(-1.0, min(1.0, blended)), 4)


def adjust_expectancy(heuristic: float,
                      stats: Optional[ExpectancyStats]) -> Tuple[float, str]:
    if stats is None:
        return round(max(-1.0, min(1.0, heuristic)), 4), "heuristic"
    adjusted = blend_expectancy(heuristic, stats.avg_r, stats.n)
    return adjusted, f"learned:n={stats.n}:avgR={stats.avg_r:+.2f}"
