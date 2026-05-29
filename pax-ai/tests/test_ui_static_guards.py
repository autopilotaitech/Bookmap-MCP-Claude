"""Static HTML/JS string guards for the Pax AI window.

The UI was overhauled 2026-05-28 (operator directive): the analysis drawers
(EDGE CALCULUS, PLAYBOOK) and the 'Today's Bus' capture panel were removed as
bloat. The chat body is now a terminal-style transcript that streams the
agentic sim trader's decisions, plus the agent control bar.

These are STATIC GUARDS only (no browser, no JS execution). They assert the
bloat stays gone and the agent terminal stays present."""
from __future__ import annotations

import re
from pathlib import Path

HTML_PATH = Path(__file__).resolve().parent.parent / "pax_ai" / "static" / "index.html"


def _html() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


def test_index_html_exists():
    assert HTML_PATH.exists()


def test_kept_shell_markers_present():
    html = _html()
    for marker in ('class="strip"', "Pax AI", 'id="transcript"', 'id="chat-input"'):
        assert marker in html, f"core shell marker missing: {marker}"


def test_agent_control_bar_present():
    html = _html()
    for marker in ('class="agentbar"', 'id="agent-toggle"', 'id="agent-arm"',
                   'id="agent-last"', "/api/pax/agent/status",
                   "/api/pax/agent/start", "agentToggle", "agentArm"):
        assert marker in html, f"agent control marker missing: {marker}"


def test_agent_terminal_stream_present():
    html = _html()
    assert "appendAgentTick" in html, "agent terminal tick renderer missing"
    assert "tk-long" in html and "tk-short" in html, "tick CSS missing"


def test_analysis_drawers_removed():
    """The 'analyze' bloat must stay gone (operator: 'all that bloat got to go')."""
    html = _html()
    for gone in ("EDGE CALCULUS", "PLAYBOOK", "todays-bus", "Today's Bus",
                 "/api/pax/bus/status", "/api/pax/bus/recent",
                 "/api/pax/bus/summary", 'id="whynow-area"'):
        assert gone not in html, f"removed bloat reappeared in UI: {gone!r}"


def test_agent_tick_uses_textcontent_not_innerhtml():
    """XSS hardening: the agent terminal renders rationale via textContent."""
    html = _html()
    m = re.search(r"function appendAgentTick\(.*?\n}", html, flags=re.DOTALL)
    assert m, "appendAgentTick not found"
    body = m.group(0)
    assert "textContent" in body
    assert "innerHTML" not in body, "agent tick must not use innerHTML"
