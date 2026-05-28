"""Unit tests for or_day_ledger module.

Pins session detection, rotation tally, extreme tracking, whipsaw count,
CSV write format, and graceful degrade on missing fields. Tests write to a
pytest tmp_path; production paths under D:\\BookmapLogs are never touched.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict

import pytest

from bookmap_mcp import or_day_ledger as ledger


_ALIAS = "NQM6.CME@RITHMIC"


@pytest.fixture(autouse=True)
def _isolate_paths(tmp_path: Path):
    csv_path = tmp_path / "or-day-ledger.csv"
    json_path = tmp_path / "or-day-ledger-current.json"
    prior_csv, prior_json = ledger._override_paths(csv_path, json_path)
    ledger.reset_state()
    yield csv_path, json_path
    ledger._override_paths(prior_csv, prior_json)
    ledger.reset_state()


def _make_snap(
    *,
    ts_ms: int,
    mid: float,
    or_high: float = 30050.0,
    or_low: float = 29950.0,
    anchor_hhmm: str = "17:00",
    anchor_tz: str = "America/Chicago",
    anchor_mode: str = "LIVE",
    anchor_updated_at_ms: int = 1_000_000_000,
    range_seconds: int = 30,
    alias: str = _ALIAS,
) -> Dict[str, Any]:
    return {
        "ts_ms": ts_ms,
        "alias": alias,
        "book": {"mid": mid},
        "or_levels": {"orHigh": or_high, "orLow": or_low, "levels": []},
        "session": {
            "anchorHHMM": anchor_hhmm,
            "anchorTimezone": anchor_tz,
            "anchorMode": anchor_mode,
        },
        "or_session_config": {
            "updatedAtMs": anchor_updated_at_ms,
            "config": {"rangeSeconds": range_seconds},
        },
    }


def test_no_session_when_or_not_formed(_isolate_paths):
    snap = _make_snap(ts_ms=1, mid=30000.0)
    snap["or_levels"] = {}
    view = ledger.update_or_day_ledger(snap)
    assert view["active"] is False


def test_initial_session_created(_isolate_paths):
    view = ledger.update_or_day_ledger(_make_snap(ts_ms=1, mid=30000.0))
    assert view["active"] is True
    assert view["or_high"] == 30050.0
    assert view["or_low"] == 29950.0
    assert view["or_width"] == 100.0


def test_first_commit_up_recorded(_isolate_paths):
    base = 1_000
    ledger.update_or_day_ledger(_make_snap(ts_ms=base, mid=30000.0))
    view = ledger.update_or_day_ledger(_make_snap(ts_ms=base + 5_000, mid=30060.0))
    assert view["first_commit_direction"] == "UP"


def test_first_commit_down_recorded(_isolate_paths):
    ledger.update_or_day_ledger(_make_snap(ts_ms=1_000, mid=30000.0))
    view = ledger.update_or_day_ledger(_make_snap(ts_ms=5_000, mid=29940.0))
    assert view["first_commit_direction"] == "DOWN"


def test_rotation_above_increments_at_65pt(_isolate_paths):
    ledger.update_or_day_ledger(_make_snap(ts_ms=1, mid=30000.0))
    # 64 pts above: still 0 rotations
    v1 = ledger.update_or_day_ledger(_make_snap(ts_ms=2, mid=30050.0 + 64.0))
    assert v1["max_rotations_above"] == 0
    # 65 pts above: 1 rotation
    v2 = ledger.update_or_day_ledger(_make_snap(ts_ms=3, mid=30050.0 + 65.0))
    assert v2["max_rotations_above"] == 1
    # 130 pts above: 2 rotations
    v3 = ledger.update_or_day_ledger(_make_snap(ts_ms=4, mid=30050.0 + 130.0))
    assert v3["max_rotations_above"] == 2


def test_rotation_below_increments_at_65pt(_isolate_paths):
    ledger.update_or_day_ledger(_make_snap(ts_ms=1, mid=30000.0))
    v = ledger.update_or_day_ledger(_make_snap(ts_ms=2, mid=29950.0 - 65.0))
    assert v["max_rotations_below"] == 1


def test_peak_extreme_tracked(_isolate_paths):
    ledger.update_or_day_ledger(_make_snap(ts_ms=1, mid=30000.0))
    ledger.update_or_day_ledger(_make_snap(ts_ms=2, mid=30200.0))
    v = ledger.update_or_day_ledger(_make_snap(ts_ms=3, mid=30150.0))  # lower than 30200
    assert v["peak_extreme_above_price"] == 30200.0


def test_whipsaw_counted_on_direction_flip(_isolate_paths):
    ledger.update_or_day_ledger(_make_snap(ts_ms=1, mid=30000.0))
    ledger.update_or_day_ledger(_make_snap(ts_ms=2, mid=30100.0))  # commit UP
    ledger.update_or_day_ledger(_make_snap(ts_ms=3, mid=30000.0))  # back inside
    v = ledger.update_or_day_ledger(_make_snap(ts_ms=4, mid=29900.0))  # commit DOWN
    assert v["whipsaw_count"] == 1


def test_session_boundary_appends_csv(_isolate_paths):
    csv_path, _json_path = _isolate_paths
    snap1 = _make_snap(ts_ms=1, mid=30100.0)
    ledger.update_or_day_ledger(snap1)
    # New OR-H values -> new session, should finalize prior
    snap2 = _make_snap(
        ts_ms=10_000,
        mid=30200.0,
        or_high=30180.0,
        or_low=30150.0,
        anchor_updated_at_ms=2_000_000_000,
    )
    ledger.update_or_day_ledger(snap2)

    assert csv_path.exists()
    rows = list(csv.reader(csv_path.open("r", encoding="utf-8")))
    assert rows[0] == list(ledger._HEADER)
    assert len(rows) == 2  # header + 1 finalized
    finalized = rows[1]
    # Confirm the FIRST session's OR values made it to disk, not the second.
    or_high_idx = ledger._HEADER.index("or_high")
    assert float(finalized[or_high_idx]) == 30050.0


def test_same_session_does_not_append(_isolate_paths):
    csv_path, _json_path = _isolate_paths
    for i, mid in enumerate([30000.0, 30060.0, 30080.0, 30150.0]):
        ledger.update_or_day_ledger(_make_snap(ts_ms=i + 1, mid=mid))
    # No new session yet; CSV should be empty (no finalized rows).
    assert not csv_path.exists() or csv_path.stat().st_size == 0


def test_final_status_held(_isolate_paths):
    csv_path, _ = _isolate_paths
    ledger.update_or_day_ledger(_make_snap(ts_ms=1, mid=30000.0))
    ledger.update_or_day_ledger(_make_snap(ts_ms=2, mid=30001.0))
    # Trigger session boundary
    ledger.update_or_day_ledger(_make_snap(
        ts_ms=10_000, mid=20000.0, or_high=20050.0, or_low=19950.0,
        anchor_updated_at_ms=2_000_000_000,
    ))
    rows = list(csv.reader(csv_path.open("r", encoding="utf-8")))
    status_idx = ledger._HEADER.index("final_status")
    assert rows[1][status_idx] == "HELD"


def test_final_status_broke_up(_isolate_paths):
    csv_path, _ = _isolate_paths
    ledger.update_or_day_ledger(_make_snap(ts_ms=1, mid=30000.0))
    ledger.update_or_day_ledger(_make_snap(ts_ms=2, mid=30200.0))  # up 150 = 2 rotations
    ledger.update_or_day_ledger(_make_snap(
        ts_ms=10_000, mid=20000.0, or_high=20050.0, or_low=19950.0,
        anchor_updated_at_ms=2_000_000_000,
    ))
    rows = list(csv.reader(csv_path.open("r", encoding="utf-8")))
    status_idx = ledger._HEADER.index("final_status")
    assert rows[1][status_idx] == "BROKE_UP"


def test_live_json_written_each_update(_isolate_paths):
    _csv_path, json_path = _isolate_paths
    ledger.update_or_day_ledger(_make_snap(ts_ms=1, mid=30000.0))
    assert json_path.exists()
    import json as _json
    payload = _json.loads(json_path.read_text())
    assert _ALIAS in payload
    assert payload[_ALIAS]["or_high"] == 30050.0


def test_rotation_unit_nq_default(_isolate_paths):
    v = ledger.update_or_day_ledger(_make_snap(ts_ms=1, mid=30000.0))
    assert v["rotation_unit_pts"] == 65.0


def test_rotation_unit_es():
    ledger.reset_state()
    snap = {
        "ts_ms": 1,
        "alias": "ESM6.CME@RITHMIC",
        "book": {"mid": 5000.0},
        "or_levels": {"orHigh": 5010.0, "orLow": 4990.0, "levels": []},
        "session": {"anchorHHMM": "08:30", "anchorTimezone": "America/Chicago", "anchorMode": "LIVE"},
        "or_session_config": {"updatedAtMs": 1_000_000_000, "config": {"rangeSeconds": 30}},
    }
    v = ledger.update_or_day_ledger(snap)
    assert v["rotation_unit_pts"] == 15.0


def test_graceful_on_empty_snapshot():
    ledger.reset_state()
    view = ledger.update_or_day_ledger({})
    assert view["active"] is False


def test_anchor_resolves_rth_to_today_when_after_open(_isolate_paths):
    """RTH anchor 08:30 CT, called at 09:55 CT same day -> today's 08:30 CT."""
    if ledger._CT is None:
        pytest.skip("zoneinfo not available")
    from datetime import datetime as _dt
    now_dt = _dt(2026, 5, 28, 9, 55, 0, tzinfo=ledger._CT)
    now_ms = int(now_dt.timestamp() * 1000)
    anchor_ms = ledger._resolve_session_anchor_ms("08:30", "America/Chicago", now_ms)
    expected = _dt(2026, 5, 28, 8, 30, 0, tzinfo=ledger._CT)
    assert anchor_ms == int(expected.timestamp() * 1000)


