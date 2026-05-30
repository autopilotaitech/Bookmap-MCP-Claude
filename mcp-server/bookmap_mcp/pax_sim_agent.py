"""Agentic Pax SIM trader — decide / govern / execute / learn (paper only).

NOTE: distinct from the legacy deterministic `pax_agent.py` (read-only console
alerts). This is the autonomous LLM sim trader.

Design (operator-locked 2026-05-28):
- The LLM stays TOOL-LESS and single-turn. It reads a follow-the-money context
  + the deterministic baseline rule's advice + its own lessons/calibration, and
  emits a STRUCTURED decision (JSON). It never holds a broker tool.
- The deterministic GOVERNOR (`govern`) validates/clamps that decision against
  the hard sim risk limits (sim-only, in-position, daily-stop, no-stack,
  cooldown, qty cap, bracket geometry). The agent decides WHAT to trade; the
  governor bounds HOW MUCH it can hurt the sim and vetoes anything illegal.
- Execution routes ONLY through `pax_sim_tools` (local SimEngine). No live path.

`decide_cycle` is pure given an injected `call_fn` (the LLM), so it is fully
testable with a mock. The live `call_fn` shells the `claude` CLI with
`--tools "" --max-turns 1` (read-only invariant preserved) and parses JSON.

The agent is measured against `pax_loop.decide()` (the rule) so the learning
loop can prove whether the LLM adds edge.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import (
    pax_expectancy,
    pax_llm_provider,
    pax_loop,
    pax_risk_gate,
    pax_runtime_policy,
    pax_sim_calibration,
    pax_sim_tools,
    pax_trade_learning,
)

DASHBOARD_URL = "http://127.0.0.1:18888/api/snapshot"
AGENT_LOG = pax_sim_tools.LEARN_DIR / "agent-loop.jsonl"
DEFAULT_EXPECTANCY_PATH = Path(r"D:\BookmapLogs\ifl-outcomes.csv")

MAX_QTY = 2
ENTER_ACTIONS = ("ENTER_LONG", "ENTER_SHORT")
ALL_ACTIONS = ENTER_ACTIONS + ("HOLD", "WAIT", "FLATTEN", "CANCEL_ENTRY")
DEFAULT_STALE_SNAPSHOT_MS = 5_000

DEFAULT_PROVIDER = os.environ.get("PAX_LLM_PROVIDER", "claude_cli")
DEFAULT_MODEL = os.environ.get("PAX_AGENT_MODEL", "claude-haiku-4-5")
DEFAULT_OLLAMA_ENDPOINT = os.environ.get("PAX_OLLAMA_ENDPOINT", "http://127.0.0.1:11434")
# The claude CLI cold-starts / OAuth-handshakes on the first calls; 45s timed
# out intermittently (-> "agent error"). 120s absorbs the slow ones. Env-tunable.
try:
    AGENT_CALL_TIMEOUT = float(os.environ.get("PAX_AGENT_TIMEOUT", "120"))
    if AGENT_CALL_TIMEOUT <= 0:
        AGENT_CALL_TIMEOUT = 120.0
except (TypeError, ValueError):
    AGENT_CALL_TIMEOUT = 120.0


# --------------------------------------------------------------------------- #
# Context the LLM reads (deterministic, follow-the-money)                      #
# --------------------------------------------------------------------------- #

def _f(x: Any) -> Optional[float]:
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _snapshot_is_stale(snap: Dict[str, Any]) -> bool:
    if snap.get("stale") is True:
        return True
    age = _f(snap.get("ageMs") or snap.get("snapshot_age_ms"))
    if age is None:
        return False
    threshold = _f(snap.get("stale_threshold_ms")) or DEFAULT_STALE_SNAPSHOT_MS
    return age > threshold


def _market_age_sec(snap: Dict[str, Any], now_ms: int) -> Optional[float]:
    """Seconds since the snapshot's market data was composed, or None when no
    market timestamp is present (the risk gate fails closed on None).

    Prefers ``asOfMs`` (epoch-ms compose time emitted by dashboard.fetch_snapshot);
    falls back to an explicit ``ageMs`` / ``snapshot_age_ms`` if a producer ever
    supplies one. No reliable timestamp -> None -> cannot prove freshness."""
    as_of = _f(snap.get("asOfMs"))
    if as_of is not None:
        return max(0.0, (now_ms - as_of) / 1000.0)
    age_ms = _f(snap.get("ageMs"))
    if age_ms is None:
        age_ms = _f(snap.get("snapshot_age_ms"))
    if age_ms is not None:
        return max(0.0, age_ms / 1000.0)
    return None


def _apply_risk_halt(rec: Dict[str, Any], result: "pax_risk_gate.RiskGateResult"
                     ) -> None:
    """Stamp a blocked-gate result onto an agent-loop record (unified shape).
    No broker call happened; no `exec` key is added."""
    rec.update(result.as_record())


def build_context(snap: Dict[str, Any], status: Dict[str, Any],
                  baseline: Dict[str, Any], lessons: str,
                  calibration: Dict[str, Any]) -> str:
    ol = snap.get("or_levels") or {}
    ses = snap.get("session") or {}
    flow = snap.get("flow") or {}
    book = snap.get("book") or {}
    mid = _f(book.get("mid"))
    prox = None
    for l in (ol.get("levels") or []):
        if l.get("proximity"):
            if prox is None or abs(l.get("distance") or 9e9) < abs(prox.get("distance") or 9e9):
                prox = l
    pos = (status.get("position") or {}).get("size") or 0
    lines: List[str] = []
    lines.append("[STATE] health=%s session=%s anchor=%s stype=%s mid=%s" % (
        snap.get("health"), ses.get("code"), ses.get("anchorMode"),
        (snap.get("or_day_ledger") or {}).get("session_type"), mid))
    lines.append("[OR] H=%s L=%s width=%s inProx=%s middleLock=%s" % (
        ol.get("orHigh"), ol.get("orLow"), ol.get("orWidthPts"),
        ol.get("inProximity"), ol.get("middleLock")))
    if prox:
        c = prox.get("components") or {}
        lines.append("[LEVEL] %s @%s dist=%s decision=%s conf=%s ps_rot=%s lt=%s tape=%s micro=%s" % (
            prox.get("label"), prox.get("price"), prox.get("distance"),
            prox.get("decision"), prox.get("confidence"),
            c.get("ps_rot"), c.get("lt"), c.get("tape"), c.get("micro")))
    # follow-the-money microstructure (REAL snapshot fields).
    # Big-print aggression = sum of the >=50-lot tape buckets over 30s
    # (operator rule: 50-100+ aggressive prints are institutional thrust).
    big_buy = big_sell = 0
    for bk in (snap.get("tape_buckets") or {}).get("buckets", []):
        if (bk.get("minSize") or 0) >= 50:
            big_buy += bk.get("buyVol30s") or 0
            big_sell += bk.get("sellVol30s") or 0
    w30 = next((w for w in (flow.get("windows") or [])
                if w.get("label") == "30s"), {})
    lines.append(
        "[MONEY] regime=%s (%s) conf=%s bias=%s/%s cvd=%s(z=%s) ofi=%s(z=%s) "
        "bookPress5=%s imb30s=%s bigBuy30s=%s bigSell30s=%s" % (
            flow.get("regime"), flow.get("regimeReason"),
            flow.get("regimeConfidence"),
            round(flow.get("biasScore") or 0.0, 2), flow.get("biasTrajectory"),
            flow.get("cvdDelta"), round(flow.get("cvdDeltaZ") or 0.0, 2),
            flow.get("ofi"), round(flow.get("ofiZ") or 0.0, 2),
            round(flow.get("bookPressureTop5") or 0.0, 2),
            round(w30.get("imbalance") or 0.0, 2), big_buy, big_sell))
    lines.append("[POSITION] size=%s working=%s losers_today=%s realized_today=%s" % (
        pos, len(status.get("working") or []), status.get("losers_today"),
        status.get("realized_today_usd")))
    lines.append("[BASELINE_RULE] state=%s action=%s reason=%s" % (
        baseline.get("state"), baseline.get("action"), baseline.get("reason")))
    if baseline.get("order"):
        o = baseline["order"]
        lines.append("[BASELINE_ORDER] %s trig=%s stop=%s tps=%s" % (
            o.get("side"), o.get("entry_stop"), o.get("stop_loss"), o.get("tps")))
    if calibration:
        lines.append("[CALIBRATION] " + json.dumps(calibration, default=str)[:600])
    lines.append("[LESSONS]\n" + (_clean_lessons_for_prompt(lessons) or "(none yet)"))
    return "\n".join(lines)


_BAD_LESSON_PHRASES = (
    "big prints",
    "institutional conviction",
    "institutional direction",
    "capital preservation",
    "preserve capital",
    "down 160",
    "no institutional",
)


def _clean_lessons_for_prompt(lessons: str, limit: int = 8) -> str:
    """Keep recent useful lessons, drop stale RTH/proximity poison.

    Lessons are model-written and therefore untrusted prompt input. Old WAIT
    lessons were teaching the agent to require institutional prints during ETH
    and to stand aside forever after a small drawdown. Do not feed that back.
    """
    keep: List[str] = []
    for raw in (lessons or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if any(p in low for p in _BAD_LESSON_PHRASES):
            continue
        keep.append(line)
    return "\n".join(keep[-limit:])


SYSTEM_INSTRUCTION = (
    "You are Pax, an autonomous SIM (paper) NQ scalper that EXECUTES the "
    "operator's Pax OR rotation strategy. You follow price + level structure and "
    "you ACT - you do not sit around waiting for perfect conditions. Size = 2 "
    "contracts: one PAYS FOR THE TRADE (take profit at the payline / first rung) "
    "and one RIDES as a runner. Entries are RESTING stop-limit / limit orders "
    "parked AT the level so price comes to you: if price runs past you were "
    "never filled (no loss); if it rotates you are positioned.\n"
    "WE GET PAID TO REACT, NOT TO ANALYZE. Do not over-think, do not wait for a "
    "perfect setup - it is not a perfect world. Entries do NOT need to be exact: "
    "a few points (~5-10pt) off the OR / EXT is fine. That is the WHOLE REASON "
    "you rest a buy/sell stop NEAR the level - the order is the reaction; price "
    "triggers it, you don't predict it. Rule of thumb: rest the stop within "
    "~5-10pt of the level. Do NOT demand a perfect touch, and do NOT chase into "
    "the MIDDLE of the range (mid is no-man's-land). React, don't predict.\n"
    "DIRECTION = who the DATA says is in CONTROL: buyers in control -> long; "
    "sellers in control -> short; unclear -> WAIT a beat (don't force it). You "
    "have many indicators - you should know FAST if you're wrong, so CUT IT "
    "QUICK (tight scratch, small loss). When you're RIGHT: bank the payer early, "
    "let the runner ride, and (later layer) add to the runner once it's a "
    "confirmed winner trending one way. We don't predict the future - we follow "
    "the trail and take what the market gives.\n"
    "SESSION RULES - read stype in [STATE], they are DIFFERENT:\n"
    "- ETH (overnight): pure algo, THIN volume, NO institutional flow and NO big "
    "prints. That is NORMAL for ETH and is NEVER a reason to stand down. Do NOT "
    "require big prints, CVD spikes, or 'institutional conviction'. Trade the "
    "ROTATION: rung-to-rung (EXT-to-EXT) and back to the OR. Short the breakdown "
    "rungs in a down-rotation, long the rotations back up. A clean break of a "
    "rung in the move's direction IS the trade - take it.\n"
    "- RTH (08:30-15:00 CT): real institutional flow exists - HERE you do want "
    "flow confirmation (CVD/OFI/big prints/book) behind an OR-boundary break.\n"
    "Stop = OR mid or the opposite rung; if a 50+ lot print ever appears, go "
    "WITH it, never fade it. BASELINE_RULE is RTH-tuned advice - override it "
    "freely on ETH (say why); it is too strict for the overnight rotation game. "
    "Respect your LESSONS and CALIBRATION.\n"
    "ACT BY DEFAULT - you exist to TRADE, not to watch. If the data shows who is "
    "in control (CVD/OFI/imbalance/tape leaning one way) and you are near a level "
    "(OR / rung, ~5-10pt is fine), ENTER in that direction. The confidence FLOOR "
    "is the deterministic rule's gate - it is NOT yours; you may enter even when "
    "BASELINE conf is below the floor if the read is there. WAIT is the EXCEPTION "
    "and needs a CONCRETE, specific reason (flat/conflicting direction at the "
    "level) - never WAIT just because volume/flow is thin, regime is BALANCED, or "
    "there are no big prints. Standing aside all session is FAILURE.\n"
    "THIS IS A SIM (paper) ACCOUNT - the point is to TRADE and LEARN. Do NOT be "
    "shy or conservative: take the read, re-enter to catch the move (you may "
    "miss the first 2-3 tries - that is fine), generate data. The only way you "
    "get better is reps. When you LOSE, in your lesson say WHY you were wrong "
    "(what the read missed) so the next read improves. More trades + honest "
    "loss lessons = a better trader. Sim only.\n\n"
    "Respond with ONE JSON object and nothing else:\n"
    "{\"action\":\"ENTER_LONG|ENTER_SHORT|HOLD|WAIT|FLATTEN|CANCEL_ENTRY\","
    "\"level\":\"OR-H|OR-L|null\",\"entry_type\":\"stop_limit|limit|null\","
    "\"entry\":number|null,\"stop\":number|null,\"tps\":[number,...]|null,"
    "\"qty\":int,\"confidence\":0..1,\"rationale\":\"one or two plain sentences\","
    "\"deviates_from_baseline\":bool,\"deviation_reason\":string|null,"
    "\"lesson\":string|null}"
)


# --------------------------------------------------------------------------- #
# LLM call (tool-less, single-turn, JSON) — injectable for tests              #
# --------------------------------------------------------------------------- #

def call_claude_json(prompt: str, model: str = DEFAULT_MODEL,
                     timeout_sec: float = AGENT_CALL_TIMEOUT) -> str:
    """Live LLM call. Tool-less + single-turn (read-only invariant preserved).
    Returns the assistant's text (expected to contain a JSON object)."""
    return pax_llm_provider.call_agent_json(
        prompt, provider="claude_cli", model=model, timeout_sec=timeout_sec)


