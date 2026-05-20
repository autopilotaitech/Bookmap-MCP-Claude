"""Phase 3A bus digest renderer tests.

Bus digest uses a NEW block layout (STATE / ANCHOR / GATES / LEVELS /
MICROSTRUCTURE / RECENT_EVENTS / POSITION / SESSION_MEMORY / USER) that is
INTENTIONALLY DIFFERENT from the legacy digest (router_hint + SNAPSHOT
DIGEST + USER). Tests assert deterministic rendering and required blocks,
NOT byte-equality with the legacy digest."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from pax_ai import bus_digest, feature_bus


def _enable_bus_at(tmp_path, monkeypatch):
    db_path  = tmp_path / "pax-bus.db"
    from pax_ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_reload_if_stale", lambda: None)
    monkeypatch.setattr(cfg_mod, "_CACHE", {
        **cfg_mod._CACHE,
        "feature_bus": {
            "enabled":           True,
            "db_path":           str(db_path),
            "snapshot_blob_dir": str(tmp_path / "s"),
            "digest_blob_dir":   str(tmp_path / "d"),
            "queue_max":         2000,
            "writer_idle_ms":    20,
            "capture_ms":        20,
            "retention_days":    30,
        },
    })
    return db_path


def _disable_bus(tmp_path, monkeypatch):
    from pax_ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_reload_if_stale", lambda: None)
    monkeypatch.setattr(cfg_mod, "_CACHE", {
        **cfg_mod._CACHE,
        "feature_bus": {
            "enabled":           False,
            "db_path":           str(tmp_path / "nope.db"),
            "snapshot_blob_dir": str(tmp_path / "s"),
            "digest_blob_dir":   str(tmp_path / "d"),
            "queue_max":         2000,
            "writer_idle_ms":    100,
            "capture_ms":        1000,
            "retention_days":    30,
        },
    })


SNAP_FIXTURE = {
    "alias": "NQM6",
    "health": "ok",
    "book": {"mid": 23450.5, "spread": 0.25, "bestBid": 23450.25, "bestAsk": 23450.5},
    "or_levels": {"orHigh": 23475.0, "orLow": 23440.0, "orWidthPts": 35.0,
                   "levels": [{"label": "OR-H", "price": 23475.0, "distance": 24.5,
                                 "decision": "FOLLOW_LONG", "confidence": 0.7}],
                   "middleLock": False, "inProximity": True},
    "flow": {"regime": "TRENDING_UP", "regimeConfidence": 0.7,
              "biasScore": 0.4, "biasTrajectory": "RISING"},
    "conviction": {"score": 0.55, "trend": "BULL", "anchorMode": "LIVE",
                    "trajectory": "RISING"},
    "trend_signal": {"kind": "STRONG_BULL", "eligible": True,
                      "renderableKind": "STRONG_BULL"},
    "vwap_bias": {"label": "BULLISH", "components": {"sigma_z": 1.2}},
    "vp_bias": {"label": "ABOVE_VA", "components": {"va_state": "ABOVE_VA"}},
    "gates": {"session": {"code": "ACTIVE", "anchorMode": "LIVE",
                            "anchorHHMM": "08:30",
                            "anchorTimezone": "America/Chicago"},
                "news": {"blocked": False, "label": "clear"}},
    "micro_events": {"events": []},
    "position": {"position": 0, "entryPrice": 0, "pnl": 0},
    "pax": {"decision": "WAIT", "size_tier": "NONE"},
    "bridgeError": None,
}


# -- block-presence + ordering -----------------------------------------------

def test_render_returns_string():
    out = bus_digest.render_user_message(
        snap=SNAP_FIXTURE, user_text="ping",
        router_hint="ROUTER: pax-or", alias="NQM6", ts_ms=1_000_000_000_000)
    assert isinstance(out, str) and len(out) > 0


def test_render_includes_router_hint():
    """router_hint MUST appear in the output - never silently dropped."""
    out = bus_digest.render_user_message(
        snap=SNAP_FIXTURE, user_text="ping",
        router_hint="ROUTER: pax-or", alias="NQM6", ts_ms=1_000_000_000_000)
    assert "ROUTER: pax-or" in out


def test_render_block_order_is_stable():
    """Block markers must appear in documented order."""
    out = bus_digest.render_user_message(
        snap=SNAP_FIXTURE, user_text="ping",
        router_hint="ROUTER: pax-or", alias="NQM6", ts_ms=1_000_000_000_000)
    markers = ["[STATE]", "[ANCHOR]", "[GATES]", "[LEVELS]",
                "[MICROSTRUCTURE]", "[RECENT_EVENTS]", "[POSITION]",
                "[SESSION_MEMORY]", "[USER]"]
    positions = [out.find(m) for m in markers]
    assert all(p >= 0 for p in positions), f"missing markers: {list(zip(markers, positions))}"
    assert positions == sorted(positions), \
        f"blocks out of order: {list(zip(markers, positions))}"


def test_render_includes_user_text_verbatim():
    out = bus_digest.render_user_message(
        snap=SNAP_FIXTURE, user_text="what is the OR bias right now?",
        router_hint="ROUTER: pax-or", alias="NQM6", ts_ms=1_000_000_000_000)
    assert "what is the OR bias right now?" in out


def test_render_is_deterministic(tmp_path, monkeypatch):
    """Same inputs -> identical output bytes (hash-stable). Bus DB is
    enabled but empty so RECENT_EVENTS / SESSION_MEMORY are quiet."""
    _enable_bus_at(tmp_path, monkeypatch)
    out1 = bus_digest.render_user_message(
        snap=SNAP_FIXTURE, user_text="ping",
        router_hint="ROUTER: pax-or", alias="NQM6", ts_ms=1_000_000_000_000)
    out2 = bus_digest.render_user_message(
        snap=SNAP_FIXTURE, user_text="ping",
        router_hint="ROUTER: pax-or", alias="NQM6", ts_ms=1_000_000_000_000)
    assert out1 == out2
    assert hashlib.sha256(out1.encode()).hexdigest() == \
            hashlib.sha256(out2.encode()).hexdigest()


def test_render_changes_when_user_text_changes(tmp_path, monkeypatch):
    _enable_bus_at(tmp_path, monkeypatch)
    a = bus_digest.render_user_message(snap=SNAP_FIXTURE, user_text="ping",
        router_hint="ROUTER: pax-or", alias="NQM6", ts_ms=1)
    b = bus_digest.render_user_message(snap=SNAP_FIXTURE, user_text="pong",
        router_hint="ROUTER: pax-or", alias="NQM6", ts_ms=1)
    assert a != b


def test_render_changes_when_alias_changes(tmp_path, monkeypatch):
    _enable_bus_at(tmp_path, monkeypatch)
    nqm_snap = {**SNAP_FIXTURE, "alias": "NQM6"}
    esm_snap = {**SNAP_FIXTURE, "alias": "ESM6"}
    a = bus_digest.render_user_message(snap=nqm_snap, user_text="ping",
        router_hint="ROUTER: pax-or", alias="NQM6", ts_ms=1)
    b = bus_digest.render_user_message(snap=esm_snap, user_text="ping",
        router_hint="ROUTER: pax-or", alias="ESM6", ts_ms=1)
    assert a != b


# -- bus-disabled / DB-missing safety ----------------------------------------

def test_render_quiet_when_feature_bus_disabled(tmp_path, monkeypatch):
    """When feature_bus.enabled=False, RECENT_EVENTS and SESSION_MEMORY render
    quietly (no rows). The render still succeeds."""
    _disable_bus(tmp_path, monkeypatch)
    out = bus_digest.render_user_message(
        snap=SNAP_FIXTURE, user_text="ping",
        router_hint="ROUTER: pax-or", alias="NQM6", ts_ms=1)
    # All required markers still present, just empty bodies.
    assert "[RECENT_EVENTS]" in out
    assert "[SESSION_MEMORY]" in out


def test_render_quiet_when_db_missing(tmp_path, monkeypatch):
    """Bus enabled but DB hasn't been created - render must not raise."""
    _enable_bus_at(tmp_path, monkeypatch)
    out = bus_digest.render_user_message(
        snap=SNAP_FIXTURE, user_text="ping",
        router_hint="ROUTER: pax-or", alias="NQM6", ts_ms=1)
    assert "[RECENT_EVENTS]" in out
    assert "[SESSION_MEMORY]" in out


