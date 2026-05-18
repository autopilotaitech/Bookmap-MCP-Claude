# Bookmap MCP Micro-Events Stack Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` (inline) to walk this plan task-by-task. **Do NOT dispatch parallel subagents** — every task here mutates `InstrumentState.java` or tests that read from it, so the work is tightly coupled (CLAUDE.md "single-author for tightly coupled work"). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Realign every consumer of Bookmap `TradeInfo.isBidAggressor` with the actual API contract (`true` = bid was the aggressor = buy aggressor / lifted offer), wire the stop-sweep magnet-level mechanism end-to-end, fix MBO replace price-move semantics, and reconcile the one site in `dashboard.py` where the STOP_SWEEP sign drifted away from spec.

**Architecture:** The repository sits on a contract that was inverted in the Java layer. The Python consumers and the embedded browser JS already encode the spec correctly. We do **not** flip every site mechanically; we flip only the sites that consume `bidAggressor` directly. Sites that negate the variable (`!bidAggressor`) become correct automatically once the upstream interpretation is fixed. Tests are added to pin direction so the inversion cannot regress.

**Tech Stack:** Java 17, Bookmap API 7.4.0.21 (pinned in `build.gradle`), JUnit Jupiter 5.10.2 for Java tests, Python 3 + pytest for the MCP server tests. Only Bookmap 7.4 symbols may be used in Java (`com.bookmap.api:api-core:7.4.0.21`, `api-simplified:7.4.0.21`).

---

## Findings summary (what's actually broken, with line numbers)

The audit's six findings, ground-truthed against the source:

### 1. Trade-aggressor inversion — Java side ONLY

Bookmap 7.x: `TradeInfo.isBidAggressor == true` means **the bid was the aggressor**, i.e. **buy aggressor lifted the offer**. The codebase has interpreted it as the opposite ("sell aggressor / hit bid") at every site that uses `bidAggressor` directly, but the sites that use `!bidAggressor` are accidentally correct under the actual Bookmap semantics and need **no change**.

| File:line                                  | What it does                                                                | Correct behavior under spec                              | Action          |
| ------------------------------------------ | --------------------------------------------------------------------------- | -------------------------------------------------------- | --------------- |
| `state/TradeRecord.java:8`                 | Doc comment: "true if the aggressor hit the bid (sell aggressor)"           | "true if the bid was the aggressor (buy / lifted offer)" | **Update doc**  |
| `state/InstrumentState.java:222-224`       | CVD doc comment is OK ("buy minus sell") but local interpretation is wrong  | Comment stays                                            | No change       |
| `state/InstrumentState.java:497`           | `mboDeltas.addLast(new MboDelta(nowMs, !bidAggressor, ...))` (isBid of hit) | `!bidAggressor` is correct under spec                    | **No change**   |
| `state/InstrumentState.java:507-509`       | `if (bidAggressor) cvdValue -= size; else cvdValue += size;`                | Sign is inverted under spec                              | **Flip arith**  |
| `state/InstrumentState.java:512-514`       | `flowRegime.onTrade(price, size, !bidAggressor, nowMs);` (3rd arg=buyAggr)  | Should pass `bidAggressor` directly                      | **Drop the `!`** |
| `state/InstrumentState.java:531`           | `recordIcebergFill(..., !bidAggressor);` (`restingIsBid`)                   | `!bidAggressor` is correct under spec                    | **No change**   |
| `state/InstrumentState.java:537`           | `detectStopSweep(price, size, bidAggressor, nowMs);` — passes through       | `bidAggressor` arg is just opaque pass-through           | **No change**   |
| `state/InstrumentState.java:733`           | STOP_SWEEP isBid emitted as `!bidAggressor`                                 | `!bidAggressor` is correct under spec                    | **No change**   |
| `state/InstrumentState.java:889, 893`      | Tape buckets: `if (bidAggressor) b30s[base+1] else b30s[base]`              | `[base+0]=buy, [base+1]=sell` — flip indices             | **Swap indices** |
| `state/InstrumentState.java:1420-1421`     | Momentum: `if (bidAggressor) sell += size; else buy += size;`               | Should be `buy +=` when bidAggressor                     | **Flip branches** |
| `state/InstrumentState.java:1535`          | VolBucket: `if (bidAggressor) entry[1] += size; else entry[0] += size;`     | `entry[0]=buy, entry[1]=sell` — flip indices             | **Swap indices** |
| `handlers/RecentTradesHandler.java:51`     | `prop("side", t.bidAggressor() ? "sell" : "buy")`                           | `bidAggressor ? "buy" : "sell"`                          | **Flip ternary** |

### 2. Stop-sweep sign — one Python site is inverted from the spec

Spec & every other consumer: `STOP_SWEEP isBid=true` → bids swept → **bearish**.

- `mcp-server/bookmap_mcp/dashboard.py:254-256` `_micro_at_level` STOP_SWEEP: `-0.5` for bid, `+0.5` for ask — **correct**
- `mcp-server/bookmap_mcp/dashboard.py:1255-1257` `_source_micro_events` STOP_SWEEP: `+0.5` for bid, `-0.5` for ask — **inverted, must flip**
- `mcp-server/bookmap_mcp/dashboard.py:3417-3420` embedded browser JS — **correct**
- `mcp-server/tests/test_micro_bias.py` — pins **correct** spec
- `mcp-server/tests/test_session_conviction.py:364-370` — pins the inverted `_source_micro_events` behavior, **must flip**
- `mcp-server/tests/test_session_conviction.py:99 & :135` — bull/bear fixtures use the inverted STOP_SWEEP side; **swap the STOP_SWEEP isBid** in both fixtures so the bull-snapshot still reads bullish after the fix
- Java emission at `state/InstrumentState.java:733` is correct under spec; no Java change

### 3. Magnet levels unwired

`InstrumentState.detectStopSweep` early-returns when `magnetLevels.length == 0`, and **nothing in the repo calls `setMagnetLevels`** (verified via `Grep`). The bridge needs an HTTP endpoint to accept levels.

Existing handler style: query-string-args, alias required, JSON response, GET for read endpoints / POST for state-changing endpoints. Magnet levels mutate server state → **POST**, following the `TradingHandler` convention but without the `BOOKMAP_ALLOW_TRADING` gate (these are read-only safety markers, not orders).

