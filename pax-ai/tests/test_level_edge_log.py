"""Contract tests for the append-only level-edge JSONL log (Slice 3).

These tests pin:
  * realized_r math (LONG/SHORT, sign, divide-by-zero)
  * rising-edge dedup: false->true logs once, true->true skipped,
    false->true after a rearm logs a second event
  * non-actionable / WAIT rows never log
  * open row schema (required fields, top_drivers preserved)
  * backfill horizon maturity at +15s / +60s / +300s
  * closed row carries realized_R_{15,60,300}s and invalidated flag
  * append-only: open JSONL is never rewritten
  * logger failure does not propagate (record_payload_safe wrapper)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import pax_ai.level_edge_log as lel


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_NOW_MS = 1_700_000_000_000          # 2023-11-14 22:13:20 UTC
_ALIAS = "NQM6.CME@RITHMIC"


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset module-level state between tests. Without this, the
    cross-test _STATE dict and _OPEN_EVENTS list would leak."""
    lel._reset_state_for_tests()
    yield
    lel._reset_state_for_tests()


def _actionable_long(label="OR-H", price=20060.0, mid=20060.0, stop=20040.0):
    return {
        "label":         label,
        "price":         price,
        "side":          "high",
        "direction":     "LONG",
        "raw_direction": "LONG",
        "composite_dir": "FOLLOW_LONG",
        "confidence":    0.55,
        "score_R":       1.25,
        "stop_price":    stop,
        "size_tier":     "HALF",
        "actionable":    True,
        "color_hint":    "positive",
        "setup":         "OR_BREAK_FOLLOW",
        "top_drivers":   ["pull_stack", "vwap", "tape"],
        "blocked_reason": None,
        "snapshot_ts_ms": _NOW_MS,
        "mid_at_signal": mid,
    }


def _actionable_short(label="OR-H", price=20060.0, mid=20060.0, stop=20080.0):
    row = _actionable_long(label, price, mid, stop)
    row["direction"] = "SHORT"
    row["raw_direction"] = "SHORT"
    row["composite_dir"] = "FADE_SHORT"
    row["color_hint"] = "negative"
    row["setup"] = "LEVEL_FADE_SHORT"
    return row


def _wait_row(label="OR-H", price=20060.0, mid=20060.0):
    row = _actionable_long(label, price, mid)
    row["direction"] = "WAIT"
    row["actionable"] = False
    return row


def _payload(*rows, mid=20060.0):
    return {
        "alias":      _ALIAS,
        "asOfMs":     _NOW_MS,
        "ageMs":      0,
        "stale":      False,
        "mid":        mid,
        "anchorMode": "LIVE",
        "blocked":    {"health_ok": True, "news": False,
                        "session": False, "stale": False, "anchor": False},
        "levels":     list(rows),
    }


def _snap(mid=20060.0):
    return {"alias": _ALIAS, "book": {"mid": mid}, "or_levels": {}}


def _read_jsonl(path):
    if not Path(path).exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_realized_r_long_positive():
    # entry 100, stop 90, future 110 -> +1R
    assert lel.realized_r("LONG", 100.0, 110.0, 90.0) == pytest.approx(1.0)


def test_realized_r_long_negative():
    # entry 100, stop 90, future 85 -> -1.5R
    assert lel.realized_r("LONG", 100.0, 85.0, 90.0) == pytest.approx(-1.5)


def test_realized_r_short_positive():
    # entry 100, stop 110, future 95 -> +0.5R
    assert lel.realized_r("SHORT", 100.0, 95.0, 110.0) == pytest.approx(0.5)


def test_realized_r_short_negative():
    # entry 100, stop 110, future 115 -> -1.5R
    assert lel.realized_r("SHORT", 100.0, 115.0, 110.0) == pytest.approx(-1.5)


def test_realized_r_zero_stop_distance_returns_none():
    assert lel.realized_r("LONG", 100.0, 110.0, 100.0) is None


def test_realized_r_missing_inputs_returns_none():
    assert lel.realized_r("LONG", None, 110.0, 90.0) is None
    assert lel.realized_r("LONG", 100.0, None, 90.0) is None
    assert lel.realized_r("LONG", 100.0, 110.0, None) is None
    assert lel.realized_r("WAIT", 100.0, 110.0, 90.0) is None


