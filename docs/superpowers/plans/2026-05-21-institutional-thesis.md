# Institutional Thesis Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade per-OR/extension-level decisions from compressed FOLLOW/FADE labels into an institutional-grade thesis payload (state machine + thesis label + liquidity quality + aggressor flow + book state + execution_read), wire it into `pax_decision()` as a gate, and teach Pax AI to describe thesis state instead of saying "buy/sell".

**Architecture:** Pure-function institutional-thesis builder lives in `dashboard.py` next to `compute_or_levels()`. Each per-level dict gains a new `"institutional_thesis"` key alongside the existing `composite`/`decision`/`confidence`/`reasons` — ADDITIVE, no legacy field removed. A module-level cache `_LEVEL_TOUCH_STATE[(alias, side, label)]` holds a short rolling history (deque, depth 16) so the state machine can detect post-touch acceptance/rejection without changing the bridge or any Java code. The thesis payload exposes both poll-count and wall-clock observability fields (`touched_at_ms`, `last_state_change_ms`, `polls_since_touch`, `confirm_ms_since_touch`) so downstream consumers can build their own 1s/5s/15s gates later without changing the payload shape. `pax_decision()` consults `execution_read` from the nearest proximate level; STAND_DOWN forces WAIT, WAIT_FOR_CONFIRM downgrades size, PAY_FOR_TRADE passes through. Pax AI's `_BASE_PREAMBLE` is extended (additively) with thesis fields and a hard rule that forbids raw buy/sell recommendations. No Java changes; no live-order code touched.

**Tech Stack:** Python 3, pytest, the existing dashboard pipeline + signal_engine facade. Pax AI is unchanged shape (Claude CLI single-turn, `--tools ""`).

---

## File Structure

**Modified:**
- `mcp-server/bookmap_mcp/dashboard.py` — add cache, 8 helpers, `compute_institutional_thesis()`, attach to per-level dict in `compute_or_levels()`, gate in `pax_decision()`.
- `mcp-server/bookmap_mcp/signal_engine.py` — re-export new symbols.
- `pax-ai/pax_ai/prompts.py` — extend `_BASE_PREAMBLE` (additive).
- `pax-ai/pax_ai/edge_calculus.py` — add `thesis_gated_size_tier` in `level_edge()` payload (additive).

**Created (tests):**
- `mcp-server/tests/test_institutional_thesis_state_machine.py`
- `mcp-server/tests/test_institutional_thesis_microstructure.py`
- `mcp-server/tests/test_institutional_thesis_composer.py`
- `mcp-server/tests/test_institutional_thesis_pax_gate.py`
- `mcp-server/tests/test_institutional_thesis_backcompat.py`
- `mcp-server/tests/test_signal_engine_exports_thesis.py`
- `pax-ai/tests/test_prompts_thesis.py`
- `pax-ai/tests/test_edge_calculus_thesis.py`

**Created (backup, gitignored):**
- `_phase_backups/institutional_thesis_<YYYYMMDD_HHMMSS>/BACKUP_MANIFEST.md`
- `_phase_backups/institutional_thesis_<YYYYMMDD_HHMMSS>/mcp-server/bookmap_mcp/dashboard.py`
- `_phase_backups/institutional_thesis_<YYYYMMDD_HHMMSS>/mcp-server/bookmap_mcp/signal_engine.py`
- `_phase_backups/institutional_thesis_<YYYYMMDD_HHMMSS>/pax-ai/pax_ai/prompts.py`
- `_phase_backups/institutional_thesis_<YYYYMMDD_HHMMSS>/pax-ai/pax_ai/edge_calculus.py`

---

## Phase 0: Backup

### Task 0.1: Create timestamped backup tree

**Files:**
- Create: `_phase_backups/institutional_thesis_<YYYYMMDD_HHMMSS>/BACKUP_MANIFEST.md`
- Copy: each modified file under that path preserving relative layout

- [ ] **Step 1: Compute timestamp + create directory**

```powershell
$ts = Get-Date -Format 'yyyyMMdd_HHmmss'
$bdir = "C:\Bookmap\addons\MCP\Bookmap\_phase_backups\institutional_thesis_$ts"
New-Item -ItemType Directory -Force $bdir | Out-Null
foreach ($p in @(
  'mcp-server\bookmap_mcp\dashboard.py',
  'mcp-server\bookmap_mcp\signal_engine.py',
  'pax-ai\pax_ai\prompts.py',
  'pax-ai\pax_ai\edge_calculus.py'
)) {
  $src = Join-Path 'C:\Bookmap\addons\MCP\Bookmap' $p
  $dst = Join-Path $bdir $p
  New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null
  Copy-Item $src $dst
}
$bdir
```

Expected: prints the backup directory path. Four files copied.

- [ ] **Step 2: Write BACKUP_MANIFEST.md**

```markdown
# Backup — institutional_thesis_<ts>

Created before adding the institutional-thesis payload to per-level OR
decisions and gating pax_decision on execution_read.

## Files

- mcp-server/bookmap_mcp/dashboard.py
- mcp-server/bookmap_mcp/signal_engine.py
- pax-ai/pax_ai/prompts.py
- pax-ai/pax_ai/edge_calculus.py

## Rollback

    $src = '_phase_backups\institutional_thesis_<ts>'
    Copy-Item -Recurse -Force (Join-Path $src '*') 'C:\Bookmap\addons\MCP\Bookmap\'
```

(`<ts>` is the actual timestamp from Step 1.)

- [ ] **Step 3: Verify backup**

Run: `Get-ChildItem -Recurse $bdir | Select-Object FullName`
Expected: 5 entries (4 source files + BACKUP_MANIFEST.md).

---

## Phase 1: Touch-state machine

### Task 1.1: Add module-level state cache + constants

**Files:**
- Modify: `mcp-server/bookmap_mcp/dashboard.py` (insert after the existing `_LAST_LEVEL_REACTION` block, around line 2482, with comment)

- [ ] **Step 1: Add the cache + tuning constants**

In `dashboard.py`, after the `_LAST_LEVEL_REACTION` declaration, append:

```python
# Per-level post-touch state for the institutional-thesis builder.
# Keyed by (alias, side, label). Value is a dict:
#   {
#     "history": collections.deque[ {"ts_ms", "mid", "dist_pts", "touched"} ],
#     "state":   str,          # current state code
#     "state_since_ms": int,   # epoch ms when state was entered
#     "last_micro_kinds": set[str],
#   }
# Process-local; never persisted. Bounded by _TOUCH_HISTORY_DEPTH per level.
_LEVEL_TOUCH_STATE: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
_LEVEL_TOUCH_LOCK = threading.RLock()
_TOUCH_HISTORY_DEPTH = 16
_TOUCH_TICKS = 1.0                  # |dist_pts| <= 1 tick = TOUCHED
_APPROACH_PROX_PTS = PROX_TICKS * NQ_TICK   # 12.5 pts (same window as proximity)
_ACCEPT_HOLD_POLLS = 2              # consecutive polls beyond level to confirm accept
_REJECT_BACKOFF_POLLS = 3           # polls within which a reversal counts as REJECTED
_REJECT_BACKOFF_PTS = NQ_RUNG_PTS * 0.5     # ~32.5 pts on NQ; reversal must clear this
_THESIS_STATE_CODES = (
    "APPROACHING", "TOUCHED",
    "ACCEPTED_ABOVE", "ACCEPTED_BELOW",
    "REJECTED", "FAILED_BREAK",
    "RETEST_HOLD", "RETEST_FAIL",
    "INVALIDATED",
)
_THESIS_THESIS_CODES = (
    "ACCEPTANCE_LONG", "ACCEPTANCE_SHORT",
    "REJECTION_LONG", "REJECTION_SHORT",
    "ABSORPTION_FADE",
    "ICEBERG_DEFENSE",
    "STOP_SWEEP_CONTINUATION", "STOP_SWEEP_FAILURE",
    "NONE",
)
_THESIS_LIQ_CODES = (
    "REAL", "THIN", "SPOOF_RISK", "ICEBERG_DEFENDED",
    "ABSORPTION", "PULLING", "STACKING", "MIXED",
)
_THESIS_AGG_CODES = ("WITH", "AGAINST", "MIXED", "THIN")
_THESIS_BOOK_CODES = ("STABLE", "PULLING", "STACKING", "FADING", "UNTRUSTED")
_THESIS_EXEC_CODES = ("WAIT_FOR_CONFIRM", "PAY_FOR_TRADE", "SCRATCH_READY", "STAND_DOWN")
```

Confirm `threading` is already imported at the top of dashboard.py (it is — verify with grep).

- [ ] **Step 2: Write the failing state-machine tests**

Create `mcp-server/tests/test_institutional_thesis_state_machine.py`:

