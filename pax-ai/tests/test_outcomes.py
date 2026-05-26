"""Outcomes daemon tests (structured-forecast labeling).

Contract under test:
  - daemon disabled (outcomes.enabled=False) -> no thread, no writes
  - start/stop idempotent and never raise into the parent process
  - structured PAX_FORECAST lookup is the ONLY verdict source
        * raw user text "I'm not buying that level" is never ENTER_LONG
        * PAY_FOR_TRADE + LONG  -> ENTER_LONG with realized_r = mid_tN - mid_t0
        * PAY_FOR_TRADE + SHORT -> ENTER_SHORT with realized_r = mid_t0 - mid_tN
        * non-PAY forecasts (STAND_DOWN / WAIT_FOR_CONFIRM / SCRATCH_READY)
          -> verdict=<execution_read>, realized_r NULL, invalidated=0
  - invalidated/invalidation_reason populated:
        * SNAPSHOT_MISSING_AT_T0       (directional, t0 mid absent)
        * HORIZON_DATA_MISSING         (directional, t0 present but a forward
                                        horizon mid absent)
        * FORECAST_MISSING_OR_AMBIGUOUS (no unique structured forecast)
  - already-labeled rows are not relabeled
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest


# ----------------------------------------------------------------- helpers

def _patch_cache(monkeypatch, *, bus_db, forecast_db,
                  outcomes_enabled=True, writer_idle_ms=20):
    from pax_ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_reload_if_stale", lambda: None)
    monkeypatch.setattr(cfg_mod, "_CACHE", {
        **cfg_mod._CACHE,
        "feature_bus": {
            "enabled":           True,
            "db_path":           str(bus_db),
            "snapshot_blob_dir": str(Path(bus_db).parent / "s"),
            "digest_blob_dir":   str(Path(bus_db).parent / "d"),
            "queue_max":         2000,
            "writer_idle_ms":    writer_idle_ms,
            "capture_ms":        writer_idle_ms,
            "retention_days":    30,
        },
        "outcomes": {
            "enabled":            outcomes_enabled,
            "wake_interval_ms":   50,
            "match_tolerance_ms": 5_000,
        },
        "forecast": {
            "enabled":    True,
            "store_path": str(forecast_db) if forecast_db else "",
        },
    })


def _enable_outcomes(tmp_path, monkeypatch, *, with_forecast_db=True):
    bus_db = tmp_path / "pax-bus.db"
    fc_db  = (tmp_path / "pax-forecast.db") if with_forecast_db else None
    _patch_cache(monkeypatch, bus_db=bus_db, forecast_db=fc_db)
    return bus_db, fc_db


def _disable_outcomes(tmp_path, monkeypatch):
    bus_db = tmp_path / "pax-bus.db"
    fc_db  = tmp_path / "pax-forecast.db"
    _patch_cache(monkeypatch, bus_db=bus_db, forecast_db=fc_db,
                  outcomes_enabled=False, writer_idle_ms=100)
    return bus_db


def _seed_ai_turn(bus_db, *,
                   ai_ts_ms,
                   alias="NQM6",
                   chat_run_id="run-A",
                   digest_sha256="d" * 64,
                   snapshot_sha256="s" * 64,
                   user_text="enter long ping",
                   snapshots=((0, 100.0), (60, 101.0), (180, 102.5),
                              (300, 100.5), (900, 99.0))):
    """Insert one ai_turn plus optional snapshot_features rows.

    ``snapshots`` is an iterable of (offset_s, mid) pairs anchored at
    ``ai_ts_ms``. Pass ``snapshots=()`` to seed NO snapshots.
    """
    from pax_ai import feature_bus
    with feature_bus._open_db(bus_db) as conn:
        feature_bus._ensure_schema(conn)
        cur = conn.execute("""
            INSERT INTO ai_turns
              (schema_version, ts_ms, chat_run_id, deep, model,
               user_text_raw, user_text_normalized, pax_text,
               snapshot_alias, snapshot_sha256, digest_sha256, aborted)
            VALUES (1, ?, ?, 0, 'claude-haiku-4-5',
                    ?, ?, 'OMITTED',
                    ?, ?, ?, 0)
        """, (ai_ts_ms, chat_run_id, user_text, user_text,
              alias, snapshot_sha256, digest_sha256))
        ai_turn_id = cur.lastrowid
        for offset_s, mid in snapshots:
            conn.execute("""
                INSERT INTO snapshot_features
                  (schema_version, ts_ms, alias, health, mid)
                VALUES (1, ?, ?, 'ok', ?)
            """, (ai_ts_ms + offset_s * 1000, alias, mid))
    return ai_turn_id


def _seed_forecast(forecast_db, *,
                    chat_run_id="run-A",
                    digest_sha256="d" * 64,
                    snapshot_sha256="s" * 64,
                    alias="NQM6",
                    execution_read="PAY_FOR_TRADE",
                    direction="LONG",
                    horizon_sec=300,
                    ts_ms=1_000,
                    forecast_id=None):
    """Persist a structured forecast via PaxForecastStore so the schema
    matches the production capture path exactly."""
    from bookmap_mcp.pax_forecast_store import PaxForecastStore
    record = {
        "schema_version": 1,
        "ts_ms":          ts_ms,
        "source_turn_id": None,
        "alias":          alias,
        "level":          "OR-H",
        "thesis":         "ACCEPTANCE_LONG",
        "execution_read": execution_read,
        "direction":      direction,
        "horizon_sec":    horizon_sec,
        "prob_success":   0.6,
        "expected_r":     0.5,
        "invalidation":   "stub",
        "features_used":  ["or_levels"],
        "forecast_id":    (forecast_id
                            or f"fc_{chat_run_id}_{digest_sha256[:6]}_{ts_ms}"),
    }
    with PaxForecastStore(forecast_db) as store:
        store.record_validated(
            record,
            chat_run_id=chat_run_id,
            digest_sha256=digest_sha256,
            snapshot_sha256=snapshot_sha256,
        )


def _wait_outcomes(bus_db, target=1, timeout_s=2.0):
    deadline = time.time() + timeout_s
    n = 0
    while time.time() < deadline:
        try:
            with sqlite3.connect(bus_db) as conn:
                n = conn.execute(
                    "SELECT COUNT(*) FROM trade_outcomes").fetchone()[0]
        except sqlite3.OperationalError:
            n = 0
        if n >= target:
            return n
        time.sleep(0.02)
    return n


def _read_outcome(bus_db, ai_turn_id):
    with sqlite3.connect(bus_db) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT * FROM trade_outcomes WHERE ai_turn_id = ?",
            (ai_turn_id,)).fetchone()


# ----------------------------------------------------------- daemon safety

def test_outcomes_disabled_no_thread_no_writes(tmp_path, monkeypatch):
    """outcomes.enabled=False -> start() spawns no thread, no rows written."""
    from pax_ai import outcomes
    db = _disable_outcomes(tmp_path, monkeypatch)
    outcomes.start()
    time.sleep(0.1)
    s = outcomes.status()
    assert s["enabled"] is False
    assert s["running"] is False
    assert not db.exists()
    outcomes.stop()


def test_outcomes_start_is_idempotent(tmp_path, monkeypatch):
    from pax_ai import outcomes
    _enable_outcomes(tmp_path, monkeypatch)
    outcomes.start()
    outcomes.start()  # second call must be a no-op
    s = outcomes.status()
    assert s["running"] is True
    outcomes.stop()


def test_outcomes_never_raises_into_start(tmp_path, monkeypatch):
    """A startup failure (e.g. bad DB path) must NEVER raise."""
    from pax_ai import config as cfg_mod, outcomes
    monkeypatch.setattr(cfg_mod, "_reload_if_stale", lambda: None)
    monkeypatch.setattr(cfg_mod, "_CACHE", {**cfg_mod._CACHE,
        "feature_bus": {"enabled": True, "db_path": "Z:/does/not/exist/bus.db",
            "snapshot_blob_dir": "Z:/x", "digest_blob_dir": "Z:/y",
            "queue_max": 2000, "writer_idle_ms": 100, "capture_ms": 1000,
            "retention_days": 30},
        "outcomes": {"enabled": True, "wake_interval_ms": 50,
                      "match_tolerance_ms": 5_000},
        "forecast": {"enabled": True, "store_path": ""},
    })
    outcomes.start()                                   # MUST NOT raise
    time.sleep(0.1)
    outcomes.stop()


def test_outcomes_status_shape(tmp_path, monkeypatch):
    from pax_ai import outcomes
    _enable_outcomes(tmp_path, monkeypatch)
    outcomes.start()
    s = outcomes.status()
    for key in ("enabled", "running", "healthy", "labeled_today",
                "last_run_ms", "last_error"):
        assert key in s
    outcomes.stop()


# ------------------------------------------------------- structured labels

def test_pay_for_trade_long_emits_enter_long_with_directional_r(
        tmp_path, monkeypatch):
    """A captured PAY_FOR_TRADE/LONG forecast -> ENTER_LONG verdict.

    Mids: t0=100, t60=101, t180=102.5, t300=100.5, t900=99.
    realized_r expected (LONG):
        t60  = +1.0
        t180 = +2.5
        t300 = +0.5
        t900 = -1.0
    """
    from pax_ai import outcomes
    bus, fc = _enable_outcomes(tmp_path, monkeypatch)
    ai_ts = int(time.time() * 1000) - 86_400_000
    ai_id = _seed_ai_turn(bus, ai_ts_ms=ai_ts,
                            user_text="I'm not buying that level")
    _seed_forecast(fc, execution_read="PAY_FOR_TRADE", direction="LONG")

    outcomes.start()
    assert _wait_outcomes(bus, target=1) == 1
    outcomes.stop()

    row = _read_outcome(bus, ai_id)
    assert row is not None
    assert row["verdict"] == "ENTER_LONG"
    assert row["invalidated"] == 0
    assert row["invalidation_reason"] is None
    assert row["label_method"] == "structured_resampled_v1"
    assert row["entry_price"] == 100.0
    assert row["mid_at_t0"]    == 100.0
    assert row["mid_at_t60s"]  == 101.0
    assert row["mid_at_t180s"] == 102.5
    assert row["mid_at_t300s"] == 100.5
    assert row["mid_at_t900s"] == 99.0
    assert row["realized_r_at_t60s"]  == pytest.approx(1.0)
    assert row["realized_r_at_t180s"] == pytest.approx(2.5)
    assert row["realized_r_at_t300s"] == pytest.approx(0.5)
    assert row["realized_r_at_t900s"] == pytest.approx(-1.0)


def test_pay_for_trade_short_emits_enter_short_with_inverted_r(
        tmp_path, monkeypatch):
    """PAY_FOR_TRADE/SHORT inverts the sign of realized_r."""
    from pax_ai import outcomes
    bus, fc = _enable_outcomes(tmp_path, monkeypatch)
    ai_ts = int(time.time() * 1000) - 86_400_000
    ai_id = _seed_ai_turn(bus, ai_ts_ms=ai_ts,
                           user_text="sell here")
    _seed_forecast(fc, execution_read="PAY_FOR_TRADE", direction="SHORT")

    outcomes.start()
    assert _wait_outcomes(bus, target=1) == 1
    outcomes.stop()

    row = _read_outcome(bus, ai_id)
    assert row["verdict"] == "ENTER_SHORT"
    assert row["invalidated"] == 0
    assert row["invalidation_reason"] is None
    # Short PnL is mid_t0 - mid_tN.
    assert row["realized_r_at_t60s"]  == pytest.approx(-1.0)
    assert row["realized_r_at_t180s"] == pytest.approx(-2.5)
    assert row["realized_r_at_t300s"] == pytest.approx(-0.5)
    assert row["realized_r_at_t900s"] == pytest.approx(1.0)


def test_raw_text_buy_long_not_classified_without_forecast(
        tmp_path, monkeypatch):
    """No forecast captured -> invalidated, NEVER ENTER_LONG even when the
    raw text says 'enter long'.

    Regression guard for the deleted substring matcher.
    """
    from pax_ai import outcomes
    bus, _fc = _enable_outcomes(tmp_path, monkeypatch)
    ai_ts = int(time.time() * 1000) - 86_400_000
    ai_id = _seed_ai_turn(bus, ai_ts_ms=ai_ts,
                          user_text="enter long ping")
    # NOTE: no _seed_forecast(...) call. Outcomes must NOT invent a verdict.

    outcomes.start()
    assert _wait_outcomes(bus, target=1) == 1
    outcomes.stop()

    row = _read_outcome(bus, ai_id)
    assert row["verdict"] != "ENTER_LONG"
    assert row["invalidated"] == 1
    assert row["invalidation_reason"] == "FORECAST_MISSING_OR_AMBIGUOUS"
    assert row["realized_r_at_t60s"]  is None
    assert row["realized_r_at_t180s"] is None
    assert row["realized_r_at_t300s"] is None
    assert row["realized_r_at_t900s"] is None


def test_raw_text_not_buying_does_not_become_enter_long(
        tmp_path, monkeypatch):
    """Explicit regression of the audit example: 'I'm not buying that level'."""
    from pax_ai import outcomes
    bus, _fc = _enable_outcomes(tmp_path, monkeypatch)
    ai_ts = int(time.time() * 1000) - 86_400_000
    ai_id = _seed_ai_turn(bus, ai_ts_ms=ai_ts,
                          user_text="I'm not buying that level")

    outcomes.start()
    assert _wait_outcomes(bus, target=1) == 1
    outcomes.stop()

    row = _read_outcome(bus, ai_id)
    assert row["verdict"] != "ENTER_LONG"
    assert row["invalidated"] == 1
    assert row["invalidation_reason"] == "FORECAST_MISSING_OR_AMBIGUOUS"


