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

import hashlib
import json
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import claude_stream, feature_bus, poller, prompts, voice, config, journal


# Single global abort flag for the most-recent chat. Replaced on every
# new chat so an /abort call after a chat has finished is a no-op.
_ABORT_LOCK = threading.Lock()
_CURRENT_ABORT: Optional[threading.Event] = None


def _select_model(deep: bool) -> str:
    """Resolve the Claude model id from config.

    Priority:
      * deep=False           -> models.live  (default: claude-haiku-4-5)
      * deep=True,
        models.opus_opt_in   -> models.deep_when_opus (default: claude-opus-4-7)
      * deep=True (default)  -> models.deep  (default: claude-sonnet-4-6)

    `--tools ""` + `--max-turns 1` invariants apply in ALL paths (set in
    claude_stream._build_argv). /deep is a model swap, not an
    agentic-loop unlock.
    """
    if not deep:
        return config.get("models.live", "claude-haiku-4-5") or "claude-haiku-4-5"
    if config.get("models.opus_opt_in", False):
        return (config.get("models.deep_when_opus", "claude-opus-4-7")
                  or "claude-opus-4-7")
    return config.get("models.deep", "claude-sonnet-4-6") or "claude-sonnet-4-6"


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
        "router_hint":      routed["router_hint"],   # NEW (Batch B)
        # Feature-bus byte-exact replay: the post-done capture path must
        # hash the SAME snapshot the digest was built from, not a fresh
        # poll. Underscore-prefixed: never exposed in SSE payloads.
        "_snapshot_for_capture":    snap,
        "_snapshot_ts_ms_capture":  _as_of_ms,
        "_snapshot_age_ms_capture": age_ms,
    }
    return full, meta


def _sse_event(name: str, data: Dict[str, Any]) -> bytes:
    """Format a single SSE event ready to write to wfile."""
    payload = json.dumps(data, default=str, ensure_ascii=False)
    return f"event: {name}\ndata: {payload}\n\n".encode("utf-8")


def _capture_ai_chart_signal(pax_text: Optional[str],
                              snap: Optional[Dict[str, Any]]) -> None:
    """Best-effort: extract a structured chart-signal block from the Pax
    AI response, validate against the snapshot the chat was grounded on
    (NOT a fresh poll, to avoid a race against the 1Hz poller), and
    atomically append to the cross-process JSONL store.

    Failure paths are silent. Chart plumbing must never break the chat.
    """
    try:
        from . import ai_chart_signal, ai_chart_signal_store
        blk = ai_chart_signal.extract_block(pax_text or "")
        if blk is None:
            return
        validated = ai_chart_signal.validate_against_snapshot(blk, snap or {})
        if validated is None:
            return
        ai_chart_signal_store.append_signal(validated)
    except Exception as exc:
        sys.stderr.write(f"[chat] ai_chart_signal capture failed: {exc}\n")


def _capture_pax_forecast(pax_text: Optional[str],
                           snap: Optional[Dict[str, Any]],
                           *,
                           ts_ms: int,
                           chat_run_id: Optional[str],
                           digest_sha256: Optional[str],
                           snapshot_sha256: Optional[str]) -> None:
    """Best-effort: extract a <<PAX_FORECAST>>...<<END_FORECAST>> block
    from the Pax AI response, validate against the SAME snapshot the
    digest was built from, and persist via the forecast store writer.

    All failure paths are silent. The forecast pipeline must never break
    the chat path. Capture only runs when ``forecast.enabled`` is True.

    source_turn_id is intentionally NOT set here -- the feature_bus
    writer thread assigns ai_turns.id asynchronously, so at chat-capture
    time we do not yet know it. Linkage metadata (chat_run_id +
    digest_sha256 + snapshot_sha256) is the fallback join key the
    calibration / replay paths use.
    """
    try:
        from . import forecast_signal, forecast_store_writer
        if not forecast_store_writer.is_enabled():
            return
        blk = forecast_signal.extract_block(pax_text or "")
        if blk is None:
            return
        validated = forecast_signal.validate_against_snapshot(
            blk, snap or {}, ts_ms=ts_ms)
        if validated is None:
            return
        forecast_store_writer.persist_validated(
            validated,
            chat_run_id=chat_run_id,
            digest_sha256=digest_sha256,
            snapshot_sha256=snapshot_sha256,
        )
    except Exception as exc:
        sys.stderr.write(f"[chat] pax_forecast capture failed: {exc}\n")


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


