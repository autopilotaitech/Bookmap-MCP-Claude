"""Operational pre-execution risk gate (SIM only).

The gate is a pure facade. It unifies kill switch + stale heartbeat + stale
market + sim-broker availability + session risk limits into one deterministic
verdict at the final gate before SIM placement. It NEVER unlocks live, NEVER
blocks risk-reducing exits, and the LLM cannot override it.
"""
from __future__ import annotations

from bookmap_mcp import pax_risk_gate as RG

NOW = 1_900_000_000_000


def _ok_kwargs(**over):
    base = dict(
        now_ms=NOW,
        kill_switch_active=False,
        heartbeat_age_sec=2.0,
        market_age_sec=1.0,
        sim_broker_ok=True,
        session={"trades": 0, "consecutive_losses": 0, "realized_usd": 0.0},
        config=RG.RiskGateConfig(),
    )
    base.update(over)
    return base


# --- clean path -------------------------------------------------------------

def test_clean_session_allows():
    r = RG.evaluate_entry_gate(**_ok_kwargs())
    assert r.allowed is True
    assert r.code is None
    assert r.llm_overrode_risk is False
    rec = r.as_record()
    assert rec["risk_halt"] is None
    assert rec["executed"] is False  # gate never asserts execution


# --- precedence: kill switch dominates --------------------------------------

def test_kill_switch_blocks_first():
    r = RG.evaluate_entry_gate(**_ok_kwargs(
        kill_switch_active=True, heartbeat_age_sec=9999, market_age_sec=9999,
        sim_broker_ok=False, session={"trades": 999}))
    assert r.allowed is False
    assert r.code == "kill_switch_active"
    assert r.source == "kill_switch"


# --- TASK 1: stale heartbeat ------------------------------------------------

def test_stale_heartbeat_blocks():
    r = RG.evaluate_entry_gate(**_ok_kwargs(heartbeat_age_sec=45.0))
    assert r.allowed is False
    assert r.code == "stale_heartbeat"
    assert r.source == "heartbeat"
    assert "45" in r.message


def test_fresh_heartbeat_allows():
    r = RG.evaluate_entry_gate(**_ok_kwargs(heartbeat_age_sec=10.0))
    assert r.allowed is True


def test_none_heartbeat_is_bootstrap_allow():
    # First cycle has no prior beat; the running loop IS the beat -> not stale.
    r = RG.evaluate_entry_gate(**_ok_kwargs(heartbeat_age_sec=None))
    assert r.allowed is True


# --- TASK 2: stale / missing market data ------------------------------------

def test_stale_market_blocks():
    r = RG.evaluate_entry_gate(**_ok_kwargs(market_age_sec=30.0))
    assert r.allowed is False
    assert r.code == "stale_market_data"
    assert r.source == "market"


def test_missing_market_timestamp_blocks_fail_closed():
    # Cannot prove freshness -> must NOT trade.
    r = RG.evaluate_entry_gate(**_ok_kwargs(market_age_sec=None))
    assert r.allowed is False
    assert r.code == "stale_market_data"
    assert "cannot prove" in r.message.lower()


def test_fresh_market_allows():
    r = RG.evaluate_entry_gate(**_ok_kwargs(market_age_sec=5.0))
    assert r.allowed is True


# --- TASK 3: sim broker availability ----------------------------------------

def test_sim_broker_unavailable_blocks():
    r = RG.evaluate_entry_gate(**_ok_kwargs(sim_broker_ok=False))
    assert r.allowed is False
    assert r.code == "sim_broker_unavailable"
    assert r.source == "sim_broker"


# --- TASK 4: session risk limits --------------------------------------------

def test_max_trades_blocks():
    cfg = RG.RiskGateConfig(max_trades_per_session=5)
    r = RG.evaluate_entry_gate(**_ok_kwargs(
        config=cfg, session={"trades": 5, "consecutive_losses": 0,
                             "realized_usd": 0.0}))
    assert r.allowed is False
    assert r.code == "max_trades_reached"


def test_max_consecutive_losses_blocks():
    cfg = RG.RiskGateConfig(max_consecutive_losses=3)
    r = RG.evaluate_entry_gate(**_ok_kwargs(
        config=cfg, session={"trades": 1, "consecutive_losses": 3,
                             "realized_usd": 0.0}))
    assert r.allowed is False
    assert r.code == "max_consecutive_losses_reached"


def test_max_loss_usd_blocks():
    cfg = RG.RiskGateConfig(max_session_loss_usd=500.0)
    r = RG.evaluate_entry_gate(**_ok_kwargs(
        config=cfg, session={"trades": 1, "consecutive_losses": 0,
                             "realized_usd": -500.0}))
    assert r.allowed is False
    assert r.code == "max_loss_reached"


def test_max_loss_r_blocks_when_r_present():
    cfg = RG.RiskGateConfig(max_session_loss_r=3.0)
    r = RG.evaluate_entry_gate(**_ok_kwargs(
        config=cfg, session={"trades": 1, "consecutive_losses": 0,
                             "realized_usd": 0.0, "realized_r": -3.5}))
    assert r.allowed is False
    assert r.code == "max_loss_reached"


