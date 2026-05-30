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


def _snapshot_of(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for key in ("snapshot", "snap"):
        v = record.get(key)
        if isinstance(v, dict) and v:
            return v
    return None


def _status_of(record: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("status", "sim_status"):
        v = record.get(key)
        if isinstance(v, dict):
            return v
    return dict(_FLAT_STATUS)


def _recorded_halt(record: Dict[str, Any]) -> Optional[str]:
    code = record.get("risk_halt_code") or record.get("risk_halt")
    return str(code) if code else None


def _num(x: Any) -> Optional[float]:
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _market_age_sec(snap: Dict[str, Any], now_ms: int) -> Optional[float]:
    """Market/feed data age (seconds) from a REAL bridge timestamp, or None when
    none exists. Mirrors pax_sim_agent._market_age_sec; never uses dashboard
    compose time. None -> the operational gate cannot be replayed (missing
    fields), which is reported as a limitation rather than faked."""
    as_of = _num(snap.get("marketDataAsOfMs"))
    if as_of is None:
        as_of = _num(snap.get("marketAsOfMs"))
    if as_of is not None:
        return max(0.0, (now_ms - as_of) / 1000.0)
    age_ms = _num(snap.get("ageMs"))
    if age_ms is None:
        age_ms = _num(snap.get("snapshot_age_ms"))
    if age_ms is not None:
        return max(0.0, age_ms / 1000.0)
    return None


def _now_from(record: Dict[str, Any], snap: Dict[str, Any]) -> Tuple[Any, int]:
    """Deterministic replay clock from ts_ms (UTC); never reads wall-clock."""
    ts = record.get("ts_ms")
    if ts is None:
        ts = snap.get("marketDataAsOfMs") or snap.get("composedAtMs") or 0
    try:
        ts_ms = int(ts)
    except (TypeError, ValueError):
        ts_ms = 0
    if ts_ms > 0:
        now_dt = datetime.datetime.fromtimestamp(ts_ms / 1000.0,
                                                 datetime.timezone.utc)
    else:
        now_dt = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
    return now_dt, ts_ms


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

    for idx, rec in enumerate(records):
        recorded_action = rec.get("action")
        if recorded_action is not None:
            recorded_action_counts[str(recorded_action)] += 1
        halt = _recorded_halt(rec)
        if halt:
            risk_halt_counts[halt] += 1

        snap = _snapshot_of(rec)
        if snap is None:
            continue
        usable += 1
        status = _status_of(rec)
        now_dt, ts_ms = _now_from(rec, snap)
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
        market_age = _market_age_sec(snap, ts_ms)
        if market_age is None:
            # No real market timestamp -> cannot prove freshness without faking
            # it; do NOT run the gate (fail-closed faking is exactly what we are
            # avoiding here). Counted as a limitation instead.
            op_missing_fields += 1
            continue
        kill_active = bool(rec.get("kill_switch_active")) \
            if "kill_switch_active" in rec else False
        hb_age = _num(rec.get("heartbeat_age_sec")) \
            if "heartbeat_age_sec" in rec else None
        sim_ok = bool(rec.get("sim_broker_ok")) if "sim_broker_ok" in rec \
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
    limitations.append("SIM-only deterministic replay: no orders, no LLM, no "
                       "live Bookmap; no market-edge claim.")

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
        "deterministic_config": DETERMINISTIC_CONFIG,
        "limitations": limitations,
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
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    report = replay_file(args.input, limit=args.limit)
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