def test_signal_key_uses_alias_label_direction_setup():
    row = _actionable_long(label="OR-H")
    assert lel.signal_key(row, "NQM6") == ("NQM6", "OR-H", "LONG",
                                              "OR_BREAK_FOLLOW")


def test_should_log_transition_rising_edge():
    assert lel.should_log_transition(False, _actionable_long()) is True


def test_should_log_transition_same_key_does_not_relog():
    assert lel.should_log_transition(True, _actionable_long()) is False


def test_should_log_transition_non_actionable_never_logs():
    row = _actionable_long()
    row["actionable"] = False
    assert lel.should_log_transition(False, row) is False


def test_should_log_transition_wait_never_logs():
    assert lel.should_log_transition(False, _wait_row()) is False


# ---------------------------------------------------------------------------
# build_open_event
# ---------------------------------------------------------------------------

def test_build_open_event_returns_none_when_stop_missing():
    row = _actionable_long()
    row["stop_price"] = None
    assert lel.build_open_event(_payload(row), row, _NOW_MS) is None


def test_build_open_event_returns_none_when_mid_missing():
    row = _actionable_long()
    row["mid_at_signal"] = None
    assert lel.build_open_event(_payload(row), row, _NOW_MS) is None


def test_build_open_event_returns_none_when_zero_stop_distance():
    row = _actionable_long(mid=20040.0, stop=20040.0)
    assert lel.build_open_event(_payload(row), row, _NOW_MS) is None


def test_build_open_event_returns_none_for_wait_direction():
    row = _actionable_long()
    row["direction"] = "WAIT"
    assert lel.build_open_event(_payload(row), row, _NOW_MS) is None


def test_build_open_event_contains_required_fields():
    row = _actionable_long()
    ev = lel.build_open_event(_payload(row), row, _NOW_MS)
    required = {"signal_id", "ts_ms", "alias", "level_label", "level_price",
                  "mid_at_signal", "direction", "setup", "score_R",
                  "confidence", "size_tier", "stop_price", "top_drivers",
                  "snapshot_ts_ms", "source"}
    assert required.issubset(ev.keys())
    assert ev["alias"] == _ALIAS
    assert ev["level_label"] == "OR-H"
    assert ev["direction"] == "LONG"
    assert ev["setup"] == "OR_BREAK_FOLLOW"
    assert ev["source"] == "levels_edge"
    assert ev["ts_ms"] == _NOW_MS


def test_build_open_event_preserves_top_drivers():
    row = _actionable_long()
    row["top_drivers"] = ["pull_stack", "vwap", "tape"]
    ev = lel.build_open_event(_payload(row), row, _NOW_MS)
    assert ev["top_drivers"] == ["pull_stack", "vwap", "tape"]


# ---------------------------------------------------------------------------
# record_payload: rising-edge dedup + file behavior
# ---------------------------------------------------------------------------

def test_non_actionable_row_does_not_log(tmp_path):
    row = _actionable_long()
    row["actionable"] = False
    payload = _payload(row)
    result = lel.record_payload(payload, _snap(), now_ms=_NOW_MS, root=tmp_path)
    assert result["opened"] == 0
    assert _read_jsonl(result["open_path"]) == []


def test_wait_row_does_not_log(tmp_path):
    payload = _payload(_wait_row())
    result = lel.record_payload(payload, _snap(), now_ms=_NOW_MS, root=tmp_path)
    assert result["opened"] == 0
    assert _read_jsonl(result["open_path"]) == []


def test_false_to_true_logs_once(tmp_path):
    row = _actionable_long()
    result = lel.record_payload(_payload(row), _snap(),
                                  now_ms=_NOW_MS, root=tmp_path)
    assert result["opened"] == 1
    rows = _read_jsonl(result["open_path"])
    assert len(rows) == 1
    assert rows[0]["level_label"] == "OR-H"
    assert rows[0]["direction"] == "LONG"


def test_true_to_true_does_not_relog(tmp_path):
    row = _actionable_long()
    lel.record_payload(_payload(row), _snap(), now_ms=_NOW_MS, root=tmp_path)
    result = lel.record_payload(_payload(row), _snap(),
                                  now_ms=_NOW_MS + 1000, root=tmp_path)
    assert result["opened"] == 0
    assert len(_read_jsonl(result["open_path"])) == 1