def test_missing_horizon_mid_invalidates_with_horizon_data_missing(
        tmp_path, monkeypatch):
    """Directional forecast + t0 mid present + one forward horizon missing
    -> invalidated=1, HORIZON_DATA_MISSING, realized_r columns NULL."""
    from pax_ai import outcomes
    bus, fc = _enable_outcomes(tmp_path, monkeypatch)
    ai_ts = int(time.time() * 1000) - 86_400_000
    # Drop the t900 mid; keep t0/t60/t180/t300.
    ai_id = _seed_ai_turn(bus, ai_ts_ms=ai_ts,
                          snapshots=((0, 100.0), (60, 101.0),
                                     (180, 102.5), (300, 100.5)))
    _seed_forecast(fc, execution_read="PAY_FOR_TRADE", direction="LONG")

    outcomes.start()
    assert _wait_outcomes(bus, target=1) == 1
    outcomes.stop()

    row = _read_outcome(bus, ai_id)
    assert row["verdict"] == "ENTER_LONG"
    assert row["invalidated"] == 1
    assert row["invalidation_reason"] == "HORIZON_DATA_MISSING"
    assert row["mid_at_t0"]    == 100.0
    assert row["mid_at_t900s"] is None
    assert row["realized_r_at_t60s"]  is None
    assert row["realized_r_at_t180s"] is None
    assert row["realized_r_at_t300s"] is None
    assert row["realized_r_at_t900s"] is None


