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
import time
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


def test_agent_feed_prefers_current_autopilot_heartbeats(tmp_path, populated_journal):
    learn = tmp_path / "learn"
    learn.mkdir()
    (learn / "agent-loop.jsonl").write_text(
        json.dumps({"ts_ms": 1, "action": "WAIT", "armed": True}) + "\n" +
        json.dumps({"ts_ms": 2, "heartbeat": True, "action": "NONE",
                    "armed": False}) + "\n",
        encoding="utf-8")
    q = OverviewQueries(populated_journal, learn_dir=learn)
    feed = q.agent_feed()
    assert len(feed) == 1
    assert feed[0]["heartbeat"] is True
    assert feed[0]["armed"] is False


def test_agent_feed_uses_current_contiguous_heartbeat_epoch(tmp_path, populated_journal):
    learn = tmp_path / "learn"
    learn.mkdir()
    (learn / "agent-loop.jsonl").write_text(
        json.dumps({"ts_ms": 1_000, "heartbeat": True, "action": "PLACE",
                    "armed": True, "executed": True}) + "\n" +
        json.dumps({"ts_ms": 400_000, "heartbeat": True, "action": "NONE",
                    "armed": False}) + "\n" +
        json.dumps({"ts_ms": 415_000, "heartbeat": True, "action": "NONE",
                    "armed": False}) + "\n",
        encoding="utf-8")
    q = OverviewQueries(populated_journal, learn_dir=learn)
    feed = q.agent_feed()
    assert [r["ts_ms"] for r in feed] == [400_000, 415_000]
    summary = q.agent_summary()
    assert summary["executed"] == 0
    assert summary["armed"] is False


def test_learning_status_reads_persisted_artifacts(tmp_path, populated_journal):
    learn = tmp_path / "learn"
    learn.mkdir()
    (learn / "scorecard.json").write_text(
        json.dumps({"setups": [{"setup": "A"}]}), encoding="utf-8")
    (learn / "runtime-policy.json").write_text(
        json.dumps({"suggestions": [{"setup": "A", "action": "THROTTLE"}]}),
        encoding="utf-8")
    q = OverviewQueries(populated_journal, learn_dir=learn)
    st = q.learning_status()
    assert st["setup_count"] == 1
    assert st["suggestion_count"] == 1


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


def test_get_root_serves_html_dashboard(live_server):
    host, port = live_server
    status, body = _get(host, port, "/")
    assert status == 200
    assert "<!doctype html>" in body.lower()
    # Modern quant-desk dashboard (2026-05-28 redesign): glass cards, equity
    # chart, agent feed, calibration -- replaced the old <details> sections.
    assert "PAX" in body and "QUANT" in body
    for marker in ('id="stats"', 'id="equity"', 'id="feed"',
                   'id="calib"', 'id="settings"', 'function equityChart'):
        assert marker in body, f"dashboard marker missing: {marker}"


def test_api_status_returns_json(live_server):
    host, port = live_server
    status, body = _get(host, port, "/api/status")
    assert status == 200
    data = json.loads(body)
    assert "snapshot_count" in data
    assert data["snapshot_count"] == 1


def test_api_signals_returns_enveloped_items(live_server):
    # Stage 1: list endpoints now return {items, _meta}.
    host, port = live_server
    status, body = _get(host, port, "/api/signals")
    assert status == 200
    data = json.loads(body)
    assert isinstance(data["items"], list)
    assert len(data["items"]) == 1
    assert data["items"][0]["decision"] == "ENTER_LONG_FOLLOW"
    assert data["_meta"]["source"] == "journal"
    assert "is_stale" in data["_meta"]


def test_api_errors_returns_enveloped_items(live_server):
    host, port = live_server
    status, body = _get(host, port, "/api/errors")
    data = json.loads(body)
    assert any(e["kind"] == "WARN" for e in data["items"])
    assert data["_meta"]["source"] == "journal"


