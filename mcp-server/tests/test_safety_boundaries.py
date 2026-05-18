"""Phase 0 safety-boundary tests.

Verify the Python-side guards on the two live-trade MCP tools:
  - confirm=True is mandatory
  - BOOKMAP_ALLOW_TRADING == "1" must be set in the Bookmap process env

Either gate alone is sufficient to refuse a live order, so a single buggy
tool call or a misconfigured environment cannot route through.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bookmap_mcp.server as srv   # noqa: E402


def test_live_trade_guard_refuses_without_confirm(monkeypatch):
    """confirm=False (the default) must always raise, even if the env var is set."""
    monkeypatch.setenv("BOOKMAP_ALLOW_TRADING", "1")
    with pytest.raises(RuntimeError, match="confirm=True"):
        srv._require_live_trade_allowed(confirm=False, op="place_limit_order")


def test_live_trade_guard_refuses_without_env(monkeypatch):
    """confirm=True alone is not enough; env must also be set."""
    monkeypatch.delenv("BOOKMAP_ALLOW_TRADING", raising=False)
    with pytest.raises(RuntimeError, match="BOOKMAP_ALLOW_TRADING"):
        srv._require_live_trade_allowed(confirm=True, op="place_limit_order")


def test_live_trade_guard_refuses_with_wrong_env_value(monkeypatch):
    """Only the literal string '1' opens the env gate."""
    monkeypatch.setenv("BOOKMAP_ALLOW_TRADING", "true")
    with pytest.raises(RuntimeError, match="BOOKMAP_ALLOW_TRADING"):
        srv._require_live_trade_allowed(confirm=True, op="place_limit_order")
    monkeypatch.setenv("BOOKMAP_ALLOW_TRADING", "yes")
    with pytest.raises(RuntimeError, match="BOOKMAP_ALLOW_TRADING"):
        srv._require_live_trade_allowed(confirm=True, op="cancel_order")


def test_live_trade_guard_passes_with_both_gates_open(monkeypatch):
    """confirm=True AND BOOKMAP_ALLOW_TRADING=1 → no exception raised."""
    monkeypatch.setenv("BOOKMAP_ALLOW_TRADING", "1")
    # Should return None and not raise.
    srv._require_live_trade_allowed(confirm=True, op="place_limit_order")
    srv._require_live_trade_allowed(confirm=True, op="cancel_order")


def test_live_trade_guard_message_includes_op_name(monkeypatch):
    """Error messages must name the operation for clarity."""
    monkeypatch.delenv("BOOKMAP_ALLOW_TRADING", raising=False)
    with pytest.raises(RuntimeError, match="cancel_order"):
        srv._require_live_trade_allowed(confirm=True, op="cancel_order")
    with pytest.raises(RuntimeError, match="place_limit_order"):
        srv._require_live_trade_allowed(confirm=False, op="place_limit_order")