### 4. MBO replace loses price-move semantics

`state/InstrumentState.java:554-576` emits a single `REPLACE` delta at the new tick with `sizeDelta = newSize - oldSize`, then credits pull/stack at the new tick only. When the order actually moved price, the OLD tick's stack never gets a pull and the NEW tick's stack accounting is wrong (sizeDelta of a moved order has no meaningful sign relative to the new level).

### 5. Tests

Java tests live in `src/test/java/com/bookmapmcp/...`. Build wires JUnit 5.10.2 (build.gradle:21). Existing `InstrumentStateTest.java` is the right home for new aggressor / sweep / replace tests. Add a new `handlers/MagnetLevelsHandlerTest.java` only if the handler has unit-testable pure logic; otherwise rely on the existing pattern of testing `InstrumentState.setMagnetLevels` + `detectStopSweep` interaction.

Python tests live in `mcp-server/tests/`. Pyproject `testpaths` is `tests`. Run via `python -m pytest` from `mcp-server/`.

### 6. Bookmap 7.4 compatibility

`build.gradle` pins `com.bookmap.api:api-core:7.4.0.21` and `api-simplified:7.4.0.21` for both compile and test classpaths. No newer symbols may be introduced. All edits in this plan touch only `TradeInfo`, `Api`, `SimpleOrderSendParameters` already in use elsewhere — no new Bookmap API surface.

---

## File structure (what each task touches)

| Task | Files                                                                                           |
| ---- | ----------------------------------------------------------------------------------------------- |
| 0    | `_phase_backups/pre_micro_events_fix_<TIMESTAMP>/...`, `BACKUP_MANIFEST.md`                     |
| 1    | `src/main/java/com/bookmapmcp/state/InstrumentState.java` (lines 222, 497*, 507-514, 889, 893, 1420-1421, 1535), `state/TradeRecord.java`, `handlers/RecentTradesHandler.java` |
| 2    | `src/test/java/com/bookmapmcp/state/InstrumentStateTest.java` (new tests appended)              |
| 3    | `src/main/java/com/bookmapmcp/handlers/MagnetLevelsHandler.java` (new), `BridgeServer.java`, `mcp-server/bookmap_mcp/server.py` (new MCP tool), `mcp-server/bookmap_mcp/bridge_client.py` (no change — handlers route through `_call`) |
| 4    | `src/main/java/com/bookmapmcp/state/InstrumentState.java:554-576`, `src/test/java/com/bookmapmcp/state/InstrumentStateTest.java` (one new test) |
| 5    | `mcp-server/bookmap_mcp/dashboard.py:1255-1257`, `mcp-server/tests/test_session_conviction.py:99, 135, 364-370`, `mcp-server/tests/test_micro_bias.py` (no change — already correct) |
| 6    | Final verification: pytest + gradle build                                                       |

`*` = comment-only / no semantic change to that line. See per-task instructions.

---

## Task 0: Phase backup before any edits

**Files:**
- Create: `_phase_backups/pre_micro_events_fix_<YYYYMMDD_HHMMSS>/src/...`, `_phase_backups/pre_micro_events_fix_<YYYYMMDD_HHMMSS>/mcp-server/...`
- Create: `_phase_backups/pre_micro_events_fix_<YYYYMMDD_HHMMSS>/BACKUP_MANIFEST.md`

- [ ] **Step 0.1: Create timestamped backup directory**

In PowerShell:
```powershell
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$root = "C:\Bookmap\addons\MCP\Bookmap\_phase_backups\pre_micro_events_fix_$ts"
New-Item -ItemType Directory -Force $root | Out-Null
# Mirror the relative paths under the backup root
$files = @(
  "src\main\java\com\bookmapmcp\state\InstrumentState.java",
  "src\main\java\com\bookmapmcp\state\TradeRecord.java",
  "src\main\java\com\bookmapmcp\state\MicrostructureEvent.java",
  "src\main\java\com\bookmapmcp\handlers\MicrostructureEventsHandler.java",
  "src\main\java\com\bookmapmcp\handlers\RecentTradesHandler.java",
  "src\main\java\com\bookmapmcp\BridgeServer.java",
  "src\test\java\com\bookmapmcp\state\InstrumentStateTest.java",
  "mcp-server\bookmap_mcp\dashboard.py",
  "mcp-server\bookmap_mcp\server.py",
  "mcp-server\tests\test_micro_bias.py",
  "mcp-server\tests\test_session_conviction.py"
)
foreach ($f in $files) {
  $src = Join-Path "C:\Bookmap\addons\MCP\Bookmap" $f
  $dst = Join-Path $root $f
  New-Item -ItemType Directory -Force (Split-Path $dst -Parent) | Out-Null
  Copy-Item -Path $src -Destination $dst
}
```

- [ ] **Step 0.2: Write BACKUP_MANIFEST.md**

In `_phase_backups/pre_micro_events_fix_<TS>/BACKUP_MANIFEST.md`:

```markdown
# Backup manifest — pre_micro_events_fix

Backed up before fixing:
1. Java trade-aggressor inversion (InstrumentState.java, TradeRecord.java, RecentTradesHandler.java)
2. Magnet levels wiring (BridgeServer.java + new MagnetLevelsHandler.java)
3. MBO replace price-move (InstrumentState.java)
4. Python _source_micro_events STOP_SWEEP sign (dashboard.py + test_session_conviction.py)

## Files

- src\main\java\com\bookmapmcp\state\InstrumentState.java
- src\main\java\com\bookmapmcp\state\TradeRecord.java
- src\main\java\com\bookmapmcp\state\MicrostructureEvent.java
- src\main\java\com\bookmapmcp\handlers\MicrostructureEventsHandler.java
- src\main\java\com\bookmapmcp\handlers\RecentTradesHandler.java
- src\main\java\com\bookmapmcp\BridgeServer.java
- src\test\java\com\bookmapmcp\state\InstrumentStateTest.java
- mcp-server\bookmap_mcp\dashboard.py
- mcp-server\bookmap_mcp\server.py
- mcp-server\tests\test_micro_bias.py
- mcp-server\tests\test_session_conviction.py

## Rollback

From the repo root:

```powershell
$bk = "C:\Bookmap\addons\MCP\Bookmap\_phase_backups\pre_micro_events_fix_<TS>"
Get-ChildItem -Recurse -File $bk |
  Where-Object { $_.Name -ne "BACKUP_MANIFEST.md" } |
  ForEach-Object {
    $rel = $_.FullName.Substring($bk.Length + 1)
    Copy-Item $_.FullName (Join-Path "C:\Bookmap\addons\MCP\Bookmap" $rel) -Force
  }
