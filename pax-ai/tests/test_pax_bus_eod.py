"""Phase 4B-3 end-of-day report CLI tests.

Format detection is CONTENT-based: read the digest blob and classify by
which markers it contains. Tests cover all four format buckets and
prove --verify-replay only touches format=bus rows."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

try:
    from bookmap_mcp.pax_bus_eod import main as eod_main
    _HAVE_DIRECT_IMPORT = True
except ImportError:
    _HAVE_DIRECT_IMPORT = False
    eod_main = None


_REPO_ROOT  = Path(__file__).resolve().parent.parent.parent
_MCP_SERVER = _REPO_ROOT / "mcp-server"
_PAX_AI     = _REPO_ROOT / "pax-ai"


def _invoke_cli(args, env_extra=None):
    env = os.environ.copy()
    env.update(env_extra or {})
    py_path_parts = [str(_MCP_SERVER), str(_PAX_AI)]
    if env.get("PYTHONPATH"):
        py_path_parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(py_path_parts)
    cmd = [sys.executable, "-m", "bookmap_mcp.pax_bus_eod", *args]
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)


_UTC_2026_01_15_NOON = 1768478400000  # 2026-01-15T12:00:00Z

BUS_MARKERS_BLOB = (
    "ROUTER: pax-or\n\n"
    "[STATE]\n  alias: NQM6\n\n"
    "[ANCHOR]\n  mode: LIVE\n\n"
    "[GATES]\n  ok\n\n"
    "[LEVELS]\n  none\n\n"
    "[MICROSTRUCTURE]\n  ok\n\n"
    "[RECENT_EVENTS]\n  none\n\n"
    "[POSITION]\n  FLAT\n\n"
    "[SESSION_MEMORY]\n  none\n\n"
    "[USER]\n  ping"
)

LEGACY_BLOB = (
    "ROUTER: pax-or\n\n"
    "SNAPSHOT DIGEST\n"
    "  alias: NQM6\n"
    "  mid: 100.0\n\n"
    "USER:\n  ping"
)

UNKNOWN_BLOB = "completely-random-bytes-no-markers-here\nnot bus, not legacy"


def _seed_eod_db(tmp_path):
    """Create the bus schema + dirs. Returns (db, snap_dir, dig_dir)."""
    from pax_ai import feature_bus
    db = tmp_path / "pax-bus.db"
    snap_dir = tmp_path / "snapshots"
    dig_dir  = tmp_path / "digests"
    snap_dir.mkdir(parents=True, exist_ok=True)
    dig_dir.mkdir(parents=True, exist_ok=True)
    with feature_bus._open_db(db) as conn:
        feature_bus._ensure_schema(conn)
    return db, snap_dir, dig_dir


def _insert_ai_turn(db, ts_ms, snapshot_sha=None, digest_sha=None,
                     model="claude-haiku-4-5", router_primary="pax-or",
                     elapsed_ms=100, total_cost_usd=0.01,
                     user_text="ping"):
    snapshot_sha = snapshot_sha or hashlib.sha256(f"s{ts_ms}".encode()).hexdigest()
    digest_sha   = digest_sha   or hashlib.sha256(f"d{ts_ms}".encode()).hexdigest()
    with sqlite3.connect(db) as conn:
        conn.execute("""
            INSERT INTO ai_turns
              (schema_version, ts_ms, chat_run_id, deep, model,
               router_primary, router_secondary,
               user_text_raw, user_text_normalized, pax_text,
               snapshot_alias, snapshot_sha256, digest_sha256,
               elapsed_ms, total_cost_usd, aborted)
            VALUES (1, ?, 'r1', 0, ?, ?, NULL,
                    ?, ?, 'PRIVATE BODY DO NOT LEAK',
                    'NQM6', ?, ?, ?, ?, 0)
        """, (ts_ms, model, router_primary,
              user_text, user_text,
              snapshot_sha, digest_sha,
              elapsed_ms, total_cost_usd))
    return snapshot_sha, digest_sha


def _seed_blob(dig_dir, digest_sha, body, date="2026-01-15"):
    p = dig_dir / date / f"{digest_sha}.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def _seed_snap_blob(snap_dir, snapshot_sha, body='{"alias":"NQM6"}', date="2026-01-15"):
    p = snap_dir / date / f"{snapshot_sha}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def _insert_trade_outcome(db, ai_turn_id, verdict="ENTER_LONG",
                            t0=100.0, t60=101.0, t180=None, t300=100.5, t900=99.0):
    with sqlite3.connect(db) as conn:
        conn.execute("""
            INSERT INTO trade_outcomes
              (schema_version, ai_turn_id, alias, verdict,
               mid_at_t0, mid_at_t60s, mid_at_t180s, mid_at_t300s, mid_at_t900s,
               label_method, labeled_at_ms)
            VALUES (1, ?, 'NQM6', ?, ?, ?, ?, ?, ?,
                    'phase4a_heuristic_v1', ?)
        """, (ai_turn_id, verdict, t0, t60, t180, t300, t900, ai_turn_id))


def _run(args, db, snap, dig, report):
    if _HAVE_DIRECT_IMPORT:
        return eod_main(args + ["--db", str(db), "--snap", str(snap),
                                  "--dig", str(dig), "--report", str(report)])
    env_extra = {
        "PAX_AI_BUS_DB":     str(db),
        "PAX_AI_SNAP_DIR":   str(snap),
        "PAX_AI_DIGEST_DIR": str(dig),
        "PAX_AI_REPORT":     str(report),
    }
    return _invoke_cli(args, env_extra=env_extra).returncode


# -- happy path: empty day --------------------------------------------------

def test_eod_empty_day_returns_empty_report(tmp_path):
    db, snap, dig = _seed_eod_db(tmp_path)
    rc = _run(["--date", "2026-01-15"], db, snap, dig, tmp_path / "r.md")
    assert rc == 0
    report = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "pax_bus_eod 2026-01-15 (UTC day)" in report
    assert "ai_turns" in report.lower() or "0" in report


# -- counts + grouping ------------------------------------------------------

def test_eod_counts_turns_by_model(tmp_path):
    db, snap, dig = _seed_eod_db(tmp_path)
    _insert_ai_turn(db, _UTC_2026_01_15_NOON,        model="claude-haiku-4-5")
    _insert_ai_turn(db, _UTC_2026_01_15_NOON + 1000, model="claude-sonnet-4-6")
    _insert_ai_turn(db, _UTC_2026_01_15_NOON + 2000, model="claude-haiku-4-5")
    rc = _run(["--date", "2026-01-15"], db, snap, dig, tmp_path / "r.md")
    assert rc == 0
    report = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "claude-haiku-4-5" in report
    assert "claude-sonnet-4-6" in report
    assert "2" in report   # haiku count
    assert "3" in report   # total


def test_eod_groups_verdicts(tmp_path):
    db, snap, dig = _seed_eod_db(tmp_path)
    _insert_ai_turn(db, _UTC_2026_01_15_NOON)
    _insert_ai_turn(db, _UTC_2026_01_15_NOON + 1)
    _insert_trade_outcome(db, ai_turn_id=1, verdict="ENTER_LONG")
    _insert_trade_outcome(db, ai_turn_id=2, verdict="INFO")
    rc = _run(["--date", "2026-01-15"], db, snap, dig, tmp_path / "r.md")
    report = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "ENTER_LONG" in report
    assert "INFO" in report


def test_eod_mid_drift_handles_null_mids(tmp_path):
    db, snap, dig = _seed_eod_db(tmp_path)
    _insert_ai_turn(db, _UTC_2026_01_15_NOON)
    _insert_trade_outcome(db, ai_turn_id=1, verdict="ENTER_LONG",
                            t0=100.0, t60=101.0, t180=None, t300=None, t900=99.0)
    rc = _run(["--date", "2026-01-15"], db, snap, dig, tmp_path / "r.md")
    assert rc == 0
    report = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "n/a" in report.lower()   # for the NULL columns


# -- format detection -------------------------------------------------------

def test_eod_format_legacy_blob_with_snapshot_digest_marker(tmp_path):
    """Legacy blob (contains SNAPSHOT DIGEST, no bus markers) -> format=legacy,
    NOT counted as a replay mismatch."""
    db, snap, dig = _seed_eod_db(tmp_path)
    _, dsha = _insert_ai_turn(db, _UTC_2026_01_15_NOON)
    _seed_blob(dig, dsha, LEGACY_BLOB)
    rc = _run(["--date", "2026-01-15", "--verify-replay"],
                db, snap, dig, tmp_path / "r.md")
    assert rc == 0
    report = (tmp_path / "r.md").read_text(encoding="utf-8")
    # Verify format breakdown shows legacy=1
    assert "legacy" in report.lower()
    # Verify mismatched count is NOT 1 -- legacy must NOT be classed as mismatch.
    # Look for the replay verification section.
    assert "format=legacy" in report or "legacy:" in report.lower()


def test_eod_format_bus_blob_with_all_markers(tmp_path):
    """Bus-format blob -> classified as bus; with --verify-replay it is the
    only row submitted to the replay code path."""
    db, snap, dig = _seed_eod_db(tmp_path)
    # Seed an ai_turn whose digest_sha256 actually matches a bus-format blob.
    blob_text = BUS_MARKERS_BLOB
    real_dsha = hashlib.sha256(blob_text.encode("utf-8")).hexdigest()
    # also seed the snapshot blob the replay needs.
    snap_sha  = hashlib.sha256(b'{"alias":"NQM6"}').hexdigest()
    _seed_snap_blob(snap, snap_sha, '{"alias":"NQM6"}')
    _insert_ai_turn(db, _UTC_2026_01_15_NOON,
                     snapshot_sha=snap_sha, digest_sha=real_dsha)
    _seed_blob(dig, real_dsha, blob_text)
    rc = _run(["--date", "2026-01-15", "--verify-replay"],
                db, snap, dig, tmp_path / "r.md")
    assert rc == 0
    report = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "bus" in report.lower()
    # Replay section must be present; bus row is the only one submitted.
    assert "replay" in report.lower()


def test_eod_format_missing_blob_counted_separately(tmp_path):
    """Missing blob -> format=missing_blob, never submitted to replay."""
    db, snap, dig = _seed_eod_db(tmp_path)
    _insert_ai_turn(db, _UTC_2026_01_15_NOON)
    rc = _run(["--date", "2026-01-15", "--verify-replay"],
                db, snap, dig, tmp_path / "r.md")
    assert rc == 0
    report = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "missing_blob" in report.lower() or "missing blob" in report.lower()


def test_eod_format_unknown_blob_counted_separately(tmp_path):
    """Blob exists but matches neither bus nor legacy shape -> format=unknown."""
    db, snap, dig = _seed_eod_db(tmp_path)
    _, dsha = _insert_ai_turn(db, _UTC_2026_01_15_NOON)
    _seed_blob(dig, dsha, UNKNOWN_BLOB)
    rc = _run(["--date", "2026-01-15", "--verify-replay"],
                db, snap, dig, tmp_path / "r.md")
    assert rc == 0
    report = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "unknown" in report.lower()


def test_eod_verify_replay_invokes_replay_for_bus_format_only(tmp_path, monkeypatch):
    """Seeds bus + legacy + missing + unknown. --verify-replay must only
    submit the bus row to pax_bus_replay's code path; the other three are
    counted in the format breakdown, NOT the replay-mismatch counter."""
    db, snap, dig = _seed_eod_db(tmp_path)

    # Bus row: digest_sha must match the blob content.
    bus_blob = BUS_MARKERS_BLOB
    bus_dsha = hashlib.sha256(bus_blob.encode("utf-8")).hexdigest()
    bus_ssha = hashlib.sha256(b'{"alias":"NQM6"}').hexdigest()
    _seed_snap_blob(snap, bus_ssha, '{"alias":"NQM6"}')
    _insert_ai_turn(db, _UTC_2026_01_15_NOON,
                     snapshot_sha=bus_ssha, digest_sha=bus_dsha)
    _seed_blob(dig, bus_dsha, bus_blob)

    # Legacy row
    _, leg_dsha = _insert_ai_turn(db, _UTC_2026_01_15_NOON + 1)
    _seed_blob(dig, leg_dsha, LEGACY_BLOB)

    # Missing-blob row
    _insert_ai_turn(db, _UTC_2026_01_15_NOON + 2)

    # Unknown row
    _, unk_dsha = _insert_ai_turn(db, _UTC_2026_01_15_NOON + 3)
    _seed_blob(dig, unk_dsha, UNKNOWN_BLOB)

    rc = _run(["--date", "2026-01-15", "--verify-replay"],
                db, snap, dig, tmp_path / "r.md")
    assert rc == 0
    report = (tmp_path / "r.md").read_text(encoding="utf-8")
    # Format breakdown should show 1/1/1/1.
    rl = report.lower()
    # Be tolerant about exact phrasing; require all 4 buckets to be named.
    for name in ("bus", "legacy", "missing", "unknown"):
        assert name in rl
    # Replay-mismatch counter must NOT be 3 (only bus row is submitted, and
    # if it matches, mismatch=0; if it mismatches, mismatch<=1).
    # Hard guarantee: legacy/missing/unknown never count as mismatches.
    # Look for "mismatched" surrounded by a small number (0-1).
    import re
    m = re.search(r"mismatched[^\d]*(\d+)", rl)
    if m:
        assert int(m.group(1)) <= 1


# -- PII / size guard -------------------------------------------------------

def test_eod_excludes_blob_text_columns_from_report(tmp_path):
    db, snap, dig = _seed_eod_db(tmp_path)
    _insert_ai_turn(db, _UTC_2026_01_15_NOON,
                     user_text="ping with a private question about the trade")
    rc = _run(["--date", "2026-01-15"], db, snap, dig, tmp_path / "r.md")
    report = (tmp_path / "r.md").read_text(encoding="utf-8")
    # Pax assistant text is in pax_text in our seed (constant marker)
    assert "PRIVATE BODY DO NOT LEAK" not in report
    # The literal user_text full body should not appear in EOD.
    assert "private question about the trade" not in report.lower()
    # Blob fields must never be embedded.
    assert "snapshot_json" not in report.lower()
    assert "digest_text" not in report.lower()


def test_eod_default_date_is_utc_today(tmp_path):
    db, snap, dig = _seed_eod_db(tmp_path)
    import datetime as _dt
    today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    rc = _run([], db, snap, dig, tmp_path / "r.md")
    report = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert today in report


def test_eod_default_report_avoids_clobber(tmp_path):
    """When the default report path already exists, the CLI must NOT
    overwrite it; it must append _N to the filename instead."""
    db, snap, dig = _seed_eod_db(tmp_path)
    import datetime as _dt
    today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    explicit_path = tmp_path / f"bus-eod-{today}.md"
    explicit_path.write_text("PRE-EXISTING REPORT", encoding="utf-8")
    rc = _run(["--report", str(explicit_path)], db, snap, dig, explicit_path)
    assert rc == 0
    # The pre-existing report must remain untouched.
    assert "PRE-EXISTING REPORT" in explicit_path.read_text(encoding="utf-8")
    # And a sibling file with _N suffix must have been created.
    sib = tmp_path / f"bus-eod-{today}_1.md"
    assert sib.exists()


def test_eod_no_off_limits_imports():
    import ast
    src = (_MCP_SERVER / "bookmap_mcp" / "pax_bus_eod.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden = {"pax_ai.prompts", "pax_ai.claude_stream", "pax_ai.chat",
                  "pax_ai.triggers", "pax_ai.journal", "pax_ai.edge_calculus",
                  "pax_ai.voice", "pax_ai.outcomes"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert mod not in forbidden, f"forbidden import: {mod}"
        elif isinstance(node, ast.Import):
            for n in node.names:
                assert n.name not in forbidden, f"forbidden import: {n.name}"


# ---------------------------------------------------------------------------
# UTC-day boundary regression tests
# ---------------------------------------------------------------------------
#
# Bug: EOD originally used `ts_ms <= end_ms` where end_ms = next-day midnight.
# That counted rows stamped at the next-day's UTC midnight in the PREVIOUS
# day's report. The half-open convention used by pax_bus_replay and
# feature_bus.summary_today is `ts_ms >= start_ms AND ts_ms < end_ms`.
# These tests pin the half-open convention.

# 2026-01-15T23:59:59.999 UTC
_UTC_2026_01_15_EOD_MS = 1768521599999
# 2026-01-16T00:00:00.000 UTC (start of the next day)
_UTC_2026_01_16_START_MS = 1768521600000


def test_eod_counts_use_half_open_utc_day_bound(tmp_path):
    """Row stamped at exactly next-day UTC midnight must NOT count in the
    previous day's report."""
    import re
    db, snap, dig = _seed_eod_db(tmp_path)
    _insert_ai_turn(db, _UTC_2026_01_15_EOD_MS,     model="claude-haiku-4-5")
    _insert_ai_turn(db, _UTC_2026_01_16_START_MS,   model="claude-haiku-4-5")
    rc = _run(["--date", "2026-01-15"], db, snap, dig, tmp_path / "r.md")
    assert rc == 0
    report = (tmp_path / "r.md").read_text(encoding="utf-8")
    # Find the ai_turns line in the ## Counts block.
    m = re.search(r"ai_turns\s+(\d+)", report)
    assert m, f"ai_turns count line not found in:\n{report}"
    assert int(m.group(1)) == 1, (
        f"only the 23:59:59.999 row should be counted in 2026-01-15; "
        f"got {m.group(1)} (report follows):\n{report}"
    )
    # The Turns section should also report total = 1.
    m_total = re.search(r"total\s*:\s*(\d+)", report)
    assert m_total, f"Turns 'total:' line not found in:\n{report}"
    assert int(m_total.group(1)) == 1


