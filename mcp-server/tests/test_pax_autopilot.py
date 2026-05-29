import argparse
import json

from bookmap_mcp import pax_autopilot as P
from bookmap_mcp import pax_sim_agent


def test_parser_defaults_to_observe_mode():
    args = P.build_parser().parse_args([])
    assert args.armed is False
    assert args.observe is False
    assert args.dashboard_url == pax_sim_agent.DASHBOARD_URL
    assert args.interval_sec == 15.0
    assert args.llm_every == 6


def test_run_forever_starts_single_loop_and_scrubs_env(monkeypatch, capsys):
    monkeypatch.setenv("BOOKMAP_ALLOW_TRADING", "1")
    calls = {}

    class FakeLoop:
        def __init__(self, **kwargs):
            calls["init"] = kwargs
            self._status = {"running": False, "armed": False, "cycles": 0}

        def start(self, armed=False):
            calls["armed"] = armed
            self._status = {"running": True, "armed": armed, "cycles": 0}
            return dict(self._status)

        def status(self):
            return dict(self._status)

        def stop(self):
            self._status["running"] = False
            return dict(self._status)

    monkeypatch.setattr(P.pax_sim_agent, "AgentLoop", FakeLoop)
    monkeypatch.setattr(P.time, "sleep",
                        lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt()))

    args = argparse.Namespace(
        dashboard_url="http://dash",
        alias="NQ",
        model="model",
        interval_sec=5.0,
        llm_every=3,
        armed=True,
        observe=False,
        status_every_sec=1.0,
    )

    rc = P.run_forever(args)
    out = [json.loads(line) for line in capsys.readouterr().out.splitlines()]

    assert rc == 130
    assert calls["init"]["dashboard_url"] == "http://dash"
    assert calls["init"]["interval_sec"] == 5.0
    assert calls["armed"] is True
    assert P.os.environ.get("BOOKMAP_ALLOW_TRADING") == ""
    assert out[0]["live_trading_env_scrubbed"] is True
    assert out[-1]["stopping"] is True


def test_observe_overrides_armed(monkeypatch):
    calls = {}

    class FakeLoop:
        def __init__(self, **_kwargs):
            pass

        def start(self, armed=False):
            calls["armed"] = armed
            return {"running": True, "armed": armed, "cycles": 0}

        def status(self):
            return {"running": True, "armed": calls["armed"], "cycles": 0}

        def stop(self):
            return {"running": False, "armed": calls["armed"], "cycles": 0}

    monkeypatch.setattr(P.pax_sim_agent, "AgentLoop", FakeLoop)
    monkeypatch.setattr(P.time, "sleep",
                        lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt()))

    args = argparse.Namespace(
        dashboard_url="http://dash",
        alias=None,
        model="model",
        interval_sec=5.0,
        llm_every=3,
        armed=True,
        observe=True,
        status_every_sec=1.0,
    )

    rc = P.run_forever(args)

    assert rc == 130
    assert calls["armed"] is False
