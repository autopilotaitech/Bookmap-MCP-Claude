"""Sim-agent calibration: action distribution + veto/deviation rate + P&L."""
import json

from bookmap_mcp import pax_sim_calibration as C


def test_summarize_counts_actions_and_rates():
    lines = [
        json.dumps({"action": "WAIT", "governor": "ok"}),
        json.dumps({"action": "ENTER_LONG", "governor": "ok", "executed": True,
                    "deviates": True}),
        json.dumps({"action": "WAIT", "governor": "VETO: cooldown"}),
        "not json — ignored",
    ]
    st = {"position": {"size": 2}, "realized_today_usd": -160.0, "losers_today": 1}
    out = C.summarize(lines, st)
    assert out["decisions"] == 3
    assert out["by_action"]["WAIT"] == 2 and out["by_action"]["ENTER_LONG"] == 1
    assert out["executed"] == 1
    assert out["veto_rate"] == round(1 / 3, 3)
    assert out["deviation_rate"] == round(1 / 3, 3)
    assert out["realized_today_usd"] == -160.0
    assert out["open_position"] == 2


def test_update_writes_calibration(tmp_path, monkeypatch):
    from bookmap_mcp import pax_sim_tools
    monkeypatch.setattr(pax_sim_tools, "LEARN_DIR", tmp_path)
    monkeypatch.setattr(pax_sim_tools, "CALIBRATION_PATH", tmp_path / "calib.json")
    log = tmp_path / "agent-loop.jsonl"
    log.write_text(json.dumps({"action": "WAIT", "governor": "ok"}) + "\n",
                   encoding="utf-8")
    out = C.update({"realized_today_usd": 0.0, "losers_today": 0,
                    "position": {"size": 0}}, log_path=log,
                   out_path=tmp_path / "calib.json")
    assert out["decisions"] == 1
    assert json.loads((tmp_path / "calib.json").read_text())["decisions"] == 1