```
```

- [ ] **Step 0.3: Sanity-check the backup tree**

```powershell
Get-ChildItem -Recurse "C:\Bookmap\addons\MCP\Bookmap\_phase_backups\pre_micro_events_fix_$ts" |
  Measure-Object | Select-Object Count
```
Expected: 11 source files + 1 manifest = at least 12 items (more if directories count).

---

## Task 1: Realign Java trade-aggressor semantics

**Files:**
- Modify: `src/main/java/com/bookmapmcp/state/TradeRecord.java:8` (comment only)
- Modify: `src/main/java/com/bookmapmcp/state/InstrumentState.java:507-509, 511-514, 889, 893, 1420-1421, 1535`
- Modify: `src/main/java/com/bookmapmcp/handlers/RecentTradesHandler.java:51`

- [ ] **Step 1.1: Fix TradeRecord.java doc comment**

Edit `src/main/java/com/bookmapmcp/state/TradeRecord.java` line 8:

Old:
```java
 * @param bidAggressor true if the aggressor hit the bid (sell aggressor), false if lifted offer
```

New:
```java
 * @param bidAggressor true if the bid was the aggressor (buy aggressor, lifted offer);
 *                     false if the ask was the aggressor (sell aggressor, hit bid).
 *                     Matches Bookmap's TradeInfo.isBidAggressor semantics.
```

- [ ] **Step 1.2: Fix CVD arithmetic in onTrade**

Edit `src/main/java/com/bookmapmcp/state/InstrumentState.java` lines 506-510:

Old:
```java
            if (isInRthWindow(nowMsForSession())) {
                if (bidAggressor) cvdValue -= size;  // sell aggressor
                else              cvdValue += size;  // buy aggressor
            }
```

New:
```java
            if (isInRthWindow(nowMsForSession())) {
                if (bidAggressor) cvdValue += size;  // buy aggressor (bid was aggressor / lifted offer)
                else              cvdValue -= size;  // sell aggressor (ask was aggressor / hit bid)
            }
```

- [ ] **Step 1.3: Fix flowRegime aggressor pass and update comment**

Edit `src/main/java/com/bookmapmcp/state/InstrumentState.java` lines 511-514:

Old:
```java
        // OFI/CVD/VPT and roll the 30s window when ready. !bidAggressor = buy
        // (lift offer); bidAggressor = sell (hit bid).
        flowRegime.onTrade(price, size, !bidAggressor, nowMs);
```

New:
```java
        // FlowRegime.onTrade's third arg is `buyAggressor`. Bookmap's bidAggressor
        // == true means the bid was the aggressor (buy aggressor / lifted offer),
        // so pass it through directly.
        flowRegime.onTrade(price, size, bidAggressor, nowMs);
```

- [ ] **Step 1.4: Fix tape-bucket indices (30s + 5m branches)**

Edit `src/main/java/com/bookmapmcp/state/InstrumentState.java` line 889:

Old:
```java
                if (is30s) {
                    if (t.bidAggressor()) b30s[base+1] += t.size(); else b30s[base] += t.size();
                    b30s[base+2]++;
                }
```

New:
```java
                if (is30s) {
                    // base+0 = buyVol, base+1 = sellVol, base+2 = count (see TapeBucketsSnapshot.Bucket).
                    if (t.bidAggressor()) b30s[base] += t.size(); else b30s[base+1] += t.size();
                    b30s[base+2]++;
                }
```

Edit line 893 (same pattern):

Old:
```java
                if (is5m) {
                    if (t.bidAggressor()) b5m[base+1] += t.size(); else b5m[base] += t.size();
                    b5m[base+2]++;
                }
```

New:
```java
                if (is5m) {
                    if (t.bidAggressor()) b5m[base] += t.size(); else b5m[base+1] += t.size();
                    b5m[base+2]++;
                }
```

- [ ] **Step 1.5: Fix momentum buy/sell branches**

Edit `src/main/java/com/bookmapmcp/state/InstrumentState.java` lines 1420-1421:

Old:
```java
                if (t.bidAggressor()) sell += t.size();
                else                  buy  += t.size();
```

New:
```java
                if (t.bidAggressor()) buy  += t.size();   // buy aggressor (lifted offer)
                else                  sell += t.size();   // sell aggressor (hit bid)
```

- [ ] **Step 1.6: Fix VolBucket entry indices**

Edit `src/main/java/com/bookmapmcp/state/InstrumentState.java` line 1535:

Old:
```java
            if (bidAggressor) entry[1] += size; else entry[0] += size;
```

New:
```java
            // entry[0] = buyVolume, entry[1] = sellVolume (see VolumeProfileSnapshot.Level ctor below).
            if (bidAggressor) entry[0] += size; else entry[1] += size;
```

- [ ] **Step 1.7: Fix recent-trades JSON side mapping**

Edit `src/main/java/com/bookmapmcp/handlers/RecentTradesHandler.java` line 51:

Old:
```java
                    .prop("side", t.bidAggressor() ? "sell" : "buy")
```

New:
```java
                    .prop("side", t.bidAggressor() ? "buy" : "sell")
```

- [ ] **Step 1.8: Confirm the negated sites are LEFT AS-IS**

The following lines must NOT be changed — they negate `bidAggressor` and are already correct under spec:
- `InstrumentState.java:497` — `new MboDelta(nowMs, !bidAggressor, tick, (byte)3, size)` (isBid of the level that got hit)
- `InstrumentState.java:531` — `recordIcebergFill(tick, info.passiveOrderId, size, nowMs, !bidAggressor)` (`restingIsBid` is the passive side)
- `InstrumentState.java:733` — `emitMicroEvent(... STOP_SWEEP ... !bidAggressor, reason)` (isBid means "bids swept" → bearish)

