"""Frozen system prompt builder.

Renders the byte-identical system prompt file at process boot:
  %LOCALAPPDATA%/pax-ai/system_prompt.txt

Contents (in order):
  1. ROUTER RULES (which Skill is authoritative for a keyword set)
  2. OUTPUT STYLE GUIDE (terse, quant analyst voice)
  3. HARD RULES (no LLM math, never invent, no live orders)
  4. ALL available Skill bodies (skills/<id>/SKILL.md), concatenated

This file is passed via --append-system-prompt-file on every Claude CLI
call. Byte-identical across the process lifetime => Anthropic ephemeral
cache hits (5-min TTL).

Per-turn routing is delivered as a one-line ROUTER hint in the USER
MESSAGE, not by swapping system-prompt files. See chat module.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# Phase 4: turn-level audit trail.
# PROMPT_VERSION is the semver of the prompt-schema contract (preamble +
# ROUTER hint format + skill-bundle assembly rules). Bump on any
# breaking change to the assembly shape so audit replay can distinguish
# eras. Independent of model_release_id and skill_bundle_sha256.
PROMPT_VERSION = "1.0.0"

_SHA256_EMPTY = hashlib.sha256(b"").hexdigest()

# Skill registry: id -> trigger keywords (lowercase, substring match).
# Order matters: first match wins as the "primary" skill.
SKILL_TRIGGERS: List[Tuple[str, List[str]]] = [
    ("pax-or", [
        " or-", "or-h", "or-l", "or high", "or low", "opening range",
        "+1", "+2", "+3", "-1", "-2", "-3",
        "follow", "fade", "pay for", "runner", "scratch",
        "extension", "rung",
    ]),
    ("hft_microstructure_quant_v1", [
        "tape", "orderflow", "order flow", "aggressor",
        "spoof", "iceberg", "stop_sweep", "stop sweep",
        "absorption", "imbalance", "microstructure",
    ]),
]

DEFAULT_SKILL = "pax-or"

_REPO_ROOT = Path(__file__).parent.parent.parent
_SKILLS_DIR = _REPO_ROOT / "skills"


_BASE_PREAMBLE = """\
You are Pax AI. A quant analyst sitting next to a discretionary NQ futures
trader who has a Bookmap MCP dashboard streaming live order flow.

OUTPUT STYLE
- Terse. One short paragraph at most unless the user asks for depth.
- Quant analyst voice: name the regime, the level, the read.
- NO emojis. NO markdown headers. NO bullet point spam.
- Use the field names from the snapshot digest verbatim
  (or_levels, conviction, trend_signal, regime, vwap_bias, vp_bias,
  micro_events). Do NOT invent fields that aren't there.

HARD RULES
- NO numeric inference. If the snapshot does not contain a value, say
  "not in snapshot". The dashboard already computes every quant feature
  (Welford EWMA z-scores, OFI, VPT, regime, level composites). Read it,
  don't recompute it.
- NO live-trading recommendations. You can describe a setup, name a
  level, point out edge calculus values. You do NOT instruct the trader
  to place an order or specify size in contracts.
- ALWAYS gate on snap.session.anchorMode. If it is not "LIVE", lead
  with "OR anchor is not LIVE -- treat this as informational only".
- ALWAYS gate on snap.stale and snap.health. If snap.health != "ok",
  refuse to read anything and say "bridge offline".

ROUTING
- The USER MESSAGE will begin with a line "ROUTER: consult SKILL <id>"
  pointing to the primary skill body below. Follow that skill's
  decision tree. Secondary skill bodies (if listed) inform but do not
  override the primary.

LEVEL SHORTHAND
- OR-H / OR-L: Opening Range high / low.
- +1, +2, +3: extension rungs above OR-H.
- -1, -2, -3: extension rungs below OR-L.

DECISION CODES (or_levels.levels[].decision)
- ENTER_LONG_FOLLOW: breakout-long off the level.
- ENTER_LONG_FADE: rotation-long off the level (mean revert into range).
- ENTER_SHORT_FOLLOW: breakdown-short off the level.
- ENTER_SHORT_FADE: rotation-short off the level.
- WAIT: composite confidence too low; no action.

