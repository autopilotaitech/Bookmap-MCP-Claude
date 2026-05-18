---
name: news-blackout
description: Reads C:\Bookmap\addons\MCP\Bookmap\news-calendar.json and reports whether the current ET time falls inside a configured macro-event or mega-cap-earnings blackout window. Returns BLOCKED + reason or CLEAR. Use as a hard gate before any new entry. Documented to remove the single largest source of OR-strategy blow-ups on NQ.
---

# News Blackout

Hard deterministic gate that blocks new entries during known volatility-spike events.

## Calendar file

Path: `C:\Bookmap\addons\MCP\Bookmap\news-calendar.json`

Format:
```json
{
  "blackouts": [
    { "date": "2026-05-21", "type": "CPI",  "blackout_start": "08:00", "blackout_end": "10:00" },
    { "date": "2026-05-21", "type": "FOMC", "blackout_start": "13:30", "blackout_end": "15:30" },
    { "date": "2026-05-22", "type": "NVDA_EARN", "blackout_start": "09:30", "blackout_end": "11:00", "notes": "post-earnings vol" }
  ]
}
```

All times in ET. `type` and `notes` are free-text — used for human display only.

## What to do

1. Read the JSON. If file missing → return `CLEAR` with note "no calendar configured".
2. Get current ET date + time.
3. Find blackout entries matching today's date.
4. If current HH:MM falls within any blackout window → return `BLOCKED` with the event type and window.
5. Otherwise → return `CLEAR`.

## Output

```
{ "blocked": true|false, "label": "<event type> blackout HH:MM-HH:MM ET" or "clear" }
```

## Maintenance

The user maintains this file. A weekly skill could be added that fetches an econ calendar API and updates the JSON, but for now it's manual. Standard events to block on the day-of:

- **FOMC days** — 13:30–15:30 ET (statement + presser)
- **CPI / PPI / PCE / Core PCE / Retail Sales** — 08:30 release, block 08:30–10:00
- **NFP / Unemployment / JOLTS** — 08:30 release, block 08:30–10:00
- **ISM MFG / ISM SVC** — 10:00 release, block 09:55–10:30
- **GDP advance** — 08:30 release, block 08:30–10:00
- **FOMC minutes** — 14:00 release, block 13:55–14:30
- **Mega-cap earnings overnight** (NVDA, AAPL, MSFT, GOOGL, META, AMZN) — block the entire next session for that name's instrument, plus first 90 min for index futures

If `risk-check` is also being run, news-blackout takes precedence — even GO from risk-check is invalid if news-blackout returns BLOCKED.