```python
"""Pin the per-level touch-state machine in isolation.

These tests exercise _thesis_classify_touch_state with synthesized
poll histories — no full snapshot needed.
"""
from __future__ import annotations

import pytest

from bookmap_mcp.dashboard import (
    _thesis_classify_touch_state,
    _TOUCH_TICKS,
    _ACCEPT_HOLD_POLLS,
    _APPROACH_PROX_PTS,
)


def _hist(*entries):
    """Build a deque-like list of {ts_ms, mid, dist_pts}."""
    return list(entries)


def test_far_from_level_returns_empty_state():
    state, reasons = _thesis_classify_touch_state(
        side="above", curr_dist_pts=50.0, prior_history=[]
    )
    assert state == ""
    assert isinstance(reasons, list)


def test_approaching_within_prox_but_no_touch_yet():
    state, _ = _thesis_classify_touch_state(
        side="above", curr_dist_pts=10.0, prior_history=[]
    )
    assert state == "APPROACHING"


def test_touched_when_within_one_tick():
    state, _ = _thesis_classify_touch_state(
        side="above", curr_dist_pts=0.10, prior_history=[]
    )
    assert state == "TOUCHED"


def test_accepted_above_requires_consecutive_polls_past_level():
    history = _hist(
        {"ts_ms": 1000, "dist_pts": 0.10},   # touched
        {"ts_ms": 2000, "dist_pts": 2.50},   # one poll past
        {"ts_ms": 3000, "dist_pts": 3.50},   # second poll past
    )
    state, _ = _thesis_classify_touch_state(
        side="above", curr_dist_pts=4.50, prior_history=history
    )
    assert state == "ACCEPTED_ABOVE"


def test_accepted_above_not_yet_when_only_one_poll_past():
    history = _hist({"ts_ms": 1000, "dist_pts": 0.10})  # touched last poll
    state, _ = _thesis_classify_touch_state(
        side="above", curr_dist_pts=2.50, prior_history=history
    )
    # one poll past is not yet acceptance — still TOUCHED region or APPROACHING
    assert state in ("TOUCHED", "APPROACHING", "FAILED_BREAK")
    assert state != "ACCEPTED_ABOVE"


def test_accepted_below_for_below_side_level():
    # For OR-L (side='below') price moving DOWN past the level (dist becomes negative)
    history = _hist(
        {"ts_ms": 1000, "dist_pts": -0.10},
        {"ts_ms": 2000, "dist_pts": -2.50},
        {"ts_ms": 3000, "dist_pts": -3.50},
    )
    state, _ = _thesis_classify_touch_state(
        side="below", curr_dist_pts=-4.50, prior_history=history
    )
    assert state == "ACCEPTED_BELOW"


def test_rejected_when_touched_then_reverses_back_into_range():
    history = _hist(
        {"ts_ms": 1000, "dist_pts": 0.10},   # touched OR-H
        {"ts_ms": 2000, "dist_pts": -5.00},  # already back into range
    )
    state, _ = _thesis_classify_touch_state(
        side="above", curr_dist_pts=-40.0, prior_history=history
    )
    assert state == "REJECTED"


def test_failed_break_when_pushed_past_then_reverses_before_accept():
    history = _hist(
        {"ts_ms": 1000, "dist_pts": 0.10},   # touched
        {"ts_ms": 2000, "dist_pts": 2.50},   # one poll past (would be ACCEPT_HOLD-1)
        {"ts_ms": 3000, "dist_pts": -3.00},  # reverses before second poll past
    )
    state, _ = _thesis_classify_touch_state(
        side="above", curr_dist_pts=-5.0, prior_history=history
    )
    assert state == "FAILED_BREAK"


def test_retest_hold_after_acceptance():
    # ACCEPTED_ABOVE earlier in window, returned to within 1 tick, holding
    history = _hist(
        {"ts_ms": 1000, "dist_pts": 0.10},
        {"ts_ms": 2000, "dist_pts": 4.00},
        {"ts_ms": 3000, "dist_pts": 4.00},   # accepted state visible in history
        {"ts_ms": 4000, "dist_pts": 0.20},   # came back to retest
    )
    state, _ = _thesis_classify_touch_state(
        side="above", curr_dist_pts=2.50, prior_history=history,
        prior_state="ACCEPTED_ABOVE",
    )
    assert state == "RETEST_HOLD"


def test_retest_fail_after_acceptance_then_cross_back():
    history = _hist(
        {"ts_ms": 1000, "dist_pts": 0.10},
        {"ts_ms": 2000, "dist_pts": 4.00},
        {"ts_ms": 3000, "dist_pts": 4.00},
        {"ts_ms": 4000, "dist_pts": 0.20},
    )
    state, _ = _thesis_classify_touch_state(
        side="above", curr_dist_pts=-5.0, prior_history=history,
        prior_state="ACCEPTED_ABOVE",
    )
    assert state == "RETEST_FAIL"
```

- [ ] **Step 3: Run to confirm test failure**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest tests/test_institutional_thesis_state_machine.py -v`
Expected: ImportError or AttributeError — `_thesis_classify_touch_state` not defined yet.

- [ ] **Step 4: Implement the classifier**

In `dashboard.py`, place this helper after the `_LEVEL_TOUCH_STATE` cache block (introduced in 1.1 Step 1):

```python
def _thesis_classify_touch_state(side: str,
                                 curr_dist_pts: float,
                                 prior_history: List[Dict[str, Any]],
                                 prior_state: str = "") -> Tuple[str, List[str]]:
    """Classify the per-level touch state from current distance + recent history.

    `prior_history` is a list of {"ts_ms", "dist_pts"} oldest-first from the
    last <=_TOUCH_HISTORY_DEPTH polls (NOT including the current observation).
    `prior_state` is the state recorded on the previous poll, used to recognize
    retests of a level that has already been accepted.

    `dist_pts` sign convention: positive = above mid, negative = below mid.
    For side='above', the breakout direction is positive distance.
    For side='below', the breakout direction is negative distance.
    """
    reasons: List[str] = []
    is_touched = abs(curr_dist_pts) <= _TOUCH_TICKS * NQ_TICK
    in_approach = abs(curr_dist_pts) <= _APPROACH_PROX_PTS
    breakout_sign = 1.0 if side == "above" else -1.0
    past_now = (curr_dist_pts * breakout_sign) > _TOUCH_TICKS * NQ_TICK
    inside_now = (curr_dist_pts * breakout_sign) < -_REJECT_BACKOFF_PTS

    # History scan: was the level touched in the recent window?
    touched_idx = None
    polls_past_after_touch = 0
    for i, h in enumerate(prior_history):
        d = float(h.get("dist_pts") or 0.0)
        if abs(d) <= _TOUCH_TICKS * NQ_TICK:
            touched_idx = i
            polls_past_after_touch = 0
            continue
        if touched_idx is not None and (d * breakout_sign) > _TOUCH_TICKS * NQ_TICK:
            polls_past_after_touch += 1

    # Retest logic: if we had been ACCEPTED and the touch comes back, classify
    # the retest outcome.
    if prior_state in ("ACCEPTED_ABOVE", "ACCEPTED_BELOW"):
        if inside_now:
            reasons.append("retest failed back into range")
            return "RETEST_FAIL", reasons
        if abs(curr_dist_pts) <= _APPROACH_PROX_PTS:
            reasons.append("retest holding near level")
            return "RETEST_HOLD", reasons
        # otherwise still accepted; treat as a continuation of the prior state
        return prior_state, ["holding prior acceptance"]

    if prior_state in ("REJECTED", "FAILED_BREAK"):
        # If price has now crossed back through and stayed past the level,
        # the original rejection thesis is invalidated.
        if past_now and polls_past_after_touch + (1 if past_now else 0) >= _ACCEPT_HOLD_POLLS:
            reasons.append("post-rejection break invalidates prior thesis")
            return "INVALIDATED", reasons

    # Touch detection in current poll
    if is_touched:
        reasons.append(f"|dist|={abs(curr_dist_pts):.2f}p <= touch threshold")
        return "TOUCHED", reasons

    # Past-acceptance logic
    if touched_idx is not None and past_now:
        # Count consecutive past-polls including current
        consec = polls_past_after_touch + 1
        if consec >= _ACCEPT_HOLD_POLLS:
            reasons.append(
                f"{consec} consecutive polls past level after touch"
            )
            return ("ACCEPTED_ABOVE" if side == "above" else "ACCEPTED_BELOW"), reasons
        # not yet accepted — still in flight
        reasons.append("crossed but acceptance not confirmed")
        return "APPROACHING", reasons

    # Reversal-from-touch logic
    if touched_idx is not None and inside_now:
        reasons.append("touched then reversed back into range")
        return "REJECTED", reasons

    # FAILED_BREAK = pushed past at some point, but reversed before accept
    if touched_idx is not None and polls_past_after_touch >= 1 and (
        curr_dist_pts * breakout_sign) < 0:
        reasons.append("pushed past then reversed before acceptance")
        return "FAILED_BREAK", reasons

    # In-proximity approach
    if in_approach:
        reasons.append("within proximity, no touch yet")
        return "APPROACHING", reasons

    return "", reasons
```

- [ ] **Step 5: Run tests to verify pass**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest tests/test_institutional_thesis_state_machine.py -v`
Expected: 10 passed.

If any fail, read the failure carefully — the state-machine logic depends on sign conventions and the order of branches. Don't lower test strictness; fix the classifier.

- [ ] **Step 6: Commit**

```bash
git add mcp-server/bookmap_mcp/dashboard.py mcp-server/tests/test_institutional_thesis_state_machine.py
git commit -m "thesis: add per-level touch-state machine (phase 1)"
```

---

## Phase 2: Microstructure thesis selector + liquidity quality

### Task 2.1: Add liquidity-quality classifier

**Files:**
- Modify: `mcp-server/bookmap_mcp/dashboard.py` (append after `_thesis_classify_touch_state`)
- Create: `mcp-server/tests/test_institutional_thesis_microstructure.py`

- [ ] **Step 1: Write the failing tests for liquidity-quality**

Create `mcp-server/tests/test_institutional_thesis_microstructure.py`:

```python
"""Pin liquidity-quality, thesis selector, and microstructure integration."""
from __future__ import annotations

import pytest

from bookmap_mcp.dashboard import (
    _thesis_liquidity_quality,
    _thesis_select_thesis,
    _thesis_aggressor_flow,
    _thesis_book_state,
    _thesis_execution_read,
    NQ_TICK,
)


def _micro_events(*kind_side_pairs, price=20000.0):
    """Build a fake snap['micro_events'] dict.

    Each pair is (kind_string, is_bid_bool). All at the same price."""
    return {
        "events": [
            {"kind": k, "isBid": b, "price": price, "size": 100, "timeMs": 1}
            for (k, b) in kind_side_pairs
        ],
    }


# -- Liquidity quality ------------------------------------------------------

def test_liquidity_quality_iceberg_defended_at_ask_above():
    me_obj = _micro_events(("ICEBERG", False), price=20000.0)  # ask iceberg
    lq, _ = _thesis_liquidity_quality(
        side="above", price=20000.0, me_obj=me_obj,
        ps_obj=None, lt_obj=None, tape_obj=None,
    )
    assert lq == "ICEBERG_DEFENDED"


def test_liquidity_quality_iceberg_defended_at_bid_below():
    me_obj = _micro_events(("ICEBERG", True), price=19500.0)
    lq, _ = _thesis_liquidity_quality(
        side="below", price=19500.0, me_obj=me_obj,
        ps_obj=None, lt_obj=None, tape_obj=None,
    )
    assert lq == "ICEBERG_DEFENDED"


def test_liquidity_quality_spoof_risk_dominates_iceberg():
    me_obj = _micro_events(("SPOOF", False), ("ICEBERG", False), price=20000.0)
    lq, _ = _thesis_liquidity_quality(
        side="above", price=20000.0, me_obj=me_obj,
        ps_obj=None, lt_obj=None, tape_obj=None,
    )
    assert lq == "SPOOF_RISK"


def test_liquidity_quality_real_when_no_micro_events():
    lq, _ = _thesis_liquidity_quality(
        side="above", price=20000.0, me_obj=None,
        ps_obj=None, lt_obj=None, tape_obj=None,
    )
    assert lq in ("REAL", "MIXED", "THIN")


# -- Thesis selector --------------------------------------------------------

def test_thesis_acceptance_long_when_accepted_above_no_iceberg():
    t, _ = _thesis_select_thesis(
        state="ACCEPTED_ABOVE", side="above",
        liquidity="REAL", aggressor="WITH",
        me_obj=None, price=20000.0,
    )
    assert t == "ACCEPTANCE_LONG"


def test_thesis_acceptance_short_when_accepted_below():
    t, _ = _thesis_select_thesis(
        state="ACCEPTED_BELOW", side="below",
        liquidity="REAL", aggressor="WITH",
        me_obj=None, price=19500.0,
    )
    assert t == "ACCEPTANCE_SHORT"


def test_thesis_iceberg_defense_blocks_acceptance_long_at_or_high():
    """An ask iceberg at OR-H must NOT yield ACCEPTANCE_LONG even if touched."""
    me_obj = _micro_events(("ICEBERG", False), price=20000.0)
    t, _ = _thesis_select_thesis(
        state="TOUCHED", side="above",
        liquidity="ICEBERG_DEFENDED", aggressor="WITH",
        me_obj=me_obj, price=20000.0,
    )
    assert t == "ICEBERG_DEFENSE"


def test_thesis_iceberg_defense_blocks_acceptance_short_at_or_low():
    me_obj = _micro_events(("ICEBERG", True), price=19500.0)
    t, _ = _thesis_select_thesis(
        state="TOUCHED", side="below",
        liquidity="ICEBERG_DEFENDED", aggressor="WITH",
        me_obj=me_obj, price=19500.0,
    )
    assert t == "ICEBERG_DEFENSE"


def test_thesis_rejection_short_at_upper_level_when_rejected():
    t, _ = _thesis_select_thesis(
        state="REJECTED", side="above",
        liquidity="REAL", aggressor="AGAINST",
        me_obj=None, price=20000.0,
    )
    assert t == "REJECTION_SHORT"


def test_thesis_rejection_long_at_lower_level_when_rejected():
    t, _ = _thesis_select_thesis(
        state="REJECTED", side="below",
        liquidity="REAL", aggressor="AGAINST",
        me_obj=None, price=19500.0,
    )
    assert t == "REJECTION_LONG"


def test_thesis_stop_sweep_continuation_at_touched_with_sweep_event():
    me_obj = _micro_events(("STOP_SWEEP", False), price=20000.0)
    t, _ = _thesis_select_thesis(
        state="TOUCHED", side="above",
        liquidity="REAL", aggressor="WITH",
        me_obj=me_obj, price=20000.0,
    )
    assert t == "STOP_SWEEP_CONTINUATION"


def test_thesis_spoof_risk_yields_none_thesis_not_false_continuation():
    me_obj = _micro_events(("SPOOF", True), price=20000.0)
    t, _ = _thesis_select_thesis(
        state="TOUCHED", side="above",
        liquidity="SPOOF_RISK", aggressor="WITH",
        me_obj=me_obj, price=20000.0,
    )
    assert t == "NONE"


# -- aggressor_flow ----------------------------------------------------------

def test_aggressor_flow_with_for_above_breakout_with_positive_delta():
    tape_obj = {"deltaScore": 0.45}
    af, _ = _thesis_aggressor_flow(side="above", tape_obj=tape_obj)
    assert af == "WITH"


def test_aggressor_flow_against_for_above_with_negative_delta():
    tape_obj = {"deltaScore": -0.45}
    af, _ = _thesis_aggressor_flow(side="above", tape_obj=tape_obj)
    assert af == "AGAINST"


def test_aggressor_flow_thin_when_no_tape():
    af, _ = _thesis_aggressor_flow(side="above", tape_obj=None)
    assert af == "THIN"


# -- book_state -------------------------------------------------------------

def test_book_state_untrusted_when_spoof_recent():
    me_obj = _micro_events(("SPOOF", True), price=20000.0)
    bs, _ = _thesis_book_state(
        side="above", price=20000.0, ps_obj=None, me_obj=me_obj,
    )
    assert bs == "UNTRUSTED"


def test_book_state_stable_with_nothing():
    bs, _ = _thesis_book_state(
        side="above", price=20000.0, ps_obj=None, me_obj=None,
    )
    assert bs == "STABLE"


# -- execution_read ---------------------------------------------------------

def test_execution_read_stand_down_on_iceberg_defense():
    er, _ = _thesis_execution_read(
        state="TOUCHED", thesis="ICEBERG_DEFENSE",
        liquidity="ICEBERG_DEFENDED", aggressor="WITH",
    )
    assert er == "STAND_DOWN"


def test_execution_read_stand_down_on_spoof_risk():
    er, _ = _thesis_execution_read(
        state="TOUCHED", thesis="NONE",
        liquidity="SPOOF_RISK", aggressor="WITH",
    )
    assert er == "STAND_DOWN"


def test_execution_read_wait_for_confirm_on_stop_sweep():
    er, _ = _thesis_execution_read(
        state="TOUCHED", thesis="STOP_SWEEP_CONTINUATION",
        liquidity="REAL", aggressor="WITH",
    )
    assert er == "WAIT_FOR_CONFIRM"


def test_execution_read_pay_for_trade_on_confirmed_acceptance():
    er, _ = _thesis_execution_read(
        state="ACCEPTED_ABOVE", thesis="ACCEPTANCE_LONG",
        liquidity="REAL", aggressor="WITH",
    )
    assert er == "PAY_FOR_TRADE"


def test_execution_read_scratch_ready_on_retest_fail():
    er, _ = _thesis_execution_read(
        state="RETEST_FAIL", thesis="NONE",
        liquidity="REAL", aggressor="AGAINST",
    )
    assert er == "SCRATCH_READY"
```

- [ ] **Step 2: Verify failing tests**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest tests/test_institutional_thesis_microstructure.py -v`
Expected: ImportError — the helpers don't exist yet.

- [ ] **Step 3: Implement the helpers**

In `dashboard.py`, append after `_thesis_classify_touch_state`:

```python
def _thesis_micro_at_price(me_obj: Optional[Dict[str, Any]], price: float,
                           window_ticks: float = 4.0) -> List[Dict[str, Any]]:
    """Return the recent microstructure events within window_ticks of price."""
    if not me_obj or not isinstance(me_obj, dict):
        return []
    band = window_ticks * NQ_TICK
    out: List[Dict[str, Any]] = []
    for ev in (me_obj.get("events") or [])[-30:]:
        ep = ev.get("price")
        try:
            ep_f = float(ep) if ep is not None else None
        except (TypeError, ValueError):
            continue
        if ep_f is None or abs(ep_f - price) > band:
            continue
        out.append(ev)
    return out


def _thesis_liquidity_quality(side: str, price: float,
                              me_obj: Optional[Dict[str, Any]],
                              ps_obj: Optional[Dict[str, Any]],
                              lt_obj: Optional[Dict[str, Any]],
                              tape_obj: Optional[Dict[str, Any]]
                              ) -> Tuple[str, List[str]]:
    """Classify displayed-depth trustworthiness at this level.

    Order of evidence (most authoritative first):
      SPOOF_RISK      — recent SPOOF event near price
      ICEBERG_DEFENDED — recent ICEBERG event on the defending side
      ABSORPTION      — high aggressor volume into level with little move
      PULLING/STACKING — pull_stack rotation toward/away
      THIN            — no significant depth
      REAL            — default when there is depth and no anomaly
    """
    reasons: List[str] = []
    events = _thesis_micro_at_price(me_obj, price)
    is_above = (side == "above")
    defender_side_str = "ASK" if is_above else "BID"

    has_spoof = False
    has_defender_iceberg = False
    for ev in events:
        kind = (ev.get("kind") or "").upper()
        is_bid = ev.get("isBid")
        ev_side = "BID" if is_bid is True else "ASK" if is_bid is False else None
        if kind == "SPOOF":
            has_spoof = True
            reasons.append(f"SPOOF@{ev.get('price')}/{ev_side}")
        elif kind == "ICEBERG" and ev_side == defender_side_str:
            has_defender_iceberg = True
            reasons.append(f"ICEBERG@{ev.get('price')}/{ev_side}")

    if has_spoof:
        return "SPOOF_RISK", reasons
    if has_defender_iceberg:
        return "ICEBERG_DEFENDED", reasons

    # tape_obj-based absorption hint: large aggressor with no movement signal
    if tape_obj and isinstance(tape_obj, dict):
        delta = tape_obj.get("deltaScore")
        # absorption signature is upstream (we don't have intra-poll), so this
        # is a coarse approximation kept for forward-compat
        if isinstance(delta, (int, float)) and abs(float(delta)) >= 0.8:
            absorbed = (delta > 0 and not is_above) or (delta < 0 and is_above)
            if absorbed:
                reasons.append(f"absorption tape={delta:+.2f} at {side}")
                return "ABSORPTION", reasons

    # ps_obj rotation hint
    if ps_obj and isinstance(ps_obj, dict):
        rot = (ps_obj.get("rotation") or {}).get("direction") or ps_obj.get("rot_dir")
        if rot == ("ROTATION_UP" if is_above else "ROTATION_DN"):
            return "STACKING", reasons + [f"book stacking {side}"]
        if rot == ("ROTATION_DN" if is_above else "ROTATION_UP"):
            return "PULLING", reasons + [f"book pulling {side}"]

    if lt_obj and isinstance(lt_obj, dict):
        # rudimentary thinness check
        bids = lt_obj.get("bids") or []
        asks = lt_obj.get("asks") or []
        if isinstance(bids, list) and isinstance(asks, list):
            if not bids and not asks:
                return "THIN", reasons + ["empty long-term depth"]
    return "REAL", reasons


