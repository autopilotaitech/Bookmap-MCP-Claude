"""Phase 1+2 HTTP server.

Endpoints:
  GET /                       static index (Pax AI UI)
  GET /api/snapshot           raw dashboard proxy (kept for back-compat + debug)
  GET /api/pax/context        quantized strip context
  GET /api/pax/whynow         (placeholder - Phase 4 trigger engine wires up)
  GET /api/pax/level/<label>  per-level Jane-Street edge calculus
  GET /api/pax/playbook       scenario tree
  GET /api/pax/skills         skill registry (placeholder)
  GET /api/pax/health         process health

All reads pull from poller._LATEST_SNAPSHOT - no direct dashboard call here.
That keeps HTTP handlers fast (no I/O on a worker thread) and decouples the
server from dashboard hiccups.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import traceback
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Tuple
from urllib.parse import urlparse

from . import DASHBOARD_URL, DEFAULT_PORT
from . import poller, context as ctx_mod, edge_calculus, playbook, config
from . import chat as chat_mod, claude_stream, triggers, journal


_STATIC_DIR = Path(__file__).parent / "static"
_DASHBOARD_TIMEOUT_S = 2.0


# ---------------------------------------------------------------------------
# Request-parameter parsing helpers
# ---------------------------------------------------------------------------

HISTORY_LIMIT_DEFAULT = 50
HISTORY_LIMIT_MIN = 1
HISTORY_LIMIT_MAX = 500


def _clamp_history_limit(raw_value: Any) -> int:
    """Coerce an arbitrary query-string value into a safe history limit.

    Semantics: missing / empty / non-integer -> default 50.
    Out-of-range integers are clamped to [1, 500] rather than rejected --
    history is read-only and a clamp gives a useful response where a 400
    would just confuse callers. Matches journal.recent's own clamp.
    """
    if raw_value is None or raw_value == "":
        return HISTORY_LIMIT_DEFAULT
    try:
        n = int(raw_value)
    except (TypeError, ValueError):
        return HISTORY_LIMIT_DEFAULT
    if n < HISTORY_LIMIT_MIN:
        return HISTORY_LIMIT_MIN
    if n > HISTORY_LIMIT_MAX:
        return HISTORY_LIMIT_MAX
    return n


# ---------------------------------------------------------------------------
# Legacy proxy (still works; used by /api/snapshot for debugging)
# ---------------------------------------------------------------------------

def _fetch_dashboard_snapshot() -> Tuple[int, Dict[str, Any]]:
    """Direct fetch (no poller). Kept for the /api/snapshot debug endpoint."""
    try:
        req = urllib.request.Request(DASHBOARD_URL,
                                       headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=_DASHBOARD_TIMEOUT_S) as resp:
            raw = resp.read()
            try:
                body = json.loads(raw.decode("utf-8"))
                return 200, body
            except json.JSONDecodeError as exc:
                return 502, _offline_envelope(f"dashboard returned non-JSON: {exc}")
    except urllib.error.URLError as exc:
        return 502, _offline_envelope(str(getattr(exc, "reason", exc)))
    except Exception as exc:
        return 500, _offline_envelope(f"{type(exc).__name__}: {exc}")


def _offline_envelope(err: str) -> Dict[str, Any]:
    return {
        "health":          "offline",
        "bridgeUrl":       DASHBOARD_URL,
        "bridgeError":     err,
        "tokenConfigured": False,
        "nextSteps": [
            "Confirm the dashboard is running on 127.0.0.1:18888.",
            "Run: .\\dashboard-start.ps1",
        ],
        "error": err,
    }


# ---------------------------------------------------------------------------
# Pax AI endpoints
# ---------------------------------------------------------------------------

def _api_pax_context() -> Tuple[int, Dict[str, Any]]:
    snap, as_of_ms, age_ms, fails, err = poller.latest()
    if snap is None:
        # Poller never had a successful fetch
        body = ctx_mod.build_context(
            _offline_envelope(err or "no successful snapshot yet"),
            as_of_ms=int(time.time() * 1000),
            age_ms=0,
            stale_threshold_ms=int(config.get("stale_snapshot_ms", 5000)),
        )
        body["consecutiveFails"] = fails
        body["lastError"] = err
        return 503, body
    body = ctx_mod.build_context(
        snap, as_of_ms=as_of_ms, age_ms=age_ms,
        stale_threshold_ms=int(config.get("stale_snapshot_ms", 5000)),
    )
    body["consecutiveFails"] = fails
    body["lastError"] = err
    return 200, body


def _api_pax_level(label: str) -> Tuple[int, Dict[str, Any]]:
    snap, as_of_ms, age_ms, _fails, err = poller.latest()
    if snap is None:
        return 503, {"error": "no snapshot", "lastError": err}
    or_levels = snap.get("or_levels") or {}
    levels = or_levels.get("levels") or []
    found = None
    for L in levels:
        if isinstance(L, dict) and str(L.get("label", "")).upper() == label.upper():
            found = L; break
    if found is None:
        return 404, {"error": f"level not found: {label}",
                      "available": [L.get("label") for L in levels if isinstance(L, dict)]}
    edge = edge_calculus.level_edge(found, snap)
    return 200, {
        "label":           found.get("label"),
        "price":           found.get("price"),
        "side":            found.get("side"),
        "distance":        found.get("distance"),
        "proximity":       found.get("proximity"),
        "decision":        found.get("decision"),
        "confidence":      found.get("confidence"),
        "composite_score": found.get("composite_score"),
        "composite_dir":   found.get("composite_dir"),
        "edge_calculus":   edge,
        "asOfMs":          as_of_ms,
        "ageMs":           age_ms,
        "stale":           age_ms > int(config.get("stale_snapshot_ms", 5000)),
        "anchorMode":      ((snap.get("session") or {}).get("anchorMode")
                              or (snap.get("conviction") or {}).get("anchorMode")),
    }


def _api_pax_playbook() -> Tuple[int, Dict[str, Any]]:
    snap, as_of_ms, age_ms, _fails, _err = poller.latest()
    if snap is None:
        return 503, {"error": "no snapshot"}
    pb = playbook.build_playbook(snap)
    pb["asOfMs"] = as_of_ms
    pb["ageMs"] = age_ms
    pb["stale"] = age_ms > int(config.get("stale_snapshot_ms", 5000))
    return 200, pb


def _api_pax_whynow() -> Tuple[int, Dict[str, Any]]:
    snap, as_of_ms, age_ms, _fails, _err = poller.latest()
    trigs = triggers.compute_triggers(snap, age_ms if snap is not None else 10**9)
    return 200, {
        "triggers": trigs,
        "asOfMs":   as_of_ms,
        "ageMs":    age_ms,
        "stale":    age_ms > int(config.get("stale_snapshot_ms", 5000)) if snap else True,
    }


def _api_pax_health() -> Tuple[int, Dict[str, Any]]:
    snap, as_of_ms, age_ms, fails, err = poller.latest()
    return 200, {
        "dashboardReachable":   snap is not None and snap.get("health") == "ok",
        "dashboardLastAtMs":    as_of_ms,
        "dashboardAgeMs":       age_ms,
        "dashboardConsecutiveFails": fails,
        "dashboardLastError":   err,
        "pollMs":               int(config.get("poll_ms", 1000)),
        "modelLive":            config.get("models.live"),
        "modelDeep":            config.get("models.deep"),
        "claudeAvailable":      claude_stream.claude_available(),
    }


def _api_pax_skills() -> Tuple[int, Dict[str, Any]]:
    # Phase 4 wires the real router; for now we just enumerate what's on disk.
    skills_root = Path(__file__).parent.parent.parent / "skills"
    skills = []
    if skills_root.exists():
        for p in sorted(skills_root.glob("*/SKILL.md")):
            skills.append({"id": p.parent.name, "path": str(p)})
    return 200, {"skills": skills}


# ---------------------------------------------------------------------------
# Static index
# ---------------------------------------------------------------------------

def _load_index_html() -> bytes:
    try:
        return (_STATIC_DIR / "index.html").read_bytes()
    except FileNotFoundError:
        return b"<!doctype html><meta charset=utf-8><title>Pax AI</title><pre>index.html missing</pre>"


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    server_version = "PaxAI/0.0.2"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[pax_ai] " + (fmt % args) + "\n")

    def _send_json(self, status: int, body: Dict[str, Any]) -> None:
        raw = json.dumps(body, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_html(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except json.JSONDecodeError as exc:
                self._send_json(400, {"error": f"invalid JSON: {exc}"})
                return
            if path == "/api/pax/chat/stream":
                self._handle_chat_stream(payload)
                return
            if path == "/api/pax/chat/abort":
                aborted = chat_mod.request_abort()
                self._send_json(200, {"aborted": aborted})
                return
            if path == "/api/pax/chat/forget":
                target = payload.get("run_id")
                deleted = journal.forget(target if target else None)
                self._send_json(200, {
                    "deleted":    deleted,
                    "run_id":     target or journal.current_run_id(),
                    "new_run_id": journal.current_run_id(),
                })
                return
            self._send_json(404, {"error": "not found", "path": path})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            sys.stderr.write("[pax_ai] POST crash:\n" + traceback.format_exc() + "\n")
            try: self._send_json(500, {"error": "internal error"})
            except Exception: pass

    def _handle_chat_stream(self, payload: Dict[str, Any]) -> None:
        user_text = (payload.get("message") or "").strip()
        if not user_text:
            self._send_json(400, {"error": "missing 'message'"})
            return
        # Strict /deep parsing: only an honest JSON `true` escalates the
        # model. `bool(...)` would treat the string "false" (truthy) or
        # integer 1 as deep, which can silently turn a normal chat into
        # a Sonnet/Opus call. Hard identity check is the safe contract.
        deep = payload.get("deep") is True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache, no-store")
        # `close` rather than `keep-alive` so HTTP clients that block on
        # full-body read (curl, urllib.read()) unblock after the done event.
        # Browsers reading via fetch+ReadableStream don't care either way --
        # they react to events as they arrive.
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        # SSE body written by chat_mod
        try:
            chat_mod.handle_chat_stream(self.wfile, user_text, deep=deep)
        except (BrokenPipeError, ConnectionResetError):
            return
        # Tell BaseHTTPRequestHandler not to try to reuse this connection.
        self.close_connection = True

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path in ("/", "/index.html"):
                self._send_html(200, _load_index_html())
                return
            if path == "/api/snapshot":
                status, body = _fetch_dashboard_snapshot()
                self._send_json(status, body); return
            if path == "/api/pax/context":
                status, body = _api_pax_context()
                self._send_json(status, body); return
            if path == "/api/pax/whynow":
                status, body = _api_pax_whynow()
                self._send_json(status, body); return
            if path == "/api/pax/playbook":
                status, body = _api_pax_playbook()
                self._send_json(status, body); return
            if path == "/api/pax/skills":
                status, body = _api_pax_skills()
                self._send_json(status, body); return
            if path == "/api/pax/health":
                status, body = _api_pax_health()
                self._send_json(status, body); return
            if path.startswith("/api/pax/level/"):
                label = path[len("/api/pax/level/"):]
                status, body = _api_pax_level(label)
                self._send_json(status, body); return
            if path == "/api/pax/chat/history":
                from urllib.parse import parse_qs
                q = parse_qs(urlparse(self.path).query)
                limit_raw = (q.get("limit") or [None])[0]
                limit = _clamp_history_limit(limit_raw)
                scope = (q.get("scope") or ["all"])[0]   # 'all' | 'run'
                run = journal.current_run_id() if scope == "run" else None
                rows = journal.recent(limit=limit, run_id=run)
                self._send_json(200, {
                    "rows":        rows,
                    "run_id":      journal.current_run_id(),
                    "scope":       scope,
                    "limit":       limit,
                })
                return
            self._send_json(404, {"error": "not found", "path": path})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            sys.stderr.write("[pax_ai] handler crash:\n" + traceback.format_exc() + "\n")
            try: self._send_json(500, {"error": "internal error"})
            except Exception: pass


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def run(port: int = DEFAULT_PORT) -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(message)s")
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    sys.stderr.write(f"[pax_ai] listening on http://127.0.0.1:{port}\n")
    sys.stderr.write(f"[pax_ai] proxying snapshot from {DASHBOARD_URL}\n")
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
        sys.stderr.write("[pax_ai] server closed\n")
