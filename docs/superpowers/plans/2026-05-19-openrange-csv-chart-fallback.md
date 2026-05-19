# OpenRange CSV Chart Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a Bookmap restart with a cold or corrupt trade cache, the OpenRange indicator draws OR high/low/mid lines on the chart by falling back to the per-symbol signals CSV; live state always wins once it arrives.

**Architecture:** New stateless reader `PaxOpeningRangeChartFallback` scans `openrange-signals-*.csv` under the configured log directory, picks the newest valid row whose `symbol` column exactly matches the attached instrument's symbol, and constructs a free-standing `PaxOpeningRangeDayState` for display only. The painter consults it only when `state.calculator.getDays()` produces no completed days; the result is cached in the painter-side `InstrumentState` with a short TTL so cold-start paint isn't a per-frame disk hit. The trading calculator is never written to.

**Tech Stack:** Java 17, JUnit 5, Bookmap velox SDK (no new dependencies).

---

## File Structure

**Create:**
- `indicators/OpenRange/src/main/java/com/openrange/PaxOpeningRangeChartFallback.java` — stateless CSV reader. Returns `Optional<Result>` carrying a fully-built `PaxOpeningRangeDayState`. Package-private DayState constructors keep this self-contained.
- `indicators/OpenRange/src/test/java/com/openrange/PaxOpeningRangeChartFallbackTest.java` — focused tests with temp dirs.

**Modify:**
- `indicators/OpenRange/src/main/java/com/openrange/PaxOpeningRangeModule.java` — wire fallback into `PaxPainter.update()`. Add cache fields on `InstrumentState`. Add throttled `Log.info` diagnostics.

---

### Task 1: Build the read-only CSV reader

**Files:**
- Create: `indicators/OpenRange/src/main/java/com/openrange/PaxOpeningRangeChartFallback.java`
- Test: `indicators/OpenRange/src/test/java/com/openrange/PaxOpeningRangeChartFallbackTest.java`

- [ ] **Step 1: Write the failing tests (focused, deterministic, temp-dir only)**

Tests cover: exact symbol restore, wrong-symbol ignored, malformed rows ignored, newest valid row wins, no-CSV returns empty, multiple files newest wins, fallback values match the live source (high/low parsed verbatim; mid = (high+low)/2 rounded to tick).

- [ ] **Step 2: Run the test to verify it fails**

```powershell
cd indicators\OpenRange
$env:JAVA_HOME='C:\Program Files\Bookmap\jre'; $env:Path="$env:JAVA_HOME\bin;$env:Path"
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

Expected: compilation failure (class not found).

- [ ] **Step 3: Implement `PaxOpeningRangeChartFallback`**

Stateless `loadLatest(Path logDirectory, String symbol, double tickSize) -> Optional<Result>`. Scans the directory for `openrange-signals-*.csv`. Per file, reads every row, filters by exact `symbol` match, picks the row with the maximum `time` value. Constructs a free-standing `PaxOpeningRangeDayState` via the package-private setters (`setComplete`, `setLastUpdateTime`). Result also exposes `csvPath`, `rowTime`, `symbol` for diagnostics.

- [ ] **Step 4: Run the test to verify it passes**

Expected: PASS.

---

### Task 2: Wire the fallback into the painter (chart-display only)

**Files:**
- Modify: `indicators/OpenRange/src/main/java/com/openrange/PaxOpeningRangeModule.java`

- [ ] **Step 1: Add fallback cache fields to `InstrumentState`**

```java
volatile PaxOpeningRangeDayState fallbackDay;
volatile long fallbackLoadedAtMs;
volatile String fallbackCsvPath;
```

TTL constant on the outer class: `private static final long FALLBACK_TTL_MS = 30_000L;`.

- [ ] **Step 2: Modify `PaxPainter.update` to consult the fallback after the live loop**

```java
if (!drewDay) {
    PaxOpeningRangeDayState fallback = resolveFallback(state, loadSettings());
    if (fallback != null) {
        drawDay(state, fallback);
        drewDay = true;
    }
}
if (!drewDay) {
    addStatus("OpenRange waiting: ...");
}
```

`resolveFallback` consults `state.fallbackDay`; if null or older than `FALLBACK_TTL_MS`, calls `PaxOpeningRangeChartFallback.loadLatest(...)` and caches. Logs `Log.info(...)` on first successful load per `(symbol, csvPath)`.

- [ ] **Step 3: Add invalidation on live arrival**

Inside the live loop, after `drawDay(state, day); drewDay = true;`, also set `state.fallbackDay = null` so a future cache hit doesn't override live state.

- [ ] **Step 4: Build + run unit tests**

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

Expected: jar built, all PaxOpeningRange* tests OK.

---

### Task 3: Diagnostics + verification gate

- [ ] **Step 1: Build OpenRange jar**

Expected: `Built build\libs\openrange-release.jar`.

- [ ] **Step 2: Run root Gradle tests (Java bridge untouched but sanity check)**

```powershell
$env:JAVA_HOME='C:\Program Files\Bookmap\jre'; $env:Path="$env:JAVA_HOME\bin;$env:Path"
.\gradlew.bat test --rerun-tasks
```

Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Summary report**

Files, tests, exact algorithm, remaining risks.

---

## Self-Review

- Spec: exact symbol match? Task 1 step 3 + tests. ✓
- Live wins over CSV? Task 2 step 3 (invalidate on live arrival) + test. ✓
- Wrong-symbol ignored? Task 1 test. ✓
- Newest row wins? Task 1 test. ✓
- Fallback hydrates chart-display only, never trading state? `PaxOpeningRangeChartFallback` does NOT touch the calculator; only `PaxPainter.update` reads it. ✓
- Throttled diagnostics? Task 2 step 2 (one Log.info per first-success). ✓
- Tests deterministic? All tests use `@TempDir`. ✓
