"""Phase 0 lifecycle tests for SimEngine.

Pins the critical correctness contract that bracket children (SL/TP) must
NOT trigger or fill before the parent entry has filled. Without this, a
trade-through that crosses a child's price before the entry can fire the
SL or TP and silently corrupt every recorded outcome.

Also covers: armed children fill normally after parent fill, cascade cancel
on parent cancel (cancel + TIF expiry paths), restart-safe stale logging,
and EOD auto-flatten at 15:00 CT.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bookmap_mcp.sim_engine as se   # noqa: E402


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def db_path(tmp_path):
    """Per-test SQLite path so the live D:\\BookmapLogs\\pax-trades.db is
    not touched."""
    return tmp_path / "test-trades.db"


def _trade(price, nanos, side="sell", size=1):
    return {"price": price, "nanos": nanos, "size": size, "side": side}


def _snap(alias, price, nanos, side="sell", best_bid=None, best_ask=None):
    if best_bid is None: best_bid = price - 0.25
    if best_ask is None: best_ask = price + 0.25
    return {"alias": alias,
            "trades": [_trade(price, nanos, side=side)],
            "book": {"bestBid": best_bid, "bestAsk": best_ask,
                       "mid": (best_bid + best_ask) / 2.0}}


# ──────────────────────────────────────────────────────────────────────
# Critical: child does not fill before parent
# ──────────────────────────────────────────────────────────────────────

def test_child_does_not_fill_before_parent(db_path):
    """The classic bracket-race bug: BUY STOP entry at 100.50 plus SL at
    99.00. Before the breakout fires, price dips through 99.00. The SL
    must remain WORKING because the parent never filled."""
    eng = se.SimEngine(alias="TEST", db_path=db_path)
    ids = eng.place_bracket(
        side="BUY", qty=1,
        entry_stop=100.50, entry_limit=100.50,
        stop_loss=99.00, take_profits=[102.00])

    # Price prints at 99.00 (would trigger SL if armed).
    eng.tick(_snap("TEST", 99.00, nanos=1_000_000_000_000))

    with eng._conn() as c:
        entry = c.execute("SELECT status FROM orders WHERE id=?",
                          (ids["entry"],)).fetchone()
        sl = c.execute("SELECT status FROM orders WHERE id=?",
                       (ids["stop"],)).fetchone()
        tp = c.execute("SELECT status FROM orders WHERE id=?",
                       (ids["tps"][0],)).fetchone()

    assert entry["status"] == se.ST_WORKING, (
        f"entry not yet triggered, expected WORKING got {entry['status']}")
    assert sl["status"] == se.ST_WORKING, (
        f"SL fired before parent filled — VULNERABLE. Got {sl['status']}")
    assert tp["status"] == se.ST_WORKING, (
        f"TP fired before parent filled. Got {tp['status']}")

    # Position must still be flat.
    pos = eng.snapshot()["position"]
    assert pos["size"] == 0


def test_armed_children_fill_normally_after_parent(db_path):
    """Parent fills first, then on a subsequent tick the SL behaves like
    a normal stop-limit and triggers/fills."""
    eng = se.SimEngine(alias="TEST", db_path=db_path)
    ids = eng.place_bracket(
        side="BUY", qty=1,
        entry_stop=100.50, entry_limit=100.50,
        stop_loss=99.00, take_profits=[102.00])

    # Tick 1: breakout — entry STOP at 100.50 triggers and fills.
    # BUY limit fills against a SELL aggressor (someone hitting our bid).
    eng.tick(_snap("TEST", 100.50, nanos=1_000_000_000_000, side="sell"))

    with eng._conn() as c:
        entry = c.execute("SELECT status FROM orders WHERE id=?",
                          (ids["entry"],)).fetchone()
    assert entry["status"] == se.ST_FILLED, (
        f"entry should fill on breakout; got {entry['status']}")
    pos_after_entry = eng.snapshot()["position"]
    assert pos_after_entry["size"] == 1

    # Tick 2: price drops to 99.00 — SL now armed, should trigger & fill.
    # SELL limit fills against a BUY aggressor.
    eng.tick(_snap("TEST", 99.00, nanos=2_000_000_000_000, side="buy"))

    with eng._conn() as c:
        sl = c.execute("SELECT status FROM orders WHERE id=?",
                       (ids["stop"],)).fetchone()
    assert sl["status"] == se.ST_FILLED, (
        f"armed SL must fill after parent; got {sl['status']}")
    pos_after_sl = eng.snapshot()["position"]
    assert pos_after_sl["size"] == 0


def test_arm_child_event_logged_on_parent_fill(db_path):
    """Audit trail: every armed child gets an ARM_CHILD event when the
    parent fills, so we can prove the gate fired."""
    eng = se.SimEngine(alias="TEST", db_path=db_path)
    ids = eng.place_bracket(
        side="BUY", qty=2,
        entry_stop=100.50, entry_limit=100.50,
        stop_loss=99.00, take_profits=[102.00, 103.00])

    # BUY limit fills against a SELL aggressor.
    eng.tick(_snap("TEST", 100.50, nanos=1_000_000_000_000, side="sell"))

    with eng._conn() as c:
        arm_events = c.execute(
            "SELECT order_id FROM events WHERE alias=? AND kind=? ORDER BY order_id",
            ("TEST", "ARM_CHILD")).fetchall()
    armed_ids = {r["order_id"] for r in arm_events}
    expected = {ids["stop"]} | set(ids["tps"])
    assert armed_ids == expected, (
        f"expected ARM_CHILD for {expected}, got {armed_ids}")


# ──────────────────────────────────────────────────────────────────────
# Cascade cancel
# ──────────────────────────────────────────────────────────────────────

def test_bracket_cleanup_on_parent_cancel(db_path):
    """Canceling the entry must cascade-cancel SL and TP children."""
    eng = se.SimEngine(alias="TEST", db_path=db_path)
    ids = eng.place_bracket(
        side="BUY", qty=1,
        entry_stop=100.50, entry_limit=100.50,
        stop_loss=99.00, take_profits=[102.00, 103.00])

    assert eng.cancel(ids["entry"], reason="test cancel")

    with eng._conn() as c:
        rows = c.execute(
            "SELECT id, status FROM orders WHERE alias=? ORDER BY id",
            ("TEST",)).fetchall()
    by_id = {r["id"]: r["status"] for r in rows}

    assert by_id[ids["entry"]] == se.ST_CANCELED
    assert by_id[ids["stop"]] == se.ST_CANCELED, "SL must be cascade-canceled"
    for tp_id in ids["tps"]:
        assert by_id[tp_id] == se.ST_CANCELED, (
            f"TP {tp_id} must be cascade-canceled")


def test_tif_expiry_cascade_cancels_children(db_path):
    """When an ENTRY's TIF expires, the bracket children must also cancel."""
    eng = se.SimEngine(alias="TEST", db_path=db_path)
    ids = eng.place_bracket(
        side="BUY", qty=1,
        entry_stop=100.50, entry_limit=100.50,
        stop_loss=99.00, take_profits=[102.00])

    # Override TIF on the entry so it expires on the next tick.
    with eng._conn() as c:
        c.execute("UPDATE orders SET tif_sec=0.001 WHERE id=?", (ids["entry"],))

    # Tick with a future wall-clock to exceed the 1ms TIF.
    fake_now = se._now_ms() + 60_000
    eng.tick(_snap("TEST", 99.50, nanos=1_000_000_000_000), now_ms=fake_now)

    with eng._conn() as c:
        entry = c.execute("SELECT status FROM orders WHERE id=?",
                          (ids["entry"],)).fetchone()
        sl = c.execute("SELECT status FROM orders WHERE id=?",
                       (ids["stop"],)).fetchone()
    assert entry["status"] == se.ST_CANCELED, (
        f"entry should TIF-expire; got {entry['status']}")
    assert sl["status"] == se.ST_CANCELED, (
        f"SL must cascade-cancel on parent TIF expiry; got {sl['status']}")


