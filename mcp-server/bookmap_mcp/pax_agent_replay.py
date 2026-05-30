"""Deterministic replay of the PAX decision path over saved JSONL logs.

Re-runs the SAME deterministic policy (``pax_loop.decide``) the live heartbeat
runs, but against saved records instead of the live bridge. It places NO orders,
calls NO LLM, and never touches live Bookmap -- it is a pure decision replay so
a session's reads can be re-derived and diffed deterministically.

This is NOT a duplicate of:
- ``pax_policy_replay`` (replays candidate *lessons* vs stored forecasts), or
- ``pax_replay`` (aggregates forward *outcomes* from CSV).
Those answer "did the signals pay?". This answers "given the saved snapshots,
what would the deterministic policy decide, and does it match what was logged?".

Input format
------------
A JSONL file. Each line is one record. A record is "usable" for replay only if
it carries a market snapshot under ``snapshot`` or ``snap`` (a dict). The live
``agent-loop.jsonl`` heartbeat records do NOT embed the raw snapshot, so real
agent logs are summarized (recorded actions / risk halts counted) but cannot be
re-decided -- this is reported honestly via ``usable_snapshot_count`` and a
``limitations`` note, never faked. Fixture logs (and any future snapshot-
embedding writer) carry the snapshot and are fully replayed.

Optional per-record fields used when present: ``ts_ms`` (replay clock; pure, no
wall-clock), ``status`` / ``sim_status`` (SIM status dict), ``action`` (recorded
action, for divergence), ``risk_halt`` / ``risk_halt_code`` (recorded enforced
halt, counted).

Determinism
-----------
Same input + same config -> byte-identical summary EXCEPT ``generated_ms`` (the
only wall-clock field; pass ``now_ms`` to pin it). The replay derives its clock
from each record's ``ts_ms``.

CLI
---
    python -m bookmap_mcp.pax_agent_replay --input path\\to\\agent-loop.jsonl \\
        --output reports\\agent-replay.json --limit 1000
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import pax_loop, pax_risk_gate   # pax_risk_gate is pure (no broker/LLM)
from . import pax_replay_clock           # pure replay clock (no wall-clock)

# Captured so the report is self-describing about the policy it replayed. These
# are the deterministic pax_loop knobs that shape a decision.
DETERMINISTIC_CONFIG: Dict[str, Any] = {
    "policy": "pax_loop.decide",
    "profile": pax_loop.PROFILE,
    "payline": pax_loop.PAYLINE,
    "rung": pax_loop.RUNG,
    "stop_breathing_pts": pax_loop.STOP_BREATHING_PTS,
    "cooldown_min": pax_loop.COOLDOWN_MIN,
    "daily_stop_losers": pax_loop.DAILY_STOP_LOSERS,
    "expectancy_stats": None,        # replay uses base policy, no learned stats
    "runtime_policy": None,          # replay uses base policy, no runtime policy
}

_FLAT_STATUS: Dict[str, Any] = {"position": {"size": 0}, "losers_today": 0,
                                "working": [], "fills_today": [],
                                "realized_today_usd": 0.0}


def _replay_input_of(record: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Return (replay_input_dict_or_None, malformed). ``malformed`` is True when
    a ``replay_input`` key is present but not a dict."""
    ri = record.get("replay_input")
    if isinstance(ri, dict):
        return ri, False
    if ri is not None:
        return None, True
    return None, False


