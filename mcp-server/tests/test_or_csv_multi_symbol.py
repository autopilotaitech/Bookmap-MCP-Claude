"""Per-symbol OR CSV indexing + alias->symbol resolution.

Multiple OpenRange CSVs (one per symbol) must each be read and the latest
row mapped to its `symbol`. The bridge alias is mapped to a symbol via the
/instruments payload, and per-alias OR rows are built from those two.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bookmap_mcp.dashboard as d  # noqa: E402


_HEADER = ("time,symbol,price,orHigh,orLow,cvdDelta,bidDepthDelta,askDepthDelta,"
           "netDepthDelta,cvdZ,psZ,cvdPercentile,psPercentile,location,distanceTicks,"
           "rangeWidth,rangeQuality,ageSeconds,bias,action,confidence,score,maxScore,"
           "evidence,reason")


def _row(symbol: str, or_high: float, or_low: float) -> str:
    return (f"2026-05-19T09:00:00,{symbol},{(or_high+or_low)/2:.2f},"
            f"{or_high:.2f},{or_low:.2f},0,0,0,0,0,0,0,0,IN,0,0,GOOD,30,"
            f"NEUTRAL,WAIT,LOW,0,1,\"\",\"\"")


def _write_csv(path: Path, symbol: str, or_high: float, or_low: float) -> None:
    path.write_text(_HEADER + "\n" + _row(symbol, or_high, or_low) + "\n",
                    encoding="utf-8")


# ── _or_rows_by_symbol ────────────────────────────────────────────────────

def test_or_rows_by_symbol_indexes_each_csv(tmp_path, monkeypatch):
    nq_csv = tmp_path / "openrange-signals-NQM6.csv"
    es_csv = tmp_path / "openrange-signals-ESM6.csv"
    _write_csv(nq_csv, "NQM6", 21500.00, 21400.00)
    _write_csv(es_csv, "ESM6",  5900.00,  5870.00)
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])

    out = d._or_rows_by_symbol()
    assert set(out.keys()) == {"NQM6", "ESM6"}
    assert float(out["NQM6"]["orHigh"]) == pytest.approx(21500.00)
    assert float(out["NQM6"]["orLow"]) == pytest.approx(21400.00)
    assert float(out["ESM6"]["orHigh"]) == pytest.approx(5900.00)
    assert float(out["ESM6"]["orLow"]) == pytest.approx(5870.00)
    assert out["NQM6"]["_csv_path"].endswith("openrange-signals-NQM6.csv")
    assert out["ESM6"]["_csv_path"].endswith("openrange-signals-ESM6.csv")


def test_or_rows_by_symbol_returns_empty_when_no_files(tmp_path, monkeypatch):
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])
    assert d._or_rows_by_symbol() == {}


def test_or_rows_by_symbol_picks_latest_row_per_symbol(tmp_path, monkeypatch):
    nq_csv = tmp_path / "openrange-signals-NQM6.csv"
    body = _HEADER + "\n"
    body += _row("NQM6", 21500.0, 21400.0) + "\n"
    body += _row("NQM6", 21525.0, 21425.0) + "\n"
    nq_csv.write_text(body, encoding="utf-8")
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])
    out = d._or_rows_by_symbol()
    assert float(out["NQM6"]["orHigh"]) == pytest.approx(21525.0)


def test_or_rows_by_symbol_skips_header_only_files(tmp_path, monkeypatch):
    good = tmp_path / "openrange-signals-NQM6.csv"
    bad  = tmp_path / "openrange-signals-CORRUPT.csv"
    _write_csv(good, "NQM6", 21500.0, 21400.0)
    bad.write_text(_HEADER + "\n", encoding="utf-8")
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])
    out = d._or_rows_by_symbol()
    assert set(out.keys()) == {"NQM6"}


def test_or_rows_by_symbol_skips_rows_missing_symbol_column(tmp_path, monkeypatch):
    """A CSV row with no symbol value must be dropped, not indexed under
    the empty string."""
    csv_path = tmp_path / "openrange-signals-mystery.csv"
    body = _HEADER + "\n"
    body += _row("", 21500.0, 21400.0) + "\n"
    csv_path.write_text(body, encoding="utf-8")
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])
    assert d._or_rows_by_symbol() == {}


def test_or_latest_row_still_returns_newest_row(tmp_path, monkeypatch):
    """Back-compat: or_latest_row() still returns SOME row when CSVs exist.
    The exact row chosen depends on mtime ordering, which we don't pin here
    (mtime granularity on Windows is filesystem-dependent)."""
    nq_csv = tmp_path / "openrange-signals-NQM6.csv"
    es_csv = tmp_path / "openrange-signals-ESM6.csv"
    _write_csv(nq_csv, "NQM6", 21500.0, 21400.0)
    _write_csv(es_csv, "ESM6", 5900.0, 5870.0)
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])
    row = d.or_latest_row()
    assert row is not None
    assert row["symbol"] in {"NQM6", "ESM6"}
    assert "orHigh" in row and "orLow" in row


def test_or_latest_row_none_when_no_files(tmp_path, monkeypatch):
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])
    assert d.or_latest_row() is None


# ── _resolve_alias_symbol ─────────────────────────────────────────────────

def test_resolve_alias_symbol_returns_symbol_from_instruments():
    instruments = {"instruments": [
        {"alias": "NQM6.CME@RITHMIC", "symbol": "NQM6", "fullName": "Nasdaq E-mini"},
        {"alias": "ESM6.CME@RITHMIC", "symbol": "ESM6", "fullName": "S&P 500 E-mini"},
    ]}
    assert d._resolve_alias_symbol("NQM6.CME@RITHMIC", instruments) == "NQM6"
    assert d._resolve_alias_symbol("ESM6.CME@RITHMIC", instruments) == "ESM6"


def test_resolve_alias_symbol_missing_alias_returns_none():
    instruments = {"instruments": [
        {"alias": "NQM6.CME@RITHMIC", "symbol": "NQM6"},
    ]}
    assert d._resolve_alias_symbol("BTCUSDT@COINBASE", instruments) is None


def test_resolve_alias_symbol_handles_missing_payload():
    assert d._resolve_alias_symbol("NQM6.CME@RITHMIC", None) is None
    assert d._resolve_alias_symbol("NQM6.CME@RITHMIC", {}) is None
    assert d._resolve_alias_symbol("NQM6.CME@RITHMIC", {"instruments": []}) is None


def test_resolve_alias_symbol_handles_missing_symbol_field():
    instruments = {"instruments": [
        {"alias": "NQM6.CME@RITHMIC"},  # no symbol field
    ]}
    assert d._resolve_alias_symbol("NQM6.CME@RITHMIC", instruments) is None


def test_resolve_alias_symbol_handles_blank_symbol():
    instruments = {"instruments": [
        {"alias": "NQM6.CME@RITHMIC", "symbol": "   "},
    ]}
    assert d._resolve_alias_symbol("NQM6.CME@RITHMIC", instruments) is None


def test_resolve_alias_symbol_exact_match_only():
    """A prefix match (NQ vs NQM6) must NOT resolve — exact symbol only."""
    instruments = {"instruments": [
        {"alias": "NQM6.CME@RITHMIC", "symbol": "NQM6"},
    ]}
    # Wrong alias entirely — must not match by prefix.
    assert d._resolve_alias_symbol("NQ.CME@RITHMIC", instruments) is None


# ── _build_or_rows_by_alias ───────────────────────────────────────────────

def test_build_or_rows_by_alias_maps_each_alias_to_its_symbol_row(tmp_path, monkeypatch):
    nq_csv = tmp_path / "openrange-signals-NQM6.csv"
    es_csv = tmp_path / "openrange-signals-ESM6.csv"
    _write_csv(nq_csv, "NQM6", 21500.0, 21400.0)
    _write_csv(es_csv, "ESM6", 5900.0, 5870.0)
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])

    instruments = {"instruments": [
        {"alias": "NQM6.CME@RITHMIC", "symbol": "NQM6"},
        {"alias": "ESM6.CME@RITHMIC", "symbol": "ESM6"},
    ]}
    out = d._build_or_rows_by_alias(
        ["NQM6.CME@RITHMIC", "ESM6.CME@RITHMIC"], instruments)

    assert set(out.keys()) == {"NQM6.CME@RITHMIC", "ESM6.CME@RITHMIC"}
    assert float(out["NQM6.CME@RITHMIC"]["orHigh"]) == pytest.approx(21500.0)
    assert float(out["ESM6.CME@RITHMIC"]["orHigh"]) == pytest.approx(5900.0)


def test_build_or_rows_by_alias_missing_csv_yields_none(tmp_path, monkeypatch):
    """An alias whose symbol has no OR CSV maps to None (not crash)."""
    nq_csv = tmp_path / "openrange-signals-NQM6.csv"
    _write_csv(nq_csv, "NQM6", 21500.0, 21400.0)
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])

    instruments = {"instruments": [
        {"alias": "NQM6.CME@RITHMIC", "symbol": "NQM6"},
        {"alias": "MESM6.CME@RITHMIC", "symbol": "MESM6"},  # no CSV
    ]}
    out = d._build_or_rows_by_alias(
        ["NQM6.CME@RITHMIC", "MESM6.CME@RITHMIC"], instruments)

    assert out["NQM6.CME@RITHMIC"] is not None
    assert out["MESM6.CME@RITHMIC"] is None


def test_build_or_rows_by_alias_missing_instruments_returns_none_for_each(tmp_path, monkeypatch):
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])
    out = d._build_or_rows_by_alias(["NQM6.CME@RITHMIC"], None)
    assert out == {"NQM6.CME@RITHMIC": None}