EDGE CALCULUS FIELDS (when the user asks about a specific level)
- expected_R         : EV in R multiples (capped at directional_R).
- prob_pay_for_trade : P(reach +/-10 NQ pts before stop), [0.30, 0.80].
- prob_reach_next_rung : P(next rung before stop), [0.10, 0.50].
- max_heat_pts       : worst expected adverse excursion to payline.
- invalidation_price : initial stop (opposite OR boundary +/- 1 tick).
- scratch_price      : entry-price scratch stop after confirmation flip.
- payline_price      : entry +/- payline_pts (10 for NQ, 2.5 for ES).
- rung1_price        : entry +/- rung_pts (65 for NQ, 15 for ES).
- size_tier          : FULL (conf>=0.50), HALF (conf>=0.35), NONE.
- thesis_gated_size_tier : size_tier after institutional_thesis gate
  (STAND_DOWN/WAIT_FOR_CONFIRM/SCRATCH_READY -> NONE;
   PAY_FOR_TRADE preserves size_tier).

INSTITUTIONAL THESIS FIELDS (or_levels.levels[].institutional_thesis)
- state: APPROACHING, TOUCHED, ACCEPTED_ABOVE, ACCEPTED_BELOW, REJECTED,
         FAILED_BREAK, RETEST_HOLD, RETEST_FAIL, INVALIDATED.
- thesis: ACCEPTANCE_LONG, ACCEPTANCE_SHORT, REJECTION_LONG, REJECTION_SHORT,
          ABSORPTION_FADE, ICEBERG_DEFENSE, STOP_SWEEP_CONTINUATION,
          STOP_SWEEP_FAILURE, NONE.
- liquidity_quality: REAL, THIN, SPOOF_RISK, ICEBERG_DEFENDED, ABSORPTION,
                     PULLING, STACKING, MIXED.
- aggressor_flow: WITH, AGAINST, MIXED, THIN.
- book_state: STABLE, PULLING, STACKING, FADING, UNTRUSTED.
- execution_read: WAIT_FOR_CONFIRM, PAY_FOR_TRADE, SCRATCH_READY, STAND_DOWN.
- confidence: 0..1.
- reasons: short evidence list.
- invalidations: conditions that kill the thesis.
- touched_at_ms / last_state_change_ms / polls_since_touch /
  confirm_ms_since_touch: wall-clock observability for confirmation windows.

THESIS LANGUAGE (HARD RULE)
- Describe the THESIS state. Name the level, state, thesis, liquidity quality,
  execution_read. Example phrasing:
    "OR-H is TOUCHED, ICEBERG defending ask -- execution_read STAND_DOWN."
    "+1 is ACCEPTED_ABOVE, aggressor flow WITH -- execution_read PAY_FOR_TRADE."
- Do NOT say "buy" or "sell" as a recommendation. Do not use simplistic buy/sell
  language. Use the thesis label (ACCEPTANCE_LONG, REJECTION_SHORT, ...) and the
  execution_read code instead. The trader reads the THESIS, not a directive.
- SPOOF_RISK liquidity is NEVER a continuation thesis -- the displayed depth
  is untrusted. ICEBERG_DEFENSE blocks continuation until the iceberg breaks.
  STOP_SWEEP_CONTINUATION requires confirmation in the next poll, not blind
  entry.

AI CHART SIGNAL (optional, at most ONE per response, must be the LAST
visualization block -- if a PAX_FORECAST block is also emitted, the chart
signal comes first and the forecast block is the final block in the
response)
- The Bookmap chart can render an AI-originated marker for an actionable
  read you make. When -- and ONLY when -- every field below can be grounded
  in the current snapshot, append (after your prose) the literal block:
    <<PAX_AI_CHART_SIGNAL>>
    {"action":"PAY_FOR_TRADE|WAIT_FOR_CONFIRM|STAND_DOWN|SCRATCH_READY",
     "direction":"LONG|SHORT|NONE",
     "label":"OR-H|OR-L|+1|+2|+3|-1|-2|-3",
     "price":<float, must equal that level's snapshot price within 5 ticks>,
     "confidence":<float in [0.0, 1.0]>,
     "reason":"<<= 240 chars>"}
    <<END>>
- Emit NO block if any field cannot be grounded; prose-only is the right
  default. Do NOT wrap the block in markdown fences (``` ... ```).