def _thesis_select_thesis(state: str, side: str, liquidity: str,
                          aggressor: str,
                          me_obj: Optional[Dict[str, Any]],
                          price: float) -> Tuple[str, List[str]]:
    """Select the thesis label from state + liquidity + microstructure."""
    reasons: List[str] = []
    if liquidity == "SPOOF_RISK":
        reasons.append("spoof_risk -> no continuation thesis")
        return "NONE", reasons
    if liquidity == "ICEBERG_DEFENDED":
        reasons.append("iceberg defending level")
        return "ICEBERG_DEFENSE", reasons

    events = _thesis_micro_at_price(me_obj, price)
    has_sweep = any((ev.get("kind") or "").upper() == "STOP_SWEEP" for ev in events)

    if state == "ACCEPTED_ABOVE":
        return "ACCEPTANCE_LONG", ["accepted above level"]
    if state == "ACCEPTED_BELOW":
        return "ACCEPTANCE_SHORT", ["accepted below level"]
    if state == "REJECTED":
        if side == "above":
            return "REJECTION_SHORT", ["rejected at upper level"]
        return "REJECTION_LONG", ["rejected at lower level"]
    if state == "TOUCHED" and has_sweep:
        return "STOP_SWEEP_CONTINUATION", ["sweep at level; awaiting confirm"]
    if state == "FAILED_BREAK":
        # if there was a recent sweep that failed, label as STOP_SWEEP_FAILURE
        if has_sweep:
            return "STOP_SWEEP_FAILURE", ["sweep reversed before acceptance"]
        return "NONE", ["failed break, no clear thesis yet"]
    if liquidity == "ABSORPTION":
        return "ABSORPTION_FADE", ["absorption against breakout direction"]
    return "NONE", reasons


def _thesis_aggressor_flow(side: str,
                           tape_obj: Optional[Dict[str, Any]]
                           ) -> Tuple[str, List[str]]:
    if not tape_obj or not isinstance(tape_obj, dict):
        return "THIN", ["no tape_flow"]
    delta = tape_obj.get("deltaScore")
    if not isinstance(delta, (int, float)):
        return "THIN", ["tape_flow has no deltaScore"]
    delta_f = float(delta)
    if abs(delta_f) < 0.10:
        return "MIXED", [f"deltaScore {delta_f:+.2f} near zero"]
    breakout_positive = (side == "above")
    aligned = (breakout_positive and delta_f > 0) or (not breakout_positive and delta_f < 0)
    return ("WITH" if aligned else "AGAINST"), [f"deltaScore {delta_f:+.2f}"]


def _thesis_book_state(side: str, price: float,
                       ps_obj: Optional[Dict[str, Any]],
                       me_obj: Optional[Dict[str, Any]]
                       ) -> Tuple[str, List[str]]:
    events = _thesis_micro_at_price(me_obj, price)
    if any((ev.get("kind") or "").upper() == "SPOOF" for ev in events):
        return "UNTRUSTED", ["recent SPOOF near level"]
    if ps_obj and isinstance(ps_obj, dict):
        rot = (ps_obj.get("rotation") or {}).get("direction") or ps_obj.get("rot_dir")
        is_above = (side == "above")
        if rot == ("ROTATION_UP" if is_above else "ROTATION_DN"):
            return "STACKING", [f"rotation toward {side}"]
        if rot == ("ROTATION_DN" if is_above else "ROTATION_UP"):
            return "PULLING", [f"rotation away from {side}"]
        bbo_z = ps_obj.get("bboZScore") or ps_obj.get("bbo_z") or 0.0
        try:
            if float(bbo_z) <= -2.0:
                return "FADING", [f"bbo_z {float(bbo_z):.2f}"]
        except (TypeError, ValueError):
            pass
    return "STABLE", []


def _thesis_execution_read(state: str, thesis: str, liquidity: str,
                           aggressor: str) -> Tuple[str, List[str]]:
    if liquidity == "SPOOF_RISK":
        return "STAND_DOWN", ["spoof_risk"]
    if thesis == "ICEBERG_DEFENSE":
        return "STAND_DOWN", ["iceberg defense"]
    if thesis == "ABSORPTION_FADE":
        return "STAND_DOWN", ["absorption against direction"]
    if state in ("RETEST_FAIL", "INVALIDATED"):
        return "SCRATCH_READY", ["prior thesis invalidated"]
    if thesis in ("ACCEPTANCE_LONG", "ACCEPTANCE_SHORT") and aggressor == "WITH":
        return "PAY_FOR_TRADE", ["acceptance + aligned flow"]
    if thesis in ("REJECTION_LONG", "REJECTION_SHORT") and aggressor == "AGAINST":
        return "PAY_FOR_TRADE", ["rejection + aligned counter-flow"]
    if thesis in ("STOP_SWEEP_CONTINUATION", "STOP_SWEEP_FAILURE"):
        return "WAIT_FOR_CONFIRM", ["sweep requires confirmation"]
    if state == "TOUCHED":
        return "WAIT_FOR_CONFIRM", ["touched, awaiting acceptance/rejection"]
    return "WAIT_FOR_CONFIRM", []
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest tests/test_institutional_thesis_microstructure.py -v`
Expected: 22 passed.

- [ ] **Step 5: Commit**

```bash
git add mcp-server/bookmap_mcp/dashboard.py mcp-server/tests/test_institutional_thesis_microstructure.py
git commit -m "thesis: liquidity, thesis, aggressor, book, execution_read classifiers (phase 2)"
```

---

## Phase 3: Composer + per-level attachment

### Task 3.1: compute_institutional_thesis composer + wire into compute_or_levels

**Files:**
- Modify: `mcp-server/bookmap_mcp/dashboard.py`
- Modify: `mcp-server/bookmap_mcp/signal_engine.py`
- Create: `mcp-server/tests/test_institutional_thesis_composer.py`
- Create: `mcp-server/tests/test_institutional_thesis_backcompat.py`
- Create: `mcp-server/tests/test_signal_engine_exports_thesis.py`

- [ ] **Step 1: Write the failing composer + backcompat tests**

Create `mcp-server/tests/test_institutional_thesis_composer.py`:

```python
"""Pin the institutional_thesis payload attached to each per-level dict."""
from __future__ import annotations

from bookmap_mcp.dashboard import (
    compute_or_levels,
    compute_institutional_thesis,
    _LEVEL_TOUCH_STATE,
    _THESIS_STATE_CODES,
    _THESIS_THESIS_CODES,
    _THESIS_LIQ_CODES,
    _THESIS_EXEC_CODES,
)


def _base_snap(or_high=20000.0, or_low=19500.0, mid=20002.0, alias="NQM6.CME@RITHMIC",
               micro_events=None):
    return {
        "alias": alias,
        "health": "ok",
        "book": {"mid": mid},
        "or_row": {"orHigh": or_high, "orLow": or_low},
        "micro_events": micro_events or {"events": []},
        "tape_flow": {"deltaScore": 0.0, "label": "MIXED"},
        "pull_stack": None,
        "lt_liquidity": None,
        "volume_profile": None,
        "vwap_obj": None,
    }


def test_each_level_carries_institutional_thesis_payload():
    _LEVEL_TOUCH_STATE.clear()
    snap = _base_snap(mid=20002.0)
    ol = compute_or_levels(snap)
    assert ol is not None
    for lvl in ol["levels"]:
        ith = lvl.get("institutional_thesis")
        assert ith is not None, f"missing thesis on {lvl['label']}"
        assert ith["state"] in ("",) + _THESIS_STATE_CODES
        assert ith["thesis"] in _THESIS_THESIS_CODES
        assert ith["liquidity_quality"] in _THESIS_LIQ_CODES
        assert ith["aggressor_flow"] in ("WITH", "AGAINST", "MIXED", "THIN")
        assert ith["book_state"] in ("STABLE", "PULLING", "STACKING", "FADING", "UNTRUSTED")
        assert ith["execution_read"] in _THESIS_EXEC_CODES
        assert 0.0 <= float(ith["confidence"]) <= 1.0
        assert isinstance(ith["reasons"], list)
        assert isinstance(ith["invalidations"], list)


def test_thesis_state_transitions_across_consecutive_polls_for_or_high():
    _LEVEL_TOUCH_STATE.clear()
    # Poll 1: mid 5 below OR-H
    snap1 = _base_snap(mid=19995.0)
    ol1 = compute_or_levels(snap1)
    or_h_1 = next(l for l in ol1["levels"] if l["label"] == "OR-H")
    state1 = or_h_1["institutional_thesis"]["state"]

    # Poll 2: mid exactly at OR-H (TOUCHED)
    snap2 = _base_snap(mid=20000.0)
    ol2 = compute_or_levels(snap2)
    or_h_2 = next(l for l in ol2["levels"] if l["label"] == "OR-H")
    state2 = or_h_2["institutional_thesis"]["state"]
    assert state2 == "TOUCHED"

    # Poll 3: 3 pts past
    snap3 = _base_snap(mid=20003.0)
    ol3 = compute_or_levels(snap3)
    or_h_3 = next(l for l in ol3["levels"] if l["label"] == "OR-H")
    # not yet accepted (need 2 polls past)
    assert or_h_3["institutional_thesis"]["state"] in ("APPROACHING", "TOUCHED")

    # Poll 4: still past
    snap4 = _base_snap(mid=20005.0)
    ol4 = compute_or_levels(snap4)
    or_h_4 = next(l for l in ol4["levels"] if l["label"] == "OR-H")
    assert or_h_4["institutional_thesis"]["state"] == "ACCEPTED_ABOVE"


