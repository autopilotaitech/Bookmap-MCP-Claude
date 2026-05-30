"""Agentic sim trader: governor (hard limits), decision routing, cycle.

LLM is mocked everywhere here - no real Claude calls, no DB writes (cycles run
dry or route through monkeypatched sim tools).
"""
import datetime
import json
from pathlib import Path

from bookmap_mcp import pax_sim_agent as A
from bookmap_mcp import pax_llm_provider
from bookmap_mcp import pax_sim_tools

NOW = datetime.datetime(2026, 5, 28, 19, 30, 0)
NOW_MS = 1_900_000_000_000


def snap(dec="ENTER_LONG_FOLLOW", conf=0.6, label="OR-H", price=30340.0,
         orH=30340.0, orL=30325.5, mid=30339.0, health="ok",
         anchor="LIVE", code="ACTIVE", news=False):
    lvl = {"label": label, "price": price, "distance": price - mid,
           "proximity": True, "decision": dec, "confidence": conf,
           "components": {"ps_rot": "NONE"}}
    return {"health": health, "book": {"mid": mid},
            "session": {"anchorMode": anchor, "code": code},
            "or_day_ledger": {"session_type": "ETH"},
            "gates": {"news": {"blocked": news}}, "flow": {},
            "or_levels": {"orHigh": orH, "orLow": orL,
                          "orWidthPts": round(orH - orL, 2),
                          "inProximity": True, "middleLock": False,
                          "levels": [lvl]}}


def status(size=0, losers=0, working=None, last_exit_ms=None):
    fills = [{"role": "STOP", "filled_ms": last_exit_ms}] if last_exit_ms else []
    return {"position": {"size": size}, "losers_today": losers,
            "working": working or [], "fills_today": fills,
            "realized_today_usd": 0.0}


def baseline(s, st):
    from bookmap_mcp import pax_loop
    return pax_loop.decide(s, st, NOW, NOW_MS)


# ---- governor ----

def test_governor_allows_valid_long_and_clamps_qty():
    s, st = snap(), status()
    dec = {"action": "ENTER_LONG", "entry": 30340.0, "stop": 30330.0,
           "tps": [30350.0, 30405.0], "qty": 9}
    g = A.govern(dec, s, st, NOW_MS, baseline(s, st))
    assert g["action"] == "ENTER_LONG" and g["governor"] == "ok"
    assert g["qty"] == A.MAX_QTY


def test_governor_vetoes_in_position():
    s, st = snap(), status(size=2)
    g = A.govern({"action": "ENTER_LONG", "entry": 30340, "stop": 30330,
                  "tps": [30350]}, s, st, NOW_MS, baseline(s, st))
    assert g["action"] == "HOLD" and "in position" in g["governor"]


def test_governor_daily_stop_loose_backstop():
    from bookmap_mcp import pax_loop
    # A couple losses must NOT veto (sim-aggressive re-entry model).
    s, st = snap(), status(losers=2)
    g = A.govern({"action": "ENTER_LONG", "entry": 30340, "stop": 30330,
                  "tps": [30350]}, s, st, NOW_MS, baseline(s, st))
    assert g["action"] == "ENTER_LONG"
    # Backstop still fires at the loose threshold.
    st2 = status(losers=pax_loop.DAILY_STOP_LOSERS)
    g2 = A.govern({"action": "ENTER_LONG", "entry": 30340, "stop": 30330,
                   "tps": [30350]}, s, st2, NOW_MS, baseline(s, st2))
    assert "daily stop" in g2["governor"]


def test_governor_no_cooldown_allows_reentry():
    # COOLDOWN_MIN=0 -> a recent exit does NOT veto re-entry (re-entry is the edge).
    s, st = snap(), status(last_exit_ms=NOW_MS - 60_000)
    g = A.govern({"action": "ENTER_LONG", "entry": 30340, "stop": 30330,
                  "tps": [30350]}, s, st, NOW_MS, baseline(s, st))
    assert g["action"] == "ENTER_LONG"