# ──────────────────────────────────────────────────────────────────────
# Restart safety
# ──────────────────────────────────────────────────────────────────────

def test_armed_flag_persists_across_engine_restart(db_path):
    """Schema column survives DB close + re-open."""
    eng1 = se.SimEngine(alias="TEST", db_path=db_path)
    ids = eng1.place_bracket(
        side="BUY", qty=1,
        entry_stop=100.50, entry_limit=100.50,
        stop_loss=99.00, take_profits=[102.00])
    del eng1

    eng2 = se.SimEngine(alias="TEST", db_path=db_path)
    with eng2._conn() as c:
        entry = c.execute(
            "SELECT armed_after_parent_fill FROM orders WHERE id=?",
            (ids["entry"],)).fetchone()
        sl = c.execute(
            "SELECT armed_after_parent_fill FROM orders WHERE id=?",
            (ids["stop"],)).fetchone()
        tp = c.execute(
            "SELECT armed_after_parent_fill FROM orders WHERE id=?",
            (ids["tps"][0],)).fetchone()
    assert entry["armed_after_parent_fill"] == 0
    assert sl["armed_after_parent_fill"] == 1
    assert tp["armed_after_parent_fill"] == 1

    # Drive same race after restart — SL must still NOT fire.
    eng2.tick(_snap("TEST", 99.00, nanos=1_000_000_000_000))
    with eng2._conn() as c:
        sl_status = c.execute("SELECT status FROM orders WHERE id=?",
                               (ids["stop"],)).fetchone()["status"]
    assert sl_status == se.ST_WORKING, (
        "SL must still be gated by armed flag after restart")