def test_max_loss_r_unavailable_is_not_enforced():
    # R gate configured but no R data in the path -> reported unavailable,
    # never faked into a block.
    cfg = RG.RiskGateConfig(max_session_loss_r=3.0)
    r = RG.evaluate_entry_gate(**_ok_kwargs(
        config=cfg, session={"trades": 1, "consecutive_losses": 0,
                             "realized_usd": 0.0, "realized_r": None}))
    assert r.allowed is True
    assert r.detail["unavailable"] and "realized_r" in r.detail["unavailable"]


def test_max_drawdown_blocks_when_present():
    cfg = RG.RiskGateConfig(max_drawdown_usd=800.0)
    r = RG.evaluate_entry_gate(**_ok_kwargs(
        config=cfg, session={"trades": 1, "consecutive_losses": 0,
                             "realized_usd": -100.0, "drawdown_usd": -900.0}))
    assert r.allowed is False
    assert r.code == "max_drawdown_reached"


def test_consecutive_unavailable_not_enforced():
    cfg = RG.RiskGateConfig(max_consecutive_losses=3)
    r = RG.evaluate_entry_gate(**_ok_kwargs(
        config=cfg, session={"trades": 1, "consecutive_losses": None,
                             "realized_usd": 0.0}))
    assert r.allowed is True
    assert "consecutive_losses" in r.detail["unavailable"]


# --- TASK 5: unified record shape -------------------------------------------

def test_record_shape_is_unified():
    for kw, expect in (
        (dict(kill_switch_active=True), "kill_switch_active"),
        (dict(heartbeat_age_sec=99.0), "stale_heartbeat"),
        (dict(market_age_sec=99.0), "stale_market_data"),
        (dict(sim_broker_ok=False), "sim_broker_unavailable"),
    ):
        r = RG.evaluate_entry_gate(**_ok_kwargs(**kw))
        rec = r.as_record()
        assert rec["governor"] == f"VETO: {expect}"
        assert rec["risk_halt"] == expect
        assert rec["risk_halt_code"] == expect
        assert rec["risk_halt_message"]
        assert isinstance(rec["risk_halt_detail"], dict)
        assert rec["executed"] is False
        assert rec["order"] is None
        assert rec["llm_overrode_risk"] is False


def test_kill_switch_result_helper_matches_gate():
    r = RG.kill_switch_result(NOW)
    assert r.allowed is False
    assert r.code == "kill_switch_active"


# --- config loading ----------------------------------------------------------

def test_load_config_reads_env(monkeypatch):
    monkeypatch.setenv("PAX_RISK_MAX_TRADES", "7")
    monkeypatch.setenv("PAX_RISK_MAX_CONSEC_LOSSES", "4")
    monkeypatch.setenv("PAX_RISK_MAX_LOSS_USD", "1234.5")
    monkeypatch.setenv("PAX_STALE_SEC_HEARTBEAT", "20")
    monkeypatch.setenv("PAX_STALE_SEC_MARKET", "9")
    cfg = RG.load_config()
    assert cfg.max_trades_per_session == 7
    assert cfg.max_consecutive_losses == 4
    assert cfg.max_session_loss_usd == 1234.5
    assert cfg.heartbeat_stale_sec == 20.0
    assert cfg.market_stale_sec == 9.0


def test_load_config_garbage_env_falls_back(monkeypatch):
    monkeypatch.setenv("PAX_RISK_MAX_TRADES", "not-a-number")
    monkeypatch.setenv("PAX_RISK_MAX_LOSS_USD", "banana")
    cfg = RG.load_config()
    assert cfg.max_trades_per_session == RG.RiskGateConfig().max_trades_per_session
    assert cfg.max_session_loss_usd == RG.RiskGateConfig().max_session_loss_usd


def test_load_config_loss_usd_can_be_disabled(monkeypatch):
    # "off"/"none" explicitly disable a session-limit gate.
    monkeypatch.setenv("PAX_RISK_MAX_LOSS_USD", "off")
    assert RG.load_config().max_session_loss_usd is None


def test_max_loss_usd_disabled_when_none():
    cfg = RG.RiskGateConfig(max_session_loss_usd=None)
    r = RG.evaluate_entry_gate(**_ok_kwargs(
        config=cfg, session={"trades": 1, "consecutive_losses": 0,
                             "realized_usd": -99999.0}))
    assert r.allowed is True


# --- session counter extraction from a sim status --------------------------

def test_session_counters_from_status_extracts_available():
    status = {
        "position": {"size": 0},
        "fills_today": [
            {"role": "ENTRY", "filled_ms": 1},
            {"role": "TP", "filled_ms": 2},
            {"role": "ENTRY", "filled_ms": 3},
        ],
        "losers_today": 2,
        "realized_today_usd": -120.0,
    }
    c = RG.session_counters_from_status(status)
    assert c["trades"] == 2                 # ENTRY fills
    assert c["realized_usd"] == -120.0
    assert c["losers_today"] == 2
    # consecutive_losses / realized_r / drawdown not derivable here -> None
    assert c["consecutive_losses"] is None
    assert c["realized_r"] is None
    assert c["drawdown_usd"] is None


def test_session_counters_handles_status_error():
    c = RG.session_counters_from_status({"_status_error": "db locked"})
    assert c["trades"] == 0
    assert c["realized_usd"] is None
