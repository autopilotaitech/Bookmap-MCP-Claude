"""Phase 3: pax_daemon lifecycle tests.

Drives the daemon against a small CSV through end-to-end: journal opens,
snapshots get computed and persisted, decisions land in signals table,
adapter health heartbeats, run row closes cleanly.

Crash recovery is exercised at the Journal layer (test_journal.py); here
we verify the daemon respects the BOOKMAP_ALLOW_TRADING guard and runs
clean.
"""

from __future__ import annotations

import csv
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bookmap_mcp.pax_daemon as daemon   # noqa: E402


def _write_csv(path: Path, rows: list) -> Path:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        for r in rows:
            w.writerow(r)
    return path


@pytest.fixture
def sample_csv(tmp_path) -> Path:
    return _write_csv(tmp_path / "replay.csv", [
        ["ts_iso", "alias", "mid", "bid", "ask",
          "or_high", "or_low", "vwap", "vwap_stddev",
          "regime", "bias_score", "bias_trajectory"],
        ["2026-05-18T13:30:00+00:00", "NQM6", "20050.0", "20049.75", "20050.25",
          "20100.0", "20000.0", "20040.0", "8.0",
          "TRENDING_UP", "0.5", "RISING"],
        ["2026-05-18T13:30:01+00:00", "NQM6", "20050.5", "20050.25", "20050.75",
          "20100.0", "20000.0", "20040.5", "8.0",
          "TRENDING_UP", "0.55", "RISING"],
        ["2026-05-18T13:30:02+00:00", "NQM6", "20051.0", "20050.75", "20051.25",
          "20100.0", "20000.0", "20041.0", "8.0",
          "TRENDING_UP", "0.6", "RISING_STRONG"],
    ])


@pytest.fixture
def daemon_args(tmp_path, sample_csv):
    """Build an argparse.Namespace pointing at sandboxed paths."""
    return daemon.build_parser().parse_args([
        "--source", "csv",
        "--path", str(sample_csv),
        "--alias", "NQM6",
        "--journal", str(tmp_path / "journal.db"),
        "--sim-db", str(tmp_path / "sim.db"),
        "--poll-ms", "0",
    ])


def test_daemon_refuses_when_live_trading_allowed(monkeypatch, daemon_args):
    """The very first check on startup: BOOKMAP_ALLOW_TRADING=1 → SystemExit(2)."""
    monkeypatch.setenv("BOOKMAP_ALLOW_TRADING", "1")
    with pytest.raises(SystemExit) as exc:
        daemon.run_daemon(daemon_args)
    assert exc.value.code == 2


def test_daemon_starts_without_live_trading_env(monkeypatch, daemon_args):
    monkeypatch.delenv("BOOKMAP_ALLOW_TRADING", raising=False)
    rc = daemon.run_daemon(daemon_args)
    assert rc == 0


def test_daemon_writes_snapshots_to_journal(monkeypatch, daemon_args):
    monkeypatch.delenv("BOOKMAP_ALLOW_TRADING", raising=False)
    rc = daemon.run_daemon(daemon_args)
    assert rc == 0
    c = sqlite3.connect(str(daemon_args.journal))
    rows = c.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    c.close()
    # CSV has 3 rows → 3 snapshot rows.
    assert rows == 3


def test_daemon_closes_run_cleanly(monkeypatch, daemon_args):
    monkeypatch.delenv("BOOKMAP_ALLOW_TRADING", raising=False)
    daemon.run_daemon(daemon_args)
    c = sqlite3.connect(str(daemon_args.journal))
    row = c.execute("SELECT ended_ms, notes FROM runs").fetchone()
    c.close()
    assert row[0] is not None, "run should be closed"
    assert "clean" in (row[1] or "")


def test_daemon_logs_adapter_eof_event_on_replay_end(monkeypatch, daemon_args):
    monkeypatch.delenv("BOOKMAP_ALLOW_TRADING", raising=False)
    daemon.run_daemon(daemon_args)
    c = sqlite3.connect(str(daemon_args.journal))
    rows = c.execute(
        "SELECT kind FROM events WHERE kind IN ('ADAPTER_EOF', 'DAEMON_EXIT')"
        ).fetchall()
    kinds = {r[0] for r in rows}
    c.close()
    assert "ADAPTER_EOF" in kinds
    assert "DAEMON_EXIT" in kinds


