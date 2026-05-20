# bookmap-mcp (Python)

The MCP server half of the Bookmap MCP Bridge. See the top-level [README](../README.md) for the full picture.

## Run

```bash
python -m bookmap_mcp
```

Reads `~/.bookmap-mcp/bridge.properties` for the URL + token by default. Override with env vars:

- `BOOKMAP_BRIDGE_URL` — default `http://127.0.0.1:8765`
- `BOOKMAP_BRIDGE_TOKEN` — required if no properties file
- `BOOKMAP_MCP_CONFIG` — alternate path to `bridge.properties`

## Session conviction (v2 — anchored multi-source)

`compute_session_conviction()` in `bookmap_mcp/dashboard.py` runs a per-alias
session-anchored weighted conviction model — anchored at the operator's
OpenRange UI session (published via `or_session.effective_session_anchor()`,
fallback 08:30 CT only when no OR config has ever been written) — over 15
explicit sources rather than the legacy EMA-over-7-labels accumulator. Each source
returns `{score, reliability, raw, reason}`; the engine maintains a per-source
ring of `(ts_ms, value)` bounded by the medium window and aggregates a score
from the short SMA (30 s), the medium SMA (120 s), and the session SMA. Each
source declares an availability/sample-confidence/freshness-derived reliability
in `[0, 1]`. Effective weight = base × reliability, then scaled down inside
correlation clusters whose summed absolute weight exceeds the cap. The
composite is `Σ(eff_w · src) / Σ|eff_w|`, clipped to `[-1, +1]`. Trajectory is
the SMA slope of the composite over the last 30 s vs 30–90 s.

Sources by cluster (see `bookmap_mcp/pax_weights.json` for live weights/caps):

| Cluster | Cap | Members |
|---|---|---|
| flow | 0.35 | `flow_ofi`, `flow_cvd`, `bias_score`, `regime` |
| vwap | 0.25 | `vwap_dislocation`, `vwap_slope`, `anchored_vwap_opening_drive` |
| structure | 0.20 | `volume_profile`, `ib_context` |
| microstructure | 0.30 | `pull_stack`, `tape_large_lot`, `lt_liquidity`, `micro_events` |
| unclustered | — | `flow_vpt_absorption`, `level_reaction` |

`anchored_vwap_opening_drive` and `ib_context` keep reliability=0 until the
bridge ships `flow.avwap` and an IB break-direction field; they are wired so
the engine activates them automatically when the data arrives.

Output keeps the v1 keys consumed by `dashboard.js` (`score`, `trajectory`,
`trend`, `durationSec`, `anchorMs`, `anchorIso`, `components`, `instantaneous`,
`weights`) and adds the v2 detail (`sourceScores`, `sourceReliability`,
`effectiveWeights`, `rawSources`, `reasons`, `method`).

## Tests

```bash
pip install pytest
pytest --basetemp=/tmp/pytest-bookmap   # any path off the project tree avoids Windows-mount cleanup quirks
```
