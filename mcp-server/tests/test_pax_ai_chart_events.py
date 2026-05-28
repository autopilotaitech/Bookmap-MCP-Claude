"""Pin compute_pax_ai_chart_events / read_pax_ai_chart_events.

The dashboard reads validated AI signals from
D:\\BookmapLogs\\pax-ai-chart-signals.jsonl (written by Pax AI's chat
handler), TTL-filters, dedupes by id (newest timestamp_ms wins), maps
each row to a chart-event dict shaped like PaxInstitutionalChartEvent,
and surfaces them as snap["pax_ai_chart_events"].

Action -> event_type / severity mapping:
  PAY_FOR_TRADE    -> AI_ACCEPTANCE,   ENTRY
  WAIT_FOR_CONFIRM -> AI_WATCH,        WATCH
  STAND_DOWN       -> AI_STAND_DOWN,   WARNING
  SCRATCH_READY    -> AI_SCRATCH,      EXIT

The map MUST be deterministic so the Java side renders the same severity
the user saw in chat. Negative tests pin that an offline snapshot or a
missing store yields the empty list, NOT an error.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from bookmap_mcp.pax_ai_chart_events import (
    read_pax_ai_chart_events,
    pax_ai_row_to_chart_event,
    compute_pax_ai_chart_events_status,
    AI_BULL_COLOR,
    AI_BEAR_COLOR,
    AI_NEUTRAL_COLOR,
)


_REQUIRED_FIELDS = (
    "id", "alias", "label", "price", "side", "event_type", "direction",
    "execution_read", "marker_text", "marker_color_hint", "severity",
    "timestamp_ms", "source", "confidence", "reason_codes",
)


def _write_signal(p, **kw):
    base = dict(
        id="pax_ai|abc", alias="NQM6.CME@RITHMIC", label="OR-H",
        price=20000.0, side="above",
        action="PAY_FOR_TRADE", direction="LONG",
        confidence=0.72, reason="acc + WITH",
        timestamp_ms=int(time.time() * 1000),
        source="pax_ai",
    )
    base.update(kw)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(base) + "\n")


def test_pay_for_trade_long_maps_to_ai_acceptance_entry(tmp_path):
    p = tmp_path / "store.jsonl"
    _write_signal(p)
    evs = read_pax_ai_chart_events(store_path=p,
                                    now_ms=int(time.time() * 1000) + 1)
    assert len(evs) == 1
    e = evs[0]
    for f in _REQUIRED_FIELDS:
        assert f in e, f"missing field {f}"
    assert e["event_type"] == "AI_ACCEPTANCE"
    assert e["severity"] == "ENTRY"
    assert e["direction"] == "LONG"
    assert e["execution_read"] == "PAY_FOR_TRADE"
    assert e["source"] == "pax_ai"
    assert e["marker_color_hint"] == AI_BULL_COLOR
    assert e["marker_text"].startswith("AI^")
    assert "ORH" in e["marker_text"]
    assert "72" in e["marker_text"]


def test_pay_for_trade_short_uses_bear_color(tmp_path):
    p = tmp_path / "store.jsonl"
    _write_signal(p, id="pax_ai|s1", direction="SHORT", side="below",
                  label="OR-L", price=19950.0)
    e = read_pax_ai_chart_events(store_path=p,
                                  now_ms=int(time.time() * 1000) + 1)[0]
    assert e["direction"] == "SHORT"
    assert e["marker_color_hint"] == AI_BEAR_COLOR


def test_wait_for_confirm_maps_to_ai_watch(tmp_path):
    p = tmp_path / "store.jsonl"
    _write_signal(p, id="pax_ai|w1", action="WAIT_FOR_CONFIRM",
                  direction="NONE", confidence=0.4)
    e = read_pax_ai_chart_events(store_path=p,
                                  now_ms=int(time.time() * 1000) + 1)[0]
    assert e["event_type"] == "AI_WATCH"
    assert e["severity"] == "WATCH"
    assert e["execution_read"] == "WAIT_FOR_CONFIRM"
    assert e["marker_color_hint"] == AI_NEUTRAL_COLOR


def test_stand_down_maps_to_ai_warning(tmp_path):
    p = tmp_path / "store.jsonl"
    _write_signal(p, id="pax_ai|sd1", action="STAND_DOWN", direction="NONE",
                  reason="iceberg defending")
    e = read_pax_ai_chart_events(store_path=p,
                                  now_ms=int(time.time() * 1000) + 1)[0]
    assert e["event_type"] == "AI_STAND_DOWN"
    assert e["severity"] == "WARNING"


def test_scratch_ready_maps_to_ai_exit(tmp_path):
    p = tmp_path / "store.jsonl"
    _write_signal(p, id="pax_ai|sc1", action="SCRATCH_READY", direction="NONE",
                  reason="flip detected")
    e = read_pax_ai_chart_events(store_path=p,
                                  now_ms=int(time.time() * 1000) + 1)[0]
    assert e["event_type"] == "AI_SCRATCH"
    assert e["severity"] == "EXIT"


def test_no_file_returns_empty_no_error():
    evs = read_pax_ai_chart_events(store_path=Path("/no/such/path.jsonl"),
                                    now_ms=int(time.time() * 1000))
    assert evs == []


def test_ttl_drops_old_signals(tmp_path):
    p = tmp_path / "store.jsonl"
    _write_signal(p, id="pax_ai|expired", timestamp_ms=0)
    evs = read_pax_ai_chart_events(store_path=p,
                                    now_ms=10 * 60 * 1000)
    assert evs == []


def test_dedup_keeps_newest_timestamp(tmp_path):
    p = tmp_path / "store.jsonl"
    _write_signal(p, id="pax_ai|dup", confidence=0.3, timestamp_ms=1)
    _write_signal(p, id="pax_ai|dup", confidence=0.9, timestamp_ms=100)
    evs = read_pax_ai_chart_events(store_path=p, now_ms=200)
    assert len(evs) == 1
    assert evs[0]["confidence"] == 0.9


def test_partial_last_line_does_not_break_read(tmp_path):
    p = tmp_path / "store.jsonl"
    _write_signal(p, id="pax_ai|good", timestamp_ms=1)
    with open(p, "ab") as f:
        f.write(b'{"id":"pax_ai|partial","timest')
    evs = read_pax_ai_chart_events(store_path=p, now_ms=10)
    assert len(evs) == 1
    assert evs[0]["id"] == "pax_ai|good"


def test_marker_text_includes_source_prefix_direction_and_score(tmp_path):
    p = tmp_path / "store.jsonl"
    _write_signal(p, confidence=0.555)
    e = read_pax_ai_chart_events(store_path=p,
                                  now_ms=int(time.time() * 1000) + 1)[0]
    # Format: "AI ▲ OR-H 56" or similar.
    assert e["marker_text"].startswith("AI^")
    # Score rounded to int 0..100
    assert "56" in e["marker_text"] or "55" in e["marker_text"]


def test_reason_passes_through_reason_codes(tmp_path):
    p = tmp_path / "store.jsonl"
    _write_signal(p, reason="hidden bid absorbing on TOUCH")
    e = read_pax_ai_chart_events(store_path=p,
                                  now_ms=int(time.time() * 1000) + 1)[0]
    assert "hidden bid absorbing on TOUCH" in e["reason_codes"][0]


# --- Alias filtering (multi-instrument safety) ----------------------------
#
# Pax AI's JSONL store is a single shared file across all instruments.
# When the dashboard composes a snapshot for alias X, only AI signals
# whose alias == X may appear in snap["pax_ai_chart_events"]. Otherwise
# a signal generated for NQM6 could leak onto an ESM6 chart.


def test_compute_pax_ai_chart_events_filters_to_current_alias(tmp_path, monkeypatch):
    """compute_pax_ai_chart_events(snap) reads the JSONL, then drops any
    row whose alias doesn't match snap['alias']."""
    from bookmap_mcp import pax_ai_chart_events as mod
    p = tmp_path / "store.jsonl"
    monkeypatch.setattr(mod, "DEFAULT_STORE_PATH", p)
    _write_signal(p, id="pax_ai|nq1", alias="NQM6.CME@RITHMIC", label="OR-H")
    _write_signal(p, id="pax_ai|es1", alias="ESM6.CME@RITHMIC", label="OR-H")
    snap_nq = {"alias": "NQM6.CME@RITHMIC", "health": "ok"}
    out = mod.compute_pax_ai_chart_events(snap_nq)
    assert len(out) == 1
    assert out[0]["alias"] == "NQM6.CME@RITHMIC"