def test_governor_vetoes_stacking():
    s = snap()
    st = status(working=[{"role": "ENTRY", "side": "buy"}])
    g = A.govern({"action": "ENTER_LONG", "entry": 30340, "stop": 30330,
                  "tps": [30350]}, s, st, NOW_MS, baseline(s, st))
    assert "stacking" in g["governor"]


def test_governor_vetoes_stale_snapshot():
    s, st = snap(), status()
    s["ageMs"] = A.DEFAULT_STALE_SNAPSHOT_MS + 1
    g = A.govern({"action": "ENTER_LONG", "entry": 30340, "stop": 30330,
                  "tps": [30350]}, s, st, NOW_MS, baseline(s, st))
    assert "snapshot stale" in g["governor"]


def test_governor_vetoes_invalid_long_geometry():
    s, st = snap(), status()
    g = A.govern({"action": "ENTER_LONG", "entry": 30340, "stop": 30345,
                  "tps": [30350]}, s, st, NOW_MS, baseline(s, st))
    assert "geometry" in g["governor"]


def test_governor_fills_geometry_from_baseline():
    s, st = snap(), status()
    g = A.govern({"action": "ENTER_LONG"}, s, st, NOW_MS, baseline(s, st))
    assert g["action"] == "ENTER_LONG"
    assert g["entry"] is not None and g["stop"] is not None and g["tps"]


def test_governor_flatten_requires_position():
    s, st = snap(), status(size=0)
    g = A.govern({"action": "FLATTEN"}, s, st, NOW_MS, baseline(s, st))
    assert g["action"] == "WAIT" and "no position" in g["governor"]


def test_governor_normalizes_flat_hold_to_wait():
    s, st = snap(), status(size=0)
    g = A.govern({"action": "HOLD"}, s, st, NOW_MS, baseline(s, st))
    assert g["action"] == "WAIT"
    assert "flat HOLD" in g["governor"]


# ---- parse / cycle (mock LLM) ----

def test_parse_decision_tolerant():
    txt = 'here is my call:\n{"action":"WAIT","qty":0,"rationale":"chop"}\nthanks'
    assert A.parse_decision(txt)["action"] == "WAIT"


def test_decide_cycle_dry_with_mock():
    s, st = snap(), status()
    call = lambda p: json.dumps({"action": "ENTER_LONG", "entry": 30340.0,
                                 "stop": 30330.0, "tps": [30350.0, 30405.0],
                                 "qty": 2, "confidence": 0.7,
                                 "rationale": "follow the break"})
    rec = A.decide_cycle(s, st, NOW, NOW_MS, call_fn=call, dry=True)
    assert rec["action"] == "ENTER_LONG"
    assert rec["baseline_state"] == "PLACE"
    assert "executed" not in rec  # dry => no execution


def test_decide_cycle_bad_llm_is_safe_noop():
    s, st = snap(), status()
    rec = A.decide_cycle(s, st, NOW, NOW_MS,
                         call_fn=lambda p: "the market looks choppy", dry=True)
    assert rec["action"] == "WAIT" and rec["governor"].startswith("VETO")


def test_decide_cycle_executes_and_learns(monkeypatch, tmp_path):
    # route sim tools to recorders + learning store to tmp; no DB, no real Claude
    placed = {}
    monkeypatch.setattr(pax_sim_tools, "sim_flatten",
                        lambda **k: {"ok": True, "noop_flatten": True})
    monkeypatch.setattr(pax_sim_tools, "LEARN_DIR", tmp_path)
    monkeypatch.setattr(pax_sim_tools, "LESSONS_PATH", tmp_path / "lessons.md")
    monkeypatch.setattr(pax_sim_tools, "SELFMOD_LEDGER", tmp_path / "ledger.csv")
    s, st = snap(), status()
    call = lambda p: json.dumps({"action": "WAIT", "qty": 0,
                                 "rationale": "thin tape", "confidence": 0.2,
                                 "lesson": "skip thin-tape OR breaks"})
    rec = A.decide_cycle(s, st, NOW, NOW_MS, call_fn=call, dry=False)
    assert rec["action"] == "WAIT" and rec["executed"] is True
    assert "lesson_added" not in rec
    assert "skip thin-tape" not in pax_sim_tools.read_lessons()