def test_api_learning_status_returns_json(live_server):
    host, port = live_server
    status, body = _get(host, port, "/api/learning_status")
    assert status == 200
    data = json.loads(body)
    assert "setup_count" in data


def test_cron_status_powershell_hidden_on_windows(monkeypatch, queries):
    captured = {}

    class _R:
        returncode = 0
        stdout = '{"installed":false}'
        stderr = ""

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _R()

    monkeypatch.setattr("bookmap_mcp.overview_ui.sys.platform", "win32")
    monkeypatch.setattr("bookmap_mcp.overview_ui.subprocess.CREATE_NO_WINDOW", 123)
    monkeypatch.setattr("bookmap_mcp.overview_ui.subprocess.run", fake_run)
    out = queries.cron_status()
    assert out["available"] is True
    assert captured["kwargs"]["creationflags"] == 123


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


# ─── Stage 1 / 6 / 7: data-truth, health, evaluation ────────────────────

def _learn(tmp_path, **files):
    d = tmp_path / "learn"
    d.mkdir()
    for name, text in files.items():
        (d / name).write_text(text, encoding="utf-8")
    return d


def test_enveloped_dict_endpoint_carries_meta(queries):
    env = queries.enveloped("status")
    assert "_meta" in env
    assert env["_meta"]["source"] == "journal"
    assert "is_stale" in env["_meta"]
    # underlying status fields still present (non-breaking merge)
    assert env["snapshot_count"] == 1


def test_enveloped_list_endpoint_uses_items(queries):
    env = queries.enveloped("signals")
    assert isinstance(env["items"], list)
    assert env["_meta"]["source"] == "journal"


def test_stale_heartbeat_reports_stale_mode(tmp_path, populated_journal):
    # An hour-old heartbeat must NOT be presented as live.
    old = int(time.time() * 1000) - 3_600_000
    learn = _learn(tmp_path, **{"agent-loop.jsonl":
        json.dumps({"ts_ms": old, "heartbeat": True, "armed": True,
                    "action": "PLACE_SHORT"}) + "\n"})
    q = OverviewQueries(populated_journal, learn_dir=learn)
    env = q.enveloped("agent_summary")
    assert env["_meta"]["is_stale"] is True
    assert env["_meta"]["mode"] == "stale"   # not "armed"


def test_fresh_heartbeat_reports_live_mode(tmp_path, populated_journal):
    now = int(time.time() * 1000)
    learn = _learn(tmp_path, **{"agent-loop.jsonl":
        json.dumps({"ts_ms": now - 3_000, "heartbeat": True, "armed": False,
                    "action": "NONE"}) + "\n"})
    q = OverviewQueries(populated_journal, learn_dir=learn)
    env = q.enveloped("agent_summary")
    assert env["_meta"]["is_stale"] is False
    assert env["_meta"]["mode"] == "observe"


def test_agent_feed_items_carry_roles(tmp_path, populated_journal):
    now = int(time.time() * 1000)
    learn = _learn(tmp_path, **{"agent-loop.jsonl":
        json.dumps({"ts_ms": now, "heartbeat": True, "armed": True,
                    "action": "PLACE_SHORT", "governor": "ok",
                    "order": {"side": "SHORT"}, "executed": True}) + "\n"})
    q = OverviewQueries(populated_journal, learn_dir=learn)
    env = q.enveloped("agent_feed")
    assert env["items"][0]["roles"]["risk"]["outcome"] == "ALLOWED"


def test_missing_sim_db_is_safe_and_stale(tmp_path, populated_journal):
    learn = _learn(tmp_path)
    q = OverviewQueries(populated_journal,
                        sim_db_path=tmp_path / "nope.db", learn_dir=learn)
    pos = q.enveloped("position")
    assert pos["_meta"]["is_stale"] is True          # never_updated
    h = q.health()
    assert h["sources"]["sim_db"]["reachable"] is False
    assert h["up"] is True


