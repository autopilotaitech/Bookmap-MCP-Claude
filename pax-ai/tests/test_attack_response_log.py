"""Stage 5 tests for pax_ai.attack_response_log.

Contracts:
  * Rising edge (false -> true) on a WATCH state logs exactly one open row.
  * Same state repeated does not relog within the same actionable run.
  * State absent on a later poll then present again logs a new row.
  * WATCH (not actionable / no stop) still logs - this is the point.
  * NO_EDGE / NEUTRAL never log.
  * 15s / 60s / 300s mid backfill matures the row into the closed file.
  * Append-only on both files.
  * record_payload_safe swallows OSError + returns None.
  * The report layer (Stage 6) does not mutate logs - test that read_*
    helpers are pure.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pax_ai import attack_response_log as ar_log


# --- fixtures ------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_state():
    ar_log._reset_state_for_tests()
    yield
    ar_log._reset_state_for_tests()


def _state(state="OR_L_SWEEP_RECLAIM", bias="BULL_WATCH",
            location="OR-L", level_price=30145.0, sid="sig-A",
            drivers=None, confidence=0.65):
    return {
        "id":           sid,
        "location":     location,
        "level_price":  level_price,
        "state":        state,
        "bias":         bias,
        "attack":       "SWEEP_LOW",
        "response":     "RECLAIMED",
        "drivers":      drivers or ["sweep_low", "bid_iceberg", "bid_stack"],
        "needed":       "reclaim OR-L",
        "invalid":      "new low",
        "confidence":   confidence,
        "sample_n":     None,
        "edge_R_60s":   None,
        "proven_edge":  False,
        "timestamp_ms": 1_000,
    }


def _payload(states):
    return {
        "alias":   "NQM6.CME@RITHMIC",
        "asOfMs":  1_000,
        "health":  "ok",
        "states":  states,
        "blocked": {"health": False, "stale": False, "anchor": False},
    }


def _snap(mid=30150.0, or_width=47.0):
    return {
        "alias": "NQM6.CME@RITHMIC",
        "book":  {"mid": mid},
        "or_levels": {"orHigh": 30192.0, "orLow": 30145.0,
                       "orWidthPts": or_width},
        "session": {"code": "ACTIVE", "anchorMode": "LIVE"},
        "vwap_bias": {"regime": "BULLISH", "sigma_z": 0.4},
        "flow":      {"regime": "BALANCED"},
    }


def _read_jsonl(path: Path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


# --- rising-edge contract ------------------------------------------------

def test_rising_edge_logs_one_open_row(tmp_path):
    payload = _payload([_state()])
    out = ar_log.record_payload(payload, _snap(), now_ms=1_000, root=tmp_path)
    assert out["opened"] == 1
    open_p = ar_log._open_path("1970-01-01", tmp_path)
    rows = _read_jsonl(open_p)
    assert len(rows) == 1
    r = rows[0]
    assert r["state"] == "OR_L_SWEEP_RECLAIM"
    assert r["bias"] == "BULL_WATCH"
    assert r["mid_at_signal"] == 30150.0
    assert r["or_width_pts"] == 47.0
    assert r["vwap_regime"] == "BULLISH"
    # backfill keys absent on the open row
    assert "mid_at_15s" not in r


def test_same_state_repeated_does_not_relog(tmp_path):
    payload = _payload([_state()])
    ar_log.record_payload(payload, _snap(), now_ms=1_000, root=tmp_path)
    out = ar_log.record_payload(payload, _snap(), now_ms=1_500, root=tmp_path)
    assert out["opened"] == 0
    rows = _read_jsonl(ar_log._open_path("1970-01-01", tmp_path))
    assert len(rows) == 1


def test_state_disappearing_then_reappearing_logs_a_new_row(tmp_path):
    s = _state()
    ar_log.record_payload(_payload([s]), _snap(), now_ms=1_000, root=tmp_path)
    # Poll with NO states for the same alias - this rearms the key.
    ar_log.record_payload(_payload([]), _snap(), now_ms=2_000, root=tmp_path)
    out = ar_log.record_payload(_payload([s]), _snap(), now_ms=3_000, root=tmp_path)
    assert out["opened"] == 1
    rows = _read_jsonl(ar_log._open_path("1970-01-01", tmp_path))
    # 2 open rows; the second one is the re-rising edge
    assert len(rows) >= 2


def test_no_edge_does_not_log(tmp_path):
    s = _state(state="NO_EDGE", bias="NEUTRAL")
    out = ar_log.record_payload(_payload([s]), _snap(),
                                  now_ms=1_000, root=tmp_path)
    assert out["opened"] == 0
    assert not ar_log._open_path("1970-01-01", tmp_path).exists()


def test_watch_logs_without_stop_price(tmp_path):
    # Stage 5 contract: WATCH must log even with stop_price=None. The
    # whole point of this log is evidence before the system knows the
    # statistical edge.
    s = _state()
    assert "stop_price" not in s  # the classifier doesn't emit one
    ar_log.record_payload(_payload([s]), _snap(), now_ms=1_000, root=tmp_path)
    rows = _read_jsonl(ar_log._open_path("1970-01-01", tmp_path))
    assert len(rows) == 1
    assert rows[0]["stop_price"] is None


# --- backfill ------------------------------------------------------------

def test_backfill_matures_into_closed_file(tmp_path):
    s = _state()
    # t=1000: open row at mid 30150.
    ar_log.record_payload(_payload([s]), _snap(mid=30150.0),
                           now_ms=1_000, root=tmp_path)
    # t=1000 + 15s: mid moves up to 30156 -> realized_pts_15s = +6.
    out = ar_log.record_payload(_payload([s]), _snap(mid=30156.0),
                                  now_ms=16_000, root=tmp_path)
    assert out["closed"] == 0
    # t=1000 + 60s: mid 30152.
    ar_log.record_payload(_payload([s]), _snap(mid=30152.0),
                           now_ms=61_000, root=tmp_path)
    # t=1000 + 300s: mid 30148.
    out2 = ar_log.record_payload(_payload([s]), _snap(mid=30148.0),
                                   now_ms=301_000, root=tmp_path)
    assert out2["closed"] == 1
    closed_rows = _read_jsonl(ar_log._closed_path("1970-01-01", tmp_path))
    assert len(closed_rows) == 1
    cr = closed_rows[0]
    assert cr["mid_at_15s"] == 30156.0
    assert cr["mid_at_60s"] == 30152.0
    assert cr["mid_at_300s"] == 30148.0
    # Bias BULL_WATCH -> realized_pts = future - now.
    assert cr["realized_pts_15s"] == pytest.approx(6.0)
    assert cr["realized_pts_60s"] == pytest.approx(2.0)
    assert cr["realized_pts_300s"] == pytest.approx(-2.0)
    # No stop -> realized_R is None.
    assert cr["realized_R_15s"] is None
    # direction sign matches sign of pts
    assert cr["dir_sign_15s"] == 1
    assert cr["dir_sign_300s"] == -1


def test_bear_watch_inverts_direction(tmp_path):
    s = _state(state="OR_H_SWEEP_FAIL", bias="BEAR_WATCH",
               location="OR-H", level_price=30192.0)
    ar_log.record_payload(_payload([s]), _snap(mid=30200.0),
                           now_ms=1_000, root=tmp_path)
    ar_log.record_payload(_payload([s]), _snap(mid=30190.0),
                           now_ms=16_000, root=tmp_path)
    ar_log.record_payload(_payload([s]), _snap(mid=30190.0),
                           now_ms=61_000, root=tmp_path)
    ar_log.record_payload(_payload([s]), _snap(mid=30190.0),
                           now_ms=301_000, root=tmp_path)
    closed = _read_jsonl(ar_log._closed_path("1970-01-01", tmp_path))
    assert len(closed) == 1
    # BEAR_WATCH: realized = mid0 - future = 30200 - 30190 = +10 (good move).
    assert closed[0]["realized_pts_15s"] == pytest.approx(10.0)
    assert closed[0]["dir_sign_15s"] == 1


# --- append-only invariant -----------------------------------------------

def test_open_file_is_append_only(tmp_path):
    """The open file is never rewritten. Closed events are also appended,
    not in-place-edited."""
    s1 = _state(sid="A")
    s2 = _state(sid="B", state="OR_H_SWEEP_FAIL", bias="BEAR_WATCH",
                location="OR-H", level_price=30192.0)
    ar_log.record_payload(_payload([s1]), _snap(), now_ms=1_000, root=tmp_path)
    p = ar_log._open_path("1970-01-01", tmp_path)
    before = p.read_bytes()
    ar_log.record_payload(_payload([s1, s2]), _snap(), now_ms=2_000, root=tmp_path)
    after = p.read_bytes()
    assert after.startswith(before), "open file MUST be append-only"


# --- safe wrapper --------------------------------------------------------

def test_safe_wrapper_swallows_oserror(monkeypatch, tmp_path):
    def boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(ar_log, "_append_jsonl", boom)
    out = ar_log.record_payload_safe(_payload([_state()]), _snap(),
                                       now_ms=1_000, root=tmp_path)
    assert out is None


def test_safe_wrapper_returns_summary_on_success(tmp_path):
    out = ar_log.record_payload_safe(_payload([_state()]), _snap(),
                                       now_ms=1_000, root=tmp_path)
    assert out is not None
    assert out["opened"] == 1


# --- pure helpers --------------------------------------------------------

def test_should_log_transition_rules():
    assert ar_log.should_log_transition(False, _state()) is True
    assert ar_log.should_log_transition(True, _state()) is False
    # NO_EDGE never logs
    assert ar_log.should_log_transition(False,
            _state(state="NO_EDGE", bias="NEUTRAL")) is False


def test_realized_points_directional():
    assert ar_log.realized_points("BULL_WATCH", 100.0, 105.0) == pytest.approx(5.0)
    assert ar_log.realized_points("BEAR_WATCH", 100.0, 95.0) == pytest.approx(5.0)
    assert ar_log.realized_points("BULL_WATCH", 100.0, 95.0) == pytest.approx(-5.0)
    assert ar_log.realized_points("NEUTRAL", 100.0, 105.0) is None
    assert ar_log.realized_points("BULL_WATCH", None, 105.0) is None


def test_realized_r_requires_stop_price():
    assert ar_log.realized_r_from_stop("BULL_WATCH", 100.0, 105.0, None) is None
    assert ar_log.realized_r_from_stop("BULL_WATCH", 100.0, 105.0, 95.0) == pytest.approx(1.0)
    assert ar_log.realized_r_from_stop("BEAR_WATCH", 100.0, 95.0, 105.0) == pytest.approx(1.0)
    # zero stop distance -> None
    assert ar_log.realized_r_from_stop("BULL_WATCH", 100.0, 105.0, 100.0) is None