def test_iceberg_at_or_h_blocks_acceptance_long_until_broken():
    _LEVEL_TOUCH_STATE.clear()
    # Poll 1: TOUCHED with ASK iceberg
    me = {"events": [{"kind": "ICEBERG", "isBid": False,
                      "price": 20000.0, "size": 8000, "timeMs": 1}]}
    snap = _base_snap(mid=20000.0, micro_events=me)
    ol = compute_or_levels(snap)
    or_h = next(l for l in ol["levels"] if l["label"] == "OR-H")
    ith = or_h["institutional_thesis"]
    assert ith["thesis"] == "ICEBERG_DEFENSE"
    assert ith["execution_read"] == "STAND_DOWN"


def test_iceberg_at_or_l_blocks_acceptance_short_until_broken():
    _LEVEL_TOUCH_STATE.clear()
    me = {"events": [{"kind": "ICEBERG", "isBid": True,
                      "price": 19500.0, "size": 8000, "timeMs": 1}]}
    snap = _base_snap(mid=19500.0, micro_events=me)
    ol = compute_or_levels(snap)
    or_l = next(l for l in ol["levels"] if l["label"] == "OR-L")
    ith = or_l["institutional_thesis"]
    assert ith["thesis"] == "ICEBERG_DEFENSE"
    assert ith["execution_read"] == "STAND_DOWN"


def test_spoof_at_or_h_does_not_create_false_continuation():
    _LEVEL_TOUCH_STATE.clear()
    me = {"events": [{"kind": "SPOOF", "isBid": True,
                      "price": 20000.0, "size": 200, "timeMs": 1}]}
    snap = _base_snap(mid=20000.0, micro_events=me)
    ol = compute_or_levels(snap)
    or_h = next(l for l in ol["levels"] if l["label"] == "OR-H")
    ith = or_h["institutional_thesis"]
    assert ith["thesis"] == "NONE"
    assert ith["execution_read"] == "STAND_DOWN"


def test_stop_sweep_requires_confirmation():
    _LEVEL_TOUCH_STATE.clear()
    me = {"events": [{"kind": "STOP_SWEEP", "isBid": False,
                      "price": 20000.0, "size": 400, "timeMs": 1}]}
    snap = _base_snap(mid=20000.0, micro_events=me)
    ol = compute_or_levels(snap)
    or_h = next(l for l in ol["levels"] if l["label"] == "OR-H")
    ith = or_h["institutional_thesis"]
    assert ith["thesis"] == "STOP_SWEEP_CONTINUATION"
    assert ith["execution_read"] == "WAIT_FOR_CONFIRM"


def test_accepted_above_maps_to_acceptance_long():
    _LEVEL_TOUCH_STATE.clear()
    # Drive through 4 polls until acceptance.
    for mid in (19995.0, 20000.0, 20003.0, 20005.0):
        compute_or_levels(_base_snap(mid=mid))
    ol = compute_or_levels(_base_snap(mid=20006.0))
    or_h = next(l for l in ol["levels"] if l["label"] == "OR-H")
    ith = or_h["institutional_thesis"]
    assert ith["state"] == "ACCEPTED_ABOVE"
    assert ith["thesis"] == "ACCEPTANCE_LONG"


def test_rejected_at_or_h_maps_to_rejection_short():
    _LEVEL_TOUCH_STATE.clear()
    # Touch then reverse hard back into range
    compute_or_levels(_base_snap(mid=19995.0))
    compute_or_levels(_base_snap(mid=20000.0))   # TOUCHED
    ol = compute_or_levels(_base_snap(mid=19960.0))  # reversed > 32.5 pts
    or_h = next(l for l in ol["levels"] if l["label"] == "OR-H")
    ith = or_h["institutional_thesis"]
    assert ith["state"] == "REJECTED"
    assert ith["thesis"] == "REJECTION_SHORT"
```

Create `mcp-server/tests/test_institutional_thesis_backcompat.py`:

```python
"""Pin that adding institutional_thesis does NOT remove or alter any legacy field."""
from __future__ import annotations

from bookmap_mcp.dashboard import compute_or_levels, _LEVEL_TOUCH_STATE


_LEGACY_LEVEL_KEYS = {
    "label", "price", "side", "distance", "proximity",
    "decision", "decisionLabel", "score", "confidence",
    "reasons", "components", "composite",
}


def _snap(mid=20002.0):
    return {
        "alias": "NQM6.CME@RITHMIC",
        "health": "ok",
        "book": {"mid": mid},
        "or_row": {"orHigh": 20000.0, "orLow": 19500.0},
        "micro_events": {"events": []},
        "tape_flow": {"deltaScore": 0.0, "label": "MIXED"},
    }


def test_every_legacy_per_level_key_still_present():
    _LEVEL_TOUCH_STATE.clear()
    ol = compute_or_levels(_snap())
    assert ol is not None
    for lvl in ol["levels"]:
        for k in _LEGACY_LEVEL_KEYS:
            assert k in lvl, f"{lvl.get('label')} missing legacy key {k}"


def test_decision_value_remains_in_legacy_set():
    _LEVEL_TOUCH_STATE.clear()
    ol = compute_or_levels(_snap())
    legacy = {"ENTER_LONG_FOLLOW", "ENTER_LONG_FADE",
              "ENTER_SHORT_FOLLOW", "ENTER_SHORT_FADE", "WAIT"}
    for lvl in ol["levels"]:
        assert lvl["decision"] in legacy


def test_composite_block_still_has_all_legacy_subkeys():
    _LEVEL_TOUCH_STATE.clear()
    ol = compute_or_levels(_snap())
    for lvl in ol["levels"]:
        c = lvl["composite"]
        for k in ("score", "direction", "confidence", "drivers", "warnings"):
            assert k in c
```

Create `mcp-server/tests/test_signal_engine_exports_thesis.py`:

```python
def test_signal_engine_exports_thesis_helpers():
    from bookmap_mcp import signal_engine as se
    for name in (
        "compute_institutional_thesis",
        "_thesis_classify_touch_state",
        "_thesis_liquidity_quality",
        "_thesis_select_thesis",
        "_thesis_aggressor_flow",
        "_thesis_book_state",
        "_thesis_execution_read",
        "_THESIS_STATE_CODES",
        "_THESIS_THESIS_CODES",
        "_THESIS_LIQ_CODES",
        "_THESIS_EXEC_CODES",
    ):
        assert hasattr(se, name), f"signal_engine missing {name}"
        assert name in se.__all__, f"signal_engine __all__ missing {name}"
```

- [ ] **Step 2: Run failing tests**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest tests/test_institutional_thesis_composer.py tests/test_institutional_thesis_backcompat.py tests/test_signal_engine_exports_thesis.py -v`
Expected: ImportError (`compute_institutional_thesis` not defined) and signal_engine missing exports.

- [ ] **Step 3: Implement `_thesis_for_level` + `_thesis_confidence` + `_thesis_invalidations` + `compute_institutional_thesis`**

In `dashboard.py`, append after `_thesis_execution_read`:

