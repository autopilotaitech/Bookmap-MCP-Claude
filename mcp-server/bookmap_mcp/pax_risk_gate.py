"""Operational pre-execution risk gate (SIM only).

Pure, deterministic facade unifying the operational HALT checks that sit at the
final gate before SIM order placement:

    kill switch -> stale heartbeat -> stale market data -> sim broker
    availability -> session risk limits

This is NOT a strategy/risk brain. ``pax_loop.decide`` and ``pax_sim_agent.govern``
own strategy selection and the trading governor; they decide WHAT to trade and
bound HOW MUCH it can hurt the sim. This gate is operational safety: if the
system cannot prove it is alive, current, has a usable broker, and is within
its session risk budget, it refuses NEW entry placement.

Contracts:
- Applies to NEW ENTRY placement only. Callers must NOT route risk-reducing
  flatten/cancel through it.
- Never unlocks live trading; it only ever blocks SIM entries.
- The LLM cannot override it: ``llm_overrode_risk`` is always False.
- Fail-closed on market data: an unknown market age cannot prove freshness, so
  it BLOCKS.
- Gates whose live counter is unavailable (R, consecutive losses, drawdown when
  the SIM status does not expose them) are reported under ``detail.unavailable``
  and NEVER faked into a block.

Thresholds come from ``pax_freshness.stale_threshold_sec`` (heartbeat/market,
``PAX_STALE_SEC_*``) and ``PAX_RISK_*`` env vars for the session limits.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from . import pax_freshness

# Block codes -> the deterministic risk-halt vocabulary. Stable identifiers;
# the dashboard / session report key off these.
KILL_SWITCH = "kill_switch_active"
STALE_HEARTBEAT = "stale_heartbeat"
STALE_MARKET = "stale_market_data"
SIM_BROKER_UNAVAILABLE = "sim_broker_unavailable"
MAX_TRADES = "max_trades_reached"
MAX_CONSECUTIVE_LOSSES = "max_consecutive_losses_reached"
MAX_LOSS = "max_loss_reached"
MAX_DRAWDOWN = "max_drawdown_reached"

ALL_CODES = (KILL_SWITCH, STALE_HEARTBEAT, STALE_MARKET, SIM_BROKER_UNAVAILABLE,
             MAX_TRADES, MAX_CONSECUTIVE_LOSSES, MAX_LOSS, MAX_DRAWDOWN)


@dataclass(frozen=True)
class RiskGateConfig:
    """Operational limits. Conservative-but-not-blocking defaults for SIM.

    The session-limit defaults are intentionally loose (the SIM mandate is more
    trades = more data) but present, so an unattended run cannot spiral. R and
    drawdown gates default to None (disabled) because the live status path does
    not yet expose those counters; tests exercise them via explicit config.
    """
    heartbeat_stale_sec: float = 30.0
    market_stale_sec: float = 15.0
    max_trades_per_session: int = 40
    max_consecutive_losses: int = 6
    max_session_loss_usd: Optional[float] = 2000.0
    max_session_loss_r: Optional[float] = None
    max_drawdown_usd: Optional[float] = None


@dataclass
class RiskGateResult:
    allowed: bool
    code: Optional[str]
    message: str
    source: Optional[str]
    detail: Dict[str, Any]
    timestamp: int
    llm_overrode_risk: bool = False

    def as_record(self) -> Dict[str, Any]:
        """Unified record fields merged into an agent-loop record. A blocked
        result carries the governor veto string + structured risk_halt fields;
        an allowed result carries null halt fields. Never asserts execution."""
        gov = (f"VETO: {self.code}" if not self.allowed and self.code else "ok")
        return {
            "governor": gov,
            "risk_halt": self.code,
            "risk_halt_code": self.code,
            "risk_halt_message": (self.message if not self.allowed else None),
            "risk_halt_detail": self.detail,
            "risk_halt_source": self.source,
            "llm_overrode_risk": False,
            "order": None,
            "executed": False,
        }


def _f(x: Any) -> Optional[float]:
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


def _env_float_opt(name: str, default: Optional[float]) -> Optional[float]:
    raw = os.environ.get(name)
    if raw is None:
        return default
    low = raw.strip().lower()
    if low in ("none", "off", "disable", "disabled", ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def load_config() -> RiskGateConfig:
    """Resolve the gate config from env. Garbage values fall back to defaults.

    Heartbeat/market budgets reuse ``pax_freshness`` so the gate and the truth
    slice agree on staleness; session limits use ``PAX_RISK_*``.
    """
    d = RiskGateConfig()
    return RiskGateConfig(
        heartbeat_stale_sec=pax_freshness.stale_threshold_sec("heartbeat"),
        market_stale_sec=pax_freshness.stale_threshold_sec("market"),
        max_trades_per_session=_env_int("PAX_RISK_MAX_TRADES",
                                        d.max_trades_per_session),
        max_consecutive_losses=_env_int("PAX_RISK_MAX_CONSEC_LOSSES",
                                        d.max_consecutive_losses),
        max_session_loss_usd=_env_float_opt("PAX_RISK_MAX_LOSS_USD",
                                            d.max_session_loss_usd),
        max_session_loss_r=_env_float_opt("PAX_RISK_MAX_LOSS_R",
                                          d.max_session_loss_r),
        max_drawdown_usd=_env_float_opt("PAX_RISK_MAX_DD_USD",
                                        d.max_drawdown_usd),
    )


def session_counters_from_status(status: Optional[Dict[str, Any]]
                                 ) -> Dict[str, Any]:
    """Extract the session counters that a SIM status snapshot truthfully
    exposes. Counters the status does not carry are returned as None
    (unavailable), never invented.

    ``SimEngine.snapshot`` now exposes (honestly, from the realized-PnL close
    stream): entry-fill count (-> trades), ``realized_today_usd``,
    ``losers_today``, ``consecutive_losses_today``, ``session_drawdown_usd``.
    Per-trade R (``realized_today_r`` / ``session_drawdown_r``) is NOT derivable
    from that stream and stays None here -- the gate reports R unavailable.
    A status that predates these keys (e.g. an old fixture) simply yields None.
    """
    status = status or {}
    if status.get("_status_error"):
        return {"trades": 0, "consecutive_losses": None, "realized_usd": None,
                "realized_r": None, "drawdown_usd": None, "losers_today": None,
                "status_error": str(status.get("_status_error"))[:160]}
    fills = status.get("fills_today") or []
    trades = sum(1 for f in fills
                 if isinstance(f, dict) and str(f.get("role")) == "ENTRY")
    cl = status.get("consecutive_losses_today")
    return {
        "trades": trades,
        # honestly derived from the SimEngine close stream when present:
        "consecutive_losses": int(cl) if cl is not None else None,
        "realized_usd": _f(status.get("realized_today_usd")),
        "realized_r": _f(status.get("realized_today_r")),   # None unless supplied
        "drawdown_usd": _f(status.get("session_drawdown_usd")),
        "losers_today": status.get("losers_today"),
    }


def kill_switch_result(now_ms: int) -> RiskGateResult:
    """Standalone kill-switch halt (used by callers that gate exits, which only
    the kill switch may halt)."""
    return RiskGateResult(
        allowed=False, code=KILL_SWITCH, source="kill_switch",
        message="operator kill switch engaged",
        detail={"counters": {}, "config": {}, "unavailable": []},
        timestamp=int(now_ms))


def evaluate_entry_gate(*,
                        now_ms: int,
                        kill_switch_active: bool = False,
                        heartbeat_age_sec: Optional[float] = None,
                        market_age_sec: Optional[float] = None,
                        sim_broker_ok: bool = True,
                        session: Optional[Dict[str, Any]] = None,
                        config: Optional[RiskGateConfig] = None
                        ) -> RiskGateResult:
    """Deterministic verdict for NEW SIM entry placement.

    ``heartbeat_age_sec`` of None means "no prior beat" (bootstrapping first
    cycle, the running loop IS the beat) -> not stale. ``market_age_sec`` of
    None means the freshness of the market data cannot be proven -> BLOCK.
    """
    cfg = config or load_config()
    session = session or {}
    now_ms = int(now_ms)

    counters = {
        "trades": int(session.get("trades") or 0),
        "consecutive_losses": session.get("consecutive_losses"),
        "realized_usd": _f(session.get("realized_usd")),
        "realized_r": _f(session.get("realized_r")),
        "drawdown_usd": _f(session.get("drawdown_usd")),
    }
    unavailable: List[str] = []
    if counters["consecutive_losses"] is None:
        unavailable.append("consecutive_losses")
    if cfg.max_session_loss_r is not None and counters["realized_r"] is None:
        unavailable.append("realized_r")
    if cfg.max_drawdown_usd is not None and counters["drawdown_usd"] is None:
        unavailable.append("drawdown_usd")

    base_detail: Dict[str, Any] = {
        "counters": counters,
        "config": asdict(cfg),
        "ages": {"heartbeat_sec": heartbeat_age_sec,
                 "market_sec": market_age_sec},
        "unavailable": unavailable,
    }

    def block(code: str, message: str, source: str) -> RiskGateResult:
        return RiskGateResult(allowed=False, code=code, message=message,
                              source=source, detail=base_detail,
                              timestamp=now_ms)

    def allow() -> RiskGateResult:
        return RiskGateResult(allowed=True, code=None, message="ok",
                              source=None, detail=base_detail,
                              timestamp=now_ms)

    # 1) kill switch dominates everything.
    if kill_switch_active:
        return block(KILL_SWITCH, "operator kill switch engaged", "kill_switch")

    # 2) stale / unavailable heartbeat (runtime not proving it is alive).
    if heartbeat_age_sec is not None and heartbeat_age_sec > cfg.heartbeat_stale_sec:
        return block(STALE_HEARTBEAT,
                     f"heartbeat age {heartbeat_age_sec:.1f}s exceeds "
                     f"{cfg.heartbeat_stale_sec:.0f}s budget",
                     "heartbeat")

    # 3) stale / missing market data (fail-closed).
    if market_age_sec is None:
        return block(STALE_MARKET,
                     "no market timestamp; cannot prove data freshness",
                     "market")
    if market_age_sec > cfg.market_stale_sec:
        return block(STALE_MARKET,
                     f"market data age {market_age_sec:.1f}s exceeds "
                     f"{cfg.market_stale_sec:.0f}s budget",
                     "market")

    # 4) SIM broker availability.
    if not sim_broker_ok:
        return block(SIM_BROKER_UNAVAILABLE,
                     "SIM broker DB unavailable or unopenable", "sim_broker")

    # 5) session risk limits (only enforced where the counter exists).
    if counters["trades"] >= cfg.max_trades_per_session:
        return block(MAX_TRADES,
                     f"{counters['trades']} trades >= "
                     f"{cfg.max_trades_per_session} session cap", "session_limits")

    cl = counters["consecutive_losses"]
    if cl is not None and int(cl) >= cfg.max_consecutive_losses:
        return block(MAX_CONSECUTIVE_LOSSES,
                     f"{int(cl)} consecutive losses >= "
                     f"{cfg.max_consecutive_losses} cap", "session_limits")

    ru = counters["realized_usd"]
    if (cfg.max_session_loss_usd is not None and ru is not None
            and ru <= -abs(cfg.max_session_loss_usd)):
        return block(MAX_LOSS,
                     f"realized {ru:.2f} USD <= -{abs(cfg.max_session_loss_usd):.2f} "
                     "session loss cap", "session_limits")

    rr = counters["realized_r"]
    if (cfg.max_session_loss_r is not None and rr is not None
            and rr <= -abs(cfg.max_session_loss_r)):
        return block(MAX_LOSS,
                     f"realized {rr:.2f}R <= -{abs(cfg.max_session_loss_r):.2f}R "
                     "session loss cap", "session_limits")

    dd = counters["drawdown_usd"]
    if (cfg.max_drawdown_usd is not None and dd is not None
            and dd <= -abs(cfg.max_drawdown_usd)):
        return block(MAX_DRAWDOWN,
                     f"drawdown {dd:.2f} USD <= -{abs(cfg.max_drawdown_usd):.2f} cap",
                     "session_limits")

    return allow()
