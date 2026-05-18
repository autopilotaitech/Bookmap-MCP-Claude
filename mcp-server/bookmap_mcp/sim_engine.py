"""Local SIM execution engine — never touches a live broker.

Storage: SQLite at D:\\BookmapLogs\\pax-trades.db
  orders     working/filled/canceled orders with full lifecycle
  positions  per-alias position snapshot + realized/unrealized P&L
  events     audit trail of every state change

Order types supported:
  LIMIT       fills when ask <= limit (BUY) or bid >= limit (SELL) and a print
              actually traded through that price
  STOP_LIMIT  triggers when last >= stop (BUY) / last <= stop (SELL); once
              triggered behaves like a LIMIT at the limit price
  MARKET      fills at next print (used for flatten-on-exit only)

Fill rule (conservative — chosen by user):
  Scan /recent_trades since the order was placed (or triggered). Fill only
  when a real print exists at or through the limit price. No fill on BBO
  touch alone. This mirrors what a marketable limit would actually get.

Sizing: FULL = 3, HALF = 1. Per Pax canon of thirds.

Public API:
    eng = SimEngine(alias="NQM6.CME@RITHMIC")
    eng.place_stop_limit(side="BUY", qty=3, stop=21340.25, limit=21340.50,
                         tp=[21350.00, 21405.25], stop_loss=21320.00,
                         tif_sec=90, reason="ENTER_LONG_FOLLOW @ OR-H")
    eng.tick(snap)        # call every poll; runs fills, TIF expiry, TP/SL triggers
    eng.snapshot()        # everything for the dashboard
    eng.flatten("EOD")    # close all positions and cancel working orders

This is SIM only. There is NO code path here that calls /place_limit_order
or any live trading endpoint. By design.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ─── config ─────────────────────────────────────────────────────────────────

LOG_DIR = Path(os.environ.get("PAX_LOG_DIR", r"D:\BookmapLogs"))
DB_PATH = LOG_DIR / "pax-trades.db"

NQ_TICK_VALUE_USD  = 5.0     # one NQ tick = $5
MNQ_TICK_VALUE_USD = 0.5
NQ_TICK_PRICE      = 0.25

SIZE_BY_TIER = {"FULL": 3, "HALF": 1, "NONE": 0}

# Order types
OT_LIMIT, OT_STOP_LIMIT, OT_MARKET = "LIMIT", "STOP_LIMIT", "MARKET"
# Sides
S_BUY, S_SELL = "BUY", "SELL"
# Statuses
ST_WORKING, ST_TRIGGERED, ST_FILLED, ST_CANCELED, ST_REJECTED = \
    "WORKING", "TRIGGERED", "FILLED", "CANCELED", "REJECTED"


# ─── schema ─────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
  id                        INTEGER PRIMARY KEY AUTOINCREMENT,
  alias                     TEXT NOT NULL,
  parent_id                 INTEGER,
  side                      TEXT NOT NULL,
  type                      TEXT NOT NULL,
  qty                       INTEGER NOT NULL,
  limit_price               REAL,
  stop_price                REAL,
  status                    TEXT NOT NULL,
  placed_ms                 INTEGER NOT NULL,
  triggered_ms              INTEGER,
  filled_ms                 INTEGER,
  filled_price              REAL,
  filled_qty                INTEGER DEFAULT 0,
  tif_sec                   REAL,
  canceled_ms               INTEGER,
  reason                    TEXT,
  role                      TEXT,
  decision_tag              TEXT,
  -- Phase 0: bracket children (SL/TP) must NOT trigger or fill until the
  -- parent entry has filled. 0 = order is independently executable; 1 = order
  -- is locked until (SELECT status FROM orders WHERE id=parent_id) == FILLED.
  armed_after_parent_fill   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_orders_status   ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_alias    ON orders(alias);

CREATE TABLE IF NOT EXISTS positions (
  alias        TEXT PRIMARY KEY,
  size         INTEGER NOT NULL DEFAULT 0,
  avg_price    REAL NOT NULL DEFAULT 0,
  realized_pnl REAL NOT NULL DEFAULT 0,
  day_anchor_ms INTEGER NOT NULL DEFAULT 0,
  updated_ms   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_ms       INTEGER NOT NULL,
  alias       TEXT,
  kind        TEXT NOT NULL,
  order_id    INTEGER,
  payload     TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_alias_ts ON events(alias, ts_ms);
"""