def test_missing_t0_mid_invalidates_with_snapshot_missing_at_t0(
        tmp_path, monkeypatch):
    """Directional forecast + NO snapshot at t0 -> SNAPSHOT_MISSING_AT_T0."""
    from pax_ai import outcomes
    bus, fc = _enable_outcomes(tmp_path, monkeypatch)
    ai_ts = int(time.time() * 1000) - 86_400_000
    ai_id = _seed_ai_turn(bus, ai_ts_ms=ai_ts, snapshots=())  # no snapshots
    _seed_forecast(fc, execution_read="PAY_FOR_TRADE", direction="LONG")

    outcomes.start()
    assert _wait_outcomes(bus, target=1) == 1
    outcomes.stop()

    row = _read_outcome(bus, ai_id)
    assert row["verdict"] == "ENTER_LONG"
    assert row["invalidated"] == 1
    assert row["invalidation_reason"] == "SNAPSHOT_MISSING_AT_T0"
    assert row["mid_at_t0"] is None
    assert row["realized_r_at_t60s"] is None


def test_missing_forecast_invalidates_forecast_missing_or_ambiguous(
        tmp_path, monkeypatch):
    """No matching forecast row at all -> FORECAST_MISSING_OR_AMBIGUOUS."""
    from pax_ai import outcomes
    bus, _fc = _enable_outcomes(tmp_path, monkeypatch)
    ai_ts = int(time.time() * 1000) - 86_400_000
    ai_id = _seed_ai_turn(bus, ai_ts_ms=ai_ts)

    outcomes.start()
    assert _wait_outcomes(bus, target=1) == 1
    outcomes.stop()

    row = _read_outcome(bus, ai_id)
    assert row["invalidated"] == 1
    assert row["invalidation_reason"] == "FORECAST_MISSING_OR_AMBIGUOUS"