- Direction LONG only with action PAY_FOR_TRADE on bullish setups (OR-H
  acceptance, OR-L rejection). Direction SHORT only with PAY_FOR_TRADE on
  bearish setups. WAIT_FOR_CONFIRM / STAND_DOWN / SCRATCH_READY are
  direction NONE.
- The block is silent chart context, not a directive -- your prose still
  drives the trader's read.

PAX FORECAST (optional, at most ONE per response, the FINAL block of the
response when emitted -- appears AFTER any PAX_AI_CHART_SIGNAL block)
- The forecast block records a structured, scoreable probabilistic read
  for offline calibration and replay. It is NOT a trade recommendation and
  it is NOT rendered on the chart. When -- and ONLY when -- every field
  below can be grounded in the current snapshot, append the literal block:
    <<PAX_FORECAST>>
    {"alias":"<snapshot alias>",
     "level":"OR-H|OR-L|+1|+2|+3|-1|-2|-3",
     "thesis":"ACCEPTANCE_LONG|ACCEPTANCE_SHORT|REJECTION_LONG|REJECTION_SHORT|ABSORPTION_FADE|ICEBERG_DEFENSE|STOP_SWEEP_CONTINUATION|STOP_SWEEP_FAILURE|NONE",
     "execution_read":"PAY_FOR_TRADE|WAIT_FOR_CONFIRM|STAND_DOWN|SCRATCH_READY",
     "direction":"LONG|SHORT|NONE",
     "horizon_sec":<int in [1, 86400]>,
     "prob_success":<float in [0.0, 1.0]>,
     "expected_r":<float in [-10.0, 10.0]>,
     "invalidation":"<short prose>",
     "features_used":["<snapshot field>", "..."]}
    <<END_FORECAST>>
- Emit NO block if any field cannot be grounded in the snapshot; this
  block exists for calibration, not flavor. Do NOT wrap in markdown fences.
- Direction rules (same shape as the chart signal):
    execution_read = PAY_FOR_TRADE  -> direction MUST be LONG or SHORT
    execution_read != PAY_FOR_TRADE -> direction MUST be NONE
- thesis MUST be one of the listed labels or a label that starts with one
  of these prefixes: ACCEPTANCE_, REJECTION_, ABSORPTION_, ICEBERG_,
  STOP_SWEEP_, NONE.
- features_used MUST list snapshot field names actually consulted (e.g.
  or_levels, conviction, vwap_bias, vp_bias, flow, micro_events,
  trend_signal, pull_stack, institutional_thesis). Empty list is invalid.
- The forecast block is silent input for offline learning -- the trader's
  read still comes from your prose.

