"""Pin that signal_engine re-exports the institutional-thesis surface."""
from __future__ import annotations


def test_signal_engine_exports_thesis_helpers():
    from bookmap_mcp import signal_engine as se
    for name in (
        "compute_institutional_thesis",
        "_thesis_classify_touch_state",
        "_thesis_liquidity_quality",
        "_thesis_select_thesis",
        "_thesis_aggressor_flow",
        "_thesis_book_state",
        "_thesis_execution_read",
        "_thesis_confidence",
        "_thesis_invalidations",
        "_thesis_for_level",
        "_thesis_micro_at_price",
        "_LEVEL_TOUCH_STATE",
        "_TOUCH_HISTORY_DEPTH",
        "_TOUCH_TICKS",
        "_APPROACH_PROX_PTS",
        "_ACCEPT_HOLD_POLLS",
        "_REJECT_BACKOFF_PTS",
        "_THESIS_STATE_CODES",
        "_THESIS_THESIS_CODES",
        "_THESIS_LIQ_CODES",
        "_THESIS_AGG_CODES",
        "_THESIS_BOOK_CODES",
        "_THESIS_EXEC_CODES",
    ):
        assert hasattr(se, name), f"signal_engine missing {name}"
        assert name in se.__all__, f"signal_engine __all__ missing {name}"
