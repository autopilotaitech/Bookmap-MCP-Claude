"""Server helper tests (audit fix 6 + future server-side utilities)."""

from __future__ import annotations

import pytest

from pax_ai import server


@pytest.mark.parametrize("raw,expected", [
    # defaults
    (None,         server.HISTORY_LIMIT_DEFAULT),
    ("",           server.HISTORY_LIMIT_DEFAULT),
    # invalid input -> default
    ("abc",        server.HISTORY_LIMIT_DEFAULT),
    ([],           server.HISTORY_LIMIT_DEFAULT),
    ({},           server.HISTORY_LIMIT_DEFAULT),
    ("1.5",        server.HISTORY_LIMIT_DEFAULT),     # int() refuses floats
    # in-range pass-through
    ("1",          1),
    ("50",         50),
    ("500",        500),
    # below min -> clamp up
    ("0",          server.HISTORY_LIMIT_MIN),
    ("-100",       server.HISTORY_LIMIT_MIN),
    # above max -> clamp down
    ("501",        server.HISTORY_LIMIT_MAX),
    ("10_000",     server.HISTORY_LIMIT_MAX),
    ("99999999",   server.HISTORY_LIMIT_MAX),
])
def test_clamp_history_limit(raw, expected):
    assert server._clamp_history_limit(raw) == expected


def test_clamp_history_limit_does_not_raise_on_garbage():
    """Audit fix 6: bad input on /api/pax/chat/history must not 500."""
    weird = ["nan", "inf", "0xff", "1e10", "true", " "]
    for w in weird:
        assert server._clamp_history_limit(w) == server.HISTORY_LIMIT_DEFAULT


# ---------------------------------------------------------------------------
# Pre-open hardening fix 1: strict /deep boolean parsing
# ---------------------------------------------------------------------------
#
# server.py inside _handle_chat_stream uses `payload.get("deep") is True`.
# Test the exact predicate (1) directly via the same expression and (2)
# via static-grep so the production line cannot regress to bool(...).

import re
from pathlib import Path


SERVER_PY = Path(__file__).parent.parent / "pax_ai" / "server.py"


def _is_strict_true(value):
    """Mirror of the production predicate."""
    return value is True


@pytest.mark.parametrize("payload,expected", [
    ({"deep": True},      True),         # the ONLY case that escalates
    ({"deep": False},     False),
    ({},                  False),         # missing -> not deep
    ({"deep": None},      False),
    ({"deep": "true"},    False),         # string "true" must NOT escalate
    ({"deep": "false"},   False),         # string "false" was the original bug
    ({"deep": 1},         False),
    ({"deep": 0},         False),
    ({"deep": "1"},       False),
    ({"deep": []},        False),
    ({"deep": {}},        False),
])
def test_strict_deep_parsing(payload, expected):
    """Pure-function mirror of the production expression. Only an
    honest JSON `true` should escalate the model."""
    assert _is_strict_true(payload.get("deep")) is expected


def test_server_uses_strict_deep_predicate_not_bool():
    """Static guard: server.py must use `is True` for /deep parsing.
    A regression to `bool(payload.get("deep"))` would treat "false"
    (truthy string) and 1 as deep and silently escalate model cost."""
    body = SERVER_PY.read_text(encoding="utf-8")
    assert re.search(r'deep\s*=\s*payload\.get\("deep"\)\s+is\s+True', body), (
        "server.py must parse the deep flag with `is True`, not bool()")
    # And the bad pattern must be gone.
    assert "deep = bool(payload.get(\"deep\"))" not in body, (
        "server.py still has the truthy bool() pattern -- regression")


# ---------------------------------------------------------------------------
# Pre-open bug fix: /api/pax/level/<label> must URL-decode the segment.
#
# The UI encodes label '+2' as '%2B2' via encodeURIComponent() (which is
# correct per RFC 3986 inside a path segment). server.py used to pass
# the raw `%2B2` straight into _api_pax_level, which compared it to the
# snapshot labels ('+2') and 404'd. Fix: unquote before lookup. These
# tests pin both the static use of unquote() and the functional
# end-to-end resolution against a synthetic snapshot.
# ---------------------------------------------------------------------------