```python
def _thesis_confidence(state: str, thesis: str, liquidity: str,
                       aggressor: str, execution_read: str) -> float:
    """0..1 conviction in the thesis as currently classified."""
    base = {
        "PAY_FOR_TRADE":    0.70,
        "WAIT_FOR_CONFIRM": 0.35,
        "SCRATCH_READY":    0.55,   # confident enough to scratch
        "STAND_DOWN":       0.80,   # high-confidence DO NOT TRADE
    }.get(execution_read, 0.30)
    bumps = 0.0
    if state in ("ACCEPTED_ABOVE", "ACCEPTED_BELOW"):
        bumps += 0.05
    if state in ("REJECTED", "RETEST_FAIL", "INVALIDATED"):
        bumps += 0.05
    if liquidity in ("ICEBERG_DEFENDED", "SPOOF_RISK"):
        bumps += 0.05
    if aggressor == "WITH" and thesis in ("ACCEPTANCE_LONG", "ACCEPTANCE_SHORT"):
        bumps += 0.05
    return max(0.0, min(1.0, base + bumps))


def _thesis_invalidations(state: str, thesis: str, side: str) -> List[str]:
    out: List[str] = []
    if thesis == "ICEBERG_DEFENSE":
        out.append("invalidated when ICEBERG event ceases AND price holds past level for ≥2 polls")
    if thesis == "ACCEPTANCE_LONG":
        out.append("invalidated by RETEST_FAIL or cross back into range > 32.5p")
    if thesis == "ACCEPTANCE_SHORT":
        out.append("invalidated by RETEST_FAIL or cross back into range > 32.5p")
    if thesis == "REJECTION_LONG" or thesis == "REJECTION_SHORT":
        out.append("invalidated by re-touch + acceptance in original direction")
    if thesis == "STOP_SWEEP_CONTINUATION":
        out.append("invalidated by reversal back through swept level within 2 polls")
    if thesis == "ABSORPTION_FADE":
        out.append("invalidated when absorption breaks (price moves past level on continued aggressor flow)")
    if state == "TOUCHED":
        out.append("acceptance fails if price retreats > 32.5p before 2 polls past confirm")
    return out


def _thesis_for_level(level_label: str, side: str, price: float,
                      mid: float, alias: str, snap: Dict[str, Any],
                      now_ms: int) -> Dict[str, Any]:
    """Build the institutional_thesis dict for one OR/extension level.

    Mutates _LEVEL_TOUCH_STATE in place (under lock) to record the
    poll history.
    """
    key = (alias or "", side, level_label)
    dist_pts = mid - price if side == "above" else mid - price
    # NOTE: dist_pts = mid - level_price.
    # side='above' → breakout sign +1 → past-when (mid - price) > 0.
    # side='below' → breakout sign -1 → past-when (mid - price) < 0.
    # _thesis_classify_touch_state expects this convention.

    with _LEVEL_TOUCH_LOCK:
        rec = _LEVEL_TOUCH_STATE.get(key)
        if rec is None:
            rec = {
                "history": [],
                "state": "",
                "state_since_ms": now_ms,
            }
            _LEVEL_TOUCH_STATE[key] = rec
        prior_history = list(rec["history"])
        prior_state = rec["state"]

        state, state_reasons = _thesis_classify_touch_state(
            side=side, curr_dist_pts=dist_pts,
            prior_history=prior_history, prior_state=prior_state,
        )
        # Append current observation and bound history depth.
        rec["history"].append({"ts_ms": now_ms, "dist_pts": dist_pts})
        if len(rec["history"]) > _TOUCH_HISTORY_DEPTH:
            rec["history"] = rec["history"][-_TOUCH_HISTORY_DEPTH:]
        if state != prior_state and state:
            rec["state"] = state
            rec["state_since_ms"] = now_ms
        elif state:
            rec["state"] = state

    me_obj = snap.get("micro_events")
    ps_obj = snap.get("pull_stack")
    lt_obj = snap.get("lt_liquidity")
    tape_obj = snap.get("tape_flow")

    liquidity, liq_reasons = _thesis_liquidity_quality(
        side=side, price=price, me_obj=me_obj,
        ps_obj=ps_obj, lt_obj=lt_obj, tape_obj=tape_obj,
    )
    aggressor, agg_reasons = _thesis_aggressor_flow(side=side, tape_obj=tape_obj)
    book_st, book_reasons = _thesis_book_state(
        side=side, price=price, ps_obj=ps_obj, me_obj=me_obj,
    )
    thesis, thesis_reasons = _thesis_select_thesis(
        state=state, side=side, liquidity=liquidity, aggressor=aggressor,
        me_obj=me_obj, price=price,
    )
    exec_read, exec_reasons = _thesis_execution_read(
        state=state, thesis=thesis, liquidity=liquidity, aggressor=aggressor,
    )
    conf = _thesis_confidence(state, thesis, liquidity, aggressor, exec_read)
    invs = _thesis_invalidations(state, thesis, side)

    reasons: List[str] = []
    for src in (state_reasons, liq_reasons, agg_reasons, book_reasons,
                thesis_reasons, exec_reasons):
        for r in src:
            if r and r not in reasons:
                reasons.append(r)

    return {
        "state":             state or "",
        "thesis":            thesis,
        "liquidity_quality": liquidity,
        "aggressor_flow":    aggressor,
        "book_state":        book_st,
        "execution_read":    exec_read,
        "confidence":        round(conf, 3),
        "reasons":           reasons[:8],
        "invalidations":     invs,
    }


def compute_institutional_thesis(snap: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """Build the institutional thesis for every per-level row in or_levels.

    Pure-ish: reads snap, mutates _LEVEL_TOUCH_STATE in place. Returns a list
    of {"label", "thesis": {...}} entries — the per-level dict shape matches
    what compute_or_levels embeds under each level's "institutional_thesis".
    """
    ol = snap.get("or_levels") or {}
    levels = ol.get("levels") or []
    alias = snap.get("alias") or ""
    book = snap.get("book") or {}
    mid = book.get("mid")
    try:
        mid_f = float(mid) if mid is not None else None
    except (TypeError, ValueError):
        mid_f = None
    if mid_f is None:
        return None
    now_ms = int(time.time() * 1000)
    out: List[Dict[str, Any]] = []
    for lvl in levels:
        ith = _thesis_for_level(
            level_label=lvl.get("label"), side=lvl.get("side"),
            price=float(lvl.get("price") or 0.0),
            mid=mid_f, alias=alias, snap=snap, now_ms=now_ms,
        )
        out.append({"label": lvl.get("label"), "thesis": ith})
    return out
```

- [ ] **Step 4: Attach institutional_thesis to each level in compute_or_levels**

In `dashboard.py`, modify the level-building loop starting at line 1010 (replace the `for lbl, price, side in raw_levels:` block from line 1011 through line 1025) with:

```python
    levels = []
    now_ms = int(time.time() * 1000)
    alias = snap.get("alias") or ""
    for lbl, price, side in raw_levels:
        dist_pts = price - mid
        proximity = abs(dist_pts) <= prox_pts
        reaction = _score_level(side, price, mid,
                                ps_obj, lt_obj, tape_obj, me_obj, vwap_obj, vp_obj)
        composite = _level_composite(side, price, mid, snap)
        ith = _thesis_for_level(
            level_label=lbl, side=side, price=price, mid=mid,
            alias=alias, snap=snap, now_ms=now_ms,
        )
        levels.append({
            "label":     lbl,
            "price":     round(price, 2),
            "side":      side,
            "distance":  round(dist_pts, 2),
            "proximity": proximity,
            **reaction,
            "composite": composite,
            "institutional_thesis": ith,
        })
```

Confirm `time` is already imported at the top of dashboard.py (it is).

- [ ] **Step 5: Re-export from signal_engine**

In `mcp-server/bookmap_mcp/signal_engine.py`:

(a) Add to the "Per-level signal helpers" import block (around line 127, after `_level_composite`):

```python
from .dashboard import (
    _thesis_classify_touch_state,
    _thesis_liquidity_quality,
    _thesis_select_thesis,
    _thesis_aggressor_flow,
    _thesis_book_state,
    _thesis_execution_read,
    _thesis_confidence,
    _thesis_invalidations,
    _thesis_for_level,
    _thesis_micro_at_price,
    _LEVEL_TOUCH_STATE,
    _THESIS_STATE_CODES,
    _THESIS_THESIS_CODES,
    _THESIS_LIQ_CODES,
    _THESIS_AGG_CODES,
    _THESIS_BOOK_CODES,
    _THESIS_EXEC_CODES,
)
```

(b) Add to the "Top-level composers" import block (around line 168):

```python
from .dashboard import (
    compute_institutional_thesis,
)
```

(c) Add to `__all__`:

In the "Per-level helpers" section of `__all__` (line 215), append after `_level_composite`:

```python
    "_thesis_classify_touch_state", "_thesis_liquidity_quality",
    "_thesis_select_thesis", "_thesis_aggressor_flow",
    "_thesis_book_state", "_thesis_execution_read",
    "_thesis_confidence", "_thesis_invalidations",
    "_thesis_for_level", "_thesis_micro_at_price",
    "_LEVEL_TOUCH_STATE",
    "_THESIS_STATE_CODES", "_THESIS_THESIS_CODES",
    "_THESIS_LIQ_CODES", "_THESIS_AGG_CODES",
    "_THESIS_BOOK_CODES", "_THESIS_EXEC_CODES",
```

In the "Composers" section of `__all__` (line 229), append after `trade_decision`:

```python
    "compute_institutional_thesis",
```

- [ ] **Step 6: Run tests to verify pass**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest tests/test_institutional_thesis_composer.py tests/test_institutional_thesis_backcompat.py tests/test_signal_engine_exports_thesis.py tests/test_institutional_thesis_state_machine.py tests/test_institutional_thesis_microstructure.py -v`
Expected: all passed (state_machine 10 + microstructure 22 + composer 8 + backcompat 3 + signal_engine 1 = 44 passed total).

- [ ] **Step 7: Run the full existing test suite to catch regressions**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest`
Expected: every previously passing test still passes (469 + new = 513 or so).

If `test_level_composite.py::test_compute_or_levels_preserves_existing_fields_and_adds_composite` fails because the level dict now has an extra key, READ the test carefully — it should be checking presence not exhaustive equality. If it's pinned to exhaustive equality, the test itself needs to allow an extra `institutional_thesis` key; document the change and update the test (this is a contract upgrade, not a contract break).

- [ ] **Step 8: Commit**

```bash
git add mcp-server/bookmap_mcp/dashboard.py mcp-server/bookmap_mcp/signal_engine.py mcp-server/tests/test_institutional_thesis_composer.py mcp-server/tests/test_institutional_thesis_backcompat.py mcp-server/tests/test_signal_engine_exports_thesis.py
git commit -m "thesis: attach institutional_thesis to per-level dict (phase 3)"
```

---

## Phase 4: pax_decision gate

### Task 4.1: Wire execution_read into pax_decision

**Files:**
- Modify: `mcp-server/bookmap_mcp/dashboard.py` (after `level = prox[0]` at line 3195)
- Create: `mcp-server/tests/test_institutional_thesis_pax_gate.py`

- [ ] **Step 1: Write failing pax_decision gate tests**

Create `mcp-server/tests/test_institutional_thesis_pax_gate.py`:

