"""Phase 4B-2 retention pruner tests.

The CLI lives in mcp-server/bookmap_mcp/pax_bus_prune.py.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

try:
    from bookmap_mcp.pax_bus_prune import main as prune_main
    _HAVE_DIRECT_IMPORT = True
except ImportError:
    _HAVE_DIRECT_IMPORT = False
    prune_main = None


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MCP_SERVER = _REPO_ROOT / "mcp-server"
_PAX_AI     = _REPO_ROOT / "pax-ai"


def _invoke_cli(args, env_extra=None):
    env = os.environ.copy()
    env.update(env_extra or {})
    py_path_parts = [str(_MCP_SERVER), str(_PAX_AI)]
    if env.get("PYTHONPATH"):
        py_path_parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(py_path_parts)
    cmd = [sys.executable, "-m", "bookmap_mcp.pax_bus_prune", *args]
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)


def _seed_bus(tmp_path, ages_days):
    """Create the schema and insert one row per (table, age) in ages_days.
    Returns (db_path, snap_dir, dig_dir, now_ms)."""
    from pax_ai import feature_bus
    db_path  = tmp_path / "pax-bus.db"
    snap_dir = tmp_path / "snapshots"
    dig_dir  = tmp_path / "digests"
    now_ms = int(time.time() * 1000)
    with feature_bus._open_db(db_path) as conn:
        feature_bus._ensure_schema(conn)
        for age_days in ages_days:
            ts = now_ms - age_days * 86_400_000
            conn.execute("""
                INSERT INTO snapshot_features
                  (schema_version, ts_ms, alias, health, mid)
                VALUES (1, ?, 'NQM6', 'ok', 100.0)
            """, (ts,))
            conn.execute("""
                INSERT INTO level_events
                  (schema_version, ts_ms, alias, level_label, level_price,
                   prev_decision, new_decision, prev_confidence, new_confidence,
                   prev_proximity, new_proximity, trigger_reason)
                VALUES (1, ?, 'NQM6', 'OR-H', 100, 'WAIT', 'FOLLOW_LONG',
                        0.3, 0.7, 0, 1, 'composite_flip')
            """, (ts,))
            conn.execute("""
                INSERT INTO microstructure_events
                  (schema_version, ts_ms, alias, event_type, price, side, size)
                VALUES (1, ?, 'NQM6', 'STOP_SWEEP', 100, 'buy', 5)
            """, (ts,))
            conn.execute("""
                INSERT INTO trigger_events
                  (schema_version, ts_ms, alias, kind, severity, snapshot_ts_ms)
                VALUES (1, ?, 'NQM6', 'TREND_SIGNAL_FIRE', 'HIGH', ?)
            """, (ts, ts))
            conn.execute("""
                INSERT INTO ai_turns
                  (schema_version, ts_ms, chat_run_id, deep, model,
                   user_text_raw, user_text_normalized, pax_text,
                   snapshot_alias, snapshot_sha256, digest_sha256, aborted)
                VALUES (1, ?, 'r1', 0, 'claude-haiku-4-5',
                        'x', 'x', '', 'NQM6', ?, ?, 0)
            """, (ts, 's' * 64, 'd' * 64))
            # Seed a trade_outcomes row referencing the ai_turn at this age
            # to verify it is NEVER deleted.
            ai_id = conn.execute("SELECT id FROM ai_turns ORDER BY id DESC LIMIT 1").fetchone()[0]
            conn.execute("""
                INSERT INTO trade_outcomes
                  (schema_version, ai_turn_id, alias, verdict, labeled_at_ms)
                VALUES (1, ?, 'NQM6', 'INFO', ?)
            """, (ai_id, ts))
    # Seed blob date-partition dirs for ages_days.
    import datetime as _dt
    for age_days in ages_days:
        ts = now_ms - age_days * 86_400_000
        date = _dt.datetime.fromtimestamp(ts / 1000.0,
                                            _dt.timezone.utc).strftime("%Y-%m-%d")
        (snap_dir / date).mkdir(parents=True, exist_ok=True)
        (snap_dir / date / f"{('s' * 64)}.json").write_text("{}", encoding="utf-8")
        (dig_dir / date).mkdir(parents=True, exist_ok=True)
        (dig_dir / date / f"{('d' * 64)}.txt").write_text("OMITTED", encoding="utf-8")
    return db_path, snap_dir, dig_dir, now_ms


def _run(args, db_path, snap_dir, dig_dir, report_path):
    """Invoke prune. Returns exit code; report is at report_path."""
    if _HAVE_DIRECT_IMPORT:
        return prune_main(args + ["--db", str(db_path), "--snap", str(snap_dir),
                                     "--dig", str(dig_dir), "--report", str(report_path)])
    env_extra = {
        "PAX_AI_BUS_DB":     str(db_path),
        "PAX_AI_SNAP_DIR":   str(snap_dir),
        "PAX_AI_DIGEST_DIR": str(dig_dir),
        "PAX_AI_REPORT":     str(report_path),
    }
    return _invoke_cli(args, env_extra=env_extra).returncode


def _count_all(db_path):
    out = {}
    with sqlite3.connect(db_path) as conn:
        for t in ("snapshot_features","level_events","microstructure_events",
                   "trigger_events","ai_turns","trade_outcomes"):
            try:
                out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.OperationalError:
                out[t] = -1
    return out


# -- argument validation -----------------------------------------------------

def test_prune_refuses_days_zero(tmp_path):
    db, snap, dig, _ = _seed_bus(tmp_path, ages_days=[0])
    rc = _run(["--days", "0"], db, snap, dig, tmp_path / "r.md")
    assert rc == 2
    counts = _count_all(db)
    assert counts["snapshot_features"] == 1


def test_prune_refuses_negative_days(tmp_path):
    db, snap, dig, _ = _seed_bus(tmp_path, ages_days=[0])
    rc = _run(["--days", "-3"], db, snap, dig, tmp_path / "r.md")
    assert rc == 2


# -- dry-run default ---------------------------------------------------------

def test_prune_dry_run_no_deletes(tmp_path):
    """Default mode = dry-run. NO rows deleted. Report shows would-delete counts."""
    db, snap, dig, _ = _seed_bus(tmp_path, ages_days=[0, 60])
    rc = _run(["--days", "30"], db, snap, dig, tmp_path / "r.md")
    assert rc == 0
    counts = _count_all(db)
    assert counts["snapshot_features"] == 2  # nothing deleted
    assert counts["ai_turns"] == 2
    report = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "dry-run" in report.lower() or "dry run" in report.lower()


def test_prune_dry_run_keeps_blob_dirs(tmp_path):
    db, snap, dig, _ = _seed_bus(tmp_path, ages_days=[60])
    rc = _run(["--days", "30"], db, snap, dig, tmp_path / "r.md")
    assert rc == 0
    # Blob date dirs still present.
    assert any(snap.iterdir())
    assert any(dig.iterdir())


# -- --yes does the real work ------------------------------------------------

def test_prune_yes_deletes_old_rows(tmp_path):
    db, snap, dig, _ = _seed_bus(tmp_path, ages_days=[0, 60])
    before = _count_all(db)
    assert before["snapshot_features"] == 2
    rc = _run(["--days", "30", "--yes"], db, snap, dig, tmp_path / "r.md")
    assert rc == 0
    after = _count_all(db)
    # One old (age 60) deleted, one recent (age 0) kept.
    assert after["snapshot_features"]     == 1
    assert after["level_events"]          == 1
    assert after["microstructure_events"] == 1
    assert after["trigger_events"]        == 1
    assert after["ai_turns"]              == 1


def test_prune_yes_keeps_trade_outcomes(tmp_path):
    """Orphan tolerance: trade_outcomes is never deleted even when the parent
    ai_turn row is gone."""
    db, snap, dig, _ = _seed_bus(tmp_path, ages_days=[60])
    before = _count_all(db)
    assert before["trade_outcomes"] == 1
    rc = _run(["--days", "30", "--yes"], db, snap, dig, tmp_path / "r.md")
    assert rc == 0
    after = _count_all(db)
    assert after["ai_turns"]       == 0  # parent gone
    assert after["trade_outcomes"] == 1  # outcome preserved


def test_prune_yes_keeps_rows_within_cutoff(tmp_path):
    db, snap, dig, _ = _seed_bus(tmp_path, ages_days=[5])
    rc = _run(["--days", "30", "--yes"], db, snap, dig, tmp_path / "r.md")
    assert rc == 0
    after = _count_all(db)
    assert after["snapshot_features"] == 1  # 5 days old, within 30-day window


def test_prune_yes_deletes_old_blob_partitions(tmp_path):
    """Blob date partitions older than cutoff are deleted; recent ones are kept."""
    db, snap, dig, _ = _seed_bus(tmp_path, ages_days=[0, 60])
    rc = _run(["--days", "30", "--yes"], db, snap, dig, tmp_path / "r.md")
    assert rc == 0
    # Today partition kept; 60-day-old partition gone.
    import datetime as _dt
    today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    old   = (_dt.datetime.now(_dt.timezone.utc)
              - _dt.timedelta(days=60)).strftime("%Y-%m-%d")
    assert (snap / today).exists()
    assert not (snap / old).exists()
    assert (dig / today).exists()
    assert not (dig / old).exists()


def test_prune_yes_idempotent(tmp_path):
    """Re-running prune on the same DB is a no-op."""
    db, snap, dig, _ = _seed_bus(tmp_path, ages_days=[60])
    rc = _run(["--days", "30", "--yes"], db, snap, dig, tmp_path / "r1.md")
    after1 = _count_all(db)
    rc2 = _run(["--days", "30", "--yes"], db, snap, dig, tmp_path / "r2.md")
    after2 = _count_all(db)
    assert rc == 0 and rc2 == 0
    assert after1 == after2


# -- VACUUM only on live ----------------------------------------------------

def test_prune_dry_run_skips_vacuum(tmp_path):
    """Dry-run must NOT VACUUM. Report should say skipped."""
    db, snap, dig, _ = _seed_bus(tmp_path, ages_days=[60])
    rc = _run(["--days", "30"], db, snap, dig, tmp_path / "r.md")
    assert rc == 0
    report = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "vacuum" in report.lower()
    assert "skipped" in report.lower() or "dry-run" in report.lower()


# -- default days from config ------------------------------------------------

def test_prune_default_days_from_config(tmp_path, monkeypatch):
    """Without --days, the cutoff is computed from feature_bus.retention_days."""
    from pax_ai import config as cfg_mod
    db, snap, dig, _ = _seed_bus(tmp_path, ages_days=[0])
    # Override retention_days = 7 for this test via _CACHE.
    monkeypatch.setattr(cfg_mod, "_reload_if_stale", lambda: None)
    monkeypatch.setattr(cfg_mod, "_CACHE", {**cfg_mod._CACHE,
        "feature_bus": {"enabled": True, "db_path": str(db),
            "snapshot_blob_dir": str(snap), "digest_blob_dir": str(dig),
            "queue_max": 2000, "writer_idle_ms": 100, "capture_ms": 1000,
            "retention_days": 7}})
    rc = _run([], db, snap, dig, tmp_path / "r.md")
    assert rc == 0
    report = (tmp_path / "r.md").read_text(encoding="utf-8")
    # Default-days line should mention 7.
    assert "7" in report


# -- import hygiene ---------------------------------------------------------

def test_prune_module_no_off_limits_imports():
    """AST scan: pax_bus_prune.py must not import from off-limits modules."""
    import ast
    src = (_MCP_SERVER / "bookmap_mcp" / "pax_bus_prune.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden = {"pax_ai.prompts", "pax_ai.claude_stream", "pax_ai.chat",
                  "pax_ai.triggers", "pax_ai.journal", "pax_ai.edge_calculus",
                  "pax_ai.voice"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert mod not in forbidden, f"forbidden import: {mod}"
            for n in node.names:
                assert f"{mod}.{n.name}" not in forbidden
        elif isinstance(node, ast.Import):
            for n in node.names:
                assert n.name not in forbidden, f"forbidden import: {n.name}"