def test_health_shape_and_live_blocked(tmp_path, populated_journal):
    learn = _learn(tmp_path)
    q = OverviewQueries(populated_journal, learn_dir=learn)
    h = q.health()
    assert h["up"] is True
    assert h["live_blocked"] is True
    for src in ("market", "sim_db", "heartbeat", "scorecard"):
        assert src in h["sources"]
    assert all(src for src in h["stale_sources"])
    assert "heartbeat" in h["stale_sources"]


def test_evaluation_state_envelope_and_block(tmp_path, populated_journal):
    learn = _learn(tmp_path, **{"scorecard.json": json.dumps({"setups": []}),
                                "runtime-policy.json": json.dumps({})})
    q = OverviewQueries(populated_journal, learn_dir=learn)
    env = q.evaluation_state()
    assert env["live_blocked"] is True
    assert env["level"] in ("observe_only", "sim_armed", "sim_restricted",
                            "sim_candidate")
    assert "_meta" in env


def test_kill_switch_surfaces_in_eval_and_health(tmp_path, populated_journal):
    learn = _learn(tmp_path, **{"scorecard.json": json.dumps({"setups": []})})
    (learn / "KILL_SWITCH").write_text("stop", encoding="utf-8")
    q = OverviewQueries(populated_journal, learn_dir=learn)
    assert q.kill_switch_active() is True
    assert q.evaluation_state()["reason"] == "kill_switch_active"
    assert q.health()["kill_switch_active"] is True


def test_health_exposes_risk_halt_fields(tmp_path, populated_journal):
    learn = _learn(tmp_path)
    q = OverviewQueries(populated_journal, learn_dir=learn)
    h = q.health()
    for k in ("risk_halt_active", "risk_halt_code", "risk_halt_message",
              "last_risk_halt_record"):
        assert k in h
    # No live Bookmap feed in this fixture -> heartbeat is stale -> health must
    # report an enforced operational halt, never a null/blank stale source.
    assert h["risk_halt_active"] is True
    for src in h["sources"].values():
        assert src.get("is_stale") is not None


def test_health_reports_kill_switch_halt(tmp_path, populated_journal):
    learn = _learn(tmp_path)
    (learn / "KILL_SWITCH").write_text("stop", encoding="utf-8")
    q = OverviewQueries(populated_journal, learn_dir=learn)
    h = q.health()
    assert h["kill_switch_active"] is True
    assert h["risk_halt_active"] is True
    assert h["risk_halt_code"] == "kill_switch_active"


def test_health_surfaces_last_risk_halt_record(tmp_path, populated_journal):
    now = int(time.time() * 1000)
    learn = _learn(tmp_path, **{"agent-loop.jsonl":
        json.dumps({"ts_ms": now, "heartbeat": True, "armed": True,
                    "action": "PLACE_LONG", "governor": "VETO: max_trades_reached",
                    "risk_halt": "max_trades_reached",
                    "risk_halt_code": "max_trades_reached",
                    "risk_halt_message": "trade cap hit",
                    "executed": False, "order": None}) + "\n"})
    q = OverviewQueries(populated_journal, learn_dir=learn)
    rec = q.health()["last_risk_halt_record"]
    assert rec is not None
    assert rec["risk_halt_code"] == "max_trades_reached"


def test_evaluation_state_includes_operational_blockers(tmp_path, populated_journal):
    # Armed heartbeat but stale market (no live feed) -> restricted with an
    # operational blocker, not promoted.
    now = int(time.time() * 1000)
    learn = _learn(tmp_path, **{
        "agent-loop.jsonl":
            json.dumps({"ts_ms": now, "heartbeat": True, "armed": True,
                        "action": "NONE"}) + "\n",
        "scorecard.json": json.dumps({"setups": []}),
        "runtime-policy.json": json.dumps({})})
    q = OverviewQueries(populated_journal, learn_dir=learn)
    env = q.evaluation_state()
    assert "operational_blockers" in env
    assert "risk_halt_active" in env
    # populated_journal has a stale snapshot timestamp -> market stale blocker.
    codes = [b["code"] for b in env["operational_blockers"]]
    assert "stale_market_data" in codes or "sim_broker_unavailable" in codes
    assert env["level"] in ("observe_only", "sim_restricted")
    assert env["live_blocked"] is True