Visually verify by re-reading those three lines.

---

## Task 2: Java regression tests for aggressor semantics

**Files:**
- Modify: `src/test/java/com/bookmapmcp/state/InstrumentStateTest.java` (append new tests, do NOT replace existing ones)

- [ ] **Step 2.1: Add CVD direction test**

Append to `InstrumentStateTest.java` (the existing `tradeSideIsBidAggressorMappedToSellAggressorLabel` only checks the boolean field passes through — the new tests check semantic direction):

```java
    @Test
    void cvdAccumulatesBuyAggressorAsPositive() {
        // Note: the existing onTrade helper accepts the raw boolean. The contract under test:
        // bidAggressor == true means the BID was the aggressor (buy aggressor / lifted offer).
        // CVD should add for buys and subtract for sells.
        InstrumentState s = newState();
        // Drive 5 buy aggressors of size 3 and 2 sell aggressors of size 4.
        for (int i = 0; i < 5; i++) s.onTrade(100, 3, true);
        for (int i = 0; i < 2; i++) s.onTrade(100, 4, false);
        // CVD lives behind FlowRegime's snapshot via the momentum payload, so we
        // verify via momentumSnapshot's per-window buy/sell counts instead — same
        // data source, simpler to assert.
        // (Direct CVD field is not exposed publicly; momentum cross-checks the sign.)
        // Set lastSeenNanos so the momentum window picks up the trades.
        s.onTimestamp(System.nanoTime());
        MomentumSnapshot mo = s.momentumSnapshot(new int[]{600});
        MomentumSnapshot.Window w = mo.windows().get(0);
        // 5 buys * 3 = 15 buy, 2 sells * 4 = 8 sell
        assertEquals(15L, w.buy(), "buy aggressor volume should accumulate as 'buy'");
        assertEquals(8L,  w.sell(),"sell aggressor volume should accumulate as 'sell'");
    }
```

Note: the existing `recentTradesAreNewestFirstAndBoundedByCapacity` test (line 57+) does NOT set `lastSeenNanos` and relies on raw nanos=0. The momentum test path requires `nanos > 0` to fall inside the window (see InstrumentState.java:1414), which is why we call `s.onTimestamp(...)` after the trades. The trades themselves all get `lastSeenNanos` snapshot at trade time, which is 0 because `onTimestamp` is called AFTER. Adjust: call `s.onTimestamp(System.nanoTime())` BEFORE the trades so each trade records a non-zero nanos:

Corrected order:
```java
    @Test
    void cvdAccumulatesBuyAggressorAsPositive() {
        InstrumentState s = newState();
        s.onTimestamp(System.nanoTime());
        for (int i = 0; i < 5; i++) s.onTrade(100, 3, true);
        for (int i = 0; i < 2; i++) s.onTrade(100, 4, false);
        MomentumSnapshot mo = s.momentumSnapshot(new int[]{600});
        MomentumSnapshot.Window w = mo.windows().get(0);
        assertEquals(15L, w.buy(),  "buy aggressor (isBidAggressor=true) should accumulate to 'buy'");
        assertEquals(8L,  w.sell(), "sell aggressor (isBidAggressor=false) should accumulate to 'sell'");
    }
```

Add the import if missing:
```java
import com.bookmapmcp.state.MomentumSnapshot;
```

- [ ] **Step 2.2: Add volume-profile direction test**

```java
    @Test
    void volumeProfileBuyAggressorPopulatesBuyVolume() {
        InstrumentState s = newState();
        s.onTimestamp(System.nanoTime());
        // Force a session window: drive a trade so the bucket anchors, then verify direction.
        s.onTrade(100, 7, true);   // buy aggressor
        s.onTrade(100, 4, false);  // sell aggressor at same price
        VolumeProfileSnapshot vp = s.volumeProfileEthSnapshot();
        assertEquals(1, vp.levels.size(), "single price should produce one VP level");
        VolumeProfileSnapshot.Level l = vp.levels.get(0);
        assertEquals(7L, l.buyVolume,  "buy aggressor must go to buyVolume");
        assertEquals(4L, l.sellVolume, "sell aggressor must go to sellVolume");
    }
```

- [ ] **Step 2.3: Add tape-bucket direction test**

```java
    @Test
    void tapeBucketsBuyAggressorPopulatesBuyVolume() {
        InstrumentState s = newState();
        s.onTimestamp(System.nanoTime());
        // Small lots (1-10 bucket): 5 buys @ size 3, 2 sells @ size 4
        for (int i = 0; i < 5; i++) s.onTrade(100, 3, true);
        for (int i = 0; i < 2; i++) s.onTrade(100, 4, false);
        TapeBucketsSnapshot tb = s.tapeBucketsSnapshot();
        // First bucket is "1-10"
        TapeBucketsSnapshot.Bucket b = tb.buckets.get(0);
        assertEquals("1-10", b.label);
        assertEquals(15L, b.buyVol5m,  "buy aggressor must accumulate to buyVol");
        assertEquals(8L,  b.sellVol5m, "sell aggressor must accumulate to sellVol");
    }
```

- [ ] **Step 2.4: Add stop-sweep side emission test**

