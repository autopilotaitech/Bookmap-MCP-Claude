"""Phase 3A bus-backed digest renderer.

Reads from the in-memory snapshot dict (passed by caller) and the bus DB
(via feature_bus.recent_events + recent_ai_turns) to build a deterministic
user-message string that COULD be passed to Claude as `claude -p` input.

Block layout (NEW; intentionally different from the legacy
chat._digest_lines layout):
  [STATE] [ANCHOR] [GATES] [LEVELS] [MICROSTRUCTURE]
  [RECENT_EVENTS] [POSITION] [SESSION_MEMORY] [USER]

Read-only. NEVER mutates the snapshot. NEVER raises. Missing fields
collapse to "n/a" lines. Bus DB missing or disabled -> empty
RECENT_EVENTS / SESSION_MEMORY blocks (quiet).

router_hint is REQUIRED as an explicit argument; routing context must
not be silently dropped on flag-flip."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import feature_bus


def _state_block(snap: Dict[str, Any]) -> str:
    book = snap.get("book") or {}
    alias = snap.get("alias") or "n/a"
    mid = book.get("mid")
    spread = book.get("spread")
    health = snap.get("health") or "n/a"
    lines = [
        "[STATE]",
        f"  alias    : {alias}",
        f"  mid      : {mid if mid is not None else 'n/a'}",
        f"  spread   : {spread if spread is not None else 'n/a'}",
        f"  health   : {health}",
    ]
    return "\n".join(lines)


def _anchor_block(snap: Dict[str, Any]) -> str:
    gates = snap.get("gates") or {}
    sess = gates.get("session") or snap.get("session") or {}
    mode = sess.get("anchorMode") or "n/a"
    hhmm = sess.get("anchorHHMM") or "n/a"
    tz   = sess.get("anchorTimezone") or "n/a"
    lines = [
        "[ANCHOR]",
        f"  anchorMode : {mode}",
        f"  anchorHHMM : {hhmm}",
        f"  anchorTz   : {tz}",
    ]
    if mode != "LIVE":
        lines.insert(1, f"  INFORMATIONAL_ONLY: anchor mode is {mode}")
    return "\n".join(lines)


def _gates_block(snap: Dict[str, Any]) -> str:
    gates = snap.get("gates") or {}
    sess = gates.get("session") or {}
    news = gates.get("news") or {}
    lines = [
        "[GATES]",
        f"  session     : {sess.get('code') or 'n/a'}",
        f"  news        : {'BLOCKED' if news.get('blocked') else 'clear'}"
        + (f" ({news.get('label')})" if news.get('blocked') and news.get('label') else ""),
    ]
    return "\n".join(lines)


def _levels_block(snap: Dict[str, Any]) -> str:
    ors = snap.get("or_levels") or {}
    levels = list(ors.get("levels") or [])
    # Top-3 by |distance|.
    def _abs_dist(lvl):
        d = lvl.get("distance")
        try:
            return abs(float(d))
        except (TypeError, ValueError):
            return float("inf")
    levels.sort(key=_abs_dist)
    top = levels[:3]
    lines = ["[LEVELS]"]
    if not top:
        lines.append("  (no levels)")
    for lvl in top:
        label = lvl.get("label") or "?"
        price = lvl.get("price")
        dist  = lvl.get("distance")
        dec   = lvl.get("decision") or "WAIT"
        conf  = lvl.get("confidence") or 0.0
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            conf = 0.0
        lines.append(
            f"  {label:<6} {price if price is not None else 'n/a':>10}  "
            f"dist={dist if dist is not None else 'n/a':>8}  "
            f"{dec} (conf={conf:.2f})"
        )
    return "\n".join(lines)


def _microstructure_block(snap: Dict[str, Any]) -> str:
    flow = snap.get("flow") or {}
    conv = snap.get("conviction") or {}
    vw   = snap.get("vwap_bias") or {}
    vp   = snap.get("vp_bias") or {}
    trend = snap.get("trend_signal") or {}
    vw_comp = vw.get("components") or {}
    vp_comp = vp.get("components") or {}
    lines = [
        "[MICROSTRUCTURE]",
        f"  flow_regime   : {flow.get('regime') or 'n/a'} "
            f"(conf={flow.get('regimeConfidence') or 'n/a'}, "
            f"bias={flow.get('biasScore') or 'n/a'}, traj={flow.get('biasTrajectory') or 'n/a'})",
        f"  vwap_bias     : {vw.get('label') or 'n/a'} "
            f"(sigma_z={vw_comp.get('sigma_z') or 'n/a'})",
        f"  vp_bias       : {vp.get('label') or 'n/a'} "
            f"(va_state={vp_comp.get('va_state') or 'n/a'})",
        f"  conviction    : score={conv.get('score') or 'n/a'} "
            f"trend={conv.get('trend') or 'n/a'} "
            f"trajectory={conv.get('trajectory') or 'n/a'}",
        f"  trend_signal  : {trend.get('renderableKind') or trend.get('kind') or 'NONE'}",
    ]
    return "\n".join(lines)


def _format_ts(ts_ms: Optional[int]) -> str:
    if not ts_ms:
        return "??:??:??"
    import datetime as _dt
    try:
        return _dt.datetime.utcfromtimestamp(int(ts_ms) / 1000.0).strftime("%H:%M:%S")
    except (OSError, ValueError, OverflowError):
        return "??:??:??"


def _recent_events_block(alias: Optional[str]) -> str:
    """Last 3 trigger_events from the bus DB. Quiet when bus disabled / DB missing."""
    rows = feature_bus.recent_events("trigger_events", limit=3, alias=alias)
    lines = ["[RECENT_EVENTS]"]
    if not rows:
        lines.append("  (no recent events)")
    else:
        for r in rows:
            lines.append(
                f"  {_format_ts(r.get('ts_ms'))}  {r.get('alias') or 'n/a'}  "
                f"{r.get('kind') or '?'} {r.get('label') or ''} "
                f"({r.get('severity') or '?'})"
            )
    return "\n".join(lines)


def _position_block(snap: Dict[str, Any]) -> str:
    pos = snap.get("position") or {}
    size = pos.get("position") if pos.get("position") is not None else pos.get("size")
    entry = pos.get("entryPrice") if pos.get("entryPrice") is not None else pos.get("entry")
    pnl  = pos.get("pnl")
    if not size:
        return "[POSITION]\n  position: FLAT"
    return "\n".join([
        "[POSITION]",
        f"  size  : {size}",
        f"  entry : {entry if entry is not None else 'n/a'}",
        f"  pnl   : {pnl if pnl is not None else 'n/a'}",
    ])


def _session_memory_block(alias: Optional[str],
                            before_ts_ms: Optional[int] = None) -> str:
    """Last 3 ai_turns from the bus DB. Quiet when bus disabled / DB missing.
    Phase 3A does NOT join to trade_outcomes (Phase 4A will populate it).

    before_ts_ms (Phase 4A replay correctness): when provided, only includes
    rows with ts_ms < before_ts_ms - matches capture-time semantics where the
    current turn did not yet exist in the DB."""
    rows = feature_bus.recent_ai_turns(limit=3, alias=alias,
                                         before_ts_ms=before_ts_ms)
    lines = ["[SESSION_MEMORY]"]
    if not rows:
        lines.append("  (no prior turns today)")
    else:
        for r in rows:
            cost = r.get("total_cost_usd")
            cost_str = f"${cost:.4f}" if isinstance(cost, (int, float)) else "n/a"
            lines.append(
                f"  {_format_ts(r.get('ts_ms'))}  "
                f"\"{(r.get('user_text_raw') or '')[:32]}\"  "
                f"model={r.get('model') or 'n/a'}  cost={cost_str}"
            )
    return "\n".join(lines)


def _user_block(user_text: str) -> str:
    return "[USER]\n  " + (user_text or "").strip()


def render_user_message(snap: Dict[str, Any], user_text: str,
                          router_hint: str = "",
                          alias: Optional[str] = None,
                          ts_ms: Optional[int] = None,
                          session_memory_before_ts_ms: Optional[int] = None) -> str:
    """Build the deterministic bus-backed user message that COULD be sent to
    Claude. router_hint is a REQUIRED-by-contract string (defaults to empty
    only for testing convenience); chat.py callers pass meta['router_hint'].

    session_memory_before_ts_ms (Phase 4A replay correctness): when provided,
    the SESSION_MEMORY block only includes ai_turns rows with ts_ms < this
    value. Capture-time chat.py leaves this None (the current turn isn't in
    the DB yet); pax_bus_replay passes the rebuilt turn's own ts_ms so the
    rebuilt SESSION_MEMORY matches the capture-time SESSION_MEMORY."""
    alias = alias or (snap.get("alias") if isinstance(snap, dict) else None)
    parts: List[str] = []
    if router_hint:
        parts.append(router_hint)
    parts.append(_state_block(snap or {}))
    parts.append(_anchor_block(snap or {}))
    parts.append(_gates_block(snap or {}))
    parts.append(_levels_block(snap or {}))
    parts.append(_microstructure_block(snap or {}))
    parts.append(_recent_events_block(alias))
    parts.append(_position_block(snap or {}))
    parts.append(_session_memory_block(alias, before_ts_ms=session_memory_before_ts_ms))
    parts.append(_user_block(user_text or ""))
    return "\n\n".join(parts)