def test_sim_broker_preflight_missing_db(tmp_path, populated_journal):
    learn = _learn(tmp_path)
    q = OverviewQueries(populated_journal,
                        sim_db_path=tmp_path / "nope.db", learn_dir=learn)
    pf = q.sim_broker_preflight()
    assert pf["openable"] is False and pf["readable"] is False
    assert pf["error"]
    assert q.health()["sources"]["sim_db"]["reachable"] is False


def test_sim_broker_preflight_corrupt_db_unavailable(tmp_path, populated_journal):
    learn = _learn(tmp_path)
    bad = tmp_path / "corrupt.db"
    bad.write_bytes(b"this is definitely not a sqlite database " * 16)
    q = OverviewQueries(populated_journal, sim_db_path=bad, learn_dir=learn)
    pf = q.sim_broker_preflight()
    assert pf["readable"] is False
    assert pf["error"]                       # surfaced, not crashed
    h = q.health()                           # must not crash
    assert h["up"] is True
    assert h["sources"]["sim_db"]["reachable"] is False
    assert h["sources"]["sim_db"]["error"]


def test_sim_broker_preflight_valid_db_ok(tmp_path, populated_journal):
    from bookmap_mcp.sim_engine import SimEngine
    db = tmp_path / "sim.db"
    SimEngine(alias="NQM6", db_path=db, eod_close_hour_ct=None)  # creates schema
    learn = _learn(tmp_path)
    q = OverviewQueries(populated_journal, sim_db_path=db, learn_dir=learn)
    pf = q.sim_broker_preflight()
    assert pf["openable"] is True and pf["readable"] is True
    assert pf["error"] is None
    assert q.health()["sources"]["sim_db"]["reachable"] is True


def test_evaluation_state_blocks_on_broker_unavailable(tmp_path, populated_journal):
    now = int(time.time() * 1000)
    bad = tmp_path / "corrupt.db"
    bad.write_bytes(b"not a database")
    learn = _learn(tmp_path, **{
        "agent-loop.jsonl":
            json.dumps({"ts_ms": now, "heartbeat": True, "armed": True,
                        "action": "NONE"}) + "\n",
        "scorecard.json": json.dumps({"setups": []}),
        "runtime-policy.json": json.dumps({})})
    q = OverviewQueries(populated_journal, sim_db_path=bad, learn_dir=learn)
    env = q.evaluation_state()
    codes = [b["code"] for b in env["operational_blockers"]]
    assert "sim_broker_unavailable" in codes
    assert env["risk_halt_active"] is True
    assert env["live_blocked"] is True


# ── STAGE 5: arming go/no-go + STAGE 3 promotion endpoint ──────────────────

def _fresh_journal(tmp_path):
    """Journal with a snapshot whose ts is NOW -> market source is fresh
    (the shared populated_journal pins ts to 2026-05-18, which is stale)."""
    import datetime
    from bookmap_mcp.journal import Journal
    db = tmp_path / "fresh-journal.db"
    j = Journal(db); j.open()
    j.begin_run(adapter_name="csv", signal_version="v2", weights_hash="x")
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds")
    j.write_snapshot({"alias": "NQM6", "health": "ok", "ts": now_iso,
                      "book": {"mid": 1.0}})
    j.end_run("done"); j.close()
    return db


