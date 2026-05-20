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


# ---------------------------------------------------------------------------
# Phase 1: cost / latency footer
# ---------------------------------------------------------------------------

def test_cost_footer_css_rule_present(html_body):
    assert re.search(r"\.cost-footer\s*\{", html_body), (
        "static .cost-footer CSS rule must exist for Phase 1 cost footer")


def test_cost_footer_dom_element_present(html_body):
    assert re.search(r'id="cost-footer"', html_body), (
        "static cost-footer DOM element must exist between transcript and input")


def test_update_cost_footer_function_present(html_body):
    assert "function updateCostFooter(" in html_body, (
        "updateCostFooter helper must be wired in the JS")


def test_done_handler_calls_update_cost_footer(html_body):
    """The SSE done branch must invoke updateCostFooter with the done payload.

    Allow arbitrary intervening characters (including the inner
    `if (obj.error) { ... }` block) between the branch entry and the
    updateCostFooter call -- we only care that the call lives within
    the done arm, before the next `else if (event ===` branch starts.
    """
    pat = (r"event === 'done'"          # entry into the done branch
           r"[\s\S]*?"                  # body, non-greedy
           r"updateCostFooter\(obj\)"   # the cost-footer call
           r"[\s\S]*?"
           r"else if \(event ===")      # bounded by the next branch
    assert re.search(pat, html_body) is not None, (
        "the SSE done branch must call updateCostFooter(obj) before the "
        "next else-if branch")


def test_cost_footer_no_client_side_pricing(html_body):
    """The CLI's total_cost_usd is authoritative -- the client must never
    compute pricing itself. We check the absence of telltale patterns
    that would indicate client-side cost math."""
    forbidden = [
        r"\bprice_per_input_token\b",
        r"\bprice_per_output_token\b",
        r"\binput_tokens\s*\*\s*0\.",   # client-side per-token multiply
        r"\boutput_tokens\s*\*\s*0\.",
    ]
    for pat in forbidden:
        assert not re.search(pat, html_body), (
            f"client-side pricing pattern {pat!r} detected -- the CLI's "
            f"total_cost_usd must be the only USD source")


def test_cost_footer_renders_subscription_fallback(html_body):
    """When the CLI omits total_cost_usd, the footer must say
    'subscription' rather than $0."""
    assert "'subscription'" in html_body or '"subscription"' in html_body, (
        "subscription-mode fallback label must be present in the footer logic")


# ---------------------------------------------------------------------------
# Phase 2: /deep mode static UI checks
# ---------------------------------------------------------------------------

def test_send_chat_intercepts_deep_prefix(html_body):
    """sendChat must strip a `/deep ` prefix exactly once and set
    deep=true on the POST body. /forget remains checked first."""
    # /forget check appears before the /deep check in sendChat
    forget_idx = html_body.find("raw === '/forget'")
    deep_idx = html_body.find("raw.startsWith('/deep ')")
    assert forget_idx != -1, "/forget intercept must remain"
    assert deep_idx != -1, "/deep intercept must be present"
    assert forget_idx < deep_idx, (
        "/forget must be checked before /deep so '/forget' never gets "
        "reinterpreted as a deep prompt")


def test_stream_chat_message_posts_deep_flag(html_body):
    """The fetch body must include both message and deep keys."""
    assert re.search(
        r"body:\s*JSON\.stringify\(\{message:\s*msg,\s*deep:\s*deep\}\)",
        html_body), (
        "POST body must be {message: msg, deep: deep}")


def test_deep_prefix_strip_is_exactly_six_chars(html_body):
    """`/deep ` is 6 chars; strip exactly one prefix to allow `/deep /deep …`
    to land as a deep request whose message starts with '/deep '."""
    assert "raw.slice(6).trim()" in html_body, (
        "must slice exactly the '/deep ' prefix (6 chars)")


def test_deep_empty_message_after_strip_is_noop(html_body):
    """An empty message after stripping `/deep ` must not send."""
    # Match the conservative pattern: msg = ... .trim(); if (!msg) return;
    assert re.search(r"msg = raw\.slice\(6\)\.trim\(\);\s+if \(!msg\) return;",
                       html_body), (
        "empty-after-strip must return without POSTing")


