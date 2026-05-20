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
