"""Stage 6 tests for pax_ai.attack_response_report.

Contracts:
  * Empty day -> clean text + JSON with n_signals=0.
  * Malformed JSONL rows are skipped and counted.
  * Grouping is stable: same input -> same bucket assignments.
  * Read pipeline never mutates input files.
  * JSON shape is stable across runs.
  * The text format never claims measured EDGE.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pax_ai import attack_response_log
from pax_ai import attack_response_report as report


def _write_closed(tmp_path: Path, date: str, rows):
    p = attack_response_log._closed_path(date, tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for r in rows:
            if isinstance(r, str):
                f.write(r + "\n")
            else:
                f.write(json.dumps(r) + "\n")
    return p


def _row(**kw):
    base = {
        "signal_id":          "sigA",
        "ts_ms":              1_700_000_000_000,
        "alias":              "NQM6.CME@RITHMIC",
        "location":           "OR-L",
        "level_price":        30145.0,
        "mid_at_signal":      30150.0,
        "state":              "OR_L_SWEEP_RECLAIM",
        "bias":               "BULL_WATCH",
        "attack":             "SWEEP_LOW",
        "response":           "RECLAIMED",
        "drivers":            ["sweep_low", "bid_iceberg"],
        "confidence":         0.65,
        "or_width_pts":       47.0,
        "vwap_regime":        "BULLISH",
        "vwap_sigma_z":       0.4,
        "flow_regime":        "BALANCED",
        "session_code":       "ACTIVE",
        "anchor_mode":        "LIVE",
        "stop_price":         None,
        "proven_edge":        False,
        "sample_n":           None,
        "edge_R_60s":         None,
        "source":             "attack_response",
        "mid_at_15s":         30156.0,
        "mid_at_60s":         30153.0,
        "mid_at_300s":        30148.0,
        "realized_pts_15s":   6.0,
        "realized_pts_60s":   3.0,
        "realized_pts_300s": -2.0,
        "realized_R_15s":     None,
        "realized_R_60s":     None,
        "realized_R_300s":    None,
        "dir_sign_15s":       1,
        "dir_sign_60s":       1,
        "dir_sign_300s":     -1,
    }
    base.update(kw)
    return base


# --- empty day -----------------------------------------------------------

def test_empty_day_clean_text(tmp_path):
    code, text = report.run("1970-01-01", root=tmp_path)
    assert code == 0
    assert "No closed attack-response signals" in text
    assert "WATCH evidence only" in text


def test_empty_day_clean_json(tmp_path):
    code, text = report.run("1970-01-01", root=tmp_path, json_mode=True)
    body = json.loads(text)
    assert body["n_signals"] == 0
    assert body["n_skipped"] == 0
    assert "horizons" in body
    assert "groups"   in body
    assert body["_warning"] == "WATCH evidence only - NOT measured EDGE"


# --- malformed rows ------------------------------------------------------

def test_malformed_rows_skipped_and_counted(tmp_path):
    _write_closed(tmp_path, "1970-01-01", [
        _row(),
        "not json at all",
        "[\"json array, not dict\"]",   # not a dict
        _row(),
    ])
    code, text = report.run("1970-01-01", root=tmp_path, json_mode=True)
    body = json.loads(text)
    assert body["n_signals"] == 2
    assert body["n_skipped"] == 2


# --- grouping ------------------------------------------------------------

def test_groups_by_state(tmp_path):
    rows = [
        _row(state="OR_L_SWEEP_RECLAIM", bias="BULL_WATCH"),
        _row(state="OR_L_SWEEP_RECLAIM", bias="BULL_WATCH"),
        _row(state="OR_H_SWEEP_FAIL",    bias="BEAR_WATCH"),
    ]
    _write_closed(tmp_path, "1970-01-01", rows)
    _, text = report.run("1970-01-01", root=tmp_path, json_mode=True)
    body = json.loads(text)
    by_state = {g["name"]: g for g in body["groups"]["state"]}
    assert by_state["OR_L_SWEEP_RECLAIM"]["n"] == 2
    assert by_state["OR_H_SWEEP_FAIL"]["n"] == 1


def test_horizon_hit_rate_uses_pts_positive(tmp_path):
    rows = [
        _row(realized_pts_60s=5.0),
        _row(realized_pts_60s=-3.0),
        _row(realized_pts_60s=0.0),
        _row(realized_pts_60s=1.0),
    ]
    _write_closed(tmp_path, "1970-01-01", rows)
    _, text = report.run("1970-01-01", root=tmp_path, json_mode=True)
    body = json.loads(text)
    h60 = body["horizons"]["60s"]
    assert h60["n"] == 4
    # 2 of 4 are > 0 (5.0 and 1.0). 0.0 is NOT a hit.
    assert h60["hit_rate"] == 0.50


def test_or_width_bucket_grouping(tmp_path):
    _write_closed(tmp_path, "1970-01-01", [
        _row(or_width_pts=3.0),
        _row(or_width_pts=12.0),
        _row(or_width_pts=47.0),
        _row(or_width_pts=None),
    ])
    _, text = report.run("1970-01-01", root=tmp_path, json_mode=True)
    body = json.loads(text)
    by_width = {g["name"]: g for g in body["groups"]["or_width"]}
    assert by_width["<5"]["n"] == 1
    assert by_width["10-20"]["n"] == 1
    assert by_width["30-50"]["n"] == 1
    assert by_width["unknown"]["n"] == 1


# --- read-only invariant -------------------------------------------------

def test_report_does_not_mutate_file(tmp_path):
    p = _write_closed(tmp_path, "1970-01-01", [_row(), _row()])
    before = p.read_bytes()
    before_mtime = p.stat().st_mtime
    report.run("1970-01-01", root=tmp_path)
    report.run("1970-01-01", root=tmp_path, json_mode=True)
    after = p.read_bytes()
    assert after == before, "report must NOT mutate the source JSONL"
    assert p.stat().st_mtime == before_mtime


# --- JSON shape stability ------------------------------------------------

def test_json_shape_keys_stable(tmp_path):
    _write_closed(tmp_path, "1970-01-01", [_row()])
    _, text = report.run("1970-01-01", root=tmp_path, json_mode=True)
    body = json.loads(text)
    assert set(body.keys()) >= {
        "date", "path", "n_signals", "n_skipped",
        "horizons", "groups", "_warning",
    }
    assert set(body["horizons"].keys()) == {"15s", "60s", "300s"}
    expected_group_names = {
        "state", "bias", "location", "drivers", "or_width",
        "vwap_regime", "flow_regime", "confidence_bucket", "time_bucket",
    }
    assert set(body["groups"].keys()) == expected_group_names


# --- text never claims measured EDGE -------------------------------------

def test_text_includes_watch_disclaimer(tmp_path):
    _write_closed(tmp_path, "1970-01-01", [_row()])
    _, text = report.run("1970-01-01", root=tmp_path)
    assert "WATCH evidence only" in text
    # Must NOT claim measured edge anywhere
    for word in ("measured edge", "PROVEN_EDGE", "expected_R", "edge_R_60s"):
        assert word not in text, f"text leaked '{word}'"


# --- bucket helpers ------------------------------------------------------

@pytest.mark.parametrize("w,expected", [
    (3.0, "<5"), (7.0, "5-10"), (15.0, "10-20"),
    (25.0, "20-30"), (40.0, "30-50"), (75.0, ">=50"),
    (None, "unknown"), ("xyz", "unknown"),
])
def test_or_width_bucket(w, expected):
    assert report._or_width_bucket(w) == expected


@pytest.mark.parametrize("c,expected", [
    (0.3, "<0.40"), (0.45, "0.40-0.55"),
    (0.60, "0.55-0.70"), (0.85, ">=0.70"),
    (None, "unknown"),
])
def test_confidence_bucket(c, expected):
    assert report._confidence_bucket(c) == expected


def test_drivers_bundle_sorted():
    assert report._drivers_bundle(["b", "a"]) == "a+b"
    assert report._drivers_bundle([]) == "none"
    assert report._drivers_bundle(None) == "none"