# -- block content sanity ----------------------------------------------------

def test_state_block_contains_alias_and_mid():
    out = bus_digest.render_user_message(
        snap=SNAP_FIXTURE, user_text="ping",
        router_hint="ROUTER: pax-or", alias="NQM6", ts_ms=1)
    state_start = out.find("[STATE]")
    next_block  = out.find("[ANCHOR]", state_start)
    state_body  = out[state_start:next_block]
    assert "NQM6"   in state_body
    assert "23450.5" in state_body


def test_anchor_block_contains_anchor_mode():
    out = bus_digest.render_user_message(
        snap=SNAP_FIXTURE, user_text="ping",
        router_hint="ROUTER: pax-or", alias="NQM6", ts_ms=1)
    anchor_start = out.find("[ANCHOR]")
    next_block   = out.find("[GATES]", anchor_start)
    anchor_body  = out[anchor_start:next_block]
    assert "LIVE" in anchor_body


def test_levels_block_contains_or_h_row():
    out = bus_digest.render_user_message(
        snap=SNAP_FIXTURE, user_text="ping",
        router_hint="ROUTER: pax-or", alias="NQM6", ts_ms=1)
    levels_start = out.find("[LEVELS]")
    next_block   = out.find("[MICROSTRUCTURE]", levels_start)
    levels_body  = out[levels_start:next_block]
    assert "OR-H" in levels_body