def test_true_then_false_then_true_logs_second_event(tmp_path):
    row = _actionable_long()
    lel.record_payload(_payload(row), _snap(), now_ms=_NOW_MS, root=tmp_path)
    # Rearm: the key returns to actionable=false
    rearm = _wait_row()
    lel.record_payload(_payload(rearm), _snap(),
                        now_ms=_NOW_MS + 1000, root=tmp_path)
    # Now actionable again -> second event
    result = lel.record_payload(_payload(row), _snap(),
                                  now_ms=_NOW_MS + 2000, root=tmp_path)
    assert result["opened"] == 1
    open_rows = _read_jsonl(result["open_path"])
    assert len(open_rows) == 2
    assert open_rows[0]["ts_ms"] == _NOW_MS
    assert open_rows[1]["ts_ms"] == _NOW_MS + 2000
    # Two distinct signal_ids
    assert open_rows[0]["signal_id"] != open_rows[1]["signal_id"]


def test_missing_stop_does_not_log(tmp_path):
    row = _actionable_long()
    row["stop_price"] = None
    result = lel.record_payload(_payload(row), _snap(),
                                  now_ms=_NOW_MS, root=tmp_path)
    assert result["opened"] == 0
    assert _read_jsonl(result["open_path"]) == []


def test_distinct_directions_at_same_label_are_separate_keys(tmp_path):
    long_row = _actionable_long()
    short_row = _actionable_short()
    short_row["label"] = "OR-H"  # same label, different direction+setup
    short_row["setup"] = "LEVEL_FADE_SHORT"
    payload = _payload(long_row, short_row)
    result = lel.record_payload(payload, _snap(),
                                  now_ms=_NOW_MS, root=tmp_path)
    assert result["opened"] == 2


# ---------------------------------------------------------------------------
# Backfill maturity
# ---------------------------------------------------------------------------

def test_backfill_fills_15s_only_when_mature(tmp_path):
    row = _actionable_long()
    lel.record_payload(_payload(row), _snap(mid=20060.0),
                        now_ms=_NOW_MS, root=tmp_path)
    # Probe at +5s -- nothing should mature.
    res = lel.record_payload(_payload(row), _snap(mid=20065.0),
                              now_ms=_NOW_MS + 5_000, root=tmp_path)
    assert res["closed"] == 0
    # Probe at +15s -- 15s horizon should fill but 60/300 not yet, so no
    # closed row.
    res = lel.record_payload(_payload(row), _snap(mid=20070.0),
                              now_ms=_NOW_MS + 15_000, root=tmp_path)
    assert res["closed"] == 0
    # Mid_at_15s should now be set on the in-memory open event.
    assert lel._OPEN_EVENTS[0].get("mid_at_15s") == pytest.approx(20070.0)
    assert lel._OPEN_EVENTS[0].get("mid_at_60s") is None
    assert lel._OPEN_EVENTS[0].get("mid_at_300s") is None


def test_backfill_emits_closed_row_with_realized_r(tmp_path):
    row = _actionable_long(mid=20060.0, stop=20040.0)
    lel.record_payload(_payload(row), _snap(mid=20060.0),
                        now_ms=_NOW_MS, root=tmp_path)
    # +15s -- mid moves to 20070
    lel.record_payload(_payload(row), _snap(mid=20070.0),
                        now_ms=_NOW_MS + 15_000, root=tmp_path)
    # +60s -- mid moves to 20080
    lel.record_payload(_payload(row), _snap(mid=20080.0),
                        now_ms=_NOW_MS + 60_000, root=tmp_path)
    # +300s -- mid 20100, all three horizons mature -> closed
    res = lel.record_payload(_payload(row), _snap(mid=20100.0),
                              now_ms=_NOW_MS + 300_000, root=tmp_path)
    assert res["closed"] == 1
    closed_rows = _read_jsonl(res["closed_path"])
    assert len(closed_rows) == 1
    c = closed_rows[0]
    # stop distance = 20.0
    assert c["realized_R_15s"] == pytest.approx(0.5)   # (20070-20060)/20
    assert c["realized_R_60s"] == pytest.approx(1.0)   # (20080-20060)/20
    assert c["realized_R_300s"] == pytest.approx(2.0)  # (20100-20060)/20
    assert c["invalidated"] is False
    # Open list is empty after close.
    assert lel._OPEN_EVENTS == []


