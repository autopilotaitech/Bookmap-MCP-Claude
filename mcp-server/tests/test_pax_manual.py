"""Tests for the pax_manual CLI.

Verifies:
- BOOKMAP_ALLOW_TRADING=1 safety gate refuses to run.
- `long` and `short` subcommands place a bracket on the SimEngine.
- `status` subcommand prints a non-empty JSON snapshot.
- `flatten` and `cancel` paths exit cleanly.
"""

from __future__ import annotations

import json
import os
import sys
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from bookmap_mcp import pax_manual


_ALIAS = "MANUAL.TEST"


def _common_args(db: Path):
    return ["--alias", _ALIAS, "--sim-db", str(db), "--eod-hour", ""] if False else [
        "--alias", _ALIAS, "--sim-db", str(db),
    ]


def test_help_does_not_crash(capsys):
    with pytest.raises(SystemExit):
        pax_manual.build_parser().parse_args(["--help"])


def test_safety_refuses_when_live_trading_enabled(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BOOKMAP_ALLOW_TRADING", "1")
    db = tmp_path / "sim.db"
    with pytest.raises(SystemExit) as exc:
        pax_manual.main([
            *_common_args(db),
            "long", "1", "30000.0", "29990.0", "30020.0",
        ])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "BOOKMAP_ALLOW_TRADING" in err


def test_long_bracket_placed_in_sim_db(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("BOOKMAP_ALLOW_TRADING", raising=False)
    db = tmp_path / "sim.db"
    code = pax_manual.main([
        *_common_args(db),
        "long", "2", "30000.0", "29990.0", "30020.0", "30040.0",
        "--reason", "test entry",
    ])
    assert code == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["action"] == "bracket_placed"
    assert payload["side"] == "BUY"
    assert payload["qty"] == 2
    assert payload["entry_limit"] == 30000.0
    assert payload["take_profits"] == [30020.0, 30040.0]
    # ids should be a dict with entry/stop/tps keys
    ids = payload["ids"]
    assert "entry" in ids and "stop" in ids and "tps" in ids
    assert len(ids["tps"]) == 2

    # Verify rows landed in the DB
    with sqlite3.connect(str(db)) as conn:
        rows = conn.execute(
            "SELECT side, qty, role FROM orders WHERE alias=? ORDER BY id",
            (_ALIAS,)).fetchall()
    # 1 entry + 1 stop + 2 TPs = 4 rows
    assert len(rows) == 4
    roles = sorted(r[2] for r in rows)
    assert roles == ["ENTRY", "STOP", "TP", "TP"]
    sides = {r[0] for r in rows}
    # Entry is BUY; stop+tps are SELL (exit side)
    assert sides == {"BUY", "SELL"}


def test_short_bracket_placed(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("BOOKMAP_ALLOW_TRADING", raising=False)
    db = tmp_path / "sim.db"
    code = pax_manual.main([
        *_common_args(db),
        "short", "1", "30100.0", "30110.0", "30080.0",
    ])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["side"] == "SELL"
    assert payload["entry_limit"] == 30100.0
    with sqlite3.connect(str(db)) as conn:
        rows = conn.execute("SELECT side, role FROM orders WHERE alias=?",
                            (_ALIAS,)).fetchall()
    # 1 entry SELL + 1 stop BUY + 1 TP BUY = 3 rows
    sides = sorted(r[0] for r in rows)
    assert sides == ["BUY", "BUY", "SELL"]


def test_status_returns_json_snapshot(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("BOOKMAP_ALLOW_TRADING", raising=False)
    db = tmp_path / "sim.db"
    code = pax_manual.main([*_common_args(db), "status"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    # SimEngine.snapshot() should return at least these top-level keys
    assert "alias" in payload or "position" in payload or "orders" in payload


def test_flatten_runs_cleanly_with_no_position(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("BOOKMAP_ALLOW_TRADING", raising=False)
    db = tmp_path / "sim.db"
    code = pax_manual.main([*_common_args(db), "flatten", "--reason", "noop"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "flatten"


def test_cancel_nonexistent_returns_non_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("BOOKMAP_ALLOW_TRADING", raising=False)
    db = tmp_path / "sim.db"
    code = pax_manual.main([*_common_args(db), "cancel", "9999"])
    # No such order -> ok=False -> exit 1
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "cancel"
    assert payload["ok"] is False


def test_long_requires_at_least_one_tp(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("BOOKMAP_ALLOW_TRADING", raising=False)
    db = tmp_path / "sim.db"
    # Missing TP arg -> argparse error (exit 2)
    with pytest.raises(SystemExit):
        pax_manual.main([
            *_common_args(db), "long", "1", "30000.0", "29990.0",
        ])