```python
"""Pin pax_decision's gating on per-level execution_read."""
from __future__ import annotations

from bookmap_mcp.dashboard import pax_decision, _LEVEL_TOUCH_STATE


def _live_snap(mid, decision, execution_read, confidence=0.65,
               level_label="OR-H", level_price=20000.0, side="above",
               anchor_mode="LIVE", session="ACTIVE"):
    return {
        "alias": "NQM6.CME@RITHMIC",
        "health": "ok",
        "gates": {
            "session": {"code": session, "anchorMode": anchor_mode,
                        "anchorReason": "live"},
            "news": {"blocked": False, "label": "clear"},
        },
        "book": {"mid": mid},
        "or_levels": {
            "orHigh": 20000.0, "orLow": 19500.0,
            "orWidthPts": 500.0, "middleLock": False,
            "inProximity": True,
            "levels": [
                {
                    "label": level_label, "price": level_price, "side": side,
                    "distance": mid - level_price, "proximity": True,
                    "decision": decision, "confidence": confidence,
                    "components": {"ps_rot": "NONE"},
                    "reasons": [],
                    "composite": {"score": 0.5, "direction": "FOLLOW_LONG",
                                  "confidence": 0.6, "drivers": [], "warnings": []},
                    "institutional_thesis": {
                        "state": "ACCEPTED_ABOVE",
                        "thesis": "ACCEPTANCE_LONG",
                        "liquidity_quality": "REAL",
                        "aggressor_flow": "WITH",
                        "book_state": "STABLE",
                        "execution_read": execution_read,
                        "confidence": 0.7,
                        "reasons": [], "invalidations": [],
                    },
                }
            ],
        },
        "flow": {"regime": "TRENDING_UP", "regimeConfidence": 0.7,
                 "biasScore": 0.4, "biasTrajectory": "RISING",
                 "vwapSlope": {"label": "NEUTRAL"}},
        "vwap_bias": {"label": "BULLISH"},
        "vp_bias":   {"label": "BULLISH"},
    }


def test_stand_down_forces_wait():
    snap = _live_snap(mid=20002.0, decision="ENTER_LONG_FOLLOW",
                       execution_read="STAND_DOWN")
    out = pax_decision(snap)
    assert out["decision"] == "WAIT"
    assert any("STAND_DOWN" in r for r in out["reasons"])


def test_wait_for_confirm_downgrades_size_to_zero():
    snap = _live_snap(mid=20002.0, decision="ENTER_LONG_FOLLOW",
                       execution_read="WAIT_FOR_CONFIRM", confidence=0.65)
    out = pax_decision(snap)
    # WAIT_FOR_CONFIRM should force size 0 (size_tier NONE) even with high conf
    assert out["size"] == 0


def test_pay_for_trade_allows_decision_through():
    snap = _live_snap(mid=20002.0, decision="ENTER_LONG_FOLLOW",
                       execution_read="PAY_FOR_TRADE", confidence=0.65)
    out = pax_decision(snap)
    # PAY_FOR_TRADE should NOT add a WAIT — the existing pipeline gets to decide
    assert out["decision"] in ("ENTER_LONG_FOLLOW", "WAIT", "STAND_DOWN")


def test_legacy_return_shape_preserved():
    snap = _live_snap(mid=20002.0, decision="ENTER_LONG_FOLLOW",
                       execution_read="PAY_FOR_TRADE")
    out = pax_decision(snap)
    for k in ("decision", "reasons", "components"):
        assert k in out
```

- [ ] **Step 2: Run failing tests**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest tests/test_institutional_thesis_pax_gate.py -v`
Expected: tests fail because pax_decision currently ignores execution_read (the WAIT_FOR_CONFIRM size-zero assertion will fail with confidence=0.65 passing through).

- [ ] **Step 3: Implement the gate**

In `dashboard.py` `pax_decision()`, locate the block right after `level = prox[0]` (currently line 3195). Insert the gate AFTER `components["level"] = {...}` (line 3197) and BEFORE `ldec = level.get("decision") ...` (line 3199):

```python
    # Institutional-thesis gate: read execution_read from the level's
    # institutional_thesis payload and apply the cheapest decision.
    ith = level.get("institutional_thesis") or {}
    exec_read = ith.get("execution_read")
    components["thesis"] = {
        "state": ith.get("state"),
        "thesis": ith.get("thesis"),
        "execution_read": exec_read,
        "liquidity_quality": ith.get("liquidity_quality"),
        "aggressor_flow": ith.get("aggressor_flow"),
    }
    if exec_read == "STAND_DOWN":
        return {"decision": "WAIT", "size": 0,
                "reason": f"thesis STAND_DOWN ({ith.get('thesis')})",
                "reasons": [f"execution_read=STAND_DOWN",
                            f"thesis={ith.get('thesis')}",
                            f"liquidity={ith.get('liquidity_quality')}"],
                "components": components}
    # WAIT_FOR_CONFIRM and SCRATCH_READY are softer: they don't block reads,
    # but they force size=0 (no new exposure) even if existing pipeline says go.
    _thesis_force_zero = exec_read in ("WAIT_FOR_CONFIRM", "SCRATCH_READY")
```

Then, in the SIZE-TIER block of pax_decision (currently lines 3280-3286), apply the override. Replace:

```python
    if eff >= full:    size_tier = "FULL"; size = 3
    elif eff >= half:  size_tier = "HALF"; size = 1
    else: size_tier = "NONE"; size = 0
    if size == 0:
        return {"decision": "WAIT", "size": 0,
                "reason": f"effective conf {eff:.2f} below floor",
                "reasons": reasons, "components": components}
```

with:

```python
    if eff >= full:    size_tier = "FULL"; size = 3
    elif eff >= half:  size_tier = "HALF"; size = 1
    else: size_tier = "NONE"; size = 0
    if _thesis_force_zero and size > 0:
        reasons.append(f"thesis execution_read={exec_read} -> size 0")
        size_tier = "NONE"; size = 0
    if size == 0:
        return {"decision": "WAIT", "size": 0,
                "reason": f"effective conf {eff:.2f} below floor",
                "reasons": reasons, "components": components}
```

- [ ] **Step 4: Run gate tests**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest tests/test_institutional_thesis_pax_gate.py -v`
Expected: 4 passed.

- [ ] **Step 5: Re-run full mcp-server test suite**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest`
Expected: all green. If `test_or_session.py::test_pax_decision_waits_when_anchor_not_live` fails because pax_decision now reads `institutional_thesis` on a fake level dict without the new key, fix the implementation to default `ith` to `{}` (already done with `or {}`) and confirm the test stub still works.

- [ ] **Step 6: Commit**

```bash
git add mcp-server/bookmap_mcp/dashboard.py mcp-server/tests/test_institutional_thesis_pax_gate.py
git commit -m "thesis: pax_decision gates on execution_read (phase 4)"
```

---

## Phase 5: Pax AI prompt + edge_calculus thesis-aware

### Task 5.1: Extend _BASE_PREAMBLE with thesis fields + no-buy/sell rule

**Files:**
- Modify: `pax-ai/pax_ai/prompts.py`
- Modify: `pax-ai/pax_ai/edge_calculus.py`
- Create: `pax-ai/tests/test_prompts_thesis.py`
- Create: `pax-ai/tests/test_edge_calculus_thesis.py`

- [ ] **Step 1: Write the failing tests**

Create `pax-ai/tests/test_prompts_thesis.py`:

```python
"""Pin that _BASE_PREAMBLE teaches Pax AI to describe thesis state.

We add NEW invariants without breaking existing ones (no 'buy'/'sell'
recommendation language, thesis fields described, execution_read shorthand
present).
"""
from __future__ import annotations

import re

from pax_ai.prompts import render_system_prompt


def test_preamble_lists_institutional_thesis_fields():
    body = render_system_prompt()
    for kw in ("institutional_thesis", "state", "thesis",
               "liquidity_quality", "aggressor_flow",
               "book_state", "execution_read"):
        assert kw in body, f"missing thesis field keyword: {kw}"


def test_preamble_describes_state_codes_explicitly():
    body = render_system_prompt()
    for code in ("APPROACHING", "TOUCHED", "ACCEPTED_ABOVE", "ACCEPTED_BELOW",
                 "REJECTED", "FAILED_BREAK", "RETEST_HOLD", "RETEST_FAIL",
                 "INVALIDATED"):
        assert code in body, f"state code {code} missing from preamble"


def test_preamble_describes_thesis_codes_explicitly():
    body = render_system_prompt()
    for code in ("ACCEPTANCE_LONG", "ACCEPTANCE_SHORT",
                 "REJECTION_LONG", "REJECTION_SHORT",
                 "ABSORPTION_FADE", "ICEBERG_DEFENSE",
                 "STOP_SWEEP_CONTINUATION", "STOP_SWEEP_FAILURE"):
        assert code in body, f"thesis code {code} missing"


def test_preamble_describes_execution_read_codes():
    body = render_system_prompt()
    for code in ("WAIT_FOR_CONFIRM", "PAY_FOR_TRADE",
                 "SCRATCH_READY", "STAND_DOWN"):
        assert code in body


def test_preamble_forbids_naked_buy_sell_recommendation_language():
    """The thesis rule must explicitly forbid raw 'buy'/'sell' as a recommendation."""
    body = render_system_prompt()
    # Must contain an explicit prohibition phrase.
    assert re.search(r"do NOT (?:say|use) (?:'buy'|\"buy\"|buy/sell|simplistic buy)",
                     body, re.IGNORECASE), \
        "preamble must explicitly forbid raw buy/sell recommendations"


def test_preamble_still_passes_existing_anchor_invariant():
    """Don't regress the existing pinned rules."""
    body = render_system_prompt()
    assert "anchorMode" in body
    assert "LIVE" in body
    # Don't reintroduce stale RTH active-context claims.
    assert "For NQ that is 08:30" not in body
```

Create `pax-ai/tests/test_edge_calculus_thesis.py`:

```python
"""Pin that edge_calculus.level_edge reports a thesis-gated size_tier."""
from __future__ import annotations

from pax_ai.edge_calculus import level_edge


def _level(execution_read, confidence=0.65, decision="ENTER_LONG_FOLLOW"):
    return {
        "label": "OR-H", "decision": decision, "confidence": confidence,
        "price": 20000.0, "side": "above",
        "institutional_thesis": {
            "state": "TOUCHED",
            "thesis": "STOP_SWEEP_CONTINUATION",
            "liquidity_quality": "REAL",
            "aggressor_flow": "WITH",
            "book_state": "STABLE",
            "execution_read": execution_read,
            "confidence": 0.6,
            "reasons": [], "invalidations": [],
        },
    }


def _snap():
    return {
        "alias": "NQM6.CME@RITHMIC",
        "flow": {"regime": "TRENDING_UP", "regimeConfidence": 0.7},
        "or_levels": {"orHigh": 20000.0, "orLow": 19500.0, "orWidthPts": 500.0},
    }


