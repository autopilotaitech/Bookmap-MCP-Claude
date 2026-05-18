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
import sqlite3
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ─── data access ────────────────────────────────────────────────────────

class OverviewQueries:
    """Read-only queries against the journal DB. SQLite is opened in
    read-only mode (`?mode=ro`) so the UI can never accidentally mutate
    the daemon's audit trail."""

    def __init__(self, journal_path: Path,
                  sim_db_path: Optional[Path] = None) -> None:
        self.journal_path = Path(journal_path)
        self.sim_db_path = Path(sim_db_path) if sim_db_path else None

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
        return c

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
                if path == "/api/status":
                    return _json_response(self, queries.status())
                if path == "/api/position":
                    return _json_response(self, queries.current_position())
                if path == "/api/working":
                    return _json_response(self, queries.working_orders())
                if path == "/api/daily_stats":
                    return _json_response(self, queries.daily_stats())
                if path == "/api/pnl_summary":
                    return _json_response(self, queries.pnl_summary())
                if path == "/api/signals":
                    return _json_response(self, queries.latest_signals())
                if path == "/api/setup_winrates":
                    return _json_response(self, queries.setup_winrates())
                if path == "/api/errors":
                    return _json_response(self, queries.errors())
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
  body { font-family: ui-monospace, monospace; background: #1a1b26;
          color: #c0caf5; margin: 10px; }
  details { background: #24283b; border: 1px solid #414868;
             border-radius: 6px; margin-bottom: 8px; padding: 6px 10px; }
  details > summary { cursor: pointer; font-weight: 600; padding: 4px 0;
                       list-style: revert; }
  details[open] > summary { color: #7aa2f7; }
  .kv { display: flex; justify-content: space-between; padding: 2px 0; }
  .kv .k { color: #9aa5ce; }
  .kv .v { color: #c0caf5; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th, td { padding: 4px 6px; text-align: left;
            border-bottom: 1px solid #2f334d; }
  th { color: #9aa5ce; }
  .ok { color: #9ece6a; } .warn { color: #e0af68; } .err { color: #f7768e; }
  .muted { color: #565f89; }
  #refresh-status { float: right; color: #565f89; font-size: 11px; }
</style></head>
<body>
<h2>Pax Overview <span id="refresh-status">loading...</span></h2>

<details id="sec-status" open>
  <summary>Status / heartbeat</summary>
  <div id="status-box"></div></details>

<details id="sec-pnl" open>
  <summary>P&amp;L summary</summary>
  <div id="pnl-box"></div></details>

<details id="sec-position" open>
  <summary>Current position</summary>
  <div id="position-box"></div></details>

<details id="sec-working" open>
  <summary>Working sim orders</summary>
  <div id="working-box"></div></details>

<details id="sec-signals" open>
  <summary>Latest signals</summary>
  <div id="signals-box"></div></details>

<details id="sec-setups">
  <summary>Setup counts</summary>
  <div id="setups-box"></div></details>

<details id="sec-daily">
  <summary>Daily stats (30d)</summary>
  <div id="daily-box"></div></details>

<details id="sec-errors">
  <summary>Errors / warnings</summary>
  <div id="errors-box"></div></details>

<details id="sec-freshness">
  <summary>Data freshness</summary>
  <div id="freshness-box"></div></details>

<script>
// Persist collapse state across reload.
document.querySelectorAll('details').forEach(el => {
  const key = 'pax-overview:' + el.id;
  const saved = localStorage.getItem(key);
  if (saved === 'closed') el.open = false;
  else if (saved === 'open') el.open = true;
  el.addEventListener('toggle', () => {
    localStorage.setItem(key, el.open ? 'open' : 'closed');
  });
});

const $ = id => document.getElementById(id);

function kv(k, v, klass) {
  return '<div class="kv"><span class="k">' + k + '</span>' +
          '<span class="v ' + (klass || '') + '">' + (v == null ? '—' : v) + '</span></div>';
}

function tableHtml(rows, cols) {
  if (!rows || !rows.length) return '<span class="muted">none</span>';
  let h = '<table><tr>';
  cols.forEach(c => h += '<th>' + c.h + '</th>');
  h += '</tr>';
  rows.forEach(r => {
    h += '<tr>';
    cols.forEach(c => {
      const v = c.f ? c.f(r) : r[c.k];
      h += '<td>' + (v == null ? '—' : v) + '</td>';
    });
    h += '</tr>';
  });
  return h + '</table>';
}

function fmtMs(ms) {
  if (!ms) return '—';
  const d = new Date(ms);
  return d.toLocaleTimeString();
}

async function fetchJson(path) {
  const r = await fetch(path, {cache: 'no-store'});
  if (!r.ok) throw new Error(path + ' -> ' + r.status);
  return r.json();
}

async function refresh() {
  $('refresh-status').textContent = 'updating...';
  try {
    const [status, pnl, pos, working, signals, setups, daily, errors] = await Promise.all([
      fetchJson('/api/status'),
      fetchJson('/api/pnl_summary'),
      fetchJson('/api/position'),
      fetchJson('/api/working'),
      fetchJson('/api/signals'),
      fetchJson('/api/setup_winrates'),
      fetchJson('/api/daily_stats'),
      fetchJson('/api/errors')
    ]);

    // Status
    const run = status.run || {};
    const health = status.health || {};
    const ageSec = status.last_snapshot_age_sec;
    const ageClass = ageSec == null ? 'muted' : ageSec < 30 ? 'ok' : ageSec < 120 ? 'warn' : 'err';
    $('status-box').innerHTML =
      kv('Run ID', run.run_id ? run.run_id.substring(0, 8) + '…' : '—') +
      kv('Adapter', run.adapter_name) +
      kv('Signal ver', run.signal_version) +
      kv('Health', health.status, health.status === 'ok' ? 'ok' : 'warn') +
      kv('Snapshots', status.snapshot_count) +
      kv('Signals', status.signal_count) +
      kv('Uptime', status.uptime_sec ? (status.uptime_sec).toFixed(0) + 's' : '—') +
      kv('Last snap', ageSec == null ? '—' : ageSec.toFixed(1) + 's ago', ageClass);

    // PnL
    $('pnl-box').innerHTML =
      kv('Gross P&L', '$' + Number(pnl.total || 0).toFixed(2)) +
      kv('Wins',  pnl.wins) + kv('Losses', pnl.losses) +
      kv('Max DD', '$' + Number(pnl.max_dd || 0).toFixed(2));

    // Position
    if (!pos) {
      $('position-box').innerHTML = '<span class="muted">no position data</span>';
    } else {
      $('position-box').innerHTML =
        kv('Alias', pos.alias) + kv('Size', pos.size,
           pos.size > 0 ? 'ok' : pos.size < 0 ? 'err' : 'muted') +
        kv('Avg', pos.avg_price ? pos.avg_price.toFixed(2) : '—') +
        kv('Realized', '$' + Number(pos.realized_pnl || 0).toFixed(2));
    }

    // Working orders
    $('working-box').innerHTML = tableHtml(working, [
      {h: 'Side', k: 'side'}, {h: 'Type', k: 'type'},
      {h: 'Qty', k: 'qty'},
      {h: 'Limit', f: r => r.limit_price ? r.limit_price.toFixed(2) : '—'},
      {h: 'Stop', f: r => r.stop_price ? r.stop_price.toFixed(2) : '—'},
      {h: 'Role', k: 'role'}, {h: 'Status', k: 'status'},
      {h: 'Armed?', f: r => r.armed_after_parent_fill ?
                              '<span class="warn">pending parent</span>' :
                              '<span class="ok">armed</span>'},
    ]);

    // Signals
    $('signals-box').innerHTML = tableHtml(signals, [
      {h: 'Time', f: r => fmtMs(r.ts_ms)},
      {h: 'Decision', k: 'decision'},
      {h: 'Size', k: 'size_tier'},
      {h: 'Conf', f: r => r.confidence ? r.confidence.toFixed(2) : '—'},
      {h: 'Level', k: 'level_label'},
      {h: 'Comp', f: r => r.composite_score ? r.composite_score.toFixed(2) : '—'},
      {h: 'Dir', k: 'composite_dir'},
      {h: 'Conv traj', k: 'conviction_trajectory'},
    ]);

    // Setup counts
    $('setups-box').innerHTML = tableHtml(setups, [
      {h: 'Decision', k: 'decision'}, {h: 'Level', k: 'level_label'},
      {h: 'N', k: 'n'},
      {h: 'Avg conf', f: r => r.avg_conf ? r.avg_conf.toFixed(2) : '—'},
    ]);

    // Daily stats
    $('daily-box').innerHTML = tableHtml(daily, [
      {h: 'Date', k: 'session_date'}, {h: 'Alias', k: 'alias'},
      {h: 'Trades', k: 'trades_count'},
      {h: 'W', k: 'wins'}, {h: 'L', k: 'losses'},
      {h: 'P&L', f: r => '$' + Number(r.gross_pnl).toFixed(2)},
      {h: 'Max DD', f: r => '$' + Number(r.max_drawdown).toFixed(2)},
    ]);

    // Errors
    $('errors-box').innerHTML = tableHtml(errors, [
      {h: 'Time', f: r => fmtMs(r.ts_ms)}, {h: 'Kind', k: 'kind'},
      {h: 'Source', k: 'source'}, {h: 'Message', k: 'message'},
    ]);

    // Freshness
    $('freshness-box').innerHTML =
      kv('Last snapshot', ageSec == null ? '—' :
                            ageSec.toFixed(1) + 's ago', ageClass) +
      kv('Heartbeat', health.ts_ms ? fmtMs(health.ts_ms) : '—');

    $('refresh-status').textContent = 'ok ' + new Date().toLocaleTimeString();
  } catch (err) {
    $('refresh-status').textContent = 'error: ' + err.message;
  }
}
refresh();
setInterval(refresh, 5000);
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
