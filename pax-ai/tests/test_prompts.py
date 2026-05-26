"""Tests for pax_ai.prompts router logic + frozen prompt assembly."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from pax_ai import prompts


_SHA256_EMPTY = hashlib.sha256(b"").hexdigest()


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


# ---------------------------------------------------------------------------
# Phase 7: strengthen the forecast-emission contract guardrails.
# Every Phase 7 test pins a piece of the prompt that documents the
# <<PAX_FORECAST>> schema; the production prompt already carries the
# content, these tests pin it so a silent regression breaks here first.
# ---------------------------------------------------------------------------

def test_pax_forecast_lists_all_execution_read_values():
    """All four execution_read enum values must be documented in the
    forecast section. Phase 1 outcome labeling depends on the model
    emitting exactly these strings."""
    body = prompts.render_system_prompt()
    forecast_section = body.split("PAX FORECAST", 1)[-1]
    for v in ("PAY_FOR_TRADE", "WAIT_FOR_CONFIRM",
               "STAND_DOWN", "SCRATCH_READY"):
        assert v in forecast_section, (
            f"forecast section must list execution_read={v}")


def test_pax_forecast_lists_thesis_label_set():
    """The named thesis labels must appear so Claude does not invent
    its own. The validator's _THESIS_PREFIXES set is the contract."""
    body = prompts.render_system_prompt()
    forecast_section = body.split("PAX FORECAST", 1)[-1]
    for label in ("ACCEPTANCE_LONG", "ACCEPTANCE_SHORT",
                   "REJECTION_LONG",  "REJECTION_SHORT",
                   "ABSORPTION_FADE", "ICEBERG_DEFENSE",
                   "STOP_SWEEP_CONTINUATION", "STOP_SWEEP_FAILURE",
                   "NONE"):
        assert label in forecast_section, (
            f"forecast section must list thesis={label}")


def test_pax_forecast_documents_thesis_prefix_rule():
    """The thesis-prefix allowlist (ACCEPTANCE_/REJECTION_/...) backs the
    validator's prefix match. The prompt must teach the rule."""
    body = prompts.render_system_prompt()
    forecast_section = body.split("PAX FORECAST", 1)[-1]
    for prefix in ("ACCEPTANCE_", "REJECTION_", "ABSORPTION_",
                    "ICEBERG_", "STOP_SWEEP_"):
        assert prefix in forecast_section, (
            f"forecast section must mention thesis prefix {prefix}")


def test_pax_forecast_documents_numeric_ranges():
    """Range bounds enforced by pax_forecast_schema must appear in the
    contract: horizon_sec in [1, 86400], prob_success in [0.0, 1.0],
    expected_r in [-10.0, 10.0]. Without these, Claude has no source
    of truth for the bounds the validator enforces."""
    body = prompts.render_system_prompt()
    forecast_section = body.split("PAX FORECAST", 1)[-1]
    assert "86400"   in forecast_section, "horizon_sec upper bound missing"
    assert "0.0"     in forecast_section, "prob_success lower bound missing"
    assert "1.0"     in forecast_section, "prob_success upper bound missing"
    assert "-10.0"   in forecast_section, "expected_r lower bound missing"
    assert "10.0"    in forecast_section, "expected_r upper bound missing"


def test_pax_forecast_documents_no_markdown_fences_rule():
    """Markdown fences would break the regex extractor in
    forecast_signal.extract_block. The prompt must forbid them."""
    body = prompts.render_system_prompt()
    forecast_section = body.split("PAX FORECAST", 1)[-1]
    lowered = forecast_section.lower()
    assert "markdown fences" in lowered or "markdown fence" in lowered, (
        "forecast section must forbid wrapping the block in markdown fences")


def test_pax_forecast_documents_at_most_one_per_response():
    """forecast_signal.extract_block picks the LAST well-formed block when
    multiple appear; the prompt must say 'at most one per response' so the
    model does not rely on accidental retention semantics."""
    body = prompts.render_system_prompt()
    forecast_section = body.split("PAX FORECAST", 1)[-1]
    lowered = forecast_section.lower()
    assert "at most one" in lowered, (
        "forecast section must state 'at most one' per response")


def test_pax_forecast_direction_NONE_mapped_to_non_pay_execution_reads():
    """The contract must spell out that WAIT_FOR_CONFIRM / STAND_DOWN /
    SCRATCH_READY all require direction=NONE (not just 'execution_read
    != PAY_FOR_TRADE -> NONE')."""
    body = prompts.render_system_prompt()
    forecast_section = body.split("PAX FORECAST", 1)[-1]
    # The contract has two equivalent statements -- accept either form.
    assert "MUST be NONE" in forecast_section or \
           "direction MUST be NONE" in forecast_section


# ---------------------------------------------------------------------------
# Phase 4: turn-level audit trail -- prompt lineage helpers
# ---------------------------------------------------------------------------

def test_prompt_version_constant_exists_and_is_semver_shaped():
    """PROMPT_VERSION is the version of the prompt schema, not the model."""
    assert hasattr(prompts, "PROMPT_VERSION")
    v = prompts.PROMPT_VERSION
    assert isinstance(v, str) and v.strip()
    # Soft semver shape (major.minor.patch). Don't pin exact value.
    parts = v.split(".")
    assert 2 <= len(parts) <= 4
    assert all(p.isdigit() for p in parts), v


