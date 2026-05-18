# Bookmap MCP — Claude instructions

This file is loaded automatically by Claude Code at session start. Conventions
below override the default system prompt where they conflict.

## What this repo is

- Java Bookmap addon (under `addons/`) plus a Python MCP server
  (`mcp-server/bookmap_mcp/`) that exposes live order flow to Claude via skills
  like `momentum-scan`, `or-bias`, `risk-check`, `trade-decision`,
  `absorption-watch`, `vwap-or-gate`, `session-clock`, `news-blackout`.
- Production-grade trading systems work. Treat specs like an audit-friendly
  engineering doc: explicit definitions, anchored statistics, deterministic
  decision trees, pinned sign conventions in tests. No LLM math inside trading
  skills.
- Live-money runtime; bias toward small, auditable functions and one
  reversible commit per change.

## Workflow rules

### Backup before substantial refactors

Before any change that replaces an existing function, model, or config:

1. Create `_phase_backups/pre_<phase_name>_<YYYYMMDD_HHMMSS>/` at repo root.
2. Copy every file you will touch into the backup, preserving the relative
   path under `mcp-server/...`.
3. Write `BACKUP_MANIFEST.md` listing files and a PowerShell rollback snippet.

`_phase_backups/` is already in `.gitignore`. Don't try to commit it.

### ASCII unless the file already uses Unicode

Some files use Unicode (`dashboard.py` has sigma, arrows, box-drawing) — keep
their style. Files that are strictly ASCII (`pax_weights.json`, `README.md`,
JSON configs) must stay ASCII. Don't add em-dashes, smart quotes, sigma, or
arrows to an ASCII-only file.

### Single-author for tightly coupled work

When implementation, config, and tests must match each other exactly, do the
work yourself sequentially. Dispatch parallel agents only for genuinely
independent domains (different test files for unrelated subsystems, parallel
research across separate parts of the codebase). The
`dispatching-parallel-agents` skill describes the condition.

### Output style

- Be terse. Don't narrate your internal deliberation.
- End-of-turn summary: one or two sentences. What changed, what's next.
- Don't write multi-paragraph docstrings or comment blocks. Prefer named
  identifiers over comments. Only comment WHY when it's non-obvious.
- Never reference "the current task" or "this fix" in code comments.

## Running things

### Python tests

```bash
cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest
```

Pyproject sets `testpaths: tests`. If `pytest` is missing:
`python -m pip install pytest --quiet`.

### Byte-compile check (fast syntax sanity)

```bash
cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m compileall -q bookmap_mcp
```

### Hot-loaded config

`mcp-server/bookmap_mcp/pax_weights.json` reloads automatically on mtime
change. `_*`-prefixed keys are metadata comments stripped by
`_load_pax_weights()`. Restart the dashboard process to flush cached state.

## Code layout pointers

- `mcp-server/bookmap_mcp/dashboard.py` — snapshot composer (`fetch_snapshot`),
  `compute_or_levels`, `compute_vwap_bias`, `compute_vp_bias`, `trade_decision`,
  `compute_session_conviction`. ~3000 lines, audited heavily.
- Session conviction is the v2 anchored multi-source engine. State per alias
  in `_CONVICTION_STATE`. Source helpers under the `_source_*` prefix return
  `{score, reliability, raw, reason}`. See `mcp-server/README.md` for the
  cluster table.
- Legacy `_regime_to_signal`, `_slope_to_signal`, `_level_to_signal` are
  preserved and reused by v2 sources — don't refactor them away without
  updating the pinned helper-signal tests.

## Git etiquette

- Single-line commit subject in conventional style (e.g. `conviction: rebuild
  as anchored multi-source engine`). Body for context.
- Never commit `bridge.properties` (the bridge auth token) or files under
  `_phase_backups/`, `__pycache__/`, `.venv/`, `*.pyc`.
- Don't skip pre-commit hooks. If a hook fails, fix the underlying issue.

## What NOT to do

- Don't introduce live order-placement code paths. The Pax agent is
  CSV-logged and read-only by design.
- Don't add LLM-driven math to decision skills. Skills are deterministic
  decision trees over the snapshot.
- Don't refactor `dashboard.py` without backing up. It's the production hub.
- Don't reformat or "tidy" Unicode in files that already use it; the user
  diffs these manually.