def test_read_pax_ai_chart_events_alias_arg_filters(tmp_path):
    p = tmp_path / "store.jsonl"
    _write_signal(p, id="pax_ai|nq1", alias="NQM6.CME@RITHMIC")
    _write_signal(p, id="pax_ai|es1", alias="ESM6.CME@RITHMIC")
    nq = read_pax_ai_chart_events(store_path=p, alias="NQM6.CME@RITHMIC",
                                    now_ms=int(time.time() * 1000) + 1)
    es = read_pax_ai_chart_events(store_path=p, alias="ESM6.CME@RITHMIC",
                                    now_ms=int(time.time() * 1000) + 1)
    assert len(nq) == 1 and nq[0]["alias"] == "NQM6.CME@RITHMIC"
    assert len(es) == 1 and es[0]["alias"] == "ESM6.CME@RITHMIC"


def test_read_pax_ai_chart_events_no_alias_arg_returns_all(tmp_path):
    """When alias is None or empty string, the reader does NOT filter --
    callers that want all rows (e.g. ad-hoc inspection) keep the legacy
    behavior. The dashboard composer DOES pass an alias, so production
    snapshots are always filtered."""
    p = tmp_path / "store.jsonl"
    _write_signal(p, id="pax_ai|nq1", alias="NQM6.CME@RITHMIC")
    _write_signal(p, id="pax_ai|es1", alias="ESM6.CME@RITHMIC")
    out = read_pax_ai_chart_events(store_path=p,
                                    now_ms=int(time.time() * 1000) + 1)
    assert len(out) == 2


