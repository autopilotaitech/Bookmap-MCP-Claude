"""Bookmap replay/playback OR-session truth.

In Bookmap replay the tape (book.generatedNanos / trade nanos) is a PAST date
while the dashboard wall clock is today. The OR CSV row for the replay date must
be accepted (so PAX sees OR-H/OR-L) instead of being rejected as "not today".
composedAtMs / trend_analyzer.updatedAtMs are wall-clock-ish and must NEVER
drive replay session/tape time.

These tests pin the data-plumbing fix only (no strategy/threshold change).
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bookmap_mcp.dashboard as d                                              # noqa: E402
from bookmap_mcp.config import BridgeConfig                                    # noqa: E402

# 2026-05-29T13:35:10Z tape instant from the live-playback evidence.
MAY29_GEN_NANOS = 1780061710808058400
MAY29_TAPE_MS = MAY29_GEN_NANOS // 1_000_000
MAY29_TRADE_NANOS = 1780061715000000000  # a few seconds later

_HEADER = ("time,symbol,price,orHigh,orLow,cvdDelta,bidDepthDelta,askDepthDelta,"
           "netDepthDelta,cvdZ,psZ,cvdPercentile,psPercentile,location,distanceTicks,"
           "rangeWidth,rangeQuality,ageSeconds,bias,action,confidence,score,maxScore,"
           "evidence,reason")


def _row(symbol, or_high, or_low, time_iso, price=None):
    if price is None:
        price = (or_high + or_low) / 2
    return (f"{time_iso},{symbol},{price:.2f},{or_high:.2f},{or_low:.2f},"
            f"0,0,0,0,0,0,0,0,IN,0,0,GOOD,30,NEUTRAL,WAIT,LOW,0,1,\"\",\"\"")


def _write_csv(path, symbol, or_high, or_low, time_iso):
    path.write_text(_HEADER + "\n" + _row(symbol, or_high, or_low, time_iso) + "\n",
                    encoding="utf-8")


# ── derive_tape_time_ms (pure) ─────────────────────────────────────────────

def test_derive_tape_time_prefers_trade_nanos():
    book = {"generatedNanos": MAY29_GEN_NANOS}
    trades = [{"nanos": MAY29_TRADE_NANOS}, {"nanos": MAY29_GEN_NANOS}]
    assert d.derive_tape_time_ms(book, trades) == MAY29_TRADE_NANOS // 1_000_000


def test_derive_tape_time_falls_to_generated_nanos():
    assert d.derive_tape_time_ms({"generatedNanos": MAY29_GEN_NANOS}, []) == MAY29_TAPE_MS


def test_derive_tape_time_ignores_composed_at_ms():
    # composedAtMs is dashboard wall-clock and is NOT a tape time input.
    assert d.derive_tape_time_ms({"composedAtMs": 1780157232118}, []) is None


def test_derive_tape_time_ignores_trend_analyzer_updated_at():
    # updatedAtMs advances on replay wall-clock; never a tape time.
    assert d.derive_tape_time_ms({"updatedAtMs": 1780157232118}, []) is None


def test_derive_tape_time_none_when_absent():
    assert d.derive_tape_time_ms({}, []) is None
    assert d.derive_tape_time_ms(None, None) is None


# ── window acceptance keyed to the tape clock ──────────────────────────────

def test_or_rows_by_symbol_accepts_replay_date_with_tape_now(tmp_path, monkeypatch):
    nq = tmp_path / "openrange-signals-NQM6.csv"
    _write_csv(nq, "NQM6", 30404.75, 30364.75, "2026-05-29T08:35:43")
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])
    tape_now = dt.datetime(2026, 5, 29, 13, 36, 0, tzinfo=dt.timezone.utc)
    out = d._or_rows_by_symbol(now=tape_now)
    assert "NQM6" in out
    assert float(out["NQM6"]["orHigh"]) == pytest.approx(30404.75)


def test_or_rows_by_symbol_rejects_replay_date_under_wallclock_window(tmp_path, monkeypatch):
    # Explicit wall-clock 'now' a day later than the row -> row rejected.
    nq = tmp_path / "openrange-signals-NQM6.csv"
    _write_csv(nq, "NQM6", 30404.75, 30364.75, "2026-05-29T08:35:43")
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])
    wall_now = dt.datetime(2026, 5, 30, 16, 7, 0, tzinfo=dt.timezone.utc)
    out = d._or_rows_by_symbol(now=wall_now)
    assert out == {}


def test_build_or_rows_by_alias_accepts_replay_row_with_tape_now(tmp_path, monkeypatch):
    nq = tmp_path / "openrange-signals-NQM6.csv"
    _write_csv(nq, "NQM6", 30404.75, 30364.75, "2026-05-29T08:35:43")
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])
    instruments = {"instruments": [{"alias": "NQM6.CME@RITHMIC", "symbol": "NQM6"}]}
    tape_now = dt.datetime(2026, 5, 29, 13, 36, 0, tzinfo=dt.timezone.utc)
    out = d._build_or_rows_by_alias(["NQM6.CME@RITHMIC"], instruments, now=tape_now)
    assert out["NQM6.CME@RITHMIC"] is not None
    assert float(out["NQM6.CME@RITHMIC"]["orHigh"]) == pytest.approx(30404.75)


# ── compute_or_levels proximity regression (real-style row) ────────────────

@pytest.mark.parametrize("mid", [30402.0, 30405.0, 30411.0])
def test_compute_or_levels_in_proximity_near_or_high(mid):
    snap = {
        "alias": "NQM6.CME@RITHMIC",
        "or_row": {"orHigh": 30404.75, "orLow": 30364.75, "symbol": "NQM6"},
        "book": {"mid": mid},
    }
    ol = d.compute_or_levels(snap)
    assert ol is not None
    assert ol["orHigh"] == pytest.approx(30404.75)
    assert ol["orLow"] == pytest.approx(30364.75)
    assert ol["inProximity"] is True
    orh = next(l for l in ol["levels"] if l["label"] == "OR-H")
    assert orh["proximity"] is True


# ── full compose snapshot in playback ──────────────────────────────────────

def _playback_payload(alias, mid):
    return {
        "/orderbook": {"alias": alias, "bestBid": mid - 0.25, "bestAsk": mid + 0.25,
                       "mid": mid, "spread": 0.5, "bids": [], "asks": [],
                       "generatedNanos": MAY29_GEN_NANOS},
        "/recent_trades": {"alias": alias, "trades": [
            {"price": mid, "size": 1, "side": "buy", "nanos": MAY29_TRADE_NANOS}]},
        "/position": {"alias": alias, "size": 0, "avgPrice": 0.0},
        "/working_orders": {"alias": alias, "orders": []},
        "/balance": {"alias": alias, "balance": 100000.0},
        "/recent_fills": {"alias": alias, "fills": []},
        "/vwap": {"alias": alias, "vwap": mid + 0.1, "samples": 100},
        "/momentum": {"alias": alias, "regime": "BALANCED", "biasScore": 0.0},
        "/volume_profile": {"alias": alias},
        "/tape_buckets": {"alias": alias, "buckets": []},
        "/lt_liquidity": {"alias": alias},
        "/pull_stack": {"alias": alias, "stack": []},
        "/microstructure_events": {"alias": alias, "events": []},
        "/trend_analyzer": {"alias": alias, "warmedUp": False,
                            "updatedAtMs": 1780157232118},  # wall-clock-ish, must be ignored for tape
    }


class _PlaybackClient:
    def __init__(self, *_a, **_kw):
        self.posts = []
        self._instruments = None
    def __enter__(self): return self
    def __exit__(self, *exc): return None
    def get_json(self, path, params=None):
        params = params or {}
        alias = params.get("alias", "")
        if path == "/ping": return {"ok": True}
        if path == "/instruments": return self._instruments
        return _playback_payload(alias, 30405.0).get(path, {})
    def post_json(self, path, payload):
        self.posts.append((path, dict(payload)))
        return {}


@pytest.fixture
def playback_bridge(monkeypatch):
    fake_cfg = BridgeConfig(url="http://127.0.0.1:8765", token="t")
    monkeypatch.setattr(d.BridgeConfig, "load", classmethod(lambda cls: fake_cfg))
    holder = {"instruments": None}
    def _factory(*a, **kw):
        c = _PlaybackClient(*a, **kw)
        c._instruments = holder["instruments"]
        return c
    monkeypatch.setattr(d, "BridgeClient", _factory)
    monkeypatch.setattr(d, "_get_pax_collector", lambda: None)
    monkeypatch.setattr(d, "pax_record", lambda *a, **kw: None)
    monkeypatch.setattr(d, "_sync_bridge_config", lambda *a, **kw: None)
    return holder


@pytest.fixture(autouse=True)
def _reset_magnet_cache():
    with d._LAST_MAGNETS_LOCK:
        d._LAST_MAGNETS.clear()
    yield
    with d._LAST_MAGNETS_LOCK:
        d._LAST_MAGNETS.clear()


def test_playback_snapshot_populates_or_row_and_levels(playback_bridge, monkeypatch, tmp_path):
    nq = tmp_path / "openrange-signals-NQM6.csv"
    _write_csv(nq, "NQM6", 30404.75, 30364.75, "2026-05-29T08:35:43")
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])
    playback_bridge["instruments"] = {"instruments": [
        {"alias": "NQM6.CME@RITHMIC", "symbol": "NQM6"}]}

    snap = d.fetch_snapshot()
    nq_snap = snap["aliases"]["NQM6.CME@RITHMIC"]
    assert nq_snap["or_row"] is not None
    assert float(nq_snap["or_row"]["orHigh"]) == pytest.approx(30404.75)
    ol = nq_snap["or_levels"]
    assert ol is not None
    assert ol["orHigh"] == pytest.approx(30404.75)
    assert ol["orLow"] == pytest.approx(30364.75)
    # mid 30405 is within proxPts of OR-H 30404.75 -> inProximity true.
    assert ol["inProximity"] is True


def test_playback_session_label_reports_tape_date(playback_bridge, monkeypatch, tmp_path):
    nq = tmp_path / "openrange-signals-NQM6.csv"
    _write_csv(nq, "NQM6", 30404.75, 30364.75, "2026-05-29T08:35:43")
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])
    playback_bridge["instruments"] = {"instruments": [
        {"alias": "NQM6.CME@RITHMIC", "symbol": "NQM6"}]}

    snap = d.fetch_snapshot()
    nq_snap = snap["aliases"]["NQM6.CME@RITHMIC"]
    # session label must reflect the TAPE date (2026-05-29), not today's anchor.
    assert "2026-05-29" in nq_snap["session"]["label"]
    assert nq_snap["session"].get("clockSource") == "tape"
