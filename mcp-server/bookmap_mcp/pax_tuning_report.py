"""Report-only tuning-candidate suggestions for PAX SIM (read-only).

Proposes what to look at next from the existing evidence -- candidate setups,
blocked setups, under-sampled setups, what data to collect, and which thresholds
a HUMAN might review. It is strictly REPORT-ONLY:

- it NEVER writes runtime-policy.json, pax_weights.json, scorecard.json, or any
  production config,
- it NEVER auto-promotes or mutates strategy,
- there is intentionally no apply/promote flag (do not add one here).

Thin layer over ``pax_promotion_report`` (which reuses
``pax_eval_state.setup_eligibility``) + ``pax_evidence_report``. Reads
calibration.json / runtime-policy.json only for CONTEXT, never to change them.
SIM-only; live stays hard-blocked; no market-edge claim.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import pax_promotion_report

_DEF_LEARN = Path(r"D:\BookmapLogs\pax-agent")


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None


def build_tuning_report(*,
                        scorecard: Optional[Dict[str, Any]],
                        calibration: Optional[Dict[str, Any]] = None,
                        runtime_policy: Optional[Dict[str, Any]] = None,
                        now_ms: Optional[int] = None) -> Dict[str, Any]:
    """Pure: scorecard (+ optional context) -> report-only tuning suggestions.
    Writes nothing; deterministic except ``generated_ms``."""
    promo = pax_promotion_report.build_promotion_report(scorecard, now_ms=now_ms)
    setups = promo.get("setups") or []
    thresholds = promo.get("thresholds") or {}

    candidate_setups: List[Dict[str, Any]] = []
    blocked_setups: List[Dict[str, Any]] = []
    under_sampled_setups: List[Dict[str, Any]] = []
    next_data: List[str] = []
    threshold_review: List[Dict[str, Any]] = []

    for r in setups:
        if not isinstance(r, dict):
            continue
        status = r.get("promotion_status")
        row = {"setup": r.get("setup"), "n": r.get("n"),
               "avg_r": r.get("avg_r"), "net_r": r.get("net_r"),
               "win_rate": r.get("win_rate")}
        if status == "candidate":
            candidate_setups.append(row)
            threshold_review.append({
                "setup": r.get("setup"),
                "review": "candidate meets gates -- a HUMAN may review for paper "
                          "focus (NOT auto-promotion; candidate != validated)"})
        elif status == "blocked":
            blocked_setups.append(row)
            threshold_review.append({
                "setup": r.get("setup"),
                "review": "non-positive expectancy -- consider throttling this "
                          "setup (report-only; no policy written)"})
        elif status == "insufficient_sample":
            under_sampled_setups.append(row)

    if under_sampled_setups:
        next_data.append(
            f"collect more samples for {len(under_sampled_setups)} under-sampled "
            f"setup(s) (need n >= {thresholds.get('min_samples')})")
    if not setups:
        next_data.append("no scorecard/outcomes yet -- run armed SIM so "
                         "pax_trade_learning writes scorecard.json")
    if not candidate_setups and setups:
        next_data.append("no candidate setups yet -- accumulate samples toward "
                         f"avgR >= {thresholds.get('candidate_avg_r')} and "
                         f"netR >= {thresholds.get('candidate_net_r')}")

    return {
        "generated_ms": int(now_ms) if now_ms is not None else int(time.time() * 1000),
        "report_only": True,
        "policy_written": False,
        "thresholds": thresholds,
        "candidate_setups": candidate_setups,
        "blocked_setups": blocked_setups,
        "under_sampled_setups": under_sampled_setups,
        "suggested_next_data_to_collect": next_data,
        "suggested_threshold_review": threshold_review,
        "context": {
            "has_calibration": bool(calibration),
            "has_runtime_policy": bool(runtime_policy),
            "note": "calibration/runtime-policy read for context only; never "
                    "modified by this report",
        },
        "live_blocked": True,
        "limitations": [
            "SIM-only; live trading hard-blocked; no live-market edge claim.",
            "REPORT-ONLY: writes no policy; no auto-promotion; 'candidate' is "
            "NOT 'validated'.",
            "threshold reviews are suggestions for a HUMAN, derived from sample "
            "counts/expectancy only -- not a tuning decision.",
        ],
    }


def gather_tuning_report(*,
                         learn_dir: Optional[Path] = None,
                         out_path: Optional[Path] = None,
                         now_ms: Optional[int] = None) -> Dict[str, Any]:
    """Load inputs read-only and build the report. Writes ONLY ``out_path`` (the
    report itself) -- never any policy/config file."""
    learn = Path(learn_dir) if learn_dir else _DEF_LEARN
    scorecard = _load_json(learn / "scorecard.json")
    calibration = _load_json(learn / "calibration.json")
    runtime_policy = _load_json(learn / "runtime-policy.json")
    report = build_tuning_report(scorecard=scorecard, calibration=calibration,
                                 runtime_policy=runtime_policy, now_ms=now_ms)
    report["input_paths"] = {"learn_dir": str(learn),
                             "scorecard": str(learn / "scorecard.json")}
    if out_path is not None:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, sort_keys=True,
                                       default=str), encoding="utf-8")
    return report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bookmap_mcp.pax_tuning_report",
        description="REPORT-ONLY tuning suggestions from SIM evidence. Writes no "
                    "policy. SIM-only; live hard-blocked.")
    p.add_argument("--learn-dir", type=Path, default=None)
    p.add_argument("--out", type=Path, default=None)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    report = gather_tuning_report(learn_dir=args.learn_dir, out_path=args.out)
    body = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.out:
        print(f"tuning report written: {args.out} "
              f"(candidates={len(report['candidate_setups'])}, report-only)",
              file=sys.stderr)
    else:
        print(body)
    return 0


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