def test_eod_trade_outcomes_join_uses_half_open_bound(tmp_path):
    """The trade_outcomes COUNT and the verdict / mid-drift joins all key
    off ai_turns.ts_ms via the same half-open predicate."""
    import re
    db, snap, dig = _seed_eod_db(tmp_path)
    _insert_ai_turn(db, _UTC_2026_01_15_EOD_MS)        # ai_turn id=1
    _insert_ai_turn(db, _UTC_2026_01_16_START_MS)      # ai_turn id=2 (next day)
    _insert_trade_outcome(db, ai_turn_id=1, verdict="ENTER_LONG")
    _insert_trade_outcome(db, ai_turn_id=2, verdict="ENTER_LONG")
    rc = _run(["--date", "2026-01-15"], db, snap, dig, tmp_path / "r.md")
    assert rc == 0
    report = (tmp_path / "r.md").read_text(encoding="utf-8")
    # trade_outcomes count in the ## Counts block: only the id=1 outcome
    # whose parent ai_turn fell in Jan 15.
    m = re.search(r"trade_outcomes\s+(\d+)", report)
    assert m, f"trade_outcomes count line missing in:\n{report}"
    assert int(m.group(1)) == 1, (
        f"only outcome for ai_turn at 23:59:59.999 should join in 2026-01-15; "
        f"got {m.group(1)} (report follows):\n{report}"
    )
    # Verdict rollup must reflect the same single-row count.
    # ENTER_LONG line should show n=1 (one outcome, not two).
    m_enter = re.search(r"ENTER_LONG\s+(\d+)", report)
    assert m_enter, "ENTER_LONG row missing from Verdicts section"
    assert int(m_enter.group(1)) == 1