def test_backfill_invalidated_when_any_horizon_below_negative_one_r(tmp_path):
    row = _actionable_long(mid=20060.0, stop=20040.0)
    lel.record_payload(_payload(row), _snap(mid=20060.0),
                        now_ms=_NOW_MS, root=tmp_path)
    # +15s: mid drops to 20035 -> realized R = (20035-20060)/20 = -1.25
    lel.record_payload(_payload(row), _snap(mid=20035.0),
                        now_ms=_NOW_MS + 15_000, root=tmp_path)
    lel.record_payload(_payload(row), _snap(mid=20055.0),
                        now_ms=_NOW_MS + 60_000, root=tmp_path)
    res = lel.record_payload(_payload(row), _snap(mid=20065.0),
                              now_ms=_NOW_MS + 300_000, root=tmp_path)
    assert res["closed"] == 1
    c = _read_jsonl(res["closed_path"])[0]
    assert c["realized_R_15s"] == pytest.approx(-1.25)
    assert c["invalidated"] is True


def test_backfill_short_realized_r_signs_are_inverted(tmp_path):
    row = _actionable_short(mid=20060.0, stop=20080.0)
    lel.record_payload(_payload(row), _snap(mid=20060.0),
                        now_ms=_NOW_MS, root=tmp_path)
    lel.record_payload(_payload(row), _snap(mid=20050.0),
                        now_ms=_NOW_MS + 15_000, root=tmp_path)
    lel.record_payload(_payload(row), _snap(mid=20040.0),
                        now_ms=_NOW_MS + 60_000, root=tmp_path)
    res = lel.record_payload(_payload(row), _snap(mid=20030.0),
                              now_ms=_NOW_MS + 300_000, root=tmp_path)
    c = _read_jsonl(res["closed_path"])[0]
    assert c["realized_R_15s"] == pytest.approx(0.5)
    assert c["realized_R_60s"] == pytest.approx(1.0)
    assert c["realized_R_300s"] == pytest.approx(1.5)
    assert c["invalidated"] is False


# ---------------------------------------------------------------------------
# Append-only file behavior
# ---------------------------------------------------------------------------

def test_open_jsonl_is_append_only(tmp_path):
    row = _actionable_long()
    lel.record_payload(_payload(row), _snap(), now_ms=_NOW_MS, root=tmp_path)
    open_path = lel._open_path(lel._utc_date_str(_NOW_MS), tmp_path)
    contents_before = open_path.read_bytes()
    # Re-arm + relog
    lel.record_payload(_payload(_wait_row()), _snap(),
                        now_ms=_NOW_MS + 1000, root=tmp_path)
    lel.record_payload(_payload(row), _snap(),
                        now_ms=_NOW_MS + 2000, root=tmp_path)
    contents_after = open_path.read_bytes()
    # The original prefix bytes must be byte-identical (append-only).
    assert contents_after.startswith(contents_before)
    # And the file must have grown.
    assert len(contents_after) > len(contents_before)


# ---------------------------------------------------------------------------
# Error-isolation: record_payload_safe must swallow on bad root
# ---------------------------------------------------------------------------

def test_record_payload_safe_swallows_errors(monkeypatch, tmp_path, capsys):
    row = _actionable_long()

    def boom(*_a, **_kw):
        raise OSError("disk on fire")

    monkeypatch.setattr(lel, "_append_jsonl", boom)
    # Should NOT raise -- even though the inner _append_jsonl explodes.
    result = lel.record_payload_safe(_payload(row), _snap(),
                                       now_ms=_NOW_MS, root=tmp_path)
    assert result is None
    captured = capsys.readouterr()
    assert "level_edge_log" in captured.err


# ---------------------------------------------------------------------------
# Path layout
# ---------------------------------------------------------------------------

def test_files_land_in_utc_dated_path(tmp_path):
    row = _actionable_long()
    result = lel.record_payload(_payload(row), _snap(),
                                  now_ms=_NOW_MS, root=tmp_path)
    expected_date = lel._utc_date_str(_NOW_MS)
    open_path = Path(result["open_path"])
    assert open_path.name == f"{expected_date}.open.jsonl"
    assert open_path.parent == tmp_path
    assert open_path.exists()
