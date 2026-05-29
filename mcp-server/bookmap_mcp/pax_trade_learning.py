"""Closed-loop SIM trade learning for Pax AI.

Stage 1 links executed Pax theses to SimEngine orders.
Stage 2 turns linked trades into setup scorecards.
Stage 3 emits deterministic policy suggestions.

Everything here is read-only against the SIM broker database.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .pax_manual import DEFAULT_SIM_DB
from .pax_sim_tools import LEARN_DIR, tail_lines

AGENT_LOG = LEARN_DIR / "agent-loop.jsonl"
SCORECARD_PATH = LEARN_DIR / "scorecard.json"
RUNTIME_POLICY_PATH = LEARN_DIR / "runtime-policy.json"


@dataclass(frozen=True)
class TradeOutcome:
    entry_order_id: int
    alias: str
    side: str
    setup_type: str
    level: str
    session_type: str
    expectancy: Optional[float]
    expectancy_source: Optional[str]
    entry_price: Optional[float]
    stop_price: Optional[float]
    risk_pts: Optional[float]
    status: str
    realized_r: Optional[float]
    mfe_r: Optional[float]
    mae_r: Optional[float]
    closed_qty: int
    entry_qty: int
    first_entry_ms: Optional[int]
    last_exit_ms: Optional[int]


def _f(value: Any) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def _i(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _read_jsonl(path: Path, max_lines: int = 3000) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for line in tail_lines(path, max_lines=max_lines):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _executed_entry_records(records: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for rec in records:
        ids = ((rec.get("exec") or {}).get("ids") or {})
        entry = _i(ids.get("entry"))
        if not entry:
            continue
        order = rec.get("order") or {}
        out.append({
            "entry_order_id": entry,
            "stop_order_id": _i(ids.get("stop")),
            "tp_order_ids": [_i(x) for x in (ids.get("tps") or []) if _i(x)],
            "ts_ms": _i(rec.get("ts_ms")),
            "alias": rec.get("alias"),
            "side": order.get("side") or rec.get("side"),
            "setup_type": rec.get("setup_type") or "UNKNOWN",
            "level": rec.get("level") or "UNKNOWN",
            "session_type": rec.get("stype") or rec.get("session_type") or "UNKNOWN",
            "expectancy": _f(rec.get("expectancy")),
            "expectancy_source": rec.get("expectancy_source"),
            "entry_stop": _f(order.get("entry_stop")),
            "stop_loss": _f(order.get("stop_loss")),
        })
    return out


def _load_orders(db_path: Path, order_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    if not order_ids or not Path(db_path).exists():
        return {}
    qs = ",".join("?" for _ in order_ids)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, alias, parent_id, side, type, qty, limit_price, stop_price, "
            "status, placed_ms, triggered_ms, filled_ms, filled_price, "
            "filled_qty, role, reason, decision_tag "
            f"FROM orders WHERE id IN ({qs})",
            order_ids,
        ).fetchall()
        return {int(r["id"]): dict(r) for r in rows}
    finally:
        conn.close()


def _signed_points(side: str, entry: float, exit_price: float) -> float:
    return exit_price - entry if str(side).upper() == "LONG" else entry - exit_price


def _excursion_r(side: str, entry: Optional[float], risk: Optional[float],
                 mids: Iterable[float]) -> Dict[str, Optional[float]]:
    if entry is None or risk is None or risk <= 0:
        return {"mfe_r": None, "mae_r": None}
    vals = list(mids)
    if not vals:
        return {"mfe_r": None, "mae_r": None}
    if side == "LONG":
        rs = [(m - entry) / risk for m in vals]
    else:
        rs = [(entry - m) / risk for m in vals]
    return {"mfe_r": round(max(rs), 6), "mae_r": round(min(rs), 6)}


def _mids_between(records: Iterable[Mapping[str, Any]],
                  start_ms: Optional[int],
                  end_ms: Optional[int]) -> List[float]:
    if start_ms is None:
        return []
    hi = end_ms if end_ms is not None else 9_999_999_999_999
    mids: List[float] = []
    for rec in records:
        ts = _i(rec.get("ts_ms"))
        mid = _f(rec.get("mid"))
        if ts is not None and mid is not None and start_ms <= ts <= hi:
            mids.append(mid)
    return mids


def link_record_to_outcome(record: Mapping[str, Any],
                           orders: Mapping[int, Mapping[str, Any]],
                           timeline: Iterable[Mapping[str, Any]] = ()) -> Optional[TradeOutcome]:
    entry_id = _i(record.get("entry_order_id"))
    if not entry_id:
        return None
    entry = orders.get(entry_id)
    if not entry:
        return None
    entry_price = _f(entry.get("filled_price"))
    entry_qty = _i(entry.get("filled_qty")) or _i(entry.get("qty")) or 0
    side = str(record.get("side") or "").upper()
    if side not in ("LONG", "SHORT"):
        side = "LONG" if str(entry.get("side")).upper() == "BUY" else "SHORT"

    stop_price = _f(record.get("stop_loss"))
    if stop_price is None:
        stop_order = orders.get(_i(record.get("stop_order_id")) or -1)
        stop_price = _f((stop_order or {}).get("stop_price")) or _f((stop_order or {}).get("limit_price"))

    child_ids = [x for x in ([record.get("stop_order_id")] + list(record.get("tp_order_ids") or [])) if _i(x)]
    child_orders = [orders[int(x)] for x in child_ids if int(x) in orders]
    filled_children = [o for o in child_orders
                       if str(o.get("status")) == "FILLED" and _f(o.get("filled_price")) is not None]
    closed_qty = sum(_i(o.get("filled_qty")) or _i(o.get("qty")) or 0
                     for o in filled_children)

    risk = None
    realized = None
    if entry_price is not None and stop_price is not None:
        risk = abs(entry_price - stop_price)
        if risk > 0 and entry_qty > 0 and filled_children:
            total = 0.0
            for o in filled_children:
                qty = _i(o.get("filled_qty")) or _i(o.get("qty")) or 0
                px = _f(o.get("filled_price"))
                if px is None:
                    continue
                total += (_signed_points(side, entry_price, px) / risk) * (qty / entry_qty)
            realized = round(total, 6)

    first_entry_ms = _i(entry.get("filled_ms"))
    last_exit_ms = max((_i(o.get("filled_ms")) or 0 for o in filled_children), default=0) or None
    excursions = _excursion_r(
        side, entry_price, risk, _mids_between(timeline, first_entry_ms, last_exit_ms))

    if entry_price is None:
        status = "UNFILLED"
    elif closed_qty >= entry_qty and entry_qty > 0:
        status = "CLOSED"
    elif closed_qty > 0:
        status = "PARTIAL"
    else:
        status = "OPEN"

    return TradeOutcome(
        entry_order_id=entry_id,
        alias=str(entry.get("alias") or record.get("alias") or ""),
        side=side,
        setup_type=str(record.get("setup_type") or "UNKNOWN"),
        level=str(record.get("level") or "UNKNOWN"),
        session_type=str(record.get("session_type") or "UNKNOWN"),
        expectancy=record.get("expectancy"),
        expectancy_source=record.get("expectancy_source"),
        entry_price=entry_price,
        stop_price=stop_price,
        risk_pts=round(risk, 6) if risk is not None else None,
        status=status,
        realized_r=realized,
        mfe_r=excursions["mfe_r"],
        mae_r=excursions["mae_r"],
        closed_qty=closed_qty,
        entry_qty=entry_qty,
        first_entry_ms=first_entry_ms,
        last_exit_ms=last_exit_ms,
    )


def link_agent_log(agent_log: Path = AGENT_LOG,
                   db_path: Path = DEFAULT_SIM_DB,
                   max_lines: int = 3000) -> List[TradeOutcome]:
    raw_records = _read_jsonl(agent_log, max_lines=max_lines)
    records = _executed_entry_records(raw_records)
    ids: List[int] = []
    for rec in records:
        ids.append(int(rec["entry_order_id"]))
        if rec.get("stop_order_id"):
            ids.append(int(rec["stop_order_id"]))
        ids.extend(int(x) for x in rec.get("tp_order_ids") or [])
    orders = _load_orders(db_path, sorted(set(ids)))
    linked = [link_record_to_outcome(rec, orders, timeline=raw_records) for rec in records]
    return [x for x in linked if x is not None]


def scorecard(outcomes: Iterable[TradeOutcome],
              min_samples: int = 3) -> Dict[str, Any]:
    buckets: Dict[str, List[TradeOutcome]] = {}
    for out in outcomes:
        if out.status not in ("CLOSED", "PARTIAL") or out.realized_r is None:
            continue
        key = "|".join((out.setup_type, out.side, out.level, out.session_type))
        buckets.setdefault(key, []).append(out)

    rows: List[Dict[str, Any]] = []
    for key, group in sorted(buckets.items()):
        n = len(group)
        mean_r = sum(float(o.realized_r) for o in group) / n
        hit_rate = sum(1 for o in group if float(o.realized_r) > 0) / n
        avg_risk = sum(float(o.risk_pts or 0.0) for o in group) / n
        mfe_vals = [float(o.mfe_r) for o in group if o.mfe_r is not None]
        mae_vals = [float(o.mae_r) for o in group if o.mae_r is not None]
        rows.append({
            "setup": key,
            "n": n,
            "mean_realized_r": round(mean_r, 6),
            "hit_rate": round(hit_rate, 6),
            "avg_risk_pts": round(avg_risk, 6),
            "mfe_mean_r": round(sum(mfe_vals) / len(mfe_vals), 6) if mfe_vals else None,
            "mae_mean_r": round(sum(mae_vals) / len(mae_vals), 6) if mae_vals else None,
            "warning": None if n >= min_samples else "insufficient_sample_count",
        })
    return {"min_samples": min_samples, "setups": rows}


def policy_suggestions(card: Mapping[str, Any]) -> Dict[str, Any]:
    suggestions: List[Dict[str, Any]] = []
    for row in card.get("setups") or []:
        if row.get("warning"):
            continue
        mean_r = float(row.get("mean_realized_r") or 0.0)
        hit = float(row.get("hit_rate") or 0.0)
        setup = row.get("setup")
        if mean_r <= -0.25 or hit < 0.35:
            action = "THROTTLE"
            reason = f"negative expectancy meanR={mean_r:+.2f} hit={hit:.0%}"
        elif mean_r >= 0.35 and hit >= 0.55:
            action = "PROMOTE"
            reason = f"positive expectancy meanR={mean_r:+.2f} hit={hit:.0%}"
        else:
            action = "KEEP"
            reason = f"mixed expectancy meanR={mean_r:+.2f} hit={hit:.0%}"
        suggestions.append({"setup": setup, "action": action, "reason": reason})
    return {"suggestions": suggestions}


def geometry_suggestions(card: Mapping[str, Any]) -> Dict[str, Any]:
    suggestions: List[Dict[str, Any]] = []
    for row in card.get("setups") or []:
        if row.get("warning"):
            continue
        setup = row.get("setup")
        mfe = row.get("mfe_mean_r")
        mae = row.get("mae_mean_r")
        mean_r = float(row.get("mean_realized_r") or 0.0)
        if mae is not None and float(mae) < -0.85 and mean_r < 0:
            suggestions.append({
                "setup": setup,
                "action": "REVIEW_STOP",
                "reason": f"avg adverse excursion {float(mae):+.2f}R with meanR={mean_r:+.2f}",
            })
        if mfe is not None and float(mfe) >= 1.2 and mean_r < 0.35:
            suggestions.append({
                "setup": setup,
                "action": "REVIEW_TARGETS",
                "reason": f"avg favorable excursion {float(mfe):+.2f}R not captured; meanR={mean_r:+.2f}",
            })
    return {"suggestions": suggestions}


def persist_learning_artifacts(summary: Mapping[str, Any],
                               *,
                               scorecard_path: Path = SCORECARD_PATH,
                               runtime_policy_path: Path = RUNTIME_POLICY_PATH) -> None:
    scorecard_path.parent.mkdir(parents=True, exist_ok=True)
    scorecard_path.write_text(
        json.dumps(summary.get("scorecard") or {}, indent=2, default=str),
        encoding="utf-8")
    runtime_policy_path.write_text(
        json.dumps(summary.get("policy") or {}, indent=2, default=str),
        encoding="utf-8")


def summarize_learning(agent_log: Path = AGENT_LOG,
                       db_path: Path = DEFAULT_SIM_DB,
                       max_lines: int = 3000,
                       min_samples: int = 3,
                       persist: bool = False) -> Dict[str, Any]:
    outcomes = link_agent_log(agent_log=agent_log, db_path=db_path, max_lines=max_lines)
    card = scorecard(outcomes, min_samples=min_samples)
    summary = {
        "n_linked": len(outcomes),
        "n_closed": sum(1 for o in outcomes if o.realized_r is not None),
        "scorecard": card,
        "policy": policy_suggestions(card),
        "geometry": geometry_suggestions(card),
    }
    if persist:
        persist_learning_artifacts(summary)
    return summary
