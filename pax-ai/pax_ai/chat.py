"""SSE chat handler bridging /api/pax/chat/stream to Claude CLI streaming.

The HTTP handler in server.py delegates the long-running, byte-streaming
work to handle_chat_stream() here. We own:
  * digest building (quantized snapshot summary)
  * ROUTER hint prepending
  * SSE event framing (event: token / event: done / event: error)
  * abort wiring (a per-process abort Event)

Per-process abort, not per-chat-id, is the MVP shape: the floating window
only ever runs one outstanding chat. POST /api/pax/chat/abort sets the
flag for whichever chat is currently in flight.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import claude_stream, poller, prompts, voice, config, journal


# Single global abort flag for the most-recent chat. Replaced on every
# new chat so an /abort call after a chat has finished is a no-op.
_ABORT_LOCK = threading.Lock()
_CURRENT_ABORT: Optional[threading.Event] = None


def request_abort() -> bool:
    """Set the abort flag for the chat currently in flight (if any).

    Returns True if a chat was actually in flight, False otherwise.
    """
    with _ABORT_LOCK:
        evt = _CURRENT_ABORT
    if evt is None:
        return False
    evt.set()
    return True


def _digest_lines(snap: Dict[str, Any]) -> list:
    """Build the quantized snapshot digest block (one stat per line)."""
    if snap is None or snap.get("health") != "ok":
        return ["SNAPSHOT DIGEST",
                "  health        : OFFLINE",
                "  bridgeError   : " + str(snap.get("bridgeError") if snap else "no snapshot")]

    book = snap.get("book") or {}
    or_levels = snap.get("or_levels") or {}
    conv = snap.get("conviction") or {}
    flow = snap.get("flow") or {}
    vwap = snap.get("vwap_bias") or {}
    vp = snap.get("vp_bias") or {}
    gates = snap.get("gates") or {}
    session = gates.get("session") or snap.get("session") or {}
    news = gates.get("news") or {}
    pax = snap.get("pax") or {}

    # Nearest level summary
    levels = or_levels.get("levels") or []
    nearest = None
    if levels:
        with_d = [L for L in levels if isinstance(L, dict) and L.get("distance") is not None]
        if with_d:
            nearest = min(with_d, key=lambda L: abs(L.get("distance") or 1e9))
    nearest_str = "--"
    if nearest is not None:
        d = nearest.get("distance")
        d_str = (f"{d:+.2f}p" if isinstance(d, (int, float)) else "--")
        nearest_str = (f"{nearest.get('label','?')} ({d_str}, "
                        f"{nearest.get('decision','?')}, "
                        f"conf {nearest.get('confidence',0):.2f})")

    # Recent micro_events (deduped types in the last 5 events)
    me = snap.get("micro_events") or {}
    me_events = me.get("events") or []
    me_types = []
    seen = set()
    for ev in reversed(me_events[-10:]):
        t = ev.get("type") if isinstance(ev, dict) else None
        if t and t not in seen:
            me_types.append(t); seen.add(t)
        if len(me_types) >= 5:
            break

    return [
        "SNAPSHOT DIGEST",
        f"  alias         : {snap.get('alias') or '--'}",
        f"  mid           : {book.get('mid')}",
        f"  spread        : {book.get('spread')}",
        f"  nearest       : {nearest_str}",
        f"  middleLock    : {or_levels.get('middleLock')}",
        f"  inProximity   : {or_levels.get('inProximity')}",
        f"  conviction    : score={conv.get('score')} trend={conv.get('trend')} anchorMode={conv.get('anchorMode')}",
        f"  trend_signal  : kind={(snap.get('trend_signal') or {}).get('kind')} "
                f"eligible={(snap.get('trend_signal') or {}).get('eligible')}",
        f"  flow          : regime={flow.get('regime')} "
                f"regimeConfidence={flow.get('regimeConfidence')} "
                f"biasScore={flow.get('biasScore')} "
                f"biasTrajectory={flow.get('biasTrajectory')}",
        f"  vwap_bias     : {vwap.get('label')} (sigma_z={(vwap.get('components') or {}).get('sigma_z')})",
        f"  vp_bias       : {vp.get('label')} "
                f"(va_state={(vp.get('components') or {}).get('va_state')})",
        f"  micro_events  : " + (", ".join(me_types) if me_types else "(none recent)"),
        f"  session       : code={session.get('code')} anchorMode={session.get('anchorMode')}",
        f"  news          : blocked={news.get('blocked')} label={news.get('label')}",
        f"  pax           : decision={pax.get('decision')} size_tier={pax.get('size_tier')}",
    ]


def build_user_message(user_text: str) -> Tuple[str, Dict[str, Any]]:
    """Build the message that goes to `claude -p`.

    Returns (full_message, meta) where meta exposes routing + digest stats
    for the SSE 'done' payload.
    """
    user_norm = voice.normalize(user_text)
    routed = prompts.route(user_norm)
    snap, _as_of_ms, age_ms, _fails, _err = poller.latest()
    stale_ms = int(config.get("stale_snapshot_ms", 5000))
    stale = (snap is None) or (age_ms > stale_ms)

    digest = "\n".join(_digest_lines(snap))
    if stale:
        digest += f"\n  STALE         : {age_ms} ms (threshold {stale_ms})"

    full = (
        routed["router_hint"]
        + "\n\n"
        + digest
        + "\n\nUSER:\n  "
        + user_norm
    )
    meta = {
        "router_primary":   routed["primary"],
        "router_secondary": routed["secondary"],
        "snapshot_stale":   stale,
        "snapshot_age_ms":  age_ms,
        "user_normalized":  user_norm,
    }
    return full, meta


def _sse_event(name: str, data: Dict[str, Any]) -> bytes:
    """Format a single SSE event ready to write to wfile."""
    payload = json.dumps(data, default=str, ensure_ascii=False)
    return f"event: {name}\ndata: {payload}\n\n".encode("utf-8")


def _clear_abort_if_owned(abort: threading.Event) -> None:
    """Clear _CURRENT_ABORT only if it still points at our event.

    Idempotent and tolerant of concurrent handlers: if a newer chat has
    already replaced _CURRENT_ABORT with its own event, we leave that
    one alone.
    """
    global _CURRENT_ABORT
    with _ABORT_LOCK:
        if _CURRENT_ABORT is abort:
            _CURRENT_ABORT = None


def handle_chat_stream(wfile, user_text: str) -> None:
    """SSE handler. Called by server.py after sending the status + headers.

    Writes a sequence of SSE events to wfile and flushes after each.
    Closes the chat after the subprocess exits or aborts.

    The _CURRENT_ABORT flag is set on entry and cleared on EVERY exit
    path via try/finally so that a follow-up POST /api/pax/chat/abort
    cannot bind to a stale event from a previously failed chat. Previously
    early-return paths (prompt-write failure, BrokenPipe before the
    start event) leaked _CURRENT_ABORT.
    """
    global _CURRENT_ABORT
    abort = threading.Event()
    with _ABORT_LOCK:
        _CURRENT_ABORT = abort

    try:
        full_msg, meta = build_user_message(user_text)

        # Journal the user turn immediately. The normalized text (voice ->
        # canonical jargon) is what we persist, not the raw transcript -- it
        # matches what Claude sees in the digest.
        journal.record("YOU", meta["user_normalized"], meta={
            "router_primary":  meta["router_primary"],
            "snapshot_stale":  meta["snapshot_stale"],
            "snapshot_age_ms": meta["snapshot_age_ms"],
        })

        # System prompt path - rendered at boot, cached on disk
        try:
            sp_path = prompts.write_frozen_prompt()
        except Exception as exc:
            try:
                wfile.write(_sse_event("error", {"error": f"prompts.write failed: {exc}"}))
                wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        model = config.get("models.live", "claude-haiku-4-5")
        pax_collected: List[str] = []

        # Tell the UI the chat is starting + which skill is leading.
        try:
            wfile.write(_sse_event("start", {
                "model":            model,
                "router_primary":   meta["router_primary"],
                "router_secondary": meta["router_secondary"],
                "snapshot_stale":   meta["snapshot_stale"],
                "snapshot_age_ms":  meta["snapshot_age_ms"],
            }))
            wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

        def on_token(text: str) -> None:
            pax_collected.append(text)
            try:
                wfile.write(_sse_event("token", {"text": text}))
                wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                abort.set()

        final_info: Dict[str, Any] = {}
        def on_done(info: Dict[str, Any]) -> None:
            final_info.update(info)

        t0 = time.monotonic()
        rc = claude_stream.stream_chat(
            user_message=full_msg,
            model=model,
            system_prompt_path=sp_path,
            on_token=on_token,
            on_done=on_done,
            abort=abort,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        # Journal the assistant turn (even on partial / aborted / errored
        # runs so the audit trail is complete).
        pax_text = "".join(pax_collected)
        journal.record("PAX", pax_text, meta={
            "model":          model,
            "exit_code":      rc,
            "elapsed_ms":     elapsed_ms,
            "tokens_emitted": final_info.get("tokens_emitted", 0),
            "aborted":        final_info.get("aborted", abort.is_set()),
            "error":          final_info.get("error"),
            "router_primary": meta["router_primary"],
        })

        try:
            wfile.write(_sse_event("done", {
                "exit_code":      rc,
                "elapsed_ms":     elapsed_ms,
                "tokens_emitted": final_info.get("tokens_emitted", 0),
                "aborted":        final_info.get("aborted", abort.is_set()),
                "error":          final_info.get("error"),
                "stderr_tail":    final_info.get("stderr_tail"),
                "model":          model,
                "router_primary": meta["router_primary"],
            }))
            wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
    finally:
        _clear_abort_if_owned(abort)
