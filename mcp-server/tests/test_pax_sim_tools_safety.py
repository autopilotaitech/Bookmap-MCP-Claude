"""Safety wall for the agentic sim trader.

The agent reaches a broker ONLY through pax_sim_tools, which wraps the local
SimEngine. These tests pin that the surface has no live-order code path and
refuses to act when live trading is enabled.
"""
from pathlib import Path

import pytest

from bookmap_mcp import pax_sim_tools

PKG = Path(pax_sim_tools.__file__).parent

# Symbols that only ever appear on the LIVE order path (server.py / bridge).
FORBIDDEN = (
    "bookmap_place_limit_order",
    "bookmap_cancel_order",
    "bookmap_place_market_order",
    "place_market_order",
)


def _src(name: str) -> str:
    return (PKG / name).read_text(encoding="utf-8")


def test_sim_tools_has_no_live_order_symbols():
    src = _src("pax_sim_tools.py")
    for sym in FORBIDDEN:
        assert sym not in src, f"{sym} must not appear in pax_sim_tools"
    # It must not import the pax-ai server or a live bridge order client.
    assert "import server" not in src
    assert "place_limit_order" not in src


def test_pax_loop_is_pure_no_live_symbols():
    src = _src("pax_loop.py")
    for sym in FORBIDDEN + ("place_limit_order", "subprocess", "urllib"):
        assert sym not in src, f"{sym} must not appear in pax_loop (pure core)"


def test_sim_tools_imports_local_sim_engine():
    # The broker IS the local SQLite SimEngine, not Bookmap.
    assert "from .sim_engine import SimEngine" in _src("pax_sim_tools.py")


def test_refuses_when_live_trading_enabled(monkeypatch):
    monkeypatch.setenv("BOOKMAP_ALLOW_TRADING", "1")
    with pytest.raises(pax_sim_tools.SimSafetyError):
        pax_sim_tools.ensure_sim_safe()
    with pytest.raises(pax_sim_tools.SimSafetyError):
        pax_sim_tools.sim_status()
    with pytest.raises(pax_sim_tools.SimSafetyError):
        pax_sim_tools.sim_place_bracket("buy", 1, 100.0, 90.0, [110.0])


def test_allows_when_unset(monkeypatch):
    monkeypatch.delenv("BOOKMAP_ALLOW_TRADING", raising=False)
    pax_sim_tools.ensure_sim_safe()  # must not raise


def test_learning_store_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(pax_sim_tools, "LEARN_DIR", tmp_path)
    monkeypatch.setattr(pax_sim_tools, "LESSONS_PATH", tmp_path / "lessons.md")
    monkeypatch.setattr(pax_sim_tools, "PLAYBOOK_PATH", tmp_path / "pb.json")
    monkeypatch.setattr(pax_sim_tools, "SELFMOD_LEDGER", tmp_path / "ledger.csv")

    assert pax_sim_tools.read_lessons() == ""
    assert pax_sim_tools.read_playbook() == {}

    pax_sim_tools.write_lessons("dont fade 100+ prints")
    pax_sim_tools.write_playbook({"eth": {"floor": 0.35}})
    assert "dont fade" in pax_sim_tools.read_lessons()
    assert pax_sim_tools.read_playbook()["eth"]["floor"] == 0.35

    pax_sim_tools.append_selfmod("floor", 0.35, 0.45, "ETH breaks fake out", now_ms=1)
    pax_sim_tools.append_selfmod("lesson", "", "stand aside in chop", "low vol", now_ms=2)
    rows = (tmp_path / "ledger.csv").read_text(encoding="utf-8").strip().splitlines()
    assert rows[0].startswith("ts_ms,kind")
    assert len(rows) == 3  # header + 2 entries


def test_tail_lines_reads_only_tail(tmp_path):
    f = tmp_path / "log.jsonl"
    f.write_text("\n".join("line%d" % i for i in range(1000)) + "\n", encoding="utf-8")
    out = pax_sim_tools.tail_lines(f, 50)
    assert len(out) == 50 and out[-1] == "line999" and out[0] == "line950"
    assert pax_sim_tools.tail_lines(tmp_path / "missing.jsonl", 10) == []


def test_append_line_capped_rotates(tmp_path):
    f = tmp_path / "loop.jsonl"
    for i in range(400):
        pax_sim_tools.append_line_capped(f, "x" * 120 + str(i),
                                         max_bytes=20000, keep_lines=50)
    lines = [l for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) < 200                 # bounded (rotated), not 400 -> no leak
    assert lines[-1].endswith("399")        # newest retained
