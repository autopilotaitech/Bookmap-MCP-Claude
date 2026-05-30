"""Sim-only tool surface for the agentic Pax trader.

This is the ONLY interface through which the agent's decisions reach a broker.
It wraps the LOCAL SQLite ``SimEngine`` (paper only) and the agent's own
learning store. It deliberately has NO path to live order routing:

- It imports ``SimEngine`` (local SQLite paper broker) — never the pax-ai
  server and never the live order-placement tools that live in server.py.
- It refuses to operate if ``BOOKMAP_ALLOW_TRADING=1`` (mirrors pax_manual).
- The agent process runs with that var unset, so even a stray live call would
  be refused by the bridge gate — but the real wall is that this module simply
  has no live-order code path.

Pinned by ``tests/test_pax_sim_tools_safety.py`` (AST guard: no live-order
symbols; runtime guard: BOOKMAP_ALLOW_TRADING).
"""
from __future__ import annotations

import csv
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .sim_engine import SimEngine, S_BUY, S_SELL
from .pax_manual import DEFAULT_SIM_DB, DEFAULT_ALIAS

# --- learning store (agent-owned, sim only) ---------------------------------
LEARN_DIR = Path(r"D:\BookmapLogs\pax-agent")
LESSONS_PATH = LEARN_DIR / "sim_lessons.md"
PLAYBOOK_PATH = LEARN_DIR / "sim_playbook.json"
SELFMOD_LEDGER = LEARN_DIR / "agent-selfmod-ledger.csv"
CALIBRATION_PATH = LEARN_DIR / "calibration.json"


class SimSafetyError(RuntimeError):
    pass


class SimKillSwitchError(SimSafetyError):
    """Raised when the operator kill switch is engaged and a SIM order
    placement is attempted anyway (defense-in-depth backstop)."""
    pass


# Operator kill switch: presence of this file under the agent learn dir is a
# hard risk-halt. It blocks NEW SIM order placement before any broker call.
KILL_SWITCH_NAME = "KILL_SWITCH"


def kill_switch_path(learn_dir: Optional[Path] = None) -> Path:
    base = Path(learn_dir) if learn_dir else LEARN_DIR
    return base / KILL_SWITCH_NAME


def kill_switch_active(learn_dir: Optional[Path] = None) -> bool:
    """True when the operator kill switch file exists. Deterministic; the
    only input is the presence of ``<learn_dir>/KILL_SWITCH``."""
    try:
        return kill_switch_path(learn_dir).exists()
    except OSError:
        return False


def risk_halt_reason(learn_dir: Optional[Path] = None) -> Optional[str]:
    """Single reason code for a risk halt, or None. Today the only halt is
    the kill switch; the signature leaves room for future halts."""
    return "kill_switch_active" if kill_switch_active(learn_dir) else None


def ensure_sim_safe() -> None:
    """Refuse to act if live trading is enabled. Paper-only, always."""
    if os.environ.get("BOOKMAP_ALLOW_TRADING") == "1":
        raise SimSafetyError(
            "pax_sim_tools refuses to run with BOOKMAP_ALLOW_TRADING=1; "
            "this surface is paper-only.")


def ensure_not_halted() -> None:
    """Backstop: refuse to PLACE a SIM order while the kill switch is on.

    The agent governor blocks first and records a clean veto; this wall
    guarantees that even a stray/future caller cannot place a SIM order while
    halted. It does NOT block flatten/cancel -- those reduce risk."""
    reason = risk_halt_reason()
    if reason:
        raise SimKillSwitchError(
            f"{reason}: SIM order placement halted; remove "
            f"{kill_switch_path()} to resume.")


def _engine(alias: str = DEFAULT_ALIAS,
            db_path: Path = DEFAULT_SIM_DB) -> SimEngine:
    ensure_sim_safe()
    # eod_close_hour_ct=None: the agent governor owns session-stop, not EOD.
    return SimEngine(alias=alias, db_path=db_path, eod_close_hour_ct=None)


# --- broker (local SimEngine only) ------------------------------------------

def sim_status(alias: str = DEFAULT_ALIAS) -> Dict[str, Any]:
    return _engine(alias).snapshot()