def call_model_json(prompt: str, *, provider: str = DEFAULT_PROVIDER,
                    model: str = DEFAULT_MODEL,
                    timeout_sec: float = AGENT_CALL_TIMEOUT,
                    endpoint: Optional[str] = None,
                    keep_alive: str = "30m") -> str:
    return pax_llm_provider.call_agent_json(
        prompt, provider=provider, model=model, timeout_sec=timeout_sec,
        endpoint=endpoint, keep_alive=keep_alive)


def parse_decision(text: str) -> Dict[str, Any]:
    """Extract the JSON decision object from the LLM text. Tolerant."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("no JSON object in agent response")
    d = json.loads(m.group(0))
    if d.get("action") not in ALL_ACTIONS:
        raise ValueError(f"bad action {d.get('action')!r}")
    return d


# --------------------------------------------------------------------------- #
# Governor — deterministic hard limits (agent cannot disable)                 #
# --------------------------------------------------------------------------- #

def govern(decision: Dict[str, Any], snap: Dict[str, Any],
           status: Dict[str, Any], now_ms: int,
           baseline: Dict[str, Any]) -> Dict[str, Any]:
    """Validate/clamp the agent decision. Returns a (possibly downgraded)
    decision with a `governor` note. Never lets an illegal action through."""
    d = dict(decision)
    d.setdefault("qty", MAX_QTY)
    note = "ok"
    action = d.get("action")
    pos = (status.get("position") or {}).get("size") or 0
    ses = snap.get("session") or {}
    flow_news = bool(((snap.get("gates") or {}).get("news") or {}).get("blocked"))
    losers = status.get("losers_today") or 0
    wentry = pax_loop.working_entry(status)
    last_exit = pax_loop.last_exit_ms(status)

    def veto(reason: str) -> Dict[str, Any]:
        d["action"] = "HOLD" if pos != 0 else "WAIT"
        d["governor"] = f"VETO: {reason}"
        d["vetoed_action"] = action
        return d

    if action == "FLATTEN":
        if pos == 0:
            return veto("flatten with no position")
        d["governor"] = note; return d
    if action == "CANCEL_ENTRY":
        if not wentry:
            return veto("cancel with no working entry")
        d["governor"] = note; return d
    if action == "HOLD" and pos == 0:
        d["action"] = "WAIT"
        d["governor"] = "NORMALIZED: flat HOLD -> WAIT"
        d["normalized_action"] = action
        return d
    if action in ("HOLD", "WAIT"):
        d["governor"] = note; return d

    # ENTER_* — apply the hard entry gates (same as pax_loop governor).
    if pos != 0:
        return veto("already in position")
    if wentry:
        return veto("entry already working (no stacking)")
    if snap.get("health") != "ok":
        return veto("bridge not ok")
    if _snapshot_is_stale(snap):
        return veto("snapshot stale")
    if ses.get("anchorMode") != "LIVE":
        return veto(f"anchor {ses.get('anchorMode')} not LIVE")
    if ses.get("code") in pax_loop.BLOCK_CODES:
        return veto(f"session {ses.get('code')}")
    if flow_news:
        return veto("news blackout")
    if losers >= pax_loop.DAILY_STOP_LOSERS:
        return veto(f"daily stop ({losers} losers)")
    if last_exit is not None and (now_ms - last_exit) < pax_loop.COOLDOWN_MIN * 60_000:
        return veto("post-trade cooldown")

    # clamp size
    try:
        d["qty"] = max(1, min(MAX_QTY, int(d.get("qty") or MAX_QTY)))
    except (TypeError, ValueError):
        d["qty"] = MAX_QTY

    # fill missing geometry from the baseline order when available
    bo = baseline.get("order") or {}
    entry = _f(d.get("entry")) or _f(bo.get("entry_stop")) or _f(bo.get("entry_limit"))
    stop = _f(d.get("stop")) or _f(bo.get("stop_loss"))
    tps = [t for t in (d.get("tps") or bo.get("tps") or []) if _f(t) is not None]
    if entry is None or stop is None or not tps:
        return veto("incomplete bracket (no entry/stop/tps)")

    is_long = action == "ENTER_LONG"
    # geometry sanity: long => stop < entry <= tps ; short => stop > entry >= tps
    if is_long and not (stop < entry <= min(tps)):
        return veto("invalid long geometry (need stop<entry<=tps)")
    if not is_long and not (stop > entry >= max(tps)):
        return veto("invalid short geometry (need stop>entry>=tps)")

    d["entry"], d["stop"], d["tps"] = entry, stop, [float(t) for t in tps]
    d.setdefault("entry_type", "stop_limit")
    d["governor"] = note
    return d


# --------------------------------------------------------------------------- #
# Execute (local SimEngine only)                                              #
# --------------------------------------------------------------------------- #

def execute(decision: Dict[str, Any], alias: Optional[str] = None) -> Dict[str, Any]:
    a = decision.get("action")
    kw = {"alias": alias} if alias else {}
    if a in ENTER_ACTIONS:
        side = "buy" if a == "ENTER_LONG" else "sell"
        entry = float(decision["entry"])
        etype = decision.get("entry_type", "stop_limit")
        return pax_sim_tools.sim_place_bracket(
            side=side, qty=int(decision["qty"]), entry_limit=entry,
            stop_loss=float(decision["stop"]), take_profits=decision["tps"],
            entry_stop=entry if etype == "stop_limit" else None,
            tag="AGENT", reason=(decision.get("rationale") or "")[:160], **kw)
    if a == "FLATTEN":
        return pax_sim_tools.sim_flatten(
            reason=(decision.get("rationale") or "agent-flatten")[:160], **kw)
    if a == "CANCEL_ENTRY":
        return pax_sim_tools.sim_flatten(reason="agent-cancel-entry", **kw)
    return {"ok": True, "action": a, "noop": True}


# --------------------------------------------------------------------------- #
# One cycle: decide -> govern -> execute -> learn                             #
# --------------------------------------------------------------------------- #

def decide_cycle(snap: Dict[str, Any], status: Dict[str, Any], now_dt, now_ms: int,
                 call_fn: Callable[[str], str] = call_claude_json,
                 alias: Optional[str] = None, dry: bool = False) -> Dict[str, Any]:
    """Run one agent cycle. `call_fn(prompt)->text` is injectable for tests."""
    baseline = pax_loop.decide(snap, status, now_dt, now_ms)
    lessons = pax_sim_tools.read_lessons()
    calib = pax_sim_tools.read_calibration()
    context = build_context(snap, status, baseline, lessons, calib)
    prompt = SYSTEM_INSTRUCTION + "\n\n=== LIVE CONTEXT ===\n" + context

    rec: Dict[str, Any] = {"baseline_state": baseline.get("state"),
                           "baseline_action": baseline.get("action")}
    try:
        raw = call_fn(prompt)
        decision = parse_decision(raw)
    except Exception as e:  # LLM failure -> safe no-op, never act blind
        rec.update(error=f"agent_decide: {e}", action="WAIT",
                   governor="VETO: agent error", executed=False)
        return rec

    governed = govern(decision, snap, status, now_ms, baseline)
    rec.update(action=governed.get("action"),
               raw_action=decision.get("action"),
               confidence=decision.get("confidence"),
               rationale=decision.get("rationale"),
               deviates=decision.get("deviates_from_baseline"),
               deviation_reason=decision.get("deviation_reason"),
               governor=governed.get("governor"),
               order={k: governed.get(k) for k in ("entry_type", "entry", "stop", "tps", "qty")}
               if governed.get("action") in ENTER_ACTIONS else None)

    if not dry:
        gaction = governed.get("action")
        is_entry = gaction in ENTER_ACTIONS
        is_exit = gaction in ("FLATTEN", "CANCEL_ENTRY")
        # Operational risk gate at the final pre-execution point. ENTRIES run the
        # full gate (kill switch / stale heartbeat / stale market / sim broker /
        # session limits). EXITS reduce risk -> only the kill switch may halt
        # them. A blocked gate records a clean unified veto, writes no lesson (a
        # vetoed decision must not poison the prompt), and never calls the broker.
        halt = None
        if is_entry:
            halt = pax_risk_gate.evaluate_entry_gate(
                now_ms=now_ms,
                kill_switch_active=pax_sim_tools.kill_switch_active(),
                heartbeat_age_sec=None,   # one-shot path: no prior beat to age
                market_age_sec=_market_age_sec(snap, now_ms),
                sim_broker_ok=not status.get("_status_error"),
                session=pax_risk_gate.session_counters_from_status(status))
            if halt.allowed:
                halt = None
        elif is_exit and pax_sim_tools.kill_switch_active():
            halt = pax_risk_gate.kill_switch_result(now_ms)
        if halt is not None:
            _apply_risk_halt(rec, halt)
            return rec
        try:
            rec["exec"] = execute(governed, alias=alias)
            rec["executed"] = bool(rec["exec"].get("ok"))
        except Exception as e:
            rec["exec_error"] = str(e); rec["executed"] = False
        # autonomous self-learning: append a lesson ONLY from a decision the
        # governor allowed (governor == "ok"). Lessons from VETOED/garbage
        # decisions would poison the prompt on every future cycle.
        lesson = (decision.get("lesson") or "").strip()
        # Persist lessons only from real acted decisions. WAIT/HOLD lessons
        # created the feedback loop that taught the agent to keep waiting.
        if (lesson and governed.get("governor") == "ok"
                and governed.get("action") in ENTER_ACTIONS + ("FLATTEN", "CANCEL_ENTRY")):
            try:
                pax_sim_tools.write_lessons(
                    (lessons.rstrip() + "\n- " + lesson).strip())
                pax_sim_tools.append_selfmod("lesson", "", lesson,
                                             rationale=(decision.get("rationale") or "")[:160],
                                             now_ms=now_ms)
                rec["lesson_added"] = lesson
            except Exception as e:
                rec["lesson_error"] = str(e)
    return rec


# --------------------------------------------------------------------------- #
# Loop runner — in-process daemon thread (no terminal, no cron)               #
# --------------------------------------------------------------------------- #

def _fetch_snapshot(url: str = DASHBOARD_URL, timeout: float = 8.0) -> Dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


class AgentLoop:
    """Runs decide_cycle on an interval in a daemon thread. `armed=False`
    (default) = observe mode: the agent thinks + logs but places NO sim orders.
    Arming flips execution on. Either way it never reaches a live order."""

    def __init__(self, dashboard_url: str = DASHBOARD_URL,
                 alias: Optional[str] = None, interval_sec: float = 15.0,
                 model: str = DEFAULT_MODEL, llm_every: int = 6,
                 llm_provider: str = DEFAULT_PROVIDER,
                 llm_endpoint: Optional[str] = None,
                 llm_keep_alive: str = "30m",
                 expectancy_path: Optional[Path] = DEFAULT_EXPECTANCY_PATH,
                 expectancy_refresh_sec: float = 60.0,
                 learning_refresh_sec: float = 120.0):
        self.url = dashboard_url
        self.alias = alias
        # FAST heartbeat: the deterministic rule (pax_loop.decide) is evaluated
        # + executed every `interval` seconds (microseconds of compute), so it
        # watches the tape LIVE and reacts fast. An LLM call takes ~25s so it
        # CANNOT gate a 15s loop; the LLM rides alongside on a slower cadence
        # (every `llm_every` ticks) to narrate + learn, fire-and-forget so it
        # never blocks the heartbeat.
        self.interval = max(5.0, float(interval_sec))
        self.model = model
        self.llm_provider = llm_provider
        self.llm_endpoint = llm_endpoint
        self.llm_keep_alive = llm_keep_alive
        self.llm_every = max(1, int(llm_every))
        self.expectancy_path = Path(expectancy_path) if expectancy_path else None
        self.expectancy_refresh_sec = max(5.0, float(expectancy_refresh_sec))
        self._expectancy_stats: Dict[str, pax_expectancy.ExpectancyStats] = {}
        self._expectancy_loaded_ms = 0
        self.learning_refresh_sec = max(10.0, float(learning_refresh_sec))
        self._learning_loaded_ms = 0
        self.learning_summary: Dict[str, Any] = {}
        self._tick_n = 0
        self._narrating = False
        self.last_llm: Optional[Dict[str, Any]] = None
        self.armed = False
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._gen = 0          # generation guard: only the latest worker runs
        self.cycles = 0
        self.started_ms: Optional[int] = None
        self.last: Optional[Dict[str, Any]] = None
        # Operational risk-gate config (kill switch / stale data / session
        # limits). Resolved once from env at construction; the gate itself is
        # pure and deterministic.
        self._risk_config = pax_risk_gate.load_config()

    def _heartbeat_age_sec(self, now_ms: int) -> Optional[float]:
        """Age of the PRIOR heartbeat record in seconds, or None on the first
        cycle (bootstrapping -- the running loop is itself the current beat, so
        there is nothing stale to prove)."""
        last = self.last
        if not isinstance(last, dict):
            return None
        ts = last.get("ts_ms")
        if not ts:
            return None
        return max(0.0, (now_ms - int(ts)) / 1000.0)

    def _refresh_expectancy_stats(self, now_ms: int) -> None:
        if self.expectancy_path is None:
            return
        due_ms = self.expectancy_refresh_sec * 1000.0
        if self._expectancy_loaded_ms and now_ms - self._expectancy_loaded_ms < due_ms:
            return
        try:
            self._expectancy_stats = pax_expectancy.load_ifl_stats(self.expectancy_path)
            self._expectancy_loaded_ms = now_ms
        except Exception:
            self._expectancy_stats = {}
            self._expectancy_loaded_ms = now_ms

    def _refresh_learning_summary(self, now_ms: int) -> None:
        due_ms = self.learning_refresh_sec * 1000.0
        if self._learning_loaded_ms and now_ms - self._learning_loaded_ms < due_ms:
            return
        try:
            self.learning_summary = pax_trade_learning.summarize_learning(persist=True)
            self._learning_loaded_ms = now_ms
        except Exception as e:
            self.learning_summary = {"error": str(e)[:160]}
            self._learning_loaded_ms = now_ms

    def _execute_rule_plan(self, plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Execute the deterministic plan on the local sim.

        Returns the sim broker receipt for acted plans, or None for a no-op.
        The receipt carries bracket IDs, which the learning linker uses to
        attach later fills/PnL back to the original Pax thesis.
        """
        kw = {"alias": self.alias} if self.alias else {}
        if plan.get("flatten") or plan.get("cancel"):
            return pax_sim_tools.sim_flatten(
                reason=("rule: " + str(plan.get("reason")))[:150], **kw)
        o = plan.get("order")
        if o:
            return pax_sim_tools.sim_place_bracket(
                side=o["sidecmd"], qty=int(o["qty"]),
                entry_limit=o["entry_limit"], stop_loss=o["stop_loss"],
                take_profits=o["tps"], entry_stop=o.get("entry_stop"),
                tag=o.get("tag", "RULE"),
                reason=str(o.get("reason"))[:150], **kw)
        return None

    def _maybe_narrate(self, snap, st, now, now_ms, plan) -> None:
        """Fire-and-forget LLM commentary + lesson. Does NOT gate trades and
        runs on its own thread so a slow ~25s claude call never stalls the
        15s heartbeat."""
        if self._narrating:
            return
        self._narrating = True

        def _job():
            try:
                lessons = pax_sim_tools.read_lessons()
                calib = pax_sim_tools.read_calibration()
                ctx = build_context(snap, st, plan, lessons, calib)
                prompt = (SYSTEM_INSTRUCTION + "\n\n=== LIVE CONTEXT ===\n" + ctx +
                          "\n\nYou are NARRATING — the deterministic rule already "
                          "executes. Give your read; set lesson only on a real "
                          "learning point. action can be WAIT.")
                d = parse_decision(call_model_json(
                    prompt, provider=self.llm_provider, model=self.model,
                    endpoint=self.llm_endpoint, keep_alive=self.llm_keep_alive))
                self.last_llm = {"ts_ms": now_ms, "read": d.get("rationale"),
                                 "action": d.get("action")}
                lesson = (d.get("lesson") or "").strip()
                if lesson:
                    pax_sim_tools.write_lessons(
                        (lessons.rstrip() + "\n- " + lesson).strip())
            except Exception as e:
                self.last_llm = {"ts_ms": now_ms, "error": str(e)[:160]}
            finally:
                self._narrating = False

        threading.Thread(target=_job, daemon=True, name="pax-agent-narrate").start()

    def _cycle_once(self) -> Dict[str, Any]:
        now = datetime.datetime.now()
        now_ms = int(time.time() * 1000)
        snap = _fetch_snapshot(self.url)
        try:
            st = pax_sim_tools.sim_status(self.alias) if self.alias \
                else pax_sim_tools.sim_status()
        except Exception as e:
            st = {"position": {"size": 0}, "_status_error": str(e)}

        self._refresh_expectancy_stats(now_ms)
        self._refresh_learning_summary(now_ms)
        runtime_policy = (self.learning_summary.get("policy")
                          if isinstance(self.learning_summary, dict) else None)
        scorecard = (self.learning_summary.get("scorecard")
                     if isinstance(self.learning_summary, dict) else None)
        runtime_policy = pax_runtime_policy.guard_runtime_policy(
            runtime_policy, scorecard)
        # FAST: deterministic decision (instant) -> the live watcher/trigger.
        plan = pax_loop.decide(
            snap, st, now, now_ms, expectancy_stats=self._expectancy_stats,
            runtime_policy=runtime_policy)
        o = plan.get("order") or {}
        rec: Dict[str, Any] = {
            "ts_ms": now_ms, "heartbeat": True, "armed": self.armed,
            "baseline_state": plan.get("state"), "action": plan.get("action"),
            "governor": "ok", "rationale": plan.get("reason"),
            "mid": plan.get("mid"), "level": plan.get("level"),
            "alias": self.alias,
            "stype": plan.get("stype"),
            "setup_type": plan.get("setup_type"),
            "expectancy": plan.get("expectancy"),
            "expectancy_source": plan.get("expectancy_source"),
            "order": ({k: o.get(k) for k in
                       ("side", "entry_stop", "entry_limit", "stop_loss", "tps", "qty")}
                      if plan.get("order") else None),
        }
        if self.armed:
            # Operational risk gate at the LAST safe point before any broker
            # call. ENTRIES run the full gate (kill switch / stale heartbeat /
            # stale market / sim broker / session limits). EXITS reduce risk ->
            # only the kill switch may halt them. A blocked gate writes a clean
            # unified veto record; no order is placed and no broker receipt
            # exists. The deterministic pax_loop governor already owns strategy.
            is_entry = bool(plan.get("order"))
            is_exit = bool(plan.get("flatten") or plan.get("cancel"))
            halt = None
            if is_entry:
                halt = pax_risk_gate.evaluate_entry_gate(
                    now_ms=now_ms,
                    kill_switch_active=pax_sim_tools.kill_switch_active(),
                    heartbeat_age_sec=self._heartbeat_age_sec(now_ms),
                    market_age_sec=_market_age_sec(snap, now_ms),
                    sim_broker_ok=not st.get("_status_error"),
                    session=pax_risk_gate.session_counters_from_status(st),
                    config=self._risk_config)
                if halt.allowed:
                    halt = None
            elif is_exit and pax_sim_tools.kill_switch_active():
                halt = pax_risk_gate.kill_switch_result(now_ms)
            if halt is not None:
                _apply_risk_halt(rec, halt)
            else:
                try:
                    exec_result = self._execute_rule_plan(plan)
                    rec["exec"] = exec_result
                    rec["executed"] = exec_result is not None
                except Exception as e:
                    rec["exec_error"] = str(e); rec["executed"] = False

        # SLOW (every llm_every ticks): LLM narrates + learns, off-thread.
        self._tick_n += 1
        if self._tick_n % self.llm_every == 0:
            self._maybe_narrate(snap, st, now, now_ms, plan)
        if self.last_llm:
            rec["llm"] = self.last_llm

        self.last = rec
        self.cycles += 1
        try:
            # capped append: rotates the heartbeat log so it can't grow unbounded
            pax_sim_tools.append_line_capped(AGENT_LOG, json.dumps(rec, default=str))
        except Exception:
            pass
        try:
            pax_sim_calibration.update(st)
        except Exception:
            pass
        return rec

    def _run(self, gen: int) -> None:
        # Generation guard: a stop()/start() bumps self._gen, so a stale worker
        # retires at the top of its loop instead of double-placing alongside a
        # newer worker.
        while not self._stop.is_set() and self._gen == gen:
            try:
                self._cycle_once()
            except Exception as e:
                self.last = {"error": f"cycle: {e}", "ts_ms": int(time.time() * 1000)}
            if self._stop.wait(self.interval):
                break
        if self._gen == gen:
            self._running = False

    def start(self, armed: Optional[bool] = None) -> Dict[str, Any]:
        if armed is not None:
            self.armed = bool(armed)
        # Already running cleanly under a live thread -> don't spawn a second.
        if self._running and self._thread is not None and self._thread.is_alive():
            return self.status()
        # pax_ai is sim-only and has no live-order path. If it inherited the
        # bridge's BOOKMAP_ALLOW_TRADING=1, the local sim tools would refuse.
        # Force it off for this process so the paper agent can run; this can
        # never enable a live order because no live route exists here.
        os.environ["BOOKMAP_ALLOW_TRADING"] = ""
        self._gen += 1                 # retire any lingering prior worker
        gen = self._gen
        self._stop.clear()
        self._running = True
        self.started_ms = int(time.time() * 1000)
        self._thread = threading.Thread(target=self._run, args=(gen,),
                                        daemon=True, name="pax-agent-loop")
        self._thread.start()
        return self.status()

    def stop(self) -> Dict[str, Any]:
        self._gen += 1                 # current worker sees gen mismatch + exits
        self._stop.set()
        self._running = False
        return self.status()

    def set_armed(self, armed: bool) -> Dict[str, Any]:
        self.armed = bool(armed)
        return self.status()

    def status(self) -> Dict[str, Any]:
        return {"running": self._running, "armed": self.armed,
                "cycles": self.cycles, "interval_sec": self.interval,
                "model": self.model, "llm_provider": self.llm_provider,
                "llm_endpoint": self.llm_endpoint, "alias": self.alias,
                "expectancy_stats_n": len(self._expectancy_stats),
                "expectancy_loaded_ms": self._expectancy_loaded_ms,
                "learning": self.learning_summary,
                "started_ms": self.started_ms,
                "last": self.last}


_LOOP: Optional[AgentLoop] = None


def get_loop() -> AgentLoop:
    """Process-wide singleton the server endpoints drive."""
    global _LOOP
    if _LOOP is None:
        _LOOP = AgentLoop()
    return _LOOP
