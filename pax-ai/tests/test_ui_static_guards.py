"""Static HTML/JS string guards for the Phase 2 'Today's Bus' UI section.

These are STATIC GUARDS only. They assert that the strings + element
structure that WOULD drive UI polling and disabled-state rendering exist
in the served HTML. They do NOT execute JavaScript, do NOT render the page
in a browser, do NOT verify polling cadence in real time, and do NOT assert
visible pixels. Browser-level verification belongs to operator smoke runs."""
from __future__ import annotations

import re
from pathlib import Path


HTML_PATH = Path(__file__).resolve().parent.parent / "pax_ai" / "static" / "index.html"


def _html() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


def test_index_html_exists():
    assert HTML_PATH.exists()


def test_index_html_has_todays_bus_details_element():
    """The collapsible <details> block with id='todays-bus' must exist."""
    html = _html()
    assert re.search(r'<details\s+id="todays-bus"', html), \
        "expected <details id=\"todays-bus\"> element"


def test_index_html_todays_bus_is_collapsed_by_default():
    """The <details> tag must NOT carry the 'open' attribute - default collapsed."""
    html = _html()
    m = re.search(r'<details\s+id="todays-bus"([^>]*)>', html)
    assert m, "details element missing"
    attrs = m.group(1)
    assert "open" not in attrs.lower(), \
        "todays-bus must not have 'open' attribute (default collapsed)"


def test_index_html_uses_utc_day_label_in_summary_panel():
    """The summary panel template/markup must include the literal 'UTC day' label."""
    html = _html()
    assert "UTC day" in html, "summary panel must label the date as 'UTC day'"


def test_index_html_has_disabled_state_block():
    """When bus disabled, UI shows the #todays-bus-disabled paragraph with
    instructions to enable + restart."""
    html = _html()
    assert 'id="todays-bus-disabled"' in html
    assert "feature_bus.enabled=true" in html
    assert "restart" in html.lower(), \
        "disabled state copy should mention restarting Pax AI"


def test_index_html_references_three_bus_endpoints():
    """The static HTML/JS must contain the three Phase-2 endpoint URLs as
    string literals. (This proves WIRING intent only - actual polling
    behavior is an operator-smoke concern, not a Python-test concern.)"""
    html = _html()
    assert "/api/pax/bus/status"  in html
    assert "/api/pax/bus/recent"  in html
    assert "/api/pax/bus/summary" in html


def test_index_html_has_state_dot_element():
    """The status-dot indicator element must exist with the expected id."""
    html = _html()
    assert 'id="todays-bus-state-dot"' in html


def test_index_html_phase_1_strip_section_unchanged():
    """Regression: the existing strip / WhyNow / Edge / Playbook sections
    are not accidentally renamed or removed. (Smoke-level guard - exact
    inner content is operator concern.)"""
    html = _html()
    # Sanity: a few existing Phase 1 markers should still be present.
    # If these break, Phase 2 has touched off-limits markup.
    for marker in ('class="strip"', "Pax AI"):
        assert marker in html, f"Phase 1 marker missing: {marker}"


def test_todays_bus_script_does_not_use_innerhtml():
    """XSS hardening: the Today's Bus polling script must NOT interpolate
    DB-backed row values into innerHTML. The recent-events display uses
    textContent + CSS white-space:pre-line for newline rendering.

    This guard is scoped to the IIFE that immediately follows the
    <details id="todays-bus"> element. Other pre-existing scripts in
    index.html may still use innerHTML on trusted content (Phase 1 strip,
    edge calculus, playbook, chat) - those are out of scope for this test."""
    html = _html()
    # Locate the <script> tag that immediately follows the closing
    # </details> of the todays-bus block.
    m = re.search(r'<details\s+id="todays-bus".*?</details>\s*<script>',
                   html, flags=re.DOTALL)
    assert m, "could not locate <script> after <details id=\"todays-bus\">"
    script_start = m.end()
    script_end = html.find("</script>", script_start)
    assert script_end > script_start, "Today's Bus <script> has no closing tag"
    todays_bus_script = html[script_start:script_end]
    assert "innerHTML" not in todays_bus_script, (
        "Today's Bus script must not use innerHTML; render bus rows via "
        "textContent + CSS white-space:pre-line instead"
    )


def test_todays_bus_recent_divs_use_pre_line_whitespace():
    """Companion to the no-innerHTML guard: the divs that hold recent rows
    use CSS white-space:pre-line so newlines from textContent render as
    visible line breaks."""
    html = _html()
    for div_id in ("todays-bus-recent-levels", "todays-bus-recent-triggers"):
        m = re.search(rf'<div\s+id="{div_id}"([^>]*)>', html)
        assert m, f"missing div id={div_id}"
        attrs = m.group(1)
        assert "pre-line" in attrs or "pre-wrap" in attrs, (
            f"{div_id} must declare CSS white-space:pre-line (or pre-wrap) "
            "so textContent newlines render as line breaks"
        )
