"""Tests for the operator-driven OR session-config bridge."""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

import pytest

from bookmap_mcp import or_session


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Point or_session at a tmp dir for the duration of the test, blocking
    the well-known production paths so a real local file can't bleed into
    the test."""
    cfg_path = tmp_path / "or-session-config.json"
    monkeypatch.setenv("BOOKMAP_OR_SESSION_CONFIG", str(cfg_path))
    # Force or_session.candidate_paths() to return ONLY the tmp path so
    # any pre-existing C:/D: file can't leak into the assertion.
    from bookmap_mcp import or_session
    monkeypatch.setattr(or_session, "candidate_paths", lambda: [cfg_path])
    yield cfg_path


def _write(cfg_path, **overrides):
    payload = {
        "version":       1,
        "updatedAtMs":   int(time.time() * 1000),
        "timezone":      "America/Chicago",
        "startHour":     8,
        "startMinute":   30,
        "startSecond":   0,
        "rangeSeconds":  30,
        "endHour":       8,
        "endMinute":     30,
        "labelPrefix":   "OpenRange",
        "daysToDisplay": 8,
        "source":        "openrange-indicator",
    }
    payload.update(overrides)
    cfg_path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def test_load_effective_fallback_when_no_file(isolated_config):
    eff = or_session.load_effective()
    assert eff["available"] is False
    assert eff["source"] == "fallback"
    assert eff["config"]["startHour"] == 8
    assert eff["config"]["startMinute"] == 30
    assert eff["reason"]


def test_load_effective_returns_operator_config(isolated_config):
    _write(isolated_config, startHour=9, startMinute=30, rangeSeconds=15)
    eff = or_session.load_effective()
    assert eff["available"] is True
    assert eff["source"] == "or_config"
    assert eff["config"]["startHour"] == 9
    assert eff["config"]["startMinute"] == 30
    assert eff["config"]["rangeSeconds"] == 15
    assert eff["path"] == str(isolated_config)


def test_load_effective_marks_stale_config(isolated_config):
    # updatedAtMs in the distant past
    _write(isolated_config, updatedAtMs=1_000_000)
    eff = or_session.load_effective()
    assert eff["available"] is False
    assert eff["source"] == "stale_or_config"
    assert eff["ageMs"] is not None and eff["ageMs"] > 0


def test_load_effective_rejects_invalid_config(isolated_config):
    isolated_config.write_text(
        json.dumps({"version": 1, "startHour": 25, "startMinute": 0,
                    "rangeSeconds": 30, "endHour": 17, "endMinute": 0,
                    "timezone": "America/Chicago",
                    "updatedAtMs": int(time.time() * 1000)}),
        encoding="utf-8",
    )
    eff = or_session.load_effective()
    assert eff["available"] is False
    assert eff["source"] == "fallback"
    assert "out of range" in (eff["reason"] or "")


def test_effective_session_anchor_shape(isolated_config):
    _write(isolated_config, startHour=9, startMinute=15, rangeSeconds=45,
           startSecond=0)
    a = or_session.effective_session_anchor()
    assert a["hour"] == 9
    assert a["minute"] == 15
    assert a["rangeSeconds"] == 45
    assert a["source"] == "or_config"
    assert a["available"] is True
    assert a["timezone"] == "America/Chicago"


def test_dashboard_or_signal_globs_follow_published_log_directory(isolated_config):
    from bookmap_mcp import dashboard

    _write(isolated_config,
           logDirectory="D:\\ConfiguredOR",
           logDirectoryAbsolute="D:\\ConfiguredOR")
    globs = dashboard._or_signal_globs()
    assert globs == [str(Path("D:\\ConfiguredOR") / "openrange-signals-*.csv")]


def test_dashboard_or_signal_globs_use_bookmap_config_for_old_schema(isolated_config, monkeypatch):
    from bookmap_mcp import dashboard

    monkeypatch.setenv("BOOKMAP_CONFIG_DIR", "E:\\BookmapConfig")
    _write(isolated_config)

    globs = dashboard._or_signal_globs()
    assert globs == [
        str(Path("E:\\BookmapConfig") / "build" / "logs" / "openrange-signals-*.csv")
    ]


def test_session_state_uses_or_config(isolated_config):
    """session_state honors the operator-published OR anchor, not a
    hard-coded canonical time."""
    from bookmap_mcp import dashboard
    from zoneinfo import ZoneInfo
    CT = ZoneInfo("America/Chicago")

    # Operator sets OR start at 09:30 CT for a 60s window.
    _write(isolated_config, startHour=9, startMinute=30, startSecond=0,
           rangeSeconds=60)

    # 08:30 CT — under PREVIOUS day's 09:30 anchor → ACTIVE.
    just_before = dt.datetime(2026, 5, 19, 8, 30, 0, tzinfo=CT)
    assert dashboard.session_state(just_before)[0] == "ACTIVE"
    # 09:30:00 CT today — OR_FORMING begins.
    at_anchor = dt.datetime(2026, 5, 19, 9, 30, 0, tzinfo=CT)
    assert dashboard.session_state(at_anchor)[0] == "OR_FORMING"
    # 09:30:59 CT today — still inside the 60s OR window.
    inside_or = dt.datetime(2026, 5, 19, 9, 30, 59, tzinfo=CT)
    assert dashboard.session_state(inside_or)[0] == "OR_FORMING"
    # 09:31:00 CT today — ACTIVE.
    after_or = dt.datetime(2026, 5, 19, 9, 31, 0, tzinfo=CT)
    assert dashboard.session_state(after_or)[0] == "ACTIVE"


def test_session_state_uses_fallback_when_no_config(isolated_config):
    """Without an OR config the dashboard falls back to canonical 08:30 CT
    (documented behavior; label carries the fallback marker)."""
    from bookmap_mcp import dashboard
    from zoneinfo import ZoneInfo
    CT = ZoneInfo("America/Chicago")

    # No file written; fallback used.
    assert not isolated_config.exists()

    forming = dt.datetime(2026, 5, 19, 8, 30, 5, tzinfo=CT)
    code, label = dashboard.session_state(forming)
    assert code == "OR_FORMING"
    # Label carries source marker so operators see they're on fallback.
    assert "fallback" in label or "OR" in label


def test_or_row_rejects_yesterday_after_current_or_open(monkeypatch):
    """Dashboard must not keep serving yesterday's OR row after today's
    OR anchor has advanced."""
    from bookmap_mcp import dashboard
    from zoneinfo import ZoneInfo

    anchor = {
        "hour": 8, "minute": 30, "second": 0, "rangeSeconds": 30,
        "timezone": "America/Chicago", "source": "or_config",
        "anchorMode": "LIVE", "available": True, "reason": None,
        "ageMs": 1000, "updatedAtMs": 1, "path": "test",
    }
    monkeypatch.setattr(or_session, "effective_session_anchor", lambda: anchor)
    now = dt.datetime(2026, 5, 22, 8, 31, 0,
                      tzinfo=ZoneInfo("America/Chicago"))
    stale = {"time": "2026-05-21T15:00:00", "symbol": "NQM6",
             "orHigh": "100", "orLow": "90"}
    assert dashboard._or_row_is_current_session(stale, now) is False


def test_or_row_accepts_current_row_after_or_complete(monkeypatch):
    from bookmap_mcp import dashboard
    from zoneinfo import ZoneInfo

    anchor = {
        "hour": 8, "minute": 30, "second": 0, "rangeSeconds": 30,
        "timezone": "America/Chicago", "source": "or_config",
        "anchorMode": "LIVE", "available": True, "reason": None,
        "ageMs": 1000, "updatedAtMs": 1, "path": "test",
    }
    monkeypatch.setattr(or_session, "effective_session_anchor", lambda: anchor)
    now = dt.datetime(2026, 5, 22, 8, 31, 0,
                      tzinfo=ZoneInfo("America/Chicago"))
    current = {"time": "2026-05-22T08:30:30", "symbol": "NQM6",
               "orHigh": "100", "orLow": "90"}
    assert dashboard._or_row_is_current_session(current, now) is True


def test_or_row_rejects_before_current_or_complete(monkeypatch):
    from bookmap_mcp import dashboard
    from zoneinfo import ZoneInfo

    anchor = {
        "hour": 8, "minute": 30, "second": 0, "rangeSeconds": 30,
        "timezone": "America/Chicago", "source": "or_config",
        "anchorMode": "LIVE", "available": True, "reason": None,
        "ageMs": 1000, "updatedAtMs": 1, "path": "test",
    }
    monkeypatch.setattr(or_session, "effective_session_anchor", lambda: anchor)
    now = dt.datetime(2026, 5, 22, 8, 30, 15,
                      tzinfo=ZoneInfo("America/Chicago"))
    row = {"time": "2026-05-21T15:00:00", "symbol": "NQM6",
           "orHigh": "100", "orLow": "90"}
    assert dashboard._or_row_is_current_session(row, now) is False


def test_freshest_config_wins_across_candidates(tmp_path, monkeypatch):
    """When multiple candidate config files exist, the freshest valid one
    wins regardless of path priority."""
    # Build a tmp_path/A/or-session-config.json (old) and
    # tmp_path/B/or-session-config.json (new) and force or_session to
    # prefer A first by env override — newest must still win.
    old_dir = tmp_path / "A"; old_dir.mkdir()
    new_dir = tmp_path / "B"; new_dir.mkdir()
    old_path = old_dir / "or-session-config.json"
    new_path = new_dir / "or-session-config.json"

    now_ms = int(time.time() * 1000)
    old_payload = {"version": 1, "updatedAtMs": now_ms - 5_000,
                   "timezone": "America/Chicago",
                   "startHour": 8, "startMinute": 30, "startSecond": 0,
                   "rangeSeconds": 30, "endHour": 8, "endMinute": 30,
                   "labelPrefix": "OR", "daysToDisplay": 8,
                   "source": "openrange-indicator"}
    new_payload = dict(old_payload)
    new_payload["updatedAtMs"] = now_ms
    new_payload["startHour"] = 9
    new_payload["startMinute"] = 30
    old_path.write_text(json.dumps(old_payload), encoding="utf-8")
    new_path.write_text(json.dumps(new_payload), encoding="utf-8")

    # Force or_session to check old_path FIRST (high priority) then new_path.
    from bookmap_mcp import or_session
    monkeypatch.setattr(or_session, "candidate_paths",
                         lambda: [old_path, new_path])
    eff = or_session.load_effective()
    assert eff["available"] is True
    # Freshest wins → 09:30, not 08:30.
    assert eff["config"]["startHour"] == 9
    assert eff["config"]["startMinute"] == 30
    assert eff["path"] == str(new_path)


def test_invalid_newest_valid_older_selects_older(tmp_path, monkeypatch):
    """If the freshest candidate is INVALID and an older candidate is
    valid, the reader must select the older valid one (not fall back)."""
    from bookmap_mcp import or_session
    a = tmp_path / "newest-invalid.json"
    b = tmp_path / "older-valid.json"
    now_ms = int(time.time() * 1000)
    # Newest: invalid (startHour out of range).
    a.write_text(json.dumps({
        "version": 1, "updatedAtMs": now_ms,
        "timezone": "America/Chicago",
        "startHour": 99, "startMinute": 0, "startSecond": 0,
        "rangeSeconds": 30, "endHour": 9, "endMinute": 30,
    }), encoding="utf-8")
    # Older: valid.
    b.write_text(json.dumps({
        "version": 1, "updatedAtMs": now_ms - 10_000,
        "timezone": "America/Chicago",
        "startHour": 9, "startMinute": 30, "startSecond": 0,
        "rangeSeconds": 30, "endHour": 9, "endMinute": 30,
    }), encoding="utf-8")
    monkeypatch.setattr(or_session, "candidate_paths", lambda: [a, b])
    eff = or_session.load_effective()
    assert eff["available"] is True
    assert eff["source"] == "or_config"
    assert eff["anchorMode"] == "LIVE"
    assert eff["config"]["startHour"] == 9     # the OLDER valid wins
    assert eff["path"] == str(b)
    # Diagnostics record the invalid candidate.
    assert any(r["path"] == str(a) and "invalid" in r["reason"]
               for r in eff["rejections"]), eff["rejections"]


def test_stale_config_marks_last_known_stale(tmp_path, monkeypatch):
    """A valid-but-stale config is reported as LAST_KNOWN_STALE so the
    decision gates can fail safe without silently treating it as live."""
    from bookmap_mcp import or_session
    p = tmp_path / "stale.json"
    p.write_text(json.dumps({
        "version": 1, "updatedAtMs": 1_000_000_000,    # very old
        "timezone": "America/Chicago",
        "startHour": 9, "startMinute": 30, "startSecond": 0,
        "rangeSeconds": 30, "endHour": 9, "endMinute": 30,
    }), encoding="utf-8")
    monkeypatch.setattr(or_session, "candidate_paths", lambda: [p])
    eff = or_session.load_effective()
    assert eff["available"] is False
    assert eff["anchorMode"] == "LAST_KNOWN_STALE"
    assert eff["source"] == "stale_or_config"
    # Config payload retained so VWAP/VP can keep computing with last-known.
    assert eff["config"]["startHour"] == 9


def test_all_invalid_falls_back_with_diagnostics(tmp_path, monkeypatch):
    """When every candidate is invalid, we fall back AND surface a full
    rejection list so the operator can debug."""
    from bookmap_mcp import or_session
    a = tmp_path / "junk-a.json"; a.write_text("not json", encoding="utf-8")
    b = tmp_path / "junk-b.json"; b.write_text("{\"bad\": true}", encoding="utf-8")
    monkeypatch.setattr(or_session, "candidate_paths", lambda: [a, b])
    eff = or_session.load_effective()
    assert eff["available"] is False
    assert eff["anchorMode"] == "FALLBACK"
    assert eff["source"] == "fallback"
    assert len(eff["rejections"]) == 2


def test_trade_decision_waits_when_anchor_not_live(tmp_path, monkeypatch):
    """v18 stale policy: trade_decision must WAIT when anchorMode != LIVE."""
    from bookmap_mcp import dashboard
    snap = {
        "alias": "NQM6",
        "gates": {
            "session": {
                "code": "ACTIVE",
                "anchorMode": "LAST_KNOWN_STALE",
                "anchorReason": "updatedAtMs is stale (age 99999999ms)",
            },
            "news": {"blocked": False, "label": "clear"},
        },
        "or_row": {"bias": "BULLISH", "confidence": "HIGH",
                    "orHigh": 17005, "orLow": 16995},
        "book": {"mid": 17000}, "vwap": 17000,
        "momentum": {"flag": "neutral"},
        "vwap_obj": {},
        "position": {"position": 0},
    }
    out = dashboard.trade_decision(snap)
    assert out["decision"] == "WAIT"
    assert "anchorMode=LAST_KNOWN_STALE".lower() in (" ".join(out["reasons"])).lower() \
        or "not LIVE" in " ".join(out["reasons"])


def test_pax_decision_waits_when_anchor_not_live(monkeypatch):
    """v18 stale policy: pax_decision must WAIT when anchorMode != LIVE."""
    from bookmap_mcp import dashboard
    snap = {
        "alias": "NQM6", "health": "ok",
        "gates": {
            "session": {"code": "ACTIVE", "anchorMode": "FALLBACK",
                         "anchorReason": "no OR config"},
            "news": {"blocked": False},
        },
        "book": {"mid": 17000}, "or_levels": {"orWidthPts": 10, "levels": []},
        "vwap_bias": {}, "vp_bias": {},
    }
    out = dashboard.pax_decision(snap)
    assert out["decision"] == "WAIT"
    assert any("anchorMode" in r or "FALLBACK" in r or "not LIVE" in r
               for r in out.get("reasons", []))


def test_conviction_exposes_source_base_weights():
    """compute_session_conviction must publish sourceBaseWeights so the
    Heatwave TA row can detect CAPPED/ZEROED accurately."""
    from bookmap_mcp import dashboard
    snap = {
        "health": "ok",
        "alias": "NQM6",
        "book":  {"mid": 17000.0},
        "trend_analyzer": {
            "warmedUp": True,
            "fast": {"direction": "UP", "directionSign": 1, "confidence": 80},
            "slow": {"direction": "UP", "directionSign": 1, "confidence": 70},
            "updatedAtMs": int(time.time() * 1000),
        },
        # Minimal stubs for the other sources so compute_session_conviction
        # doesn't choke.
        "flow":  {"alias": "NQM6", "regime": "BALANCED"},
        "vwap_obj": {}, "volume_profile": {},
        "tape_flow": {}, "pull_stack": {}, "lt_liquidity": {},
        "micro_events": {}, "vwap_bias": {}, "vp_bias": {},
        "or_levels": {"levels": []},
    }
    result = dashboard.compute_session_conviction(snap)
    assert result is not None
    assert "sourceBaseWeights" in result
    bw = result["sourceBaseWeights"]
    # Must include trend_analyzer and match the configured base weight.
    assert "trend_analyzer" in bw
    assert bw["trend_analyzer"] == pytest.approx(0.06)
    # Other source base weights must also be present.
    for k in ("flow_ofi", "regime", "vwap_dislocation", "ib_context"):
        assert k in bw
