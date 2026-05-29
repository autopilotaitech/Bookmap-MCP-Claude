"""Always-awake autonomous Pax SIM trader entrypoint.

This is the preferred runtime wrapper around pax_sim_agent.AgentLoop. It owns a
single long-running process whose fast deterministic heartbeat can manage SIM
risk without waiting on the LLM. Claude/local-model narration remains slower
and advisory inside AgentLoop.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Optional

from . import pax_sim_agent


def _scrub_live_trading_env_for_sim() -> bool:
    """Clear inherited live-trading env in this paper-only process."""
    if os.environ.get("BOOKMAP_ALLOW_TRADING") == "1":
        os.environ["BOOKMAP_ALLOW_TRADING"] = ""
        return True
    return False


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m bookmap_mcp.pax_autopilot",
        description="Run the always-awake autonomous Pax SIM trader.",
    )
    p.add_argument("--dashboard-url", default=pax_sim_agent.DASHBOARD_URL,
                   help="Dashboard snapshot URL.")
    p.add_argument("--alias", default=None,
                   help="Optional sim alias. Defaults to SimEngine default.")
    p.add_argument("--model", default=pax_sim_agent.DEFAULT_MODEL,
                   help="Model for advisory narration.")
    p.add_argument("--llm-provider", default=pax_sim_agent.DEFAULT_PROVIDER,
                   choices=("claude_cli", "ollama", "local_llm"),
                   help="Advisory narration provider.")
    p.add_argument("--llm-endpoint", default=None,
                   help="Optional local LLM endpoint, e.g. http://127.0.0.1:11434.")
    p.add_argument("--llm-keep-alive", default="30m",
                   help="Ollama keep_alive value to keep the model warm.")
    p.add_argument("--interval-sec", type=float, default=15.0,
                   help="Fast deterministic heartbeat interval.")
    p.add_argument("--llm-every", type=int, default=6,
                   help="Run advisory LLM narration every N heartbeats.")
    p.add_argument("--armed", action="store_true",
                   help="Allow governor-approved SIM execution.")
    p.add_argument("--observe", action="store_true",
                   help="Force observe mode; no SIM execution.")
    p.add_argument("--status-every-sec", type=float, default=60.0,
                   help="Print compact JSON status this often; 0 disables.")
    return p


def run_forever(args: argparse.Namespace) -> int:
    scrubbed = _scrub_live_trading_env_for_sim()
    loop = pax_sim_agent.AgentLoop(
        dashboard_url=args.dashboard_url,
        alias=args.alias,
        interval_sec=args.interval_sec,
        model=args.model,
        llm_every=args.llm_every,
        llm_provider=getattr(args, "llm_provider", pax_sim_agent.DEFAULT_PROVIDER),
        llm_endpoint=getattr(args, "llm_endpoint", None),
        llm_keep_alive=getattr(args, "llm_keep_alive", "30m"),
    )
    armed = bool(args.armed and not args.observe)
    status = loop.start(armed=armed)
    status.update({
        "ok": True,
        "mode": "armed" if armed else "observe",
        "live_trading_env_scrubbed": scrubbed,
    })
    print(json.dumps(status, default=str), flush=True)

    next_status = time.monotonic() + max(0.0, float(args.status_every_sec or 0.0))
    try:
        while True:
            time.sleep(0.5)
            every = float(args.status_every_sec or 0.0)
            if every > 0 and time.monotonic() >= next_status:
                print(json.dumps({"ok": True, **loop.status()}, default=str),
                      flush=True)
                next_status = time.monotonic() + every
    except KeyboardInterrupt:
        print(json.dumps({"ok": True, "stopping": True, **loop.stop()},
                         default=str), flush=True)
        return 130


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run_forever(args)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"pax_autopilot: {exc}"},
                         default=str), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