def test_server_unquotes_level_segment_static():
    """Guard: server.py must import urllib.parse.unquote and apply it
    to the level segment before passing to _api_pax_level."""
    body = SERVER_PY.read_text(encoding="utf-8")
    assert re.search(r"from urllib\.parse import [^\n]*\bunquote\b", body), (
        "server.py must import urllib.parse.unquote")
    assert re.search(
        r"if path\.startswith\(\"/api/pax/level/\"\):[\s\S]{0,800}?"
        r"label\s*=\s*unquote\(",
        body), (
        "the level branch must call unquote() on the path segment")


def _snap_with_or_levels():
    """Minimal snapshot with the full Pax OR/extension grid; used to
    drive _api_pax_level via the poller monkeypatch."""
    return {
        "health": "ok",
        "alias":  "NQM6.CME@RITHMIC",
        "book":   {"mid": 21326.00, "spread": 0.25},
        "or_levels": {
            "orHigh": 21340.0, "orLow": 21320.0, "orWidthPts": 20.0,
            "levels": [
                {"label": "+3",   "price": 21535.0, "side": "above", "distance":  209.0, "proximity": False, "decision": "WAIT",                "confidence": 0.10},
                {"label": "+2",   "price": 21470.0, "side": "above", "distance":  144.0, "proximity": False, "decision": "ENTER_LONG_FOLLOW",   "confidence": 0.45},
                {"label": "+1",   "price": 21405.0, "side": "above", "distance":   79.0, "proximity": False, "decision": "ENTER_LONG_FOLLOW",   "confidence": 0.55},
                {"label": "OR-H", "price": 21340.0, "side": "above", "distance":   14.0, "proximity": True,  "decision": "ENTER_LONG_FOLLOW",   "confidence": 0.65},
                {"label": "OR-L", "price": 21320.0, "side": "below", "distance":   -6.0, "proximity": True,  "decision": "ENTER_SHORT_FOLLOW",  "confidence": 0.50},
                {"label": "-1",   "price": 21255.0, "side": "below", "distance":  -71.0, "proximity": False, "decision": "WAIT",                "confidence": 0.30},
                {"label": "-2",   "price": 21190.0, "side": "below", "distance": -136.0, "proximity": False, "decision": "WAIT",                "confidence": 0.20},
                {"label": "-3",   "price": 21125.0, "side": "below", "distance": -201.0, "proximity": False, "decision": "WAIT",                "confidence": 0.10},
            ],
        },
        "flow":       {"regime": "TRENDING_UP", "regimeConfidence": 0.7,
                        "biasScore": 0.30, "biasTrajectory": "RISING"},
        "session":    {"anchorMode": "LIVE", "code": "ACTIVE"},
        "conviction": {"score": 0.30, "trend": "RISING", "anchorMode": "LIVE"},
    }


@pytest.fixture
def patch_poller_to_or_grid(monkeypatch):
    """Make poller.latest() return a deterministic OR-grid snapshot."""
    snap = _snap_with_or_levels()
    monkeypatch.setattr(server.poller, "latest",
                          lambda: (snap, 1000, 50, 0, None))
    return snap


@pytest.mark.parametrize("encoded,expected_label", [
    ("%2B1",  "+1"),
    ("%2B2",  "+2"),
    ("%2B3",  "+3"),
    ("+1",    "+1"),       # raw '+' (works under unquote too)
    ("+2",    "+2"),
    ("-1",    "-1"),
    ("-2",    "-2"),
    ("-3",    "-3"),
    ("OR-H",  "OR-H"),
    ("OR-L",  "OR-L"),
    # case-insensitive match in _api_pax_level handles or-h too
    ("or-h",  "OR-H"),
])
def test_level_endpoint_resolves_encoded_label(patch_poller_to_or_grid,
                                                  encoded, expected_label):
    """End-to-end: the URL-decoded segment finds the matching snapshot
    label. Drives _api_pax_level directly with the unquote step
    applied at the handler call site."""
    from urllib.parse import unquote as _uq
    decoded = _uq(encoded)
    status, body = server._api_pax_level(decoded)
    assert status == 200, (
        f"encoded {encoded!r} (decoded {decoded!r}) must resolve to "
        f"a 200 with label {expected_label!r}, got {status} {body}")
    assert body["label"] == expected_label


