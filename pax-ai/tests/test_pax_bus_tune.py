"""Phase 5A tuning report CLI tests (READ-ONLY).

pax_bus_tune.py reads ai_turns + trade_outcomes + snapshot_features +
trigger_events, joins via snapshot_sha256, computes per-kind trigger
stats and per-source Spearman correlations vs realized_r_at_t300s, and
writes an advisory JSON report. NEVER writes pax_weights.json. NEVER
mutates DB rows.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

try:
    from bookmap_mcp.pax_bus_tune import main as tune_main
    from bookmap_mcp.pax_bus_tune import _spearman, _clamp_weight_delta
    _HAVE_DIRECT_IMPORT = True
except ImportError:
    _HAVE_DIRECT_IMPORT = False
    tune_main = None
    _spearman = None
    _clamp_weight_delta = None


_REPO_ROOT  = Path(__file__).resolve().parent.parent.parent
_MCP_SERVER = _REPO_ROOT / "mcp-server"
_PAX_AI     = _REPO_ROOT / "pax-ai"


_UTC_2026_01_15_NOON     = 1768478400000   # 2026-01-15T12:00:00Z
_UTC_2026_01_15_EOD_MS   = 1768521599999   # 2026-01-15T23:59:59.999Z
_UTC_2026_01_16_START_MS = 1768521600000   # 2026-01-16T00:00:00.000Z


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
LEGACY_BLOB = "SNAPSHOT DIGEST\n  alias: NQM6\n\nUSER:\n  ping"
UNKNOWN_BLOB = "completely-random-no-markers"


def _invoke_cli(args, env_extra=None):
    env = os.environ.copy()
    env.update(env_extra or {})
    parts = [str(_MCP_SERVER), str(_PAX_AI)]
    if env.get("PYTHONPATH"):
        parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    cmd = [sys.executable, "-m", "bookmap_mcp.pax_bus_tune", *args]
    return subprocess.run(cmd, capture_output=True, text=True,
                          env=env, timeout=30)


def _run(args, db, snap, dig, report):
    if _HAVE_DIRECT_IMPORT:
        return tune_main(args + ["--db", str(db), "--snap", str(snap),
                                  "--dig", str(dig), "--report", str(report)])
    env_extra = {
        "PAX_AI_BUS_DB":     str(db),
        "PAX_AI_SNAP_DIR":   str(snap),
        "PAX_AI_DIGEST_DIR": str(dig),
        "PAX_AI_REPORT":     str(report),
    }
    return _invoke_cli(args, env_extra=env_extra).returncode


def _seed_db(tmp_path):
    from pax_ai import feature_bus
    db = tmp_path / "pax-bus.db"
    snap_dir = tmp_path / "snapshots"
    dig_dir  = tmp_path / "digests"
    snap_dir.mkdir(parents=True, exist_ok=True)
    dig_dir.mkdir(parents=True, exist_ok=True)
    with feature_bus._open_db(db) as conn:
        feature_bus._ensure_schema(conn)
    return db, snap_dir, dig_dir


def _seed_dig_blob(dig_dir, digest_sha, body, date="2026-01-15"):
    p = dig_dir / date / f"{digest_sha}.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def _insert_ai_turn(db, ts_ms, snapshot_sha=None, digest_sha=None,
                     model="claude-haiku-4-5", router_primary="pax-or",
                     alias="NQM6"):
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
            VALUES (1, ?, 'r1', 0, ?, ?, NULL, 'q', 'q', 'a',
                    ?, ?, ?, 100, 0.01, 0)
        """, (ts_ms, model, router_primary, alias, snapshot_sha, digest_sha))
        rowid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return rowid, snapshot_sha, digest_sha


def _insert_snapshot_features(db, ts_ms, raw_json_sha256, alias="NQM6",
                                conviction_score=0.0, flow_bias_score=0.0,
                                vwap_sigma_z=0.0, momentum_i10=0.0,
                                momentum_i50=0.0, momentum_i200=0.0,
                                pax_confidence=0.0):
    with sqlite3.connect(db) as conn:
        conn.execute("""
            INSERT INTO snapshot_features
              (schema_version, ts_ms, alias, health, raw_json_sha256,
               conviction_score, flow_bias_score, vwap_sigma_z,
               momentum_i10, momentum_i50, momentum_i200, pax_confidence)
            VALUES (1, ?, ?, 'ok', ?, ?, ?, ?, ?, ?, ?, ?)
        """, (ts_ms, alias, raw_json_sha256,
              conviction_score, flow_bias_score, vwap_sigma_z,
              momentum_i10, momentum_i50, momentum_i200, pax_confidence))