def test_daemon_once_flag_stops_after_one_snapshot(monkeypatch, daemon_args):
    monkeypatch.delenv("BOOKMAP_ALLOW_TRADING", raising=False)
    daemon_args.once = True
    daemon.run_daemon(daemon_args)
    c = sqlite3.connect(str(daemon_args.journal))
    n = c.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    c.close()
    assert n == 1


def test_daemon_dry_run_skips_journal_writes(monkeypatch, daemon_args):
    monkeypatch.delenv("BOOKMAP_ALLOW_TRADING", raising=False)
    daemon_args.dry_run = True
    rc = daemon.run_daemon(daemon_args)
    assert rc == 0
    # Journal file should NOT have been created.
    assert not daemon_args.journal.exists()


def test_daemon_does_not_import_live_trading_tools():
    """Hard safety: pax_daemon module must not pull in the live MCP tools."""
    import bookmap_mcp.pax_daemon as d
    # The module has no reference to the live-order tool names.
    src = Path(d.__file__).read_text(encoding="utf-8")
    assert "bookmap_place_limit_order" not in src
    assert "bookmap_cancel_order" not in src


def test_daemon_records_signal_version_in_run(monkeypatch, daemon_args):
    """The journal `runs.signal_version` should pick up the conviction
    method version constant so we know which signal engine produced the data."""
    monkeypatch.delenv("BOOKMAP_ALLOW_TRADING", raising=False)
    daemon.run_daemon(daemon_args)
    c = sqlite3.connect(str(daemon_args.journal))
    sv = c.execute("SELECT signal_version FROM runs").fetchone()[0]
    c.close()
    # CONVICTION_METHOD_VERSION = "anchored_multi_source_v2"
    assert "v2" in sv


def test_daemon_places_paper_bracket_on_enter_decision(monkeypatch, daemon_args):
    """End-to-end proof: when the decision pipeline emits ENTER_*, the
    daemon calls decide_and_act which places the bracket on the SimEngine,
    and the journal logs a BRACKET_PLACED event. Without this wiring the
    daemon would compute decisions but never take paper trades — exactly
    the bug that prompted this commit."""
    monkeypatch.delenv("BOOKMAP_ALLOW_TRADING", raising=False)

    captured = []
    def fake_decide(snap, sim, use_claude=False):
        # Actually place a bracket via the real sim engine so the order
        # rows land in pax-daemon-trades.db.
        ids = sim.place_bracket(
            side="BUY", qty=1,
            entry_stop=20100.0, entry_limit=20100.25,
            stop_loss=19990.0, take_profits=[20150.0],
            decision_tag="TEST_ENTER", reason="end-to-end test")
        captured.append(ids)
        return {
            "action": "placed_bracket",
            "decision": "ENTER_LONG_FOLLOW",
            "size_tier": "FULL", "qty": 1, "side": "BUY",
            "entry_px": 20100.0, "entry_limit": 20100.25,
            "stop_loss": 19990.0, "take_profits": [20150.0],
            "level": "OR-H", "ids": ids,
        }
    monkeypatch.setattr("bookmap_mcp.pax_daemon.decide_and_act", fake_decide)

    daemon.run_daemon(daemon_args)

    # 1. Journal has the BRACKET_PLACED event.
    c = sqlite3.connect(str(daemon_args.journal))
    kinds = {r[0] for r in c.execute(
        "SELECT kind FROM events").fetchall()}
    c.close()
    assert "BRACKET_PLACED" in kinds

    # 2. SimEngine actually has the working orders (entry + SL + TP).
    assert captured, "fake_decide should have been called at least once"
    c = sqlite3.connect(str(daemon_args.sim_db))
    rows = c.execute(
        "SELECT role, status FROM orders WHERE alias=?",
        (daemon_args.alias,)).fetchall()
    c.close()
    roles = {r[0] for r in rows}
    assert "ENTRY" in roles
    assert "STOP" in roles
    assert "TP" in roles
