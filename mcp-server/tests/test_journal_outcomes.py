"""Phase 5: journal_outcomes backfill + setup_stats tests.

Writes a tiny synthetic journal with a few signals and follow-on
snapshots; runs backfill; verifies forward returns match expected
direction and win-rate aggregation works.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bookmap_mcp.journal import Journal              # noqa: E402
from bookmap_mcp.journal_outcomes import (           # noqa: E402
    backfill,
    setup_stats,
    DEFAULT_HORIZONS_SEC,
    _direction_sign,
)


def _ts(seconds_offset: int) -> int:
    """Synthetic ms timestamp at a fixed base + offset."""
    return 1_700_000_000_000 + seconds_offset * 1000


def _snap_dict(ts_ms: int, mid: float, alias: str = "NQM6") -> dict:
    return {
        "alias": alias, "health": "ok",
        # Cast ms back to ISO for journal._snap_ts_ms() to parse.
        "ts":    _ms_to_iso(ts_ms),
        "book":  {"mid": mid, "bestBid": mid - 0.25, "bestAsk": mid + 0.25,
                   "spread": 0.5},
    }


def _ms_to_iso(ms: int) -> str:
    import datetime as dt
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat()


@pytest.fixture
def journal_with_signals(tmp_path):
    """Build a journal with:
      - One bullish ENTER_LONG_FOLLOW signal at t=0, mid=100.
      - Snapshots ahead: t=5min mid=101, t=10min mid=102, t=30min mid=99.
      - One bearish ENTER_SHORT_FADE signal at t=20min, mid=99.
      - Follow-up: t=25min mid=97, t=50min mid=101.
    """
    db = tmp_path / "j.db"
    j = Journal(db); j.open(); j.begin_run("test")
    # Bullish signal first.
    bull_ts = _ts(0)
    bull_snap = _snap_dict(bull_ts, 100.0)
    bull_snap["or_levels"] = {"levels": [{"label": "OR-H",
                                            "composite":
                                                {"score": 0.6,
                                                 "direction": "FOLLOW_LONG"}}]}
    j.write_snapshot(bull_snap)
    j.write_signal(bull_snap, {
        "decision": "ENTER_LONG_FOLLOW", "size_tier": "FULL",
        "confidence": 0.7, "level_label": "OR-H",
        "entry": 100.0, "components": {}, "reasons": [],
    })
    # Follow-up snapshots for the bullish trade.
    for off_sec, mid in [(300, 101.0), (600, 102.0), (1800, 99.0), (3600, 98.0)]:
        j.write_snapshot(_snap_dict(_ts(off_sec), mid))

    # Bearish signal.
    bear_ts = _ts(1200)   # 20 min in
    bear_snap = _snap_dict(bear_ts, 99.0)
    bear_snap["or_levels"] = {"levels": [{"label": "OR-L",
                                            "composite":
                                                {"score": -0.5,
                                                 "direction": "ENTER_SHORT_FADE"}}]}
    j.write_snapshot(bear_snap)
    j.write_signal(bear_snap, {
        "decision": "ENTER_SHORT_FADE", "size_tier": "HALF",
        "confidence": 0.45, "level_label": "OR-L",
        "entry": 99.0, "components": {}, "reasons": [],
    })
    for off_sec, mid in [(1500, 97.0), (1800, 96.0), (3000, 95.0), (4800, 101.0)]:
        j.write_snapshot(_snap_dict(_ts(off_sec), mid))
    j.end_run()
    j.close()
    return db


def test_direction_sign_mapping():
    assert _direction_sign("ENTER_LONG_FOLLOW") == +1
    assert _direction_sign("ENTER_LONG_FADE") == +1
    assert _direction_sign("ENTER_SHORT_FOLLOW") == -1
    assert _direction_sign("ENTER_SHORT_FADE") == -1
    assert _direction_sign("WAIT") == 0
    assert _direction_sign("") == 0


def test_backfill_writes_outcomes(journal_with_signals):
    result = backfill(journal_with_signals)
    # Two signals × 4 horizons = 8 outcomes.
    assert result["outcomes_written"] == 8
    assert result["signals_scanned"] == 2


def test_outcomes_match_directional_expectation(journal_with_signals):
    """Bullish signal at mid=100; mid at +5m=101, +10m=102 → wins.
    Mid at +30m=99 (below entry) → loss.
    Bearish signal at mid=99; mid at +5m=97 (favorable for short) → win.
    Mid at +30m=95 → win. Mid at +1h (4800s=80min not enough; we have
    samples at offsets 1500=25min, 1800=30min, 3000=50min, 4800=80min). The
    bearish +1h (60min) horizon = bear_ts(1200) + 3600 = 4800s exactly → mid 101 → loss for short."""
    backfill(journal_with_signals)
    stats = setup_stats(journal_with_signals)
    # Build lookup by (decision, level_label, horizon)
    by_key = {(r["decision"], r["level_label"], r["horizon_sec"]): r
               for r in stats}

    # Bullish 5m, 10m wins; 30m, 60m losses.
    assert by_key[("ENTER_LONG_FOLLOW", "OR-H", 300)]["wins"] == 1
    assert by_key[("ENTER_LONG_FOLLOW", "OR-H", 600)]["wins"] == 1
    assert by_key[("ENTER_LONG_FOLLOW", "OR-H", 1800)]["wins"] == 0
    assert by_key[("ENTER_LONG_FOLLOW", "OR-H", 3600)]["wins"] == 0

    # Bearish 5m at offset 1500, mid=97 → return_pts = (97-99)*-1 = +2 → win
    # Bearish 10m at offset 1800, mid=96 → return_pts = (96-99)*-1 = +3 → win
    # Bearish 30m at offset 3000, mid=95 → return_pts = (95-99)*-1 = +4 → win
    # Bearish 60m at offset 4800, mid=101 → return_pts = (101-99)*-1 = -2 → loss
    assert by_key[("ENTER_SHORT_FADE", "OR-L", 300)]["wins"] == 1
    assert by_key[("ENTER_SHORT_FADE", "OR-L", 600)]["wins"] == 1
    assert by_key[("ENTER_SHORT_FADE", "OR-L", 1800)]["wins"] == 1
    assert by_key[("ENTER_SHORT_FADE", "OR-L", 3600)]["wins"] == 0


def test_backfill_idempotent(journal_with_signals):
    """Running backfill twice produces the same outcomes (UPSERT)."""
    r1 = backfill(journal_with_signals)
    r2 = backfill(journal_with_signals)
    assert r1["outcomes_written"] == r2["outcomes_written"]
    # Total rows in outcomes table = 8 (not 16).
    import sqlite3
    c = sqlite3.connect(str(journal_with_signals))
    n = c.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
    c.close()
    assert n == 8


def test_backfill_handles_no_future_snapshot(tmp_path):
    """A signal at the very end of a replay has no future mid for the longer
    horizons. The outcome row is still written; return_pts/win are NULL."""
    db = tmp_path / "j.db"
    j = Journal(db); j.open(); j.begin_run("test")
    sig_ts = _ts(0)
    snap = _snap_dict(sig_ts, 100.0)
    j.write_snapshot(snap)
    j.write_signal(snap, {
        "decision": "ENTER_LONG_FOLLOW", "size_tier": "FULL",
        "confidence": 0.5, "level_label": "OR-H",
        "entry": 100.0, "components": {}, "reasons": [],
    })
    # No follow-up snapshots.
    j.end_run(); j.close()
    r = backfill(db, horizons_sec=(300, 600))
    assert r["outcomes_written"] == 2
    import sqlite3
    c = sqlite3.connect(str(db))
    rows = c.execute("SELECT horizon_sec, return_pts, win FROM outcomes").fetchall()
    c.close()
    # Both horizons should be written but with NULL return/win.
    for h, ret, win in rows:
        assert ret is None
        assert win is None


def test_outcomes_table_exists_in_fresh_journal(tmp_path):
    """The outcomes table is part of the schema, present on first open."""
    db = tmp_path / "fresh.db"
    j = Journal(db); j.open()
    rows = j._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='outcomes'"
        ).fetchall()
    j.close()
    assert rows, "outcomes table missing from schema"