def _snapshot_of(record: Dict[str, Any],
                 ri: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    # Prefer the embedded compact replay snapshot (live logs); fall back to the
    # legacy fixture shape (top-level snapshot/snap).
    if ri is not None:
        v = ri.get("snapshot")
        if isinstance(v, dict) and v:
            return v
    for key in ("snapshot", "snap"):
        v = record.get(key)
        if isinstance(v, dict) and v:
            return v
    return None


def _status_of(record: Dict[str, Any],
               ri: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if ri is not None:
        v = ri.get("status")
        if isinstance(v, dict):
            return v
    for key in ("status", "sim_status"):
        v = record.get(key)
        if isinstance(v, dict):
            return v
    return dict(_FLAT_STATUS)


def _recorded_halt(record: Dict[str, Any]) -> Optional[str]:
    code = record.get("risk_halt_code") or record.get("risk_halt")
    return str(code) if code else None


def _op_field(ri: Optional[Dict[str, Any]], record: Dict[str, Any],
              key: str, default: Any) -> Any:
    """Resolve an operational gate field: replay_input first, then a legacy
    top-level record field, then the default."""
    if ri is not None and key in ri:
        return ri.get(key)
    if key in record:
        return record.get(key)
    return default


def _num(x: Any) -> Optional[float]:
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _now_from(record: Dict[str, Any], snap: Dict[str, Any],
              ri: Optional[Dict[str, Any]] = None
              ) -> Tuple[Any, int, str]:
    """Deterministic replay clock (UTC datetime, epoch ms, source label).

    Delegates to ``pax_replay_clock`` -- the single pure clock authority. Never
    reads wall-clock; ``composedAtMs`` is NOT a clock source. Returns ts_ms=0 /
    source="none" when no real timestamp exists (reported as a limitation)."""
    res = pax_replay_clock.resolve_clock(record, snap, ri)
    now_dt = pax_replay_clock.replay_now_dt_utc(res.ts_ms)
    return now_dt, res.ts_ms, res.source


def iter_records(text: str) -> Tuple[List[Any], int]:
    """Parse JSONL text -> (records, malformed_count). A line that is not valid
    JSON, or parses to a non-dict, is counted malformed and skipped."""
    records: List[Any] = []
    malformed = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if not isinstance(obj, dict):
            malformed += 1
            continue
        records.append(obj)
    return records, malformed


def replay_records(records: List[Dict[str, Any]],
                   *,
                   malformed: int = 0,
                   input_path: Optional[str] = None,
                   limit: Optional[int] = None,
                   now_ms: Optional[int] = None,
                   risk_config: Optional["pax_risk_gate.RiskGateConfig"] = None
                   ) -> Dict[str, Any]:
    """Pure replay over already-parsed records. Returns a stable summary dict.

    Two layers, both pure (no orders, no LLM, no live Bookmap):
      1. Decision replay -- always runs ``pax_loop.decide``.
      2. OPTIONAL operational risk-gate replay -- runs
         ``pax_risk_gate.evaluate_entry_gate`` ONLY for entry plans that carry a
         real market-freshness timestamp. Records lacking it are reported as a
         limitation, never faked into a pass/fail.

    Deterministic except ``generated_ms`` (pin with ``now_ms``)."""
    if limit is not None and limit >= 0:
        records = records[:limit]

    action_counts: Counter = Counter()
    recorded_action_counts: Counter = Counter()
    setup_counts: Counter = Counter()
    risk_halt_counts: Counter = Counter()          # recorded (kept for compat)
    replayed_halt_counts: Counter = Counter()      # re-evaluated by the gate
    decisions = 0
    usable = 0
    divergences: List[Dict[str, Any]] = []
    divergence_count = 0
    risk_halt_divergences: List[Dict[str, Any]] = []
    risk_halt_divergence_count = 0
    op_replayed = 0                                # entries the gate was run on
    op_missing_fields = 0                          # entries skipped: no mkt ts
    replay_input_count = 0                          # records carrying replay_input
    malformed_replay_input = 0                      # present but not a dict
    replay_input_version_counts: Counter = Counter()
    clock_source_counts: Counter = Counter()       # per usable record
    no_clock_count = 0                             # usable records w/o real clock
    min_ts: Optional[int] = None                   # earliest real replay clock
    max_ts: Optional[int] = None                   # latest real replay clock

    for idx, rec in enumerate(records):
        ri, ri_malformed = _replay_input_of(rec)
        if ri_malformed:
            malformed_replay_input += 1
        if ri is not None:
            replay_input_count += 1
            replay_input_version_counts[str(ri.get("version"))] += 1

        recorded_action = rec.get("action")
        if recorded_action is not None:
            recorded_action_counts[str(recorded_action)] += 1
        halt = _recorded_halt(rec)
        if halt:
            risk_halt_counts[halt] += 1

        snap = _snapshot_of(rec, ri)
        if snap is None:
            continue
        usable += 1
        status = _status_of(rec, ri)
        now_dt, ts_ms, clock_source = _now_from(rec, snap, ri)
        clock_source_counts[clock_source] += 1
        if clock_source == "none" or ts_ms <= 0:
            no_clock_count += 1
        else:
            min_ts = ts_ms if min_ts is None else min(min_ts, ts_ms)
            max_ts = ts_ms if max_ts is None else max(max_ts, ts_ms)
        try:
            plan = pax_loop.decide(snap, status, now_dt, ts_ms)
        except Exception as exc:   # a malformed snapshot must not crash replay
            risk_halt_counts[f"replay_error:{type(exc).__name__}"] += 1
            continue
        decisions += 1
        replay_action = plan.get("action")
        action_counts[str(replay_action)] += 1
        if plan.get("setup_type"):
            setup_counts[str(plan.get("setup_type"))] += 1

        if recorded_action is not None and str(recorded_action) != str(replay_action):
            divergence_count += 1
            if len(divergences) < 50:
                divergences.append({
                    "index": idx,
                    "ts_ms": ts_ms,
                    "recorded_action": str(recorded_action),
                    "replay_action": str(replay_action),
                    "replay_state": plan.get("state"),
                })

        # ---- Layer 2: optional operational risk-gate replay (entry plans) ----
        if not plan.get("order"):
            continue   # gate is entry-only; non-entry plans are not gated
        # Operational fields: prefer the embedded replay_input (what the live
        # system actually saw), then legacy top-level record fields, then derive
        # market age from the snapshot timestamp.
        market_age = pax_replay_clock.market_age_sec_for_replay(snap, ts_ms, ri)
        if market_age is None:
            # No real market timestamp -> cannot prove freshness without faking
            # it; do NOT run the gate (fail-closed faking is exactly what we are
            # avoiding here). Counted as a limitation instead.
            op_missing_fields += 1
            continue
        kill_active = bool(_op_field(ri, rec, "kill_switch_active", False))
        hb_age = pax_replay_clock.heartbeat_age_sec_for_replay(rec, ri)
        sim_raw = _op_field(ri, rec, "sim_broker_ok", None)
        sim_ok = bool(sim_raw) if sim_raw is not None \
            else (not status.get("_status_error"))
        gate = pax_risk_gate.evaluate_entry_gate(
            now_ms=ts_ms, kill_switch_active=kill_active,
            heartbeat_age_sec=hb_age, market_age_sec=market_age,
            sim_broker_ok=sim_ok,
            session=pax_risk_gate.session_counters_from_status(status),
            config=risk_config)
        op_replayed += 1
        replayed_code = gate.code   # None when the gate allows the entry
        if replayed_code:
            replayed_halt_counts[replayed_code] += 1
        if (halt or None) != (replayed_code or None):
            risk_halt_divergence_count += 1
            if len(risk_halt_divergences) < 50:
                risk_halt_divergences.append({
                    "index": idx,
                    "ts_ms": ts_ms,
                    "recorded_risk_halt": halt,
                    "replayed_risk_halt": replayed_code,
                })

    limitations: List[str] = []
    if usable < len(records):
        limitations.append(
            f"{len(records) - usable} of {len(records)} records had no embedded "
            "snapshot (e.g. live agent-loop.jsonl heartbeats) and could not be "
            "re-decided; their recorded actions/risk-halts are still counted.")
    if usable == 0:
        limitations.append(
            "no usable snapshots: this input cannot prove decision-path replay; "
            "use a snapshot-embedding fixture to exercise pax_loop.decide.")
    if op_missing_fields:
        limitations.append(
            f"operational risk gate not replayed for {op_missing_fields} entry "
            "record(s) due to missing fields (no real market-freshness "
            "timestamp); not faked into a pass/fail.")
    if malformed_replay_input:
        limitations.append(
            f"{malformed_replay_input} record(s) had a malformed replay_input "
            "(not a dict); ignored and fell back to legacy/record fields.")
    if replay_input_count == 0 and len(records) > 0:
        limitations.append(
            "no record carried replay_input: these are pre-replay-input logs "
            "(summarized only); future logs embed replay_input for full replay.")
    limitations.append("SIM-only deterministic replay: no orders, no LLM, no "
                       "live Bookmap; no market-edge claim.")

    # Clock-specific limitations (subset focused on replay-clock safety).
    clock_limitations: List[str] = []
    if no_clock_count:
        clock_limitations.append(
            f"{no_clock_count} usable record(s) had no real replay clock "
            "(no replay_input.now_ms / ts_ms / real feed timestamp; "
            "composedAtMs is not a clock); decision ran at epoch and is NOT "
            "time-anchored.")
    snap_only = sum(clock_source_counts[s] for s in (
        "snapshot.marketDataAsOfMs", "snapshot.marketAsOfMs",
        "snapshot.eventMs", "snapshot.updatedAtMs"))
    if snap_only:
        clock_limitations.append(
            f"{snap_only} usable record(s) derived the replay clock from a "
            "snapshot feed timestamp (no replay_input.now_ms / ts_ms); fine "
            "for replay, but log replay_input for the strongest provenance.")
    clock_limitations.append(
        "weekend/offline replay clock is derived from the recorded tape only; "
        "the wall clock is never read for decisions (composedAtMs excluded).")

    first_ct = (pax_replay_clock.replay_now_dt_ct(min_ts).isoformat()
                if min_ts is not None else None)
    last_ct = (pax_replay_clock.replay_now_dt_ct(max_ts).isoformat()
               if max_ts is not None else None)

    return {
        "generated_ms": int(now_ms) if now_ms is not None else _wall_ms(),
        "input": input_path,
        "event_count": len(records),
        "usable_snapshot_count": usable,
        "skipped_malformed_count": int(malformed),
        "decisions_generated": decisions,
        "action_counts": dict(sorted(action_counts.items())),
        "recorded_action_counts": dict(sorted(recorded_action_counts.items())),
        "setup_counts": dict(sorted(setup_counts.items())),
        # recorded halts read from the log (back-compat: risk_halt_counts) +
        # the gate-replayed halts, kept strictly separate.
        "risk_halt_counts": dict(sorted(risk_halt_counts.items())),
        "recorded_risk_halt_counts": dict(sorted(risk_halt_counts.items())),
        "replayed_risk_halt_counts": dict(sorted(replayed_halt_counts.items())),
        "op_gate_replayed_count": op_replayed,
        "op_gate_missing_fields_count": op_missing_fields,
        "risk_halt_divergence_count": risk_halt_divergence_count,
        "risk_halt_divergences": risk_halt_divergences,
        "divergence_count": divergence_count,
        "divergences": divergences,
        "replay_input_count": replay_input_count,
        "malformed_replay_input": malformed_replay_input,
        "replay_input_version_counts": dict(sorted(
            replay_input_version_counts.items())),
        # --- replay clock report (weekend/offline clock safety) ---
        "replay_clock_source_counts": dict(sorted(clock_source_counts.items())),
        "first_replay_time_ct": first_ct,
        "last_replay_time_ct": last_ct,
        "weekend_wall_clock_ignored": True,
        "clock_limitations": clock_limitations,
        "deterministic_config": DETERMINISTIC_CONFIG,
        "limitations": limitations,
    }


def clock_report(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Derive a weekend/offline replay-clock verdict from a replay summary.

    Pure: reads only the summary. Answers "can this tape be replayed offline
    safely?" without re-running the replay.

    overall:
      pass  -- every usable record had a real replay clock AND every entry
               record's market-freshness gate was replayable; no wall-clock
               fallback was used.
      warn  -- records replay, but some operational gate fields are missing
               (e.g. market-freshness timestamp) or some records lacked a clock.
      fail  -- no usable replay clock exists (no usable records, or none of
               the usable records carried a real timestamp)."""
    usable = int(summary.get("usable_snapshot_count") or 0)
    src_counts = dict(summary.get("replay_clock_source_counts") or {})
    no_clock = int(src_counts.get("none", 0))
    real_clock = max(0, usable - no_clock)
    mkt_missing = int(summary.get("op_gate_missing_fields_count") or 0)

    replay_clock_ok = usable > 0 and no_clock == 0
    if usable == 0 or real_clock == 0:
        overall = "fail"
    elif no_clock == 0 and mkt_missing == 0:
        overall = "pass"
    else:
        overall = "warn"

    required_actions: List[str] = []
    if usable == 0:
        required_actions.append(
            "no usable snapshot to replay: capture a snapshot-embedding "
            "log/fixture (replay_input.snapshot or top-level snapshot).")
    if no_clock > 0:
        required_actions.append(
            f"{no_clock} usable record(s) had no real replay clock: log "
            "replay_input.now_ms (or ts_ms / a real feed timestamp) per record.")
    if mkt_missing > 0:
        required_actions.append(
            f"{mkt_missing} entry record(s) lacked market freshness: log "
            "market_age_sec (or marketDataAsOfMs) so the freshness gate replays.")

    return {
        "overall": overall,
        "replay_clock_ok": replay_clock_ok,
        "usable_records": usable,
        "clock_source_counts": dict(sorted(src_counts.items())),
        "first_replay_time_ct": summary.get("first_replay_time_ct"),
        "last_replay_time_ct": summary.get("last_replay_time_ct"),
        "market_freshness_missing": mkt_missing,
        "wall_clock_used": False,
        "limitations": list(summary.get("clock_limitations") or []),
        "required_actions": required_actions,
    }


def _wall_ms() -> int:
    import time
    return int(time.time() * 1000)


def replay_file(input_path: Path, *, limit: Optional[int] = None,
                now_ms: Optional[int] = None,
                risk_config: Optional["pax_risk_gate.RiskGateConfig"] = None
                ) -> Dict[str, Any]:
    """Read a JSONL file and replay it. Missing file -> a structured summary
    with an explicit limitation (never raises for a missing input)."""
    p = Path(input_path)
    try:
        text = p.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        return {
            "generated_ms": int(now_ms) if now_ms is not None else _wall_ms(),
            "input": str(p),
            "event_count": 0,
            "usable_snapshot_count": 0,
            "skipped_malformed_count": 0,
            "decisions_generated": 0,
            "action_counts": {},
            "recorded_action_counts": {},
            "setup_counts": {},
            "risk_halt_counts": {},
            "recorded_risk_halt_counts": {},
            "replayed_risk_halt_counts": {},
            "op_gate_replayed_count": 0,
            "op_gate_missing_fields_count": 0,
            "risk_halt_divergence_count": 0,
            "risk_halt_divergences": [],
            "divergence_count": 0,
            "divergences": [],
            "replay_input_count": 0,
            "malformed_replay_input": 0,
            "replay_input_version_counts": {},
            "replay_clock_source_counts": {},
            "first_replay_time_ct": None,
            "last_replay_time_ct": None,
            "weekend_wall_clock_ignored": True,
            "clock_limitations": [],
            "deterministic_config": DETERMINISTIC_CONFIG,
            "limitations": [f"input unreadable: {type(exc).__name__}: {exc}"],
        }
    records, malformed = iter_records(text)
    return replay_records(records, malformed=malformed, input_path=str(p),
                          limit=limit, now_ms=now_ms, risk_config=risk_config)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bookmap_mcp.pax_agent_replay",
        description="Deterministically replay the PAX decision path over saved "
                    "JSONL logs (no orders, no LLM, no live Bookmap).")
    p.add_argument("--input", required=True, type=Path,
                   help="JSONL log (records with embedded snapshot/snap).")
    p.add_argument("--output", type=Path, default=None,
                   help="Write the summary JSON here (else stdout).")
    p.add_argument("--limit", type=int, default=None,
                   help="Replay at most N records.")
    p.add_argument("--clock-report", action="store_true",
                   help="Emit the weekend/offline replay-clock verdict "
                        "(pass|warn|fail) instead of the full replay summary.")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    report = replay_file(args.input, limit=args.limit)
    if args.clock_report:
        report = clock_report(report)
    body = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body, encoding="utf-8")
        print(f"agent replay report written: {args.output}", file=sys.stderr)
    else:
        print(body)
    return 0


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