def test_clean_lessons_filters_wait_poison_and_caps_recent():
    lessons = "\n".join([
        "- Skip because no institutional conviction",
        "- useful acted lesson 1",
        "- preserve capital after down 160",
        "- useful acted lesson 2",
        "- big prints required",
    ])
    out = A._clean_lessons_for_prompt(lessons)
    assert "institutional conviction" not in out
    assert "preserve capital" not in out
    assert "big prints" not in out
    assert "useful acted lesson 1" in out
    assert "useful acted lesson 2" in out


def test_llm_call_stays_tool_less():
    src = Path(pax_llm_provider.__file__).read_text(encoding="utf-8")
    assert '"--tools", ""' in src
    assert '"--max-turns", "1"' in src
    assert "CREATE_NO_WINDOW" in src
    for sym in ("bookmap_place_limit_order", "bookmap_cancel_order",
                "place_market_order"):
        assert sym not in src


# ---- loop runner ----

def test_agent_loop_heartbeat_observe(monkeypatch, tmp_path):
    # Observe (armed=False): deterministic heartbeat decides but does NOT execute.
    monkeypatch.setattr(A, "_fetch_snapshot", lambda *a, **k: snap())
    monkeypatch.setattr(pax_sim_tools, "sim_status", lambda *a, **k: status())
    monkeypatch.setattr(pax_sim_tools, "LEARN_DIR", tmp_path)
    monkeypatch.setattr(A, "AGENT_LOG", tmp_path / "loop.jsonl")
    loop = A.AgentLoop(interval_sec=15)
    rec = loop._cycle_once()
    assert loop.cycles == 1 and rec["armed"] is False and rec["heartbeat"] is True
    # PLACE-able snapshot -> deterministic rule decides PLACE_LONG (no LLM call).
    assert rec["action"] == "PLACE_LONG"
    assert "executed" not in rec          # observe -> nothing placed
    assert (tmp_path / "loop.jsonl").exists()


def test_agent_loop_passes_cached_expectancy_stats(monkeypatch, tmp_path):
    stats = {
        "x": A.pax_expectancy.ExpectancyStats(
            n=12, avg_r=-0.8, hit_rate=0.1, partial_rate=0.1, miss_rate=0.8)
    }
    seen = {}
    monkeypatch.setattr(A, "_fetch_snapshot", lambda *a, **k: snap())
    monkeypatch.setattr(A.pax_expectancy, "load_ifl_stats", lambda path: stats)
    monkeypatch.setattr(A.pax_trade_learning, "summarize_learning",
                        lambda **kw: {"policy": {"suggestions": []},
                                      "scorecard": {"setups": []}})
    monkeypatch.setattr(pax_sim_tools, "sim_status", lambda *a, **k: status())
    monkeypatch.setattr(pax_sim_tools, "LEARN_DIR", tmp_path)
    monkeypatch.setattr(A, "AGENT_LOG", tmp_path / "loop.jsonl")

    def fake_decide(snap_, st_, now_, now_ms_, expectancy_stats=None,
                    runtime_policy=None):
        seen["stats"] = expectancy_stats
        return {"state": "SIT", "action": "NONE", "reason": "x"}

    monkeypatch.setattr(A.pax_loop, "decide", fake_decide)
    loop = A.AgentLoop(interval_sec=15, expectancy_path=tmp_path / "ifl.csv")
    loop._cycle_once()
    assert seen["stats"] is stats