def test_deep_tag_dom_node_built_via_textcontent(html_body):
    """The DEEP marker on the YOU bubble must be a span built via DOM
    nodes + textContent, never innerHTML (so a future user-shaped flag
    can't inject markup)."""
    assert "createElement('span')" in html_body
    assert "deepTag.className = 'deep-tag'" in html_body
    assert "deepTag.textContent" in html_body


def test_last_request_state_is_captured(html_body):
    """Phase 3 regenerate hooks off _lastRequest; capture must happen
    inside _streamChatMessage so the request is recorded even on errors."""
    assert "let _lastRequest" in html_body, "_lastRequest must be declared"
    assert re.search(r"_lastRequest\s*=\s*\{message:\s*msg,\s*deep:\s*deep,",
                       html_body), (
        "_lastRequest must capture {message, deep, displayText}")


# ---------------------------------------------------------------------------
# Phase 3: UX bundle (abort, regenerate, keyboard shortcuts)
# ---------------------------------------------------------------------------

def test_abort_button_dom_present(html_body):
    assert re.search(r'id="abort-btn"', html_body), (
        "abort button DOM node must exist with id='abort-btn'")
    assert re.search(r'\.icon-btn\.abort\b', html_body), (
        ".icon-btn.abort CSS class must exist")


def test_abort_button_handler_calls_endpoint(html_body):
    assert "async function abortChat" in html_body, (
        "abortChat handler must exist")
    assert re.search(r"fetch\('/api/pax/chat/abort'", html_body), (
        "abortChat must POST to /api/pax/chat/abort")


def test_abort_button_hidden_when_idle(html_body):
    """The send button shows when idle, abort shows in-flight. Both states
    must be driven by the body.in-flight class so toggling is global and
    idempotent."""
    assert "body.in-flight .icon-btn.send" in html_body
    assert "body.in-flight .icon-btn.abort" in html_body


def test_regenerate_function_present(html_body):
    assert "async function regenerateLastPax" in html_body, (
        "regenerateLastPax must exist")
    # It must call _streamChatMessage with deep=_lastRequest.deep so a
    # regenerated /deep turn stays deep. The deep reference can appear
    # inside the _streamChatMessage call -- only the function-name
    # context matters for this guard.
    assert re.search(
        r"async function regenerateLastPax[\s\S]*?"
        r"_streamChatMessage\(\{[\s\S]*?deep:\s*_lastRequest\.deep",
        html_body), (
        "regenerateLastPax must preserve _lastRequest.deep on the re-run")


def test_regenerate_button_present_per_pax_bubble(html_body):
    assert re.search(r"createElement\('button'\)[\s\S]{0,200}?regen-btn",
                       html_body), (
        "each Pax turn must get a regenerate button via DOM nodes")
    assert ".turn.pax.last-completed .regen-btn { display: flex; }" in html_body


def test_keyboard_escape_aborts_when_in_flight(html_body):
    assert re.search(r"e\.key === 'Escape'[\s\S]*?_chatInFlight[\s\S]*?abortChat\(\)",
                       html_body), (
        "Escape must call abortChat when _chatInFlight is true")


def test_keyboard_arrow_up_recalls_last_prompt(html_body):
    assert re.search(r"e\.key === 'ArrowUp'[\s\S]*?_lastRequest[\s\S]*?_lastRequest\.message",
                       html_body), (
        "ArrowUp must recall _lastRequest.message into the textarea")


def test_keyboard_arrow_up_preserves_deep_prefix(html_body):
    """If the prior request was deep, the recalled prompt must include
    the '/deep ' prefix so the user can edit + resend in deep mode."""
    assert re.search(r"_lastRequest\.deep[\s\S]*?'/deep '\s*\+\s*_lastRequest\.message",
                       html_body), (
        "ArrowUp must re-add the '/deep ' prefix when prior request was deep")