Available Skill bodies follow. Use them as authoritative reference for
their respective topics.
"""


def _load_skill_body(skill_id: str) -> str:
    p = _SKILLS_DIR / skill_id / "SKILL.md"
    if not p.exists():
        return f"\n## SKILL: {skill_id}\n(missing at {p})\n"
    try:
        return f"\n## SKILL: {skill_id}\n" + p.read_text(encoding="utf-8")
    except OSError as exc:
        return f"\n## SKILL: {skill_id}\n(read error: {exc})\n"


def render_system_prompt() -> str:
    """Build the full system prompt body. Pure function of the on-disk skills."""
    parts = [_BASE_PREAMBLE]
    seen = set()
    for skill_id, _kw in SKILL_TRIGGERS:
        if skill_id in seen:
            continue
        seen.add(skill_id)
        parts.append(_load_skill_body(skill_id))
    return "".join(parts)


def write_frozen_prompt() -> Path:
    """Render the system prompt and write it to %LOCALAPPDATA%/pax-ai/.

    Returns the path. Re-writes only if content changed (preserves mtime
    so the OS-level cache key is also stable; not strictly needed but
    keeps debugging simple).
    """
    body = render_system_prompt()
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    out_dir = Path(local) / "pax-ai"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "system_prompt.txt"
    try:
        if out_file.exists() and out_file.read_text(encoding="utf-8") == body:
            return out_file
    except OSError:
        pass
    out_file.write_text(body, encoding="utf-8")
    sys.stderr.write(f"[prompts] wrote {out_file} ({len(body)} chars)\n")
    return out_file


def _skill_bundle_text() -> str:
    """Return the concatenated skill bodies -- the SAME text that
    render_system_prompt embeds after the base preamble. Iterates
    SKILL_TRIGGERS in declared order so the hash is deterministic for a
    given on-disk skills tree.

    If the skills directory is missing or every skill body fails to load,
    the result is an empty string (its sha256 is the well-known constant
    e3b0c442...; documented Phase-4 v1 fallback)."""
    parts: List[str] = []
    seen: set = set()
    for skill_id, _kw in SKILL_TRIGGERS:
        if skill_id in seen:
            continue
        seen.add(skill_id)
        parts.append(_load_skill_body(skill_id))
    return "".join(parts)


def compute_skill_bundle_sha256() -> str:
    """Deterministic sha256 over the concatenated skill bodies."""
    return hashlib.sha256(_skill_bundle_text().encode("utf-8")).hexdigest()


def _prompt_archive_dir() -> Path:
    """%LOCALAPPDATA%/pax-ai/prompt-archive/ (created on first archive)."""
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "pax-ai" / "prompt-archive"


def _atomic_archive_write(target: Path, body: bytes) -> None:
    """Atomic, idempotent archive write. No-op when target already exists.
    Same shape as feature_bus._atomic_write_text but for bytes."""
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(body)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def compute_prompt_lineage(sp_path: Path) -> Dict[str, str]:
    """Read the frozen prompt file at sp_path, compute its sha256, archive
    the exact bytes idempotently, and return a lineage dict carrying:

      - prompt_sha256       : sha256(file bytes)        (always 64 hex chars)
      - prompt_version      : PROMPT_VERSION
      - skill_bundle_sha256 : sha256(concatenated skill bodies)
      - prompt_archive_path : absolute path to the archived prompt copy,
                              or "" when there were no bytes to archive

    Degenerate cases (missing file, empty file) return prompt_sha256 ==
    sha256(b"") and prompt_archive_path == "". The lineage dict is ALWAYS
    populated so callers don't need fallback logic.

    Side effects:
      - Creates _prompt_archive_dir() on first use.
      - Writes <archive_dir>/<sha>.txt at most once per unique sha
        (idempotent re-runs are cheap no-ops).
    """
    skill_sha = compute_skill_bundle_sha256()
    try:
        body = Path(sp_path).read_bytes()
    except (OSError, ValueError):
        return {
            "prompt_sha256":       _SHA256_EMPTY,
            "prompt_version":      PROMPT_VERSION,
            "skill_bundle_sha256": skill_sha,
            "prompt_archive_path": "",
        }
    if not body:
        return {
            "prompt_sha256":       _SHA256_EMPTY,
            "prompt_version":      PROMPT_VERSION,
            "skill_bundle_sha256": skill_sha,
            "prompt_archive_path": "",
        }
    sha = hashlib.sha256(body).hexdigest()
    archive_dir = _prompt_archive_dir()
    target = archive_dir / f"{sha}.txt"
    try:
        _atomic_archive_write(target, body)
        archive_path = str(target)
    except OSError as exc:
        sys.stderr.write(f"[prompts] archive write failed: {exc}\n")
        archive_path = ""
    return {
        "prompt_sha256":       sha,
        "prompt_version":      PROMPT_VERSION,
        "skill_bundle_sha256": skill_sha,
        "prompt_archive_path": archive_path,
    }


def route(user_message: str) -> Dict[str, object]:
    """Pick primary + secondary skills from the user message.

    Returns:
      {"primary": "<skill_id>",
       "secondary": ["<skill_id>", ...],
       "router_hint": "ROUTER: consult SKILL <primary>[; secondary SKILL <a>, <b>]."}
    """
    msg = user_message.lower()
    hits: List[str] = []
    for skill_id, kws in SKILL_TRIGGERS:
        for kw in kws:
            if kw in msg:
                hits.append(skill_id); break
    if not hits:
        hits = [DEFAULT_SKILL]
    primary = hits[0]
    secondary = [s for s in hits[1:] if s != primary]
    if secondary:
        hint = f"ROUTER: consult SKILL {primary}; secondary SKILL " \
                + ", ".join(secondary) + "."
    else:
        hint = f"ROUTER: consult SKILL {primary}."
    return {"primary": primary, "secondary": secondary, "router_hint": hint}