def test_ambiguous_forecast_invalidates_forecast_missing_or_ambiguous(
        tmp_path, monkeypatch):
    """Two forecasts share the same chat_run_id + digest_sha256 AND the
    snapshot_sha256 disambiguator doesn't pin a unique row -> AMBIGUOUS."""
    from pax_ai import outcomes
    bus, fc = _enable_outcomes(tmp_path, monkeypatch)
    ai_ts = int(time.time() * 1000) - 86_400_000
    ai_id = _seed_ai_turn(bus, ai_ts_ms=ai_ts,
                          snapshot_sha256="aaa" + "0" * 61)
    # Two distinct forecasts under the same chat_run_id + digest_sha256,
    # neither matching the ai_turn's snapshot_sha256 -> cannot pick one.
    _seed_forecast(fc, snapshot_sha256="bbb" + "0" * 61,
                    forecast_id="fc_dup_1", direction="LONG")
    _seed_forecast(fc, snapshot_sha256="ccc" + "0" * 61,
                    forecast_id="fc_dup_2", direction="SHORT")

    outcomes.start()
    assert _wait_outcomes(bus, target=1) == 1
    outcomes.stop()

    row = _read_outcome(bus, ai_id)
    assert row["invalidated"] == 1
    assert row["invalidation_reason"] == "FORECAST_MISSING_OR_AMBIGUOUS"