def handle_chat_stream(wfile, user_text: str, deep: bool = False) -> None:
    """SSE handler. Called by server.py after sending the status + headers.

    Writes a sequence of SSE events to wfile and flushes after each.
    Closes the chat after the subprocess exits or aborts.

    Args:
      wfile     - response writer to stream SSE events into.
      user_text - the user message (already had any /deep prefix stripped
                  client-side; this function does NOT re-interpret slash
                  commands).
      deep      - when True, escalate model selection via _select_model
                  and use a longer per-call timeout. --tools "" and
                  --max-turns 1 stay enforced.

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

        # Phase 3A shadow bus digest: gated on feature_bus.enabled so the
        # disabled path stays bit-identical to Phase 1+2 (no shadow render,
        # no [bus-digest] stderr log, no behavior change). Live input swap
        # requires BOTH gates: feature_bus.enabled=true AND
        # chat.use_feature_bus_digest=true.
        bus_full_msg = None
        if config.get("feature_bus.enabled", False):
            try:
                from . import bus_digest as _bus_dig
                snap_for_bus = meta.get("_snapshot_for_capture") or {}
                bus_full_msg = _bus_dig.render_user_message(
                    snap=snap_for_bus,
                    user_text=user_text,
                    router_hint=meta.get("router_hint") or "",
                    alias=(snap_for_bus or {}).get("alias"),
                    ts_ms=meta.get("_snapshot_ts_ms_capture"),
                )
                live_sha = hashlib.sha256(full_msg.encode("utf-8")).hexdigest()
                bus_sha  = hashlib.sha256(bus_full_msg.encode("utf-8")).hexdigest()
                diff_bytes = abs(len(bus_full_msg) - len(full_msg))
                sys.stderr.write(
                    f"[bus-digest] live_sha={live_sha[:12]} "
                    f"bus_sha={bus_sha[:12]} diff_bytes={diff_bytes}\n")
            except Exception as exc:
                sys.stderr.write(f"[bus-digest] shadow render failed: {exc}\n")

        if (config.get("feature_bus.enabled", False)
                and config.get("chat.use_feature_bus_digest", False)
                and bus_full_msg is not None):
            full_msg = bus_full_msg

        # Journal the user turn immediately. The normalized text (voice ->
        # canonical jargon) is what we persist, not the raw transcript -- it
        # matches what Claude sees in the digest.
        journal.record("YOU", meta["user_normalized"], meta={
            "router_primary":  meta["router_primary"],
            "snapshot_stale":  meta["snapshot_stale"],
            "snapshot_age_ms": meta["snapshot_age_ms"],
            "deep":            deep,
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

        model = _select_model(deep)
        chat_timeout = (claude_stream.DEEP_CHAT_TIMEOUT_SEC if deep
                          else claude_stream.CHAT_TIMEOUT_SEC)
        pax_collected: List[str] = []

        # Tell the UI the chat is starting + which skill is leading.
        try:
            wfile.write(_sse_event("start", {
                "model":            model,
                "deep":             deep,
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
            timeout_sec=chat_timeout,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        # Journal the assistant turn (even on partial / aborted / errored
        # runs so the audit trail is complete). Cost/usage fields are
        # captured when the CLI's `result` event provides them; absent
        # otherwise (subscription auth may omit on some versions).
        pax_text = "".join(pax_collected)
        journal.record("PAX", pax_text, meta={
            "model":          model,
            "deep":           deep,
            "exit_code":      rc,
            "elapsed_ms":     elapsed_ms,
            "tokens_emitted": final_info.get("tokens_emitted", 0),
            "aborted":        final_info.get("aborted", abort.is_set()),
            "error":          final_info.get("error"),
            "router_primary": meta["router_primary"],
            # Optional cost/usage -- pass through whatever the CLI gave us.
            "total_cost_usd":              final_info.get("total_cost_usd"),
            "input_tokens":                final_info.get("input_tokens"),
            "output_tokens":               final_info.get("output_tokens"),
            "cache_creation_input_tokens": final_info.get("cache_creation_input_tokens"),
            "cache_read_input_tokens":     final_info.get("cache_read_input_tokens"),
            "duration_api_ms":             final_info.get("duration_api_ms"),
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
                "deep":           deep,
                "router_primary": meta["router_primary"],
                # Optional cost/usage from the CLI's final `result` event.
                # The client must treat each field as optional -- some
                # auth/version combinations omit total_cost_usd entirely.
                "total_cost_usd":              final_info.get("total_cost_usd"),
                "input_tokens":                final_info.get("input_tokens"),
                "output_tokens":               final_info.get("output_tokens"),
                "cache_creation_input_tokens": final_info.get("cache_creation_input_tokens"),
                "cache_read_input_tokens":     final_info.get("cache_read_input_tokens"),
                "duration_api_ms":             final_info.get("duration_api_ms"),
            }))
            wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

        # ------------------------------------------------------------------
        # Phase 1 feature-bus capture. Strictly post-`done`-flush. Any
        # failure logs to stderr but never raises into the chat path.
        # The captured snapshot MUST be the one build_user_message used
        # (carried in meta["_snapshot_for_capture"]); polling again here
        # would race against the 1 Hz poller and break replay byte-exactness.
        # ------------------------------------------------------------------
        try:
            snap = meta.get("_snapshot_for_capture")
            snap_ts_ms = int(meta.get("_snapshot_ts_ms_capture") or 0)
            snap_age_ms = int(meta.get("_snapshot_age_ms_capture") or 0)
            snapshot_json = feature_bus._canonical_snapshot_json(snap or {})
            digest_sha = hashlib.sha256(full_msg.encode("utf-8")).hexdigest()
            snap_sha   = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
            rec = feature_bus.AiTurnRecord(
                schema_version=1,
                ts_ms=int(time.time() * 1000),
                chat_run_id=journal.current_run_id(),
                deep=bool(deep),
                model=model,
                router_primary=meta.get("router_primary"),
                router_secondary=meta.get("router_secondary"),
                user_text_raw=user_text,
                user_text_normalized=meta.get("user_normalized") or user_text,
                digest_text=full_msg,
                digest_sha256=digest_sha,
                snapshot_json=snapshot_json,
                snapshot_sha256=snap_sha,
                snapshot_alias=(snap or {}).get("alias"),
                snapshot_ts_ms=snap_ts_ms if snap_ts_ms > 0 else None,
                snapshot_age_ms=snap_age_ms,
                pax_text=pax_text,
                exit_code=rc,
                elapsed_ms=elapsed_ms,
                api_duration_ms=final_info.get("duration_api_ms"),
                total_cost_usd=final_info.get("total_cost_usd"),
                input_tokens=final_info.get("input_tokens"),
                output_tokens=final_info.get("output_tokens"),
                cache_creation_tokens=final_info.get("cache_creation_input_tokens"),
                cache_read_tokens=final_info.get("cache_read_input_tokens"),
                aborted=bool(final_info.get("aborted", abort.is_set())),
                error=final_info.get("error"),
            )
            feature_bus.record_ai_turn(rec)
        except Exception as exc:
            sys.stderr.write(f"[chat] feature_bus capture failed: {exc}\n")

        # Pax AI -> chart marker bridge. Validates against the snapshot
        # the digest was built from (NOT a fresh poll). Any failure is
        # silent — chart plumbing is not allowed to break the chat path.
        _capture_ai_chart_signal(pax_text, meta.get("_snapshot_for_capture"))

        # Pax AI -> structured forecast capture for the self-training
        # research loop. Same snapshot as the chart-signal capture. Same
        # silent-failure contract; chat path must never break.
        try:
            _digest_sha = digest_sha if "digest_sha" in locals() else None
            _snap_sha = snap_sha if "snap_sha" in locals() else None
        except Exception:
            _digest_sha = None
            _snap_sha = None
        _capture_pax_forecast(
            pax_text,
            meta.get("_snapshot_for_capture"),
            ts_ms=int(time.time() * 1000),
            chat_run_id=journal.current_run_id(),
            digest_sha256=_digest_sha,
            snapshot_sha256=_snap_sha,
        )
    finally:
        _clear_abort_if_owned(abort)
