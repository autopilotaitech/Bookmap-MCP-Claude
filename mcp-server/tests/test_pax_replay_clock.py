"""Unit tests for pax_replay_clock — the pure offline replay clock helper.

The helper must NEVER read the wall clock: every function derives time from the
saved record / snapshot / replay_input only. These tests pin that purity and the
documented source-priority order so weekend (Saturday/Sunday) replay of a
weekday-recorded tape uses the recorded weekday time, not "today".
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bookmap_mcp import pax_replay_clock as C   # noqa: E402

# A real WEDNESDAY instant (2026-05-27 18:00:00 UTC == 13:00 CDT). Used to prove
# replay derives the recorded weekday regardless of the actual run day.
WED_UTC = datetime.datetime(2026, 5, 27, 18, 0, 0, tzinfo=datetime.timezone.utc)
WED_MS = int(WED_UTC.timestamp() * 1000)


# ── replay_now_ms: source priority ─────────────────────────────────────────

def test_now_ms_prefers_replay_input():
    res = C.resolve_clock(
        record={"ts_ms": 222},
        snapshot={"marketDataAsOfMs": 333},
        replay_input={"now_ms": 111})
    assert res.ts_ms == 111
    assert res.source == "replay_input.now_ms"
    assert C.replay_now_ms({"ts_ms": 222}, {"marketDataAsOfMs": 333},
                           {"now_ms": 111}) == 111


def test_now_ms_falls_to_record_ts():
    res = C.resolve_clock(record={"ts_ms": 222},
                          snapshot={"marketDataAsOfMs": 333},
                          replay_input=None)
    assert res.ts_ms == 222
    assert res.source == "record.ts_ms"


def test_now_ms_falls_to_market_data_as_of():
    res = C.resolve_clock(record={}, snapshot={"marketDataAsOfMs": 333},
                          replay_input=None)
    assert res.ts_ms == 333
    assert res.source == "snapshot.marketDataAsOfMs"


def test_now_ms_falls_to_market_as_of():
    res = C.resolve_clock(record={}, snapshot={"marketAsOfMs": 444},
                          replay_input=None)
    assert res.ts_ms == 444
    assert res.source == "snapshot.marketAsOfMs"


def test_now_ms_uses_event_ms_then_updated_at():
    assert C.resolve_clock({}, {"eventMs": 555}, None).source == \
        "snapshot.eventMs"
    assert C.resolve_clock({}, {"updatedAtMs": 666}, None).source == \
        "snapshot.updatedAtMs"


def test_composed_at_ms_is_NOT_a_clock_source():
    # composedAtMs is dashboard compose time, never a real replay clock.
    res = C.resolve_clock(record={}, snapshot={"composedAtMs": 999},
                          replay_input=None)
    assert res.ts_ms == 0
    assert res.source == "none"


def test_no_clock_returns_zero_not_wallclock():
    res = C.resolve_clock(record={}, snapshot={}, replay_input=None)
    assert res.ts_ms == 0
    assert res.source == "none"
    assert res.has_real_clock is False


# ── datetime projection ─────────────────────────────────────────────────────

def test_replay_now_dt_utc_roundtrip():
    assert C.replay_now_dt_utc(WED_MS) == WED_UTC


def test_replay_now_dt_zero_is_epoch():
    assert C.replay_now_dt_utc(0) == datetime.datetime(
        1970, 1, 1, tzinfo=datetime.timezone.utc)


def test_ct_projection_is_chicago():
    dt_ct = C.replay_now_dt_ct(WED_MS)
    # 18:00 UTC in May (CDT, UTC-5) -> 13:00 CT, still Wednesday.
    assert dt_ct.hour == 13
    assert dt_ct.strftime("%a") == "Wed"


# ── weekend purity: derived weekday is the RECORD's weekday ──────────────────

def test_weekday_is_recorded_not_today():
    # No wall clock is read; the resolved weekday is Wednesday no matter what
    # day this test runs (e.g. Saturday).
    s = C.session_clock_summary(record={"ts_ms": WED_MS}, snapshot={},
                                replay_input=None)
    assert s["weekday_ct"] == "Wed"
    assert s["source"] == "record.ts_ms"
    assert s["has_real_clock"] is True
    assert s["utc_iso"].startswith("2026-05-27T18:00")
    assert s["ct_iso"].startswith("2026-05-27T13:00")


# ── market age (freshness) ──────────────────────────────────────────────────

def test_market_age_prefers_embedded_replay_input():
    age = C.market_age_sec_for_replay(
        snapshot={"marketDataAsOfMs": 1000}, now_ms=100000,
        replay_input={"market_age_sec": 7.0})
    assert age == 7.0


def test_market_age_from_market_data_as_of():
    age = C.market_age_sec_for_replay(
        snapshot={"marketDataAsOfMs": 90000}, now_ms=150000, replay_input=None)
    assert age == 60.0


def test_market_age_never_uses_composed_at_ms():
    # Only composedAtMs present -> no real freshness -> None (limitation).
    age = C.market_age_sec_for_replay(
        snapshot={"composedAtMs": 90000}, now_ms=150000, replay_input=None)
    assert age is None


def test_market_age_missing_is_none():
    assert C.market_age_sec_for_replay(snapshot={}, now_ms=1,
                                       replay_input=None) is None


# ── heartbeat age ───────────────────────────────────────────────────────────

def test_heartbeat_age_prefers_replay_input():
    assert C.heartbeat_age_sec_for_replay(
        record={"heartbeat_age_sec": 99.0},
        replay_input={"heartbeat_age_sec": 4.0}) == 4.0


def test_heartbeat_age_from_record():
    assert C.heartbeat_age_sec_for_replay(
        record={"heartbeat_age_sec": 12.0}, replay_input=None) == 12.0


def test_heartbeat_age_missing_is_none():
    assert C.heartbeat_age_sec_for_replay(record={}, replay_input=None) is None


# ── purity guard: module reads no wall clock ────────────────────────────────

def test_module_does_not_read_wall_clock():
    src = Path(C.__file__).read_text(encoding="utf-8")
    for forbidden in ("time.time(", "datetime.now(", "datetime.utcnow(",
                      "date.today(", ".now()"):
        assert forbidden not in src, f"{forbidden} must not be in pax_replay_clock"