def _green_arming(tmp_path, *, scorecard=True, kill_switch=False,
                  replay_input=False):
    """A fully-green arming setup: fresh snapshot, fresh heartbeat, valid SIM
    DB, optional scorecard."""
    from bookmap_mcp.sim_engine import SimEngine
    journal = _fresh_journal(tmp_path)
    db = tmp_path / "sim.db"
    SimEngine(alias="NQM6", db_path=db, eod_close_hour_ct=None)
    now = int(time.time() * 1000)
    hb = {"ts_ms": now, "heartbeat": True, "armed": False, "action": "NONE"}
    if replay_input:
        hb["replay_input"] = {"version": 1, "snapshot": {"health": "ok"},
                              "status": {"position": {"size": 0}}, "now_ms": now,
                              "market_age_sec": 1.0, "heartbeat_age_sec": 1.0,
                              "sim_broker_ok": True, "kill_switch_active": False}
    files = {"agent-loop.jsonl": json.dumps(hb) + "\n"}
    if scorecard:
        files["scorecard.json"] = json.dumps(
            {"setups": [{"setup": "A|LONG|OR-H|ETH", "n": 5,
                         "mean_realized_r": 0.2, "hit_rate": 0.6}]})
    learn = _learn(tmp_path, **files)
    if kill_switch:
        (learn / "KILL_SWITCH").write_text("stop", encoding="utf-8")
    return OverviewQueries(journal, sim_db_path=db, learn_dir=learn)


def test_arming_check_all_green_can_arm(tmp_path, populated_journal):
    q = _green_arming(tmp_path)
    a = q.arming_check()
    assert a["can_arm"] is True
    assert a["blocking_codes"] == []
    assert a["live_blocked"] is True


def test_arming_check_kill_switch_blocks(tmp_path, populated_journal):
    q = _green_arming(tmp_path, kill_switch=True)
    a = q.arming_check()
    assert a["can_arm"] is False
    assert "kill_switch_absent" in a["blocking_codes"]
    assert a["live_blocked"] is True


def test_arming_check_stale_market_blocks(tmp_path, populated_journal):
    # Empty journal => no snapshots => market never_updated => stale.
    from bookmap_mcp.journal import Journal
    from bookmap_mcp.sim_engine import SimEngine
    empty = tmp_path / "empty.db"
    j = Journal(empty); j.open()
    j.begin_run(adapter_name="csv", signal_version="v2", weights_hash="x")
    j.end_run("done"); j.close()
    db = tmp_path / "sim.db"
    SimEngine(alias="NQM6", db_path=db, eod_close_hour_ct=None)
    now = int(time.time() * 1000)
    learn = _learn(tmp_path, **{"agent-loop.jsonl":
        json.dumps({"ts_ms": now, "heartbeat": True, "action": "NONE"}) + "\n"})
    q = OverviewQueries(empty, sim_db_path=db, learn_dir=learn)
    a = q.arming_check()
    assert a["can_arm"] is False
    assert "market_data_fresh" in a["blocking_codes"]
    assert a["live_blocked"] is True


def test_arming_check_sim_broker_unavailable_blocks(tmp_path, populated_journal):
    bad = tmp_path / "corrupt.db"
    bad.write_bytes(b"not a database")
    now = int(time.time() * 1000)
    learn = _learn(tmp_path, **{"agent-loop.jsonl":
        json.dumps({"ts_ms": now, "heartbeat": True, "action": "NONE"}) + "\n"})
    q = OverviewQueries(populated_journal, sim_db_path=bad, learn_dir=learn)
    a = q.arming_check()
    assert a["can_arm"] is False
    assert "sim_broker_ok" in a["blocking_codes"]


def test_arming_check_missing_scorecard_is_warning_not_blocker(tmp_path,
                                                               populated_journal):
    q = _green_arming(tmp_path, scorecard=False)
    a = q.arming_check()
    assert a["can_arm"] is True                    # warning does not block
    assert "scorecard_present" in a["warnings"]
    assert "scorecard_present" not in a["blocking_codes"]


def test_arming_check_is_read_only_no_probe_file(tmp_path):
    q = _green_arming(tmp_path)
    before = sorted(p.name for p in q.learn_dir.iterdir())
    a = q.arming_check()
    after = sorted(p.name for p in q.learn_dir.iterdir())
    # No probe file created and no files added/removed -> endpoint is read-only.
    assert ".arming_write_test" not in after
    assert before == after
    sr = [c for c in a["checks"] if c["code"] == "session_report_writable"][0]
    assert sr["status"] in ("pass", "warn")
    assert "session_report_writable" not in a["blocking_codes"]