def test_recent_events_block_picks_up_seeded_trigger(tmp_path, monkeypatch):
    db = _enable_bus_at(tmp_path, monkeypatch)
    with feature_bus._open_db(db) as conn:
        feature_bus._ensure_schema(conn)
        conn.execute("""
            INSERT INTO trigger_events
              (schema_version, ts_ms, alias, kind, severity, label, snapshot_ts_ms)
            VALUES (1, ?, 'NQM6', 'TREND_SIGNAL_FIRE', 'HIGH', 'STRONG_BULL', ?)
        """, (1_700_000_000_000, 1_700_000_000_000))
    out = bus_digest.render_user_message(
        snap=SNAP_FIXTURE, user_text="ping",
        router_hint="ROUTER: pax-or", alias="NQM6", ts_ms=1)
    re_start = out.find("[RECENT_EVENTS]")
    next_block = out.find("[POSITION]", re_start)
    re_body = out[re_start:next_block]
    assert "TREND_SIGNAL_FIRE" in re_body


def test_session_memory_block_picks_up_seeded_ai_turn(tmp_path, monkeypatch):
    db = _enable_bus_at(tmp_path, monkeypatch)
    with feature_bus._open_db(db) as conn:
        feature_bus._ensure_schema(conn)
        conn.execute("""
            INSERT INTO ai_turns
              (schema_version, ts_ms, chat_run_id, deep, model,
               user_text_raw, user_text_normalized, pax_text,
               snapshot_alias, snapshot_sha256, digest_sha256, aborted)
            VALUES (1, ?, 'r1', 0, 'claude-haiku-4-5',
                    'earlier ping', 'earlier ping', 'OMITTED',
                    'NQM6', ?, ?, 0)
        """, (1_700_000_000_000, 's' * 64, 'd' * 64))
    out = bus_digest.render_user_message(
        snap=SNAP_FIXTURE, user_text="ping",
        router_hint="ROUTER: pax-or", alias="NQM6", ts_ms=1)
    sm_start = out.find("[SESSION_MEMORY]")
    next_block = out.find("[USER]", sm_start)
    sm_body = out[sm_start:next_block]
    # claude-haiku-4-5 or earlier ping (or both) should appear
    assert "earlier ping" in sm_body or "claude-haiku-4-5" in sm_body


def test_render_session_memory_before_ts_ms_excludes_later_rows(tmp_path, monkeypatch):
    """The SESSION_MEMORY block honors session_memory_before_ts_ms so replay
    can rebuild a digest that matches capture-time semantics."""
    db = _enable_bus_at(tmp_path, monkeypatch)
    base = 1_700_000_000_000
    with feature_bus._open_db(db) as conn:
        feature_bus._ensure_schema(conn)
        for i in range(3):
            conn.execute("""
                INSERT INTO ai_turns
                  (schema_version, ts_ms, chat_run_id, deep, model,
                   user_text_raw, user_text_normalized, pax_text,
                   snapshot_alias, snapshot_sha256, digest_sha256, aborted)
                VALUES (1, ?, 'r1', 0, 'claude-haiku-4-5',
                        ?, ?, 'OMITTED', 'NQM6', ?, ?, 0)
            """, (base + i, f"turn {i}", f"turn {i}",
                  's' * 64, 'd' * 64))

    # With before=base+2, only turns 0 and 1 are visible.
    out = bus_digest.render_user_message(
        snap=SNAP_FIXTURE, user_text="ping",
        router_hint="ROUTER: pax-or", alias="NQM6", ts_ms=base + 2,
        session_memory_before_ts_ms=base + 2)
    sm_start = out.find("[SESSION_MEMORY]")
    next_block = out.find("[USER]", sm_start)
    sm_body = out[sm_start:next_block]
    assert "turn 0" in sm_body
    assert "turn 1" in sm_body
    assert "turn 2" not in sm_body


def test_render_session_memory_before_ts_ms_none_includes_all(tmp_path, monkeypatch):
    db = _enable_bus_at(tmp_path, monkeypatch)
    base = 1_700_000_000_000
    with feature_bus._open_db(db) as conn:
        feature_bus._ensure_schema(conn)
        for i in range(2):
            conn.execute("""
                INSERT INTO ai_turns
                  (schema_version, ts_ms, chat_run_id, deep, model,
                   user_text_raw, user_text_normalized, pax_text,
                   snapshot_alias, snapshot_sha256, digest_sha256, aborted)
                VALUES (1, ?, 'r1', 0, 'claude-haiku-4-5',
                        ?, ?, 'OMITTED', 'NQM6', ?, ?, 0)
            """, (base + i, f"turn {i}", f"turn {i}",
                  's' * 64, 'd' * 64))
    out = bus_digest.render_user_message(
        snap=SNAP_FIXTURE, user_text="ping",
        router_hint="ROUTER: pax-or", alias="NQM6", ts_ms=base + 100)
    sm_start = out.find("[SESSION_MEMORY]")
    next_block = out.find("[USER]", sm_start)
    sm_body = out[sm_start:next_block]
    assert "turn 0" in sm_body
    assert "turn 1" in sm_body
