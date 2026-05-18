"""P6 — Claude CLI hard-call subprocess (cache-backed).

Spawns the `claude` CLI for ambiguous trade decisions. The system prompt
(pax-or SKILL.md + current weights + decision schema) is stable and gets
prompt-cached by Anthropic. Cache keys are bucketed (regime + level +
biases + conviction-tier) so identical situations within 30 seconds reuse
the prior verdict at zero API cost.

Cost (per call):
  L0 hit      $0      — same situation within 30s
  L1 hit      ~$0.001 — sqlite-cached verdict, no API call
  L2 hit      ~$0.010 — Anthropic prompt cache, only output tokens billed
  Cold        ~$0.026 — full system + snapshot, ~500 output tokens

Usage:
  from bookmap_mcp.claude_cli import ask_claude
  decision = ask_claude(snap, level, agent_state)

Returns dict shaped:
  {"action": "ENTER_LONG_FADE"|"ENTER_SHORT_FOLLOW"|"ADD"|"EXIT"|"HOLD"|"NO_TRADE",
   "size_tier": "FULL"|"HALF"|"NONE",
   "rationale": "<one sentence>",
   "confidence": 0.0-1.0,
   "_source": "L0"|"L1"|"CLI"|"FALLBACK"}
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from .cache import cache_get, cache_set, make_key, bucket

CLAUDE_BIN = os.environ.get("CLAUDE_CLI", "claude")
CLAUDE_MODEL = os.environ.get("CLAUDE_CLI_MODEL", "sonnet")
CLAUDE_TIMEOUT_SEC = float(os.environ.get("CLAUDE_CLI_TIMEOUT", "15"))

SKILL_PATH = Path(__file__).parent.parent.parent / "skills" / "pax-or" / "SKILL.md"
WEIGHTS_PATH = Path(__file__).parent / "pax_weights.json"


def _build_system_prompt() -> str:
    parts: list = [
        "You are the Pax decision agent. Apply the Pax OR methodology below to the "
        "live snapshot in the user message. Output STRICTLY a single JSON object — "
        "no preamble, no explanation outside the JSON. Schema:\n"
        "{\n"
        '  "action":     "ENTER_LONG_FOLLOW"|"ENTER_LONG_FADE"|"ENTER_SHORT_FOLLOW"|"ENTER_SHORT_FADE"|"ADD"|"EXIT"|"HOLD"|"NO_TRADE",\n'
        '  "size_tier":  "FULL"|"HALF"|"NONE",\n'
        '  "rationale":  "<one sentence, name the dominant signal>",\n'
        '  "confidence": 0.0-1.0\n'
        "}\n\n"
        "Hard rules from the SKILL: never in the middle, only at OR-H/OR-L or "
        "extension rungs (+1/+2/+3, -1/-2/-3). FADE only on rotation against the "
        "level. FOLLOW requires regime confirmation. STAND DOWN on news blackout, "
        "stretched VWAP blowoff, or mismatched VWAP+VP biases.",
    ]
    if SKILL_PATH.exists():
        try: parts.append("\n--- PAX SKILL ---\n" + SKILL_PATH.read_text(encoding="utf-8"))
        except Exception: pass
    if WEIGHTS_PATH.exists():
        try: parts.append("\n--- CURRENT WEIGHTS ---\n" + WEIGHTS_PATH.read_text(encoding="utf-8"))
        except Exception: pass
    return "".join(parts)


def _build_user_message(snap: Dict[str, Any], level: Dict[str, Any],
                        agent_state: Optional[Dict[str, Any]]) -> str:
    flow = snap.get("flow") or {}
    pax  = snap.get("pax") or {}
    conv = snap.get("conviction") or {}
    summary = {
        "alias": snap.get("alias"),
        "level": {
            "label":      level.get("label"),
            "price":      level.get("price"),
            "side":       level.get("side"),
            "distance":   level.get("distance"),
            "decision":   level.get("decision"),
            "confidence": level.get("confidence"),
        },
        "regime":          flow.get("regime"),
        "regime_conf":     flow.get("regimeConfidence"),
        "biasScore":       flow.get("biasScore"),
        "biasTrajectory":  flow.get("biasTrajectory"),
        "vwap_slope":      (flow.get("vwapSlope") or {}).get("label"),
        "vwap_bias":       (snap.get("vwap_bias") or {}).get("label"),
        "vp_bias":         (snap.get("vp_bias") or {}).get("label"),
        "day_type":        (flow.get("ib") or {}).get("dayType"),
        "ib_size":         (flow.get("ib") or {}).get("ibSizeTag"),
        "conviction":      conv.get("score"),
        "conviction_trend": conv.get("trend"),
        "pax_decision":    pax.get("decision"),
        "pax_size_tier":   pax.get("size_tier"),
        "agent_state":     agent_state or {},
    }
    return ("Snapshot context:\n" + json.dumps(summary, indent=2, default=str)
            + "\n\nReturn the JSON decision now.")


def _decision_cache_key(snap: Dict[str, Any], level: Dict[str, Any],
                        agent_state: Optional[Dict[str, Any]]) -> str:
    """Bucket the snapshot so near-identical decisions coalesce in cache.

    Includes current market state (mid, level distance, spread, OR width) at
    coarse bucket sizes so materially different setups don't reuse a prior
    verdict just because the regime/bias inputs happened to agree. Without
    these, price could drift 20pts away from the level and we'd still serve
    the stale "enter at level" decision for 30s.
    """
    flow = snap.get("flow") or {}
    conv = snap.get("conviction") or {}
    ob   = snap.get("orderbook") or {}
    ol   = snap.get("or_levels") or {}
    or_h = ol.get("orHigh")
    or_l = ol.get("orLow")
    or_width = None
    try:
        if or_h is not None and or_l is not None:
            or_width = float(or_h) - float(or_l)
    except (TypeError, ValueError):
        or_width = None

    parts = (
        snap.get("alias"),
        level.get("label"),
        level.get("decision"),
        bucket(level.get("confidence"), 0.1),
        flow.get("regime"),
        bucket(flow.get("regimeConfidence"), 0.1),
        bucket(flow.get("biasScore"), 0.1),
        (flow.get("vwapSlope") or {}).get("label"),
        (snap.get("vwap_bias") or {}).get("label"),
        (snap.get("vp_bias")   or {}).get("label"),
        bucket(conv.get("score"), 0.1),
        (agent_state or {}).get("position_size"),
        # Market-state freshness fields — coarse buckets so identical setups
        # still share keys but a meaningfully different price/spread doesn't.
        bucket(ob.get("mid"), 1.0),
        bucket(level.get("distance"), 1.0),
        bucket(ob.get("spread"), 0.25),
        bucket(or_width, 5.0),
    )
    return make_key("claude.decide", *parts)


def ask_claude(snap: Dict[str, Any], level: Dict[str, Any],
               agent_state: Optional[Dict[str, Any]] = None,
               ttl_sec: float = 30.0) -> Dict[str, Any]:
    """Ask Claude for a decision on a hard case. Cache-backed."""
    key = _decision_cache_key(snap, level, agent_state)
    hit = cache_get(key)
    if hit is not None:
        hit = dict(hit); hit["_source"] = hit.get("_source", "L0_or_L1")
        return hit

    # Run claude CLI: piped stdin = user message, --append-system carries the rules
    sys_prompt = _build_system_prompt()
    user_msg   = _build_user_message(snap, level, agent_state)
    try:
        proc = subprocess.run(
            [CLAUDE_BIN, "-p", user_msg,
             "--model", CLAUDE_MODEL,
             "--append-system-prompt", sys_prompt,
             "--output-format", "text"],
            capture_output=True, text=True,
            timeout=CLAUDE_TIMEOUT_SEC,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        fallback = {
            "action": "NO_TRADE", "size_tier": "NONE",
            "rationale": f"claude CLI unavailable: {type(e).__name__}",
            "confidence": 0.0, "_source": "FALLBACK",
        }
        return fallback

    if proc.returncode != 0:
        sys.stderr.write(f"[claude] non-zero exit: {proc.stderr[:200]}\n")
        return {"action": "NO_TRADE", "size_tier": "NONE",
                "rationale": f"claude CLI rc={proc.returncode}",
                "confidence": 0.0, "_source": "FALLBACK"}

    out = (proc.stdout or "").strip()
    parsed = _extract_json(out)
    if not parsed or "action" not in parsed:
        return {"action": "NO_TRADE", "size_tier": "NONE",
                "rationale": "claude returned unparseable JSON",
                "confidence": 0.0, "_source": "FALLBACK",
                "_raw": out[:200]}
    parsed.setdefault("size_tier", "NONE")
    parsed.setdefault("rationale", "")
    parsed.setdefault("confidence", 0.5)
    parsed["_source"] = "CLI"
    cache_set(key, parsed, ttl_sec, persist=True)
    return parsed


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Find the first {...} object in text and parse it."""
    if not text: return None
    # Greedy fence strip
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    # Find first balanced object
    depth = 0; start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0: start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try: return json.loads(text[start:i+1])
                except json.JSONDecodeError: return None
    return None
