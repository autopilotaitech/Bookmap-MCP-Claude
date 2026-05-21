"""Pin that _BASE_PREAMBLE teaches Pax AI to describe thesis state.

ADDITIVE invariants — must NOT break existing pinned tests in test_prompts.py.
"""
from __future__ import annotations

import re

from pax_ai.prompts import render_system_prompt


def test_preamble_lists_institutional_thesis_fields():
    body = render_system_prompt()
    for kw in ("institutional_thesis", "state", "thesis",
               "liquidity_quality", "aggressor_flow",
               "book_state", "execution_read"):
        assert kw in body, f"missing thesis field keyword: {kw}"


def test_preamble_describes_state_codes_explicitly():
    body = render_system_prompt()
    for code in ("APPROACHING", "TOUCHED", "ACCEPTED_ABOVE", "ACCEPTED_BELOW",
                 "REJECTED", "FAILED_BREAK", "RETEST_HOLD", "RETEST_FAIL",
                 "INVALIDATED"):
        assert code in body, f"state code {code} missing from preamble"


def test_preamble_describes_thesis_codes_explicitly():
    body = render_system_prompt()
    for code in ("ACCEPTANCE_LONG", "ACCEPTANCE_SHORT",
                 "REJECTION_LONG", "REJECTION_SHORT",
                 "ABSORPTION_FADE", "ICEBERG_DEFENSE",
                 "STOP_SWEEP_CONTINUATION", "STOP_SWEEP_FAILURE"):
        assert code in body, f"thesis code {code} missing"


def test_preamble_describes_execution_read_codes():
    body = render_system_prompt()
    for code in ("WAIT_FOR_CONFIRM", "PAY_FOR_TRADE",
                 "SCRATCH_READY", "STAND_DOWN"):
        assert code in body


def test_preamble_describes_liquidity_quality_codes():
    body = render_system_prompt()
    for code in ("SPOOF_RISK", "ICEBERG_DEFENDED", "ABSORPTION"):
        assert code in body


def test_preamble_forbids_naked_buy_sell_recommendation_language():
    """The thesis rule must explicitly forbid raw 'buy'/'sell' as a recommendation."""
    body = render_system_prompt()
    assert re.search(r"do NOT (?:say|use)[^.\n]*buy", body, re.IGNORECASE), \
        "preamble must explicitly forbid raw buy/sell recommendations"


def test_preamble_still_passes_existing_anchor_invariant():
    """Don't regress the existing pinned rules."""
    body = render_system_prompt()
    assert "anchorMode" in body
    assert "LIVE" in body
    # Don't reintroduce stale RTH active-context claims.
    assert "For NQ that is 08:30" not in body
    assert "Regular Trading Hours" not in body
