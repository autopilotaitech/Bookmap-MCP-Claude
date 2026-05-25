from __future__ import annotations

from pax_ai import trigger_chart_signal


def _snap():
    return {
        "health": "ok",
        "alias": "NQM6.CME@RITHMIC",
        "book": {"mid": 29540.25},
    }


def test_trend_trigger_does_not_become_chart_signal():
    trig = {
        "kind": "TREND_SIGNAL_FIRE",
        "severity": "MED",
        "label": "WEAK_BEAR",
        "headline": "WEAK_BEAR fired -- short bias",
        "details": "trend_signal mid=29531.25 bucketEnteredMs=1",
        "asOfMs": 12345,
    }
    assert trigger_chart_signal.trigger_to_signal(trig, _snap()) is None


def test_regime_trigger_does_not_become_chart_signal():
    trig = {
        "kind": "REGIME_CHANGE",
        "severity": "HIGH",
        "label": "EXHAUSTION_UP",
        "headline": "regime -> EXHAUSTION_UP (conf 0.53)",
        "details": "absorption/exhaustion entered",
        "asOfMs": 12345,
    }
    assert trigger_chart_signal.trigger_to_signal(trig, _snap()) is None


def test_record_trigger_chart_events_never_writes_popup_triggers(tmp_path, monkeypatch):
    from pax_ai import ai_chart_signal_store

    p = tmp_path / "signals.jsonl"
    monkeypatch.setattr(ai_chart_signal_store, "DEFAULT_STORE_PATH", p)
    trig = {
        "kind": "CONVICTION_FLIP",
        "severity": "HIGH",
        "label": "-1",
        "headline": "conviction crossed to bear",
        "details": "prev_sign=1 new_sign=-1",
        "asOfMs": 12345,
    }
    assert trigger_chart_signal.record_trigger_chart_events([trig], _snap()) == 0
    assert not p.exists()
