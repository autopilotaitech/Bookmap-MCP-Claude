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
