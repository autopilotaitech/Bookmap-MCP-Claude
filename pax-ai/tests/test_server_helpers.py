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
