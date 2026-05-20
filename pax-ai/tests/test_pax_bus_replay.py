"""Phase 4A pax_bus_replay CLI tests.

The CLI lives in mcp-server/bookmap_mcp/pax_bus_replay.py. We test it
either via direct import (`from bookmap_mcp.pax_bus_replay import main`)
when reachable, or via subprocess invoking `python -m bookmap_mcp.pax_bus_replay`.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import sqlite3
from pathlib import Path

import pytest

# Try the direct import first; fall back to subprocess.
try:
    from bookmap_mcp.pax_bus_replay import main as replay_main
    _HAVE_DIRECT_IMPORT = True
except ImportError:
    _HAVE_DIRECT_IMPORT = False
    replay_main = None


# Find the repo root so we can invoke `python -m bookmap_mcp.pax_bus_replay`
# with the right PYTHONPATH if needed.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MCP_SERVER = _REPO_ROOT / "mcp-server"
_PAX_AI     = _REPO_ROOT / "pax-ai"


def _invoke_cli(args: list, env_extra: dict = None) -> subprocess.CompletedProcess:
    """Run the CLI as a subprocess. Falls back to PYTHONPATH if needed."""
    env = os.environ.copy()
    env.update(env_extra or {})
    # Ensure both packages are importable in the subprocess.
    py_path_parts = [str(_MCP_SERVER), str(_PAX_AI)]
    if env.get("PYTHONPATH"):
        py_path_parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(py_path_parts)
    cmd = [sys.executable, "-m", "bookmap_mcp.pax_bus_replay", *args]
    return subprocess.run(cmd, capture_output=True, text=True, env=env,
                           timeout=30)


def _seed_bus(tmp_path, ai_ts_ms, snap_dict, user_text="ping",
                alias="NQM6", router_hint="ROUTER: pax-or"):
    """Create the bus DB schema, write the snapshot blob, write the digest
    blob (using bus_digest.render_user_message), and insert one ai_turns row.
    Returns (db_path, snapshot_dir, digest_dir, snapshot_sha, digest_sha)."""
    from pax_ai import feature_bus, bus_digest
    db_path = tmp_path / "pax-bus.db"
    snap_dir = tmp_path / "snapshots"
    dig_dir  = tmp_path / "digests"
    # Use UTC date partition for blob paths to match feature_bus._date_partition.
    import datetime as _dt
    date_part = _dt.datetime.utcfromtimestamp(ai_ts_ms / 1000.0).strftime("%Y-%m-%d")
    snap_dir_dated = snap_dir / date_part
    dig_dir_dated  = dig_dir / date_part
    snap_dir_dated.mkdir(parents=True, exist_ok=True)
    dig_dir_dated.mkdir(parents=True, exist_ok=True)

    # Build the same bus digest the CLI will rebuild.
    digest_text = bus_digest.render_user_message(
        snap=snap_dict, user_text=user_text,
        router_hint=router_hint, alias=alias, ts_ms=ai_ts_ms)

    # Canonical JSON for snapshot.
    snap_json = feature_bus._canonical_snapshot_json(snap_dict)
    snap_sha = hashlib.sha256(snap_json.encode("utf-8")).hexdigest()
    digest_sha = hashlib.sha256(digest_text.encode("utf-8")).hexdigest()

    (snap_dir_dated / f"{snap_sha}.json").write_text(snap_json, encoding="utf-8")
    (dig_dir_dated  / f"{digest_sha}.txt").write_text(digest_text, encoding="utf-8")

    with feature_bus._open_db(db_path) as conn:
        feature_bus._ensure_schema(conn)
        conn.execute("""
            INSERT INTO ai_turns
              (schema_version, ts_ms, chat_run_id, deep, model,
               router_primary, router_secondary,
               user_text_raw, user_text_normalized, pax_text,
               snapshot_alias, snapshot_sha256, digest_sha256, aborted)
            VALUES (1, ?, 'r1', 0, 'claude-haiku-4-5',
                    'pax-or', NULL,
                    ?, ?, 'OMITTED',
                    ?, ?, ?, 0)
        """, (ai_ts_ms, user_text, user_text, alias, snap_sha, digest_sha))

    return db_path, snap_dir, dig_dir, snap_sha, digest_sha


def _enable_bus_pointing_at(tmp_path, db_path, snap_dir, dig_dir, monkeypatch):
    """Override pax_ai.config so feature_bus + bus_digest use these paths."""
    from pax_ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_reload_if_stale", lambda: None)
    monkeypatch.setattr(cfg_mod, "_CACHE", {
        **cfg_mod._CACHE,
        "feature_bus": {
            "enabled": True, "db_path": str(db_path),
            "snapshot_blob_dir": str(snap_dir),
            "digest_blob_dir":   str(dig_dir),
            "queue_max": 2000, "writer_idle_ms": 20, "capture_ms": 20,
            "retention_days": 30,
        },
    })


SNAP_FIXTURE = {
    "alias": "NQM6",
    "health": "ok",
    "book": {"mid": 23450.5, "spread": 0.25},
    "or_levels": {"orHigh": 23475.0, "orLow": 23440.0, "orWidthPts": 35.0,
                   "levels": [], "middleLock": False, "inProximity": False},
    "flow": {"regime": "TRENDING_UP", "regimeConfidence": 0.7},
    "conviction": {"score": 0.55, "trend": "BULL", "anchorMode": "LIVE"},
    "trend_signal": {"kind": "STRONG_BULL", "eligible": True},
    "gates": {"session": {"code": "ACTIVE", "anchorMode": "LIVE"},
                "news": {"blocked": False, "label": "clear"}},
    "micro_events": {"events": []},
    "position": {"position": 0, "entryPrice": 0, "pnl": 0},
    "pax": {"decision": "WAIT", "size_tier": "NONE"},
}

# 2026-01-15 12:00:00 UTC = 1768478400000 ms
_UTC_2026_01_15_NOON = 1768478400000


# -- happy path --------------------------------------------------------------

def test_replay_happy_path_matches(tmp_path, monkeypatch):
    """Seeded ai_turn + matching blob -> replay rebuilds digest, SHA equals
    stored ai_turns.digest_sha256, exit code 0."""
    db_path, snap_dir, dig_dir, snap_sha, digest_sha = _seed_bus(
        tmp_path, _UTC_2026_01_15_NOON, SNAP_FIXTURE)
    _enable_bus_pointing_at(tmp_path, db_path, snap_dir, dig_dir, monkeypatch)

    if _HAVE_DIRECT_IMPORT:
        rc = replay_main(["--date", "2026-01-15",
                           "--report", str(tmp_path / "report.md")])
        assert rc == 0
    else:
        env_extra = {
            "PAX_AI_BUS_DB":     str(db_path),
            "PAX_AI_SNAP_DIR":   str(snap_dir),
            "PAX_AI_DIGEST_DIR": str(dig_dir),
            "PAX_AI_REPORT":     str(tmp_path / "report.md"),
        }
        result = _invoke_cli(["--date", "2026-01-15"], env_extra=env_extra)
        assert result.returncode == 0, f"stderr={result.stderr}"

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "1 row" in report.lower() or "match" in report.lower()


# -- tampered blob -> mismatch reported, non-zero exit ----------------------

def test_replay_tampered_blob_reports_mismatch(tmp_path, monkeypatch):
    db_path, snap_dir, dig_dir, snap_sha, digest_sha = _seed_bus(
        tmp_path, _UTC_2026_01_15_NOON, SNAP_FIXTURE)
    # Corrupt the snapshot blob to force a SHA mismatch on rebuild.
    snap_files = list((snap_dir / "2026-01-15").glob("*.json"))
    assert snap_files
    snap_files[0].write_text('{"alias":"DIFFERENT"}', encoding="utf-8")

    _enable_bus_pointing_at(tmp_path, db_path, snap_dir, dig_dir, monkeypatch)
    if _HAVE_DIRECT_IMPORT:
        rc = replay_main(["--date", "2026-01-15",
                           "--report", str(tmp_path / "report.md")])
    else:
        env_extra = {
            "PAX_AI_BUS_DB":     str(db_path),
            "PAX_AI_SNAP_DIR":   str(snap_dir),
            "PAX_AI_DIGEST_DIR": str(dig_dir),
            "PAX_AI_REPORT":     str(tmp_path / "report.md"),
        }
        result = _invoke_cli(["--date", "2026-01-15"], env_extra=env_extra)
        rc = result.returncode
    assert rc != 0, "tampered blob must produce non-zero exit code"
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "mismatch" in report.lower()


# -- missing blob -> reported, not a crash ---------------------------------

def test_replay_missing_blob_reported(tmp_path, monkeypatch):
    db_path, snap_dir, dig_dir, snap_sha, digest_sha = _seed_bus(
        tmp_path, _UTC_2026_01_15_NOON, SNAP_FIXTURE)
    # Delete the snapshot blob so the replay can't rebuild it.
    for fp in (snap_dir / "2026-01-15").glob("*.json"):
        fp.unlink()

    _enable_bus_pointing_at(tmp_path, db_path, snap_dir, dig_dir, monkeypatch)
    if _HAVE_DIRECT_IMPORT:
        rc = replay_main(["--date", "2026-01-15",
                           "--report", str(tmp_path / "report.md")])
    else:
        env_extra = {
            "PAX_AI_BUS_DB":     str(db_path),
            "PAX_AI_SNAP_DIR":   str(snap_dir),
            "PAX_AI_DIGEST_DIR": str(dig_dir),
            "PAX_AI_REPORT":     str(tmp_path / "report.md"),
        }
        result = _invoke_cli(["--date", "2026-01-15"], env_extra=env_extra)
        rc = result.returncode
    # Non-zero exit because at least one row could not be verified.
    assert rc != 0
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "missing" in report.lower() or "blob" in report.lower()


# -- multi-turn correctness: session_memory_before_ts_ms ------------------

def test_replay_multi_turn_matches_with_before_ts_ms_filter(tmp_path, monkeypatch):
    """Multi-turn correctness: when N>1 ai_turn rows exist, replay rebuilds
    each digest using session_memory_before_ts_ms=ai_turn.ts_ms so that the
    SESSION_MEMORY block matches what capture-time chat.py saw. Without the
    filter, this test would fail with false SHA mismatches for turn 2."""
    from pax_ai import feature_bus, bus_digest
    db_path = tmp_path / "pax-bus.db"
    snap_dir = tmp_path / "snapshots"
    dig_dir  = tmp_path / "digests"
    date_part = "2026-01-15"
    snap_dir_dated = snap_dir / date_part
    dig_dir_dated  = dig_dir / date_part
    snap_dir_dated.mkdir(parents=True, exist_ok=True)
    dig_dir_dated.mkdir(parents=True, exist_ok=True)

    # Two ai_turns minutes apart. Both share the same snapshot for simplicity.
    ts1 = _UTC_2026_01_15_NOON
    ts2 = _UTC_2026_01_15_NOON + 60_000  # one minute later

    snap_json = feature_bus._canonical_snapshot_json(SNAP_FIXTURE)
    snap_sha = hashlib.sha256(snap_json.encode("utf-8")).hexdigest()
    (snap_dir_dated / f"{snap_sha}.json").write_text(snap_json, encoding="utf-8")

    # Set the config pointing at our tmp paths BEFORE rendering digests.
    _enable_bus_pointing_at(tmp_path, db_path, snap_dir, dig_dir, monkeypatch)

    # Create schema first so the digest renderer can see (initially empty) ai_turns.
    with feature_bus._open_db(db_path) as conn:
        feature_bus._ensure_schema(conn)

    # Turn 1 at ts1: SESSION_MEMORY is empty (no prior turns).
    digest_1 = bus_digest.render_user_message(
        snap=SNAP_FIXTURE, user_text="first",
        router_hint="ROUTER: pax-or", alias="NQM6", ts_ms=ts1)
    digest_1_sha = hashlib.sha256(digest_1.encode("utf-8")).hexdigest()
    (dig_dir_dated / f"{digest_1_sha}.txt").write_text(digest_1, encoding="utf-8")

    # Insert turn-1 row.
    with feature_bus._open_db(db_path) as conn:
        conn.execute("""
            INSERT INTO ai_turns
              (schema_version, ts_ms, chat_run_id, deep, model,
               router_primary, router_secondary,
               user_text_raw, user_text_normalized, pax_text,
               snapshot_alias, snapshot_sha256, digest_sha256, aborted)
            VALUES (1, ?, 'r1', 0, 'claude-haiku-4-5',
                    'pax-or', NULL,
                    ?, ?, 'OMITTED', 'NQM6', ?, ?, 0)
        """, (ts1, "first", "first", snap_sha, digest_1_sha))

    # Turn 2 at ts2: SESSION_MEMORY sees turn 1 because it's in the DB.
    digest_2 = bus_digest.render_user_message(
        snap=SNAP_FIXTURE, user_text="second",
        router_hint="ROUTER: pax-or", alias="NQM6", ts_ms=ts2)
    digest_2_sha = hashlib.sha256(digest_2.encode("utf-8")).hexdigest()
    (dig_dir_dated / f"{digest_2_sha}.txt").write_text(digest_2, encoding="utf-8")

    # Insert turn-2 row.
    with feature_bus._open_db(db_path) as conn:
        conn.execute("""
            INSERT INTO ai_turns
              (schema_version, ts_ms, chat_run_id, deep, model,
               router_primary, router_secondary,
               user_text_raw, user_text_normalized, pax_text,
               snapshot_alias, snapshot_sha256, digest_sha256, aborted)
            VALUES (1, ?, 'r1', 0, 'claude-haiku-4-5',
                    'pax-or', NULL,
                    ?, ?, 'OMITTED', 'NQM6', ?, ?, 0)
        """, (ts2, "second", "second", snap_sha, digest_2_sha))

    # Now replay both: both rebuilds must SHA-match their stored digests.
    if _HAVE_DIRECT_IMPORT:
        rc = replay_main(["--date", "2026-01-15",
                           "--report", str(tmp_path / "report.md")])
    else:
        env_extra = {
            "PAX_AI_BUS_DB":     str(db_path),
            "PAX_AI_SNAP_DIR":   str(snap_dir),
            "PAX_AI_DIGEST_DIR": str(dig_dir),
            "PAX_AI_REPORT":     str(tmp_path / "report.md"),
        }
        result = _invoke_cli(["--date", "2026-01-15"], env_extra=env_extra)
        rc = result.returncode
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert rc == 0, f"multi-turn replay should match. report:\n{report}"
    # Sanity: both turns counted.
    assert "2" in report


# -- date filter excludes other UTC days -----------------------------------

def test_replay_date_filter_excludes_other_days(tmp_path, monkeypatch):
    db_path, snap_dir, dig_dir, snap_sha, digest_sha = _seed_bus(
        tmp_path, _UTC_2026_01_15_NOON, SNAP_FIXTURE)
    _enable_bus_pointing_at(tmp_path, db_path, snap_dir, dig_dir, monkeypatch)
    if _HAVE_DIRECT_IMPORT:
        rc = replay_main(["--date", "2026-01-14",
                           "--report", str(tmp_path / "report.md")])
    else:
        env_extra = {
            "PAX_AI_BUS_DB":     str(db_path),
            "PAX_AI_SNAP_DIR":   str(snap_dir),
            "PAX_AI_DIGEST_DIR": str(dig_dir),
            "PAX_AI_REPORT":     str(tmp_path / "report.md"),
        }
        result = _invoke_cli(["--date", "2026-01-14"], env_extra=env_extra)
        rc = result.returncode
    # Zero ai_turns matched -> exit 0 with "0 rows" report.
    assert rc == 0
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "0 row" in report.lower() or "no rows" in report.lower()
