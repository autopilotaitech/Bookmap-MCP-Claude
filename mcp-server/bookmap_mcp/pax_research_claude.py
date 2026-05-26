"""Offline research path: turn calibration data into candidate lessons.

This module:
- Reads a calibration report (and optionally a tune-recommendations report).
- Deterministically extracts candidate lessons from buckets that look
  miscalibrated or negative-expectancy.
- Writes two artifacts:
    reports/policy-candidates-YYYY-MM-DD.json   (structured)
    reports/prompt-lessons-YYYY-MM-DD.md        (human / Claude prompt)

Hard rules baked into this module:
- Default mode is ``dry_run=True``: no subprocess, no Claude call.
- Promotion status is fixed at ``research_only`` for every candidate written
  here. Promotion past ``replay_passed`` belongs to ``pax_policy_replay``
  (and even there ``human_approved -> active`` is never automatic).
- Never imports or writes ``pax_ai_config.json``, ``pax_weights.json``,
  ``prompts.py``, or any production playbook artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


PROMOTION_STATUS = "research_only"


# ---------------------------------------------------------------- derivation

def derive_candidate_lessons(
    calibration: Dict[str, Any],
    *,
    overconfidence_threshold: float = 0.10,
    min_samples: int = 5,
) -> List[Dict[str, Any]]:
    """Walk the calibration setup_buckets and emit candidate lessons.

    Heuristics (deterministic, no LLM):
    1. Overconfident setup: ``mean_stated_prob - actual_hit_rate >= threshold``
       AND ``n_samples >= min_samples`` -> ``downweight`` (or ``filter`` when
       expectancy is also negative).
    2. Negative expectancy setup: ``mean_realized_r < 0`` over
       ``n_samples >= min_samples`` -> ``filter``.

    Both findings may fire for the same bucket; we collapse them so the
    same setup is not double-counted.
    """
    setup_buckets = calibration.get("setup_buckets") or []
    lessons: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for bucket in setup_buckets:
        n = int(bucket.get("n_samples") or 0)
        if n < min_samples:
            continue

        setup = str(bucket.get("setup") or "?")
        stated = float(bucket.get("mean_stated_prob") or 0.0)
        hit = float(bucket.get("actual_hit_rate") or 0.0)
        cal_err = float(bucket.get("calibration_error") or abs(stated - hit))
        mean_r = float(bucket.get("mean_realized_r") or 0.0)

        finding: Optional[str] = None
        change_kind: Optional[str] = None
        condition: Optional[str] = None

        overconf = (stated - hit) >= overconfidence_threshold
        neg_r = mean_r < 0.0

        if overconf and neg_r:
            finding = "overconfident_setup"
            change_kind = "filter"
            condition = ("Setup is overconfident AND negative expectancy over "
                         f"{n} samples")
        elif overconf:
            finding = "overconfident_setup"
            change_kind = "downweight"
            condition = (f"Stated probability exceeds actual hit rate by "
                         f"{cal_err:.2f} over {n} samples")
        elif neg_r:
            finding = "negative_expectancy"
            change_kind = "filter"
            condition = (f"Mean realized R is {mean_r:+.2f} over {n} samples")

        if finding is None:
            continue

        key = f"{setup}|{finding}|{change_kind}"
        if key in seen:
            continue
        seen.add(key)

        lessons.append({
            "lesson_id": _lesson_id(setup, finding, change_kind),
            "setup": setup,
            "finding": finding,
            "condition": condition,
            "change": {
                "kind": change_kind,
                "target": setup,
            },
            "evidence": {
                "stated_prob": round(stated, 6),
                "actual_hit_rate": round(hit, 6),
                "calibration_error": round(cal_err, 6),
            },
            "sample_count": n,
            "before_metrics": {
                "mean_realized_r": round(mean_r, 6),
                "actual_hit_rate": round(hit, 6),
                "n_samples": n,
            },
            "promotion_status": PROMOTION_STATUS,
        })

    return lessons


def _lesson_id(setup: str, finding: str, change_kind: Optional[str]) -> str:
    seed = "|".join([setup, finding, change_kind or "none"])
    h = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]
    return f"pax_lesson|{h}"


# ------------------------------------------------------------------- prompt

def build_research_prompt(*,
                          calibration: Dict[str, Any],
                          lessons: List[Dict[str, Any]],
                          tune_report: Optional[Dict[str, Any]] = None,
                          ) -> str:
    """Construct a Markdown prompt summarizing what was found.

    This text is suitable both as a human-readable lesson digest and as a
    deeper-reflection prompt for a (separately invoked, out-of-band) Claude
    call. The module itself never calls Claude in dry-run mode.
    """
    date_utc = calibration.get("date_utc") or "unknown-date"
    g = calibration.get("global") or {}

    lines: List[str] = []
    lines.append(f"# Pax AI research lessons - {date_utc}")
    lines.append("")
    lines.append("**Status:** research_only. None of these lessons are active.")
    lines.append("")
    lines.append("## Calibration summary")
    lines.append("")
    lines.append(f"- forecasts (total): {g.get('n_forecasts', 0)}")
    lines.append(f"- forecasts paired with outcomes: {g.get('n_paired', 0)}")
    lines.append(f"- mean stated probability: {g.get('mean_stated_prob', 0):.4f}")
    lines.append(f"- actual hit rate: {g.get('actual_hit_rate', 0):.4f}")
    lines.append(f"- calibration_error: {g.get('calibration_error', 0):.4f}")
    lines.append(f"- mean realized R: {g.get('mean_realized_r', 0):+.4f}")
    lines.append(f"- min_samples threshold: {calibration.get('min_samples', 0)}")
    lines.append("")

    lines.append("## Candidate lessons")
    lines.append("")
    if not lessons:
        lines.append("_No candidate lessons derived from this calibration window._")
    else:
        for lesson in lessons:
            lines.append(f"### {lesson['lesson_id']}")
            lines.append("")
            lines.append(f"- setup: `{lesson['setup']}`")
            lines.append(f"- finding: **{lesson['finding']}**")
            lines.append(f"- condition: {lesson['condition']}")
            lines.append(f"- proposed change: `{lesson['change']['kind']}` "
                         f"on `{lesson['change']['target']}`")
            ev = lesson["evidence"]
            lines.append(
                f"- evidence: stated={ev['stated_prob']:.4f} actual="
                f"{ev['actual_hit_rate']:.4f} calibration_error="
                f"{ev['calibration_error']:.4f}"
            )
            bm = lesson["before_metrics"]
            lines.append(
                f"- before_metrics: mean_realized_r={bm['mean_realized_r']:+.4f} "
                f"n_samples={bm['n_samples']}"
            )
            lines.append(f"- promotion_status: {lesson['promotion_status']}")
            lines.append("")

    if tune_report:
        lines.append("## Tune advisory context")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(tune_report, indent=2, sort_keys=True))
        lines.append("```")
        lines.append("")

    lines.append("## What to do with this report")
    lines.append("")
    lines.append("1. Run `pax_policy_replay` against these candidates on saved "
                 "forecasts/outcomes (time-ordered split).")
    lines.append("2. If replay improves validation AND test metrics on the "
                 "same setup buckets, the candidate may advance to "
                 "`replay_passed`.")
    lines.append("3. `human_approved -> active` is never automatic.")
    lines.append("")
    return "\n".join(lines)


# ----------------------------------------------------------------- artifacts

def write_candidate_artifacts(
    *,
    date_utc: str,
    calibration: Dict[str, Any],
    tune_report: Optional[Dict[str, Any]] = None,
    reports_dir: Path = Path("reports"),
    dry_run: bool = True,
    overconfidence_threshold: float = 0.10,
    min_samples: int = 5,
    invoker: Optional[Callable[[str], str]] = None,
) -> Dict[str, Path]:
    """Generate + persist the two candidate artifacts.

    ``dry_run=True`` (default) suppresses any external invocation. If a
    non-dry-run path is later wired up, ``invoker(prompt)`` will receive the
    Markdown prompt and is expected to return a textual response. The
    invocation result is appended to the Markdown artifact, never to the
    structured JSON or to any active config.
    """
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)

    lessons = derive_candidate_lessons(
        calibration,
        overconfidence_threshold=overconfidence_threshold,
        min_samples=min_samples,
    )

    payload = {
        "schema_version": 1,
        "generated_ms": int(time.time() * 1000),
        "date_utc": date_utc,
        "calibration_window": calibration.get("window"),
        "calibration_min_samples": calibration.get("min_samples"),
        "overconfidence_threshold": overconfidence_threshold,
        "promotion_status": PROMOTION_STATUS,
        "lessons": lessons,
        "notes": [],
    }
    if not lessons:
        payload["notes"].append("no_candidate_lessons")

    prompt_md = build_research_prompt(calibration=calibration,
                                      lessons=lessons,
                                      tune_report=tune_report)
    if not dry_run and invoker is not None:
        reply = invoker(prompt_md)
        prompt_md = (
            prompt_md
            + "\n## External response (research only)\n\n"
            + reply
            + "\n"
        )

    json_path = reports_dir / f"policy-candidates-{date_utc}.json"
    md_path = reports_dir / f"prompt-lessons-{date_utc}.md"

    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True),
                         encoding="utf-8")
    md_path.write_text(prompt_md, encoding="utf-8")

    return {"policy_candidates": json_path, "prompt_lessons": md_path}


# ------------------------------------------------------------------------ CLI

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bookmap_mcp.pax_research_claude",
        description="Generate research-only candidate lessons from a "
                    "calibration report.",
    )
    parser.add_argument("--date", required=True, help="UTC day YYYY-MM-DD")
    parser.add_argument("--calibration", required=True, type=Path,
                        help="Path to a calibration JSON (see pax_calibration)")
    parser.add_argument("--tune", type=Path, default=None,
                        help="Optional path to a tune-recommendations JSON")
    parser.add_argument("--out-dir", type=Path, default=Path("reports"))
    parser.add_argument("--min-samples", type=int, default=5)
    parser.add_argument("--overconfidence-threshold", type=float, default=0.10)
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="(Default.) Never invoke any subprocess.")
    args = parser.parse_args(argv)

    calibration = json.loads(args.calibration.read_text(encoding="utf-8"))
    tune_report = (json.loads(args.tune.read_text(encoding="utf-8"))
                   if args.tune else None)

    paths = write_candidate_artifacts(
        date_utc=args.date,
        calibration=calibration,
        tune_report=tune_report,
        reports_dir=args.out_dir,
        dry_run=True,  # CLI is dry-run-only on purpose.
        overconfidence_threshold=args.overconfidence_threshold,
        min_samples=args.min_samples,
    )

    print(f"wrote: {paths['policy_candidates']}", file=sys.stderr)
    print(f"wrote: {paths['prompt_lessons']}", file=sys.stderr)
    return 0


__all__ = [
    "PROMOTION_STATUS",
    "build_research_prompt",
    "derive_candidate_lessons",
    "main",
    "write_candidate_artifacts",
]


if __name__ == "__main__":
    raise SystemExit(main())