def test_non_pay_forecast_is_not_a_directional_trade(
        tmp_path, monkeypatch):
    """STAND_DOWN/NONE -> verdict='STAND_DOWN', realized_r NULL,
    invalidated=0, label_method='structured_resampled_v1'."""
    from pax_ai import outcomes
    bus, fc = _enable_outcomes(tmp_path, monkeypatch)
    ai_ts = int(time.time() * 1000) - 86_400_000
    ai_id = _seed_ai_turn(bus, ai_ts_ms=ai_ts,
                          user_text="should I take this trade")
    _seed_forecast(fc, execution_read="STAND_DOWN", direction="NONE")

    outcomes.start()
    assert _wait_outcomes(bus, target=1) == 1
    outcomes.stop()

    row = _read_outcome(bus, ai_id)
    assert row["verdict"] == "STAND_DOWN"
    assert row["invalidated"] == 0
    assert row["invalidation_reason"] is None
    assert row["label_method"] == "structured_resampled_v1"
    assert row["entry_price"] is None
    assert row["realized_r_at_t60s"]  is None
    assert row["realized_r_at_t180s"] is None
    assert row["realized_r_at_t300s"] is None
    assert row["realized_r_at_t900s"] is None


def test_outcomes_does_not_relabel_already_labeled_rows(
        tmp_path, monkeypatch):
    """A second daemon pass must NOT re-insert the same trade_outcomes row."""
    from pax_ai import outcomes
    bus, fc = _enable_outcomes(tmp_path, monkeypatch)
    ai_ts = int(time.time() * 1000) - 86_400_000
    _seed_ai_turn(bus, ai_ts_ms=ai_ts)
    _seed_forecast(fc, execution_read="PAY_FOR_TRADE", direction="LONG")

    outcomes.start()
    assert _wait_outcomes(bus, target=1) == 1
    # Allow several wake_interval cycles to ensure no duplicate row appears.
    time.sleep(0.3)
    with sqlite3.connect(bus) as conn:
        n2 = conn.execute("SELECT COUNT(*) FROM trade_outcomes").fetchone()[0]
    outcomes.stop()
    assert n2 == 1


# ---------------------------------------------------------- time alignment

def _insert_snapshot(bus_db, *, ts_ms, mid, alias="NQM6"):
    """Insert a snapshot_features row at the exact ``ts_ms``.

    Used by the Phase 6 time-aligned-lookup tests to seed bracketing
    snapshots at custom millisecond offsets that the legacy
    (offset_s * 1000) seed helper can't express.
    """
    from pax_ai import feature_bus
    with feature_bus._open_db(bus_db) as conn:
        conn.execute("""
            INSERT INTO snapshot_features
              (schema_version, ts_ms, alias, health, mid)
            VALUES (1, ?, ?, 'ok', ?)
        """, (int(ts_ms), alias, float(mid)))


def test_exact_timestamp_match_uses_exact_mid(tmp_path, monkeypatch):
    """A snapshot at exactly the target ts must short-circuit interpolation
    and return that mid verbatim. We prove 'no interpolation' by also
    seeding bracketing snapshots whose interpolated value would be very
    different from the exact-hit mid -- the exact-match path must win."""
    from pax_ai import outcomes
    bus, fc = _enable_outcomes(tmp_path, monkeypatch)
    ai_ts = int(time.time() * 1000) - 86_400_000
    ai_id = _seed_ai_turn(bus, ai_ts_ms=ai_ts, snapshots=())
    # Exact-hit snapshots at every required offset, plus distractor
    # snapshots that would skew an interpolation if it ran.
    for off_s, mid in ((0, 100.0), (60, 110.0), (180, 120.0),
                        (300, 130.0), (900, 140.0)):
        _insert_snapshot(bus, ts_ms=ai_ts + off_s * 1000, mid=mid)
        # Distractor: 100ms before and 100ms after each target, mid=999.
        # If interpolation ran, mid_at_tN would be ~999, not the exact value.
        _insert_snapshot(bus, ts_ms=ai_ts + off_s * 1000 - 100, mid=999.0)
        _insert_snapshot(bus, ts_ms=ai_ts + off_s * 1000 + 100, mid=999.0)
    _seed_forecast(fc, execution_read="PAY_FOR_TRADE", direction="LONG")

    outcomes.start()
    assert _wait_outcomes(bus, target=1) == 1
    outcomes.stop()

    row = _read_outcome(bus, ai_id)
    assert row["mid_at_t0"]    == 100.0
    assert row["mid_at_t60s"]  == 110.0
    assert row["mid_at_t180s"] == 120.0
    assert row["mid_at_t300s"] == 130.0
    assert row["mid_at_t900s"] == 140.0
    assert row["invalidated"] == 0