def test_arming_check_does_not_create_missing_learn_dir(tmp_path):
    from bookmap_mcp.journal import Journal
    db = tmp_path / "j.db"
    j = Journal(db); j.open()
    j.begin_run(adapter_name="csv", signal_version="v2", weights_hash="x")
    j.end_run("done"); j.close()
    missing = tmp_path / "no_such_learn"
    q = OverviewQueries(db, learn_dir=missing)
    a = q.arming_check()                       # must not write anything
    assert not missing.exists()
    sr = [c for c in a["checks"] if c["code"] == "session_report_writable"][0]
    assert sr["status"] == "warn"              # never claims writable by writing


def test_arming_check_bookmap_closed_is_nogo_not_exception(tmp_path):
    # Fresh empty journal, no learn files, no sim db -> all-stale weekend state.
    from bookmap_mcp.journal import Journal
    db = tmp_path / "empty.db"
    j = Journal(db); j.open()
    j.begin_run(adapter_name="csv", signal_version="v2", weights_hash="x")
    j.end_run("done"); j.close()
    q = OverviewQueries(db, learn_dir=tmp_path / "learn")
    a = q.arming_check()                            # must not raise
    assert a["can_arm"] is False
    assert a["live_blocked"] is True


def test_promotion_report_endpoint_enveloped(tmp_path, populated_journal):
    learn = _learn(tmp_path, **{"scorecard.json": json.dumps(
        {"min_samples": 30,
         "setups": [{"setup": "A|LONG|OR-H|ETH", "n": 40,
                     "mean_realized_r": 0.3, "hit_rate": 0.6}]})})
    q = OverviewQueries(populated_journal, learn_dir=learn)
    env = q.enveloped("promotion_report")
    assert "_meta" in env
    assert env["live_blocked"] is True
    assert env["setups"][0]["promotion_status"] == "candidate"


def test_api_arming_check_endpoint_200(live_server):
    host, port = live_server
    status, body = _get(host, port, "/api/arming_check")
    assert status == 200
    data = json.loads(body)
    assert data["live_blocked"] is True
    assert "can_arm" in data and "checks" in data


def test_api_promotion_report_endpoint_200(live_server):
    host, port = live_server
    status, body = _get(host, port, "/api/promotion_report")
    assert status == 200
    data = json.loads(body)
    assert data["live_blocked"] is True
    assert "_meta" in data


def test_api_health_endpoint_200(live_server):
    host, port = live_server
    status, body = _get(host, port, "/api/health")
    assert status == 200
    data = json.loads(body)
    assert data["up"] is True
    assert data["live_blocked"] is True


def test_api_evaluation_state_endpoint_200(live_server):
    host, port = live_server
    status, body = _get(host, port, "/api/evaluation_state")
    assert status == 200
    data = json.loads(body)
    assert data["live_blocked"] is True


def test_html_page_constant_has_dashboard_panels():
    """Independent check on the page template — the quant-desk panels and the
    self-contained SVG equity chart renderer are present."""
    for marker in ('id="stats"', 'id="equity"', 'id="feed"', 'id="calib"',
                   'id="settings"', '/api/cron_status',
                   '/api/learning_status',
                   'id="lessons"', 'id="working"', 'id="fills"',
                   'function equityChart', 'function bars('):
        assert marker in _PAGE_HTML, f"missing dashboard panel: {marker}"
    # Charts are hand-drawn SVG -- no external chart library dependency.
    assert "<svg" in _PAGE_HTML
    assert "<script src=" not in _PAGE_HTML, "must stay self-contained (no CDN)"


# ── STAGE 4: replay readiness in health + arming_check ─────────────────────

