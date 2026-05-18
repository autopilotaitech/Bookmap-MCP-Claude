"""Phase 2: snapshot schema validator tests."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bookmap_mcp.snapshot import (    # noqa: E402
    REQUIRED_KEYS,
    is_valid,
    validate_snapshot,
)


def _minimal_valid_snap():
    return {
        "alias": "TEST",
        "health": "ok",
        "book": {"bestBid": 99.75, "bestAsk": 100.25, "mid": 100.0,
                  "spread": 0.5},
        "trades": [],
        "_source": "csv_replay",
    }


def test_minimal_valid_snap_has_no_errors():
    assert validate_snapshot(_minimal_valid_snap()) == []
    assert is_valid(_minimal_valid_snap())


def test_each_required_key_individually_required():
    """Drop each required key one at a time → validator complains about it."""
    for k in REQUIRED_KEYS:
        snap = _minimal_valid_snap()
        snap.pop(k)
        errors = validate_snapshot(snap)
        assert any(k in e for e in errors), (
            f"removing {k} should produce an error mentioning {k}; got {errors}")


def test_non_dict_input_is_rejected():
    assert validate_snapshot(None) != []
    assert validate_snapshot("not a dict") != []
    assert validate_snapshot([]) != []


def test_book_must_be_dict_or_none():
    snap = _minimal_valid_snap()
    snap["book"] = "not a dict"
    errors = validate_snapshot(snap)
    assert any("book must be dict" in e for e in errors)


def test_book_must_have_mid():
    snap = _minimal_valid_snap()
    snap["book"] = {"bestBid": 99.0, "bestAsk": 100.0}
    errors = validate_snapshot(snap)
    assert any("book.mid" in e for e in errors)


def test_trades_must_be_list():
    snap = _minimal_valid_snap()
    snap["trades"] = "not a list"
    errors = validate_snapshot(snap)
    assert any("trades must be list" in e for e in errors)


def test_synthetic_must_be_list_of_strings():
    snap = _minimal_valid_snap()
    snap["_synthetic"] = "not a list"
    assert any("_synthetic must be list" in e
                for e in validate_snapshot(snap))
    snap["_synthetic"] = [1, 2]
    assert any("only strings" in e for e in validate_snapshot(snap))
    snap["_synthetic"] = ["book.mid", "trades"]
    assert validate_snapshot(snap) == []
