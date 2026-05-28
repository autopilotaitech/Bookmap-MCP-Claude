"""Unit tests for ifl_outcomes module.

Pins episode open/close logic, mid-delta-based verdict computation,
and CSV persistence on episode close.

Verdict logic (mid-delta based, not rotation_state.rotations_completed):
- HIT  : peak favorable mid delta from start >= 1 rotation unit
        (NQ=65pt). The regime call's direction was right.
- MISS : episode closed by opposite-direction regime, peak favorable
        delta did not reach a rotation unit.
- STALE: episode drifted into BALANCED/TRANSITION for 180s+ with no
        favorable advance.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from bookmap_mcp import ifl_outcomes as ifl


_ALIAS = "NQM6.CME@RITHMIC"


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path):
    csv_path = tmp_path / "ifl-outcomes.csv"
    prior = ifl._override_paths(csv_path)
    ifl.reset_state()
    yield csv_path
    ifl._override_paths(prior)
    ifl.reset_state()


def _snap(ts_ms: int, regime: str, mid: float, conv: float = 0.6,
          commit_price: float = 30100.0,
          commit_level: str = "OR-L") -> dict:
    return {
        "ts_ms": ts_ms,
        "alias": _ALIAS,
        "book": {"mid": mid},
        "institutional_flow": {
            "alias": _ALIAS,
            "regime": regime,
            "conviction": conv,
            "rotation_state": {
                "commit_level": commit_level,
                "commit_price": commit_price,
            },
        },
    }


def test_no_active_when_balanced(_isolate):
    view = ifl.update_ifl_outcomes(_snap(ts_ms=1, regime="BALANCED", mid=30000.0))
    assert view["active"] is None
    assert view["last_closed"] is None


def test_opens_episode_on_distribution(_isolate):
    view = ifl.update_ifl_outcomes(_snap(ts_ms=1, regime="DISTRIBUTION", mid=30050.0))
    assert view["active"] is not None
    assert view["active"]["regime"] == "DISTRIBUTION"
    assert view["active"]["start_mid"] == 30050.0
    assert view["active"]["peak_favorable_pts"] == 0.0


def test_open_episode_tracks_favorable_advance_short(_isolate):
    ifl.update_ifl_outcomes(_snap(ts_ms=1, regime="DISTRIBUTION", mid=30050.0))
    # Price drops 70 pts (favorable for short)
    v = ifl.update_ifl_outcomes(_snap(ts_ms=10_000, regime="DISTRIBUTION", mid=29980.0))
    assert v["active"]["peak_favorable_pts"] == 70.0
    assert v["active"]["rungs_advanced_so_far"] == 1


def test_open_episode_tracks_favorable_advance_long(_isolate):
    ifl.update_ifl_outcomes(_snap(ts_ms=1, regime="ACCUMULATION", mid=30050.0))
    # Price rises 70 pts (favorable for long)
    v = ifl.update_ifl_outcomes(_snap(ts_ms=10_000, regime="ACCUMULATION", mid=30120.0))
    assert v["active"]["peak_favorable_pts"] == 70.0
    assert v["active"]["rungs_advanced_so_far"] == 1


def test_adverse_move_recorded_not_favorable(_isolate):
    """Bug fix: if regime is DISTRIBUTION but price moves UP, it should NOT count as advance."""
    ifl.update_ifl_outcomes(_snap(ts_ms=1, regime="DISTRIBUTION", mid=30050.0))
    # Price RISES 70 pts (adverse for short DISTRIBUTION call)
    v = ifl.update_ifl_outcomes(_snap(ts_ms=10_000, regime="DISTRIBUTION", mid=30120.0))
    assert v["active"]["peak_favorable_pts"] == 0.0
    assert v["active"]["max_adverse_pts"] == -70.0
    assert v["active"]["rungs_advanced_so_far"] == 0


def test_flip_to_opposite_after_favorable_is_hit(_isolate):
    csv_path = _isolate
    ifl.update_ifl_outcomes(_snap(ts_ms=1, regime="DISTRIBUTION", mid=30050.0))
    ifl.update_ifl_outcomes(_snap(ts_ms=5_000, regime="DISTRIBUTION", mid=29980.0))  # +70 favorable
    # Flip to ACCUMULATION
    v = ifl.update_ifl_outcomes(_snap(ts_ms=10_000, regime="ACCUMULATION", mid=29990.0))
    assert v["last_closed"]["verdict"] == "HIT"
    assert v["last_closed"]["rungs_advanced"] == 1
    # New episode opens
    assert v["active"]["regime"] == "ACCUMULATION"
    rows = list(csv.reader(csv_path.open()))
    assert len(rows) == 2  # header + 1 row


def test_flip_to_opposite_without_favorable_is_miss(_isolate):
    """Bug-fix coverage: DISTRIBUTION call that goes adverse (price up) closes as MISS."""
    ifl.update_ifl_outcomes(_snap(ts_ms=1, regime="DISTRIBUTION", mid=30050.0))
    # Price went UP 60 pts during DISTRIBUTION call -- not even close to favorable
    ifl.update_ifl_outcomes(_snap(ts_ms=5_000, regime="DISTRIBUTION", mid=30110.0))
    v = ifl.update_ifl_outcomes(_snap(ts_ms=10_000, regime="ACCUMULATION", mid=30115.0))
    assert v["last_closed"]["verdict"] == "MISS"
    assert v["last_closed"]["rungs_advanced"] == 0
    assert v["last_closed"]["max_adverse_pts"] == -65.0  # most adverse


def test_balanced_for_long_period_closes_as_stale_when_no_advance(_isolate):
    ifl.update_ifl_outcomes(_snap(ts_ms=1, regime="DISTRIBUTION", mid=30050.0))
    ifl.update_ifl_outcomes(_snap(ts_ms=30_000, regime="BALANCED", mid=30055.0))
    v = ifl.update_ifl_outcomes(_snap(ts_ms=220_000, regime="BALANCED", mid=30060.0))
    assert v["active"] is None
    assert v["last_closed"]["verdict"] == "STALE"


def test_balanced_for_long_period_closes_as_hit_when_advanced(_isolate):
    ifl.update_ifl_outcomes(_snap(ts_ms=1, regime="DISTRIBUTION", mid=30050.0))
    ifl.update_ifl_outcomes(_snap(ts_ms=20_000, regime="DISTRIBUTION", mid=29980.0))  # +70 favorable
    ifl.update_ifl_outcomes(_snap(ts_ms=30_000, regime="BALANCED", mid=29985.0))
    v = ifl.update_ifl_outcomes(_snap(ts_ms=230_000, regime="BALANCED", mid=29990.0))
    assert v["active"] is None
    assert v["last_closed"]["verdict"] == "HIT"
    assert v["last_closed"]["rungs_advanced"] == 1


def test_balanced_brief_then_back_to_regime_does_not_close(_isolate):
    ifl.update_ifl_outcomes(_snap(ts_ms=1, regime="DISTRIBUTION", mid=30050.0))
    ifl.update_ifl_outcomes(_snap(ts_ms=10_000, regime="BALANCED", mid=30055.0))
    v = ifl.update_ifl_outcomes(_snap(ts_ms=40_000, regime="DISTRIBUTION", mid=29990.0))
    assert v["active"] is not None
    assert v["active"]["regime"] == "DISTRIBUTION"
    assert v["active"]["peak_favorable_pts"] == 60.0


def test_csv_header_written_on_first_row(_isolate):
    csv_path = _isolate
    ifl.update_ifl_outcomes(_snap(ts_ms=1, regime="DISTRIBUTION", mid=30050.0))
    ifl.update_ifl_outcomes(_snap(ts_ms=5_000, regime="ACCUMULATION", mid=30055.0))
    assert csv_path.exists()
    rows = list(csv.reader(csv_path.open()))
    assert rows[0] == list(ifl._HEADER)


def test_peak_favorable_only_updates_in_regime_direction(_isolate):
    """DISTRIBUTION = short. Peak favorable tracks LOWEST mid since start."""
    ifl.update_ifl_outcomes(_snap(ts_ms=1, regime="DISTRIBUTION", mid=30050.0))
    ifl.update_ifl_outcomes(_snap(ts_ms=5_000, regime="DISTRIBUTION", mid=29980.0))  # +70 favorable
    v = ifl.update_ifl_outcomes(_snap(ts_ms=8_000, regime="DISTRIBUTION", mid=29995.0))  # bounced
    # Peak favorable stays at 70 -- doesn't regress when mid bounces
    assert v["active"]["peak_favorable_pts"] == 70.0


def test_graceful_when_flow_missing(_isolate):
    v = ifl.update_ifl_outcomes({"ts_ms": 1, "alias": _ALIAS})
    assert v == {"active": None, "last_closed": None}


def test_sustained_hit_when_two_rungs_advanced(_isolate):
    """2+ rungs advanced = SUSTAINED_HIT (operator's runner profitable)."""
    ifl.update_ifl_outcomes(_snap(ts_ms=1, regime="ACCUMULATION", mid=30000.0))
    ifl.update_ifl_outcomes(_snap(ts_ms=5_000, regime="ACCUMULATION", mid=30140.0))  # +140 fav
    v = ifl.update_ifl_outcomes(_snap(ts_ms=10_000, regime="DISTRIBUTION", mid=30140.0))
    assert v["last_closed"]["verdict"] == "SUSTAINED_HIT"
    assert v["last_closed"]["rungs_advanced"] >= 2


def test_partial_hit_when_half_rotation_without_full_rung(_isolate):
    """Half-rotation favorable but no full rung = PARTIAL_HIT (contract #1
    stop-coverage met even though rotation didn't complete)."""
    ifl.update_ifl_outcomes(_snap(ts_ms=1, regime="DISTRIBUTION", mid=30100.0))
    ifl.update_ifl_outcomes(_snap(ts_ms=5_000, regime="DISTRIBUTION", mid=30060.0))  # +40 fav (no full rung)
    v = ifl.update_ifl_outcomes(_snap(ts_ms=10_000, regime="ACCUMULATION", mid=30070.0))
    # Opposite flip but peak_fav >= 32.5 (half of 65) -> PARTIAL_HIT, not MISS
    assert v["last_closed"]["verdict"] == "PARTIAL_HIT"
    assert v["last_closed"]["rungs_advanced"] == 0


def test_miss_when_no_favorable_and_opposite_flip(_isolate):
    """No favorable move + opposite regime flip = MISS (unchanged from prior)."""
    ifl.update_ifl_outcomes(_snap(ts_ms=1, regime="DISTRIBUTION", mid=30050.0))
    ifl.update_ifl_outcomes(_snap(ts_ms=5_000, regime="DISTRIBUTION", mid=30110.0))  # adverse only
    v = ifl.update_ifl_outcomes(_snap(ts_ms=10_000, regime="ACCUMULATION", mid=30115.0))
    assert v["last_closed"]["verdict"] == "MISS"


def test_hit_at_exactly_one_rung(_isolate):
    """Exactly 1 rung = HIT (operator's TP1 hits)."""
    ifl.update_ifl_outcomes(_snap(ts_ms=1, regime="ACCUMULATION", mid=30000.0))
    ifl.update_ifl_outcomes(_snap(ts_ms=5_000, regime="ACCUMULATION", mid=30070.0))  # +70 = 1 rung
    v = ifl.update_ifl_outcomes(_snap(ts_ms=10_000, regime="DISTRIBUTION", mid=30060.0))
    assert v["last_closed"]["verdict"] == "HIT"
    assert v["last_closed"]["rungs_advanced"] == 1


def test_es_alias_uses_15pt_rotation(_isolate):
    """Confirm ES uses 15pt rotation unit instead of NQ's 65pt."""
    snap = {
        "ts_ms": 1,
        "alias": "ESM6.CME@RITHMIC",
        "book": {"mid": 5000.0},
        "institutional_flow": {
            "alias": "ESM6.CME@RITHMIC",
            "regime": "ACCUMULATION",
            "conviction": 0.6,
            "rotation_state": {"commit_level": "OR-H", "commit_price": 4990.0},
        },
    }
    ifl.update_ifl_outcomes(snap)
    # +15 pts favorable on ES = 1 rung
    snap2 = dict(snap)
    snap2["ts_ms"] = 5_000
    snap2["book"] = {"mid": 5015.0}
    v = ifl.update_ifl_outcomes(snap2)
    assert v["active"]["rungs_advanced_so_far"] == 1
