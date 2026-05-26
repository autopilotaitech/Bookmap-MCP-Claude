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


def test_base_preamble_includes_ai_chart_signal_contract():
    """The frozen system prompt must teach the model how to emit the
    structured chart-signal block. The chart-render path depends on it,
    so this is a hard contract -- not a stylistic guideline."""
    body = prompts.render_system_prompt()
    assert "<<PAX_AI_CHART_SIGNAL>>" in body
    assert "<<END>>" in body
    assert "PAY_FOR_TRADE" in body
    assert "WAIT_FOR_CONFIRM" in body
    assert "STAND_DOWN" in body
    assert "SCRATCH_READY" in body
    # The emission rule MUST be conditional ("emit NO block if any field
    # cannot be grounded"). Otherwise the model spams meaningless markers.
    assert "Emit NO block" in body or "emit no block" in body.lower()


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
    # Round 2 audit: any wording that frames the active OR as RTH must
    # also be absent. RTH is only acceptable inside the §1.2 "historical
    # context only -- NOT the active anchor" paragraph and inside Volume
    # Profile / VWAP component-field documentation. The phrases below
    # specifically frame the live anchor, so they must NEVER appear.
    "When RTH opens",
    "Regular Trading Hours",
    "of Regular Trading Hours",
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


def test_render_system_prompt_defines_or_as_operator_configured_static_or():
    """Round 2 audit positive assertion: §1 must define the OR as the
    operator-configured Static OR, not as RTH."""
    body = prompts.render_system_prompt()
    assert "operator-configured Static OR" in body, (
        "skill §1 must define OR as the operator-configured Static OR, "
        "not as RTH")
    # When/if-the-OR-window-opens replaces "When RTH opens".
    assert ("configured OR window opens" in body), (
        "the institutional-flow paragraph must trigger on the configured "
        "OR window opening, not on RTH opening")


# ---------------------------------------------------------------------------
# Round 2 audit fix 2: HFT skill must NOT push Pax AI into JSON-only mode.
# ---------------------------------------------------------------------------

def test_render_system_prompt_no_router_mode_selector():
    """The old HFT skill used 'If the USER MESSAGE began with ROUTER...
    you are in mode 1' to switch into JSON output. prompts.route() can
    legitimately make hft_microstructure_quant_v1 the primary skill on
    keywords like 'tape' or 'iceberg', so that selector would coerce
    Pax AI into JSON. The selector text must NOT appear anywhere in
    the rendered prompt."""
    body = prompts.render_system_prompt()
    assert "If the USER MESSAGE began with" not in body, (
        "rendered prompt must not contain the router-primary-as-mode-selector "
        "phrasing -- it can flip Pax AI to JSON-only on a normal query")
    assert "you are in mode 1" not in body, (
        "the 'mode 1' selector phrase must be gone")


def test_render_system_prompt_no_unconditional_output_only_json():
    """The literal instruction 'Output only JSON' must not appear in the
    rendered prompt. Mentioning JSON output as an EXTERNAL contract is
    fine; an unconditional command to Claude is not."""
    body = prompts.render_system_prompt()
    assert "Output only JSON" not in body, (
        "unconditional 'Output only JSON' instruction must not survive in "
        "the rendered system prompt -- it can be interpreted as Pax AI's "
        "output mode")


def test_render_system_prompt_pax_ai_json_exclusion_is_explicit():
    """The HFT skill must explicitly tell the agent that JSON mode is
    never Pax AI's output. Either 'never Pax AI' or 'never Pax AI's
    output' is acceptable phrasing."""
    body = prompts.render_system_prompt()
    assert "never Pax AI" in body or "never** Pax AI" in body, (
        "HFT skill must explicitly disclaim Pax AI as a JSON-output context")


# ---------------------------------------------------------------------------
# PAX_FORECAST emission contract (self-training research loop).
# ---------------------------------------------------------------------------

def test_base_preamble_includes_pax_forecast_contract():
    """The system prompt must teach Claude how to emit the structured
    <<PAX_FORECAST>> block consumed by the calibration / replay pipeline."""
    body = prompts.render_system_prompt()
    assert "<<PAX_FORECAST>>" in body
    assert "<<END_FORECAST>>" in body
    for label in ("OR-H", "OR-L", "+1", "+2", "+3", "-1", "-2", "-3"):
        assert label in body
    # Required field names appear in the block contract.
    for field in ("alias", "level", "thesis", "execution_read", "direction",
                  "horizon_sec", "prob_success", "expected_r",
                  "invalidation", "features_used"):
        assert field in body, f"forecast contract missing field: {field}"
    # Conditional-emission language must be present.
    assert "Emit NO block" in body or "emit no block" in body.lower()


def test_pax_forecast_has_distinct_end_marker_from_chart_signal():
    """The chart-signal block ends with <<END>>; the forecast block ends
    with <<END_FORECAST>>. Distinct markers keep the two regex extractors
    from overlapping."""
    body = prompts.render_system_prompt()
    assert "<<END_FORECAST>>" in body
    # And the chart signal contract still uses <<END>>.
    assert "<<END>>" in body


def test_pax_forecast_block_direction_rules_documented():
    body = prompts.render_system_prompt()
    # PAY_FOR_TRADE -> LONG or SHORT; otherwise NONE.
    # The chart-signal section uses the same rule. We just need the
    # forecast section to repeat it so the model has no excuse to mix.
    forecast_section = body.split("PAX FORECAST", 1)[-1]
    assert "PAY_FOR_TRADE" in forecast_section
    assert "LONG or SHORT" in forecast_section
    assert "NONE" in forecast_section


def test_pax_forecast_block_listed_after_chart_signal():
    """If both blocks are emitted, the forecast must come AFTER the chart
    signal. The prompt should describe ordering explicitly."""
    body = prompts.render_system_prompt()
    chart_pos = body.find("<<PAX_AI_CHART_SIGNAL>>")
    forecast_pos = body.find("<<PAX_FORECAST>>")
    assert chart_pos != -1 and forecast_pos != -1
    assert chart_pos < forecast_pos, (
        "the prompt must describe the chart signal before the forecast "
        "so the ordering is clear to Claude")


def test_pax_forecast_block_features_used_must_be_non_empty():
    body = prompts.render_system_prompt()
    forecast_section = body.split("PAX FORECAST", 1)[-1]
    assert "features_used" in forecast_section
    assert "Empty list is invalid" in forecast_section or \
           "must list" in forecast_section
