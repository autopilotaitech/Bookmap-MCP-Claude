"""Unit tests for ifl_rich_signals deterministic chart-signal emitter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bookmap_mcp import ifl_rich_signals as rich


_ALIAS = "NQM6.CME@RITHMIC"


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path):
    p = tmp_path / "pax-ai-chart-signals.jsonl"
    prior = rich._override_paths(p)
    rich.reset_state()
    yield p
    rich._override_paths(prior)
    rich.reset_state()


def _snap(*, regime: str, mid: float, conviction: float = 0.6,
          levels=None, ts_ms: int = 1_700_000_000_000) -> dict:
    return {
        "ts_ms": ts_ms,
        "alias": _ALIAS,
        "book": {"mid": mid},
        "or_levels": {
            "levels": levels or [
                {"label": "OR-H", "price": 30105.75, "side": "above", "proximity": True},
                {"label": "+1", "price": 30170.75, "side": "above", "proximity": True},
                {"label": "+2", "price": 30235.75, "side": "above", "proximity": False},
            ],
        },
        "institutional_flow": {
            "alias": _ALIAS,
            "asOfMs": ts_ms,
            "regime": regime,
            "conviction": conviction,
            "weighted_vote": 0.27 if regime == "ACCUMULATION" else -0.27,
            "trend_filter": "ALIGNED",
            "drivers": [
                {"name": "micro_events", "signed": 0.85},
                {"name": "flow_ofi", "signed": 0.42},
                {"name": "lt_liquidity_slope", "signed": 0.30},
            ],
            "rotation_state": {"rotations_completed": 1},
        },
    }


def test_no_emit_when_balanced(_isolate):
    out = rich.emit_rich_signals(_snap(regime="BALANCED", mid=30000.0))
    assert out["emitted"] == 0


def test_emits_for_proximity_levels_during_accumulation(_isolate):
    p = _isolate
    out = rich.emit_rich_signals(_snap(regime="ACCUMULATION", mid=30100.0))
    # OR-H and +1 are proximity=True; +2 is False. So 2 rows expected.
    assert out["emitted"] == 2
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    parsed = [json.loads(l) for l in lines]
    labels = sorted(r["label"] for r in parsed)
    assert labels == ["+1", "OR-H"]
    for r in parsed:
        assert r["direction"] == "LONG"
        assert r["action"] == "BIAS_SIGNAL"
        assert r["source"] == "institutional_flow_rich"
        assert "STOP" in r["reason"]
        assert "FOLLOW" in r["reason"] or "FADE" in r["reason"]


def test_emits_short_for_distribution(_isolate):
    p = _isolate
    out = rich.emit_rich_signals(_snap(regime="DISTRIBUTION", mid=30100.0))
    assert out["emitted"] == 2
    parsed = [json.loads(l) for l in p.read_text(encoding="utf-8").strip().splitlines()]
    for r in parsed:
        assert r["direction"] == "SHORT"
        assert "STOP" in r["reason"]


def test_dedup_within_30s_bucket(_isolate):
    p = _isolate
    s = _snap(regime="ACCUMULATION", mid=30100.0)
    rich.emit_rich_signals(s)
    # Same bucket -> no new emit
    out2 = rich.emit_rich_signals(s)
    assert out2["emitted"] == 0


def test_emits_again_after_30s_bucket(_isolate):
    p = _isolate
    rich.emit_rich_signals(_snap(regime="ACCUMULATION", mid=30100.0, ts_ms=1_700_000_000_000))
    out2 = rich.emit_rich_signals(_snap(regime="ACCUMULATION", mid=30100.0, ts_ms=1_700_000_035_000))
    assert out2["emitted"] == 2


def test_proximity_false_levels_skipped(_isolate):
    p = _isolate
    rich.emit_rich_signals(_snap(regime="ACCUMULATION", mid=30100.0))
    parsed = [json.loads(l) for l in p.read_text(encoding="utf-8").strip().splitlines()]
    labels = [r["label"] for r in parsed]
    assert "+2" not in labels  # was proximity=False


def test_fade_short_setup_type():
    """SHORT + above-OR level = FADE SHORT (fade the rally at extension)."""
    out = rich._setup_type("DISTRIBUTION", "above", mid=30050.0, level_price=30100.0)
    assert out == "FADE SHORT"


def test_follow_long_setup_type():
    """LONG + above-OR level approached from below = FOLLOW LONG."""
    out = rich._setup_type("ACCUMULATION", "above", mid=30050.0, level_price=30100.0)
    assert out == "FOLLOW LONG"


def test_stop_for_fade_short_is_one_rung_above_level():
    """FADE SHORT at +2 (30235): stop = +3 (30300)."""
    stop = rich._compute_stop_price("DISTRIBUTION", "above", level_price=30235.0, rotation_unit=65.0)
    assert stop == 30300.0


def test_stop_for_follow_long_is_scratch_at_level():
    """FOLLOW LONG break above OR-H: stop = OR-H itself (scratch)."""
    stop = rich._compute_stop_price("ACCUMULATION", "above", level_price=30105.0, rotation_unit=65.0)
    assert stop == 30105.0


def test_graceful_on_missing_fields():
    out = rich.emit_rich_signals({})
    assert out["emitted"] == 0
