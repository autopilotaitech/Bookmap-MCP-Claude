"""Phase 4: read-only overview dashboard.

Separate process from `pax_daemon`. Reads from `pax-journal.db` (the journal
the daemon writes) and renders an overview page with 10 collapsible
sections per the spec. No write endpoints. No trade-entry UI. Two-gate
safety guard from Phase 0 makes this process pure presentation.

Each section uses native HTML `<details open><summary>` so the user gets
a real drop-down arrow that toggles collapse. State persists per-section
via `localStorage` so reloads remember which sections were collapsed.

Usage:
    python -m bookmap_mcp.overview_ui --journal D:\\BookmapLogs\\pax-journal.db
    python -m bookmap_mcp.overview_ui --journal ... --port 18890
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sqlite3
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import (pax_freshness, pax_roles, pax_eval_state, pax_risk_gate,
               pax_promotion_report)


def _mtime_ms(path: Path) -> Optional[int]:
    try:
        return int(path.stat().st_mtime * 1000)
    except OSError:
        return None


def _git_commit() -> Optional[str]:
    """Short HEAD commit of this repo, best-effort (no window on win32)."""
    try:
        repo = Path(__file__).resolve().parents[2]
        kwargs: Dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=2.0, **kwargs)
        if r.returncode == 0:
            return (r.stdout or "").strip() or None
    except Exception:
        return None
    return None


# ─── data access ────────────────────────────────────────────────────────

class OverviewQueries:
    """Read-only queries against the journal DB. SQLite is opened in
    read-only mode (`?mode=ro`) so the UI can never accidentally mutate
    the daemon's audit trail."""

    def __init__(self, journal_path: Path,
                  sim_db_path: Optional[Path] = None,
                  learn_dir: Optional[Path] = None) -> None:
        self.journal_path = Path(journal_path)
        self.sim_db_path = Path(sim_db_path) if sim_db_path else None
        self.learn_dir = Path(learn_dir) if learn_dir \
            else Path(r"D:\BookmapLogs\pax-agent")

    def _journal(self) -> sqlite3.Connection:
        c = sqlite3.connect(
            f"file:{self.journal_path.as_posix()}?mode=ro",
            uri=True, timeout=2.0)
        c.row_factory = sqlite3.Row
        return c

    def _sim(self) -> Optional[sqlite3.Connection]:
        if self.sim_db_path is None or not self.sim_db_path.exists():
            return None
        c = sqlite3.connect(
            f"file:{self.sim_db_path.as_posix()}?mode=ro",
            uri=True, timeout=2.0)
        c.row_factory = sqlite3.Row
        # Validate it is a real, readable SIM DB before handing it to callers. A
        # corrupt/locked/non-SIM file connects lazily but raises on first query;
        # catching it here means every consumer degrades to empty instead of
        # crashing (the structured reporter is sim_broker_preflight()).
        try:
            c.execute("SELECT 1 FROM orders LIMIT 1").fetchone()
        except sqlite3.Error:
            c.close()
            return None
        return c

    def sim_broker_preflight(self) -> Dict[str, Any]:
        """Read-only health probe of the configured SIM broker DB.

        Opens it read-only and runs a minimal schema query (``SELECT COUNT(*)
        FROM orders``) so a corrupt, locked, unreadable, or non-SIM DB is
        reported unavailable instead of merely 'file exists'. No writes, never
        raises. Returns {openable, readable, error, source_path}.
        ``reachable`` for health = openable AND readable."""
        sp = str(self.sim_db_path) if self.sim_db_path else None
        if self.sim_db_path is None:
            return {"openable": False, "readable": False,
                    "error": "no sim_db_path configured", "source_path": None}
        if not self.sim_db_path.exists():
            return {"openable": False, "readable": False,
                    "error": "sim db file does not exist", "source_path": sp}
        c = None
        try:
            c = sqlite3.connect(
                f"file:{self.sim_db_path.as_posix()}?mode=ro",
                uri=True, timeout=2.0)
            # Minimal safe read: proves a real, readable sqlite DB carrying the
            # SIM schema (the orders table the broker reads/writes). A corrupt
            # file raises DatabaseError; a non-SIM DB raises 'no such table'.
            c.execute("SELECT COUNT(*) FROM orders").fetchone()
            return {"openable": True, "readable": True, "error": None,
                    "source_path": sp}
        except sqlite3.Error as e:
            return {"openable": c is not None, "readable": False,
                    "error": f"{type(e).__name__}: {e}"[:200], "source_path": sp}
        finally:
            if c is not None:
                c.close()

    # ─── status / heartbeat ─────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        try:
            with self._journal() as c:
                run = c.execute(
                    "SELECT run_id, started_ms, ended_ms, adapter_name, "
                    "signal_version, weights_hash, notes "
                    "FROM runs ORDER BY started_ms DESC LIMIT 1").fetchone()
                health = c.execute(
                    "SELECT status, detail, ts_ms, snapshots_emitted "
                    "FROM adapter_health ORDER BY id DESC LIMIT 1").fetchone()
                last_snap = c.execute(
                    "SELECT MAX(ts_ms) AS ts FROM snapshots").fetchone()
                snap_count = c.execute(
                    "SELECT COUNT(*) AS n FROM snapshots").fetchone()
                sig_count = c.execute(
                    "SELECT COUNT(*) AS n FROM signals").fetchone()
        except sqlite3.OperationalError as exc:
            return {"error": str(exc)}
        now_ms = int(time.time() * 1000)
        last_snap_ms = last_snap["ts"] if last_snap and last_snap["ts"] else 0
        age_sec = (now_ms - last_snap_ms) / 1000.0 if last_snap_ms else None
        return {
            "run":            dict(run) if run else None,
            "health":         dict(health) if health else None,
            "snapshot_count": snap_count["n"] if snap_count else 0,
            "signal_count":   sig_count["n"] if sig_count else 0,
            "last_snapshot_age_sec": age_sec,
            "uptime_sec": (now_ms - run["started_ms"]) / 1000.0
                            if run and run["started_ms"] else None,
        }

    # ─── position / orders (from sim DB) ────────────────────────────

    def current_position(self) -> Optional[Dict[str, Any]]:
        c = self._sim()
        if c is None: return None
        try:
            row = c.execute("SELECT * FROM positions ORDER BY updated_ms DESC "
                              "LIMIT 1").fetchone()
            return dict(row) if row else None
        finally:
            c.close()

    def working_orders(self) -> List[Dict[str, Any]]:
        c = self._sim()
        if c is None: return []
        try:
            rows = c.execute(
                "SELECT id, side, type, qty, limit_price, stop_price, "
                "status, role, reason, parent_id, armed_after_parent_fill, "
                "placed_ms "
                "FROM orders WHERE status IN ('WORKING','TRIGGERED') "
                "ORDER BY placed_ms DESC").fetchall()
            return [dict(r) for r in rows]
        finally:
            c.close()

    # ─── daily / cumulative pnl ─────────────────────────────────────

    def daily_stats(self, days: int = 30) -> List[Dict[str, Any]]:
        try:
            with self._journal() as c:
                rows = c.execute(
                    "SELECT session_date, alias, trades_count, wins, losses, "
                    "gross_pnl, max_drawdown "
                    "FROM daily_stats ORDER BY session_date DESC LIMIT ?",
                    (days,)).fetchall()
                return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []

    def pnl_summary(self) -> Dict[str, Any]:
        try:
            with self._journal() as c:
                row = c.execute(
                    "SELECT COALESCE(SUM(gross_pnl), 0) AS total, "
                    "COALESCE(SUM(wins), 0) AS wins, "
                    "COALESCE(SUM(losses), 0) AS losses, "
                    "COALESCE(MAX(max_drawdown), 0) AS max_dd "
                    "FROM daily_stats").fetchone()
        except sqlite3.OperationalError:
            return {"total": 0, "wins": 0, "losses": 0, "max_dd": 0}
        return dict(row) if row else {"total": 0, "wins": 0, "losses": 0, "max_dd": 0}

    # ─── latest signals + setup win rates ──────────────────────────

    def latest_signals(self, limit: int = 20) -> List[Dict[str, Any]]:
        try:
            with self._journal() as c:
                rows = c.execute(
                    "SELECT ts_ms, alias, decision, size_tier, confidence, "
                    "level_label, level_price, composite_score, "
                    "composite_dir, conviction_score, conviction_trajectory "
                    "FROM signals ORDER BY ts_ms DESC LIMIT ?",
                    (limit,)).fetchall()
                return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []

    def setup_winrates(self) -> List[Dict[str, Any]]:
        """Aggregate by decision × level_label. We have no outcome data yet
        (Phase 5 fills the `outcomes` table); for now we report counts."""
        try:
            with self._journal() as c:
                rows = c.execute(
                    "SELECT decision, level_label, COUNT(*) AS n, "
                    "AVG(confidence) AS avg_conf "
                    "FROM signals WHERE decision LIKE 'ENTER_%' "
                    "GROUP BY decision, level_label "
                    "ORDER BY n DESC LIMIT 50").fetchall()
                return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []

    # ─── events / errors ────────────────────────────────────────────

    def errors(self, limit: int = 30) -> List[Dict[str, Any]]:
        try:
            with self._journal() as c:
                rows = c.execute(
                    "SELECT ts_ms, kind, source, message "
                    "FROM events WHERE kind IN ('ERROR','WARN','LOOP_CRASH',"
                    "'SCHEMA_INVALID','RUN_CRASHED') "
                    "ORDER BY ts_ms DESC LIMIT ?",
                    (limit,)).fetchall()
                return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []

    # ─── agentic sim trader (learning store + sim P&L) ──────────────

    def agent_feed(self, limit: int = 80) -> List[Dict[str, Any]]:
        # Tail only the last `limit` lines (the log grows every heartbeat;
        # slurping the whole file on each :18890 poll was a needless read).
        try:
            from .pax_sim_tools import tail_lines
            lines = tail_lines(self.learn_dir / "agent-loop.jsonl", limit)
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                pass
        heartbeats = [r for r in out if r.get("heartbeat") is True]
        if not heartbeats:
            return out
        current: List[Dict[str, Any]] = []
        prev_ts: Optional[int] = None
        for rec in reversed(heartbeats):
            ts = int(rec.get("ts_ms") or 0)
            if prev_ts is not None and ts and (prev_ts - ts) > 120_000:
                break
            current.append(rec)
            prev_ts = ts
        return list(reversed(current))

    def calibration(self) -> Dict[str, Any]:
        try:
            return json.loads((self.learn_dir / "calibration.json").read_text(
                encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def learning_scorecard(self) -> Dict[str, Any]:
        try:
            return json.loads((self.learn_dir / "scorecard.json").read_text(
                encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def runtime_policy(self) -> Dict[str, Any]:
        try:
            return json.loads((self.learn_dir / "runtime-policy.json").read_text(
                encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def learning_status(self) -> Dict[str, Any]:
        scorecard = self.learning_scorecard()
        policy = self.runtime_policy()
        setups = scorecard.get("setups") if isinstance(scorecard, dict) else []
        suggestions = policy.get("suggestions") if isinstance(policy, dict) else []
        return {
            "scorecard": scorecard,
            "runtime_policy": policy,
            "setup_count": len(setups or []),
            "suggestion_count": len(suggestions or []),
        }

    def lessons(self, limit: int = 40) -> List[str]:
        try:
            ls = [l.strip() for l in (self.learn_dir / "sim_lessons.md").read_text(
                encoding="utf-8").splitlines() if l.strip()]
            return ls[-limit:]
        except OSError:
            return []

    def equity_curve(self, limit: int = 1000) -> Dict[str, Any]:
        c = self._sim()
        if c is None:
            return {"points": [], "realized": 0.0}
        try:
            rows = c.execute(
                "SELECT ts_ms, json_extract(payload,'$.realized_delta') AS rd "
                "FROM events WHERE kind='POSITION_UPDATE' ORDER BY ts_ms LIMIT ?",
                (limit,)).fetchall()
            cum = 0.0
            pts: List[Dict[str, Any]] = []
            wins = losses = 0
            for r in rows:
                rd = float(r["rd"] or 0.0)
                if rd > 0: wins += 1
                elif rd < 0: losses += 1
                cum += rd
                pts.append({"ts": r["ts_ms"], "eq": round(cum, 2)})
            return {"points": pts, "realized": round(cum, 2),
                    "wins": wins, "losses": losses}
        except sqlite3.OperationalError:
            return {"points": [], "realized": 0.0}
        finally:
            c.close()

    def recent_fills(self, limit: int = 30) -> List[Dict[str, Any]]:
        c = self._sim()
        if c is None:
            return []
        try:
            rows = c.execute(
                "SELECT id, side, type, qty, filled_price, filled_ms, role, "
                "reason, decision_tag FROM orders WHERE status='FILLED' "
                "ORDER BY filled_ms DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []
        finally:
            c.close()

    def agent_summary(self) -> Dict[str, Any]:
        from collections import Counter
        feed = self.agent_feed(limit=500)
        acts: Counter = Counter(f.get("action") or "?" for f in feed)
        executed = sum(1 for f in feed if f.get("executed"))
        vetoes = sum(1 for f in feed if str(f.get("governor") or "").startswith("VETO"))
        deviations = sum(1 for f in feed if f.get("deviates"))
        eq = self.equity_curve()
        pos = self.current_position()
        last = feed[-1] if feed else None
        armed = bool(last.get("armed")) if last else False
        now_ms = int(time.time() * 1000)
        last_ts = int(last.get("ts_ms") or 0) if last else 0
        last_age = round((now_ms - last_ts) / 1000.0, 3) if last_ts else None
        wins, losses = eq.get("wins", 0), eq.get("losses", 0)
        wr = round(100.0 * wins / (wins + losses), 1) if (wins + losses) else None
        return {
            "cycles": len(feed), "executed": executed, "vetoes": vetoes,
            "deviations": deviations, "actions": dict(acts),
            "realized": eq["realized"], "wins": wins, "losses": losses,
            "win_rate": wr, "armed": armed, "last": last,
            "last_heartbeat_age_sec": last_age,
            "position": pos,
        }

    def cron_status(self, task_name: str = "PaxAgentCron") -> Dict[str, Any]:
        """Best-effort read of Windows Task Scheduler state for cron agent."""
        if sys.platform != "win32":
            return {"available": False, "reason": "not_windows"}
        ps = (
            "$t=Get-ScheduledTask -TaskName '%s' -ErrorAction SilentlyContinue; "
            "if($null -eq $t){'{\"installed\":false}'; exit 0}; "
            "$i=Get-ScheduledTaskInfo -TaskName '%s'; "
            "$a=$t.Actions | Select-Object -First 1; "
            "[pscustomobject]@{installed=$true;taskName=$t.TaskName;state=$t.State.ToString();"
            "execute=$a.Execute;arguments=$a.Arguments;workingDirectory=$a.WorkingDirectory;"
            "lastRunTime=$i.LastRunTime;lastTaskResult=$i.LastTaskResult;nextRunTime=$i.NextRunTime;"
            "missedRuns=$i.NumberOfMissedRuns} | ConvertTo-Json -Compress"
        ) % (task_name, task_name)
        try:
            popen_kwargs: Dict[str, Any] = {}
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = getattr(
                    subprocess, "CREATE_NO_WINDOW", 0)
            r = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-Command", ps],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=3.0, **popen_kwargs)
        except Exception as exc:
            return {"available": False, "error": str(exc)}
        if r.returncode != 0:
            return {"available": False, "error": (r.stderr or r.stdout)[:500]}
        try:
            out = json.loads((r.stdout or "").strip() or "{}")
        except json.JSONDecodeError:
            return {"available": False, "error": (r.stdout or "")[:500]}
        out["available"] = True
        return out

    # ─── freshness / source helpers (Stage 1 data-truth) ────────────

    def _sim_max_ms(self, expr: str, where: str = "") -> Optional[int]:
        c = self._sim()
        if c is None:
            return None
        try:
            row = c.execute(
                f"SELECT MAX({expr}) AS m FROM orders {where}").fetchone()
            return int(row["m"]) if row and row["m"] else None
        except sqlite3.OperationalError:
            return None
        finally:
            c.close()

    def _journal_max_ms(self, table: str) -> Optional[int]:
        try:
            with self._journal() as c:
                row = c.execute(f"SELECT MAX(ts_ms) AS m FROM {table}").fetchone()
                return int(row["m"]) if row and row["m"] else None
        except sqlite3.OperationalError:
            return None

    def _heartbeat_mode(self) -> Tuple[Optional[int], str]:
        """Return (last_heartbeat_ms, mode) where mode is armed/observe/
        stale/unknown. mode reflects the LAST record but the caller's
        is_stale flag (via the envelope) is what makes 'looks live' honest.
        """
        feed = self.agent_feed(limit=2)
        if not feed:
            return None, "unknown"
        last = feed[-1]
        ts = int(last.get("ts_ms") or 0) or None
        base = "armed" if last.get("armed") else "observe"
        if ts:
            fresh = pax_freshness.staleness(ts, "heartbeat")
            if fresh["is_stale"]:
                return ts, "stale"
        return ts, base

    def enveloped(self, name: str) -> Any:
        """Return the freshness-enveloped payload for an endpoint name.

        Centralizes the data-truth contract: every endpoint declares its
        source, source_path, updated_at, and (where relevant) mode, and the
        envelope computes age_sec / is_stale / stale_reason.
        """
        jp = str(self.journal_path)
        sp = str(self.sim_db_path) if self.sim_db_path else None
        ld = str(self.learn_dir)
        if name == "status":
            data = self.status()
            return pax_freshness.envelope(
                data, source="journal", source_path=jp,
                updated_at_ms=self._journal_max_ms("snapshots"))
        if name == "position":
            return pax_freshness.envelope(
                self.current_position(), source="sim_db", source_path=sp,
                updated_at_ms=self._sim_max_ms("filled_ms"))
        if name == "working":
            return pax_freshness.envelope(
                self.working_orders(), source="sim_db", source_path=sp,
                updated_at_ms=self._sim_max_ms(
                    "placed_ms", "WHERE status IN ('WORKING','TRIGGERED')"))
        if name == "fills":
            return pax_freshness.envelope(
                self.recent_fills(), source="sim_db", source_path=sp,
                updated_at_ms=self._sim_max_ms("filled_ms",
                                               "WHERE status='FILLED'"))
        if name == "equity":
            eq = self.equity_curve()
            pts = eq.get("points") or []
            last_ts = pts[-1]["ts"] if pts else None
            return pax_freshness.envelope(eq, source="sim_db", source_path=sp,
                                          updated_at_ms=last_ts)
        if name == "daily_stats":
            return pax_freshness.envelope(
                self.daily_stats(), source="journal", source_path=jp,
                updated_at_ms=None)
        if name == "pnl_summary":
            return pax_freshness.envelope(self.pnl_summary(), source="journal",
                                          source_path=jp, updated_at_ms=None)
        if name == "signals":
            return pax_freshness.envelope(
                self.latest_signals(), source="journal", source_path=jp,
                updated_at_ms=self._journal_max_ms("signals"))
        if name == "setup_winrates":
            return pax_freshness.envelope(self.setup_winrates(),
                                          source="journal", source_path=jp,
                                          updated_at_ms=None)
        if name == "errors":
            return pax_freshness.envelope(
                self.errors(), source="journal", source_path=jp,
                updated_at_ms=self._journal_max_ms("events"))
        if name == "agent_feed":
            ts, mode = self._heartbeat_mode()
            feed = [pax_roles.annotate_record(r) for r in self.agent_feed()]
            return pax_freshness.envelope(
                feed, source="heartbeat",
                source_path=str(self.learn_dir / "agent-loop.jsonl"),
                updated_at_ms=ts, mode=mode)
        if name == "agent_summary":
            ts, mode = self._heartbeat_mode()
            data = self.agent_summary()
            return pax_freshness.envelope(
                data, source="heartbeat",
                source_path=str(self.learn_dir / "agent-loop.jsonl"),
                updated_at_ms=ts, mode=mode)
        if name == "calibration":
            return pax_freshness.envelope(
                self.calibration(), source="learn_file",
                source_path=str(self.learn_dir / "calibration.json"),
                updated_at_ms=_mtime_ms(self.learn_dir / "calibration.json"))
        if name == "learning_scorecard":
            return pax_freshness.envelope(
                self.learning_scorecard(), source="learn_file",
                source_path=str(self.learn_dir / "scorecard.json"),
                updated_at_ms=_mtime_ms(self.learn_dir / "scorecard.json"))
        if name == "runtime_policy":
            return pax_freshness.envelope(
                self.runtime_policy(), source="learn_file",
                source_path=str(self.learn_dir / "runtime-policy.json"),
                updated_at_ms=_mtime_ms(self.learn_dir / "runtime-policy.json"))
        if name == "learning_status":
            return pax_freshness.envelope(
                self.learning_status(), source="learn_file", source_path=ld,
                updated_at_ms=_mtime_ms(self.learn_dir / "scorecard.json"))
        if name == "lessons":
            return pax_freshness.envelope(
                self.lessons(), source="learn_file",
                source_path=str(self.learn_dir / "sim_lessons.md"),
                updated_at_ms=_mtime_ms(self.learn_dir / "sim_lessons.md"))
        if name == "cron_status":
            return pax_freshness.envelope(self.cron_status(),
                                          source="task_scheduler",
                                          source_path=None, updated_at_ms=None)
        if name == "promotion_report":
            return pax_freshness.envelope(
                self.promotion_report(), source="learn_file",
                source_path=str(self.learn_dir / "scorecard.json"),
                updated_at_ms=_mtime_ms(self.learn_dir / "scorecard.json"))
        raise KeyError(name)

    # ─── evaluation gate (Stage 6, read-only) ───────────────────────

    def kill_switch_active(self) -> bool:
        return (self.learn_dir / "KILL_SWITCH").exists()

    def _ops_status(self, now: Optional[int] = None) -> Dict[str, Any]:
        """Operational freshness/availability flags shared by eval + health,
        keyed to the same staleness budgets the live gate uses."""
        now = pax_freshness.now_ms() if now is None else now
        hb_ms, _mode = self._heartbeat_mode()
        hb_stale = bool(pax_freshness.staleness(hb_ms, "heartbeat", now)["is_stale"])
        snap_ms = self._journal_max_ms("snapshots")
        market_stale = bool(
            pax_freshness.staleness(snap_ms, "market", now)["is_stale"])
        pf = self.sim_broker_preflight()
        sim_reachable = bool(pf["openable"] and pf["readable"])
        return {"heartbeat_stale": hb_stale, "market_stale": market_stale,
                "sim_broker_unavailable": not sim_reachable}

    def _last_risk_halt_record(self, limit: int = 200) -> Optional[Dict[str, Any]]:
        """Most recent agent-loop record that carried an enforced risk halt."""
        for rec in reversed(self.agent_feed(limit=limit)):
            code = rec.get("risk_halt_code") or rec.get("risk_halt")
            if code:
                return {"ts_ms": rec.get("ts_ms"), "risk_halt_code": code,
                        "risk_halt_message": rec.get("risk_halt_message"),
                        "governor": rec.get("governor")}
        return None

    def evaluation_state(self) -> Dict[str, Any]:
        summary = self.agent_summary()
        eq = self.equity_curve()
        ts, _mode = self._heartbeat_mode()
        # Restriction is driven by CURRENT operational state (real-time stale /
        # broker flags), never a historical halt record -- a past max_trades
        # block must not pin the next session into restriction.
        ops = self._ops_status()
        hb_stale = ops["heartbeat_stale"]
        state = pax_eval_state.compute_eval_state(
            scorecard=self.learning_scorecard(),
            runtime_policy=self.runtime_policy(),
            agent_stats={
                "armed": summary.get("armed"),
                "cycles": summary.get("cycles"),
                "executed": summary.get("executed"),
                "wins": eq.get("wins"),
                "losses": eq.get("losses"),
            },
            ops={**ops, "malformed_count": 0},
            kill_switch_active=self.kill_switch_active(),
        )
        return pax_freshness.envelope(
            state, source="heartbeat",
            source_path=str(self.learn_dir / "agent-loop.jsonl"),
            updated_at_ms=ts, mode=("stale" if hb_stale else
                                    ("armed" if summary.get("armed") else "observe")))

    # ─── health (Stage 7) ───────────────────────────────────────────

    def health(self) -> Dict[str, Any]:
        """Aggregate per-source freshness + risk/eval summary into one
        authoritative truth surface."""
        now = pax_freshness.now_ms()
        sources: Dict[str, Any] = {}

        snap_ms = self._journal_max_ms("snapshots")
        sources["market"] = pax_freshness.staleness(snap_ms, "market", now)
        sources["market"]["source"] = "market"
        sources["market"]["source_path"] = str(self.journal_path)

        sim_ms = self._sim_max_ms("placed_ms")
        sources["sim_db"] = pax_freshness.staleness(sim_ms, "sim_db", now)
        sources["sim_db"]["source"] = "sim_db"
        # Reachability is a real read-only preflight (openable AND a minimal
        # schema query succeeds), not mere file existence -- a corrupt/locked/
        # non-SIM DB is reported unavailable with its error.
        pf = self.sim_broker_preflight()
        sources["sim_db"]["reachable"] = bool(pf["openable"] and pf["readable"])
        sources["sim_db"]["openable"] = pf["openable"]
        sources["sim_db"]["readable"] = pf["readable"]
        sources["sim_db"]["error"] = pf["error"]
        sources["sim_db"]["source_path"] = pf["source_path"]

        hb_ms, mode = self._heartbeat_mode()
        sources["heartbeat"] = pax_freshness.staleness(hb_ms, "heartbeat", now)
        sources["heartbeat"]["source"] = "heartbeat"
        sources["heartbeat"]["mode"] = mode

        for fname, key in (("scorecard.json", "scorecard"),
                           ("runtime-policy.json", "runtime_policy"),
                           ("calibration.json", "calibration")):
            p = self.learn_dir / fname
            m = pax_freshness.staleness(_mtime_ms(p), "learn_file", now)
            m["source"] = "learn_file"
            m["reachable"] = p.exists()
            m["source_path"] = str(p)
            sources[key] = m

        agg = pax_freshness.worst_of(*sources.values())
        try:
            evald = self.evaluation_state()
            eval_level = evald.get("level")
            live_blocked = evald.get("live_blocked")
        except Exception:
            eval_level, live_blocked = None, True

        # Enforced operational risk-halt summary. Same vocabulary + precedence
        # as pax_risk_gate so health, eval, and the live gate agree on truth.
        ks = self.kill_switch_active()
        hb_stale = bool(sources["heartbeat"].get("is_stale"))
        market_stale = bool(sources["market"].get("is_stale"))
        broker_unavailable = not bool(sources["sim_db"].get("reachable"))
        halt_code: Optional[str] = None
        halt_message: Optional[str] = None
        if ks:
            halt_code, halt_message = (pax_risk_gate.KILL_SWITCH,
                                       "operator kill switch engaged")
        elif hb_stale:
            halt_code, halt_message = (pax_risk_gate.STALE_HEARTBEAT,
                                       "agent heartbeat stale")
        elif market_stale:
            halt_code, halt_message = (pax_risk_gate.STALE_MARKET,
                                       "market data stale")
        elif broker_unavailable:
            halt_code, halt_message = (pax_risk_gate.SIM_BROKER_UNAVAILABLE,
                                       "SIM broker DB unavailable")
        last_halt = self._last_risk_halt_record()

        return {
            "service": "pax_overview_ui",
            "up": True,
            "git_commit": _git_commit(),
            "checked_ms": now,
            "mode": mode,
            "sources": sources,
            "is_stale": agg["is_stale"],
            "stale_sources": agg["stale_sources"],
            "kill_switch_active": ks,
            "risk_halt_active": halt_code is not None,
            "risk_halt_code": halt_code,
            "risk_halt_message": halt_message,
            "last_risk_halt_record": last_halt,
            "evaluation_level": eval_level,
            "live_blocked": live_blocked,
        }

    # ─── promotion report (Stage 3, read-only) ──────────────────────

    def promotion_report(self) -> Dict[str, Any]:
        """Honest per-setup promotion view from the SIM scorecard. Read-only,
        deterministic; reuses pax_promotion_report (which reuses the audited
        eval-state eligibility). live trading stays hard-blocked."""
        return pax_promotion_report.build_promotion_report(
            self.learning_scorecard())

    # ─── arming readiness (Stage 5, read-only go/no-go) ─────────────

    def _learn_dir_writable(self) -> Tuple[bool, Optional[str]]:
        d = self.learn_dir
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".arming_write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return True, None
        except OSError as e:
            return False, f"{type(e).__name__}: {e}"

    def arming_check(self) -> Dict[str, Any]:
        """Machine-readable go/no-go before arming SIM. Read-only; never arms,
        never unlocks live. ``can_arm`` is false unless every BLOCKING check
        passes (warnings do not block). With Bookmap closed/weekend the market
        + heartbeat sources are stale, so this correctly returns can_arm=false."""
        now = pax_freshness.now_ms()
        try:
            h = self.health()
        except Exception as exc:   # never raise from a readiness probe
            return {
                "can_arm": False, "mode": "unknown", "live_blocked": True,
                "checks": [{"code": "health_error", "status": "fail",
                            "message": f"{type(exc).__name__}: {exc}"}],
                "blocking_codes": ["health_error"], "warnings": [],
                "required_actions": ["investigate overview health error"],
                "checked_ms": now,
            }

        sources = h.get("sources") or {}
        checks: List[Dict[str, Any]] = []
        fails: List[str] = []
        warns: List[str] = []
        required: List[str] = []

        def add(code: str, ok_status: str, message: str,
                action: Optional[str] = None) -> None:
            checks.append({"code": code, "status": ok_status, "message": message})
            if ok_status == "fail":
                fails.append(code)
                if action:
                    required.append(action)
            elif ok_status == "warn":
                warns.append(code)

        ks = bool(h.get("kill_switch_active"))
        add("kill_switch_absent", "fail" if ks else "pass",
            "operator kill switch engaged" if ks else "no kill switch file",
            "remove the KILL_SWITCH file")

        hb_stale = bool((sources.get("heartbeat") or {}).get("is_stale"))
        add("heartbeat_fresh", "fail" if hb_stale else "pass",
            "heartbeat stale/absent -- start observe mode and confirm a beat"
            if hb_stale else "agent heartbeat fresh",
            "start PAX observe (paxi.bat start) and confirm a fresh heartbeat")

        mk_stale = bool((sources.get("market") or {}).get("is_stale"))
        add("market_data_fresh", "fail" if mk_stale else "pass",
            "market data stale/absent (bridge/Bookmap feed down)"
            if mk_stale else "market data fresh",
            "bring up Bookmap + bridge so market data is fresh")

        sim_ok = bool((sources.get("sim_db") or {}).get("reachable"))
        add("sim_broker_ok", "pass" if sim_ok else "fail",
            "SIM broker openable+readable" if sim_ok
            else ((sources.get("sim_db") or {}).get("error")
                  or "SIM broker DB unavailable"),
            "point PAX at a valid, readable SIM broker DB")

        live_blocked = bool(h.get("live_blocked", True))
        add("live_hard_blocked", "pass" if live_blocked else "fail",
            "live trading hard-blocked (SIM only)" if live_blocked
            else "LIVE NOT BLOCKED -- refuse to arm",
            "restore the live hard-block before any arming")

        stale_required = [s for s in (h.get("stale_sources") or [])
                          if s in ("market", "heartbeat")]
        add("required_sources_fresh", "fail" if stale_required else "pass",
            ("stale required sources: " + ", ".join(stale_required))
            if stale_required else "required live sources fresh")

        rha = bool(h.get("risk_halt_active"))
        add("risk_halt_clear", "fail" if rha else "pass",
            (h.get("risk_halt_message") or h.get("risk_halt_code")
             or "active risk halt") if rha else "no active risk halt",
            f"clear risk halt: {h.get('risk_halt_code')}" if rha else None)

        sc = self.learning_scorecard()
        has_setups = bool(isinstance(sc, dict) and sc.get("setups"))
        add("scorecard_present", "pass" if has_setups else "warn",
            "scorecard with setups present" if has_setups
            else "no scorecard/outcome data yet (exploratory; not a blocker)")

        writable, werr = self._learn_dir_writable()
        add("session_report_writable", "pass" if writable else "warn",
            "session report path writable" if writable
            else f"learn dir not writable: {werr}")

        return {
            "can_arm": len(fails) == 0,
            "mode": h.get("mode"),
            "live_blocked": True,
            "checks": checks,
            "blocking_codes": fails,
            "warnings": warns,
            "required_actions": required,
            "checked_ms": now,
        }


# ─── HTTP handler ───────────────────────────────────────────────────────

def _json_response(handler: BaseHTTPRequestHandler, data: Any,
                    status: int = 200) -> None:
    body = json.dumps(data, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _html_response(handler: BaseHTTPRequestHandler, html: str) -> None:
    body = html.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _build_handler(queries: OverviewQueries) -> type:
    """Factory: bake the queries object into a BaseHTTPRequestHandler subclass."""

    class _Handler(BaseHTTPRequestHandler):
        # Silence default per-request stdout noise.
        def log_message(self, fmt: str, *args: Any) -> None:
            pass

        # Read-only — every non-GET returns 405.
        def do_POST(self):    self.send_error(405, "read-only overview")
        def do_PUT(self):     self.send_error(405, "read-only overview")
        def do_DELETE(self):  self.send_error(405, "read-only overview")
        def do_PATCH(self):   self.send_error(405, "read-only overview")

        def do_GET(self):
            path = (self.path or "").split("?")[0].rstrip("/")
            try:
                if path in ("", "/"):
                    return _html_response(self, _PAGE_HTML)
                # Freshness-enveloped endpoints (Stage 1 data-truth). Every
                # one carries _meta {source, source_path, updated_at, age_sec,
                # is_stale, stale_reason, mode}. Lists are under .items.
                _ENVELOPED = {
                    "/api/status", "/api/position", "/api/working",
                    "/api/daily_stats", "/api/pnl_summary", "/api/signals",
                    "/api/setup_winrates", "/api/errors", "/api/agent_summary",
                    "/api/agent_feed", "/api/cron_status", "/api/calibration",
                    "/api/learning_scorecard", "/api/runtime_policy",
                    "/api/learning_status", "/api/equity", "/api/fills",
                    "/api/lessons",
                }
                if path in _ENVELOPED:
                    return _json_response(self,
                                          queries.enveloped(path[len("/api/"):]))
                if path == "/api/health":
                    return _json_response(self, queries.health())
                if path == "/api/evaluation_state":
                    return _json_response(self, queries.evaluation_state())
                if path == "/api/arming_check":
                    return _json_response(self, queries.arming_check())
                if path == "/api/promotion_report":
                    return _json_response(self,
                                          queries.enveloped("promotion_report"))
                return self.send_error(404, f"unknown path: {path}")
            except Exception as exc:   # pragma: no cover — defensive
                return _json_response(self,
                                       {"error": f"{type(exc).__name__}: {exc}"},
                                       status=500)

    return _Handler


# ─── HTML / JS ──────────────────────────────────────────────────────────

_PAGE_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<title>Pax Overview</title>
<style>
  :root{
    --bg:#070a10; --txt:#dbe4f3; --muted:#6b7891; --line:rgba(255,255,255,0.07);
    --glass:rgba(255,255,255,0.035); --glass2:rgba(255,255,255,0.05);
    --grn:#34d399; --red:#fb7185; --cyan:#38bdf8; --amber:#fbbf24; --violet:#a78bfa;
  }
  *{box-sizing:border-box}
  body{font-family:'Inter',system-ui,-apple-system,Segoe UI,sans-serif;
    background:radial-gradient(120% 90% at 15% -10%,#13203a 0%,#0a1018 45%,var(--bg) 100%) fixed;
    color:var(--txt); margin:0; padding:18px; min-height:100vh; font-size:13px;}
  .head{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}
  .head h1{font-size:16px;font-weight:700;letter-spacing:.14em;margin:0;
    background:linear-gradient(90deg,#7dd3fc,#a78bfa);-webkit-background-clip:text;
    -webkit-text-fill-color:transparent}
  .head .meta{font-size:11px;color:var(--muted);font-family:ui-monospace,monospace}
  .dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#445;margin-right:6px;vertical-align:middle}
  .dot.on{background:var(--grn);box-shadow:0 0 8px var(--grn)}
  .dot.armed{background:var(--red);box-shadow:0 0 8px var(--red)}
  .dot.stale{background:var(--amber);box-shadow:0 0 8px var(--amber)}
  .card{background:var(--glass);border:1px solid var(--line);border-radius:14px;
    padding:14px 16px;backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
    box-shadow:0 8px 30px rgba(0,0,0,.35)}
  .grid{display:grid;gap:12px}
  .stats{grid-template-columns:repeat(6,1fr);margin-bottom:12px}
  .stat .lbl{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
  .stat .val{font-size:22px;font-weight:700;font-family:ui-monospace,monospace;margin-top:4px}
  .stat .sub{font-size:10px;color:var(--muted);margin-top:2px}
  .cols{grid-template-columns:1.3fr 1fr;align-items:start}
  .ttl{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:#93a3bd;
    margin:0 0 10px;font-weight:600}
  .pos{color:var(--grn)} .neg{color:var(--red)} .mut{color:var(--muted)}
  .feed{font-family:ui-monospace,monospace;font-size:11.5px;max-height:340px;overflow:auto}
  .feed .row{padding:3px 0;border-left:2px solid #1d2738;padding-left:9px;margin-bottom:2px;color:#9fb0c8}
  .feed .row.long{color:var(--grn);border-left-color:#2a7a55}
  .feed .row.short{color:var(--red);border-left-color:#7a2a40}
  .feed .row.wait{color:#6f7c92}
  .feed .row .t{color:#566;margin-right:8px}
  .feed .row .why{color:#7d8aa0}
  table{width:100%;border-collapse:collapse;font-size:11.5px;font-family:ui-monospace,monospace}
  th,td{padding:5px 6px;text-align:left;border-bottom:1px solid rgba(255,255,255,.05)}
  th{color:var(--muted);font-weight:600;text-transform:uppercase;font-size:9.5px;letter-spacing:.08em}
  .bar{display:flex;align-items:center;gap:8px;margin:5px 0;font-family:ui-monospace,monospace;font-size:11px}
  .bar .name{width:78px;color:#9fb0c8}
  .bar .track{flex:1;height:8px;background:rgba(255,255,255,.05);border-radius:4px;overflow:hidden}
  .bar .fill{height:100%;border-radius:4px;background:linear-gradient(90deg,#38bdf8,#a78bfa)}
  .bar .n{width:34px;text-align:right;color:var(--muted)}
  .lessons{max-height:200px;overflow:auto;font-size:11.5px}
  .lessons div{padding:3px 0;color:#9fb0c8;border-bottom:1px solid rgba(255,255,255,.04)}
  .gap{margin-top:12px}
  .pill{display:inline-block;padding:2px 8px;border-radius:20px;font-size:10px;
    font-family:ui-monospace,monospace;border:1px solid var(--line)}
  .mono{font-family:ui-monospace,monospace;font-size:11px;color:#9fb0c8;line-height:1.45}
  ::-webkit-scrollbar{width:7px;height:7px}
  ::-webkit-scrollbar-thumb{background:#223;border-radius:4px}
</style></head>
<body>
  <div class="head">
    <h1>PAX&nbsp;QUANT&nbsp;DESK</h1>
    <div class="meta"><span id="agdot" class="dot"></span><span id="agstate">connecting</span> &middot; <span id="rfsh">--</span></div>
  </div>

  <div class="grid stats" id="stats"></div>

  <div class="card">
    <div class="ttl">Equity curve &middot; realized P&amp;L (sim)</div>
    <div id="equity"></div>
  </div>

  <div class="grid cols gap">
    <div>
      <div class="card">
        <div class="ttl">Agent decision feed</div>
        <div class="feed" id="feed"></div>
      </div>
      <div class="card gap">
        <div class="ttl">Lessons learned (self-written)</div>
        <div class="lessons" id="lessons"></div>
      </div>
    </div>
    <div>
      <div class="card">
        <div class="ttl">Calibration &middot; tuning data</div>
        <div id="calib"></div>
      </div>
      <div class="card gap">
        <div class="ttl">Settings</div>
        <div id="settings" class="mono">loading...</div>
      </div>
      <div class="card gap">
        <div class="ttl">Position &amp; working orders</div>
        <div id="posbox"></div>
        <div id="working" class="gap"></div>
      </div>
      <div class="card gap">
        <div class="ttl">Recent fills</div>
        <div id="fills"></div>
      </div>
    </div>
  </div>

<script>
const $ = id => document.getElementById(id);
const esc = s => String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const money = n => (n<0?'-$':'$') + Math.abs(Number(n||0)).toFixed(2);
const fmtMs = ms => ms ? new Date(Number(ms)).toLocaleTimeString() : '--';
async function J(p){const r=await fetch(p,{cache:'no-store'});if(!r.ok)throw new Error(p+' '+r.status);return r.json();}
// Stage 1 envelope helpers: list endpoints now return {items,_meta}; dict
// endpoints carry a _meta block. Stay tolerant of both old and new shapes.
const arr = x => Array.isArray(x)?x:((x&&x.items)||[]);
const meta = x => (x&&x._meta)||{};
const ageStr = m => (m&&m.age_sec!=null)?(m.age_sec<90?Math.round(m.age_sec)+'s':Math.round(m.age_sec/60)+'m')+' ago':'--';

function statCard(lbl,val,sub,cls){
  return '<div class="card stat"><div class="lbl">'+lbl+'</div><div class="val '+(cls||'')+'">'+
    val+'</div><div class="sub">'+(sub||'')+'</div></div>';
}

// Hand-drawn SVG area+line chart (no external deps).
function equityChart(pts){
  if(!pts || pts.length<2) return '<div class="mut" style="font-family:ui-monospace;font-size:12px">no closed trades yet - equity flat at $0</div>';
  const eq=pts.map(p=>p.eq); let mn=Math.min(...eq,0), mx=Math.max(...eq,0);
  if(mx===mn){mx+=1;mn-=1;}
  const W=1000,H=160,pad=4;
  const X=i=>pad+i*(W-2*pad)/(pts.length-1);
  const Y=v=>pad+(H-2*pad)*(1-(v-mn)/(mx-mn));
  let line='',area='';
  pts.forEach((p,i)=>{const x=X(i).toFixed(1),y=Y(p.eq).toFixed(1);line+=(i?'L':'M')+x+' '+y+' ';});
  area='M'+X(0).toFixed(1)+' '+Y(0).toFixed(1)+' '+line.replace(/^M/,'L')+'L'+X(pts.length-1).toFixed(1)+' '+Y(0).toFixed(1)+' Z';
  const up=eq[eq.length-1]>=0; const col=up?'#34d399':'#fb7185';
  const zeroY=Y(0).toFixed(1);
  return '<svg viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none" style="width:100%;height:170px;display:block">'+
    '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'+
    '<stop offset="0" stop-color="'+col+'" stop-opacity="0.28"/>'+
    '<stop offset="1" stop-color="'+col+'" stop-opacity="0"/></linearGradient></defs>'+
    '<line x1="0" y1="'+zeroY+'" x2="'+W+'" y2="'+zeroY+'" stroke="rgba(255,255,255,.12)" stroke-dasharray="4 4"/>'+
    '<path d="'+area+'" fill="url(#g)"/>'+
    '<path d="'+line+'" fill="none" stroke="'+col+'" stroke-width="2"/></svg>';
}

function bars(actions){
  const ents=Object.entries(actions||{}).sort((a,b)=>b[1]-a[1]);
  if(!ents.length) return '<div class="mut">no decisions yet</div>';
  const mx=Math.max(...ents.map(e=>e[1]));
  return ents.map(([k,v])=>'<div class="bar"><span class="name">'+esc(k)+'</span>'+
    '<span class="track"><span class="fill" style="width:'+(100*v/mx)+'%"></span></span>'+
    '<span class="n">'+v+'</span></div>').join('');
}

function table(rows,cols){
  if(!rows||!rows.length) return '<div class="mut" style="font-size:11px">none</div>';
  let h='<table><tr>'+cols.map(c=>'<th>'+c.h+'</th>').join('')+'</tr>';
  rows.forEach(r=>{h+='<tr>'+cols.map(c=>{const v=c.f?c.f(r):r[c.k];return '<td>'+(v==null?'--':v)+'</td>';}).join('')+'</tr>';});
  return h+'</table>';
}

function feedRow(r){
  const a=String(r.action||(r.error?'ERR':'?'));
  const cls=a.indexOf('LONG')>=0?'long':a.indexOf('SHORT')>=0?'short':(a==='WAIT'||a==='HOLD')?'wait':'';
  const why=esc(r.rationale||r.reason||r.governor||r.error||'');
  return '<div class="row '+cls+'"><span class="t">'+fmtMs(r.ts_ms)+'</span>'+
    (r.armed?'<b>['+'ARMED'+']</b> ':'')+esc(a)+(why?' <span class="why">'+why+'</span>':'')+'</div>';
}

async function refresh(){
  try{
    const [sum,feed,calib,eq,fills,pos,working,cron,learn]=await Promise.all([
      J('/api/agent_summary'),J('/api/agent_feed'),J('/api/calibration'),
      J('/api/equity'),J('/api/fills'),J('/api/position'),J('/api/working'),
      J('/api/cron_status'),J('/api/learning_status')]);

    const sm=meta(sum);
    const stale=!!sm.is_stale, mode=sm.mode||(sum.armed?'armed':'observe');
    const armed=sum.armed, running=(sum.cycles||0)>0;
    // Data truth: never present a stale heartbeat as live. If the last
    // heartbeat is older than its budget, say STALE with the age.
    if(stale){
      $('agdot').className='dot stale';
      $('agstate').textContent='AGENT STALE ('+ageStr(sm)+')'+(mode==='armed'?' last:ARMED':mode==='observe'?' last:observe':'');
    }else{
      $('agdot').className='dot '+(armed?'armed':running?'on':'');
      $('agstate').textContent='AGENT '+(armed?'ARMED (sim)':running?'observing':'idle');
    }
    $('rfsh').textContent=new Date().toLocaleTimeString()+(stale?' · STALE':'');

    const rz=Number(sum.realized||0);
    const pz=(sum.position&&sum.position.size)||0;
    $('stats').innerHTML=
      statCard('Realized P&L',money(rz),'sim paper',rz>=0?'pos':'neg')+
      statCard('Win rate',sum.win_rate==null?'--':sum.win_rate+'%',(sum.wins||0)+'W / '+(sum.losses||0)+'L')+
      statCard('Agent cycles',sum.cycles||0,(sum.executed||0)+' executed')+
      statCard('Vetoed',sum.vetoes||0,'governor blocks',(sum.vetoes?'neg':'mut'))+
      statCard('Deviations',sum.deviations||0,'vs baseline rule','')+
      statCard('Open pos',pz,pz>0?'long':pz<0?'short':'flat',pz>0?'pos':pz<0?'neg':'mut');

    $('equity').innerHTML=equityChart(eq.points);
    const feedArr=arr(feed);
    $('feed').innerHTML=feedArr.slice().reverse().map(feedRow).join('')||'<div class="mut">waiting for agent...</div>';
    if(cron && cron.installed){
      const hidden = String(cron.execute||'').toLowerCase().indexOf('pythonw.exe')>=0;
      const mode = String(cron.arguments||'').indexOf('--armed')>=0 ? 'ARMED' : 'observe';
      $('settings').innerHTML =
        '<div><span class="dot '+(cron.state==='Running'?'on':'')+'"></span>'+
        'cron '+esc(cron.state)+' &middot; '+mode+'</div>'+
        '<div>next: '+esc(cron.nextRunTime||'--')+' &middot; last result: '+esc(cron.lastTaskResult)+'</div>'+
        '<div>runner: '+(hidden?'hidden pythonw':'visible python')+'</div>'+
        '<div class="mut">'+esc(cron.arguments||'')+'</div>'+
        '<div>learning: '+(learn.setup_count||0)+' setup buckets &middot; '+
        (learn.suggestion_count||0)+' runtime suggestions</div>';
    }else{
      $('settings').innerHTML='<div class="mut">PaxAgentCron not installed</div>'+
        '<div>learning: '+(learn.setup_count||0)+' setup buckets &middot; '+
        (learn.suggestion_count||0)+' runtime suggestions</div>';
    }

    $('calib').innerHTML=
      '<div style="display:flex;gap:8px;margin-bottom:10px">'+
      '<span class="pill">veto '+Math.round(100*(calib.veto_rate||0))+'%</span>'+
      '<span class="pill">deviate '+Math.round(100*(calib.deviation_rate||0))+'%</span>'+
      '<span class="pill">'+(calib.decisions||0)+' decisions</span></div>'+
      bars((calib.by_action)||sum.actions);

    const psize=(pos&&pos.size)||0;
    if(!pos||!('size' in pos)){$('posbox').innerHTML='<div class="mut">flat</div>';}
    else{const s=psize;
      $('posbox').innerHTML='<div style="font-family:ui-monospace;font-size:13px">'+
        '<span class="'+(s>0?'pos':s<0?'neg':'mut')+'">'+(s>0?'LONG ':s<0?'SHORT ':'FLAT ')+s+'</span>'+
        (pos.avg_price?(' @ '+Number(pos.avg_price).toFixed(2)):'')+
        '  &middot; realized '+money(pos.realized_pnl)+'</div>';}

    $('working').innerHTML=table(arr(working),[
      {h:'Side',k:'side'},{h:'Type',k:'type'},{h:'Qty',k:'qty'},
      {h:'Limit',f:r=>r.limit_price?Number(r.limit_price).toFixed(2):'--'},
      {h:'Stop',f:r=>r.stop_price?Number(r.stop_price).toFixed(2):'--'},
      {h:'Role',k:'role'},{h:'Status',k:'status'}]);

    $('fills').innerHTML=table(arr(fills),[
      {h:'Time',f:r=>fmtMs(r.filled_ms)},{h:'Side',k:'side'},{h:'Qty',k:'qty'},
      {h:'Price',f:r=>r.filled_price?Number(r.filled_price).toFixed(2):'--'},
      {h:'Role',k:'role'},{h:'Tag',k:'decision_tag'}]);

    // lessons
    try{const ls=arr(await J('/api/lessons'));
      $('lessons').innerHTML=(ls&&ls.length)?ls.slice().reverse().map(l=>'<div>'+esc(l)+'</div>').join(''):'<div class="mut">none yet - the agent writes these as it learns</div>';
    }catch(e){}
  }catch(err){ $('rfsh').textContent='error: '+err.message; }
}
refresh();
setInterval(refresh,4000);
</script>
</body></html>
"""


# ─── serve / CLI ────────────────────────────────────────────────────────

def serve(journal_path: Path, sim_db_path: Optional[Path],
           host: str = "127.0.0.1", port: int = 18890) -> None:
    queries = OverviewQueries(journal_path, sim_db_path)
    Handler = _build_handler(queries)
    srv = ThreadingHTTPServer((host, port), Handler)
    sys.stderr.write(f"Pax overview UI on http://{host}:{port}\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("shutdown\n")
    finally:
        srv.server_close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="overview_ui",
        description="Read-only Pax overview dashboard.")
    p.add_argument("--journal", type=Path,
                    default=Path(r"D:\BookmapLogs\pax-journal.db"))
    p.add_argument("--sim-db", type=Path,
                    default=Path(r"D:\BookmapLogs\pax-daemon-trades.db"))
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=18890)
    return p


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    serve(args.journal, args.sim_db, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