def test_anchor_resolves_eth_to_yesterday_during_overnight(_isolate_paths):
    """ETH anchor 17:00 CT, called at 09:55 CT next morning -> previous-day 17:00."""
    if ledger._CT is None:
        pytest.skip("zoneinfo not available")
    from datetime import datetime as _dt
    now_dt = _dt(2026, 5, 28, 9, 55, 0, tzinfo=ledger._CT)
    now_ms = int(now_dt.timestamp() * 1000)
    anchor_ms = ledger._resolve_session_anchor_ms("17:00", "America/Chicago", now_ms)
    expected = _dt(2026, 5, 27, 17, 0, 0, tzinfo=ledger._CT)
    assert anchor_ms == int(expected.timestamp() * 1000)


def test_session_high_and_low_tracked_independent_of_or(_isolate_paths):
    """Operator tracks session H/L as support/resistance independent of OR.
    These are the absolute max/min mid since session start, regardless of
    OR boundary."""
    ledger.update_or_day_ledger(_make_snap(ts_ms=1_000, mid=30100.0))   # starts
    ledger.update_or_day_ledger(_make_snap(ts_ms=2_000, mid=30050.0))   # session low
    ledger.update_or_day_ledger(_make_snap(ts_ms=3_000, mid=30200.0))   # session high
    v = ledger.update_or_day_ledger(_make_snap(ts_ms=4_000, mid=30150.0))
    assert v["session_high_price"] == 30200.0
    assert v["session_low_price"] == 30050.0
    assert v["session_range"] == 150.0


