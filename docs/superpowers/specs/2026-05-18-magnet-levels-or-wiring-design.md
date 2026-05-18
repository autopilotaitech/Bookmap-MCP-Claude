# Magnet-Levels OR-Strategy Wiring — Design

**Date:** 2026-05-18
**Owner:** will@autopilotaitech.com
**Scope:** wire the bridge's `/magnet_levels` endpoint into the existing OR-Strategy CSV ingestion path so STOP_SWEEP detection actually fires in production.

---

## Goal

After this change, every dashboard poll that produces a valid OR level grid also publishes those prices to the Bookmap bridge as magnet levels, so `InstrumentState.detectStopSweep` has reference levels to fire against. The change is idempotent (no level churn → no POSTs) and silent on failure (broken bridge → snapshot still composes).

## Non-goals

- **No new CSV format work.** The OR-Strategy CSV schema is unchanged. We consume `orHigh`/`orLow` exactly as `compute_or_levels` does today.
- **No new transport.** We reuse the existing `BridgeClient.post_json` against `/magnet_levels` as defined in the prior fix.
- **No expansion to non-OR levels.** Prior-day H/L, IB H/L, VWAP bands, POC — out of scope. The design is shaped so adding them later is a one-line append to the level-collector function.
- **No retry/backoff.** The bridge is on localhost; failures are surfaced via stderr and the next snapshot retries naturally.

## Architecture

One new helper inside `dashboard.py`:

```text
_sync_magnet_levels(cfg: BridgeConfig, alias: str, or_levels: Optional[Dict]) -> None
```

Plus one module-level cache and a refresh interval:

```text
_LAST_MAGNETS: Dict[str, Tuple[Tuple[float, ...], float]] = {}
    # alias → (sorted_price_tuple, last_successful_post_monotonic_seconds)
_LAST_MAGNETS_LOCK = threading.Lock()
_MAGNET_REFRESH_SECS = 60.0
    # If the cached level set is identical but the last successful post is older than this,
    # re-post anyway. Bounds the worst-case "bridge restart wiped magnets, dashboard
    # thinks they're still set" failure mode to one minute.
```

Hook point: `fetch_snapshot` calls `_sync_magnet_levels(cfg, alias, snap["or_levels"])` once, immediately after `snap["or_levels"]` is populated by `_safe_call(compute_or_levels, ...)` at line ~2068.

The helper:

1. Returns immediately if `alias` is missing/empty, `or_levels` is `None`, an `_error` shape, or has no usable `levels` list.
2. Extracts every numeric `price` from `or_levels["levels"]`. Non-numeric entries are skipped silently.
3. Rounds each to 2 decimals (mirrors `compute_or_levels`), sorts ascending, keeps the full 8 prices, and builds a sorted tuple.
4. Looks up `_LAST_MAGNETS[alias]` under the lock. If `cached_tuple == new_tuple` **and** `time.monotonic() - cached_ts < _MAGNET_REFRESH_SECS`, returns silently.
5. Otherwise opens a short-lived `BridgeClient(cfg, timeout_s=2.0)`, calls `post_json("/magnet_levels", {"alias": alias, "levels": ",".join(f"{p:g}" for p in prices)})`. On a successful 2xx/JSON response, updates `_LAST_MAGNETS[alias] = (new_tuple, time.monotonic())` under the lock.
6. Any exception (`BridgeError`, network, anything) is caught, a concise one-line message is written to stderr, and the cache is **not** updated. The next snapshot retries naturally; the TTL is independent of failure handling because failures don't refresh the timestamp.

The bridge client is opened separately from the main `with BridgeClient(...) as c:` block in `fetch_snapshot` because:
- That block has already closed by the time `compute_or_levels` runs (it ends at line 2021; OR levels are computed at line 2068).
- A second client costs one localhost TCP connect — sub-millisecond overhead.
- Keeping it scoped to the magnet sync means a magnet-related failure can't poison the main snapshot read loop.

## Data flow

```text
OR-Strategy CSV
  │ (or_latest_row reads newest row)
  ▼
or_row dict in snap
  │ (compute_or_levels: 8-level grid)
  ▼
snap["or_levels"]["levels"] = [{label,price,...}, … × 8]
  │ (NEW: _sync_magnet_levels)
  ▼
sorted tuple of 8 prices  ──── compare ──→  _LAST_MAGNETS[alias] = (tuple, ts)
                                    │
                ┌──────────────────┴───────────────┐
                ▼                                  ▼
       tuple differs                     tuple identical
                │                                  │
                │                                  ▼
                │                        ts younger than 60s?
                │                       ┌────────┴────────┐
                │                       ▼                 ▼
                │                      yes               no
                │                       │                 │
                ▼                       ▼                 ▼
       POST /magnet_levels           (no-op)     POST /magnet_levels (TTL refresh)
                │                                         │
                └──────── on 2xx: _LAST_MAGNETS[alias] = (new_tuple, monotonic()) ────┘
```