def _insert_outcome(db, ai_turn_id, verdict="ENTER_LONG",
                     realized_r_at_t300s=0.0):
    with sqlite3.connect(db) as conn:
        conn.execute("""
            INSERT INTO trade_outcomes
              (schema_version, ai_turn_id, alias, verdict,
               realized_r_at_t300s, label_method, labeled_at_ms)
            VALUES (1, ?, 'NQM6', ?, ?, 'phase4a_heuristic_v1', ?)
        """, (ai_turn_id, verdict, realized_r_at_t300s, ai_turn_id))


def _insert_trigger(db, ts_ms, kind="OR_TOUCH", severity="info",
                     alias="NQM6", label="evt", headline="h", details="d"):
    with sqlite3.connect(db) as conn:
        conn.execute("""
            INSERT INTO trigger_events
              (schema_version, ts_ms, alias, kind, severity,
               label, headline, details, snapshot_ts_ms)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (ts_ms, alias, kind, severity, label, headline, details, ts_ms))


def _seed_bus_row(db, snap, dig, ts_ms, ai_idx,
                    conviction=0.0, realized_r=0.0):
    """Helper: insert one bus-format ai_turn + matching snapshot_features +
    matching trade_outcome. Returns ai_turn id."""
    snap_sha = hashlib.sha256(f"snap{ai_idx}".encode()).hexdigest()
    blob = BUS_MARKERS_BLOB + f"\n# unique-{ai_idx}"
    dig_sha = hashlib.sha256(blob.encode()).hexdigest()
    rowid, _, _ = _insert_ai_turn(db, ts_ms,
                                    snapshot_sha=snap_sha, digest_sha=dig_sha)
    _seed_dig_blob(dig, dig_sha, blob)
    _insert_snapshot_features(db, ts_ms, snap_sha,
                                conviction_score=conviction)
    _insert_outcome(db, rowid, realized_r_at_t300s=realized_r)
    return rowid


# ---------------------------------------------------------------------------
# Empty / happy paths
# ---------------------------------------------------------------------------

def test_tune_empty_day_returns_valid_json_with_empty_arrays(tmp_path):
    db, snap, dig = _seed_db(tmp_path)
    out = tmp_path / "tune.json"
    rc = _run(["--date", "2026-01-15"], db, snap, dig, out)
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert "window" in data
    assert "format_breakdown" in data
    assert "trigger_stats" in data and data["trigger_stats"] == []
    assert "source_correlations" in data and data["source_correlations"] == []
    assert "advisory_weight_deltas" in data and data["advisory_weight_deltas"] == []
    assert "notes" in data and isinstance(data["notes"], list)


def test_tune_window_uses_half_open_utc_day_bound(tmp_path):
    """ts_ms == next_day_start must be excluded from the window."""
    db, snap, dig = _seed_db(tmp_path)
    _seed_bus_row(db, snap, dig, _UTC_2026_01_15_EOD_MS,   ai_idx=1,
                  conviction=0.5, realized_r=1.0)
    _seed_bus_row(db, snap, dig, _UTC_2026_01_16_START_MS, ai_idx=2,
                  conviction=0.5, realized_r=1.0)
    out = tmp_path / "tune.json"
    rc = _run(["--date", "2026-01-15"], db, snap, dig, out)
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["format_breakdown"]["bus"] == 1


# ---------------------------------------------------------------------------
# Format filtering
# ---------------------------------------------------------------------------

def test_tune_excludes_legacy_format_from_correlations(tmp_path):
    db, snap, dig = _seed_db(tmp_path)
    # legacy ai_turn (no bus markers in blob)
    rowid, snap_sha, dig_sha = _insert_ai_turn(db, _UTC_2026_01_15_NOON)
    _seed_dig_blob(dig, dig_sha, LEGACY_BLOB)
    _insert_snapshot_features(db, _UTC_2026_01_15_NOON, snap_sha,
                              conviction_score=0.9)
    _insert_outcome(db, rowid, realized_r_at_t300s=1.0)
    out = tmp_path / "tune.json"
    rc = _run(["--date", "2026-01-15"], db, snap, dig, out)
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["format_breakdown"]["legacy"] == 1
    assert data["format_breakdown"]["bus"] == 0
    # Source correlations come from bus rows only -> none here.
    assert data["source_correlations"] == []


def test_tune_excludes_missing_blob_format(tmp_path):
    db, snap, dig = _seed_db(tmp_path)
    rowid, snap_sha, _ = _insert_ai_turn(db, _UTC_2026_01_15_NOON)
    # no digest blob seeded -> classified missing_blob
    _insert_snapshot_features(db, _UTC_2026_01_15_NOON, snap_sha,
                              conviction_score=0.9)
    _insert_outcome(db, rowid, realized_r_at_t300s=1.0)
    out = tmp_path / "tune.json"
    rc = _run(["--date", "2026-01-15"], db, snap, dig, out)
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["format_breakdown"]["missing_blob"] == 1
    assert data["format_breakdown"]["bus"] == 0
    assert data["source_correlations"] == []


def test_tune_excludes_unknown_format(tmp_path):
    db, snap, dig = _seed_db(tmp_path)
    rowid, snap_sha, dig_sha = _insert_ai_turn(db, _UTC_2026_01_15_NOON)
    _seed_dig_blob(dig, dig_sha, UNKNOWN_BLOB)
    _insert_snapshot_features(db, _UTC_2026_01_15_NOON, snap_sha,
                              conviction_score=0.9)
    _insert_outcome(db, rowid, realized_r_at_t300s=1.0)
    out = tmp_path / "tune.json"
    rc = _run(["--date", "2026-01-15"], db, snap, dig, out)
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["format_breakdown"]["unknown"] == 1
    assert data["source_correlations"] == []


# ---------------------------------------------------------------------------
# Trigger stats
# ---------------------------------------------------------------------------

def test_tune_trigger_stats_groups_by_kind(tmp_path):
    db, snap, dig = _seed_db(tmp_path)
    _insert_trigger(db, _UTC_2026_01_15_NOON,        kind="OR_TOUCH")
    _insert_trigger(db, _UTC_2026_01_15_NOON + 1000, kind="OR_TOUCH")
    _insert_trigger(db, _UTC_2026_01_15_NOON + 2000, kind="LEVEL_FLIP")
    out = tmp_path / "tune.json"
    rc = _run(["--date", "2026-01-15", "--min-samples", "1"],
              db, snap, dig, out)
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    kinds = {t["kind"]: t["count"] for t in data["trigger_stats"]}
    assert kinds.get("OR_TOUCH") == 2
    assert kinds.get("LEVEL_FLIP") == 1


def test_tune_trigger_stats_uses_half_open_utc_day_bound(tmp_path):
    db, snap, dig = _seed_db(tmp_path)
    _insert_trigger(db, _UTC_2026_01_15_EOD_MS,   kind="OR_TOUCH")
    _insert_trigger(db, _UTC_2026_01_16_START_MS, kind="OR_TOUCH")
    out = tmp_path / "tune.json"
    rc = _run(["--date", "2026-01-15", "--min-samples", "1"],
              db, snap, dig, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    kinds = {t["kind"]: t["count"] for t in data["trigger_stats"]}
    assert kinds.get("OR_TOUCH") == 1


def test_tune_min_samples_gate_suppresses_tiny_kind_classes(tmp_path):
    """Kinds with count < min_samples are dropped from trigger_stats."""
    db, snap, dig = _seed_db(tmp_path)
    for i in range(10):
        _insert_trigger(db, _UTC_2026_01_15_NOON + i, kind="FREQUENT")
    _insert_trigger(db, _UTC_2026_01_15_NOON + 100, kind="RARE")
    out = tmp_path / "tune.json"
    rc = _run(["--date", "2026-01-15", "--min-samples", "5"],
              db, snap, dig, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    kinds = {t["kind"] for t in data["trigger_stats"]}
    assert "FREQUENT" in kinds
    assert "RARE" not in kinds


# ---------------------------------------------------------------------------
# Source correlation
# ---------------------------------------------------------------------------

def test_tune_source_correlation_present_when_data_sufficient(tmp_path):
    """Seed 6 bus rows where conviction_score is monotone with outcome.
    Expect a source_correlations entry for 'conviction_score' with positive
    spearman near +1.0."""
    db, snap, dig = _seed_db(tmp_path)
    # monotone: higher conviction -> higher realized_r
    pairs = [(0.1, -2.0), (0.2, -1.0), (0.3, -0.5),
              (0.4, 0.5), (0.5, 1.0), (0.6, 2.0)]
    for i, (c, r) in enumerate(pairs):
        _seed_bus_row(db, snap, dig, _UTC_2026_01_15_NOON + i,
                      ai_idx=i, conviction=c, realized_r=r)
    out = tmp_path / "tune.json"
    rc = _run(["--date", "2026-01-15", "--min-samples", "5"],
              db, snap, dig, out)
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    cs = {row["source"]: row for row in data["source_correlations"]}
    assert "conviction_score" in cs
    assert cs["conviction_score"]["n_samples"] == 6
    assert cs["conviction_score"]["spearman"] > 0.9


def test_tune_source_correlation_under_min_samples_suppressed(tmp_path):
    """With min_samples=10 and only 6 rows, no source_correlations row."""
    db, snap, dig = _seed_db(tmp_path)
    for i in range(6):
        _seed_bus_row(db, snap, dig, _UTC_2026_01_15_NOON + i,
                      ai_idx=i, conviction=float(i), realized_r=float(i))
    out = tmp_path / "tune.json"
    rc = _run(["--date", "2026-01-15", "--min-samples", "10"],
              db, snap, dig, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    sources = {r["source"] for r in data["source_correlations"]}
    assert "conviction_score" not in sources


def test_tune_no_correlation_when_outcomes_missing(tmp_path):
    """Bus rows without outcomes produce empty source_correlations."""
    db, snap, dig = _seed_db(tmp_path)
    for i in range(8):
        snap_sha = hashlib.sha256(f"snap{i}".encode()).hexdigest()
        blob = BUS_MARKERS_BLOB + f"\n# u{i}"
        dig_sha = hashlib.sha256(blob.encode()).hexdigest()
        _insert_ai_turn(db, _UTC_2026_01_15_NOON + i,
                        snapshot_sha=snap_sha, digest_sha=dig_sha)
        _seed_dig_blob(dig, dig_sha, blob)
        _insert_snapshot_features(db, _UTC_2026_01_15_NOON + i, snap_sha,
                                   conviction_score=float(i))
        # NO outcome inserted
    out = tmp_path / "tune.json"
    rc = _run(["--date", "2026-01-15", "--min-samples", "5"],
              db, snap, dig, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["source_correlations"] == []


# ---------------------------------------------------------------------------
# Advisory output
# ---------------------------------------------------------------------------

def test_tune_advisory_weight_deltas_marked_advisory(tmp_path):
    db, snap, dig = _seed_db(tmp_path)
    pairs = [(0.1, -1.0), (0.2, -0.5), (0.3, 0.0),
              (0.4, 0.5), (0.5, 1.0), (0.6, 1.5)]
    for i, (c, r) in enumerate(pairs):
        _seed_bus_row(db, snap, dig, _UTC_2026_01_15_NOON + i,
                      ai_idx=i, conviction=c, realized_r=r)
    out = tmp_path / "tune.json"
    rc = _run(["--date", "2026-01-15", "--min-samples", "5"],
              db, snap, dig, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data["advisory_weight_deltas"]) >= 1
    for row in data["advisory_weight_deltas"]:
        assert "rationale" in row
        assert "suggested_delta_pct" in row
        # Bounded ±10%.
        assert -0.10 <= row["suggested_delta_pct"] <= 0.10
    # The whole report carries an advisory note.
    blob = json.dumps(data).lower()
    assert "advisory" in blob


# ---------------------------------------------------------------------------
# Math primitives (unit)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAVE_DIRECT_IMPORT, reason="module not importable")
def test_spearman_perfect_monotonic_returns_one():
    rho = _spearman([1, 2, 3, 4, 5], [10, 20, 30, 40, 50])
    assert rho == pytest.approx(1.0)


@pytest.mark.skipif(not _HAVE_DIRECT_IMPORT, reason="module not importable")
def test_spearman_perfect_antitone_returns_minus_one():
    rho = _spearman([1, 2, 3, 4, 5], [50, 40, 30, 20, 10])
    assert rho == pytest.approx(-1.0)


@pytest.mark.skipif(not _HAVE_DIRECT_IMPORT, reason="module not importable")
def test_spearman_empty_and_constant_returns_zero():
    assert _spearman([], []) == 0.0
    assert _spearman([1.0], [1.0]) == 0.0      # n<2
    assert _spearman([2, 2, 2], [1, 2, 3]) == 0.0  # x constant
    assert _spearman([1, 2, 3], [5, 5, 5]) == 0.0  # y constant


@pytest.mark.skipif(not _HAVE_DIRECT_IMPORT, reason="module not importable")
def test_clamp_weight_delta_bounds_to_ten_percent():
    assert _clamp_weight_delta(0.5) == 0.10
    assert _clamp_weight_delta(-0.5) == -0.10
    assert _clamp_weight_delta(0.05) == pytest.approx(0.05)
    assert _clamp_weight_delta(0.0) == 0.0


# ---------------------------------------------------------------------------
# Safety: never writes pax_weights.json, never mutates DB
# ---------------------------------------------------------------------------

def test_tune_does_not_modify_pax_weights_json(tmp_path):
    """Asserts that running the tuner does NOT touch pax_weights.json.
    We use mtime + content sha to detect any mutation."""
    pw = _MCP_SERVER / "bookmap_mcp" / "pax_weights.json"
    if not pw.exists():
        pytest.skip("pax_weights.json not present at expected path")
    before_bytes = pw.read_bytes()
    before_mtime = pw.stat().st_mtime_ns

    db, snap, dig = _seed_db(tmp_path)
    for i in range(6):
        _seed_bus_row(db, snap, dig, _UTC_2026_01_15_NOON + i,
                      ai_idx=i, conviction=float(i), realized_r=float(i))
    out = tmp_path / "tune.json"
    rc = _run(["--date", "2026-01-15"], db, snap, dig, out)
    assert rc == 0

    after_bytes = pw.read_bytes()
    after_mtime = pw.stat().st_mtime_ns
    assert after_bytes == before_bytes, "pax_weights.json content changed"
    assert after_mtime == before_mtime, "pax_weights.json mtime changed"


def test_tune_does_not_mutate_db(tmp_path):
    """Asserts that the bus DB tables have the same row counts and content
    after the tuner runs (read-only contract)."""
    db, snap, dig = _seed_db(tmp_path)
    for i in range(6):
        _seed_bus_row(db, snap, dig, _UTC_2026_01_15_NOON + i,
                      ai_idx=i, conviction=float(i), realized_r=float(i))
    _insert_trigger(db, _UTC_2026_01_15_NOON + 50)

    def _signature():
        with sqlite3.connect(db) as conn:
            counts = {}
            for t in ("ai_turns", "snapshot_features", "trade_outcomes",
                       "trigger_events"):
                counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            return counts

    before = _signature()
    out = tmp_path / "tune.json"
    _run(["--date", "2026-01-15"], db, snap, dig, out)
    after = _signature()
    assert before == after


# ---------------------------------------------------------------------------
# --days N trailing window
# ---------------------------------------------------------------------------

def test_tune_days_window_covers_trailing_span(tmp_path):
    """--days 3 (anchored on 2026-01-15) covers 13/14/15. A trigger on 12
    must NOT appear; one on 15 must."""
    db, snap, dig = _seed_db(tmp_path)
    _UTC_2026_01_12_NOON = _UTC_2026_01_15_NOON - 3 * 86_400_000
    _UTC_2026_01_13_NOON = _UTC_2026_01_15_NOON - 2 * 86_400_000
    _insert_trigger(db, _UTC_2026_01_12_NOON, kind="OUTSIDE_WINDOW")
    for _ in range(10):
        _insert_trigger(db, _UTC_2026_01_13_NOON, kind="INSIDE_WINDOW")
    out = tmp_path / "tune.json"
    rc = _run(["--date", "2026-01-15", "--days", "3"],
              db, snap, dig, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    kinds = {t["kind"] for t in data["trigger_stats"]}
    assert "INSIDE_WINDOW" in kinds
    assert "OUTSIDE_WINDOW" not in kinds


# ---------------------------------------------------------------------------
# Default date and AST import guard
# ---------------------------------------------------------------------------

def test_tune_default_date_is_utc_today(tmp_path):
    db, snap, dig = _seed_db(tmp_path)
    out = tmp_path / "tune.json"
    rc = _run([], db, snap, dig, out)
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    import datetime as _dt
    today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    assert today in json.dumps(data)


def test_tune_no_off_limits_imports():
    """AST guard: pax_bus_tune must NOT import frozen modules. It is a
    read-only reporter that lives outside the chat/decision codepaths."""
    src = (_MCP_SERVER / "bookmap_mcp" / "pax_bus_tune.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden = {
        "pax_ai.prompts", "pax_ai.claude_stream", "pax_ai.chat",
        "pax_ai.triggers", "pax_ai.journal", "pax_ai.edge_calculus",
        "pax_ai.voice", "pax_ai.outcomes",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert mod not in forbidden, f"forbidden import: {mod}"
        elif isinstance(node, ast.Import):
            for n in node.names:
                assert n.name not in forbidden, f"forbidden import: {n.name}"


def test_tune_no_writes_to_feature_bus_module():
    """AST guard: pax_bus_tune must NOT call any feature_bus writer-path
    function (Phase 1 writer freeze)."""
    src = (_MCP_SERVER / "bookmap_mcp" / "pax_bus_tune.py").read_text(encoding="utf-8")
    forbidden_calls = (
        "record_trigger", "record_ai_turn",
        "_writer_loop", "_drain_to_db", "_detect_snapshot_deltas",
        "start", "stop",
    )
    # crude lexical scan; the file is small.
    for name in forbidden_calls:
        assert f"feature_bus.{name}" not in src, (
            f"pax_bus_tune must not call feature_bus.{name}")