def test_stale_reset_event_logged_for_old_working_orders(db_path):
    """If a working order pre-dates today's RTH anchor, restart logs a
    STALE_RESET event for the audit trail."""
    eng1 = se.SimEngine(alias="TEST", db_path=db_path)
    # Inject an order with placed_ms a week in the past.
    week_ago_ms = se._now_ms() - 7 * 86_400_000
    with eng1._conn() as c:
        cur = c.execute(
            "INSERT INTO orders(alias, parent_id, side, type, qty, "
            "limit_price, stop_price, status, placed_ms, tif_sec, "
            "reason, role, decision_tag, armed_after_parent_fill) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("TEST", None, "BUY", se.OT_LIMIT, 1, 100.0, None,
             se.ST_WORKING, week_ago_ms, 24 * 3600,
             "stale test", "ENTRY", None, 0))
        old_id = cur.lastrowid
    del eng1

    # Restart — should log STALE_RESET.
    eng2 = se.SimEngine(alias="TEST", db_path=db_path)
    with eng2._conn() as c:
        events = c.execute(
            "SELECT order_id FROM events WHERE alias=? AND kind=?",
            ("TEST", "STALE_RESET")).fetchall()
    assert any(e["order_id"] == old_id for e in events), (
        "expected STALE_RESET event for the old working order")


# ──────────────────────────────────────────────────────────────────────
# EOD auto-flatten
# ──────────────────────────────────────────────────────────────────────

def test_eod_auto_flatten_fires_after_rth_close(db_path):
    """A tick whose wall-clock is past 15:00 CT triggers auto-flatten of
    open position and cancellation of working orders. Logs EOD_AUTO_FLATTEN."""
    eng = se.SimEngine(alias="TEST", db_path=db_path)
    # Open a position by filling a normal limit BUY.
    eng.place_limit("BUY", qty=1, limit=100.50, role="ENTRY", reason="setup")
    eng.tick(_snap("TEST", 100.50, nanos=1_000_000_000_000, side="sell"))
    pos = eng.snapshot()["position"]
    assert pos["size"] == 1

    # Place a working order that should also get canceled by EOD flatten.
    extra_id = eng.place_limit("SELL", qty=1, limit=110.00,
                                 role="ENTRY", reason="late TP",
                                 tif_sec=24 * 3600)

    # Force wall-clock to 16:00 CT today (past RTH close 15:00).
    import datetime as dt
    from zoneinfo import ZoneInfo
    ct = ZoneInfo("America/Chicago")
    now_ct = dt.datetime.now(ct).replace(hour=16, minute=0,
                                            second=0, microsecond=0)
    fake_now_ms = int(now_ct.timestamp() * 1000)

    result = eng.tick(_snap("TEST", 100.75, nanos=2_000_000_000_000,
                              side="buy"),
                       now_ms=fake_now_ms)

    # An EOD_AUTO_FLATTEN action should appear in this tick's result.
    eod_actions = [a for a in result.get("actions", [])
                    if a.get("kind") == "EOD_AUTO_FLATTEN"]
    assert eod_actions, f"expected EOD_AUTO_FLATTEN action, got {result.get('actions')}"

    # Event recorded.
    with eng._conn() as c:
        ev = c.execute(
            "SELECT COUNT(*) AS n FROM events WHERE alias=? AND kind=?",
            ("TEST", "EOD_AUTO_FLATTEN")).fetchone()
    assert ev["n"] == 1

    # Working order canceled.
    with eng._conn() as c:
        extra_status = c.execute("SELECT status FROM orders WHERE id=?",
                                   (extra_id,)).fetchone()["status"]
    assert extra_status == se.ST_CANCELED


def test_eod_auto_flatten_is_idempotent_within_session(db_path):
    """Once fired for today's session, subsequent ticks past close don't
    re-fire."""
    eng = se.SimEngine(alias="TEST", db_path=db_path)
    import datetime as dt
    from zoneinfo import ZoneInfo
    ct = ZoneInfo("America/Chicago")
    fake_now_ms = int(dt.datetime.now(ct).replace(
        hour=16, minute=0, second=0, microsecond=0).timestamp() * 1000)
    eng.tick(_snap("TEST", 100.50, nanos=1_000_000_000_000), now_ms=fake_now_ms)
    eng.tick(_snap("TEST", 100.75, nanos=2_000_000_000_000), now_ms=fake_now_ms)

    with eng._conn() as c:
        ev = c.execute(
            "SELECT COUNT(*) AS n FROM events WHERE alias=? AND kind=?",
            ("TEST", "EOD_AUTO_FLATTEN")).fetchone()
    assert ev["n"] == 1, "EOD flatten should fire exactly once per session"


# ──────────────────────────────────────────────────────────────────────
# Snapshot exposes the armed flag
# ──────────────────────────────────────────────────────────────────────

def test_snapshot_includes_armed_flag(db_path):
    """The UI needs to know which working orders are awaiting their parent."""
    eng = se.SimEngine(alias="TEST", db_path=db_path)
    eng.place_bracket(
        side="BUY", qty=1,
        entry_stop=100.50, entry_limit=100.50,
        stop_loss=99.00, take_profits=[102.00])
    working = eng.snapshot()["working"]
    by_role = {w["role"]: w for w in working}
    assert by_role["ENTRY"]["armed_after_parent_fill"] == 0
    assert by_role["STOP"]["armed_after_parent_fill"] == 1
    assert by_role["TP"]["armed_after_parent_fill"] == 1
