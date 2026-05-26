"""Deterministic policy replay + promotion gate for candidate lessons.

Reads:
- stored forecasts (via PaxForecastStore)
- forward outcomes (via an injectable lookup or an outcomes JSON)
- a candidates-JSON produced by pax_research_claude

For each candidate lesson it splits the forecast population time-ordered
into train / validation / test (default 60 / 20 / 20), evaluates the
current policy against a candidate policy (filter or downweight) on each
split, and emits a structured replay report.

Promotion is conservative:
- ``research_only`` is the default state.
- ``replay_passed`` requires the candidate to improve mean_realized_r on
  BOTH validation AND test, with test n_samples above ``min_samples``.
- ``paper_candidate`` is only assigned when the test split additionally
  shows hit_payline_pct improvement.
- ``paper_passed``, ``human_approved``, and ``active`` are NEVER assigned
  by this module.

This module:
- imports no broker, order, or live-data path,
- never writes to ``pax_ai_config.json`` / ``pax_weights.json`` /
  ``prompts.py`` / any production playbook,
- treats the candidates artifact as read-only.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from . import pax_forecast_schema as schema
from .pax_forecast_store import PaxForecastStore


OutcomeLookup = Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]


# ---------------------------------------------------------------- time split

def time_split(forecasts: List[Dict[str, Any]],
               *,
               train_pct: float = 0.6,
               val_pct: float = 0.2) -> Dict[str, List[Dict[str, Any]]]:
    """Split forecasts by ts_ms into ordered train / validation / test.

    Deterministic: identical input -> identical output. No random shuffle.
    """
    if not (0 < train_pct < 1):
        raise ValueError("train_pct must be in (0, 1)")
    if not (0 < val_pct < 1):
        raise ValueError("val_pct must be in (0, 1)")
    if train_pct + val_pct >= 1.0:
        raise ValueError("train_pct + val_pct must be < 1.0")

    ordered = sorted(forecasts, key=lambda f: (int(f.get("ts_ms") or 0),
                                               str(f.get("forecast_id") or "")))
    n = len(ordered)
    train_n = int(n * train_pct)
    val_n = int(n * val_pct)
    train = ordered[:train_n]
    val = ordered[train_n:train_n + val_n]
    test = ordered[train_n + val_n:]
    return {"train": train, "validation": val, "test": test}


# ---------------------------------------------------------------- filtering

def candidate_filter_for_lesson(
    lesson: Dict[str, Any],
) -> Callable[[Dict[str, Any]], bool]:
    """Translate a lesson into a per-forecast inclusion predicate.

    - ``filter``:     exclude forecasts whose setup_bucket equals target
    - ``downweight``: include every forecast (downweight is metadata-only
                      until expected-value reweighting lands)
    - anything else:  include every forecast
    """
    change = lesson.get("change") or {}
    kind = change.get("kind")
    target = change.get("target")

    if kind == "filter" and target:
        def _filter_excludes_target(forecast: Dict[str, Any]) -> bool:
            return schema.forecast_setup_bucket(forecast) != target
        return _filter_excludes_target

    return lambda _f: True


# ---------------------------------------------------------------- evaluate

def evaluate_policy(
    pairs: Iterable[Tuple[Dict[str, Any], Optional[Dict[str, Any]]]],
    *,
    filter_fn: Optional[Callable[[Dict[str, Any]], bool]] = None,
    invalidation_threshold_r: float = -1.0,
) -> Dict[str, Any]:
    """Aggregate forecast/outcome pairs into a policy-evaluation block."""
    pair_list = list(pairs)
    if filter_fn is not None:
        pair_list = [(f, o) for f, o in pair_list if filter_fn(f)]
    paired = [(f, o) for f, o in pair_list if _has_outcome(o)]
    unpaired = [p for p in pair_list if not _has_outcome(p[1])]

    n = len(paired)
    if n == 0:
        return {
            "n_samples": 0,
            "n_unpaired": len(unpaired),
            "mean_realized_r": 0.0,
            "median_realized_r": 0.0,
            "hit_payline_pct": 0.0,
            "hit_invalidation_pct": 0.0,
            "mfe_mean": None,
            "mae_mean": None,
        }

    rs = [float(o["realized_r"]) for _, o in paired]
    mean_r = sum(rs) / n
    median_r = statistics.median(rs)
    hits = sum(1 for r in rs if r > 0.0)
    invalids = sum(1 for r in rs if r <= invalidation_threshold_r)

    mfe_vals = [float(o["mfe_r"]) for _, o in paired if "mfe_r" in (o or {})]
    mae_vals = [float(o["mae_r"]) for _, o in paired if "mae_r" in (o or {})]

    return {
        "n_samples": n,
        "n_unpaired": len(unpaired),
        "mean_realized_r": round(mean_r, 6),
        "median_realized_r": round(median_r, 6),
        "hit_payline_pct": round(hits / n, 6),
        "hit_invalidation_pct": round(invalids / n, 6),
        "mfe_mean": (round(sum(mfe_vals) / len(mfe_vals), 6)
                     if mfe_vals else None),
        "mae_mean": (round(sum(mae_vals) / len(mae_vals), 6)
                     if mae_vals else None),
    }


def _has_outcome(outcome: Optional[Dict[str, Any]]) -> bool:
    return bool(outcome) and outcome.get("realized_r") is not None


# ---------------------------------------------------------------- replay

def replay_candidates(
    forecasts: Iterable[Dict[str, Any]],
    outcome_lookup: OutcomeLookup,
    candidates: List[Dict[str, Any]],
    *,
    min_samples: int = 30,
    train_pct: float = 0.6,
    val_pct: float = 0.2,
    improvement_epsilon: float = 1e-6,
) -> Dict[str, Any]:
    """Run the replay loop and emit a structured report."""
    forecast_list = list(forecasts)
    splits = time_split(forecast_list, train_pct=train_pct, val_pct=val_pct)

    def _pairs_for(fs: List[Dict[str, Any]]):
        return [(f, outcome_lookup(f)) for f in fs]

    paired_total = sum(1 for f in forecast_list
                       if _has_outcome(outcome_lookup(f)))

    results: List[Dict[str, Any]] = []
    for lesson in candidates:
        filter_fn = candidate_filter_for_lesson(lesson)
        splits_block: Dict[str, Any] = {}
        for name, fs in splits.items():
            pairs = _pairs_for(fs)
            cur = evaluate_policy(pairs)
            cand = evaluate_policy(pairs, filter_fn=filter_fn)
            splits_block[name] = {"current": cur, "candidate": cand}

        cur_test = splits_block["test"]["current"]
        cand_test = splits_block["test"]["candidate"]
        cur_val = splits_block["validation"]["current"]
        cand_val = splits_block["validation"]["candidate"]

        fp_reduction = max(0, cur_test["n_samples"] - cand_test["n_samples"])

        if cand_test["n_samples"] < min_samples:
            promotion = "research_only"
            reason = (f"insufficient_samples_on_test "
                      f"({cand_test['n_samples']} < {min_samples})")
        else:
            r_gain_val = (cand_val["mean_realized_r"] -
                          cur_val["mean_realized_r"])
            r_gain_test = (cand_test["mean_realized_r"] -
                           cur_test["mean_realized_r"])
            hit_gain_test = (cand_test["hit_payline_pct"] -
                             cur_test["hit_payline_pct"])

            if r_gain_val > improvement_epsilon and \
               r_gain_test > improvement_epsilon:
                if hit_gain_test > improvement_epsilon:
                    promotion = "paper_candidate"
                    reason = ("validation_and_test_r_improved AND "
                              "test_hit_payline_improved")
                else:
                    promotion = "replay_passed"
                    reason = "validation_and_test_r_improved"
            else:
                promotion = "research_only"
                reason = (f"did_not_improve_r "
                          f"(val_gain={r_gain_val:+.4f}, "
                          f"test_gain={r_gain_test:+.4f})")

        results.append({
            "lesson_id": lesson.get("lesson_id"),
            "setup": lesson.get("setup"),
            "change": lesson.get("change"),
            "splits": splits_block,
            "false_positive_reduction": fp_reduction,
            "promotion_status": promotion,
            "reason": reason,
            "calibration_impact": _calibration_impact(splits_block),
        })

    return {
        "generated_ms": int(time.time() * 1000),
        "n_forecasts": len(forecast_list),
        "n_paired": paired_total,
        "split_sizes": {k: len(v) for k, v in splits.items()},
        "min_samples": int(min_samples),
        "train_pct": train_pct,
        "val_pct": val_pct,
        "candidates": results,
    }


def _calibration_impact(splits_block: Dict[str, Any]) -> Dict[str, Any]:
    """Compute simple before/after deltas useful for the lesson digest."""
    test = splits_block.get("test", {})
    cur = test.get("current", {}) or {}
    cand = test.get("candidate", {}) or {}
    return {
        "test_mean_r_delta": round(
            float(cand.get("mean_realized_r", 0.0)) -
            float(cur.get("mean_realized_r", 0.0)),
            6),
        "test_hit_payline_delta": round(
            float(cand.get("hit_payline_pct", 0.0)) -
            float(cur.get("hit_payline_pct", 0.0)),
            6),
        "test_hit_invalidation_delta": round(
            float(cand.get("hit_invalidation_pct", 0.0)) -
            float(cur.get("hit_invalidation_pct", 0.0)),
            6),
    }


# ---------------------------------------------------------------- day wrapper

def replay_for_day(*,
                   forecasts_path: Path,
                   outcome_lookup: OutcomeLookup,
                   candidates_path: Path,
                   date_utc: Optional[str] = None,
                   min_samples: int = 30,
                   train_pct: float = 0.6,
                   val_pct: float = 0.2) -> Dict[str, Any]:
    candidates_doc = json.loads(Path(candidates_path).read_text(encoding="utf-8"))
    candidates = candidates_doc.get("lessons") or []

    with PaxForecastStore(forecasts_path) as s:
        if date_utc is None:
            forecasts = list(s.iter_forecasts())
        else:
            from .pax_calibration import utc_day_window
            start_ms, end_ms = utc_day_window(date_utc)
            forecasts = list(s.iter_forecasts(start_ms=start_ms, end_ms=end_ms))

    report = replay_candidates(forecasts, outcome_lookup, candidates,
                               min_samples=min_samples,
                               train_pct=train_pct,
                               val_pct=val_pct)
    if date_utc:
        report["date_utc"] = date_utc
    return report


# ------------------------------------------------------------------------ CLI

def _outcomes_from_json(path: Path) -> OutcomeLookup:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    by_turn_horizon: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for r in rows:
        sid = r.get("source_turn_id")
        if sid is None:
            continue
        horizon = int(r.get("horizon_used_sec") or 0)
        if horizon <= 0:
            continue
        by_turn_horizon[(int(sid), horizon)] = {
            "realized_r": float(r["realized_r"]),
            "horizon_used_sec": horizon,
            "source": r.get("source", "json"),
        }

    def _lookup(forecast: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        sid = forecast.get("source_turn_id")
        if sid is None:
            return None
        horizon = int(forecast.get("horizon_sec") or 0)
        if horizon <= 0:
            return None
        return by_turn_horizon.get((int(sid), horizon))

    return _lookup


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bookmap_mcp.pax_policy_replay",
        description="Replay current policy vs candidate lessons against "
                    "saved forecasts and outcomes.",
    )
    parser.add_argument("--forecasts", required=True, type=Path,
                        help="Path to pax_forecast_store SQLite DB")
    parser.add_argument("--candidates", required=True, type=Path,
                        help="Path to policy-candidates JSON from pax_research_claude")
    parser.add_argument("--report", required=True, type=Path,
                        help="Output replay JSON path")
    parser.add_argument("--date", default=None,
                        help="Optional UTC day YYYY-MM-DD to scope forecasts")
    parser.add_argument("--min-samples", type=int, default=30)
    parser.add_argument("--train-pct", type=float, default=0.6)
    parser.add_argument("--val-pct", type=float, default=0.2)

    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--bus-db", type=Path,
                     help="Bus DB with trade_outcomes for outcome lookup")
    src.add_argument("--outcomes-json", type=Path,
                     help="JSON list of {source_turn_id, realized_r, "
                          "horizon_used_sec} for deterministic replays/tests")

    args = parser.parse_args(argv)

    if args.outcomes_json is not None:
        lookup = _outcomes_from_json(args.outcomes_json)
    else:
        from .pax_calibration import bus_outcome_lookup
        lookup = bus_outcome_lookup(args.bus_db)

    report = replay_for_day(
        forecasts_path=args.forecasts,
        outcome_lookup=lookup,
        candidates_path=args.candidates,
        date_utc=args.date,
        min_samples=args.min_samples,
        train_pct=args.train_pct,
        val_pct=args.val_pct,
    )

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True),
                           encoding="utf-8")
    print(f"replay report written: {args.report}", file=sys.stderr)
    return 0


__all__ = [
    "OutcomeLookup",
    "candidate_filter_for_lesson",
    "evaluate_policy",
    "main",
    "replay_candidates",
    "replay_for_day",
    "time_split",
]


if __name__ == "__main__":
    raise SystemExit(main())
