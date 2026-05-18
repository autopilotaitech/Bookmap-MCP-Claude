---
name: session-clock
description: Reports the current trading-session state for index futures (ET-based). Returns a code and human label — PRE_MARKET, OR_FORMING, ACTIVE, LATE_MORNING, CHOP, AFTERNOON, CLOSE_RISK, POST_MARKET. Use to block entries during known low-edge windows (12:00–13:30 chop zone, 15:30–16:00 close risk, post-market). Pure deterministic; calls no LLM math.
---

# Session Clock

Deterministic time-of-day gate. Index futures (especially NQ) have well-documented low-edge windows where entries should be paused. This skill reports which window you're in.

## Windows (America/New_York)

| Range | Code | Behavior |
|---|---|---|
| < 09:30 | `PRE_MARKET` | No RTH liquidity yet — wait |
| 09:30–09:45 | `OR_FORMING` | 15-min Opening Range building — observe, do NOT enter |
| 09:45–11:30 | `ACTIVE` | Highest-edge entry window |
| 11:30–12:00 | `LATE_MORNING` | Decay — only HIGH-confidence signals |
| 12:00–13:30 | `CHOP` | NO NEW ENTRIES (documented dead zone) |
| 13:30–15:30 | `AFTERNOON` | Second-best entry window |
| 15:30–16:00 | `CLOSE_RISK` | NO NEW ENTRIES — pin / squaring risk |
| ≥ 16:00 | `POST_MARKET` | Wait |

## What to do

1. Get current ET time. Compute the H:MM minute-of-day.
2. Return:
   ```
   { "code": "<CODE>", "label": "<human label>", "now_et": "HH:MM ET" }
   ```
3. If asked "can I take a new entry?", combine with the rule:
   - `ACTIVE` or `AFTERNOON` → YES (subject to other gates)
   - `LATE_MORNING` → YES only if confidence is HIGH and you're already trending
   - everything else → NO with reason

## Pairing

Always combine with `news-blackout` (macro-event window override) and `risk-check` (position / PnL / size caps) before any order. If session-clock says NO, the others don't even need to run.