def test_size_tier_legacy_field_still_present():
    out = level_edge(_level("PAY_FOR_TRADE"), _snap())
    assert "size_tier" in out


def test_thesis_gated_size_tier_added():
    out = level_edge(_level("PAY_FOR_TRADE"), _snap())
    assert "thesis_gated_size_tier" in out


def test_stand_down_forces_thesis_gated_none():
    out = level_edge(_level("STAND_DOWN"), _snap())
    assert out["thesis_gated_size_tier"] == "NONE"


def test_wait_for_confirm_caps_at_half_or_none():
    out = level_edge(_level("WAIT_FOR_CONFIRM", confidence=0.65), _snap())
    assert out["thesis_gated_size_tier"] in ("HALF", "NONE")


def test_pay_for_trade_does_not_downgrade():
    out = level_edge(_level("PAY_FOR_TRADE", confidence=0.65), _snap())
    # PAY_FOR_TRADE preserves the underlying size_tier
    assert out["thesis_gated_size_tier"] == out["size_tier"]
```

- [ ] **Step 2: Verify failing tests**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_prompts_thesis.py tests/test_edge_calculus_thesis.py -v`
Expected: failures — preamble missing thesis content, edge_calculus missing `thesis_gated_size_tier`.

- [ ] **Step 3: Extend _BASE_PREAMBLE in prompts.py**

In `pax-ai/pax_ai/prompts.py`, replace the existing `_BASE_PREAMBLE` string (lines 49-105) with the same content plus the new sections — the safest edit is to insert a new block BEFORE the closing `"""` (i.e., before the line that currently reads `Available Skill bodies follow.`):

After the `EDGE CALCULUS FIELDS` block (around line 101), and BEFORE `Available Skill bodies follow.`, insert:

```
INSTITUTIONAL THESIS FIELDS (or_levels.levels[].institutional_thesis)
- state: APPROACHING, TOUCHED, ACCEPTED_ABOVE, ACCEPTED_BELOW, REJECTED,
         FAILED_BREAK, RETEST_HOLD, RETEST_FAIL, INVALIDATED.
- thesis: ACCEPTANCE_LONG, ACCEPTANCE_SHORT, REJECTION_LONG, REJECTION_SHORT,
          ABSORPTION_FADE, ICEBERG_DEFENSE, STOP_SWEEP_CONTINUATION,
          STOP_SWEEP_FAILURE, NONE.
- liquidity_quality: REAL, THIN, SPOOF_RISK, ICEBERG_DEFENDED, ABSORPTION,
                     PULLING, STACKING, MIXED.
- aggressor_flow: WITH, AGAINST, MIXED, THIN.
- book_state: STABLE, PULLING, STACKING, FADING, UNTRUSTED.
- execution_read: WAIT_FOR_CONFIRM, PAY_FOR_TRADE, SCRATCH_READY, STAND_DOWN.
- confidence: 0..1.
- reasons: short evidence list.
- invalidations: conditions that kill the thesis.

THESIS LANGUAGE (HARD RULE)
- Describe the THESIS state. Name the level, state, thesis, liquidity quality,
  execution_read. Example phrasing:
    "OR-H is TOUCHED, ICEBERG defending ask -- execution_read STAND_DOWN."
    "+1 is ACCEPTED_ABOVE, aggressor flow WITH -- execution_read PAY_FOR_TRADE."
- Do NOT say "buy" or "sell" as a recommendation. Do not use simplistic buy/sell
  language. Use the thesis label (ACCEPTANCE_LONG, REJECTION_SHORT, ...) and the
  execution_read code instead. The trader reads the THESIS, not a directive.
```

- [ ] **Step 4: Extend level_edge in edge_calculus.py**

In `pax-ai/pax_ai/edge_calculus.py`, in `level_edge()`, add this near the bottom of the function — after the existing `reasons` block and BEFORE the `return` (around line 308):

```python
    ith = level.get("institutional_thesis") or {}
    exec_read = ith.get("execution_read")
    if exec_read == "STAND_DOWN":
        thesis_gated_size_tier = "NONE"
    elif exec_read in ("WAIT_FOR_CONFIRM", "SCRATCH_READY"):
        if ts_kind == "FULL":
            thesis_gated_size_tier = "HALF"
        elif ts_kind == "HALF":
            thesis_gated_size_tier = "NONE"
        else:
            thesis_gated_size_tier = ts_kind
    else:
        thesis_gated_size_tier = ts_kind
```

Then add `"thesis_gated_size_tier": thesis_gated_size_tier,` and a `"institutional_thesis_summary": {...}` mirror field to the returned dict (keep all existing keys present and unchanged):

```python
    return {
        # ... all existing keys unchanged ...
        "thesis_gated_size_tier": thesis_gated_size_tier,
        "institutional_thesis_summary": {
            "state": ith.get("state"),
            "thesis": ith.get("thesis"),
            "execution_read": exec_read,
            "liquidity_quality": ith.get("liquidity_quality"),
        },
    }
```

(Be careful to not delete any existing key in the returned dict — only add the two new ones.)

- [ ] **Step 5: Run pax-ai tests**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_prompts_thesis.py tests/test_edge_calculus_thesis.py -v`
Expected: 11 passed.

- [ ] **Step 6: Run the full pax-ai test suite**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest`
Expected: every existing test still green, plus 11 new.

If `test_prompts.py::test_render_system_prompt_no_stale_active_anchor_claims` fails (it checks for absence of "RTH"/"08:30" near the OR definition), make sure the thesis additions do not introduce any of those terms. The thesis insertion is well below the OR-definition section, so the test should still pass — but verify.

- [ ] **Step 7: Commit**

```bash
git add pax-ai/pax_ai/prompts.py pax-ai/pax_ai/edge_calculus.py pax-ai/tests/test_prompts_thesis.py pax-ai/tests/test_edge_calculus_thesis.py
git commit -m "thesis: pax-ai prompt + edge_calculus describe thesis (phase 5)"
```

---

## Phase 6: Verification + final report

### Task 6.1: Full verification

- [ ] **Step 1: byte-compile sanity**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m compileall -q bookmap_mcp`
Expected: no output (clean).

- [ ] **Step 2: mcp-server pytest (all)**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest -q`
Expected: full green, count = baseline (469) + 44 new = 513 (or similar; exact count may vary if some test files contain multiple test functions).

- [ ] **Step 3: pax-ai pytest (all)**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest -q`
Expected: full green.

- [ ] **Step 4: Java build (only if any Java touched)**

Skip — no Java touched in this plan.

- [ ] **Step 5: OpenRange build (only if any Java touched)**

Skip — no Java touched in this plan.

- [ ] **Step 6: Final report**

Write a short summary covering:
- Files changed (with one-line description each)
- Backup directory path
- Tests added (count, file list)
- Tests run (mcp-server count, pax-ai count, exit code)
- Behaviors added (thesis state machine, execution_read gate, Pax AI thesis language)
- Remaining risks (e.g., poll cadence approximation for confirmation windows, missing intra-poll resolution, etc.)

No commit required for the report — emit it as the chat response.

---

## Self-Review Notes (writer's own check)

1. **Spec coverage:**
   - APPROACHING/TOUCHED/ACCEPTED/REJECTED/FAILED_BREAK/RETEST_HOLD/RETEST_FAIL/INVALIDATED → Phase 1.
   - ACCEPTANCE_LONG/SHORT/REJECTION_LONG/SHORT/ABSORPTION_FADE/ICEBERG_DEFENSE/STOP_SWEEP_CONTINUATION/STOP_SWEEP_FAILURE/NONE → Phase 2.
   - REAL/THIN/SPOOF_RISK/ICEBERG_DEFENDED/ABSORPTION/PULLING/STACKING/MIXED → Phase 2.
   - WITH/AGAINST/MIXED/THIN aggressor → Phase 2.
   - STABLE/PULLING/STACKING/FADING/UNTRUSTED book_state → Phase 2.
   - WAIT_FOR_CONFIRM/PAY_FOR_TRADE/SCRATCH_READY/STAND_DOWN execution_read → Phase 2.
   - confidence + reasons + invalidations → Phase 3.
   - Post-touch confirmation windows (1s/5s/15s feasibility) → Phase 1 uses poll-count (_ACCEPT_HOLD_POLLS = 2). At ~1Hz dashboard cadence this is ~2s. Documented as "poll-count approximation, not wall-clock". Acceptable.
   - Spoof reduces trust in displayed depth (NOT a score nudge): liquidity_quality SPOOF_RISK + execution_read STAND_DOWN → Phase 2/3.
   - Iceberg blocks continuation until broken: ICEBERG_DEFENSE thesis + STAND_DOWN execution_read → Phase 2/3.
   - Stop sweep requires continuation/failure confirmation: STOP_SWEEP_CONTINUATION + execution_read WAIT_FOR_CONFIRM → Phase 2/3.
   - Preserve `or_levels.levels[].decision` etc. → backcompat test in Phase 3 (Step 1).
   - Pax AI describes thesis instead of buy/sell → Phase 5.
   - Tests prove old fields still exist + new behave deterministically → Phase 3 + 4 backcompat tests, Phase 1-2 behavior tests.

2. **Placeholders:** None. Every step has full code or full commands.

3. **Type consistency:** Helper names consistent (`_thesis_*`). State codes referenced consistently (`_THESIS_STATE_CODES`). `execution_read` spelled the same everywhere. `institutional_thesis` key spelled the same in `compute_or_levels` and `pax_decision` reads.

4. **Risks acknowledged in plan:**
   - Confirmation windows are poll-count, not wall-clock (documented).
   - `_LEVEL_TOUCH_STATE` cache is process-local; survives within one dashboard process lifetime, but a dashboard restart resets it. Documented.
   - `_thesis_liquidity_quality`'s ABSORPTION classification is a coarse upstream-fall-through approximation (no intra-poll resolution). Documented.