def test_agent_loop_passes_runtime_policy_from_learning(monkeypatch, tmp_path):
    summary = {"policy": {"suggestions": [
        {"setup": "A|LONG|OR-H|ETH", "action": "THROTTLE", "reason": "x"}
    ]}, "scorecard": {"setups": [
        {"setup": "A|LONG|OR-H|ETH", "n": 3, "warning": None}
    ]}}
    seen = {}
    monkeypatch.setattr(A, "_fetch_snapshot", lambda *a, **k: snap())
    monkeypatch.setattr(A.pax_expectancy, "load_ifl_stats", lambda path: {})
    monkeypatch.setattr(A.pax_trade_learning, "summarize_learning", lambda **kw: summary)
    monkeypatch.setattr(pax_sim_tools, "sim_status", lambda *a, **k: status())
    monkeypatch.setattr(pax_sim_tools, "LEARN_DIR", tmp_path)
    monkeypatch.setattr(A, "AGENT_LOG", tmp_path / "loop.jsonl")

    def fake_decide(snap_, st_, now_, now_ms_, expectancy_stats=None,
                    runtime_policy=None):
        seen["runtime_policy"] = runtime_policy
        return {"state": "SIT", "action": "NONE", "reason": "x"}

    monkeypatch.setattr(A.pax_loop, "decide", fake_decide)
    loop = A.AgentLoop(interval_sec=15, expectancy_path=tmp_path / "ifl.csv")
    loop._cycle_once()
    assert seen["runtime_policy"] == summary["policy"]


def test_agent_loop_status_exposes_learning_summary(monkeypatch, tmp_path):
    summary = {"n_linked": 2, "policy": {"suggestions": []}}
    monkeypatch.setattr(A, "_fetch_snapshot", lambda *a, **k: snap())
    monkeypatch.setattr(A.pax_expectancy, "load_ifl_stats", lambda path: {})
    monkeypatch.setattr(A.pax_trade_learning, "summarize_learning", lambda **kw: summary)
    monkeypatch.setattr(pax_sim_tools, "sim_status", lambda *a, **k: status())
    monkeypatch.setattr(pax_sim_tools, "LEARN_DIR", tmp_path)
    monkeypatch.setattr(A, "AGENT_LOG", tmp_path / "loop.jsonl")
    loop = A.AgentLoop(interval_sec=15, expectancy_path=tmp_path / "ifl.csv")
    loop._cycle_once()
    st = loop.status()
    assert st["learning"] is summary
    assert st["expectancy_stats_n"] == 0


def test_agent_loop_armed_executes_rule_live(monkeypatch, tmp_path):
    # Armed: the deterministic rule's order is placed on the sim immediately.
    placed = {}
    monkeypatch.setattr(A, "_fetch_snapshot", lambda *a, **k: snap())
    monkeypatch.setattr(pax_sim_tools, "sim_status", lambda *a, **k: status())
    monkeypatch.setattr(pax_sim_tools, "LEARN_DIR", tmp_path)
    monkeypatch.setattr(A, "AGENT_LOG", tmp_path / "loop.jsonl")
    monkeypatch.setattr(pax_sim_tools, "sim_place_bracket",
                        lambda **kw: placed.update(kw) or {"ok": True})
    loop = A.AgentLoop(interval_sec=15)
    loop.armed = True
    rec = loop._cycle_once()
    assert rec["action"] == "PLACE_LONG" and rec["executed"] is True
    assert placed.get("side") == "long" and placed.get("entry_stop") == 30340.0


def test_kill_switch_blocks_armed_execution(monkeypatch, tmp_path):
    # Armed + kill switch present: the rule plan would normally PLACE, but the
    # risk halt blocks before any broker call and records a clean veto.
    calls = []
    monkeypatch.setattr(A, "_fetch_snapshot", lambda *a, **k: snap())
    monkeypatch.setattr(pax_sim_tools, "sim_status", lambda *a, **k: status())
    monkeypatch.setattr(pax_sim_tools, "LEARN_DIR", tmp_path)
    monkeypatch.setattr(A, "AGENT_LOG", tmp_path / "loop.jsonl")
    monkeypatch.setattr(pax_sim_tools, "sim_place_bracket",
                        lambda **kw: calls.append(kw) or {"ok": True})
    (tmp_path / "KILL_SWITCH").write_text("stop", encoding="utf-8")
    loop = A.AgentLoop(interval_sec=15)
    loop.armed = True
    rec = loop._cycle_once()
    assert calls == []                                 # broker never called
    assert rec["governor"].startswith("VETO")
    assert "kill_switch_active" in rec["governor"]
    assert rec["risk_halt"] == "kill_switch_active"
    assert rec["executed"] is False
    assert rec["order"] is None
    assert "exec" not in rec                            # no broker receipt


