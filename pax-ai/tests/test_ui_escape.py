"""Audit fix 7: regression test that dynamic untrusted snapshot fields are
escaped before they hit innerHTML in pax_ai/static/index.html.

Pure static check -- no browser, no JS runtime. We grep the HTML/JS
source for known unsafe patterns and assert they have been removed.
The list of "known snapshot fields rendered into innerHTML" is the
audit attack surface.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


INDEX_HTML = Path(__file__).parent.parent / "pax_ai" / "static" / "index.html"


@pytest.fixture(scope="module")
def html_body() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def test_esc_helper_is_defined(html_body):
    assert "function _esc(" in html_body, "the HTML-escape helper must exist"


# ---------------------------------------------------------------------------
# Negative assertions: each of the snapshot fields below MUST NOT appear
# in an unescaped innerHTML concatenation. The patterns below are the
# exact unsafe shapes that existed before the audit fix.
# ---------------------------------------------------------------------------

UNSAFE_PATTERNS = [
    # pollContext: bare nearest.label / decision concatenated into innerHTML
    r"'</b>'\s*\+\s*s\.nearest\.label",
    r"'<b>'\s*\+\s*s\.nearest\.label",
    r"' to <b>'\s*\+\s*s\.nearest\.label",
    # pollContext: bare anchor in cEl.innerHTML assignment
    r"' &middot; '\s*\+\s*anchor",
    # pollPlaybook: raw b.name / b.if / b.then in concatenation
    r"'<div class=\"nm\">'\s*\+\s*b\.name",
    r"'<div class=\"iff\">if: '\s*\+\s*b\.if",
    r"'<div class=\"ths\">then: '\s*\+\s*b\.then",
    # pollPlaybook: raw current_state / active_level
    r'class="state">\'\s*\+\s*\(j\.current_state',
    r'<b>\'\s*\+\s*j\.active_level\s*\+\s*\'</b>',
    # pollLevel: row(k, v) without _esc, and unescaped reasons items
    r"row\s*=\s*\(k,\s*v\)\s*=>\s*'<div class=\"k\">'\s*\+\s*k\s*\+",
    r"e\.reasons\.map\(x\s*=>\s*'<li>'\s*\+\s*x\s*\+\s*'</li>'",
]


@pytest.mark.parametrize("pat", UNSAFE_PATTERNS)
def test_no_unsafe_innerhtml_pattern(html_body, pat):
    assert re.search(pat, html_body) is None, (
        f"unsafe innerHTML pattern still present: {pat!r}")


# ---------------------------------------------------------------------------
# Positive assertions: known dynamic fields are routed through _esc.
# ---------------------------------------------------------------------------

EXPECTED_ESCAPED_CALLS = [
    r"_esc\(s\.nearest\.label\)",
    r"_esc\(anchor\)",
    r"_esc\(b\.name\)",
    r"_esc\(b\.if\)",
    r"_esc\(b\.then\)",
    r"_esc\(j\.current_state",
    r"_esc\(j\.active_level\)",
    r"_esc\(t\.kind\)",
    r"_esc\(t\.label\)",
    r"_esc\(t\.headline\)",
    r"_esc\(t\.details\)",
    # row()'s v argument escaped
    r"_esc\(v\)",
    # reasons list items escaped
    r"_esc\(x\)",
]


@pytest.mark.parametrize("pat", EXPECTED_ESCAPED_CALLS)
def test_dynamic_field_routed_through_esc(html_body, pat):
    assert re.search(pat, html_body) is not None, (
        f"expected dynamic field to be escaped via {pat!r}")
