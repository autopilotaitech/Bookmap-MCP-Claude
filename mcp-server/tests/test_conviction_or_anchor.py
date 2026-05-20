"""v20 invariant: compute_session_conviction() must anchor at the OR UI
session, not a hard-coded 08:30 CT clock.

These tests pin:
  1. Custom OR anchor at 17:00 CT, now=19:00 CT  -> anchorIso = today 17:00 CT.
  2. Custom OR anchor at 17:00 CT, now=16:00 CT  -> anchorIso = yesterday 17:00 CT.
  3. Changing the OR anchor mid-stream resets per-alias session state
     (sessionSum / sessionCount / rings collapse to the current tick only).
  4. Conviction output exposes anchorMode / anchorSource / anchorReason /
     anchorHHMM / anchorTz so non-LIVE modes are observable without re-deriving.
  5. anchorMs and anchorIso match the OR-anchored ms / iso (not 08:30 CT).
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import Any, Dict
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest                                                                    # noqa: E402

import bookmap_mcp.dashboard as d                                                # noqa: E402
from bookmap_mcp import or_session                                               # noqa: E402


CT = ZoneInfo("America/Chicago")


def _fake_anchor(hour: int, minute: int,
                 *,
                 mode: str = "LIVE",
                 source: str = "or_config",
                 reason: Any = None,
                 tz: str = "America/Chicago",
                 path: str = "C:/BookmapLogs/or-session-config.json",
                 range_seconds: int = 30,
                 age_ms: int = 100) -> Dict[str, Any]:
    """Synthetic effective_session_anchor() return value for a given HH:MM."""
    return {
        "hour":         hour,
        "minute":       minute,
        "second":       0,
        "rangeSeconds": range_seconds,
        "timezone":     tz,
        "source":       source,
        "anchorMode":   mode,
        "available":    mode == "LIVE",
        "reason":       reason,
        "ageMs":        age_ms,
        "updatedAtMs":  1_700_000_000_000,
        "path":         path,
    }


def _minimal_snap(alias: str = "NQM6") -> Dict[str, Any]:
    """Smallest snapshot the conviction engine will accept."""
    return {
        "health": "ok",
        "alias":  alias,
        "book":   {"mid": 17000.0, "bestBid": 16999.75, "bestAsk": 17000.25},
    }


# ─── Test 1: custom 17:00 anchor, now after the anchor → today's anchor ─────

def test_conv_session_anchor_today_when_now_after(monkeypatch):
    monkeypatch.setattr(or_session, "effective_session_anchor",
                        lambda *a, **kw: _fake_anchor(17, 0))
    now = dt.datetime(2026, 5, 19, 19, 0, tzinfo=CT)
    anchor_ms, anchor_dt, meta = d._conv_session_anchor(now)
    expected = dt.datetime(2026, 5, 19, 17, 0, tzinfo=CT)
    assert anchor_dt == expected, anchor_dt
    assert anchor_ms == int(expected.timestamp() * 1000)
    assert meta["anchorHHMM"] == "17:00"
    assert meta["anchorTz"]   == "America/Chicago"
    assert meta["anchorMode"] == "LIVE"


# ─── Test 2: custom 17:00 anchor, now before today's anchor → yesterday ─────

def test_conv_session_anchor_yesterday_when_now_before(monkeypatch):
    monkeypatch.setattr(or_session, "effective_session_anchor",
                        lambda *a, **kw: _fake_anchor(17, 0))
    now = dt.datetime(2026, 5, 19, 16, 0, tzinfo=CT)
    anchor_ms, anchor_dt, meta = d._conv_session_anchor(now)
    expected = dt.datetime(2026, 5, 18, 17, 0, tzinfo=CT)
    assert anchor_dt == expected, anchor_dt
    assert anchor_ms == int(expected.timestamp() * 1000)


# ─── Test 3: changing OR anchor resets per-alias state ──────────────────────

def test_changing_or_anchor_resets_alias_state(monkeypatch):
    """Drive conviction under 08:30 anchor until sessionCount > 0, then switch
    OR config to 17:00; next compute must create fresh state."""
    d._CONVICTION_STATE.clear()
    alias = "NQM6.CME@RITHMIC"
    snap = _minimal_snap(alias)
    snap["flow"] = {"ofiZ": 1.2}  # one source with non-zero reliability

    # Phase A: anchor at 08:30 CT.
    monkeypatch.setattr(or_session, "effective_session_anchor",
                        lambda *a, **kw: _fake_anchor(8, 30))
    for _ in range(5):
        d.compute_session_conviction(snap)
    st_before = d._CONVICTION_STATE[alias]
    pre_anchor_ms = st_before["anchorMs"]
    flow_state = st_before["sources"]["flow_ofi"]
    assert flow_state["sessionCount"] >= 1, "test pre-condition: state must build"
    assert flow_state["sessionSum"]   != 0.0

    # Phase B: operator switches OR config to 17:00 CT.
    monkeypatch.setattr(or_session, "effective_session_anchor",
                        lambda *a, **kw: _fake_anchor(17, 0))
    d.compute_session_conviction(snap)

    st_after = d._CONVICTION_STATE[alias]
    assert st_after["anchorMs"] != pre_anchor_ms, "anchorMs must change on UI switch"
    # State was rebuilt: sessionCount collapses back to the just-pushed tick.
    new_flow = st_after["sources"]["flow_ofi"]
    assert new_flow["sessionCount"] == 1, new_flow
    # Score ring also collapsed.
    assert len(st_after["scoreRing"]) <= 1


# ─── Test 4: conviction output exposes anchor metadata ──────────────────────

def test_conviction_output_carries_anchor_metadata(monkeypatch):
    d._CONVICTION_STATE.clear()
    monkeypatch.setattr(or_session, "effective_session_anchor",
                        lambda *a, **kw: _fake_anchor(17, 0))
    out = d.compute_session_conviction(_minimal_snap())
    assert out is not None
    for key in ("anchorMs", "anchorIso", "anchorMode", "anchorSource",
                "anchorHHMM", "anchorTz", "anchorPath"):
        assert key in out, f"conviction output missing {key}"
    assert out["anchorMode"] == "LIVE"
    assert out["anchorHHMM"] == "17:00"
    assert out["anchorTz"]   == "America/Chicago"
    assert out["anchorSource"] == "or_config"
    # anchorIso must reflect the 17:00 CT anchor, not 08:30 CT.
    assert "T17:00:00" in out["anchorIso"], out["anchorIso"]
    assert "T08:30:00" not in out["anchorIso"]


# ─── Test 5: anchor metadata propagates through non-LIVE modes ──────────────

def test_conviction_output_reports_non_live_anchor_mode(monkeypatch):
    d._CONVICTION_STATE.clear()
    monkeypatch.setattr(or_session, "effective_session_anchor",
                        lambda *a, **kw: _fake_anchor(
                            8, 30,
                            mode="FALLBACK",
                            source="fallback",
                            reason="no valid or-session-config.json in any candidate path",
                            path=None,
                        ))
    out = d.compute_session_conviction(_minimal_snap())
    assert out is not None
    assert out["anchorMode"] == "FALLBACK"
    assert out["anchorSource"] == "fallback"
    assert out["anchorReason"]
    assert out["anchorPath"] is None


# ─── Test 6: pinned negative invariant — anchor must NOT be hard-coded 08:30
#            when OR config publishes a different anchor ─────────────────────

def test_no_hardcoded_08_30_when_or_config_is_17_00(monkeypatch):
    """Direct anti-regression: under a 17:00 OR config, conviction.anchorIso
    must not contain 08:30 (the historical hard-code)."""
    d._CONVICTION_STATE.clear()
    monkeypatch.setattr(or_session, "effective_session_anchor",
                        lambda *a, **kw: _fake_anchor(17, 0))
    out = d.compute_session_conviction(_minimal_snap())
    assert out is not None
    assert "08:30" not in out["anchorIso"], (
        f"anchorIso leaked the legacy 08:30 hard-code: {out['anchorIso']!r}"
    )
    assert out["anchorMs"] > 0