def test_duplicate_exact_timestamp_uses_latest_snapshot_deterministically(
        tmp_path, monkeypatch):
    """Duplicate snapshot timestamps are legal in the append-only bus table.
    The aligned lookup must use a stable tie-breaker rather than depending on
    SQLite's planner order for equal ts_ms rows."""
    from pax_ai import outcomes
    bus, fc = _enable_outcomes(tmp_path, monkeypatch)
    ai_ts = int(time.time() * 1000) - 86_400_000
    ai_id = _seed_ai_turn(bus, ai_ts_ms=ai_ts, snapshots=())
    # Two exact t0 rows: the newer append should win via ORDER BY id DESC.
    _insert_snapshot(bus, ts_ms=ai_ts, mid=99.0)
    _insert_snapshot(bus, ts_ms=ai_ts, mid=100.0)
    for off_s, mid in ((60, 101.0), (180, 102.0),
                        (300, 103.0), (900, 104.0)):
        _insert_snapshot(bus, ts_ms=ai_ts + off_s * 1000, mid=mid)
    _seed_forecast(fc, execution_read="PAY_FOR_TRADE", direction="LONG")

    outcomes.start()
    assert _wait_outcomes(bus, target=1) == 1
    outcomes.stop()

    row = _read_outcome(bus, ai_id)
    assert row["invalidated"] == 0
    assert row["mid_at_t0"] == 100.0
    assert row["entry_price"] == 100.0


def test_interpolation_halfway_between_two_snapshots(
        tmp_path, monkeypatch):
    """Two snapshots straddle each target with gap <= max_gap_ms.
    Target sits exactly halfway -> mid = (m_before + m_after) / 2."""
    from pax_ai import outcomes
    bus, fc = _enable_outcomes(tmp_path, monkeypatch)
    ai_ts = int(time.time() * 1000) - 86_400_000
    ai_id = _seed_ai_turn(bus, ai_ts_ms=ai_ts, snapshots=())
    # For every target offset, place a snapshot 2s before and 2s after
    # (gap = 4s, under the 5s default). Target is exactly halfway.
    layout = [
        (0,   98.0, 102.0),     # halfway -> 100.0
        (60,  99.0, 103.0),     # halfway -> 101.0
        (180, 100.0, 105.0),    # halfway -> 102.5
        (300, 99.0, 102.0),     # halfway -> 100.5
        (900, 96.0, 102.0),     # halfway -> 99.0
    ]
    for off_s, before, after in layout:
        _insert_snapshot(bus, ts_ms=ai_ts + off_s * 1000 - 2_000, mid=before)
        _insert_snapshot(bus, ts_ms=ai_ts + off_s * 1000 + 2_000, mid=after)
    _seed_forecast(fc, execution_read="PAY_FOR_TRADE", direction="LONG")

    outcomes.start()
    assert _wait_outcomes(bus, target=1) == 1
    outcomes.stop()

    row = _read_outcome(bus, ai_id)
    assert row["mid_at_t0"]    == pytest.approx(100.0)
    assert row["mid_at_t60s"]  == pytest.approx(101.0)
    assert row["mid_at_t180s"] == pytest.approx(102.5)
    assert row["mid_at_t300s"] == pytest.approx(100.5)
    assert row["mid_at_t900s"] == pytest.approx(99.0)
    assert row["invalidated"] == 0