```java
    @Test
    void stopSweepIsBidIsTrueWhenSellersSweepBids() {
        // Spec: STOP_SWEEP isBid=true means bids were swept (sell pressure / bearish).
        // Drive enough aggressor volume in 5s across a magnet to fire the detector.
        InstrumentState s = newState();
        s.onTimestamp(System.nanoTime());
        // Magnet at display price 100 * 0.25 = 25.0
        s.setMagnetLevels(new double[]{25.0});
        // Burst floor is STOP_FLOOR = 80 contracts; emit 5 sell-aggressor prints of size 20 each = 100 contracts.
        for (int i = 0; i < 5; i++) {
            s.onTimestamp(System.nanoTime());
            s.onTrade(100, 20, false);   // false = sell aggressor (hit bid)
        }
        List<MicrostructureEvent> events = s.microstructureEvents(50);
        // Find the most recent STOP_SWEEP
        MicrostructureEvent sweep = events.stream()
                .filter(e -> e.kind == MicrostructureEvent.Kind.STOP_SWEEP)
                .findFirst()
                .orElseThrow(() -> new AssertionError("STOP_SWEEP did not fire"));
        assertTrue(sweep.isBid,  "sell-aggressor sweep should emit isBid=true (bids swept)");
    }

    @Test
    void stopSweepIsBidIsFalseWhenBuyersSweepAsks() {
        InstrumentState s = newState();
        s.onTimestamp(System.nanoTime());
        s.setMagnetLevels(new double[]{25.0});
        for (int i = 0; i < 5; i++) {
            s.onTimestamp(System.nanoTime());
            s.onTrade(100, 20, true);    // true = buy aggressor (lifted offer)
        }
        List<MicrostructureEvent> events = s.microstructureEvents(50);
        MicrostructureEvent sweep = events.stream()
                .filter(e -> e.kind == MicrostructureEvent.Kind.STOP_SWEEP)
                .findFirst()
                .orElseThrow(() -> new AssertionError("STOP_SWEEP did not fire"));
        assertFalse(sweep.isBid, "buy-aggressor sweep should emit isBid=false (asks swept)");
    }
```

Add imports if missing:
```java
import com.bookmapmcp.state.MicrostructureEvent;
import com.bookmapmcp.state.TapeBucketsSnapshot;
import com.bookmapmcp.state.VolumeProfileSnapshot;
import static org.junit.jupiter.api.Assertions.assertFalse;
```

- [ ] **Step 2.5: Run the new Java tests**

```bash
cd /c/Bookmap/addons/MCP/Bookmap && ./gradlew.bat test --tests "com.bookmapmcp.state.InstrumentStateTest"
```

Expected: all tests pass, including the four new ones.

If `gradle` / `java` are unavailable on the sandbox, report this as a blocker and continue to Python-side tasks. The new tests must still be added even if not run locally — CI / the user can run them.

---

## Task 3: Wire magnet-level configuration via HTTP

**Files:**
- Create: `src/main/java/com/bookmapmcp/handlers/MagnetLevelsHandler.java`
- Modify: `src/main/java/com/bookmapmcp/BridgeServer.java` (registration)
- Modify: `mcp-server/bookmap_mcp/server.py` (new MCP tool wrapper)

- [ ] **Step 3.1: Create MagnetLevelsHandler.java**

`src/main/java/com/bookmapmcp/handlers/MagnetLevelsHandler.java`:

```java
package com.bookmapmcp.handlers;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;

import com.bookmapmcp.BridgeRegistry;
import com.bookmapmcp.JsonWriter;
import com.bookmapmcp.state.InstrumentState;

/**
 * POST /magnet_levels?alias=&levels=p1,p2,p3
 *
 * <p>Configures stop-sweep magnet levels for a single instrument. Each level
 * is a display-currency price (NOT a tick). Pass an empty {@code levels}
 * string to clear all levels.
 *
 * <p>Response: {alias, count, levels:[...]}.
 */
public final class MagnetLevelsHandler implements HttpHandler {

    @Override
    public void handle(HttpExchange exchange) throws IOException {
        if (!"POST".equalsIgnoreCase(exchange.getRequestMethod())) {
            Http.writeJsonError(exchange, 405, "method_not_allowed",
                    "Magnet-levels endpoint requires POST.");
            return;
        }
        Map<String, String> q = Http.query(exchange.getRequestURI());
        String alias = q.get("alias");
        if (alias == null || alias.isEmpty()) {
            Http.writeJsonError(exchange, 400, "missing_alias",
                    "Required query param 'alias' was not provided.");
            return;
        }
        InstrumentState state = BridgeRegistry.INSTANCE.get(alias);
        if (state == null) {
            Http.writeJsonError(exchange, 404, "unknown_alias",
                    "No instrument with alias '" + alias + "' is currently attached.");
            return;
        }
        String raw = q.getOrDefault("levels", "");
        List<Double> parsed = new ArrayList<>();
        if (!raw.isEmpty()) {
            for (String part : raw.split(",")) {
                String trimmed = part.trim();
                if (trimmed.isEmpty()) continue;
                double v;
                try { v = Double.parseDouble(trimmed); }
                catch (NumberFormatException e) {
                    Http.writeJsonError(exchange, 400, "bad_level",
                            "Could not parse level '" + trimmed + "' as a number.");
                    return;
                }
                if (!Double.isFinite(v) || v <= 0.0) {
                    Http.writeJsonError(exchange, 400, "bad_level",
                            "Level must be a finite positive price; got '" + trimmed + "'.");
                    return;
                }
                parsed.add(v);
            }
        }
        double[] arr = new double[parsed.size()];
        for (int i = 0; i < arr.length; i++) arr[i] = parsed.get(i);
        state.setMagnetLevels(arr);

        StringBuilder lvls = new StringBuilder("[");
        for (int i = 0; i < arr.length; i++) {
            if (i > 0) lvls.append(',');
            lvls.append(arr[i]);
        }
        lvls.append(']');
        String body = new JsonWriter()
                .beginObject()
                .prop("alias", alias)
                .prop("count", arr.length)
                .rawProp("levels", lvls.toString())
                .endObject()
                .build();
        Http.writeJson(exchange, body);
    }
}
```

- [ ] **Step 3.2: Register the handler in BridgeServer**

Edit `src/main/java/com/bookmapmcp/BridgeServer.java`. Add the import alongside the other handler imports (alphabetical):

```java
import com.bookmapmcp.handlers.MagnetLevelsHandler;
```

Add the context registration right after the `/microstructure_events` line (around line 75):

Old (line 75):
```java
            http.createContext("/microstructure_events", auth.guard(new MicrostructureEventsHandler()));
            http.createContext("/screenshot",      auth.guard(new ScreenshotHandler()));
```

New:
```java
            http.createContext("/microstructure_events", auth.guard(new MicrostructureEventsHandler()));
            http.createContext("/magnet_levels",   auth.guard(new MagnetLevelsHandler()));
            http.createContext("/screenshot",      auth.guard(new ScreenshotHandler()));
```

- [ ] **Step 3.3: Add an MCP wrapper tool in server.py**

Find the existing MCP tool definitions in `mcp-server/bookmap_mcp/server.py` (the `bookmap_microstructure_events` definition is on line 144 per Grep). Add a sibling tool after it. Open the file, find this block:

```python
    def bookmap_microstructure_events(alias: str, max: int = 50) -> Dict[str, Any]:
        ...
        return _call("/microstructure_events", {"alias": alias, "max": max})
```

Add immediately after that function (within the same outer scope — match the surrounding indentation exactly):

```python
    def bookmap_set_magnet_levels(alias: str, levels: List[float]) -> Dict[str, Any]:
        """Configure stop-sweep magnet levels for the given alias. Pass an empty
        list to clear. Returns {alias, count, levels}."""
        # Bridge expects comma-separated prices in a query param. None / empty list
        # → empty string, which clears server-side levels.
        params = {"alias": alias,
                  "levels": ",".join(f"{x:g}" for x in (levels or []))}
        return _call_post("/magnet_levels", params)
```

If a `_call_post` helper does not already exist (check `bridge_client.BridgeClient.post_json`), use the same pattern other POST-route tools use. The `place_limit_order` tool is the reference — find it in server.py and copy its `_call_post` / `client.post_json(...)` invocation form.

Also register the new tool in whatever tool-registry call is at the bottom of `server.py` (FastMCP `@mcp.tool` decorator OR explicit registration — match the existing pattern).

- [ ] **Step 3.4: Verify nothing else needs to wire to magnet levels**

Per repo conventions, dashboard / OR-strategy automation can post to `/magnet_levels` later when it has appropriate levels (OR-H, OR-L, prior day H/L). That is not in scope for this fix — the endpoint exists so it can be called.

- [ ] **Step 3.5: Compile / smoke-test (optional, only if Java is available)**

```bash
cd /c/Bookmap/addons/MCP/Bookmap && ./gradlew.bat compileJava
```

Expected: clean compile.

---

## Task 4: Fix MBO replace price-move handling

**Files:**
- Modify: `src/main/java/com/bookmapmcp/state/InstrumentState.java:554-576`
- Modify: `src/test/java/com/bookmapmcp/state/InstrumentStateTest.java` (one new test)

- [ ] **Step 4.1: Rewrite onMboReplace**

Edit `src/main/java/com/bookmapmcp/state/InstrumentState.java` lines 554-576.

Old:
```java
    public void onMboReplace(String orderId, int newPriceTick, int newSize) {
        mboReplaceEvents.increment();
        mboAvailable = true;
        long nowMs = nowMs();
        MboOrder o = mboOrders.get(orderId);
        if (o != null) {
            int sizeDelta = newSize - o.currentSize;
            boolean isBid = o.isBid;
            o.priceTick = newPriceTick;
            o.currentSize = newSize;
            if (newSize > o.peakSize) o.peakSize = newSize;
            synchronized (mboLock) {
                if (mboDeltas.size() == MBO_DELTA_CAPACITY) mboDeltas.pollFirst();
                mboDeltas.addLast(new MboDelta(nowMs, isBid, newPriceTick, (byte)2, sizeDelta));
            }
            // Pull/Stack: replace with a size change → STACK (size grew) or PULL (shrank).
            if (sizeDelta > 0) {
                creditPullStackFromMbo(isBid, newPriceTick, (byte)(isBid ? 0 : 2), sizeDelta, nowMs);
            } else if (sizeDelta < 0) {
                creditPullStackFromMbo(isBid, newPriceTick, (byte)(isBid ? 1 : 3), -sizeDelta, nowMs);
            }
        }
    }
```

New:
```java
    public void onMboReplace(String orderId, int newPriceTick, int newSize) {
        mboReplaceEvents.increment();
        mboAvailable = true;
        long nowMs = nowMs();
        MboOrder o = mboOrders.get(orderId);
        if (o == null) return;
        boolean isBid = o.isBid;
        int oldPriceTick = o.priceTick;
        int oldSize      = o.currentSize;

        if (newPriceTick != oldPriceTick) {
            // Price move: model it as a full pull at the old level and a full stack
            // at the new level. Two MBO deltas, two pull/stack credits — no
            // sizeDelta double-counting because each side is accounted independently.
            synchronized (mboLock) {
                if (mboDeltas.size() == MBO_DELTA_CAPACITY) mboDeltas.pollFirst();
                mboDeltas.addLast(new MboDelta(nowMs, isBid, oldPriceTick, (byte)2, -oldSize));
                if (mboDeltas.size() == MBO_DELTA_CAPACITY) mboDeltas.pollFirst();
                mboDeltas.addLast(new MboDelta(nowMs, isBid, newPriceTick, (byte)2,  newSize));
            }
            if (oldSize > 0) {
                creditPullStackFromMbo(isBid, oldPriceTick, (byte)(isBid ? 1 : 3), oldSize, nowMs);
            }
            if (newSize > 0) {
                creditPullStackFromMbo(isBid, newPriceTick, (byte)(isBid ? 0 : 2), newSize, nowMs);
            }
        } else {
            // Same price → size-only change. Preserve existing sizeDelta semantics.
            int sizeDelta = newSize - oldSize;
            synchronized (mboLock) {
                if (mboDeltas.size() == MBO_DELTA_CAPACITY) mboDeltas.pollFirst();
                mboDeltas.addLast(new MboDelta(nowMs, isBid, newPriceTick, (byte)2, sizeDelta));
            }
            if (sizeDelta > 0) {
                creditPullStackFromMbo(isBid, newPriceTick, (byte)(isBid ? 0 : 2),  sizeDelta, nowMs);
            } else if (sizeDelta < 0) {
                creditPullStackFromMbo(isBid, newPriceTick, (byte)(isBid ? 1 : 3), -sizeDelta, nowMs);
            }
        }

        // Update tracked order state. peakSize tracks the high-water mark across the
        // order's life regardless of price moves — preserves spoof-detector behavior.
        o.priceTick   = newPriceTick;
        o.currentSize = newSize;
        if (newSize > o.peakSize) o.peakSize = newSize;
    }
```

- [ ] **Step 4.2: Add a regression test for the price-move path**

Append to `InstrumentStateTest.java`:

```java
    @Test
    void mboReplacePriceMoveEmitsPullAtOldAndStackAtNew() {
        InstrumentState s = newState();
        s.onTimestamp(System.nanoTime());
        // Place a 10-lot bid at tick 100, then move it to tick 99.
        s.onMboSend("X1", true, 100, 10);
        s.onMboReplace("X1", 99, 10);
        // BookDynamics groups MBO deltas per (priceTick, isBid). After a price-move
        // replace we expect: a pull entry at the OLD tick AND a stack entry at the NEW tick.
        BookDynamicsSnapshot bd = s.bookDynamicsSnapshot(64);
        long stackedAtNew = 0, pulledAtOld = 0;
        for (BookDynamicsSnapshot.Level l : bd.levels()) {
            if (!l.isBid()) continue;
            if (Math.abs(l.price() - 99 * 0.25) < 1e-9) stackedAtNew = l.stacked1m;
            if (Math.abs(l.price() - 100 * 0.25) < 1e-9) pulledAtOld = l.pulled1m;
        }
        assertEquals(10L, stackedAtNew, "new level should be credited as a full stack");
        assertEquals(10L, pulledAtOld,  "old level should be credited as a full pull");
    }
```

Add the import if missing:
```java
import com.bookmapmcp.state.BookDynamicsSnapshot;
```

If `BookDynamicsSnapshot.Level` does not expose `stacked1m` / `pulled1m` / `price()` / `isBid()` as field reads (they might be record-like — confirm by reading the class), adjust the field access to whatever accessor is in use.

- [ ] **Step 4.3: Run the new MBO replace test**

```bash
cd /c/Bookmap/addons/MCP/Bookmap && ./gradlew.bat test --tests "com.bookmapmcp.state.InstrumentStateTest.mboReplacePriceMoveEmitsPullAtOldAndStackAtNew"
```

Expected: PASS.

---

## Task 5: Fix Python `_source_micro_events` STOP_SWEEP sign + reconcile tests

**Files:**
- Modify: `mcp-server/bookmap_mcp/dashboard.py:1255-1257`
- Modify: `mcp-server/tests/test_session_conviction.py:99, 135, 364-370`
- Verify: `mcp-server/tests/test_micro_bias.py` (no changes — already correct)

- [ ] **Step 5.1: Flip STOP_SWEEP sign in `_source_micro_events`**

Edit `mcp-server/bookmap_mcp/dashboard.py` lines 1255-1257:

Old:
```python
        elif kind == "STOP_SWEEP":
            score += (+0.5 if bid_like else -0.5) * decay
            hits.append(f"SWEEP{'B' if bid_like else 'A'}")
```

New:
```python
        elif kind == "STOP_SWEEP":
            # Spec: isBid=True = bids were swept = sell pressure / bearish.
            # Matches _micro_at_level (line 254) and the browser badge (line 3420).
            score += (-0.5 if bid_like else +0.5) * decay
            hits.append(f"SWEEP{'B' if bid_like else 'A'}")
```

- [ ] **Step 5.2: Update `test_micro_events_stop_sweep_directional` assertions**

Edit `mcp-server/tests/test_session_conviction.py` lines 364-370:

Old:
```python
def test_micro_events_stop_sweep_directional():
    bid_sweep = d._source_micro_events({"micro_events":
        {"events": [{"kind": "STOP_SWEEP", "isBid": True, "price": 100.0}]}})
    ask_sweep = d._source_micro_events({"micro_events":
        {"events": [{"kind": "STOP_SWEEP", "isBid": False, "price": 100.0}]}})
    assert bid_sweep["score"] > 0.0
    assert ask_sweep["score"] < 0.0
```

New:
```python
def test_micro_events_stop_sweep_directional():
    # Spec: STOP_SWEEP isBid=True means BIDS were swept (sell pressure, bearish).
    bid_sweep = d._source_micro_events({"micro_events":
        {"events": [{"kind": "STOP_SWEEP", "isBid": True, "price": 100.0}]}})
    ask_sweep = d._source_micro_events({"micro_events":
        {"events": [{"kind": "STOP_SWEEP", "isBid": False, "price": 100.0}]}})
    assert bid_sweep["score"] < 0.0, f"bids swept = bearish, got {bid_sweep['score']}"
    assert ask_sweep["score"] > 0.0, f"asks swept = bullish, got {ask_sweep['score']}"
```

- [ ] **Step 5.3: Swap STOP_SWEEP isBid in the bull/bear fixtures so they stay semantically aligned**

The `_full_bull_snap` and `_full_bear_snap` fixtures use isBid=True for the BULL fixture's STOP_SWEEP and isBid=False for the BEAR fixture's STOP_SWEEP — that was correct under the inverted `_source_micro_events`. After fixing the function, swap them so the bull scenario contains a bullish (ASK-swept) sweep and the bear scenario contains a bearish (BID-swept) sweep.

Edit `mcp-server/tests/test_session_conviction.py` line 99 inside `_full_bull_snap`:

Old:
```python
            {"kind": "STOP_SWEEP","isBid": True,  "price": 20006.0, "timeMs": 0},
```

New:
```python
            {"kind": "STOP_SWEEP","isBid": False, "price": 20006.0, "timeMs": 0},
```

Edit line 135 inside `_full_bear_snap`:

Old:
```python
        {"kind": "STOP_SWEEP","isBid": False, "price": 19994.0, "timeMs": 0},
```

New:
```python
        {"kind": "STOP_SWEEP","isBid": True,  "price": 19994.0, "timeMs": 0},
```

- [ ] **Step 5.4: Add a parity test pinning `_source_micro_events` against `_micro_at_level`**

Append to `mcp-server/tests/test_session_conviction.py` (so both Python sources can never silently diverge again):