# ─── engine ─────────────────────────────────────────────────────────────────

class SimEngine:
    """One engine per alias (instrument). Thread-safe."""

    def __init__(self, alias: str, db_path: Optional[Path] = None,
                 tick_size: float = NQ_TICK_PRICE,
                 tick_value_usd: float = NQ_TICK_VALUE_USD,
                 eod_close_hour_ct: Optional[int] = 15,
                 eod_close_minute_ct: int = 0) -> None:
        """`eod_close_hour_ct=None` disables auto-flatten entirely (useful in
        tests so the wall-clock at test-run time can never trip the EOD
        path)."""
        self.alias = alias
        self.tick_size = tick_size
        self.tick_value = tick_value_usd
        self.eod_close_hour_ct = eod_close_hour_ct
        self.eod_close_minute_ct = eod_close_minute_ct
        self._lock = threading.Lock()
        self._db_path = Path(db_path) if db_path else DB_PATH
        self._init_db()
        self._last_trade_seen_ns: int = 0   # cursor into recent_trades stream
        # Track which session's EOD auto-flatten has already fired so we don't
        # re-flatten on every tick after 15:00 CT.
        self._eod_flattened_anchor_ms: Optional[int] = None

    # ─── DB helpers ───────────────────────────────────────────────────────

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)
            # Phase 0 migration: existing DBs may lack the armed_after_parent_fill
            # column. ALTER TABLE is idempotent via column presence check.
            cols = {r["name"] for r in
                     c.execute("PRAGMA table_info(orders)").fetchall()}
            if "armed_after_parent_fill" not in cols:
                c.execute("ALTER TABLE orders ADD COLUMN "
                          "armed_after_parent_fill INTEGER NOT NULL DEFAULT 0")
                self._log(c, "MIGRATE", None,
                          {"action": "add_armed_after_parent_fill"})
            row = c.execute("SELECT alias FROM positions WHERE alias=?",
                            (self.alias,)).fetchone()
            if not row:
                c.execute("INSERT INTO positions(alias,size,avg_price,realized_pnl,"
                          "day_anchor_ms,updated_ms) VALUES(?,?,?,?,?,?)",
                          (self.alias, 0, 0.0, 0.0, _now_ms(), _now_ms()))
            # Phase 0: on startup, scan working/triggered orders placed before
            # today's RTH anchor and log a STALE_RESET event for each so the
            # audit trail records that they survived a daemon restart.
            anchor_ms = _today_rth_anchor_ms()
            for r in c.execute(
                    "SELECT id, role, placed_ms FROM orders "
                    "WHERE alias=? AND status IN (?, ?) AND placed_ms < ?",
                    (self.alias, ST_WORKING, ST_TRIGGERED, anchor_ms)).fetchall():
                self._log(c, "STALE_RESET", r["id"],
                          {"role": r["role"], "placed_ms": r["placed_ms"],
                           "session_anchor_ms": anchor_ms})

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False, timeout=2.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _log(self, c: sqlite3.Connection, kind: str,
             order_id: Optional[int] = None, payload: Any = None) -> None:
        c.execute("INSERT INTO events(ts_ms, alias, kind, order_id, payload) "
                  "VALUES(?,?,?,?,?)",
                  (_now_ms(), self.alias, kind, order_id,
                   json.dumps(payload, default=str) if payload is not None else None))

    # ─── place orders ────────────────────────────────────────────────────

    def place_limit(self, side: str, qty: int, limit: float,
                    tif_sec: float = 90.0, reason: str = "",
                    role: str = "ENTRY", parent_id: Optional[int] = None,
                    decision_tag: Optional[str] = None,
                    armed_after_parent_fill: bool = False) -> int:
        return self._place(side=side, qty=qty, type_=OT_LIMIT,
                           limit=limit, stop=None,
                           tif_sec=tif_sec, reason=reason, role=role,
                           parent_id=parent_id, decision_tag=decision_tag,
                           armed_after_parent_fill=armed_after_parent_fill)

    def place_stop_limit(self, side: str, qty: int, stop: float, limit: float,
                         tif_sec: float = 90.0, reason: str = "",
                         role: str = "ENTRY", parent_id: Optional[int] = None,
                         decision_tag: Optional[str] = None,
                         armed_after_parent_fill: bool = False) -> int:
        return self._place(side=side, qty=qty, type_=OT_STOP_LIMIT,
                           limit=limit, stop=stop,
                           tif_sec=tif_sec, reason=reason, role=role,
                           parent_id=parent_id, decision_tag=decision_tag,
                           armed_after_parent_fill=armed_after_parent_fill)

    def _place(self, side: str, qty: int, type_: str,
               limit: Optional[float], stop: Optional[float],
               tif_sec: float, reason: str, role: str,
               parent_id: Optional[int],
               decision_tag: Optional[str],
               armed_after_parent_fill: bool = False) -> int:
        if side not in (S_BUY, S_SELL): raise ValueError("side")
        if qty <= 0: raise ValueError("qty>0")
        with self._lock, self._conn() as c:
            cur = c.execute(
                "INSERT INTO orders(alias, parent_id, side, type, qty, "
                "limit_price, stop_price, status, placed_ms, tif_sec, "
                "reason, role, decision_tag, armed_after_parent_fill) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.alias, parent_id, side, type_, qty,
                 limit, stop,
                 ST_WORKING,
                 _now_ms(), tif_sec, reason, role, decision_tag,
                 1 if armed_after_parent_fill else 0))
            oid = cur.lastrowid
            self._log(c, "PLACE", oid,
                      {"side": side, "type": type_, "qty": qty,
                       "limit": limit, "stop": stop,
                       "reason": reason, "role": role,
                       "armed_after_parent_fill": bool(armed_after_parent_fill)})
        return oid

    def place_bracket(self, side: str, qty: int,
                      entry_stop: Optional[float], entry_limit: float,
                      stop_loss: float, take_profits: List[float],
                      decision_tag: Optional[str] = None,
                      reason: str = "") -> Dict[str, Any]:
        """Convenience: place an entry + protective stop + N take-profit limits.

        TPs and stop are placed but only ARM after the entry fills (handled
        inside tick()).  Returns the dict of order IDs.
        """
        # Entry is independently executable (armed_after_parent_fill=False).
        if entry_stop is not None:
            entry_id = self.place_stop_limit(side=side, qty=qty,
                                             stop=entry_stop, limit=entry_limit,
                                             role="ENTRY", reason=reason,
                                             decision_tag=decision_tag,
                                             armed_after_parent_fill=False)
        else:
            entry_id = self.place_limit(side=side, qty=qty, limit=entry_limit,
                                        role="ENTRY", reason=reason,
                                        decision_tag=decision_tag,
                                        armed_after_parent_fill=False)
        # SL and TPs are bracket children — armed only after the parent entry fills.
        exit_side = S_SELL if side == S_BUY else S_BUY
        stop_id = self.place_stop_limit(
            side=exit_side, qty=qty, stop=stop_loss,
            limit=stop_loss + (-self.tick_size if exit_side == S_SELL else self.tick_size),
            role="STOP", reason="bracket stop-loss", parent_id=entry_id,
            tif_sec=24*3600, decision_tag=decision_tag,
            armed_after_parent_fill=True)
        # Equal scale-out across TPs
        tp_ids: List[int] = []
        if take_profits:
            base = qty // len(take_profits)
            rem = qty - base * len(take_profits)
            for i, tp in enumerate(take_profits):
                tp_qty = base + (1 if i < rem else 0)
                if tp_qty <= 0: continue
                tp_id = self.place_limit(side=exit_side, qty=tp_qty, limit=tp,
                                         role="TP", reason=f"bracket TP{i+1}",
                                         parent_id=entry_id, tif_sec=24*3600,
                                         decision_tag=decision_tag,
                                         armed_after_parent_fill=True)
                tp_ids.append(tp_id)
        return {"entry": entry_id, "stop": stop_id, "tps": tp_ids}

    def cancel(self, order_id: int, reason: str = "manual") -> bool:
        with self._lock, self._conn() as c:
            row = c.execute("SELECT status FROM orders WHERE id=? AND alias=?",
                            (order_id, self.alias)).fetchone()
            if not row or row["status"] not in (ST_WORKING, ST_TRIGGERED):
                return False
            now_ms = _now_ms()
            c.execute("UPDATE orders SET status=?, canceled_ms=?, reason=COALESCE(reason,'') || ' | cancel: ' || ? WHERE id=?",
                      (ST_CANCELED, now_ms, reason, order_id))
            self._log(c, "CANCEL", order_id, {"reason": reason})
            # Phase 0: cascade-cancel any unfilled bracket children. If the
            # parent never filled, the children would otherwise sit forever
            # (their TIF is 24h and armed_after_parent_fill keeps them dormant).
            child_ids = [r["id"] for r in c.execute(
                "SELECT id FROM orders WHERE alias=? AND parent_id=? AND status IN (?, ?)",
                (self.alias, order_id, ST_WORKING, ST_TRIGGERED)).fetchall()]
            for cid in child_ids:
                c.execute("UPDATE orders SET status=?, canceled_ms=?, "
                          "reason=COALESCE(reason,'') || ' | parent " + str(order_id) + " canceled' "
                          "WHERE id=?",
                          (ST_CANCELED, now_ms, cid))
                self._log(c, "CANCEL", cid,
                          {"reason": f"parent {order_id} canceled: {reason}"})
        return True

    def cancel_role(self, role: str, parent_id: Optional[int] = None,
                    reason: str = "") -> int:
        """Cancel all working orders matching role (e.g. all TPs of a parent)."""
        ids = []
        with self._lock, self._conn() as c:
            q = ("SELECT id FROM orders WHERE alias=? AND role=? AND "
                 "status IN (?, ?)")
            args: list = [self.alias, role, ST_WORKING, ST_TRIGGERED]
            if parent_id is not None:
                q += " AND parent_id=?"; args.append(parent_id)
            ids = [r["id"] for r in c.execute(q, args).fetchall()]
        for oid in ids:
            self.cancel(oid, reason=f"role-cancel {role}: {reason}")
        return len(ids)

    # ─── poll / fill logic ───────────────────────────────────────────────

    def tick(self, snap: Dict[str, Any],
              now_ms: Optional[int] = None) -> Dict[str, Any]:
        """Drive the engine from the latest /api/snapshot.

        `now_ms` overrides the wall-clock for tests (EOD auto-flatten timing).
        Returns a summary dict for the dashboard.
        """
        if not snap or snap.get("alias") != self.alias:
            return {"_skip": "alias mismatch or empty snap"}
        trades = snap.get("trades") or []
        # /recent_trades returns newest first; sort to chronological
        try:
            sorted_trades = sorted(trades, key=lambda t: t.get("nanos") or 0)
        except (TypeError, KeyError):
            sorted_trades = trades

        book = snap.get("book") or {}
        bbo  = {"bid": _safe_f(book.get("bestBid")),
                "ask": _safe_f(book.get("bestAsk"))}

        actions: List[Dict[str, Any]] = []
        eod_action = self._maybe_eod_flatten(now_ms)
        if eod_action is not None:
            actions.append(eod_action)
        with self._lock, self._conn() as c:
            # 1. TIF expirations on working entries (cascade-cancels children).
            wall_ms = now_ms if now_ms is not None else _now_ms()
            for row in c.execute(
                    "SELECT id, placed_ms, tif_sec, role FROM orders "
                    "WHERE alias=? AND status=? AND tif_sec IS NOT NULL AND role='ENTRY'",
                    (self.alias, ST_WORKING)).fetchall():
                if (wall_ms - row["placed_ms"]) / 1000.0 > row["tif_sec"]:
                    c.execute("UPDATE orders SET status=?, canceled_ms=?, reason=COALESCE(reason,'') || ' | TIF expired' WHERE id=?",
                              (ST_CANCELED, wall_ms, row["id"]))
                    self._log(c, "TIF_EXPIRE", row["id"], None)
                    actions.append({"kind": "TIF_EXPIRE", "id": row["id"]})
                    # Cascade: cancel children too (mirror of cancel()).
                    child_ids = [r["id"] for r in c.execute(
                        "SELECT id FROM orders WHERE alias=? AND parent_id=? AND status IN (?, ?)",
                        (self.alias, row["id"], ST_WORKING, ST_TRIGGERED)).fetchall()]
                    for cid in child_ids:
                        c.execute("UPDATE orders SET status=?, canceled_ms=?, "
                                  "reason=COALESCE(reason,'') || ' | parent TIF expired' WHERE id=?",
                                  (ST_CANCELED, wall_ms, cid))
                        self._log(c, "CANCEL", cid,
                                  {"reason": f"parent {row['id']} TIF expired"})

            # 2. Process new trades (since cursor) — try to fill each working order
            for t in sorted_trades:
                t_ns = t.get("nanos") or 0
                # MED 2.3: skip trades with no clock yet (TimeListener hasn't
                # fired) — Java TradeRecord.nanos() returns 0 until the first
                # onTimestamp, which would otherwise cause every poll to
                # reprocess the same stale prints and fire phantom fills.
                if t_ns == 0: continue
                if t_ns <= self._last_trade_seen_ns: continue
                self._last_trade_seen_ns = t_ns
                px = _safe_f(t.get("price"))
                if px is None: continue
                # Phase 0 gate: a bracket child (armed_after_parent_fill=1) is
                # invisible to the fill loop until its parent has FILLED. This
                # prevents the SL or TP from triggering on a trade-through that
                # crosses their price before the entry fills. Outer table is
                # aliased `o` so the correlated subquery resolves unambiguously.
                working = c.execute(
                    "SELECT o.* FROM orders o WHERE o.alias=? "
                    "AND o.status IN (?, ?) AND "
                    "(o.armed_after_parent_fill=0 OR "
                    " (SELECT status FROM orders p WHERE p.id=o.parent_id)=?)",
                    (self.alias, ST_WORKING, ST_TRIGGERED, ST_FILLED)).fetchall()
                for o in working:
                    o = dict(o)
                    # Stop-limit: trigger first, then act as limit
                    if o["type"] == OT_STOP_LIMIT and o["status"] == ST_WORKING:
                        triggered = (
                            (o["side"] == S_BUY  and px >= _safe_f(o["stop_price"])) or
                            (o["side"] == S_SELL and px <= _safe_f(o["stop_price"]))
                        )
                        if not triggered: continue
                        c.execute("UPDATE orders SET status=?, triggered_ms=? WHERE id=?",
                                  (ST_TRIGGERED, wall_ms, o["id"]))
                        o["status"] = ST_TRIGGERED
                        self._log(c, "STOP_TRIGGER", o["id"], {"price": px})
                        actions.append({"kind": "STOP_TRIGGER", "id": o["id"], "px": px})

                    # LIMIT fill check (also triggered stop-limit)
                    if o["status"] in (ST_WORKING, ST_TRIGGERED) and o["type"] != OT_MARKET:
                        lp = _safe_f(o["limit_price"])
                        if lp is None: continue
                        # Conservative: actual print at or through limit price
                        # on the marketable side
                        can_fill = (
                            (o["side"] == S_BUY  and px <= lp) or
                            (o["side"] == S_SELL and px >= lp)
                        )
                        # Only fill against an aggressor in our favor (or matching)
                        side = t.get("side", "")
                        cross_match = (o["side"] == S_BUY  and side == "sell") or \
                                       (o["side"] == S_SELL and side == "buy") or \
                                       side == ""
                        if can_fill and cross_match:
                            self._fill_order(c, o, px, wall_ms)
                            actions.append({"kind": "FILL", "id": o["id"],
                                            "px": px, "qty": o["qty"]})

                    # MARKET — fills at next print regardless of side
                    if o["type"] == OT_MARKET and o["status"] == ST_WORKING:
                        self._fill_order(c, o, px, wall_ms)
                        actions.append({"kind": "FILL", "id": o["id"], "px": px, "qty": o["qty"]})

            # 3. Mark-to-market unrealized & day P&L for dashboard
            pos = self._read_position(c)
            mid = _safe_f(book.get("mid"))
            unreal = 0.0
            if pos["size"] != 0 and mid is not None and pos["avg_price"]:
                ticks = (mid - pos["avg_price"]) / self.tick_size
                unreal = ticks * self.tick_value * abs(pos["size"]) * (1 if pos["size"] > 0 else -1)

        return {
            "actions": actions,
            "bbo": bbo,
            "position": pos,
            "unrealized_usd": round(unreal, 2),
            "tick_size": self.tick_size,
        }

    def _fill_order(self, c: sqlite3.Connection, o: Dict[str, Any],
                    px: float, now_ms: int) -> None:
        """Apply a fill to an order and update position state."""
        c.execute("UPDATE orders SET status=?, filled_ms=?, filled_price=?, filled_qty=? WHERE id=?",
                  (ST_FILLED, now_ms, px, o["qty"], o["id"]))
        self._log(c, "FILL", o["id"], {"price": px, "qty": o["qty"], "side": o["side"]})
        # Phase 0: when a parent ENTRY fills, log an ARM_CHILD event for each
        # of its bracket children. The fill-loop's working-orders query already
        # gates on parent status, so the children become executable on the
        # next iteration automatically — this is purely for the audit trail.
        if o.get("role") == "ENTRY":
            for child in c.execute(
                    "SELECT id, role FROM orders "
                    "WHERE alias=? AND parent_id=? AND status IN (?, ?) "
                    "AND armed_after_parent_fill=1",
                    (self.alias, o["id"], ST_WORKING, ST_TRIGGERED)).fetchall():
                self._log(c, "ARM_CHILD", child["id"],
                          {"parent_id": o["id"], "role": child["role"]})

        # Update position
        pos = self._read_position(c)
        sign = +1 if o["side"] == S_BUY else -1
        new_size = pos["size"] + sign * o["qty"]
        realized_delta = 0.0
        if pos["size"] != 0 and sign != (1 if pos["size"] > 0 else -1):
            # Closing or reversing — realize P&L on the closed portion
            closed = min(abs(pos["size"]), o["qty"]) * (1 if pos["size"] > 0 else -1)
            ticks = (px - pos["avg_price"]) / self.tick_size
            realized_delta = ticks * self.tick_value * closed
            remaining_open = abs(pos["size"]) - abs(closed)   # original-side remainder
            remaining_new  = o["qty"] - abs(closed)             # new-side excess (reversal)
            if remaining_new > 0:
                # Reversed: new position is opposite side at fill price
                new_avg = px
            elif remaining_open > 0:
                # Partial close: keep original avg_price for the remainder
                new_avg = pos["avg_price"]
            else:
                # Flat
                new_avg = 0.0
        else:
            # Adding to or opening
            old_notional = pos["avg_price"] * abs(pos["size"])
            new_notional = old_notional + px * o["qty"]
            new_avg = new_notional / abs(new_size) if new_size != 0 else 0.0
        c.execute("UPDATE positions SET size=?, avg_price=?, realized_pnl=?, updated_ms=? WHERE alias=?",
                  (new_size, new_avg if new_size != 0 else 0.0,
                   pos["realized_pnl"] + realized_delta, now_ms, self.alias))
        self._log(c, "POSITION_UPDATE", o["id"],
                  {"new_size": new_size, "new_avg": new_avg,
                   "realized_delta": realized_delta})

        # If position flat, cancel any orphaned protective orders for the same parent
        if new_size == 0:
            parent = o.get("parent_id") or o["id"]
            c.execute("UPDATE orders SET status=?, canceled_ms=?, "
                      "reason=COALESCE(reason,'') || ' | auto-cancel: position flat' "
                      "WHERE alias=? AND status IN (?, ?) AND (parent_id=? OR id=?) AND id != ?",
                      (ST_CANCELED, now_ms, self.alias,
                       ST_WORKING, ST_TRIGGERED, parent, parent, o["id"]))
            self._log(c, "AUTO_CANCEL_BRACKET", o["id"], {"parent": parent})

    def _read_position(self, c: sqlite3.Connection) -> Dict[str, Any]:
        row = c.execute("SELECT * FROM positions WHERE alias=?", (self.alias,)).fetchone()
        if not row:
            return {"alias": self.alias, "size": 0, "avg_price": 0.0,
                    "realized_pnl": 0.0, "day_anchor_ms": 0, "updated_ms": 0}
        return dict(row)

    # ─── snapshot for dashboard ─────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        with self._lock, self._conn() as c:
            pos = self._read_position(c)
            working = [dict(r) for r in c.execute(
                "SELECT id, side, type, qty, limit_price, stop_price, "
                "status, placed_ms, tif_sec, role, reason, parent_id, decision_tag, "
                "armed_after_parent_fill "
                "FROM orders WHERE alias=? AND status IN (?, ?) ORDER BY id",
                (self.alias, ST_WORKING, ST_TRIGGERED)).fetchall()]
            today_anchor = _today_rth_anchor_ms()
            fills = [dict(r) for r in c.execute(
                "SELECT id, side, type, qty, filled_price, filled_ms, role, reason, decision_tag "
                "FROM orders WHERE alias=? AND status=? AND filled_ms>=? "
                "ORDER BY filled_ms DESC LIMIT 50",
                (self.alias, ST_FILLED, today_anchor)).fetchall()]
            # Today's realized
            day_real = c.execute(
                "SELECT COALESCE(SUM(json_extract(payload,'$.realized_delta')),0) AS r "
                "FROM events WHERE alias=? AND kind='POSITION_UPDATE' AND ts_ms>=?",
                (self.alias, today_anchor)).fetchone()["r"]
            n_today = c.execute(
                "SELECT COUNT(*) AS n FROM orders WHERE alias=? AND status=? AND filled_ms>=?",
                (self.alias, ST_FILLED, today_anchor)).fetchone()["n"]
        return {
            "alias": self.alias,
            "position": pos,
            "working": working,
            "fills_today": fills,
            "n_fills_today": n_today,
            "realized_today_usd": round(day_real or 0.0, 2),
        }

    def _maybe_eod_flatten(self, now_ms: Optional[int]) -> Optional[Dict[str, Any]]:
        """If the current wall-clock is past RTH close for today's session
        and we have not already flattened today, close out the position
        and cancel working orders. Idempotent — only fires once per
        session anchor. Returns None when EOD is disabled
        (`eod_close_hour_ct=None`) or before the close anchor."""
        if self.eod_close_hour_ct is None:
            return None
        anchor_ms = _today_rth_anchor_ms()
        if self._eod_flattened_anchor_ms == anchor_ms:
            return None
        wall_ms = now_ms if now_ms is not None else _now_ms()
        eod_ms = _today_eod_anchor_ms(self.eod_close_hour_ct,
                                       self.eod_close_minute_ct)
        if wall_ms < eod_ms:
            return None
        # Past RTH close for today's session. Flatten.
        result = self.flatten(reason="EOD")
        # Log the canonical EOD event so the audit trail is explicit.
        with self._conn() as c:
            self._log(c, "EOD_AUTO_FLATTEN", None,
                      {"session_anchor_ms": anchor_ms,
                       "eod_anchor_ms": eod_ms,
                       "wall_ms": wall_ms,
                       "result": result})
        self._eod_flattened_anchor_ms = anchor_ms
        return {"kind": "EOD_AUTO_FLATTEN", "result": result}


    def flatten(self, reason: str = "manual") -> Dict[str, Any]:
        """Cancel everything working, flatten any open position at next print."""
        cancels = 0
        with self._lock, self._conn() as c:
            ids = [r["id"] for r in c.execute(
                "SELECT id FROM orders WHERE alias=? AND status IN (?, ?)",
                (self.alias, ST_WORKING, ST_TRIGGERED)).fetchall()]
            for oid in ids:
                c.execute("UPDATE orders SET status=?, canceled_ms=?, "
                          "reason=COALESCE(reason,'') || ' | flatten: ' || ? WHERE id=?",
                          (ST_CANCELED, _now_ms(), reason, oid))
                self._log(c, "CANCEL", oid, {"reason": "flatten " + reason})
                cancels += 1
            pos = self._read_position(c)
        if pos["size"] != 0:
            close_side = S_SELL if pos["size"] > 0 else S_BUY
            mid = pos["avg_price"]  # placeholder — real fill on next tick
            self._place(side=close_side, qty=abs(pos["size"]),
                        type_=OT_MARKET, limit=None, stop=None,
                        tif_sec=999, reason=f"flatten: {reason}",
                        role="FLATTEN", parent_id=None, decision_tag=None)
        return {"canceled": cancels, "flattened_size": pos["size"]}


# ─── helpers ────────────────────────────────────────────────────────────────

def _now_ms() -> int: return int(time.time() * 1000)


def _safe_f(x: Any) -> Optional[float]:
    if x is None: return None
    try:
        v = float(x)
        if v != v: return None
        return v
    except (TypeError, ValueError):
        return None


def _today_rth_anchor_ms() -> int:
    """Most recent 08:30 CT anchor before now."""
    from zoneinfo import ZoneInfo
    ct = ZoneInfo("America/Chicago")
    now_ct = dt.datetime.now(ct)
    anchor = now_ct.replace(hour=8, minute=30, second=0, microsecond=0)
    if now_ct < anchor: anchor = anchor - dt.timedelta(days=1)
    return int(anchor.timestamp() * 1000)


def _today_eod_anchor_ms(hour_ct: int = 15, minute_ct: int = 0) -> int:
    """Today's RTH close anchor (default 15:00 CT)."""
    from zoneinfo import ZoneInfo
    ct = ZoneInfo("America/Chicago")
    now_ct = dt.datetime.now(ct)
    anchor = now_ct.replace(hour=hour_ct, minute=minute_ct,
                              second=0, microsecond=0)
    return int(anchor.timestamp() * 1000)
