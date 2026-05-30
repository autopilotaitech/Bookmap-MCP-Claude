"""Honest per-setup promotion report (STAGE 3).

Thin wrapper that turns the existing SIM scorecard (``pax_trade_learning``
shape) into an operator-facing promotion view, reusing
``pax_eval_state.setup_eligibility`` for the eligible/ineligible verdict so this
module invents NO new profitability logic.

Promotion ladder (honest, conservative):
- ``insufficient_sample`` -- below ``min_samples``.
- ``blocked``             -- enough samples but non-positive expectancy.
- ``exploratory``         -- enough samples, positive but below candidate gates.
- ``candidate``           -- enough samples AND meets candidate net/avg-R gates.
- ``validated``           -- NEVER assigned here. Promotion past ``candidate``
  requires the existing human + replay + paper-pass gate
  (``pax_policy_replay`` ladder); this report tops out at ``candidate`` by
  design and live trading stays hard-blocked.

If no outcome data exists, every setup reports ``insufficient_sample`` and the
report says so -- it does not fabricate a sample.

CLI:
    python -m bookmap_mcp.pax_promotion_report \\
        --scorecard D:\\BookmapLogs\\pax-agent\\scorecard.json \\
        --out reports\\promotion-report.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import pax_eval_state


def _num(x: Any) -> Optional[float]:
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def build_promotion_report(scorecard: Optional[Dict[str, Any]],
                           *,
                           thresholds: Optional[Dict[str, Any]] = None,
                           now_ms: Optional[int] = None) -> Dict[str, Any]:
    """Pure: scorecard dict -> promotion report dict. Deterministic except
    ``generated_ms`` (pin with ``now_ms``)."""
    scorecard = scorecard if isinstance(scorecard, dict) else {}
    t = {**pax_eval_state._DEFAULTS, **(thresholds or {})}

    # Reuse the audited eligibility verdict (sample-gated, positive-expectancy).
    elig = pax_eval_state.setup_eligibility(scorecard, t)
    elig_by_setup = {e.get("setup"): e for e in elig}

    raw_setups = scorecard.get("setups") or []
    rows: List[Dict[str, Any]] = []
    status_counts: Counter = Counter()

    for s in raw_setups:
        if not isinstance(s, dict):
            continue
        setup = s.get("setup")
        n = int(_num(s.get("n")) or 0)
        avg_r = _num(s.get("mean_realized_r"))
        if avg_r is None:
            avg_r = _num(s.get("avg_r"))
        hit_rate = _num(s.get("hit_rate"))
        net_r = round(avg_r * n, 6) if avg_r is not None else None
        # max_drawdown is not present in the SIM scorecard payload -> unavailable
        # (never faked). Same for a calibration bucket on this path.
        max_drawdown = _num(s.get("max_drawdown_r"))
        calibration_bucket = s.get("calibration_bucket")

        reasons: List[str] = []
        ev = elig_by_setup.get(setup) or {}
        if n < int(scorecard.get("min_samples") or t["min_samples"]):
            status = "insufficient_sample"
            reasons.append(ev.get("reason")
                           or f"n={n} < min_samples")
        elif avg_r is None or avg_r <= 0:
            status = "blocked"
            reasons.append(f"non_positive_expectancy (avgR={avg_r})")
        else:
            meets_candidate = (avg_r >= t["candidate_avg_r"]
                               and net_r is not None
                               and net_r >= t["candidate_net_r"])
            if meets_candidate:
                status = "candidate"
                reasons.append(
                    f"meets candidate gates (avgR={avg_r:.3f} >= "
                    f"{t['candidate_avg_r']}, netR={net_r:.2f} >= "
                    f"{t['candidate_net_r']})")
                reasons.append("validated NOT auto-assigned: requires human + "
                               "replay + paper-pass gate; live hard-blocked")
            else:
                status = "exploratory"
                reasons.append(
                    f"positive but below candidate gates (avgR={avg_r:.3f} vs "
                    f"{t['candidate_avg_r']}, netR={net_r} vs "
                    f"{t['candidate_net_r']})")
        if s.get("warning"):
            reasons.append(str(s.get("warning")))

        status_counts[status] += 1
        rows.append({
            "setup": setup,
            "n": n,
            "avg_r": round(avg_r, 6) if avg_r is not None else None,
            "net_r": net_r,
            "win_rate": round(hit_rate, 6) if hit_rate is not None else None,
            "max_drawdown": max_drawdown,           # None when unavailable
            "calibration_bucket": calibration_bucket,
            "promotion_status": status,
            "reasons": reasons,
        })

    rows.sort(key=lambda r: str(r.get("setup")))
    has_data = bool(raw_setups)
    limitations: List[str] = [
        "SIM-only; no live-market edge claim. live trading hard-blocked.",
        "max_drawdown / calibration_bucket are unavailable in the SIM scorecard "
        "payload and reported null, never fabricated.",
        "'validated' is never auto-assigned: candidate is the ceiling here.",
    ]
    if not has_data:
        limitations.insert(0, "no scorecard/outcome data: insufficient sample "
                              "for every setup (nothing fabricated).")

    return {
        "generated_ms": int(now_ms) if now_ms is not None else int(time.time() * 1000),
        "min_samples": int(scorecard.get("min_samples") or t["min_samples"]),
        "thresholds": {
            "min_samples": t["min_samples"],
            "candidate_net_r": t["candidate_net_r"],
            "candidate_avg_r": t["candidate_avg_r"],
        },
        "has_outcome_data": has_data,
        "setups": rows,
        "counts": dict(status_counts),
        "candidate_count": status_counts.get("candidate", 0),
        "live_blocked": True,
        "limitations": limitations,
    }


def load_scorecard(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bookmap_mcp.pax_promotion_report",
        description="Honest per-setup promotion report from the SIM scorecard.")
    p.add_argument("--scorecard", type=Path,
                   default=Path(r"D:\BookmapLogs\pax-agent\scorecard.json"))
    p.add_argument("--out", type=Path, default=None,
                   help="Write the report JSON here (else stdout).")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_promotion_report(load_scorecard(args.scorecard))
    body = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(body, encoding="utf-8")
        print(f"promotion report written: {args.out}", file=sys.stderr)
    else:
        print(body)
    return 0


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