def test_compute_prompt_lineage_returns_required_fields(tmp_path, monkeypatch):
    """Required keys: prompt_sha256, prompt_version, skill_bundle_sha256,
    prompt_archive_path. (model_release_id is filled by the chat caller, not
    here -- it's a per-turn fact, not a per-prompt-file fact.)"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    sp = tmp_path / "system_prompt.txt"
    sp.write_text("SYSTEM PROMPT BODY", encoding="utf-8")
    out = prompts.compute_prompt_lineage(sp)
    for key in ("prompt_sha256", "prompt_version",
                "skill_bundle_sha256", "prompt_archive_path"):
        assert key in out, f"missing key: {key}"


def test_compute_prompt_lineage_hashes_exact_file_bytes(tmp_path, monkeypatch):
    """SHA must be over the on-disk bytes of sp_path, not a reconstructed string."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    sp = tmp_path / "system_prompt.txt"
    payload = b"EXACTLY THESE BYTES\nnewline-sensitive\n"
    sp.write_bytes(payload)
    out = prompts.compute_prompt_lineage(sp)
    assert out["prompt_sha256"] == hashlib.sha256(payload).hexdigest()


def test_compute_prompt_lineage_archives_prompt_at_sha_path(tmp_path, monkeypatch):
    """Archive lands at <archive_dir>/<sha>.txt with byte-identical content."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    sp = tmp_path / "system_prompt.txt"
    payload = b"ARCHIVE ME"
    sp.write_bytes(payload)
    out = prompts.compute_prompt_lineage(sp)

    arc = Path(out["prompt_archive_path"])
    assert arc.exists()
    sha = hashlib.sha256(payload).hexdigest()
    assert arc.name == f"{sha}.txt"
    assert arc.read_bytes() == payload


def test_compute_prompt_lineage_archive_is_idempotent(tmp_path, monkeypatch):
    """Two calls with the same prompt SHA must NOT rewrite the archive
    file. Idempotent + atomic. Compare mtime + content as the canary."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    sp = tmp_path / "system_prompt.txt"
    sp.write_text("SAME BYTES", encoding="utf-8")

    first = prompts.compute_prompt_lineage(sp)
    arc = Path(first["prompt_archive_path"])
    body_before = arc.read_bytes()
    mtime_before = arc.stat().st_mtime_ns

    second = prompts.compute_prompt_lineage(sp)
    arc2 = Path(second["prompt_archive_path"])
    assert arc2 == arc
    assert arc2.read_bytes() == body_before
    assert arc2.stat().st_mtime_ns == mtime_before


def test_compute_prompt_lineage_keeps_entries_when_new_prompt_arrives(
        tmp_path, monkeypatch):
    """A second, different prompt must NOT delete the first archive entry."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    sp_a = tmp_path / "a.txt"; sp_a.write_text("PROMPT A", encoding="utf-8")
    sp_b = tmp_path / "b.txt"; sp_b.write_text("PROMPT B", encoding="utf-8")

    out_a = prompts.compute_prompt_lineage(sp_a)
    out_b = prompts.compute_prompt_lineage(sp_b)

    arc_a = Path(out_a["prompt_archive_path"])
    arc_b = Path(out_b["prompt_archive_path"])
    assert arc_a.exists() and arc_b.exists()
    assert arc_a != arc_b


def test_compute_prompt_lineage_handles_missing_prompt_file(tmp_path, monkeypatch):
    """A non-existent sp_path must not raise -- the helper falls back to
    the empty-bytes SHA and skips the archive (empty path string)."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    out = prompts.compute_prompt_lineage(tmp_path / "does-not-exist.txt")
    # Empty-bytes SHA is a fixed 64-char hex string; non-empty, audit-safe.
    assert out["prompt_sha256"] == _SHA256_EMPTY
    assert out["prompt_archive_path"] == ""
    assert isinstance(out["skill_bundle_sha256"], str)
    assert len(out["skill_bundle_sha256"]) == 64
    assert out["prompt_version"] == prompts.PROMPT_VERSION


def test_compute_prompt_lineage_does_not_raise_on_empty_file(tmp_path, monkeypatch):
    """Empty file => sha-of-empty + empty archive path (we do not archive
    an empty body; nothing to learn from it later)."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    sp = tmp_path / "empty.txt"; sp.write_bytes(b"")
    out = prompts.compute_prompt_lineage(sp)
    assert out["prompt_sha256"] == _SHA256_EMPTY
    assert out["prompt_archive_path"] == ""


def test_compute_skill_bundle_sha256_is_deterministic():
    """The skill-bundle SHA must be stable across calls for the same on-disk
    skills. It is hashed over the same concatenated text that
    render_system_prompt embeds after the base preamble."""
    a = prompts.compute_skill_bundle_sha256()
    b = prompts.compute_skill_bundle_sha256()
    assert isinstance(a, str) and len(a) == 64
    assert a == b


def test_skill_bundle_sha256_differs_from_prompt_sha256(tmp_path, monkeypatch):
    """They hash different bodies (skills only vs full preamble + skills)."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    sp = tmp_path / "system_prompt.txt"
    sp.write_text(prompts.render_system_prompt(), encoding="utf-8")
    out = prompts.compute_prompt_lineage(sp)
    assert out["prompt_sha256"] != out["skill_bundle_sha256"]