def test_session_high_low_persist_in_live_json(_isolate_paths):
    csv_path, json_path = _isolate_paths
    ledger.update_or_day_ledger(_make_snap(ts_ms=1_000, mid=30100.0))
    ledger.update_or_day_ledger(_make_snap(ts_ms=2_000, mid=30050.0))
    ledger.update_or_day_ledger(_make_snap(ts_ms=3_000, mid=30200.0))
    import json as _json
    payload = _json.loads(json_path.read_text())
    sess = payload[_ALIAS]
    assert sess["session_high_price"] == 30200.0
    assert sess["session_low_price"] == 30050.0


def test_session_high_low_in_csv_row_on_finalize(_isolate_paths):
    csv_path, _ = _isolate_paths
    ledger.update_or_day_ledger(_make_snap(ts_ms=1_000, mid=30100.0))
    ledger.update_or_day_ledger(_make_snap(ts_ms=2_000, mid=30050.0))   # session low
    ledger.update_or_day_ledger(_make_snap(ts_ms=3_000, mid=30200.0))   # session high
    # Force session boundary -> finalize prior session to CSV
    ledger.update_or_day_ledger(_make_snap(
        ts_ms=10_000, mid=29900.0, or_high=29950.0, or_low=29900.0,
        anchor_updated_at_ms=2_000_000_000,
    ))
    rows = list(csv.reader(csv_path.open()))
    high_idx = list(ledger._HEADER).index("session_high")
    low_idx = list(ledger._HEADER).index("session_low")
    range_idx = list(ledger._HEADER).index("session_range")
    assert float(rows[1][high_idx]) == 30200.0
    assert float(rows[1][low_idx]) == 30050.0
    assert float(rows[1][range_idx]) == 150.0


