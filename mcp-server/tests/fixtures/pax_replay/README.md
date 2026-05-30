# PAX replay regression fixtures

Curated JSONL sessions that pin the **behavior** of the deterministic decision
path (`pax_loop.decide`, replayed by `pax_agent_replay`). They are test
fixtures, not production logs and **not market-edge proof** -- they prove the
replay/gate plumbing stays stable, nothing about profitability.

Each record embeds a `snapshot` so the decision can be re-derived offline (the
live `agent-loop.jsonl` does not embed snapshots; see the replay module's
limitation note). The fixtures use a fixed `ts_ms` so summaries are byte-stable.

| fixture | what it pins |
|---------|--------------|
| `clean_eligible_entry.jsonl` | in-proximity OR-H follow -> replay decides `PLACE_LONG` (`OR_BREAK_ACCEPT`), matching the recorded action (no divergence). |
| `stale_market_blocked.jsonl` | recorded enforced `stale_market_data` halt is counted in `risk_halt_counts`. |
| `stale_heartbeat_blocked.jsonl` | recorded enforced `stale_heartbeat` halt is counted. |
| `kill_switch_blocked.jsonl` | recorded enforced `kill_switch_active` halt is counted. |
| `malformed_missing.jsonl` | broken JSON, a snapshot-less heartbeat, a null snapshot, a non-dict line -> counted as malformed/non-usable, never crashes. |
| `divergence.jsonl` | recorded `PLACE_LONG` but an out-of-proximity snapshot -> replay decides `NONE` -> exactly one divergence. |

### Weekend/offline replay-clock fixtures

Pin that replaying a **weekday** tape uses the recorded weekday time, never the
(possibly weekend) wall clock. All four carry a Wednesday timestamp
(2026-05-27 13:00 CT). Verdicts come from `pax_agent_replay.clock_report`.

| fixture | what it pins |
|---------|--------------|
| `replay_clock_weekday_market.jsonl` | real `ts_ms` + fresh `marketDataAsOfMs` -> clock-report `pass`; first replay time is Wednesday. |
| `replay_clock_weekend_wallclock_guard.jsonl` | clock derived ONLY from `replay_input.now_ms` (Wed) + embedded fresh `market_age_sec` -> `pass`; proves no wall-clock leak. |
| `replay_clock_missing_market_ts.jsonl` | real `ts_ms` clock but NO market-freshness timestamp -> `warn` (gate not replayable, not faked). |
| `replay_clock_composed_only.jsonl` | only `composedAtMs` present (not a clock source) -> no real replay clock -> `fail`. |

Replay never places orders, never calls an LLM, and never touches live Bookmap.