def test_compute_with_missing_snap_alias_returns_all(tmp_path, monkeypatch):
    """Documented safe behavior: a snapshot without an alias (developer
    smoke / partial payload) yields all active rows. Production code
    paths always set snap['alias']; this is a fallback for tooling."""
    from bookmap_mcp import pax_ai_chart_events as mod
    p = tmp_path / "store.jsonl"
    monkeypatch.setattr(mod, "DEFAULT_STORE_PATH", p)
    _write_signal(p, id="pax_ai|nq1", alias="NQM6.CME@RITHMIC")
    _write_signal(p, id="pax_ai|es1", alias="ESM6.CME@RITHMIC")
    out = mod.compute_pax_ai_chart_events({"health": "ok"})
    assert len(out) == 2


# --- Reader-side strict (action, direction) combo defense -----------------
#
# The Pax AI writer already enforces these combos before appending to
# JSONL. But D:\\BookmapLogs\\pax-ai-chart-signals.jsonl can be hand-edited
# for ops / debugging. The dashboard reader is the LAST gate before a
# marker reaches the Java chart layer, so it must enforce the same rules
# independently.


def _direct_event(action, direction):
    """Bypass the writer; build a raw row dict as if hand-edited."""
    return {
        "id":           f"pax_ai|{action}|{direction}",
        "alias":        "NQM6.CME@RITHMIC",
        "label":        "OR-H",
        "price":        20000.0,
        "side":         "above",
        "action":       action,
        "direction":    direction,
        "confidence":   0.5,
        "reason":       "hand-edited",
        "timestamp_ms": int(time.time() * 1000),
        "source":       "pax_ai",
    }


