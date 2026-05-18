# Trading skills for Claude Code

Skills that ride on top of the Bookmap MCP bridge to give Claude a playbook for analysis and pre-trade gating.

**They do not touch OR-Strategy code.** `or-bias` reads OR-Strategy's CSV output (the file `PaxOpeningRangeSignalCsvLogger` writes) — pure read.

## Skills in this folder

- **risk-check** — pre-trade gate. Reads position, working orders, balance; returns GO/NO-GO with explicit reasons. Always run before any order-placement tool.
- **momentum-scan** — aggressor imbalance over rolling windows of recent trades; flags inflections.
- **or-bias** — reads the latest row of OR-Strategy's signal CSV + cross-checks the live orderbook to translate the strategy's output into a current-moment opinion.
- **trade-journal** — append/summarize daily fills with the OR-Strategy bias at fill time.

## Two ways to install

### Project scope (already done)
Files live under `C:\Bookmap\addons\MCP\Bookmap\.claude\skills\`. To use them, just `cd` to the project before launching claude:

```powershell
cd C:\Bookmap\addons\MCP\Bookmap
claude
```

### User scope (available from anywhere)
Copy the folder once to your user-level Claude directory:

```powershell
$dst = "$env:USERPROFILE\.claude\skills"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
Copy-Item "C:\Bookmap\addons\MCP\Bookmap\.claude\skills\*" $dst -Recurse -Force
```

After that, any `claude` session anywhere on your machine sees the skills.

## How skills are invoked

Inside `claude`, either:

- Type `/risk-check NQM6.CME@RITHMIC buy 1 29746` — explicit invocation.
- Just describe what you want naturally — *"check risk before I buy 1 NQM6 at 29746"* — and Claude will pick the matching skill from its description.
