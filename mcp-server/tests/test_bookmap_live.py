"""Phase 7: BookmapLiveAdapter tests.

Critical safety: this adapter must NEVER import the live-order MCP tools
(`bookmap_place_limit_order`, `bookmap_cancel_order`) and must NEVER call
`/place_limit_order` or `/cancel_order`. Verified by source grep so a
future change that accidentally pulls in those symbols will fail the test.

The functional behavior (snapshot emission) is exercised by monkeypatching
`_fetch_snapshot` so we don't require a live Bookmap process.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bookmap_mcp.adapters import BookmapLiveAdapter      # noqa: E402
from bookmap_mcp.adapters.base import DataAdapter        # noqa: E402
import bookmap_mcp.adapters.bookmap_live as bm_live      # noqa: E402


# ─── safety boundary ────────────────────────────────────────────────────

def test_module_source_does_not_reference_live_order_tools():
    """Hard lint: this module must not contain the literal symbol names
    of the live-order MCP tools, nor reference the live order endpoints."""
    src = Path(bm_live.__file__).read_text(encoding="utf-8")
    forbidden = [
        "bookmap_place_limit_order",
        "bookmap_cancel_order",
        "/place_limit_order",
        "/cancel_order",
        "BOOKMAP_ALLOW_TRADING",   # not even checking it here; live-trade gate is a separate concern
    ]
    for token in forbidden:
        assert token not in src, (
            f"adapter source contains forbidden token {token!r} — "
            "live-trade boundary breached")


def test_module_does_not_import_live_trading_helpers():
    """AST-level check on imports — even if a future docstring uses the
    name, the actual import statement is what matters."""
    import ast
    src = Path(bm_live.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported_names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for n in node.names:
                imported_names.append((node.module or "", n.name))
        elif isinstance(node, ast.Import):
            for n in node.names:
                imported_names.append(("", n.name))
    for mod, name in imported_names:
        assert "place_limit_order" not in name, f"forbidden import {mod}.{name}"
        assert "cancel_order"      not in name, f"forbidden import {mod}.{name}"


# ─── protocol + behavior ────────────────────────────────────────────────

def test_adapter_satisfies_protocol():
    a = BookmapLiveAdapter()
    assert isinstance(a, DataAdapter)
    assert a.name == "bookmap_live"


def test_next_snapshot_returns_fetched_snap(monkeypatch):
    """When _fetch_snapshot returns a valid dict, the adapter tags and
    forwards it."""
    fake = {"alias": "NQM6", "health": "ok",
             "book": {"bestBid": 100.0, "bestAsk": 100.5,
                       "mid": 100.25, "spread": 0.5},
             "trades": [], "ts": "2026-05-18T13:30:00+00:00"}
    monkeypatch.setattr(bm_live, "_fetch_snapshot", lambda: fake)
    a = BookmapLiveAdapter(alias="NQM6")
    a.start()
    snap = a.next_snapshot()
    a.stop()
    assert snap is not None
    assert snap["alias"] == "NQM6"
    assert snap["_source"] == "bookmap_live"
    assert "_synthetic" in snap


def test_next_snapshot_returns_none_when_stopped(monkeypatch):
    monkeypatch.setattr(bm_live, "_fetch_snapshot", lambda: {})
    a = BookmapLiveAdapter()
    a.start()
    a.stop()
    assert a.next_snapshot() is None


def test_next_snapshot_records_error_on_bridge_failure(monkeypatch):
    """When the underlying bridge call raises, the adapter records the
    error and returns None instead of crashing."""
    def _boom(): raise RuntimeError("bridge offline")
    monkeypatch.setattr(bm_live, "_fetch_snapshot", _boom)
    a = BookmapLiveAdapter()
    a.start()
    snap = a.next_snapshot()
    a.stop()
    assert snap is None
    h = a.health()
    assert h.status == "error"
    assert any("RuntimeError" in e for e in h.errors)


def test_health_reports_snapshot_count(monkeypatch):
    fake = {"alias": "X", "health": "ok",
             "book": {"mid": 100.0}, "trades": [], "ts": ""}
    monkeypatch.setattr(bm_live, "_fetch_snapshot", lambda: fake)
    a = BookmapLiveAdapter()
    a.start()
    for _ in range(3):
        a.next_snapshot()
    a.stop()
    assert a.health().snapshots_emitted == 3


def test_pax_daemon_supports_bookmap_source(monkeypatch):
    """pax_daemon's --source bookmap routes to BookmapLiveAdapter without
    requiring --path. CSV-only operation is still possible — the daemon
    chooses the adapter from --source, no transitive Bookmap import for
    --source csv runs."""
    import bookmap_mcp.pax_daemon as d
    args = d.build_parser().parse_args(["--source", "bookmap",
                                          "--alias", "NQM6"])
    adapter = d._build_adapter(args.source, args.path, args.alias)
    assert isinstance(adapter, BookmapLiveAdapter)
    assert adapter.alias == "NQM6"
