"""Tests for pax_ai.prompts router logic + frozen prompt assembly."""

from __future__ import annotations

from pax_ai import prompts


def test_route_pax_or_keyword():
    r = prompts.route("is +1 still in play?")
    assert r["primary"] == "pax-or"


def test_route_microstructure_keyword_primary():
    r = prompts.route("any SPOOF on the offer right now?")
    assert r["primary"] == "hft_microstructure_quant_v1"


def test_route_default_when_no_keyword():
    r = prompts.route("hello, what's going on")
    assert r["primary"] == "pax-or"   # default


def test_route_emits_router_hint_string():
    r = prompts.route("watch the tape at OR-High")
    assert r["router_hint"].startswith("ROUTER: consult SKILL")
    assert "pax-or" in r["router_hint"]


def test_route_secondary_skill_appended():
    r = prompts.route("OR-High and orderflow context")
    # pax-or fires first (OR-High), hft fires too (orderflow).
    assert r["primary"] == "pax-or"
    assert "hft_microstructure_quant_v1" in r["secondary"]
    assert "secondary SKILL" in r["router_hint"]


def test_render_system_prompt_contains_preamble_and_skills():
    body = prompts.render_system_prompt()
    assert "Pax AI" in body
    # Output style header
    assert "OUTPUT STYLE" in body
    # Both skills should be concatenated, neither missing.
    assert "## SKILL: pax-or" in body
    assert "## SKILL: hft_microstructure_quant_v1" in body


def test_render_system_prompt_no_missing_skill_placeholders():
    """If a skill body file is absent, _load_skill_body emits a
    "(missing at <path>)" placeholder. The audit requires that BOTH
    advertised skills be tracked in the repo, so neither placeholder
    should ever appear in the rendered prompt."""
    body = prompts.render_system_prompt()
    assert "(missing at " not in body, (
        "render_system_prompt rendered a missing-skill placeholder; one of "
        "skills/pax-or/SKILL.md or skills/hft_microstructure_quant_v1/SKILL.md "
        "is not on disk")


# ---------------------------------------------------------------------------
# Audit fix 1: no stale 08:30 anchor claims; positive language about the
# operator OR / snapshot anchor being authoritative.
# ---------------------------------------------------------------------------

STALE_ANCHOR_PHRASES = [
    "For NQ that is 08:30",
    "08:30:00 – 08:30:30",
    "08:30:00 - 08:30:30",          # ASCII-hyphen variant
    'anchor: "RTH 08:30',
    '< 08:30:30 CT',
    'Outside 08:30 – 15:00',
    'Outside 08:30 - 15:00',
    'pre-08:35',
]


def test_render_system_prompt_no_stale_active_anchor_claims():
    body = prompts.render_system_prompt()
    found = [p for p in STALE_ANCHOR_PHRASES if p in body]
    assert not found, (
        f"Pax AI system prompt contains stale active-anchor claims that "
        f"contradict the operator-configured OR anchor invariant: {found}")


def test_render_system_prompt_states_operator_anchor_is_authoritative():
    body = prompts.render_system_prompt()
    # The skill must explicitly tell Claude that the operator-configured
    # OR (via the live snapshot) is the source of truth.
    assert "anchorHHMM" in body
    assert "anchorTimezone" in body
    assert "anchorRangeSeconds" in body
    assert "anchorMode" in body
    # And it must spell out the LIVE gate.
    assert ("anchorMode != \"LIVE\"" in body
            or "anchorMode != 'LIVE'" in body), (
        "skill must instruct the agent to treat output as informational only "
        "when anchorMode != LIVE")
    # And there must be language disclaiming the historical RTH table.
    assert "historical context only" in body or "historical reference only" in body