def test_keyboard_ctrl_enter_sends(html_body):
    assert re.search(r"e\.key === 'Enter' && \(e\.ctrlKey \|\| e\.metaKey\)",
                       html_body), (
        "Ctrl/Cmd+Enter shortcut must be present")


def test_keyboard_ctrl_l_scrolls_to_bottom(html_body):
    assert re.search(
        r"\(e\.ctrlKey \|\| e\.metaKey\) && \(e\.key === 'l' \|\| e\.key === 'L'\)",
        html_body), (
        "Ctrl/Cmd+L shortcut must be present")


def test_deep_request_via_regenerate_keeps_deep(html_body):
    """Belt: regenerate calls _streamChatMessage with deep=_lastRequest.deep,
    and _streamChatMessage propagates that to the POST body. Together
    these guarantee a regenerated /deep turn stays deep on the wire."""
    # 1. Regenerate passes deep through.
    assert re.search(r"regenerateLastPax[\s\S]*?deep:\s*_lastRequest\.deep",
                       html_body)
    # 2. _streamChatMessage posts deep on the wire.
    assert re.search(r"body:\s*JSON\.stringify\(\{message:\s*msg,\s*deep:\s*deep\}\)",
                       html_body)


# ---------------------------------------------------------------------------
# Pre-open hardening fix 2: regenerate must not duplicate the YOU bubble.
# Original bug: regenerateLastPax removed only the Pax bubble, then
# _streamChatMessage unconditionally appended a fresh YOU bubble, so
# repeated regen clicks stacked duplicate user prompts in the transcript.
# Fix: _streamChatMessage gates the YOU-bubble append on a
# `suppressUserEcho` flag that regenerateLastPax sets to true.
# ---------------------------------------------------------------------------

def test_stream_chat_message_supports_suppress_user_echo(html_body):
    """_streamChatMessage must read a suppressUserEcho flag from the
    request and gate the appendTurn YOU call on it."""
    assert "suppressUserEcho" in html_body, (
        "_streamChatMessage must accept a suppressUserEcho option for "
        "regenerate replay")
    # The YOU appendTurn call must be wrapped in `if (!suppressUserEcho)`.
    assert re.search(
        r"if \(!suppressUserEcho\)\s*\{\s*appendTurn\('you',\s*'YOU',",
        html_body), (
        "the YOU appendTurn call must be gated on suppressUserEcho so "
        "regenerate does not duplicate the prior user prompt")


def test_regenerate_passes_suppress_user_echo_true(html_body):
    """regenerateLastPax must pass suppressUserEcho: true so the
    existing YOU bubble survives and only a new Pax bubble is streamed."""
    assert re.search(
        r"async function regenerateLastPax[\s\S]*?"
        r"_streamChatMessage\(\{[\s\S]*?suppressUserEcho:\s*true",
        html_body), (
        "regenerateLastPax must set suppressUserEcho: true on the replay")


def test_regenerate_still_preserves_deep_after_hardening(html_body):
    """Belt-and-braces: the hardening MUST NOT break the
    deep-preservation guarantee from Phase 3."""
    assert re.search(
        r"async function regenerateLastPax[\s\S]*?"
        r"_streamChatMessage\(\{[\s\S]*?deep:\s*_lastRequest\.deep[\s\S]*?"
        r"suppressUserEcho:\s*true",
        html_body), (
        "the regenerate call site must keep deep=_lastRequest.deep AND "
        "now also set suppressUserEcho:true")


def test_normal_send_path_still_echoes_you(html_body):
    """Normal sends (sendChat -> _streamChatMessage with no
    suppressUserEcho) must still produce a YOU bubble. We assert the
    sendChat call site does NOT set suppressUserEcho."""
    # Find the sendChat function body up to its closing brace.
    m = re.search(r"async function sendChat\([^)]*\) \{([\s\S]*?)\n\}", html_body)
    assert m, "sendChat function must be present"
    body = m.group(1)
    assert "suppressUserEcho" not in body, (
        "sendChat must NOT set suppressUserEcho -- regenerate is the "
        "only caller that should suppress the YOU echo")