def sim_place_bracket(side: str, qty: int, entry_limit: float,
                      stop_loss: float, take_profits: List[float],
                      entry_stop: Optional[float] = None,
                      tag: Optional[str] = None,
                      reason: str = "", alias: str = DEFAULT_ALIAS) -> Dict[str, Any]:
    """Place a sim bracket. ``side`` is 'buy'/'sell' (or S_BUY/S_SELL).
    ``entry_stop`` set => resting STOP-LIMIT entry; else plain limit."""
    ensure_sim_safe()
    ensure_not_halted()
    s = S_BUY if str(side).lower() in ("buy", "long", S_BUY) else S_SELL
    eng = _engine(alias)
    ids = eng.place_bracket(
        side=s, qty=int(qty), entry_stop=entry_stop, entry_limit=float(entry_limit),
        stop_loss=float(stop_loss), take_profits=[float(t) for t in take_profits],
        decision_tag=tag or "agent", reason=reason or "")
    return {"ok": True, "side": s, "qty": int(qty),
            "entry_type": "stop_limit" if entry_stop is not None else "limit",
            "entry_stop": entry_stop, "entry_limit": float(entry_limit),
            "stop_loss": float(stop_loss), "take_profits": list(take_profits),
            "ids": ids}


def sim_flatten(reason: str = "agent-flatten",
                alias: str = DEFAULT_ALIAS) -> Dict[str, Any]:
    ensure_sim_safe()
    # SimEngine.flatten returns {canceled, flattened_size} with no "ok" key;
    # callers test result["ok"], so surface it (success unless it raised).
    return {"ok": True, **_engine(alias).flatten(reason=reason)}


def sim_cancel(order_id: int, reason: str = "agent-cancel",
               alias: str = DEFAULT_ALIAS) -> Dict[str, Any]:
    ensure_sim_safe()
    ok = _engine(alias).cancel(int(order_id), reason=reason)
    return {"ok": bool(ok), "order_id": int(order_id)}


# --- learning store (read/write the agent's own memory) ---------------------

def _ensure_dir() -> None:
    LEARN_DIR.mkdir(parents=True, exist_ok=True)


def tail_lines(path: Path, max_lines: int = 200, block: int = 65536) -> List[str]:
    """Read only the last `max_lines` non-empty lines WITHOUT loading the whole
    file (seek from the end in blocks). Keeps per-cycle cost O(max_lines) instead
    of O(filesize) -- the agent-loop.jsonl grows every heartbeat, and reading the
    whole thing each 15s cycle was the lag."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            pos = f.tell()
            data = b""
            while pos > 0 and data.count(b"\n") <= max_lines:
                step = min(block, pos)
                pos -= step
                f.seek(pos)
                data = f.read(step) + data
    except (FileNotFoundError, OSError):
        return []
    # splitlines() handles \r\n (Windows text-mode writes) cleanly.
    out = [ln for ln in data.decode("utf-8", "replace").splitlines() if ln.strip()]
    return out[-max_lines:]


def append_line_capped(path: Path, line: str, max_bytes: int = 4_000_000,
                       keep_lines: int = 3000) -> None:
    """Append one line; if the file exceeds `max_bytes`, rotate it down to the
    last `keep_lines`. Prevents the per-heartbeat log from growing unbounded."""
    _ensure_dir()
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line if line.endswith("\n") else line + "\n")
    try:
        if path.stat().st_size > max_bytes:
            kept = tail_lines(path, keep_lines)
            path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    except OSError:
        pass


def read_lessons() -> str:
    try:
        return LESSONS_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def write_lessons(text: str, keep_lines: int = 150) -> None:
    # Cap the lessons file so it can't grow unbounded (it is read each narration
    # and rewritten whole on each append). Keep the most recent keep_lines.
    _ensure_dir()
    lines = [l for l in (text or "").splitlines() if l.strip()][-keep_lines:]
    LESSONS_PATH.write_text(("\n".join(lines) + "\n") if lines else "",
                            encoding="utf-8")


def read_playbook() -> Dict[str, Any]:
    try:
        return json.loads(PLAYBOOK_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_playbook(obj: Dict[str, Any]) -> None:
    _ensure_dir()
    PLAYBOOK_PATH.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def read_calibration() -> Dict[str, Any]:
    try:
        return json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def append_selfmod(kind: str, before: Any, after: Any, rationale: str,
                   now_ms: Optional[int] = None) -> None:
    """Append one revertible row to the self-modification ledger."""
    _ensure_dir()
    new = not SELFMOD_LEDGER.exists()
    ts = int(now_ms if now_ms is not None else time.time() * 1000)
    with SELFMOD_LEDGER.open("a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["ts_ms", "kind", "before", "after", "rationale"])
        w.writerow([ts, kind, json.dumps(before, default=str),
                    json.dumps(after, default=str), rationale])
