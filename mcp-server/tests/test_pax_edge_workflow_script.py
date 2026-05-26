"""Static guardrails for the Pax edge workflow helper.

The helper is intentionally a research-chain wrapper around feature-bus
forecast/outcome data. It must not invoke the daemon-journal outcome backfill,
because calibration consumes feature-bus ``trade_outcomes`` via ``--bus-db``.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "pax-edge-workflow.ps1"
DOC = ROOT / "docs" / "pax-edge-workflow.md"


def test_edge_workflow_uses_feature_bus_trade_outcomes_not_journal_outcomes():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "bookmap_mcp.journal_outcomes" not in text
    assert "SkipJournalOutcomes" not in text
    assert "--bus-db" in text
    assert "trade_outcomes" in text
    assert "daemon journal" in text


def test_edge_workflow_docs_match_outcome_source():
    text = DOC.read_text(encoding="utf-8")

    assert "journal_outcomes` is intentionally not part" in text
    assert "feature-bus" in text
    assert "trade_outcomes" in text
    assert "--bus-db" in text