def test_level_endpoint_404_includes_available_list(patch_poller_to_or_grid):
    """An unknown label must return 404 with the available list so the
    UI can render an actionable error."""
    status, body = server._api_pax_level("DOES_NOT_EXIST")
    assert status == 404
    assert "available" in body
    # All 8 canonical labels appear in the available list.
    avail = body["available"]
    for lbl in ("+3", "+2", "+1", "OR-H", "OR-L", "-1", "-2", "-3"):
        assert lbl in avail, f"available list must contain {lbl}, got {avail}"


def test_percent_encoded_plus_routes_via_unquote(patch_poller_to_or_grid):
    """Belt: simulate the exact path string the UI's
    encodeURIComponent('+2') produces -- '%2B2' -- and verify the
    handler's unquote() call resolves it. This is the regression
    that the live screenshot revealed."""
    from urllib.parse import unquote as _uq
    raw_segment = "%2B2"
    decoded = _uq(raw_segment)
    assert decoded == "+2"
    status, body = server._api_pax_level(decoded)
    assert status == 200
    assert body["label"] == "+2"


def test_api_pax_health_includes_feature_bus_block():
    from pax_ai.server import _api_pax_health
    status, body = _api_pax_health()
    assert status == 200
    assert "feature_bus" in body
    fb = body["feature_bus"]
    assert "enabled" in fb
    assert "healthy" in fb
    assert "running" in fb
    assert "queueDepth" in fb
    assert "rowsToday" in fb


# ---------------------------------------------------------------------------
# parse_qs/urlparse scoping fix.
#
# Root cause: server.py:489 had `from urllib.parse import parse_qs` INSIDE
# do_GET. Python's compiler treats every name assigned anywhere in a
# function body as a local for the whole function, so the bus routes at
# lines 470/475 saw `parse_qs` as an unbound local and raised
# UnboundLocalError on every /api/pax/bus/summary and /api/pax/bus/recent
# request. Fix: drop the local import; the module-level import on
# server.py:30 already exposes parse_qs in the function's globals.
#
# Three guards below: AST static scan (no shadowing), bytecode introspection
# (the names are globals, not locals), and live ThreadingHTTPServer fetches
# of the previously-crashing endpoints.
# ---------------------------------------------------------------------------

import ast
import http.client
import json
import threading
import time
from http.server import ThreadingHTTPServer


def test_do_GET_does_not_shadow_parse_qs_or_urlparse():
    """AST guard: a local Import / ImportFrom / Assign that introduces
    parse_qs or urlparse anywhere inside do_GET would re-create the
    bug. Catch it at parse time so a future regression fails the
    test suite before any HTTP call."""
    tree = ast.parse(SERVER_PY.read_text(encoding="utf-8"))
    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "do_GET":
            target = node
            break
    assert target is not None, "do_GET not found in server.py"

    forbidden = {"parse_qs", "urlparse"}
    for child in ast.walk(target):
        if isinstance(child, ast.ImportFrom):
            local = {n.asname or n.name for n in child.names}
            bad = local & forbidden
            assert not bad, (
                f"do_GET locally imports {bad}; this shadows the module-"
                f"level import and causes UnboundLocalError on any prior "
                f"use of the name inside the function")
        elif isinstance(child, ast.Import):
            local = {alias.asname or alias.name for alias in child.names}
            bad = local & forbidden
            assert not bad, f"do_GET locally imports {bad}"
        elif isinstance(child, ast.Assign):
            for tgt in child.targets:
                if isinstance(tgt, ast.Name) and tgt.id in forbidden:
                    raise AssertionError(
                        f"do_GET assigns to {tgt.id}; this shadows the "
                        f"module-level import")


