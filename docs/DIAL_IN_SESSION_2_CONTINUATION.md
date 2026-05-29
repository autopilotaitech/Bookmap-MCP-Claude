# Dial-In Session 2 — Continuation (operator-selected 2h trio)

**Window:** 2026-05-28 ~15:07 CT. Operator AFK; picked M2/T4/M4 from the
punch list as the max-value-in-2h set before ETH. Strict TDD, real evidence.

---

## Shipped

| Item | File(s) | Tests (all watched RED first) | Status |
|---|---|---|---|
| **M2** — micro_events decay 180s -> 60s + exp (30s half-life) + freshness scaling | `institutional_flow.py` | +3 in `test_institutional_flow.py` (48/48) | OK |
| **T4** — `flow.trade_eligibility` (LIVE_OK / DIAL_IN_ONLY / NO_TRADE) | `institutional_flow.py` | +10 in `test_institutional_flow_trade_eligibility.py` (new) | OK |
| **M4** — `pax.entry_trigger_age_ms` audit field | `dashboard.py` | +7 in `test_pax_entry_trigger_age.py` (new) | OK |

**Suite:** 999 -> 1019 passing (+20). `compileall` clean.

---

## M2 — the real fix was freshness scaling, not the window number

`_extract_micro_events_signal` normalizes `avg = signed_total / weight_total`.
A lone stale +event therefore normalized back to `+1` and **pinned the signal
until the hard window cutoff** — the live failure where micro stayed +0.85 for
~3 min after the last fingerprint while trend had already flipped down. Just
shrinking the window would have left a cliff (full strength, then 0).

Fix (`institutional_flow.py`):
- `_MICRO_EVENT_WINDOW_SEC` 180 -> **60** (hard cutoff).
- new `_MICRO_EVENT_HALFLIFE_SEC = 30.0`; recency is now `0.5 ** (age/half_life)`.
- magnitude is **freshness-scaled**: `avg * max_recency` (freshest contributing
  event's recency). A stale lone fingerprint now fades exponentially toward 0
  instead of pinning.

Side effect (intended): the `_TEXTBOOK_MICRO_THRESHOLD = 0.7` force-regime path
no longer trips on stale micro, which is exactly the fire-at-exhaustion pattern
M3/Stage-A is meant to attack. Fresh strong fingerprints (1-2s old) still read
~0.95 so the textbook-override tests are unchanged.

## T4 — deterministic stand-aside gate

`_compute_trade_eligibility(lt_quality, chop, lockout_active)` — pure function
of already-computed signals (no LLM, no new inputs), mirroring lt_signal_quality:

- **NO_TRADE**: DEAD tape, OR whipsaw lockout active, OR (chop window AND
  LIKELY_SPOOFED LT). Today's 12:00-14:10 "bullshit conditions" land here.
- **DIAL_IN_ONLY**: LIKELY_SPOOFED LT alone, OR a chop window alone.
- **LIVE_OK**: otherwise. Most-restrictive class wins.

Exposed as `snap.institutional_flow.trade_eligibility` + `..._reason`.

## M4 — entry lateness instrumentation

`pax.entry_trigger_age_ms` = how long the actionable ENTER decision has been
live (elapsed since it first became actionable). Large value at entry = late.
`None` when not actionable; resets when the decision label changes or lapses.
Implemented as a thin `pax_decision` wrapper (`_pax_decision_core` +
`_attach_entry_trigger_age`) so every existing gate/early-return is untouched.

---

## Not done (carried forward)

J3 (restingClusters), M1 (Stage A/B regime model), M3 (trend velocity),
T3 (flow.day_bias). M1 is the biggest structural win and needs its own window.

## Discipline

- Backup: `_phase_backups/pre_m2_t4_m4_20260528_150716/` (institutional_flow.py,
  dashboard.py) + manifest with PowerShell rollback.
- No git commits (operator commits explicitly).
- No Java touched; no jar rebuild needed for this trio.
