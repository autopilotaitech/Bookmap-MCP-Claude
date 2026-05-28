"""Manual paper-trade CLI for an externally-driven SimEngine.

Usage from operator wake loop or shell:

    python -m bookmap_mcp.pax_manual long  2 30305.50 30295.00 30325.00 30341.00 \\
        --reason "+3 ext absorption + buy tape 2.4:1 size"
    python -m bookmap_mcp.pax_manual short 2 30331.75 30342.00 30260.00          \\
        --reason "fresh DIST + tape 2:1 sell"
    python -m bookmap_mcp.pax_manual flatten --reason "thesis softened"
    python -m bookmap_mcp.pax_manual status
    python -m bookmap_mcp.pax_manual cancel  12

The CLI opens a SimEngine briefly against the shared sim-db (same DB that
pax_daemon --no-auto-decide is ticking) and either places, cancels, or
queries. The daemon's tick loop handles fills and journals everything.

Constraints:
- DOES NOT touch live Bookmap. Refuses with non-zero exit if
  ``BOOKMAP_ALLOW_TRADING=1`` is set in the environment (mirrors daemon
  safety check; nothing in this module ever calls live endpoints).
- Uses the same sim-db path conventions as pax-start.bat by default.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

from .sim_engine import SimEngine, S_BUY, S_SELL

DEFAULT_SIM_DB = Path(r"D:\BookmapLogs\pax-daemon-trades.db")
DEFAULT_ALIAS = "NQM6.CME@RITHMIC"


def _ensure_safe_environment() -> None:
    if os.environ.get("BOOKMAP_ALLOW_TRADING") == "1":
        sys.stderr.write(
            "pax_manual refuses to run with BOOKMAP_ALLOW_TRADING=1. This CLI "
            "is paper-only. Unset the variable before retrying.\n"
        )
        sys.exit(2)


def _open(args: argparse.Namespace) -> SimEngine:
    return SimEngine(
        alias=args.alias,
        db_path=args.sim_db,
        # eod_close_hour_ct=None so manual trades don't get auto-flattened
        # at 15:00 CT during ETH practice. Operator can pass --eod-hour
        # to re-enable.
        eod_close_hour_ct=args.eod_hour,
    )


def _cmd_long(args: argparse.Namespace) -> int:
    return _cmd_bracket(args, side=S_BUY)


def _cmd_short(args: argparse.Namespace) -> int:
    return _cmd_bracket(args, side=S_SELL)


def _cmd_bracket(args: argparse.Namespace, *, side: str) -> int:
    _ensure_safe_environment()
    entry = float(args.entry)
    stop = float(args.stop)
    tps: List[float] = [float(p) for p in args.take_profits]
    if not tps:
        sys.stderr.write("at least one take-profit price required\n")
        return 2
    eng = _open(args)
    result = eng.place_bracket(
        side=side,
        qty=int(args.qty),
        entry_stop=None,
        entry_limit=entry,
        stop_loss=stop,
        take_profits=tps,
        decision_tag=args.tag or f"manual-{side.lower()}",
        reason=args.reason or "",
    )
    print(json.dumps({
        "action": "bracket_placed",
        "side": side,
        "qty": int(args.qty),
        "entry_limit": entry,
        "stop_loss": stop,
        "take_profits": tps,
        "ids": result,
        "alias": args.alias,
        "reason": args.reason,
    }, default=str, indent=2))
    return 0


def _cmd_cancel(args: argparse.Namespace) -> int:
    _ensure_safe_environment()
    eng = _open(args)
    ok = eng.cancel(int(args.order_id), reason=args.reason or "manual-cancel")
    print(json.dumps({"action": "cancel", "order_id": int(args.order_id), "ok": ok}))
    return 0 if ok else 1


def _cmd_flatten(args: argparse.Namespace) -> int:
    _ensure_safe_environment()
    eng = _open(args)
    result = eng.flatten(reason=args.reason or "manual-flatten")
    print(json.dumps({"action": "flatten", "result": result}, default=str, indent=2))
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    eng = _open(args)
    snap = eng.snapshot()
    print(json.dumps(snap, default=str, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pax_manual",
        description="Manual paper-trade CLI against a shared SimEngine.")
    p.add_argument("--alias", default=DEFAULT_ALIAS,
                   help="Instrument alias (default: %(default)s).")
    p.add_argument("--sim-db", type=Path, default=DEFAULT_SIM_DB,
                   help="Path to shared sim DB (default: %(default)s).")
    p.add_argument("--eod-hour", type=int, default=None,
                   help="EOD auto-flatten hour CT. Default: None (disabled "
                        "for ETH practice; pass 15 to enable RTH-style EOD).")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_bracket(name: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=f"Place a {name} bracket order.")
        sp.add_argument("qty", type=int)
        sp.add_argument("entry", type=float)
        sp.add_argument("stop", type=float)
        sp.add_argument("take_profits", nargs="+", type=float,
                        help="One or more TP prices, ordered by priority.")
        sp.add_argument("--reason", default="")
        sp.add_argument("--tag", default=None,
                        help="decision_tag stored on each order.")
        return sp

    sp_long = add_bracket("long")
    sp_long.set_defaults(func=_cmd_long)
    sp_short = add_bracket("short")
    sp_short.set_defaults(func=_cmd_short)

    sp_cancel = sub.add_parser("cancel", help="Cancel one working order.")
    sp_cancel.add_argument("order_id", type=int)
    sp_cancel.add_argument("--reason", default="")
    sp_cancel.set_defaults(func=_cmd_cancel)

    sp_flat = sub.add_parser("flatten", help="Market-close any open position.")
    sp_flat.add_argument("--reason", default="")
    sp_flat.set_defaults(func=_cmd_flatten)

    sp_status = sub.add_parser("status", help="Print current SimEngine snapshot.")
    sp_status.set_defaults(func=_cmd_status)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
