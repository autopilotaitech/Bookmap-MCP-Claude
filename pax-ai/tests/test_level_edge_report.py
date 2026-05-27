"""Contract tests for the level-edge daily report CLI (Slice 4).

Pins:
  * empty / missing file -> exit 0 + clean message
  * malformed JSONL rows are skipped and counted, not fatal
  * horizon hit_rate, mean_R, median_R math
  * null realized_R values ignored per horizon
  * invalidated count + rate
  * groupings: setup, direction, size_tier, level_label, confidence_bucket,
    top_driver bundle
  * sort: descending n, then ascending name
  * JSON output stable shape, text output includes expected sections
  * CLI returns 0 on empty day
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from pax_ai import level_edge_log
from pax_ai import level_edge_report as ler


_DATE = "2026-05-27"


def _write_closed(tmp_path: Path, rows):
    path = tmp_path / f"{_DATE}.closed.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            if isinstance(r, str):
                # Raw line (e.g. malformed JSON injected as-is).
                f.write(r.rstrip("\n") + "\n")
            else:
                f.write(json.dumps(r) + "\n")
    return path


def _row(**overrides):
    base = {
        "signal_id":      "abc",
        "ts_ms":          1_700_000_000_000,
        "alias":          "NQM6.CME@RITHMIC",
        "level_label":    "OR-H",
        "level_price":    20060.0,
        "mid_at_signal":  20060.0,
        "direction":      "LONG",
        "setup":          "OR_BREAK_FOLLOW",
        "score_R":        1.0,
        "confidence":     0.55,
        "size_tier":      "HALF",
        "stop_price":     20040.0,
        "top_drivers":    ["pull_stack", "vwap", "tape"],
        "snapshot_ts_ms": 1_700_000_000_000,
        "source":         "levels_edge",
        "mid_at_15s":     20070.0,
        "mid_at_60s":     20080.0,
        "mid_at_300s":    20100.0,
        "realized_R_15s": 0.5,
        "realized_R_60s": 1.0,
        "realized_R_300s": 2.0,
        "invalidated":    False,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Empty / missing
# ---------------------------------------------------------------------------

def test_empty_day_exits_zero_with_clear_message(tmp_path, capsys):
    # No file -> exit 0, text mode prints a clear "no signals" line.
    code = ler.main(["--date", _DATE, "--root", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert _DATE in out
    assert "No closed level-edge signals" in out


def test_empty_day_json_returns_zero_counts(tmp_path, capsys):
    code = ler.main(["--date", _DATE, "--root", str(tmp_path), "--json"])
    assert code == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["date"] == _DATE
    assert payload["n_signals"] == 0
    assert payload["n_skipped"] == 0
    for label in ("15s", "60s", "300s"):
        h = payload["horizons"][label]
        assert h["n"] == 0
        assert h["hit_rate"] is None
        assert h["mean_R"] is None
        assert h["median_R"] is None
    assert payload["invalidated"]["count"] == 0
    assert payload["invalidated"]["rate"] is None
    for gname in ("setup", "direction", "size_tier", "level_label",
                   "confidence_bucket", "top_drivers"):
        assert payload["groups"][gname] == []


def test_empty_day_text_includes_path(tmp_path, capsys):
    code = ler.main(["--date", _DATE, "--root", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert f"{_DATE}.closed.jsonl" in out


def test_path_resolution_uses_level_edge_log_helper(tmp_path):
    # `run` should resolve the path through level_edge_log._closed_path so
    # the report and writer agree on the file layout.
    code, text = ler.run(_DATE, root=tmp_path, json_mode=True)
    assert code == 0
    expected = str(level_edge_log._closed_path(_DATE, tmp_path))
    payload = json.loads(text)
    assert payload["path"] == expected


# ---------------------------------------------------------------------------
# Malformed rows
# ---------------------------------------------------------------------------

def test_malformed_rows_are_skipped_and_counted(tmp_path, capsys):
    rows = [
        _row(),
        "{not valid json",            # parse error
        json.dumps([1, 2, 3]),          # parseable but not a dict
        _row(direction="SHORT", realized_R_60s=-0.5),
    ]
    _write_closed(tmp_path, rows)
    code, text = ler.run(_DATE, root=tmp_path, json_mode=True)
    assert code == 0
    payload = json.loads(text)
    assert payload["n_signals"] == 2
    assert payload["n_skipped"] == 2


# ---------------------------------------------------------------------------
# Horizon stats
# ---------------------------------------------------------------------------

def test_horizon_hit_rate_mean_median(tmp_path):
    rows = [
        _row(realized_R_60s=+1.0),
        _row(realized_R_60s=+0.5),
        _row(realized_R_60s=-0.5),
        _row(realized_R_60s=-1.5, invalidated=True),
    ]
    _write_closed(tmp_path, rows)
    code, text = ler.run(_DATE, root=tmp_path, json_mode=True)
    assert code == 0
    h60 = json.loads(text)["horizons"]["60s"]
    assert h60["n"] == 4
    assert h60["hit_rate"] == pytest.approx(0.50)
    assert h60["mean_R"] == pytest.approx(-0.125)
    # median of [-1.5, -0.5, 0.5, 1.0] is mean(-0.5, 0.5) = 0.0
    assert h60["median_R"] == pytest.approx(0.0)


def test_null_realized_r_is_ignored_per_horizon(tmp_path):
    rows = [
        _row(realized_R_15s=+1.0, realized_R_60s=None, realized_R_300s=+2.0),
        _row(realized_R_15s=-0.5, realized_R_60s=+0.5, realized_R_300s=None),
    ]
    _write_closed(tmp_path, rows)
    code, text = ler.run(_DATE, root=tmp_path, json_mode=True)
    payload = json.loads(text)
    h15 = payload["horizons"]["15s"]
    h60 = payload["horizons"]["60s"]
    h300 = payload["horizons"]["300s"]
    assert h15["n"] == 2
    assert h60["n"] == 1
    assert h300["n"] == 1
    assert h60["mean_R"] == pytest.approx(0.5)
    assert h300["mean_R"] == pytest.approx(2.0)


def test_invalidated_rate(tmp_path):
    rows = [
        _row(invalidated=False),
        _row(invalidated=True),
        _row(invalidated=True),
        _row(invalidated=False),
    ]
    _write_closed(tmp_path, rows)
    code, text = ler.run(_DATE, root=tmp_path, json_mode=True)
    payload = json.loads(text)
    inv = payload["invalidated"]
    assert inv["count"] == 2
    assert inv["n"] == 4
    assert inv["rate"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

def test_group_by_setup(tmp_path):
    rows = [
        _row(setup="OR_BREAK_FOLLOW",  realized_R_60s=+1.0),
        _row(setup="OR_BREAK_FOLLOW",  realized_R_60s=+0.5),
        _row(setup="LEVEL_FADE_SHORT", direction="SHORT",
             realized_R_60s=-0.25, invalidated=True),
    ]
    _write_closed(tmp_path, rows)
    code, text = ler.run(_DATE, root=tmp_path, json_mode=True)
    groups = json.loads(text)["groups"]["setup"]
    assert [g["name"] for g in groups] == ["OR_BREAK_FOLLOW",
                                                "LEVEL_FADE_SHORT"]
    g0 = groups[0]
    assert g0["n"] == 2
    assert g0["hit_rate_60s"] == pytest.approx(1.0)
    assert g0["mean_R_60s"] == pytest.approx(0.75)
    assert g0["median_R_60s"] == pytest.approx(0.75)
    assert g0["invalidated_rate"] == pytest.approx(0.0)
    g1 = groups[1]
    assert g1["n"] == 1
    assert g1["hit_rate_60s"] == pytest.approx(0.0)
    assert g1["invalidated_rate"] == pytest.approx(1.0)


def test_group_by_direction(tmp_path):
    rows = [
        _row(direction="LONG"),
        _row(direction="LONG"),
        _row(direction="SHORT", realized_R_60s=-0.5),
    ]
    _write_closed(tmp_path, rows)
    code, text = ler.run(_DATE, root=tmp_path, json_mode=True)
    groups = json.loads(text)["groups"]["direction"]
    assert [g["name"] for g in groups] == ["LONG", "SHORT"]


def test_group_by_size_tier(tmp_path):
    rows = [
        _row(size_tier="HALF"),
        _row(size_tier="HALF"),
        _row(size_tier="FULL"),
    ]
    _write_closed(tmp_path, rows)
    code, text = ler.run(_DATE, root=tmp_path, json_mode=True)
    groups = json.loads(text)["groups"]["size_tier"]
    assert [g["name"] for g in groups] == ["HALF", "FULL"]


def test_group_by_level_label(tmp_path):
    rows = [
        _row(level_label="OR-H"),
        _row(level_label="OR-L"),
        _row(level_label="OR-H"),
    ]
    _write_closed(tmp_path, rows)
    code, text = ler.run(_DATE, root=tmp_path, json_mode=True)
    groups = json.loads(text)["groups"]["level_label"]
    assert [g["name"] for g in groups] == ["OR-H", "OR-L"]


def test_group_by_confidence_bucket(tmp_path):
    rows = [
        _row(confidence=0.20),  # <0.35
        _row(confidence=0.40),  # 0.35-0.50
        _row(confidence=0.55),  # 0.50-0.65
        _row(confidence=0.70),  # >=0.65
        _row(confidence=0.55),  # 0.50-0.65 second
    ]
    _write_closed(tmp_path, rows)
    code, text = ler.run(_DATE, root=tmp_path, json_mode=True)
    groups = json.loads(text)["groups"]["confidence_bucket"]
    names = [g["name"] for g in groups]
    # n desc, then name asc: 0.50-0.65 (2), then alphabetical for ties.
    assert names[0] == "0.50-0.65"
    assert {"<0.35", "0.35-0.50", ">=0.65"}.issubset(set(names))


def test_group_by_top_drivers_bundle(tmp_path):
    rows = [
        _row(top_drivers=["pull_stack", "vwap", "tape"]),
        _row(top_drivers=["pull_stack", "vwap", "tape"]),
        _row(top_drivers=["pull_stack", "vwap", "tape"]),
        _row(top_drivers=["pull_stack", "vwap"]),
        _row(top_drivers=[]),
        _row(top_drivers=None),
    ]
    _write_closed(tmp_path, rows)
    code, text = ler.run(_DATE, root=tmp_path, json_mode=True)
    groups = json.loads(text)["groups"]["top_drivers"]
    names = [g["name"] for g in groups]
    # Sorted-bundle key: pull_stack+vwap+tape -> pull_stack+tape+vwap.
    assert names[0] == "pull_stack+tape+vwap"
    assert "pull_stack+vwap" in names
    assert "none" in names
    none_group = next(g for g in groups if g["name"] == "none")
    assert none_group["n"] == 2  # empty list AND None both map to "none"
    triple = next(g for g in groups if g["name"] == "pull_stack+tape+vwap")
    assert triple["n"] == 3


def test_top_drivers_bundle_caps_at_three(tmp_path):
    rows = [
        _row(top_drivers=["pull_stack", "vwap", "tape", "orderbook", "micro"]),
    ]
    _write_closed(tmp_path, rows)
    code, text = ler.run(_DATE, root=tmp_path, json_mode=True)
    groups = json.loads(text)["groups"]["top_drivers"]
    # cap-3 picks [pull_stack, vwap, tape]; sorted -> pull_stack+tape+vwap.
    assert groups[0]["name"] == "pull_stack+tape+vwap"


# ---------------------------------------------------------------------------
# Sort order
# ---------------------------------------------------------------------------

def test_groups_sorted_by_n_desc_then_name_asc(tmp_path):
    rows = [
        # Three setups: A=1, B=2, C=2. Expected order B, C, A.
        _row(setup="A"),
        _row(setup="B"),
        _row(setup="B"),
        _row(setup="C"),
        _row(setup="C"),
    ]
    _write_closed(tmp_path, rows)
    code, text = ler.run(_DATE, root=tmp_path, json_mode=True)
    groups = json.loads(text)["groups"]["setup"]
    assert [g["name"] for g in groups] == ["B", "C", "A"]


# ---------------------------------------------------------------------------
# Formatters / CLI
# ---------------------------------------------------------------------------

def test_text_output_includes_key_sections(tmp_path):
    rows = [
        _row(setup="OR_BREAK_FOLLOW"),
        _row(setup="LEVEL_FADE_SHORT", direction="SHORT",
             realized_R_60s=-0.5, invalidated=True),
    ]
    _write_closed(tmp_path, rows)
    code, text = ler.run(_DATE, root=tmp_path, json_mode=False)
    assert code == 0
    for needle in (
            "Pax AI level-edge report",
            "Closed signals: 2",
            "Horizons:",
            "+15s",
            "+60s",
            "+300s",
            "Invalidated:",
            "By setup:",
            "By direction:",
            "By size_tier:",
            "By level_label:",
            "By confidence bucket:",
            "By top_driver bundle:",
            "OR_BREAK_FOLLOW",
            "LEVEL_FADE_SHORT",
    ):
        assert needle in text, f"missing section: {needle!r}"


def test_json_output_stable_shape(tmp_path):
    _write_closed(tmp_path, [_row()])
    code, text = ler.run(_DATE, root=tmp_path, json_mode=True)
    payload = json.loads(text)
    assert set(payload.keys()) == {
        "date", "path", "n_signals", "n_skipped",
        "horizons", "invalidated", "groups",
    }
    assert set(payload["horizons"].keys()) == {"15s", "60s", "300s"}
    for h in payload["horizons"].values():
        assert set(h.keys()) == {"n", "hit_rate", "mean_R", "median_R"}
    assert set(payload["invalidated"].keys()) == {"count", "rate", "n"}
    assert set(payload["groups"].keys()) == {
        "setup", "direction", "size_tier", "level_label",
        "confidence_bucket", "top_drivers",
    }
    for grp in payload["groups"].values():
        for row in grp:
            assert set(row.keys()) == {
                "name", "n", "hit_rate_60s", "mean_R_60s",
                "median_R_60s", "invalidated_rate",
            }


def test_cli_exits_zero_on_empty_day(tmp_path, capsys):
    code = ler.main(["--date", _DATE, "--root", str(tmp_path)])
    assert code == 0


def test_cli_logs_are_not_mutated(tmp_path):
    """Running the report must NOT touch the input file."""
    rows = [_row()]
    path = _write_closed(tmp_path, rows)
    before_bytes = path.read_bytes()
    before_mtime = path.stat().st_mtime
    code, _text = ler.run(_DATE, root=tmp_path, json_mode=False)
    assert code == 0
    after_bytes = path.read_bytes()
    after_mtime = path.stat().st_mtime
    assert before_bytes == after_bytes
    assert before_mtime == after_mtime


# ---------------------------------------------------------------------------
# Group-row stat correctness
# ---------------------------------------------------------------------------

def test_group_row_uses_60s_horizon_for_hit_rate(tmp_path):
    rows = [
        _row(setup="A", realized_R_15s=+10.0, realized_R_60s=-0.5,
             realized_R_300s=+0.0),
        _row(setup="A", realized_R_15s=+10.0, realized_R_60s=-0.25,
             realized_R_300s=+0.0),
    ]
    _write_closed(tmp_path, rows)
    code, text = ler.run(_DATE, root=tmp_path, json_mode=True)
    g = json.loads(text)["groups"]["setup"][0]
    # Both 60s values are negative -> hit_rate_60s = 0.0
    assert g["hit_rate_60s"] == pytest.approx(0.0)
    # Mean of (-0.5, -0.25) = -0.375
    assert g["mean_R_60s"] == pytest.approx(-0.375)


# ---------------------------------------------------------------------------
# Slice 5 patch: top_drivers bundle is sorted for analytical grouping
# ---------------------------------------------------------------------------


def test_top_drivers_bundle_sorts_alphabetically():
    # Same triplet in three orderings -> identical bundle key.
    a = ler._top_drivers_bundle(["pull_stack", "vwap", "tape"])
    b = ler._top_drivers_bundle(["vwap", "tape", "pull_stack"])
    c = ler._top_drivers_bundle(["tape", "pull_stack", "vwap"])
    assert a == b == c == "pull_stack+tape+vwap"


def test_top_drivers_bundle_caps_then_sorts():
    # cap-3 of [pull_stack, vwap, tape, orderbook, micro] -> [p, v, t];
    # sorted -> pull_stack+tape+vwap. orderbook/micro must NOT appear
    # because they fall outside the cap.
    name = ler._top_drivers_bundle(
        ["pull_stack", "vwap", "tape", "orderbook", "micro"])
    assert name == "pull_stack+tape+vwap"
    assert "orderbook" not in name
    assert "micro" not in name


def test_top_drivers_bundle_does_not_mutate_source():
    src = ["pull_stack", "vwap", "tape"]
    before = list(src)
    _ = ler._top_drivers_bundle(src)
    assert src == before


def test_top_drivers_bundle_empty_returns_none_after_patch():
    assert ler._top_drivers_bundle([]) == "none"
    assert ler._top_drivers_bundle(None) == "none"
    assert ler._top_drivers_bundle([""]) == "none"
    assert ler._top_drivers_bundle([None]) == "none"


def test_group_consolidates_different_impact_orderings(tmp_path):
    """Three jitter-orderings of the same triplet must group to one row
    with n=3 — the original failure pinned in Slice 5 audit."""
    rows = [
        _row(top_drivers=["pull_stack", "orderbook", "tape"]),
        _row(top_drivers=["pull_stack", "tape", "orderbook"]),
        _row(top_drivers=["tape", "pull_stack", "orderbook"]),
    ]
    _write_closed(tmp_path, rows)
    code, text = ler.run(_DATE, root=tmp_path, json_mode=True)
    groups = json.loads(text)["groups"]["top_drivers"]
    assert len(groups) == 1
    assert groups[0]["name"] == "orderbook+pull_stack+tape"
    assert groups[0]["n"] == 3


def test_group_top_drivers_row_objects_unchanged(tmp_path):
    """Running the report must not reorder the row's top_drivers field
    in the on-disk JSONL or in the parsed dict. (Pin: bundle sort is a
    grouping-key transform; it must not mutate inputs.)"""
    rows = [_row(top_drivers=["tape", "pull_stack", "orderbook"])]
    path = _write_closed(tmp_path, rows)
    before_bytes = path.read_bytes()
    _ = ler.run(_DATE, root=tmp_path, json_mode=True)
    after_bytes = path.read_bytes()
    assert before_bytes == after_bytes


def test_confidence_bucket_unknown_for_missing(tmp_path):
    rows = [_row(confidence=None)]
    _write_closed(tmp_path, rows)
    code, text = ler.run(_DATE, root=tmp_path, json_mode=True)
    groups = json.loads(text)["groups"]["confidence_bucket"]
    assert groups[0]["name"] == "unknown"