def test_kill_switch_blocks_decide_cycle(monkeypatch, tmp_path):
    placed = []
    monkeypatch.setattr(pax_sim_tools, "LEARN_DIR", tmp_path)
    monkeypatch.setattr(pax_sim_tools, "LESSONS_PATH", tmp_path / "lessons.md")
    monkeypatch.setattr(pax_sim_tools, "SELFMOD_LEDGER", tmp_path / "ledger.csv")
    monkeypatch.setattr(pax_sim_tools, "sim_place_bracket",
                        lambda **kw: placed.append(kw) or {"ok": True})
    (tmp_path / "KILL_SWITCH").write_text("stop", encoding="utf-8")
    s, st = snap(), status()
    call = lambda p: json.dumps({"action": "ENTER_LONG", "entry": 30340.0,
                                 "stop": 30330.0, "tps": [30350.0, 30405.0],
                                 "qty": 2, "confidence": 0.7,
                                 "rationale": "follow OR-H",
                                 "lesson": "should not be written"})
    rec = A.decide_cycle(s, st, NOW, NOW_MS, call_fn=call, dry=False)
    assert placed == []                                # broker never called
    assert rec["governor"].startswith("VETO")
    assert "kill_switch_active" in rec["governor"]
    assert rec["executed"] is False
    assert rec["order"] is None
    assert "lesson_added" not in rec                   # vetoed -> no lesson
    assert "should not be written" not in pax_sim_tools.read_lessons()


def test_no_kill_switch_allows_armed_execution(monkeypatch, tmp_path):
    # Requirement 2: absent kill switch preserves existing execution behavior.
    calls = []
    monkeypatch.setattr(A, "_fetch_snapshot", lambda *a, **k: snap())
    monkeypatch.setattr(pax_sim_tools, "sim_status", lambda *a, **k: status())
    monkeypatch.setattr(pax_sim_tools, "LEARN_DIR", tmp_path)
    monkeypatch.setattr(A, "AGENT_LOG", tmp_path / "loop.jsonl")
    monkeypatch.setattr(pax_sim_tools, "sim_place_bracket",
                        lambda **kw: calls.append(kw) or {"ok": True})
    loop = A.AgentLoop(interval_sec=15)
    loop.armed = True
    rec = loop._cycle_once()
    assert rec["action"] == "PLACE_LONG" and rec["executed"] is True
    assert len(calls) == 1 and "risk_halt" not in rec


def test_agent_loop_arming_state():
    loop = A.AgentLoop(interval_sec=60)
    assert loop.status()["armed"] is False and loop.status()["running"] is False
    loop.set_armed(True)
    assert loop.status()["armed"] is True


def test_get_loop_is_singleton():
    assert A.get_loop() is A.get_loop()


def test_call_claude_json_prompt_via_stdin(monkeypatch):
    # The prompt must go via STDIN, never as a command-line arg (Windows caps
    # the command line at ~32k -> WinError 206 when the prompt grows).
    captured = {}

    class _R:
        returncode = 0
        stdout = json.dumps({"result": '{"action":"WAIT","qty":0,"rationale":"x"}'})
        stderr = ""

    def fake_run(args, **kw):
        captured["args"] = args
        captured["input"] = kw.get("input")
        return _R()

    monkeypatch.setattr(pax_llm_provider.subprocess, "run", fake_run)
    big = "HUGE_PROMPT_TOKEN " * 5000          # ~85k chars -> would blow argv
    A.call_claude_json(big)
    assert all("HUGE_PROMPT_TOKEN" not in str(a) for a in captured["args"]), \
        "prompt must NOT be in argv (command-line-too-long bug)"
    assert captured["input"] and "HUGE_PROMPT_TOKEN" in captured["input"], \
        "prompt must be piped via stdin"
    assert "-p" in captured["args"]
