"""Tests for read-side role annotation (Stage 5)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bookmap_mcp import pax_roles  # noqa: E402

_EXECUTED = {
    "ts_ms": 1, "armed": True, "mid": 30395.5, "level": "OR-H",
    "stype": "RTH", "baseline_state": "PLACE", "setup_type": "OR_SWEEP_REJECT",
    "action": "PLACE_SHORT", "governor": "ok", "expectancy": -0.01,
    "expectancy_source": "learned:n=37", "order": {"side": "SHORT", "qty": 2},
    "exec": {"ok": True}, "executed": True, "model": "claude-haiku-4-5",
    "live_trading_env_scrubbed": True,
}

_VETO = {
    "ts_ms": 2, "armed": True, "action": "ENTER_LONG",
    "governor": "VETO: in position", "order": None, "executed": False,
    "raw_action": "ENTER_LONG", "confidence": 0.7, "deviates": True,
}


def test_record_has_all_role_sections():
    roles = pax_roles.annotate_roles(_EXECUTED)
    for section in ("observer", "strategist", "risk", "executor", "auditor"):
        assert section in roles
    assert roles["risk"]["outcome"] == "ALLOWED"
    assert roles["risk"]["mode"] == "armed"
    assert roles["executor"]["executed"] is True


def test_veto_record_marks_blocked_and_keeps_auditor():
    # Risk block still logs an auditor record (Stage 5 requirement).
    roles = pax_roles.annotate_roles(_VETO)
    assert roles["risk"]["outcome"] == "BLOCKED"
    assert roles["executor"]["executed"] is False
    assert "ts_ms" in roles["auditor"]


def test_llm_influence_flag():
    assert pax_roles.llm_influenced(_VETO) is True       # decide_cycle fields
    assert pax_roles.llm_influenced(_EXECUTED) is False  # pure rule heartbeat
    roles = pax_roles.annotate_roles(_EXECUTED)
    assert roles["auditor"]["deterministic_path"] is True


def test_annotate_handles_garbage_without_crashing():
    # LLM/parse failure shapes must not crash the read path.
    assert pax_roles.annotate_roles({"error": "boom"})  # returns a dict
    assert pax_roles.annotate_roles(None) == {}
    assert pax_roles.annotate_record(None) is None
    enriched = pax_roles.annotate_record(_EXECUTED)
    assert "roles" in enriched and enriched is not _EXECUTED
