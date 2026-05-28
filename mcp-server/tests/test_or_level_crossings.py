"""Unit tests for or_level_crossings module.

Pins per-level crossing detection (UP/DOWN, hysteresis), prior-level
context attachment, event count aggregation, CSV schema, and graceful
handling of missing fields.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from bookmap_mcp import or_level_crossings as olc


_ALIAS = "NQM6.CME@RITHMIC"


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path):
    csv_path = tmp_path / "or-level-crossings.csv"
    prior = olc._override_paths(csv_path)
    olc.reset_state()
    yield csv_path
    olc._override_paths(prior)
    olc.reset_state()


def _snap(
    *,
    ts_ms: int,
    mid: float,
    levels=(("OR-L", 30058.25, "below"), ("OR-H", 30105.75, "above"),
            ("+1", 30170.75, "above"), ("+2", 30235.75, "above")),
    regime: str = "BALANCED",
    vote: float = 0.0,
    conviction: float = 0.0,
    trend_filter: str = "NEUTRAL",
    micro_signed: float = 0.0,
    micro_events=(),
) -> dict:
    return {
        "ts_ms": ts_ms,
        "alias": _ALIAS,
        "book": {"mid": mid},
        "or_levels": {
            "orHigh": 30105.75,
            "orLow": 30058.25,
            "levels": [{"label": l, "price": p, "side": s} for l, p, s in levels],
        },
        "institutional_flow": {
            "regime": regime,
            "weighted_vote": vote,
            "conviction": conviction,
            "trend_filter": trend_filter,
            "drivers": [{"name": "micro_events", "signed": micro_signed}],
        },
        "micro_events": {"events": list(micro_events)},
    }


def test_no_crossing_on_first_tick(_isolate):
    out = olc.update_or_level_crossings(_snap(ts_ms=1_000, mid=30100.0))
    assert out["recent"] == []


def test_up_crossing_detected(_isolate):
    olc.update_or_level_crossings(_snap(ts_ms=1_000, mid=30100.0))   # below +1
    out = olc.update_or_level_crossings(_snap(ts_ms=2_000, mid=30180.0))  # above +1
    assert len(out["recent"]) >= 1
    last = out["recent"][-1]
    assert last["level"] == "+1"
    assert last["direction"] == "UP"


def test_down_crossing_detected(_isolate):
    olc.update_or_level_crossings(_snap(ts_ms=1_000, mid=30180.0))   # above +1
    out = olc.update_or_level_crossings(_snap(ts_ms=2_000, mid=30100.0))  # below +1
    assert len(out["recent"]) >= 1
    last = out["recent"][-1]
    assert last["level"] == "+1"
    assert last["direction"] == "DOWN"


def test_no_double_fire_on_hover_above(_isolate):
    """Hovering above an already-crossed level produces no new crossing."""
    olc.update_or_level_crossings(_snap(ts_ms=1_000, mid=30180.0))   # initial: above +1, below +2
    count1 = len(olc.update_or_level_crossings(_snap(ts_ms=2_000, mid=30190.0))["recent"])
    out = olc.update_or_level_crossings(_snap(ts_ms=3_000, mid=30210.0))  # still above +1
    count2 = len(out["recent"])
    # No new crossing between these — same side of every level.
    assert count1 == 0
    assert count2 == 0


def test_multiple_crossings_on_gap(_isolate):
    """Price gaps from below OR-L to above +1 in a single tick -> records
    crossings for OR-L, OR-H, and +1, in path-order."""
    olc.update_or_level_crossings(_snap(ts_ms=1_000, mid=30000.0))   # below everything
    out = olc.update_or_level_crossings(_snap(ts_ms=2_000, mid=30200.0))  # above +1
    labels = [r["level"] for r in out["recent"]]
    assert "OR-L" in labels
    assert "OR-H" in labels
    assert "+1" in labels


def test_prior_level_context_attached(_isolate):
    olc.update_or_level_crossings(_snap(ts_ms=1_000, mid=30100.0))
    olc.update_or_level_crossings(_snap(ts_ms=2_000, mid=30180.0))   # crosses +1
    out = olc.update_or_level_crossings(_snap(ts_ms=12_000, mid=30240.0))  # crosses +2
    last = out["recent"][-1]
    assert last["level"] == "+2"
    # Prior crossing was +1 at ts=2000; current is +2 at ts=12000 -> 10s since
    assert last["secs_since_prior"] == 10


def test_pts_per_sec_computed(_isolate):
    olc.update_or_level_crossings(_snap(ts_ms=1_000, mid=30100.0))
    olc.update_or_level_crossings(_snap(ts_ms=2_000, mid=30180.0))  # +1 at ts 2
    out = olc.update_or_level_crossings(_snap(ts_ms=12_000, mid=30240.0))  # +2 at ts 12
    last = out["recent"][-1]
    # ~60 pts in 10 sec
    assert last["pts_per_sec"] is not None and last["pts_per_sec"] > 5.0


def test_event_counts_aggregated(_isolate):
    ts = 1_000
    snap1 = _snap(ts_ms=ts, mid=30100.0)
    olc.update_or_level_crossings(snap1)
    snap2 = _snap(
        ts_ms=ts + 5_000,
        mid=30180.0,
        micro_events=[
            {"kind": "ICEBERG", "timeMs": ts + 4_000, "isBid": True, "price": 30170.0, "size": 100},
            {"kind": "ICEBERG", "timeMs": ts + 4_500, "isBid": True, "price": 30171.0, "size": 100},
            {"kind": "ABSORPTION", "timeMs": ts + 4_800, "isBid": True, "price": 30172.0, "size": 100},
            {"kind": "SPOOF", "timeMs": ts + 4_900, "isBid": False, "price": 30178.0, "size": 30},
        ],
    )
    out = olc.update_or_level_crossings(snap2)
    last = out["recent"][-1]
    counts = last["event_counts"]
    assert counts["ICEBERG"] == 2
    assert counts["ABSORPTION"] == 1
    assert counts["SPOOF"] == 1


def test_csv_written_on_crossing(_isolate):
    csv_path = _isolate
    olc.update_or_level_crossings(_snap(ts_ms=1_000, mid=30180.0))   # initial: above +1
    olc.update_or_level_crossings(_snap(ts_ms=2_000, mid=30240.0))   # crosses +2
    assert csv_path.exists()
    rows = list(csv.reader(csv_path.open()))
    assert rows[0] == list(olc._HEADER)
    assert len(rows) == 2  # header + 1 row (+2 crossed)
    # Confirm the row is the +2 crossing
    label_idx = list(olc._HEADER).index("level_label")
    assert rows[1][label_idx] == "+2"


def test_micro_signed_captured(_isolate):
    olc.update_or_level_crossings(_snap(ts_ms=1_000, mid=30100.0))
    out = olc.update_or_level_crossings(
        _snap(ts_ms=2_000, mid=30180.0, regime="ACCUMULATION",
              vote=0.45, conviction=0.9, trend_filter="ALIGNED",
              micro_signed=0.85)
    )
    last = out["recent"][-1]
    assert last["regime"] == "ACCUMULATION"
    assert last["micro_signed"] == 0.85
    assert last["trend_filter"] == "ALIGNED"


def test_recent_capped_at_max(_isolate):
    """The in-memory `recent` list is capped at _MAX_RECENT."""
    olc.update_or_level_crossings(_snap(ts_ms=1_000, mid=30100.0))
    # Oscillate up/down across +1 many times
    for i in range(15):
        olc.update_or_level_crossings(
            _snap(ts_ms=2_000 + i * 1000, mid=30180.0 + (1 if i % 2 == 0 else -100))
        )
    out = olc.update_or_level_crossings(_snap(ts_ms=20_000, mid=30200.0))
    assert len(out["recent"]) <= olc._MAX_RECENT


def test_graceful_on_missing_fields(_isolate):
    out = olc.update_or_level_crossings({})
    assert out["recent"] == []
