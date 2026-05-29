import argparse
import json

from bookmap_mcp import pax_agent_tick as T
from bookmap_mcp import pax_sim_agent
from bookmap_mcp import pax_sim_tools


def test_parser_defaults_to_observe_mode():
    args = T.build_parser().parse_args([])
    assert args.armed is False
    assert args.observe is False
    assert args.dashboard_url == pax_sim_agent.DASHBOARD_URL
    assert args.timeout_sec == pax_sim_agent.AGENT_CALL_TIMEOUT


def test_run_once_observe_logs_and_does_not_execute(monkeypatch, tmp_path):
    snap = {
        "health": "ok",
        "alias": "NQ",
        "book": {"mid": 100.0},
        "session": {"anchorMode": "LIVE", "code": "ACTIVE"},
        "or_day_ledger": {"session_type": "ETH"},
        "gates": {"news": {"blocked": False}},
        "flow": {},
        "or_levels": {
            "orHigh": 101.0,
            "orLow": 99.0,
            "orWidthPts": 2.0,
            "inProximity": False,
            "middleLock": False,
            "levels": [],
        },
    }
    monkeypatch.setattr(T.pax_sim_agent, "_fetch_snapshot", lambda url: snap)
    monkeypatch.setattr(T.pax_sim_tools, "sim_status",
                        lambda alias=None: {"position": {"size": 0}, "working": []})
    monkeypatch.setattr(T.pax_sim_agent, "decide_cycle",
                        lambda *a, **k: {"action": "WAIT", "executed": False,
                                         "dry": k.get("dry")})
    monkeypatch.setattr(pax_sim_tools, "LEARN_DIR", tmp_path)
    monkeypatch.setattr(pax_sim_agent, "AGENT_LOG", tmp_path / "agent-loop.jsonl")

    args = argparse.Namespace(
        dashboard_url="http://dash",
        alias=None,
        model="model",
        armed=False,
        observe=True,
        timeout_sec=1.0,
    )
    rec = T.run_once(args)

    assert rec["action"] == "WAIT"
    assert rec["dry"] is True
    assert rec["armed"] is False
    assert rec["alias"] == "NQ"
    rows = (tmp_path / "agent-loop.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0])["action"] == "WAIT"


def test_run_once_armed_passes_dry_false(monkeypatch, tmp_path):
    monkeypatch.setattr(T.pax_sim_agent, "_fetch_snapshot",
                        lambda url: {"alias": "NQ"})
    monkeypatch.setattr(T.pax_sim_tools, "sim_status",
                        lambda alias=None: {"position": {"size": 0}, "working": []})
    monkeypatch.setattr(T.pax_sim_agent, "decide_cycle",
                        lambda *a, **k: {"action": "ENTER_LONG", "dry": k.get("dry")})
    monkeypatch.setattr(pax_sim_tools, "LEARN_DIR", tmp_path)
    monkeypatch.setattr(pax_sim_agent, "AGENT_LOG", tmp_path / "agent-loop.jsonl")

    args = argparse.Namespace(
        dashboard_url="http://dash",
        alias="NQ",
        model="model",
        armed=True,
        observe=False,
        timeout_sec=1.0,
    )
    rec = T.run_once(args)

    assert rec["dry"] is False
    assert rec["armed"] is True


def test_run_once_scrubs_live_trading_env_before_sim(monkeypatch, tmp_path):
    monkeypatch.setenv("BOOKMAP_ALLOW_TRADING", "1")
    monkeypatch.setattr(T.pax_sim_agent, "_fetch_snapshot",
                        lambda url: {"alias": "NQ"})

    def fake_status(alias=None):
        assert T.os.environ.get("BOOKMAP_ALLOW_TRADING") == ""
        return {"position": {"size": 0}, "working": []}

    monkeypatch.setattr(T.pax_sim_tools, "sim_status", fake_status)
    monkeypatch.setattr(T.pax_sim_agent, "decide_cycle",
                        lambda *a, **k: {"action": "WAIT", "dry": k.get("dry")})
    monkeypatch.setattr(pax_sim_tools, "LEARN_DIR", tmp_path)
    monkeypatch.setattr(pax_sim_agent, "AGENT_LOG", tmp_path / "agent-loop.jsonl")

    args = argparse.Namespace(
        dashboard_url="http://dash",
        alias="NQ",
        model="model",
        armed=True,
        observe=False,
        timeout_sec=1.0,
    )
    rec = T.run_once(args)

    assert rec["live_trading_env_scrubbed"] is True
    assert rec["dry"] is False