def test_health_exposes_replay_readiness(tmp_path):
    q = _green_arming(tmp_path, replay_input=True)
    h = q.health()
    assert "replay_readiness" in h
    rr = h["replay_readiness"]
    assert rr["replay_input_recent"] >= 1
    assert rr["replay_input_pct_recent"] == 100.0
    assert rr["replay_input_version"] == 1


def test_health_replay_readiness_old_logs_no_crash(tmp_path):
    q = _green_arming(tmp_path, replay_input=False)
    h = q.health()                              # old logs, no replay_input
    rr = h["replay_readiness"]
    assert rr["replay_input_recent"] == 0
    assert rr["replay_input_pct_recent"] == 0.0
    assert rr["replay_input_version"] is None


def test_arming_check_warns_when_replay_input_missing(tmp_path):
    q = _green_arming(tmp_path, replay_input=False)
    a = q.arming_check()
    assert "replay_input_present" in a["warnings"]
    assert "replay_input_present" not in a["blocking_codes"]
    assert a["can_arm"] is True                 # auditability is not a blocker


def test_arming_check_replay_input_present_passes(tmp_path):
    q = _green_arming(tmp_path, replay_input=True)
    a = q.arming_check()
    chk = [c for c in a["checks"] if c["code"] == "replay_input_present"][0]
    assert chk["status"] == "pass"
    assert "replay_input_present" not in a["warnings"]


# ── STAGE 3: evidence endpoints (read-only) ────────────────────────────────

def test_evidence_report_missing_files_is_no_data(tmp_path, populated_journal):
    learn = tmp_path / "learn"; learn.mkdir()
    q = OverviewQueries(populated_journal, learn_dir=learn)
    rep = q.evidence_report()
    assert rep["evidence_grade"] == "no_data"
    assert rep["live_blocked"] is True


def test_evidence_report_candidate_grade(tmp_path, populated_journal):
    now = int(time.time() * 1000)
    ri = {"version": 1, "snapshot": {"health": "ok"},
          "status": {"position": {"size": 0}}, "now_ms": now,
          "market_age_sec": 1.0, "heartbeat_age_sec": 1.0,
          "sim_broker_ok": True, "kill_switch_active": False}
    learn = _learn(tmp_path, **{
        "agent-loop.jsonl": json.dumps(
            {"ts_ms": now, "heartbeat": True, "action": "NONE",
             "replay_input": ri}) + "\n",
        "scorecard.json": json.dumps(
            {"min_samples": 30, "setups": [{"setup": "A|LONG|OR-H|ETH", "n": 40,
             "mean_realized_r": 0.3, "hit_rate": 0.62}]})})
    q = OverviewQueries(populated_journal, learn_dir=learn)
    rep = q.evidence_report()
    assert rep["evidence_grade"] == "promotion_candidate"
    assert rep["setup_evidence"][0]["recommended_action"] == "candidate_for_paper_focus"


def test_evidence_report_no_file_writes_from_get(tmp_path, populated_journal):
    learn = tmp_path / "learn"; learn.mkdir()
    q = OverviewQueries(populated_journal, learn_dir=learn)
    before = sorted(p.name for p in learn.iterdir())
    q.evidence_report()
    q.health()
    after = sorted(p.name for p in learn.iterdir())
    assert before == after            # read-only: no files created


def test_health_includes_compact_evidence(tmp_path, populated_journal):
    learn = tmp_path / "learn"; learn.mkdir()
    q = OverviewQueries(populated_journal, learn_dir=learn)
    h = q.health()
    assert "evidence" in h
    for k in ("evidence_grade", "replay_input_pct", "candidate_setup_count",
              "evidence_blockers"):
        assert k in h["evidence"]
    assert h["evidence"]["evidence_grade"] == "no_data"


def test_api_evidence_report_endpoint_200(live_server):
    host, port = live_server
    status, body = _get(host, port, "/api/evidence_report")
    assert status == 200
    data = json.loads(body)
    assert data["live_blocked"] is True
    assert data["evidence_grade"] in (
        "no_data", "logging_only", "replayable", "outcome_linked",
        "promotion_candidate")