def test_pay_for_trade_with_none_dropped_by_row_mapper():
    from bookmap_mcp.pax_ai_chart_events import pax_ai_row_to_chart_event
    assert pax_ai_row_to_chart_event(_direct_event("PAY_FOR_TRADE", "NONE")) is None


def test_wait_for_confirm_with_long_dropped():
    from bookmap_mcp.pax_ai_chart_events import pax_ai_row_to_chart_event
    assert pax_ai_row_to_chart_event(_direct_event("WAIT_FOR_CONFIRM", "LONG")) is None


def test_wait_for_confirm_with_short_dropped():
    from bookmap_mcp.pax_ai_chart_events import pax_ai_row_to_chart_event
    assert pax_ai_row_to_chart_event(_direct_event("WAIT_FOR_CONFIRM", "SHORT")) is None


def test_stand_down_with_long_dropped():
    from bookmap_mcp.pax_ai_chart_events import pax_ai_row_to_chart_event
    assert pax_ai_row_to_chart_event(_direct_event("STAND_DOWN", "LONG")) is None


def test_stand_down_with_short_dropped():
    from bookmap_mcp.pax_ai_chart_events import pax_ai_row_to_chart_event
    assert pax_ai_row_to_chart_event(_direct_event("STAND_DOWN", "SHORT")) is None


def test_scratch_ready_with_long_dropped():
    from bookmap_mcp.pax_ai_chart_events import pax_ai_row_to_chart_event
    assert pax_ai_row_to_chart_event(_direct_event("SCRATCH_READY", "LONG")) is None


def test_scratch_ready_with_short_dropped():
    from bookmap_mcp.pax_ai_chart_events import pax_ai_row_to_chart_event
    assert pax_ai_row_to_chart_event(_direct_event("SCRATCH_READY", "SHORT")) is None


def test_unknown_action_dropped():
    from bookmap_mcp.pax_ai_chart_events import pax_ai_row_to_chart_event
    assert pax_ai_row_to_chart_event(_direct_event("BUY_THE_DIP", "LONG")) is None


def test_unknown_direction_dropped():
    from bookmap_mcp.pax_ai_chart_events import pax_ai_row_to_chart_event
    assert pax_ai_row_to_chart_event(_direct_event("PAY_FOR_TRADE", "UP")) is None


def test_pay_for_trade_long_still_accepted():
    from bookmap_mcp.pax_ai_chart_events import pax_ai_row_to_chart_event
    out = pax_ai_row_to_chart_event(_direct_event("PAY_FOR_TRADE", "LONG"))
    assert out is not None
    assert out["event_type"] == "AI_ACCEPTANCE"
    assert out["direction"] == "LONG"


def test_pay_for_trade_short_still_accepted():
    from bookmap_mcp.pax_ai_chart_events import pax_ai_row_to_chart_event
    out = pax_ai_row_to_chart_event(_direct_event("PAY_FOR_TRADE", "SHORT"))
    assert out is not None
    assert out["event_type"] == "AI_ACCEPTANCE"
    assert out["direction"] == "SHORT"


def test_bias_signal_short_dropped_for_unanchored_popup_trigger():
    from bookmap_mcp.pax_ai_chart_events import pax_ai_row_to_chart_event
    assert pax_ai_row_to_chart_event(_direct_event("BIAS_SIGNAL", "SHORT")) is None


def test_wait_for_confirm_none_still_accepted():
    from bookmap_mcp.pax_ai_chart_events import pax_ai_row_to_chart_event
    out = pax_ai_row_to_chart_event(_direct_event("WAIT_FOR_CONFIRM", "NONE"))
    assert out is not None
    assert out["event_type"] == "AI_WATCH"