def test_interpolation_yields_correct_realized_r_for_long(
        tmp_path, monkeypatch):
    """LONG realized_r_at_tN = mid_tN - mid_t0 when both sides
    are interpolated."""
    from pax_ai import outcomes
    bus, fc = _enable_outcomes(tmp_path, monkeypatch)
    ai_ts = int(time.time() * 1000) - 86_400_000
    ai_id = _seed_ai_turn(bus, ai_ts_ms=ai_ts, snapshots=())
    # t0 halfway between 99 and 101 -> 100.
    # t60 halfway between 102 and 104 -> 103. realized_r_at_t60s = +3.
    # t180 halfway between 95 and 99 -> 97.  realized_r_at_t180s = -3.
    _insert_snapshot(bus, ts_ms=ai_ts - 1_000,            mid=99.0)
    _insert_snapshot(bus, ts_ms=ai_ts + 1_000,            mid=101.0)
    _insert_snapshot(bus, ts_ms=ai_ts + 60_000 - 1_000,  mid=102.0)
    _insert_snapshot(bus, ts_ms=ai_ts + 60_000 + 1_000,  mid=104.0)
    _insert_snapshot(bus, ts_ms=ai_ts + 180_000 - 1_000, mid=95.0)
    _insert_snapshot(bus, ts_ms=ai_ts + 180_000 + 1_000, mid=99.0)
    # Also seed exact hits for t300 and t900 so those don't invalidate.
    _insert_snapshot(bus, ts_ms=ai_ts + 300_000, mid=100.0)
    _insert_snapshot(bus, ts_ms=ai_ts + 900_000, mid=100.0)
    _seed_forecast(fc, execution_read="PAY_FOR_TRADE", direction="LONG")

    outcomes.start()
    assert _wait_outcomes(bus, target=1) == 1
    outcomes.stop()

    row = _read_outcome(bus, ai_id)
    assert row["verdict"] == "ENTER_LONG"
    assert row["invalidated"] == 0
    assert row["entry_price"] == pytest.approx(100.0)
    assert row["realized_r_at_t60s"]  == pytest.approx(+3.0)
    assert row["realized_r_at_t180s"] == pytest.approx(-3.0)


def test_interpolation_yields_correct_realized_r_for_short(
        tmp_path, monkeypatch):
    """SHORT realized_r flips the sign: mid_t0 - mid_tN."""
    from pax_ai import outcomes
    bus, fc = _enable_outcomes(tmp_path, monkeypatch)
    ai_ts = int(time.time() * 1000) - 86_400_000
    ai_id = _seed_ai_turn(bus, ai_ts_ms=ai_ts, snapshots=())
    _insert_snapshot(bus, ts_ms=ai_ts - 1_000,            mid=99.0)
    _insert_snapshot(bus, ts_ms=ai_ts + 1_000,            mid=101.0)
    _insert_snapshot(bus, ts_ms=ai_ts + 60_000 - 1_000,  mid=102.0)
    _insert_snapshot(bus, ts_ms=ai_ts + 60_000 + 1_000,  mid=104.0)
    _insert_snapshot(bus, ts_ms=ai_ts + 180_000 - 1_000, mid=95.0)
    _insert_snapshot(bus, ts_ms=ai_ts + 180_000 + 1_000, mid=99.0)
    _insert_snapshot(bus, ts_ms=ai_ts + 300_000, mid=100.0)
    _insert_snapshot(bus, ts_ms=ai_ts + 900_000, mid=100.0)
    _seed_forecast(fc, execution_read="PAY_FOR_TRADE", direction="SHORT")

    outcomes.start()
    assert _wait_outcomes(bus, target=1) == 1
    outcomes.stop()

    row = _read_outcome(bus, ai_id)
    assert row["verdict"] == "ENTER_SHORT"
    assert row["invalidated"] == 0
    assert row["realized_r_at_t60s"]  == pytest.approx(-3.0)
    assert row["realized_r_at_t180s"] == pytest.approx(+3.0)


def test_one_sided_snapshot_at_t0_invalidates_snapshot_missing_at_t0(
        tmp_path, monkeypatch):
    """Only an 'after' snapshot exists for t0. The new helper MUST refuse
    to score on one-sided data and invalidate as SNAPSHOT_MISSING_AT_T0."""
    from pax_ai import outcomes
    bus, fc = _enable_outcomes(tmp_path, monkeypatch)
    ai_ts = int(time.time() * 1000) - 86_400_000
    ai_id = _seed_ai_turn(bus, ai_ts_ms=ai_ts, snapshots=())
    # Only an after snapshot at t0, but exact hits at every forward horizon.
    _insert_snapshot(bus, ts_ms=ai_ts + 100,             mid=100.5)
    for off_s, mid in ((60, 101.0), (180, 102.5),
                        (300, 100.5), (900, 99.0)):
        _insert_snapshot(bus, ts_ms=ai_ts + off_s * 1000, mid=mid)
    _seed_forecast(fc, execution_read="PAY_FOR_TRADE", direction="LONG")

    outcomes.start()
    assert _wait_outcomes(bus, target=1) == 1
    outcomes.stop()

    row = _read_outcome(bus, ai_id)
    assert row["invalidated"] == 1
    assert row["invalidation_reason"] == "SNAPSHOT_MISSING_AT_T0"
    assert row["mid_at_t0"] is None
    assert row["realized_r_at_t60s"] is None


