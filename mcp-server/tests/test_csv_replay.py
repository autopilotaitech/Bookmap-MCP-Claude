"""Phase 2: CsvReplayAdapter tests.

Drives a small synthetic CSV through the adapter, verifies sequencing,
synthesis flags, and that the resulting snapshot is schema-valid and
consumable by signal_engine.compute_or_levels without crashing.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bookmap_mcp.adapters import CsvReplayAdapter   # noqa: E402
from bookmap_mcp.adapters.base import DataAdapter   # noqa: E402
from bookmap_mcp.snapshot import is_valid           # noqa: E402


@pytest.fixture
def sample_csv(tmp_path) -> Path:
    """A tiny replay CSV with mid + or levels + vwap + regime — exercises
    most adapter mappings."""
    p = tmp_path / "replay.csv"
    rows = [
        ["ts_iso",                    "alias", "mid",     "bid",     "ask",
         "or_high", "or_low", "vwap",   "vwap_stddev", "regime",       "bias_score"],
        ["2026-05-18T13:30:00+00:00", "NQM6",  "20050.0", "20049.75","20050.25",
         "20100.0", "20000.0","20040.0","8.0",         "TRENDING_UP",  "0.5"],
        ["2026-05-18T13:30:01+00:00", "NQM6",  "20050.5", "20050.25","20050.75",
         "20100.0", "20000.0","20040.5","8.0",         "TRENDING_UP",  "0.55"],
        ["2026-05-18T13:30:02+00:00", "NQM6",  "20049.5", "20049.25","20049.75",
         "20100.0", "20000.0","20040.5","8.0",         "TRENDING_UP",  "0.40"],
    ]
    with open(p, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        for r in rows:
            w.writerow(r)
    return p


def test_adapter_satisfies_protocol(sample_csv):
    """Runtime-checkable: CsvReplayAdapter walks like a DataAdapter."""
    a = CsvReplayAdapter(sample_csv)
    assert isinstance(a, DataAdapter)
    assert a.name == "csv_replay"


def test_adapter_emits_rows_in_sequence(sample_csv):
    a = CsvReplayAdapter(sample_csv, alias="NQM6")
    a.start()
    snaps = []
    while True:
        s = a.next_snapshot()
        if s is None: break
        snaps.append(s)
    a.stop()
    assert len(snaps) == 3
    mids = [s["book"]["mid"] for s in snaps]
    assert mids == [20050.0, 20050.5, 20049.5]


def test_adapter_eof_health_reports_row_count(sample_csv):
    a = CsvReplayAdapter(sample_csv)
    a.start()
    while a.next_snapshot() is not None:
        pass
    h = a.health()
    assert h.status == "eof"
    assert h.snapshots_emitted == 3


def test_adapter_snapshot_is_schema_valid(sample_csv):
    a = CsvReplayAdapter(sample_csv)
    a.start()
    s = a.next_snapshot()
    a.stop()
    assert is_valid(s), f"snap not valid: errors observed"


def test_adapter_synthesizes_trades_when_mid_moves(sample_csv):
    """First row has no prior mid, so no trade. Subsequent rows synthesize
    a print on every mid change, side from direction."""
    a = CsvReplayAdapter(sample_csv)
    a.start()
    s1 = a.next_snapshot()
    s2 = a.next_snapshot()  # mid up from 20050.0 → 20050.5
    s3 = a.next_snapshot()  # mid down from 20050.5 → 20049.5
    a.stop()
    assert s1["trades"] == []
    assert len(s2["trades"]) == 1
    assert s2["trades"][0]["side"] == "buy"
    assert s2["trades"][0]["price"] == 20050.5
    assert "trades" in s2["_synthetic"]
    assert len(s3["trades"]) == 1
    assert s3["trades"][0]["side"] == "sell"


def test_adapter_records_synthesized_book_fields(tmp_path):
    """CSV with only mid; bid/ask synthesized from mid ± half-tick."""
    p = tmp_path / "midonly.csv"
    with open(p, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ts_iso", "alias", "mid"])
        w.writerow(["2026-05-18T13:30:00+00:00", "TEST", "100.0"])
    a = CsvReplayAdapter(p)
    a.start()
    s = a.next_snapshot()
    a.stop()
    assert s["book"]["bestBid"] == 100.0 - 0.125
    assert s["book"]["bestAsk"] == 100.0 + 0.125
    assert s["book"]["mid"] == 100.0
    assert "book.bestBid" in s["_synthetic"]
    assert "book.bestAsk" in s["_synthetic"]


def test_adapter_omits_or_row_when_or_levels_missing(tmp_path):
    """Missing or_high/or_low → or_row is None; downstream compute_or_levels
    returns None without crashing."""
    p = tmp_path / "no_or.csv"
    with open(p, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ts_iso", "alias", "mid"])
        w.writerow(["2026-05-18T13:30:00+00:00", "TEST", "100.0"])
    a = CsvReplayAdapter(p)
    a.start()
    s = a.next_snapshot()
    a.stop()
    assert s["or_row"] is None

    # Feed through signal_engine — must not raise.
    from bookmap_mcp.signal_engine import compute_or_levels
    assert compute_or_levels(s) is None   # no or_row → no levels


def test_adapter_health_pre_start_reports_zero(sample_csv):
    """Before start(), adapter is idle but consistent."""
    a = CsvReplayAdapter(sample_csv)
    h = a.health()
    assert h.snapshots_emitted == 0
    assert h.last_snapshot_ms == 0


def test_adapter_start_missing_file_raises(tmp_path):
    a = CsvReplayAdapter(tmp_path / "does_not_exist.csv")
    with pytest.raises(FileNotFoundError):
        a.start()


def test_adapter_snap_drives_through_signal_engine(sample_csv):
    """End-to-end: replay snap → signal_engine.compute_tape_flow → no
    exception. Returns None because tape_buckets is missing; that's fine."""
    a = CsvReplayAdapter(sample_csv)
    a.start()
    s = a.next_snapshot()
    a.stop()
    from bookmap_mcp.signal_engine import compute_tape_flow
    # tape_buckets is absent from the CSV → compute_tape_flow returns None.
    assert compute_tape_flow(s) is None


def test_adapter_alias_falls_back_to_constructor_default(tmp_path):
    """When CSV has no alias column, default from __init__ wins."""
    p = tmp_path / "no_alias.csv"
    with open(p, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ts_iso", "mid"])
        w.writerow(["2026-05-18T13:30:00+00:00", "100.0"])
    a = CsvReplayAdapter(p, alias="MYDEFAULT")
    a.start()
    s = a.next_snapshot()
    a.stop()
    assert s["alias"] == "MYDEFAULT"