Cache lifetime: process-local, in-memory. Lost on dashboard restart. Acceptable because:
- The bridge holds the levels in `InstrumentState.magnetLevels` but loses them on Bookmap restart.
- After a dashboard restart the cache is empty, so the next snapshot reposts.
- After a **Bookmap restart**, the bridge loses the levels but the dashboard cache still thinks they're posted. The 60-second TTL bounds this failure mode: even if no price moves, the next post happens within a minute.

## Error handling

| Failure | Behavior |
| --- | --- |
| `cfg` load fails | `fetch_snapshot` already errors earlier; helper is never reached. |
| `or_levels` is None / `_error` / no `levels` key | Helper returns silently. No POST, no cache update. |
| `or_levels["levels"]` contains a non-numeric `price` | Helper logs once to stderr, returns. (Defensive — shouldn't happen given `compute_or_levels` always rounds.) |
| `BridgeClient` constructor raises | Caught, logged to stderr, cache unchanged. |
| `post_json` raises `BridgeError` (incl. 4xx/5xx) | Caught, logged with status/message, cache unchanged. Next snapshot retries. |
| `post_json` raises any other exception | Same as above. |

## Testing

New test file: `mcp-server/tests/test_magnet_sync.py`.

Tests stub `BridgeClient` so they hit no real socket. Required cases:

1. `test_first_call_posts_full_grid` — given a valid 8-level `or_levels`, `_sync_magnet_levels` posts once with all 8 prices.
2. `test_repeat_with_identical_levels_inside_ttl_does_not_repost` — second call with same `or_levels` inside the TTL → zero additional POSTs, cache tuple unchanged.
3. `test_identical_levels_after_ttl_reposts` — monkey-patch `time.monotonic` (or `_MAGNET_REFRESH_SECS`) so the cached timestamp is older than the TTL; same `or_levels` → one new POST and the timestamp is refreshed.
4. `test_changed_levels_repost` — call with one price changed → one new POST.
5. `test_none_or_levels_is_noop` — `or_levels=None` → no POST, no cache mutation, no exception.
6. `test_error_shape_or_levels_is_noop` — `or_levels={"_error": "..."}` → no POST.
7. `test_bridge_error_is_swallowed` — stub `post_json` to raise `BridgeError`. Helper must return normally and must NOT update `_LAST_MAGNETS`.
8. `test_cache_isolates_by_alias` — two different aliases get independent cache slots.
9. `test_payload_format_is_comma_separated` — assert the `levels` param sent to the bridge is a string like `"20100.25,20123.5,..."` (commas, no spaces, sorted ascending).

Existing tests must continue passing:

- All 70 currently-green Python tests.
- `fetch_snapshot` is not directly unit-tested today, but `test_session_conviction.py` exercises downstream consumers — they read `snap["or_levels"]`, not the side effect, so they're unaffected.

## File-level impact

| File | Change |
| --- | --- |
| `mcp-server/bookmap_mcp/dashboard.py` | Add `_LAST_MAGNETS`, `_LAST_MAGNETS_LOCK`, `_sync_magnet_levels`. Add one line in `fetch_snapshot` after `compute_or_levels`. Add `import threading` if not already imported. |
| `mcp-server/tests/test_magnet_sync.py` | New file, 8 tests. |
| Java code | No change. |
| `mcp-server/bookmap_mcp/server.py` | No change — `bookmap_set_magnet_levels` MCP tool still exists for ad-hoc manual posting. |

## Open questions / risks

- **Concurrency:** `fetch_snapshot` is called from the dashboard HTTP handler. If two HTTP requests hit simultaneously, both might race to `_LAST_MAGNETS` and to the bridge POST. The lock guards cache reads/writes; the worst case is one duplicate POST per race, which is harmless (the Java handler is idempotent).
- **Multi-alias:** the current `fetch_snapshot` only looks at `insts[0]["alias"]` (line 2001). Magnet levels are scoped per alias. If the user later attaches multiple instruments, only the first gets magnets. Acceptable — the existing snapshot path has the same single-alias scope.
- **CSV staleness:** if `or_latest_row()` returns an old CSV row (e.g., yesterday's), magnet levels will reflect yesterday's OR. The existing `compute_or_levels` does not date-validate, so this design inherits that behavior. Out of scope to fix here; flag for the OR-Strategy ingestion overhaul.
- **Bridge restart in mid-session:** if Bookmap restarts, the bridge loses `magnetLevels` but the dashboard cache still holds the last-posted tuple. The 60-second TTL bounds the failure window: even with zero level changes, the dashboard re-publishes within `_MAGNET_REFRESH_SECS`. The cost is one extra harmless POST per minute per alias once OR is finalized — well under the bridge's noise floor.