def test_one_sided_snapshot_at_horizon_invalidates_horizon_data_missing(
        tmp_path, monkeypatch):
    """t0 has an exact hit; t60 has only a 'before' snapshot (no after).
    The forward horizon must invalidate as HORIZON_DATA_MISSING."""
    from pax_ai import outcomes
    bus, fc = _enable_outcomes(tmp_path, monkeypatch)
    ai_ts = int(time.time() * 1000) - 86_400_000
    ai_id = _seed_ai_turn(bus, ai_ts_ms=ai_ts, snapshots=())
    # t0 exact.
    _insert_snapshot(bus, ts_ms=ai_ts, mid=100.0)
    # t60: only a 'before' snapshot, no 'after'.
    _insert_snapshot(bus, ts_ms=ai_ts + 60_000 - 500, mid=99.5)
    # Exact hits at remaining horizons.
    for off_s, mid in ((180, 102.5), (300, 100.5), (900, 99.0)):
        _insert_snapshot(bus, ts_ms=ai_ts + off_s * 1000, mid=mid)
    _seed_forecast(fc, execution_read="PAY_FOR_TRADE", direction="LONG")

    outcomes.start()
    assert _wait_outcomes(bus, target=1) == 1
    outcomes.stop()

    row = _read_outcome(bus, ai_id)
    assert row["invalidated"] == 1
    assert row["invalidation_reason"] == "HORIZON_DATA_MISSING"
    assert row["mid_at_t0"]    == 100.0      # t0 resolved
    assert row["realized_r_at_t60s"] is None


def test_gap_exceeds_max_gap_at_t0_invalidates(tmp_path, monkeypatch):
    """Both sides bracket t0 but the inter-snapshot gap exceeds
    max_gap_ms (5_000 default). Refuses to interpolate."""
    from pax_ai import outcomes
    bus, fc = _enable_outcomes(tmp_path, monkeypatch)
    ai_ts = int(time.time() * 1000) - 86_400_000
    ai_id = _seed_ai_turn(bus, ai_ts_ms=ai_ts, snapshots=())
    # before 4s ahead of t0, after 4s past t0 -> gap = 8s > 5s default.
    _insert_snapshot(bus, ts_ms=ai_ts - 4_000, mid=98.0)
    _insert_snapshot(bus, ts_ms=ai_ts + 4_000, mid=102.0)
    # Exact hits for forward horizons so we isolate the t0 failure.
    for off_s, mid in ((60, 101.0), (180, 102.5),
                        (300, 100.5), (900, 99.0)):
        _insert_snapshot(bus, ts_ms=ai_ts + off_s * 1000, mid=mid)
    _seed_forecast(fc, execution_read="PAY_FOR_TRADE", direction="LONG")

    outcomes.start()
    assert _wait_outcomes(bus, target=1) == 1
    outcomes.stop()

    row = _read_outcome(bus, ai_id)
    assert row["invalidated"] == 1
    assert row["invalidation_reason"] == "SNAPSHOT_MISSING_AT_T0"
    assert row["mid_at_t0"] is None


def test_gap_exceeds_max_gap_at_horizon_invalidates(tmp_path, monkeypatch):
    """Same gap test but at a forward horizon -> HORIZON_DATA_MISSING."""
    from pax_ai import outcomes
    bus, fc = _enable_outcomes(tmp_path, monkeypatch)
    ai_ts = int(time.time() * 1000) - 86_400_000
    ai_id = _seed_ai_turn(bus, ai_ts_ms=ai_ts, snapshots=())
    _insert_snapshot(bus, ts_ms=ai_ts, mid=100.0)                  # t0 exact
    # t60: bracket but gap = 8s.
    _insert_snapshot(bus, ts_ms=ai_ts + 60_000 - 4_000, mid=99.0)
    _insert_snapshot(bus, ts_ms=ai_ts + 60_000 + 4_000, mid=101.0)
    for off_s, mid in ((180, 102.5), (300, 100.5), (900, 99.0)):
        _insert_snapshot(bus, ts_ms=ai_ts + off_s * 1000, mid=mid)
    _seed_forecast(fc, execution_read="PAY_FOR_TRADE", direction="LONG")

    outcomes.start()
    assert _wait_outcomes(bus, target=1) == 1
    outcomes.stop()

    row = _read_outcome(bus, ai_id)
    assert row["invalidated"] == 1
    assert row["invalidation_reason"] == "HORIZON_DATA_MISSING"
    assert row["realized_r_at_t60s"] is None
