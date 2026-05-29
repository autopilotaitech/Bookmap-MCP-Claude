"""One-shot scheduled tick for the autonomous Pax sim agent.

This is the cron/Task Scheduler entrypoint. It runs exactly one agent cycle:
snapshot -> sim status -> LLM JSON decision -> deterministic governor ->
optional sim execution -> JSONL audit log.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import time
from typing import Any, Dict, Optional

from . import pax_sim_agent, pax_sim_tools

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python without tzdata
    ZoneInfo = None  # type: ignore


def _now_ct() -> _dt.datetime:
    if ZoneInfo is None:
        return _dt.datetime.now()
    return _dt.datetime.now(ZoneInfo("America/Chicago"))


def _load_status(alias: Optional[str]) -> Dict[str, Any]:
    try:
        return pax_sim_tools.sim_status(alias) if alias else pax_sim_tools.sim_status()
    except Exception as exc:
        return {"position": {"size": 0}, "working": [], "_status_error": str(exc)}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m bookmap_mcp.pax_agent_tick",
        description="Run one autonomous Pax sim-agent tick.",
    )
    p.add_argument("--dashboard-url", default=pax_sim_agent.DASHBOARD_URL,
                   help="Dashboard snapshot URL.")
    p.add_argument("--alias", default=None,
                   help="Optional sim alias. Defaults to SimEngine default.")
    p.add_argument("--model", default=pax_sim_agent.DEFAULT_MODEL,
                   help="Claude model for the tool-less JSON decision.")
    p.add_argument("--armed", action="store_true",
                   help="Allow governor-approved sim execution.")
    p.add_argument("--observe", action="store_true",
                   help="Force observe mode. LLM decides and logs; no execution.")
    p.add_argument("--timeout-sec", type=float, default=45.0,
                   help="Claude CLI timeout for this one tick.")
    return p


def run_once(args: argparse.Namespace) -> Dict[str, Any]:
    now = _now_ct()
    now_ms = int(time.time() * 1000)
    snap = pax_sim_agent._fetch_snapshot(args.dashboard_url)
    alias = args.alias or snap.get("alias")
    status = _load_status(alias)
    dry = bool(args.observe or not args.armed)

    rec = pax_sim_agent.decide_cycle(
        snap,
        status,
        now,
        now_ms,
        call_fn=lambda prompt: pax_sim_agent.call_claude_json(
            prompt, model=args.model, timeout_sec=args.timeout_sec),
        alias=alias,
        dry=dry,
    )
    rec.update({
        "ts_ms": now_ms,
        "ts_ct": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "armed": bool(args.armed and not args.observe),
        "alias": alias,
        "dashboard_url": args.dashboard_url,
        "model": args.model,
    })

    try:
        pax_sim_tools._ensure_dir()
        with pax_sim_agent.AGENT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str, separators=(",", ":")) + "\n")
    except Exception as exc:
        rec["log_error"] = str(exc)

    try:
        pax_sim_calibration = pax_sim_agent.pax_sim_calibration
        pax_sim_calibration.update(status)
    except Exception as exc:
        rec["calibration_error"] = str(exc)

    return rec


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        rec = run_once(args)
    except Exception as exc:
        err = {"ok": False, "error": f"agent_tick: {exc}"}
        print(json.dumps(err, default=str))
        return 1

    print(json.dumps(rec, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
