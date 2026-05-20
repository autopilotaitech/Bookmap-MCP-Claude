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
    # At least the default skill body got concatenated
    assert "## SKILL: pax-or" in body