def test_do_GET_bytecode_treats_parse_qs_and_urlparse_as_globals():
    """Bytecode guard: the names must live in co_names (globals/attr
    references) and NOT in co_varnames (function locals). This is the
    exact distinction Python uses to decide LOAD_GLOBAL vs LOAD_FAST.
    A regression that puts either name into the locals list reproduces
    the UnboundLocalError without any HTTP call."""
    do_get = server._Handler.do_GET
    code = do_get.__code__
    assert "parse_qs" not in code.co_varnames, (
        "parse_qs must not be a local of do_GET; it is imported at module "
        "scope and used as a global")
    assert "urlparse" not in code.co_varnames, (
        "urlparse must not be a local of do_GET")
    # Belt: confirm they ARE referenced as globals.
    assert "parse_qs" in code.co_names, "parse_qs is referenced in do_GET"
    assert "urlparse" in code.co_names, "urlparse is referenced in do_GET"


# -- Live-fire endpoint smokes ----------------------------------------------

def _start_handler_server():
    """Bind a ThreadingHTTPServer to an ephemeral port on 127.0.0.1 with
    the real _Handler. Returns (httpd, port). Caller is responsible for
    shutdown."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server._Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, port, t


def _get(port, path, timeout=3.0):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        raw = resp.read()
        return resp.status, raw
    finally:
        conn.close()


def test_bus_summary_routes_without_parse_qs_crash():
    """Regression: /api/pax/bus/summary must not 500. Pre-fix it raised
    UnboundLocalError inside do_GET. We don't enable feature_bus -- the
    helper still returns 200 with the disabled-summary shape."""
    httpd, port, _ = _start_handler_server()
    try:
        status, raw = _get(port, "/api/pax/bus/summary")
        assert status == 200, f"expected 200, got {status} body={raw[:200]!r}"
        body = json.loads(raw)
        # Disabled-summary shape includes these keys regardless of state.
        assert "enabled" in body
    finally:
        httpd.shutdown()


def test_bus_recent_parses_query_params_without_crash():
    """Regression: /api/pax/bus/recent?table=level_events&limit=10 must
    not 500. The query-string parse runs inside do_GET via parse_qs;
    pre-fix this raised UnboundLocalError."""
    httpd, port, _ = _start_handler_server()
    try:
        status, raw = _get(
            port, "/api/pax/bus/recent?table=level_events&limit=10")
        assert status == 200, f"expected 200, got {status} body={raw[:200]!r}"
        body = json.loads(raw)
        assert body.get("table") == "level_events"
        assert "rows" in body
    finally:
        httpd.shutdown()


def test_bus_recent_rejects_unknown_table_via_query_param():
    """The 400-path also runs through parse_qs. Confirms query-string
    parsing is intact on the rejection branch."""
    httpd, port, _ = _start_handler_server()
    try:
        status, raw = _get(port, "/api/pax/bus/recent?table=bogus")
        assert status == 400, f"expected 400, got {status} body={raw[:200]!r}"
        body = json.loads(raw)
        assert "allowed" in body
    finally:
        httpd.shutdown()


def test_health_endpoint_still_routes_via_do_GET():
    """Sanity: a healthy non-bus endpoint still routes correctly via
    the same do_GET that previously crashed on the bus paths. Pinned
    so the fix can't accidentally break adjacent routes."""
    httpd, port, _ = _start_handler_server()
    try:
        status, raw = _get(port, "/api/pax/health")
        assert status == 200, f"expected 200, got {status} body={raw[:200]!r}"
        body = json.loads(raw)
        assert "feature_bus" in body
    finally:
        httpd.shutdown()


def test_levels_edge_endpoint_still_routes_via_do_GET():
    """Sanity: the chart's primary feed must keep returning 200 after the
    fix. /api/pax/levels/edge takes no query params -- this also pins
    that the do_GET path through the non-bus branches is intact."""
    httpd, port, _ = _start_handler_server()
    try:
        status, raw = _get(port, "/api/pax/levels/edge")
        # 200 with a payload, regardless of dashboard reachability;
        # the helper returns an empty rows list when the dashboard is
        # offline rather than crashing the route.
        assert status in (200, 503), (
            f"unexpected status {status} body={raw[:200]!r}")
        body = json.loads(raw)
        assert isinstance(body, dict)
    finally:
        httpd.shutdown()
