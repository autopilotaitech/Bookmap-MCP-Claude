"""Phase 4: overview UI tests.

Cover the read-only HTTP surface: every JSON endpoint returns a sensible
shape against a journal that has at least one run, one snapshot, and one
signal; the HTML page renders 9 <details> sections with the user-required
drop-down arrows; write methods (POST/PUT/DELETE/PATCH) all return 405.
"""

from __future__ import annotations

import json
import sys
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bookmap_mcp.journal import Journal              # noqa: E402
from bookmap_mcp.overview_ui import (                # noqa: E402
    OverviewQueries,
    _build_handler,
    _PAGE_HTML,
)


@pytest.fixture
def populated_journal(tmp_path):
    """Build a journal with one run + one snapshot + one signal +
    one heartbeat + one error event."""
    db = tmp_path / "journal.db"
    j = Journal(db)
    j.open()
    j.begin_run(adapter_name="csv_replay",
                 signal_version="anchored_multi_source_v2",
                 weights_hash="abc123")
    snap = {
        "alias": "NQM6", "health": "ok",
        "ts": "2026-05-18T13:30:00+00:00",
        "book": {"bestBid": 20049.75, "bestAsk": 20050.25,
                  "mid": 20050.0, "spread": 0.5},
        "vwap_obj": {"vwap": 20040.0, "stddev": 8.0},
        "or_row": {"orHigh": "20100.0", "orLow": "20000.0"},
        "flow": {"regime": "TRENDING_UP", "biasScore": 0.5,
                  "biasTrajectory": "RISING"},
        "or_levels": {"levels": [
            {"label": "OR-H", "composite":
                {"score": 0.6, "direction": "FOLLOW_LONG"}}
        ]},
        "conviction": {"score": 0.5, "trajectory": "RISING"},
    }
    j.write_snapshot(snap)
    j.write_signal(snap, {
        "decision": "ENTER_LONG_FOLLOW", "size_tier": "FULL",
        "confidence": 0.72, "level_label": "OR-H",
        "entry": 20100.0, "components": {}, "reasons": ["test reason"],
    })
    j.write_event("WARN", "test", "stale tape")
    from bookmap_mcp.adapters.base import AdapterHealth
    j.write_adapter_health(AdapterHealth(status="ok", detail="streaming",
                                            snapshots_emitted=1))
    j.end_run("test fixture")
    j.close()
    return db


@pytest.fixture
def queries(populated_journal):
    return OverviewQueries(populated_journal)


# ─── OverviewQueries unit ────────────────────────────────────────────────

def test_status_reports_run_health_and_counts(queries):
    s = queries.status()
    assert "error" not in s
    assert s["run"]["adapter_name"] == "csv_replay"
    assert s["snapshot_count"] == 1
    assert s["signal_count"] == 1
    assert s["health"]["status"] == "ok"


def test_latest_signals_returns_inserted_row(queries):
    rows = queries.latest_signals(limit=10)
    assert len(rows) == 1
    assert rows[0]["decision"] == "ENTER_LONG_FOLLOW"
    assert rows[0]["level_label"] == "OR-H"
    assert rows[0]["composite_dir"] == "FOLLOW_LONG"
    assert rows[0]["conviction_trajectory"] == "RISING"


def test_setup_winrates_aggregates_by_decision_level(queries):
    rows = queries.setup_winrates()
    assert len(rows) == 1
    assert rows[0]["decision"] == "ENTER_LONG_FOLLOW"
    assert rows[0]["level_label"] == "OR-H"
    assert rows[0]["n"] == 1


def test_errors_includes_warn(queries):
    errs = queries.errors()
    kinds = {e["kind"] for e in errs}
    assert "WARN" in kinds


def test_pnl_summary_zero_when_no_daily_stats(queries):
    p = queries.pnl_summary()
    assert p["total"] == 0
    assert p["wins"] == 0


# ─── HTTP server smoke ──────────────────────────────────────────────────

@pytest.fixture
def live_server(populated_journal):
    """Start a real ThreadingHTTPServer on an ephemeral port. Yields the
    (host, port) tuple so tests can hit it via http.client."""
    queries = OverviewQueries(populated_journal)
    Handler = _build_handler(queries)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    host, port = srv.server_address
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield host, port
    srv.shutdown()
    srv.server_close()


def _get(host, port, path, method="GET"):
    conn = HTTPConnection(host, port, timeout=5.0)
    try:
        conn.request(method, path)
        resp = conn.getresponse()
        return resp.status, resp.read().decode("utf-8")
    finally:
        conn.close()


def test_get_root_serves_html_with_details_sections(live_server):
    host, port = live_server
    status, body = _get(host, port, "/")
    assert status == 200
    assert "<!doctype html>" in body.lower()
    # Native <details>/<summary> = the user's drop-down arrow requirement.
    assert body.count("<details") >= 9, (
        f"expected at least 9 collapsible sections, got {body.count('<details')}")
    assert "<summary>" in body
    # localStorage persistence wire-up.
    assert "localStorage" in body


def test_api_status_returns_json(live_server):
    host, port = live_server
    status, body = _get(host, port, "/api/status")
    assert status == 200
    data = json.loads(body)
    assert "snapshot_count" in data
    assert data["snapshot_count"] == 1


def test_api_signals_returns_array(live_server):
    host, port = live_server
    status, body = _get(host, port, "/api/signals")
    assert status == 200
    data = json.loads(body)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["decision"] == "ENTER_LONG_FOLLOW"


def test_api_errors_returns_array(live_server):
    host, port = live_server
    status, body = _get(host, port, "/api/errors")
    data = json.loads(body)
    assert any(e["kind"] == "WARN" for e in data)


def test_post_to_root_is_rejected(live_server):
    host, port = live_server
    status, _ = _get(host, port, "/", method="POST")
    assert status == 405


def test_put_delete_patch_all_rejected(live_server):
    host, port = live_server
    for method in ("PUT", "DELETE", "PATCH"):
        status, _ = _get(host, port, "/", method=method)
        assert status == 405, f"{method} should be rejected, got {status}"


def test_unknown_path_returns_404(live_server):
    host, port = live_server
    status, _ = _get(host, port, "/api/does_not_exist")
    assert status == 404


def test_html_page_constant_has_details_arrows():
    """Independent check on the page template — chevrons + summary elements
    are present for every section the daemon writes."""
    sections = ["sec-status", "sec-pnl", "sec-position", "sec-working",
                 "sec-signals", "sec-setups", "sec-daily", "sec-errors",
                 "sec-freshness"]
    for sec in sections:
        assert f'id="{sec}"' in _PAGE_HTML, f"missing section {sec}"
    # Each section uses <details>/<summary> — native HTML chevron + collapse.
    assert _PAGE_HTML.count("<summary>") == len(sections)