```python
def test_source_micro_events_stop_sweep_matches_micro_at_level():
    """The session-conviction micro source and the FOLLOW/FADE micro source
    must agree on STOP_SWEEP direction. Regression: previously _source_micro_events
    had the sign inverted relative to _micro_at_level."""
    # bid sweep — both sides must score bearish
    bid_evt = {"micro_events":
        {"events": [{"kind": "STOP_SWEEP", "isBid": True, "price": 100.0,
                     "timeMs": 0}]}}
    src_score = d._source_micro_events(bid_evt)["score"]
    lvl_score, _ = d._micro_at_level(bid_evt["micro_events"], 100.0)
    assert src_score < 0 and lvl_score < 0, (
        f"_source_micro_events={src_score:+.2f}, _micro_at_level={lvl_score:+.2f}")
    # ask sweep — both sides must score bullish
    ask_evt = {"micro_events":
        {"events": [{"kind": "STOP_SWEEP", "isBid": False, "price": 100.0,
                     "timeMs": 0}]}}
    src_score = d._source_micro_events(ask_evt)["score"]
    lvl_score, _ = d._micro_at_level(ask_evt["micro_events"], 100.0)
    assert src_score > 0 and lvl_score > 0, (
        f"_source_micro_events={src_score:+.2f}, _micro_at_level={lvl_score:+.2f}")
```

- [ ] **Step 5.5: Verify `test_micro_bias.py` requires NO changes**

`test_micro_bias.py` already pins the spec (STOP_SWEEP isBid=True → bearish at line 47, line 78-83). Re-read to confirm and move on. **Do not edit this file.**

- [ ] **Step 5.6: Run Python tests**

```bash
cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest tests -q
```

Expected: all tests pass. If `pytest` is missing on the environment, the runbook in `CLAUDE.md` says:

```bash
python -m pip install pytest --quiet
```

then re-run.

If the venv at `mcp-server/.venv/` exists (per glob results), activate it first:

```bash
cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && \
  .venv/Scripts/python.exe -m pytest tests -q
```

- [ ] **Step 5.7: Byte-compile sanity check**

```bash
cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m compileall -q bookmap_mcp
```

Expected: clean (no syntax errors).

---

## Task 6: Final verification + final report

- [ ] **Step 6.1: Run the full Python test suite one more time**

```bash
cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest tests -q
```

Capture the result count. Expected: all tests pass.

- [ ] **Step 6.2: Run the full Java test suite**

```bash
cd /c/Bookmap/addons/MCP/Bookmap && ./gradlew.bat test
```

Expected: all tests pass.

- [ ] **Step 6.3: Run a full build**

```bash
cd /c/Bookmap/addons/MCP/Bookmap && ./gradlew.bat build
```

Expected: BUILD SUCCESSFUL. Output: `build/libs/bookmap-mcp-bridge-v2.jar`.

If `java` / `gradle` are unavailable on the executor:
- Report `JAVA_HOME` / `java -version` output as the blocker.
- The user can run the Java half manually; the Python half is unaffected.

- [ ] **Step 6.4: Write the final answer**

Per the user's spec, the final answer must list:
- files changed (with one-line behavior per file)
- behavior fixed (one bullet per audit finding)
- exact test/build commands and their results
- remaining risk/blocker

Format:

```text
## Changes
- src/main/java/com/bookmapmcp/state/TradeRecord.java          — doc comment realigned with Bookmap 7.x isBidAggressor semantics
- src/main/java/com/bookmapmcp/state/InstrumentState.java       — CVD, flowRegime arg, tape buckets, momentum, VolBucket all reinterpret bidAggressor as buy aggressor; MBO replace handles price moves
- src/main/java/com/bookmapmcp/handlers/RecentTradesHandler.java — side string now matches spec
- src/main/java/com/bookmapmcp/handlers/MagnetLevelsHandler.java — NEW: POST /magnet_levels endpoint
- src/main/java/com/bookmapmcp/BridgeServer.java                 — registers /magnet_levels
- src/test/java/com/bookmapmcp/state/InstrumentStateTest.java   — adds CVD, VP, tape-bucket, stop-sweep, MBO-replace regression tests
- mcp-server/bookmap_mcp/server.py                              — adds bookmap_set_magnet_levels MCP tool
- mcp-server/bookmap_mcp/dashboard.py                           — _source_micro_events STOP_SWEEP sign flipped to match spec
- mcp-server/tests/test_session_conviction.py                   — assertions and fixtures realigned with spec; adds parity test

## Audit findings status
1. Trade aggressor — FIXED (8 sites, all in Java)
2. Stop-sweep sign — FIXED in _source_micro_events; Java emit was already correct after Task 1
3. Magnet levels wiring — FIXED via /magnet_levels POST + MCP tool
4. MBO replace price move — FIXED; size-only replace path unchanged
5. Regression tests — ADDED: 4 Java + 2 Python
6. Bookmap 7.4 compat — preserved; no new API surface

## Commands
- `python -m pytest mcp-server/tests -q`   → <RESULT>
- `./gradlew.bat test`                      → <RESULT>
- `./gradlew.bat build`                     → <RESULT>

## Remaining risk
- The new /magnet_levels endpoint is wired but NO caller posts levels yet. The OR-Strategy / dashboard layer should be updated separately to post OR-H / OR-L / PD-H / PD-L. Until then, STOP_SWEEP events still won't fire.
- The existing test `tradeSideIsBidAggressorMappedToSellAggressorLabel` (InstrumentStateTest.java:82-90) is renamed/clarified by context but I did not rename it (out of scope: it only tests boolean pass-through, not semantic labeling).
- Bookmap 7.x TradeInfo.isBidAggressor semantics are stated by the audit; this plan trusts that statement. If the actual API contract turns out to be different, the Java fixes will need to be reverted via the phase backup.
```

---

## Self-review checklist (reviewer runs this before declaring done)

- [ ] **Spec coverage:** Every audit finding (1–6) is implemented in at least one task. ✓ Verified: finding 1 = Task 1, finding 2 = Task 5, finding 3 = Task 3, finding 4 = Task 4, finding 5 = Tasks 2 & 5, finding 6 = enforced by build.gradle pin (no new symbols).
- [ ] **Placeholder scan:** No "TBD", "implement later", "add error handling" lines remain.
- [ ] **Type consistency:** `MagnetLevelsHandler` uses the same `Http.query`, `Http.writeJsonError`, `JsonWriter` surface as every other handler. `MboDelta(nowMs, isBid, tick, kind, size)` signature matches the inner class (InstrumentState.java:124). `BookDynamicsSnapshot.Level` field access in Task 4.2 may need adjustment based on its actual API — verified by re-reading the class before running the test.
- [ ] **No subagent dispatch:** Plan executes inline per CLAUDE.md.