def test_session_date_ct_uses_wall_clock_anchor_not_config_edit_time(_isolate_paths):
    """The cosmetic date bug: prior code used or_session_config.updatedAtMs
    which is when the OR config was last edited, not when the session
    started. New code resolves anchor_hhmm + tz to actual wall-clock
    instant, so session_date_ct reflects the real session date."""
    if ledger._CT is None:
        pytest.skip("zoneinfo not available")
    from datetime import datetime as _dt
    # Simulate: now is 2026-05-28 09:55 CT, RTH anchor 08:30 CT
    # Config was edited days ago (irrelevant)
    now_dt = _dt(2026, 5, 28, 9, 55, 0, tzinfo=ledger._CT)
    ts_ms = int(now_dt.timestamp() * 1000)
    snap = _make_snap(
        ts_ms=ts_ms,
        mid=30100.0,
        anchor_hhmm="08:30",
        anchor_tz="America/Chicago",
        anchor_updated_at_ms=1_500_000_000_000,  # ancient, would have given wrong date
    )
    view = ledger.update_or_day_ledger(snap)
    # Should resolve to today's date, not the ancient updatedAtMs
    # (Active session anchor_iso_ct not in public view; check via JSON file)
    import json as _json
    _, json_path = _isolate_paths
    payload = _json.loads(json_path.read_text())
    sess = payload[_ALIAS]
    assert sess["session_date_ct"] == "2026-05-28"


def test_classify_session_type_eth_ct():
    assert ledger._classify_session_type("17:00", "America/Chicago") == "ETH"


def test_classify_session_type_rth_ct():
    assert ledger._classify_session_type("08:30", "America/Chicago") == "RTH"


def test_classify_session_type_eth_et():
    assert ledger._classify_session_type("18:00", "America/New_York") == "ETH"


def test_classify_session_type_rth_et():
    assert ledger._classify_session_type("09:30", "America/New_York") == "RTH"


def test_classify_session_type_eu_ct():
    assert ledger._classify_session_type("02:00", "America/Chicago") == "EU"


def test_classify_session_type_other():
    assert ledger._classify_session_type("13:42", "America/Chicago") == "OTHER"
    assert ledger._classify_session_type("", "") == "OTHER"


def test_session_view_carries_session_type_eth(_isolate_paths):
    view = ledger.update_or_day_ledger(
        _make_snap(ts_ms=1, mid=30000.0, anchor_hhmm="17:00",
                   anchor_tz="America/Chicago")
    )
    assert view["session_type"] == "ETH"


def test_session_view_carries_session_type_rth(_isolate_paths):
    view = ledger.update_or_day_ledger(
        _make_snap(ts_ms=1, mid=30000.0, anchor_hhmm="08:30",
                   anchor_tz="America/Chicago")
    )
    assert view["session_type"] == "RTH"


def test_csv_row_includes_session_type_column(_isolate_paths):
    csv_path, _ = _isolate_paths
    # ETH session
    ledger.update_or_day_ledger(
        _make_snap(ts_ms=1, mid=30000.0, anchor_hhmm="17:00",
                   anchor_tz="America/Chicago")
    )
    # New session forces finalize
    ledger.update_or_day_ledger(_make_snap(
        ts_ms=10_000, mid=30200.0, or_high=30180.0, or_low=30150.0,
        anchor_hhmm="17:00", anchor_tz="America/Chicago",
        anchor_updated_at_ms=2_000_000_000,
    ))
    rows = list(csv.reader(csv_path.open("r", encoding="utf-8")))
    type_idx = ledger._HEADER.index("session_type")
    assert rows[1][type_idx] == "ETH"
    date_idx = ledger._HEADER.index("session_date_ct")
    assert rows[1][date_idx]  # non-empty
    dow_idx = ledger._HEADER.index("session_day_of_week")
    assert rows[1][dow_idx]  # non-empty
