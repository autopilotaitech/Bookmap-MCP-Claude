"""Calibration reports for Pax AI forecasts.

Pairs structured forecast records (see ``pax_forecast_store``) with their
forward outcomes and reports reliability statistics by probability bucket,
setup signature, and horizon.

Hard rules:
- Read-only relative to ``pax_ai_config.json``, ``pax_weights.json``, and
  production prompts; this module never imports them.
- No live trading, broker, or order tools touched anywhere on this path.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from . import pax_forecast_schema as schema
from .pax_forecast_store import PaxForecastStore


OutcomeLookup = Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]


# --------------------------------------------------------------- time window

def utc_day_window(date_utc: str) -> Tuple[int, int]:
    """Return half-open ``[start_ms, end_ms)`` for the UTC day ``date_utc``."""
    try:
        d = datetime.strptime(date_utc, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(f"date_utc must be YYYY-MM-DD: {date_utc}") from exc
    start_ms = int(d.timestamp() * 1000)
    end_ms = start_ms + 86_400_000
    return start_ms, end_ms


# --------------------------------------------------------------- pure compute

def compute_calibration(
    pairs: Iterable[Tuple[Dict[str, Any], Optional[Dict[str, Any]]]],
    *,
    min_samples: int = 5,
    width: float = 0.05,
) -> Dict[str, Any]:
    """Aggregate forecast/outcome pairs into a calibration report.

    Each pair is ``(forecast_dict, outcome_dict_or_None)``. ``outcome_dict``
    must contain at least ``realized_r``; ``horizon_used_sec`` and ``source``
    are passed through into the per-bucket aggregation but are optional.
    """
    pair_list = list(pairs)

    pay_pairs: List[Tuple[Dict[str, Any], Optional[Dict[str, Any]]]] = []
    non_pay: List[Dict[str, Any]] = []
    for forecast, outcome in pair_list:
        if forecast.get("execution_read") == "PAY_FOR_TRADE":
            pay_pairs.append((forecast, outcome))
        else:
            non_pay.append(forecast)

    paired_pay = [(f, o) for f, o in pay_pairs if _has_outcome(o)]
    unpaired_pay = [(f, o) for f, o in pay_pairs if not _has_outcome(o)]

    global_block = _aggregate_block(paired_pay,
                                    total_forecasts=len(pay_pairs),
                                    total_unpaired=len(unpaired_pay))
    global_block["n_forecasts"] = len(pair_list)
    global_block["n_paired"] = len(paired_pay)
    global_block["n_unpaired"] = len(pair_list) - len(paired_pay)

    notes: List[str] = []
    probability_buckets = _group(paired_pay,
                                 key_fn=lambda f: schema.probability_bucket(
                                     f["prob_success"], width=width),
                                 min_samples=min_samples,
                                 notes=notes,
                                 kind="probability")
    setup_buckets = _group(paired_pay,
                           key_fn=schema.forecast_setup_bucket,
                           min_samples=min_samples,
                           notes=notes,
                           kind="setup")
    horizon_breakdown = _group_horizon(paired_pay, min_samples=min_samples,
                                       notes=notes)

    return {
        "generated_ms": int(time.time() * 1000),
        "min_samples": int(min_samples),
        "width": float(width),
        "global": global_block,
        "probability_buckets": probability_buckets,
        "setup_buckets": setup_buckets,
        "horizon_breakdown": horizon_breakdown,
        "non_pay_for_trade": {"n_forecasts": len(non_pay)},
        "notes": notes,
    }


# --------------------------------------------------------------- group helpers

def _group(pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]],
           *,
           key_fn: Callable[[Dict[str, Any]], str],
           min_samples: int,
           notes: List[str],
           kind: str) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Tuple[Dict[str, Any], Dict[str, Any]]]] = {}
    for forecast, outcome in pairs:
        buckets.setdefault(key_fn(forecast), []).append((forecast, outcome))

    rows: List[Dict[str, Any]] = []
    for key, group in sorted(buckets.items()):
        row = _aggregate_block(group)
        if kind == "probability":
            row["bucket"] = key
        else:
            row["setup"] = key
        if row["n_samples"] < min_samples:
            row["warning"] = "insufficient_sample_count"
            notes.append(f"{kind}:{key}: insufficient_sample_count "
                         f"({row['n_samples']} < {min_samples})")
        else:
            row["warning"] = None
        rows.append(row)
    return rows


def _group_horizon(pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]],
                   *,
                   min_samples: int,
                   notes: List[str]) -> Dict[int, Dict[str, Any]]:
    buckets: Dict[int, List[Tuple[Dict[str, Any], Dict[str, Any]]]] = {}
    for forecast, outcome in pairs:
        buckets.setdefault(int(forecast["horizon_sec"]), []).append((forecast, outcome))
    out: Dict[int, Dict[str, Any]] = {}
    for horizon, group in sorted(buckets.items()):
        row = _aggregate_block(group)
        if row["n_samples"] < min_samples:
            row["warning"] = "insufficient_sample_count"
            notes.append(f"horizon:{horizon}s: insufficient_sample_count "
                         f"({row['n_samples']} < {min_samples})")
        else:
            row["warning"] = None
        out[horizon] = row
    return out


def _aggregate_block(pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]],
                     *,
                     total_forecasts: Optional[int] = None,
                     total_unpaired: Optional[int] = None) -> Dict[str, Any]:
    n = len(pairs)
    if n == 0:
        block = {
            "n_samples": 0,
            "mean_stated_prob": 0.0,
            "actual_hit_rate": 0.0,
            "calibration_error": 0.0,
            "mean_realized_r": 0.0,
        }
    else:
        stated = sum(float(f["prob_success"]) for f, _ in pairs) / n
        wins = sum(1 for _, o in pairs if float(o["realized_r"]) > 0.0)
        rs = sum(float(o["realized_r"]) for _, o in pairs) / n
        hit_rate = wins / n
        block = {
            "n_samples": n,
            "mean_stated_prob": round(stated, 6),
            "actual_hit_rate": round(hit_rate, 6),
            "calibration_error": round(abs(stated - hit_rate), 6),
            "mean_realized_r": round(rs, 6),
        }
    if total_forecasts is not None:
        block["n_forecasts"] = total_forecasts
    if total_unpaired is not None:
        block["n_unpaired"] = total_unpaired
    return block


def _has_outcome(outcome: Optional[Dict[str, Any]]) -> bool:
    return bool(outcome) and outcome.get("realized_r") is not None


# --------------------------------------------------------------- bus outcomes

def bus_outcome_lookup(bus_db_path: Path) -> OutcomeLookup:
    """Return an OutcomeLookup that reads from a bus SQLite database.

    Looks for a ``trade_outcomes`` row keyed by ``forecast['source_turn_id']``
    and picks the ``realized_r_at_t{horizon_sec}s`` column. If the exact
    horizon column does not exist the lookup returns ``None`` rather than
    silently substituting a different horizon.
    """
    bus_db_path = Path(bus_db_path)

    def _lookup(forecast: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        turn_id = forecast.get("source_turn_id")
        horizon = int(forecast.get("horizon_sec") or 0)
        if turn_id is None or horizon <= 0:
            return None
        col = f"realized_r_at_t{horizon}s"
        try:
            conn = sqlite3.connect(str(bus_db_path))
        except sqlite3.Error:
            return None
        try:
            cur = conn.execute(f"PRAGMA table_info(trade_outcomes)")
            cols = {r[1] for r in cur.fetchall()}
            if col not in cols:
                return None
            cur = conn.execute(
                f"SELECT {col} FROM trade_outcomes WHERE ai_turn_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (int(turn_id),),
            )
            row = cur.fetchone()
            if row is None or row[0] is None:
                return None
            return {
                "realized_r": float(row[0]),
                "horizon_used_sec": horizon,
                "source": "bus_trade_outcomes",
            }
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    return _lookup


# --------------------------------------------------------------- day wrapper

def calibration_for_day(*,
                        forecasts_path: Path,
                        outcome_lookup: OutcomeLookup,
                        date_utc: str,
                        min_samples: int = 5,
                        width: float = 0.05) -> Dict[str, Any]:
    start_ms, end_ms = utc_day_window(date_utc)
    pairs: List[Tuple[Dict[str, Any], Optional[Dict[str, Any]]]] = []
    with PaxForecastStore(forecasts_path) as s:
        for forecast in s.iter_forecasts(start_ms=start_ms, end_ms=end_ms):
            pairs.append((forecast, outcome_lookup(forecast)))
    report = compute_calibration(pairs, min_samples=min_samples, width=width)
    report["date_utc"] = date_utc
    report["window"] = {"start_ms": start_ms, "end_ms": end_ms}
    return report


# ------------------------------------------------------------------------ CLI

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bookmap_mcp.pax_calibration",
        description="Calibration report for stored Pax AI forecasts.",
    )
    parser.add_argument("--date", required=True, help="UTC day YYYY-MM-DD")
    parser.add_argument("--forecasts", required=True, type=Path,
                        help="Path to pax_forecast_store SQLite DB")
    parser.add_argument("--bus-db", type=Path, default=None,
                        help="Optional path to bus DB containing trade_outcomes")
    parser.add_argument("--min-samples", type=int, default=5)
    parser.add_argument("--width", type=float, default=0.05)
    parser.add_argument("--report", type=Path, default=None,
                        help="Output JSON path (default reports/calibration-DATE.json)")
    args = parser.parse_args(argv)

    if args.bus_db is not None:
        lookup: OutcomeLookup = bus_outcome_lookup(args.bus_db)
    else:
        def lookup(_f: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            return None

    report = calibration_for_day(
        forecasts_path=args.forecasts,
        outcome_lookup=lookup,
        date_utc=args.date,
        min_samples=args.min_samples,
        width=args.width,
    )

    out_path = args.report or Path("reports") / f"calibration-{args.date}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True),
                        encoding="utf-8")
    print(f"calibration report written: {out_path}", file=sys.stderr)
    return 0


__all__ = [
    "OutcomeLookup",
    "bus_outcome_lookup",
    "calibration_for_day",
    "compute_calibration",
    "main",
    "utc_day_window",
]


if __name__ == "__main__":
    raise SystemExit(main())