def test_stand_down_none_still_accepted():
    from bookmap_mcp.pax_ai_chart_events import pax_ai_row_to_chart_event
    out = pax_ai_row_to_chart_event(_direct_event("STAND_DOWN", "NONE"))
    assert out is not None
    assert out["event_type"] == "AI_STAND_DOWN"


def test_scratch_ready_none_still_accepted():
    from bookmap_mcp.pax_ai_chart_events import pax_ai_row_to_chart_event
    out = pax_ai_row_to_chart_event(_direct_event("SCRATCH_READY", "NONE"))
    assert out is not None
    assert out["event_type"] == "AI_SCRATCH"


def test_hand_edited_invalid_combo_never_reaches_compute_output(tmp_path, monkeypatch):
    """End-to-end: a hand-edited JSONL row with an invalid combo must
    not appear in compute_pax_ai_chart_events output even if it passed
    TTL + alias filters."""
    from bookmap_mcp import pax_ai_chart_events as mod
    p = tmp_path / "store.jsonl"
    monkeypatch.setattr(mod, "DEFAULT_STORE_PATH", p)
    bad = _direct_event("PAY_FOR_TRADE", "NONE")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(bad) + "\n")
    out = mod.compute_pax_ai_chart_events({"alias": "NQM6.CME@RITHMIC",
                                            "health": "ok"})
    assert out == []


def test_expired_rows_dropped_end_to_end(tmp_path, monkeypatch):
    """Audit pin: a row whose timestamp_ms < (now - TTL*1000) must NOT
    appear in compute_pax_ai_chart_events output."""
    from bookmap_mcp import pax_ai_chart_events as mod
    p = tmp_path / "store.jsonl"
    monkeypatch.setattr(mod, "DEFAULT_STORE_PATH", p)
    # Row whose ts_ms = 0 (epoch). At any current time > TTL_SEC, it expires.
    _write_signal(p, id="pax_ai|old", alias="NQM6.CME@RITHMIC",
                  timestamp_ms=0)
    # Inject a fake "now" by also passing now_ms via the lower-level
    # reader to be explicit. The composer reads real time.time() so we
    # exercise the production code path by also confirming the reader
    # path it delegates to.
    out = mod.read_pax_ai_chart_events(store_path=p,
                                        alias="NQM6.CME@RITHMIC",
                                        now_ms=(mod.DEFAULT_TTL_SEC + 60) * 1000)
    assert out == []


def test_status_exposes_expired_valid_rows(tmp_path, monkeypatch):
    from bookmap_mcp import pax_ai_chart_events as mod
    p = tmp_path / "store.jsonl"
    monkeypatch.setattr(mod, "DEFAULT_STORE_PATH", p)
    _write_signal(p, id="pax_ai|old", alias="NQM6.CME@RITHMIC",
                  action="WAIT_FOR_CONFIRM", direction="NONE",
                  timestamp_ms=1_000)

    status = compute_pax_ai_chart_events_status({
        "alias": "NQM6.CME@RITHMIC", "health": "ok",
    })

    assert status["store_exists"] is True
    assert status["rows_total"] == 1
    assert status["rows_expired"] == 1
    assert status["rows_mapped"] == 0
    assert status["newest_action"] == "WAIT_FOR_CONFIRM"
    assert status["newest_age_ms"] is not None


def test_status_exposes_unknown_action_rows(tmp_path, monkeypatch):
    from bookmap_mcp import pax_ai_chart_events as mod
    p = tmp_path / "store.jsonl"
    monkeypatch.setattr(mod, "DEFAULT_STORE_PATH", p)
    row = _direct_event("BIAS_SIGNAL", "SHORT")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")

    status = compute_pax_ai_chart_events_status({
        "alias": "NQM6.CME@RITHMIC", "health": "ok",
    })

    assert status["rows_total"] == 1
    assert status["rows_unknown_action"] == 1
    assert status["rows_mapped"] == 0
    assert status["newest_action"] == "BIAS_SIGNAL"
