"""Phase 5 audit-consumption report tests.

Contract under test:
  - half-open UTC window: ts_ms >= start_ms AND ts_ms < end_ms
  - one row per ai_turn carrying prompt lineage + forecast + outcome
  - forecast join by (chat_run_id, digest_sha256); 0 -> MISSING, 2+ -> AMBIGUOUS
  - prompt archive: PRESENT_VALID (hash matches), MISSING (no file / no path),
    HASH_MISMATCH (file present, bytes hash != prompt_sha256)
  - read-only against bus DB, forecast DB, and archive files
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

import pytest

from bookmap_mcp import pax_turn_audit as audit
from bookmap_mcp.pax_forecast_store import PaxForecastStore


# ----------------------------------------------------------------- helpers

_AI_TURN_COLS = (
    "id", "schema_version", "ts_ms", "chat_run_id", "deep", "model",
    "router_primary",
    "user_text_raw", "user_text_normalized", "pax_text",
    "snapshot_alias", "snapshot_ts_ms", "snapshot_age_ms",
    "snapshot_sha256", "digest_sha256",
    "exit_code", "elapsed_ms",
    "aborted",
    "prompt_sha256", "prompt_version", "model_release_id",
    "skill_bundle_sha256", "prompt_archive_path",
)


def _make_bus_db(path: Path,
                  ai_turn_rows: List[Dict[str, Any]],
                  trade_outcomes_rows: List[Dict[str, Any]]) -> None:
    """Build a Phase-4-shaped bus DB with just the columns the audit reads."""
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript("""
        CREATE TABLE ai_turns (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          schema_version INTEGER NOT NULL,
          ts_ms INTEGER NOT NULL,
          chat_run_id TEXT NOT NULL,
          deep INTEGER NOT NULL,
          model TEXT NOT NULL,
          router_primary TEXT,
          user_text_raw TEXT NOT NULL,
          user_text_normalized TEXT NOT NULL,
          pax_text TEXT,
          snapshot_alias TEXT, snapshot_ts_ms INTEGER, snapshot_age_ms INTEGER,
          snapshot_sha256 TEXT NOT NULL,
          digest_sha256 TEXT NOT NULL,
          exit_code INTEGER, elapsed_ms INTEGER,
          aborted INTEGER NOT NULL,
          prompt_sha256 TEXT, prompt_version TEXT, model_release_id TEXT,
          skill_bundle_sha256 TEXT, prompt_archive_path TEXT
        );
        CREATE TABLE trade_outcomes (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ai_turn_id INTEGER NOT NULL,
          verdict TEXT,
          realized_r_at_t60s REAL,
          realized_r_at_t180s REAL,
          realized_r_at_t300s REAL,
          realized_r_at_t900s REAL,
          invalidated INTEGER,
          invalidation_reason TEXT,
          label_method TEXT
        );
        """)
        for r in ai_turn_rows:
            cols = list(r.keys())
            placeholders = ",".join("?" * len(cols))
            conn.execute(
                f"INSERT INTO ai_turns ({','.join(cols)}) VALUES ({placeholders})",
                tuple(r[c] for c in cols),
            )
        for r in trade_outcomes_rows:
            cols = list(r.keys())
            placeholders = ",".join("?" * len(cols))
            conn.execute(
                f"INSERT INTO trade_outcomes ({','.join(cols)}) VALUES ({placeholders})",
                tuple(r[c] for c in cols),
            )
        conn.commit()
    finally:
        conn.close()


def _ai_turn(*, id_=1, ts_ms=1_000_000, chat_run_id="run-A",
              digest_sha256="d" * 64, snapshot_sha256="s" * 64,
              prompt_sha256="p" * 64, prompt_archive_path="",
              prompt_version="1.0.0",
              model="claude-haiku-4-5",
              model_release_id="claude-haiku-4-5",
              skill_bundle_sha256="k" * 64,
              snapshot_alias="NQM6.CME@RITHMIC",
              user_text_normalized="ping",
              pax_text="ok"):
    return {
        "id": id_, "schema_version": 1, "ts_ms": ts_ms,
        "chat_run_id": chat_run_id, "deep": 0, "model": model,
        "router_primary": "pax-or",
        "user_text_raw": user_text_normalized,
        "user_text_normalized": user_text_normalized,
        "pax_text": pax_text,
        "snapshot_alias": snapshot_alias,
        "snapshot_ts_ms": ts_ms, "snapshot_age_ms": 0,
        "snapshot_sha256": snapshot_sha256,
        "digest_sha256": digest_sha256,
        "exit_code": 0, "elapsed_ms": 5, "aborted": 0,
        "prompt_sha256": prompt_sha256,
        "prompt_version": prompt_version,
        "model_release_id": model_release_id,
        "skill_bundle_sha256": skill_bundle_sha256,
        "prompt_archive_path": prompt_archive_path,
    }


def _outcome(*, ai_turn_id=1, verdict="ENTER_LONG",
              r60=1.0, r180=2.0, r300=3.0, r900=None,
              invalidated=0, invalidation_reason=None,
              label_method="structured_v1"):
    return {
        "ai_turn_id": ai_turn_id, "verdict": verdict,
        "realized_r_at_t60s": r60, "realized_r_at_t180s": r180,
        "realized_r_at_t300s": r300, "realized_r_at_t900s": r900,
        "invalidated": invalidated,
        "invalidation_reason": invalidation_reason,
        "label_method": label_method,
    }


def _forecast_record(*, chat_run_id="run-A", digest_sha256="d" * 64,
                       snapshot_sha256="s" * 64,
                       alias="NQM6.CME@RITHMIC",
                       execution_read="PAY_FOR_TRADE",
                       direction="LONG",
                       horizon_sec=300,
                       prob_success=0.6,
                       expected_r=0.74,
                       ts_ms=1_000,
                       forecast_id=None):
    return {
        "schema_version": 1,
        "ts_ms": ts_ms,
        "source_turn_id": None,
        "alias": alias,
        "level": "OR-H",
        "thesis": "ACCEPTANCE_LONG",
        "execution_read": execution_read,
        "direction": direction,
        "horizon_sec": horizon_sec,
        "prob_success": prob_success,
        "expected_r": expected_r,
        "invalidation": "stub",
        "features_used": ["or_levels"],
        "forecast_id": (forecast_id or
                         f"fc_{chat_run_id}_{digest_sha256[:6]}_{ts_ms}"),
        "chat_run_id": chat_run_id,
        "digest_sha256": digest_sha256,
        "snapshot_sha256": snapshot_sha256,
    }


def _seed_forecast(forecast_db: Path, **kwargs) -> None:
    rec = _forecast_record(**kwargs)
    with PaxForecastStore(forecast_db) as store:
        store.record_validated(
            rec,
            chat_run_id=rec["chat_run_id"],
            digest_sha256=rec["digest_sha256"],
            snapshot_sha256=rec["snapshot_sha256"],
        )


def _write_archive(archive_root: Path, sha: str, content: bytes) -> Path:
    archive_root.mkdir(parents=True, exist_ok=True)
    p = archive_root / f"{sha}.txt"
    p.write_bytes(content)
    return p


# --------------------------------------------------------------- window

def test_half_open_utc_window_excludes_next_day_boundary(tmp_path):
    """ts_ms == start_ms is included; ts_ms == end_ms is NOT included."""
    bus = tmp_path / "bus.db"
    _make_bus_db(bus,
                 ai_turn_rows=[
                     _ai_turn(id_=1, ts_ms=100_000),   # in window
                     _ai_turn(id_=2, ts_ms=200_000),   # at boundary; excluded
                     _ai_turn(id_=3, ts_ms=199_999),   # in window
                 ],
                 trade_outcomes_rows=[])
    report = audit.build_turn_audit_report(
        bus_db_path=bus, start_ms=100_000, end_ms=200_000)
    ids = sorted(r["ai_turn_id"] for r in report["rows"])
    assert ids == [1, 3]
    assert report["summary"]["n_ai_turns"] == 2


# --------------------------------------------------------------- clean row

def test_clean_joined_row_includes_lineage_forecast_and_outcome(tmp_path):
    bus = tmp_path / "bus.db"
    fc  = tmp_path / "pax-forecast.db"
    archive_root = tmp_path / "archive"

    # Phase 4 lineage on the ai_turn.
    prompt_body = b"PROMPT BODY -- audit happy path"
    prompt_sha = hashlib.sha256(prompt_body).hexdigest()
    archive_file = _write_archive(archive_root, prompt_sha, prompt_body)

    _make_bus_db(bus,
                 ai_turn_rows=[_ai_turn(
                     id_=42, ts_ms=1_500_000_000_000,
                     chat_run_id="run-clean",
                     digest_sha256="dc" * 32,
                     snapshot_sha256="sc" * 32,
                     prompt_sha256=prompt_sha,
                     prompt_archive_path=str(archive_file),
                     prompt_version="1.0.0",
                     model="claude-haiku-4-5",
                     model_release_id="claude-haiku-4-5-20251001",
                 )],
                 trade_outcomes_rows=[_outcome(
                     ai_turn_id=42, verdict="ENTER_LONG",
                     r60=0.5, r180=1.0, r300=1.5, r900=2.0,
                     invalidated=0, label_method="structured_v1",
                 )])
    _seed_forecast(fc,
                    chat_run_id="run-clean",
                    digest_sha256="dc" * 32,
                    snapshot_sha256="sc" * 32,
                    execution_read="PAY_FOR_TRADE",
                    direction="LONG",
                    horizon_sec=300,
                    prob_success=0.62,
                    expected_r=0.74,
                    ts_ms=1_500_000_000_000)

    report = audit.build_turn_audit_report(
        bus_db_path=bus,
        forecast_db_path=fc,
        prompt_archive_root=archive_root,
        start_ms=1_500_000_000_000,
        end_ms=1_500_000_000_001,
    )
    assert len(report["rows"]) == 1
    row = report["rows"][0]

    # Identity + lineage.
    assert row["ai_turn_id"]          == 42
    assert row["ts_ms"]               == 1_500_000_000_000
    assert row["chat_run_id"]         == "run-clean"
    assert row["model"]               == "claude-haiku-4-5"
    assert row["model_release_id"]    == "claude-haiku-4-5-20251001"
    assert row["prompt_version"]      == "1.0.0"
    assert row["prompt_sha256"]       == prompt_sha
    assert row["skill_bundle_sha256"] == "k" * 64
    assert row["prompt_archive_path"] == str(archive_file)
    assert row["snapshot_sha256"]     == "sc" * 32
    assert row["digest_sha256"]       == "dc" * 32
    assert row["snapshot_alias"]      == "NQM6.CME@RITHMIC"
    assert row["user_text_normalized"] == "ping"
    assert row["pax_text"]            == "ok"
    assert row["prompt_archive_status"] == "PRESENT_VALID"

    # Forecast.
    assert row["forecast_status"]  == "PRESENT"
    assert row["forecast_id"]
    assert row["execution_read"]   == "PAY_FOR_TRADE"
    assert row["direction"]        == "LONG"
    assert row["horizon_sec"]      == 300
    assert row["prob_success"]     == pytest.approx(0.62)
    assert row["expected_r"]       == pytest.approx(0.74)

    # Outcome.
    assert row["outcome_status"]   == "VALID"
    assert row["verdict"]          == "ENTER_LONG"
    assert row["invalidated"]      == 0
    assert row["invalidation_reason"] is None
    assert row["realized_r_at_t60s"]  == pytest.approx(0.5)
    assert row["realized_r_at_t180s"] == pytest.approx(1.0)
    assert row["realized_r_at_t300s"] == pytest.approx(1.5)
    assert row["realized_r_at_t900s"] == pytest.approx(2.0)


# --------------------------------------------------------------- forecast

def test_missing_forecast_marked_missing(tmp_path):
    bus = tmp_path / "bus.db"
    fc  = tmp_path / "pax-forecast.db"
    _make_bus_db(bus,
                 ai_turn_rows=[_ai_turn(id_=1, ts_ms=1_000)],
                 trade_outcomes_rows=[])
    # Seed a forecast under DIFFERENT linkage keys so it won't join.
    _seed_forecast(fc, chat_run_id="other-run", digest_sha256="x" * 64)
    report = audit.build_turn_audit_report(
        bus_db_path=bus, forecast_db_path=fc,
        start_ms=0, end_ms=10_000)
    row = report["rows"][0]
    assert row["forecast_status"] == "MISSING"
    assert row["forecast_id"]     is None
    assert row["execution_read"]  is None
    assert row["direction"]       is None
    assert row["horizon_sec"]     is None
    assert report["summary"]["n_forecast_missing"]   == 1
    assert report["summary"]["n_forecast_ambiguous"] == 0
    assert report["summary"]["n_with_forecast"]      == 0


def test_ambiguous_forecast_marked_ambiguous(tmp_path):
    bus = tmp_path / "bus.db"
    fc  = tmp_path / "pax-forecast.db"
    _make_bus_db(bus,
                 ai_turn_rows=[_ai_turn(id_=1, ts_ms=1_000,
                                          chat_run_id="run-dup",
                                          digest_sha256="dd" * 32)],
                 trade_outcomes_rows=[])
    # Two distinct forecasts share the same (chat_run_id, digest_sha256).
    _seed_forecast(fc, chat_run_id="run-dup", digest_sha256="dd" * 32,
                     snapshot_sha256="aa" * 32, direction="LONG",
                     forecast_id="fc-1")
    _seed_forecast(fc, chat_run_id="run-dup", digest_sha256="dd" * 32,
                     snapshot_sha256="bb" * 32, direction="SHORT",
                     forecast_id="fc-2")
    report = audit.build_turn_audit_report(
        bus_db_path=bus, forecast_db_path=fc,
        start_ms=0, end_ms=10_000)
    row = report["rows"][0]
    assert row["forecast_status"] == "AMBIGUOUS"
    assert row["forecast_id"]     is None
    assert row["execution_read"]  is None
    assert report["summary"]["n_forecast_ambiguous"] == 1
    assert report["summary"]["n_with_forecast"]      == 0


# --------------------------------------------------------------- outcome

def test_invalidated_outcome_is_surfaced_and_counted(tmp_path):
    bus = tmp_path / "bus.db"
    _make_bus_db(bus,
                 ai_turn_rows=[_ai_turn(id_=7, ts_ms=1_000)],
                 trade_outcomes_rows=[_outcome(
                     ai_turn_id=7, verdict="ENTER_LONG",
                     r60=None, r180=None, r300=None, r900=None,
                     invalidated=1,
                     invalidation_reason="HORIZON_DATA_MISSING",
                 )])
    report = audit.build_turn_audit_report(
        bus_db_path=bus, start_ms=0, end_ms=10_000)
    row = report["rows"][0]
    assert row["outcome_status"]      == "INVALIDATED"
    assert row["invalidated"]         == 1
    assert row["invalidation_reason"] == "HORIZON_DATA_MISSING"
    assert row["verdict"]             == "ENTER_LONG"
    assert row["realized_r_at_t60s"]  is None
    assert report["summary"]["n_with_outcome"]        == 1
    assert report["summary"]["n_outcome_invalidated"] == 1


def test_missing_outcome_is_status_missing(tmp_path):
    bus = tmp_path / "bus.db"
    _make_bus_db(bus,
                 ai_turn_rows=[_ai_turn(id_=8, ts_ms=1_000)],
                 trade_outcomes_rows=[])
    report = audit.build_turn_audit_report(
        bus_db_path=bus, start_ms=0, end_ms=10_000)
    row = report["rows"][0]
    assert row["outcome_status"]     == "MISSING"
    assert row["verdict"]            is None
    assert row["invalidated"]        is None
    assert row["realized_r_at_t60s"] is None
    assert report["summary"]["n_with_outcome"]        == 0
    assert report["summary"]["n_outcome_invalidated"] == 0


# --------------------------------------------------------------- archive

def test_prompt_archive_present_and_valid(tmp_path):
    bus = tmp_path / "bus.db"
    archive_root = tmp_path / "archive"
    body = b"BODY-A"
    sha = hashlib.sha256(body).hexdigest()
    archive_file = _write_archive(archive_root, sha, body)
    _make_bus_db(bus,
                 ai_turn_rows=[_ai_turn(id_=1, ts_ms=1_000,
                                          prompt_sha256=sha,
                                          prompt_archive_path=str(archive_file))],
                 trade_outcomes_rows=[])
    report = audit.build_turn_audit_report(
        bus_db_path=bus, prompt_archive_root=archive_root,
        start_ms=0, end_ms=10_000)
    row = report["rows"][0]
    assert row["prompt_archive_status"] == "PRESENT_VALID"
    assert report["summary"]["n_prompt_archive_present"]      == 1
    assert report["summary"]["n_prompt_archive_missing"]      == 0
    assert report["summary"]["n_prompt_archive_hash_mismatch"] == 0


def test_prompt_archive_missing_when_path_empty(tmp_path):
    bus = tmp_path / "bus.db"
    _make_bus_db(bus,
                 ai_turn_rows=[_ai_turn(id_=1, ts_ms=1_000,
                                          prompt_archive_path="")],
                 trade_outcomes_rows=[])
    report = audit.build_turn_audit_report(
        bus_db_path=bus, start_ms=0, end_ms=10_000)
    row = report["rows"][0]
    assert row["prompt_archive_status"] == "MISSING"
    assert report["summary"]["n_prompt_archive_missing"] == 1
    assert report["summary"]["n_prompt_archive_present"] == 0


def test_prompt_archive_missing_when_file_absent(tmp_path):
    bus = tmp_path / "bus.db"
    _make_bus_db(bus,
                 ai_turn_rows=[_ai_turn(id_=1, ts_ms=1_000,
                                          prompt_sha256="a" * 64,
                                          prompt_archive_path=str(
                                              tmp_path / "ghost.txt"))],
                 trade_outcomes_rows=[])
    report = audit.build_turn_audit_report(
        bus_db_path=bus, start_ms=0, end_ms=10_000)
    row = report["rows"][0]
    assert row["prompt_archive_status"] == "MISSING"
    assert report["summary"]["n_prompt_archive_missing"] == 1


def test_prompt_archive_hash_mismatch_counted(tmp_path):
    bus = tmp_path / "bus.db"
    archive_root = tmp_path / "archive"
    # Write bytes whose SHA does NOT match the stored prompt_sha256.
    archive_file = _write_archive(archive_root,
                                    "a" * 64,             # named by claimed sha
                                    b"DIFFERENT BYTES")
    _make_bus_db(bus,
                 ai_turn_rows=[_ai_turn(id_=1, ts_ms=1_000,
                                          prompt_sha256="a" * 64,
                                          prompt_archive_path=str(archive_file))],
                 trade_outcomes_rows=[])
    report = audit.build_turn_audit_report(
        bus_db_path=bus, prompt_archive_root=archive_root,
        start_ms=0, end_ms=10_000)
    row = report["rows"][0]
    assert row["prompt_archive_status"] == "HASH_MISMATCH"
    assert report["summary"]["n_prompt_archive_hash_mismatch"] == 1
    assert report["summary"]["n_prompt_archive_present"]       == 0
    assert report["summary"]["n_prompt_archive_missing"]       == 0


# --------------------------------------------------------------- CLI

def test_cli_writes_json_report_for_date(tmp_path):
    bus = tmp_path / "bus.db"
    fc  = tmp_path / "pax-forecast.db"
    # Date = 2026-05-25 UTC -> [start, start+86_400_000).
    # Put one ai_turn inside the day so the report is non-empty.
    import datetime as _dt
    day_start = int(_dt.datetime(2026, 5, 25, 0, 0, 0,
                                   tzinfo=_dt.timezone.utc).timestamp() * 1000)
    _make_bus_db(bus,
                 ai_turn_rows=[_ai_turn(id_=1, ts_ms=day_start + 1)],
                 trade_outcomes_rows=[])
    _seed_forecast(fc, ts_ms=day_start + 1)

    report_path = tmp_path / "out" / "turn-audit-2026-05-25.json"
    rc = audit.main([
        "--bus-db", str(bus),
        "--forecast-db", str(fc),
        "--date", "2026-05-25",
        "--report", str(report_path),
    ])
    assert rc == 0
    assert report_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert "rows" in payload and "summary" in payload
    assert payload["summary"]["n_ai_turns"] == 1
    assert payload["window"]["start_ms"] == day_start
    assert payload["window"]["end_ms"]   == day_start + 86_400_000


def test_cli_supports_explicit_start_end_ms(tmp_path):
    bus = tmp_path / "bus.db"
    _make_bus_db(bus,
                 ai_turn_rows=[_ai_turn(id_=1, ts_ms=500),
                                _ai_turn(id_=2, ts_ms=1500)],
                 trade_outcomes_rows=[])
    report_path = tmp_path / "explicit.json"
    rc = audit.main([
        "--bus-db", str(bus),
        "--start-ms", "0",
        "--end-ms", "1000",
        "--report", str(report_path),
    ])
    assert rc == 0
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["summary"]["n_ai_turns"] == 1
    assert payload["rows"][0]["ai_turn_id"] == 1


def test_cli_rejects_window_without_date_or_explicit_ms(tmp_path):
    bus = tmp_path / "bus.db"
    _make_bus_db(bus, ai_turn_rows=[], trade_outcomes_rows=[])
    rc = audit.main([
        "--bus-db", str(bus),
        "--report", str(tmp_path / "x.json"),
    ])
    assert rc != 0


# --------------------------------------------------------------- read-only

def test_module_is_read_only_against_inputs(tmp_path):
    """Bus DB, forecast DB, and archive file must be byte-identical and
    mtime-stable after the report runs."""
    bus = tmp_path / "bus.db"
    fc  = tmp_path / "pax-forecast.db"
    archive_root = tmp_path / "archive"
    body = b"BODY-RO"
    sha = hashlib.sha256(body).hexdigest()
    archive_file = _write_archive(archive_root, sha, body)
    _make_bus_db(bus,
                 ai_turn_rows=[_ai_turn(id_=1, ts_ms=1_000,
                                          prompt_sha256=sha,
                                          prompt_archive_path=str(archive_file))],
                 trade_outcomes_rows=[_outcome(ai_turn_id=1)])
    _seed_forecast(fc)

    before = {p: (p.read_bytes(), p.stat().st_mtime_ns)
              for p in (bus, fc, archive_file)}
    audit.build_turn_audit_report(
        bus_db_path=bus, forecast_db_path=fc,
        prompt_archive_root=archive_root,
        start_ms=0, end_ms=10_000)
    for p, (body_before, mtime_before) in before.items():
        assert p.read_bytes() == body_before, f"{p} bytes changed"
        assert p.stat().st_mtime_ns == mtime_before, f"{p} mtime changed"


def test_missing_bus_db_returns_empty_report(tmp_path):
    """A non-existent bus DB returns an empty-but-well-formed report."""
    report = audit.build_turn_audit_report(
        bus_db_path=tmp_path / "no-such.db",
        start_ms=0, end_ms=1_000)
    assert report["rows"] == []
    assert report["summary"]["n_ai_turns"] == 0


# ---------------------------------------------------- read-only forecast DB

def _snapshot_file(p: Path):
    return (p.read_bytes(), p.stat().st_mtime_ns)


def _make_legacy_forecast_db(path: Path) -> None:
    """A 'pre-linkage' forecasts table that mirrors what PaxForecastStore
    looked like before chat_run_id/digest_sha256/snapshot_sha256 landed.
    The audit must NEVER ALTER this table or otherwise mutate the file."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("""
        CREATE TABLE forecasts (
            forecast_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL,
            ts_ms INTEGER NOT NULL,
            alias TEXT NOT NULL,
            level TEXT NOT NULL,
            thesis TEXT NOT NULL,
            execution_read TEXT NOT NULL,
            direction TEXT NOT NULL,
            horizon_sec INTEGER NOT NULL,
            prob_success REAL NOT NULL,
            expected_r REAL NOT NULL,
            invalidation TEXT NOT NULL,
            features_used TEXT NOT NULL
        )
        """)
        conn.execute(
            "INSERT INTO forecasts (forecast_id, schema_version, ts_ms, "
            "alias, level, thesis, execution_read, direction, horizon_sec, "
            "prob_success, expected_r, invalidation, features_used) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("fc-legacy-1", 1, 1_000, "NQM6.CME@RITHMIC", "OR-H",
             "ACCEPTANCE_LONG", "PAY_FOR_TRADE", "LONG", 300, 0.6, 0.74,
             "stub", "[\"or_levels\"]"),
        )
        conn.commit()
    finally:
        conn.close()


def test_audit_does_not_mutate_current_forecast_db(tmp_path):
    """Modern (linkage-aware) forecast DB: bytes + mtime stable after audit."""
    bus = tmp_path / "bus.db"
    fc  = tmp_path / "pax-forecast.db"
    _make_bus_db(bus,
                 ai_turn_rows=[_ai_turn(id_=1, ts_ms=1_000)],
                 trade_outcomes_rows=[])
    _seed_forecast(fc)
    before = _snapshot_file(fc)
    audit.build_turn_audit_report(
        bus_db_path=bus, forecast_db_path=fc,
        start_ms=0, end_ms=10_000)
    after_body, after_mtime = _snapshot_file(fc)
    assert after_body == before[0],  "forecast DB bytes changed"
    assert after_mtime == before[1], "forecast DB mtime changed"


def test_audit_does_not_mutate_legacy_forecast_db(tmp_path):
    """Pre-linkage forecasts table: must NOT be ALTERed or otherwise
    written to. The audit reads what it can and treats missing linkage
    columns as 'no forecast match'."""
    bus = tmp_path / "bus.db"
    fc  = tmp_path / "legacy-pax-forecast.db"
    _make_bus_db(bus,
                 ai_turn_rows=[_ai_turn(id_=1, ts_ms=1_000)],
                 trade_outcomes_rows=[])
    _make_legacy_forecast_db(fc)
    before = _snapshot_file(fc)
    audit.build_turn_audit_report(
        bus_db_path=bus, forecast_db_path=fc,
        start_ms=0, end_ms=10_000)
    after_body, after_mtime = _snapshot_file(fc)
    assert after_body == before[0],  "legacy forecast DB bytes changed"
    assert after_mtime == before[1], "legacy forecast DB mtime changed"


def test_audit_legacy_forecast_db_returns_missing_not_crash(tmp_path):
    """Legacy schema lacks chat_run_id/digest_sha256 — audit must surface
    forecast_status=MISSING rather than raise."""
    bus = tmp_path / "bus.db"
    fc  = tmp_path / "legacy-pax-forecast.db"
    _make_bus_db(bus,
                 ai_turn_rows=[_ai_turn(id_=1, ts_ms=1_000)],
                 trade_outcomes_rows=[])
    _make_legacy_forecast_db(fc)
    report = audit.build_turn_audit_report(
        bus_db_path=bus, forecast_db_path=fc,
        start_ms=0, end_ms=10_000)
    assert len(report["rows"]) == 1
    assert report["rows"][0]["forecast_status"] == "MISSING"
    assert report["summary"]["n_forecast_missing"] == 1


def test_audit_missing_forecasts_table_returns_missing_not_crash(tmp_path):
    """A forecast DB file with NO 'forecasts' table at all (e.g. some
    other SQLite DB the operator pointed at by mistake) -> MISSING."""
    bus = tmp_path / "bus.db"
    fc  = tmp_path / "wrong-db.db"
    # A db file with an unrelated table.
    conn = sqlite3.connect(str(fc))
    try:
        conn.execute("CREATE TABLE unrelated (x INTEGER)")
        conn.commit()
    finally:
        conn.close()
    _make_bus_db(bus,
                 ai_turn_rows=[_ai_turn(id_=1, ts_ms=1_000)],
                 trade_outcomes_rows=[])
    before = _snapshot_file(fc)
    report = audit.build_turn_audit_report(
        bus_db_path=bus, forecast_db_path=fc,
        start_ms=0, end_ms=10_000)
    after_body, after_mtime = _snapshot_file(fc)
    assert after_body  == before[0]
    assert after_mtime == before[1]
    assert report["rows"][0]["forecast_status"] == "MISSING"
    assert report["summary"]["n_forecast_missing"] == 1


# --------------------------------------------------- Phase 8: summary render

def _build_real_report(tmp_path) -> dict:
    """Build a mixed-shape report via the real public API. Reused across
    the summary-render tests so the rendered text is exercised against
    realistic, audit-shaped inputs (no hand-curated dicts)."""
    bus = tmp_path / "bus-for-summary.db"
    fc  = tmp_path / "fc-for-summary.db"
    rows = [
        _ai_turn(id_=1, ts_ms=10, chat_run_id="A", digest_sha256="d1" * 32,
                  pax_text="clean turn"),
        _ai_turn(id_=2, ts_ms=20, chat_run_id="B", digest_sha256="d2" * 32,
                  pax_text="forecast missing here"),
        _ai_turn(id_=3, ts_ms=30, chat_run_id="C", digest_sha256="d3" * 32,
                  pax_text="ambiguous"),
        _ai_turn(id_=4, ts_ms=40, chat_run_id="D", digest_sha256="d4" * 32,
                  pax_text="invalidated outcome"),
    ]
    outcomes = [
        _outcome(ai_turn_id=1, invalidated=0),
        _outcome(ai_turn_id=4, invalidated=1,
                  invalidation_reason="HORIZON_DATA_MISSING"),
    ]
    _make_bus_db(bus, ai_turn_rows=rows, trade_outcomes_rows=outcomes)
    _seed_forecast(fc, chat_run_id="A", digest_sha256="d1" * 32,
                     forecast_id="fc-A")
    _seed_forecast(fc, chat_run_id="C", digest_sha256="d3" * 32,
                     snapshot_sha256="x" * 64, forecast_id="fc-C1")
    _seed_forecast(fc, chat_run_id="C", digest_sha256="d3" * 32,
                     snapshot_sha256="y" * 64, forecast_id="fc-C2")
    return audit.build_turn_audit_report(
        bus_db_path=bus, forecast_db_path=fc,
        start_ms=0, end_ms=10_000)


def test_summary_string_includes_all_required_count_families(tmp_path):
    """Every count family in the spec must appear in the rendered summary
    so the operator can spot every defect category at a glance."""
    report = _build_real_report(tmp_path)
    text = audit.render_turn_audit_summary(report)
    # Window header.
    assert "start_ms" in text or "Turn audit summary" in text
    # n_ai_turns count.
    assert "n_ai_turns" in text
    # Forecast counts.
    assert "PRESENT"   in text
    assert "MISSING"   in text
    assert "AMBIGUOUS" in text
    # Outcome counts.
    assert "VALID"        in text
    assert "INVALIDATED"  in text
    # Prompt archive counts.
    assert "PRESENT_VALID"  in text
    assert "HASH_MISMATCH"  in text


def test_summary_invalidation_reason_aggregation_appears_with_counts(tmp_path):
    """When multiple rows share an invalidation_reason, the summary must
    aggregate counts and list the reason in a Top section."""
    bus = tmp_path / "bus-inv.db"
    rows = [_ai_turn(id_=i, ts_ms=i, chat_run_id=f"R{i}",
                       digest_sha256=f"d{i:02d}" * 16) for i in range(1, 6)]
    outcomes = [
        _outcome(ai_turn_id=1, invalidated=1,
                  invalidation_reason="HORIZON_DATA_MISSING"),
        _outcome(ai_turn_id=2, invalidated=1,
                  invalidation_reason="HORIZON_DATA_MISSING"),
        _outcome(ai_turn_id=3, invalidated=1,
                  invalidation_reason="HORIZON_DATA_MISSING"),
        _outcome(ai_turn_id=4, invalidated=1,
                  invalidation_reason="SNAPSHOT_MISSING_AT_T0"),
        _outcome(ai_turn_id=5, invalidated=1,
                  invalidation_reason="FORECAST_MISSING_OR_AMBIGUOUS"),
    ]
    _make_bus_db(bus, ai_turn_rows=rows, trade_outcomes_rows=outcomes)
    report = audit.build_turn_audit_report(
        bus_db_path=bus, start_ms=0, end_ms=1_000_000)
    text = audit.render_turn_audit_summary(report)
    assert "HORIZON_DATA_MISSING" in text
    assert "SNAPSHOT_MISSING_AT_T0" in text
    assert "FORECAST_MISSING_OR_AMBIGUOUS" in text
    # The horizon-missing line must carry its count (3).
    # We pin the substring "HORIZON_DATA_MISSING: 3" to keep the format honest.
    assert "HORIZON_DATA_MISSING: 3" in text
    assert "SNAPSHOT_MISSING_AT_T0: 1" in text


def test_summary_examples_are_capped_deterministically(tmp_path):
    """Seed > cap forecast defects and verify the summary lists at most
    the cap and the SAME set across two render calls."""
    bus = tmp_path / "bus-cap.db"
    rows = [_ai_turn(id_=i, ts_ms=i, chat_run_id=f"R{i}",
                       digest_sha256=f"d{i:03d}" * 16,
                       pax_text=f"defect-{i}")
            for i in range(1, 21)]   # 20 forecast defects, no forecasts seeded
    _make_bus_db(bus, ai_turn_rows=rows, trade_outcomes_rows=[])
    report = audit.build_turn_audit_report(
        bus_db_path=bus, start_ms=0, end_ms=1_000_000)
    text1 = audit.render_turn_audit_summary(report)
    text2 = audit.render_turn_audit_summary(report)
    # Determinism: byte-equal across runs.
    assert text1 == text2
    # Cap proof: a known cap should appear at most CAP times. We assert
    # 'defect-1 ' is present (first row by ts_ms) and 'defect-20' is NOT
    # (well past the cap). The cap itself we treat as <= 10 for this test.
    assert "defect-1 " in text1 or "defect-1\"" in text1 or "'defect-1'" in text1
    assert "defect-20" not in text1


def test_summary_truncates_long_pax_text_safely(tmp_path):
    """A 500-char pax_text on a defect row must be truncated in the
    summary so the line stays operator-readable."""
    bus = tmp_path / "bus-long.db"
    long_text = "X" * 500
    rows = [_ai_turn(id_=1, ts_ms=1, chat_run_id="R", digest_sha256="d" * 64,
                       pax_text=long_text)]
    _make_bus_db(bus, ai_turn_rows=rows, trade_outcomes_rows=[])
    report = audit.build_turn_audit_report(
        bus_db_path=bus, start_ms=0, end_ms=1_000_000)
    text = audit.render_turn_audit_summary(report)
    # The full 500-X string must NOT survive verbatim into the summary.
    assert long_text not in text
    # A truncation marker ('...') must be present near the defect row.
    assert "..." in text


# --------------------------------------------------- Phase 8: CLI flags

def test_cli_writes_summary_beside_json_in_normal_mode(tmp_path):
    bus = tmp_path / "bus.db"
    _make_bus_db(bus,
                 ai_turn_rows=[_ai_turn(id_=1, ts_ms=500)],
                 trade_outcomes_rows=[])
    report_path  = tmp_path / "audit.json"
    summary_path = tmp_path / "audit.txt"
    rc = audit.main([
        "--bus-db", str(bus),
        "--start-ms", "0", "--end-ms", "1000",
        "--report", str(report_path),
        "--summary-report", str(summary_path),
    ])
    assert rc == 0
    assert report_path.exists()
    assert summary_path.exists()
    summary_body = summary_path.read_text(encoding="utf-8")
    assert "n_ai_turns" in summary_body


def test_cli_summary_only_from_report_works_without_db_args(tmp_path, capsys):
    # Pre-create a real JSON report.
    bus = tmp_path / "bus.db"
    _make_bus_db(bus,
                 ai_turn_rows=[_ai_turn(id_=1, ts_ms=500)],
                 trade_outcomes_rows=[])
    report_path = tmp_path / "src.json"
    audit.main([
        "--bus-db", str(bus),
        "--start-ms", "0", "--end-ms", "1000",
        "--report", str(report_path),
    ])
    capsys.readouterr()  # drop the stderr line from the first run

    rc = audit.main([
        "--summary-only-from-report", str(report_path),
    ])
    assert rc == 0
    captured = capsys.readouterr()
    assert "n_ai_turns" in captured.out


def test_cli_summary_only_with_summary_report_writes_to_file(tmp_path):
    bus = tmp_path / "bus.db"
    _make_bus_db(bus,
                 ai_turn_rows=[_ai_turn(id_=1, ts_ms=500)],
                 trade_outcomes_rows=[])
    report_path  = tmp_path / "src.json"
    summary_path = tmp_path / "dst.txt"
    audit.main([
        "--bus-db", str(bus),
        "--start-ms", "0", "--end-ms", "1000",
        "--report", str(report_path),
    ])

    rc = audit.main([
        "--summary-only-from-report", str(report_path),
        "--summary-report", str(summary_path),
    ])
    assert rc == 0
    assert summary_path.exists()
    assert "n_ai_turns" in summary_path.read_text(encoding="utf-8")


def test_summary_only_mode_preserves_input_json_bytes_and_mtime(tmp_path):
    bus = tmp_path / "bus.db"
    _make_bus_db(bus,
                 ai_turn_rows=[_ai_turn(id_=1, ts_ms=500)],
                 trade_outcomes_rows=[])
    report_path = tmp_path / "src.json"
    audit.main([
        "--bus-db", str(bus),
        "--start-ms", "0", "--end-ms", "1000",
        "--report", str(report_path),
    ])
    before_body  = report_path.read_bytes()
    before_mtime = report_path.stat().st_mtime_ns
    audit.main([
        "--summary-only-from-report", str(report_path),
        "--summary-report", str(tmp_path / "ignore.txt"),
    ])
    assert report_path.read_bytes()        == before_body
    assert report_path.stat().st_mtime_ns  == before_mtime


def test_malformed_json_in_summary_only_mode_exits_nonzero_no_output(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    summary_path = tmp_path / "should-not-exist.txt"
    rc = audit.main([
        "--summary-only-from-report", str(bad),
        "--summary-report", str(summary_path),
    ])
    assert rc != 0
    assert not summary_path.exists()


def test_summary_only_mode_does_not_require_bus_db_or_window(tmp_path):
    """Spec: summary-only mode must not require --bus-db, --date,
    --start-ms, or --end-ms. argparse-level rejection would defeat the
    purpose."""
    bus = tmp_path / "bus.db"
    _make_bus_db(bus,
                 ai_turn_rows=[_ai_turn(id_=1, ts_ms=500)],
                 trade_outcomes_rows=[])
    report_path = tmp_path / "src.json"
    audit.main([
        "--bus-db", str(bus),
        "--start-ms", "0", "--end-ms", "1000",
        "--report", str(report_path),
    ])
    # Now run summary-only with NO bus-db, NO date, NO start/end.
    rc = audit.main([
        "--summary-only-from-report", str(report_path),
        "--summary-report", str(tmp_path / "out.txt"),
    ])
    assert rc == 0


def test_summary_only_missing_report_file_exits_nonzero(tmp_path):
    rc = audit.main([
        "--summary-only-from-report", str(tmp_path / "no-such.json"),
    ])
    assert rc != 0


def test_summary_counts_all_categories_independently(tmp_path):
    """Mixed dataset: one clean, one missing forecast, one ambiguous,
    one invalidated outcome, one with no outcome."""
    bus = tmp_path / "bus.db"
    fc  = tmp_path / "pax-forecast.db"
    rows = [
        _ai_turn(id_=1, ts_ms=10, chat_run_id="A", digest_sha256="d1" * 32),
        _ai_turn(id_=2, ts_ms=20, chat_run_id="B", digest_sha256="d2" * 32),
        _ai_turn(id_=3, ts_ms=30, chat_run_id="C", digest_sha256="d3" * 32),
        _ai_turn(id_=4, ts_ms=40, chat_run_id="D", digest_sha256="d4" * 32),
    ]
    outs = [
        _outcome(ai_turn_id=1, invalidated=0),
        _outcome(ai_turn_id=3, invalidated=1,
                  invalidation_reason="SNAPSHOT_MISSING_AT_T0"),
        # ai_turn 4 has no outcome row.
    ]
    _make_bus_db(bus, ai_turn_rows=rows, trade_outcomes_rows=outs)
    # Forecasts: 1 unique (clean), 3 ambiguous, 4 none.
    _seed_forecast(fc, chat_run_id="A", digest_sha256="d1" * 32,
                     forecast_id="fc-A")
    _seed_forecast(fc, chat_run_id="C", digest_sha256="d3" * 32,
                     snapshot_sha256="x" * 64, forecast_id="fc-C1")
    _seed_forecast(fc, chat_run_id="C", digest_sha256="d3" * 32,
                     snapshot_sha256="y" * 64, forecast_id="fc-C2")

    report = audit.build_turn_audit_report(
        bus_db_path=bus, forecast_db_path=fc,
        start_ms=0, end_ms=10_000)
    s = report["summary"]
    assert s["n_ai_turns"]            == 4
    assert s["n_with_forecast"]       == 1
    assert s["n_forecast_missing"]    == 2     # B and D
    assert s["n_forecast_ambiguous"]  == 1     # C
    assert s["n_with_outcome"]        == 2     # 1 and 3
    assert s["n_outcome_invalidated"] == 1     # 3
