# Pax AI / OpenRange Chart UI Readability Audit

**Date:** 2026-05-27
**Author:** Audit pass — no code changes
**Status:** Plan only. Implementation gated on operator review of this report.

---

## Operating order — right now (during market)

**Do not trade off the current chart display.** The screenshot proves the
chart language is failing: nine layers compete in one visual class, the
trade read does not dominate, the data box vanishes before it can be read or
screenshotted, and bull/bear semantics are not encoded consistently. Acting
on this surface invites avoidable mistakes.

For the remainder of today's session:

- Let the system **log** every signal it would otherwise act on. The
  level-edge JSONL log already records actionable rises with +15 s / +60 s /
  +300 s realized-R backfill (`pax-ai/pax_ai/level_edge_log.py`). That data
  is the input to tomorrow's RTH-close review and to the eventual
  chart-readability work, so the log must keep filling.
- **No discretionary trades keyed off the chart overlay.** If a trade is
  taken, it is taken on the operator's independent read of the market, not
  on the Pax glyphs.
- **No backend tuning, no new endpoints, no new gates, no new math.** The
  next real work is presentation, not analytics.

After RTH close today, the next work is the six-slice implementation plan in
§7 of this document — chart readability and signal hierarchy. Backend
framework, weight tuning, additional research endpoints, and new signal
sources are explicitly out of scope until the chart can be read at a glance.

## Why this audit exists

The operator's complaint: the chart is visually unusable. Signals overlap,
bullish/bearish meaning is unclear, labels collide, and a new data box appears
but disappears too fast to read or screenshot. The operator wants the strong
data we already have — sweeps, iceberg/absorption, institutional
setting/defending, OR/VWAP context — but presented clearly on chart in real
time.

The underlying signal stack is already rich. The problem is not data; it is
presentation. This audit inventories every existing chart layer, identifies the
specific failure modes, then proposes a layered visual hierarchy, a closed
bullish/bearish color vocabulary, a fixed data-box behavior, and a
six-slice implementation plan. **No code is written in this pass.**

Per `CLAUDE.md` (No drift): nothing here adds framework, gates, or research
infrastructure. Every change directly improves live trading decision quality at
the chart.

---

## 1. Current chart layer inventory

The OpenRange addon currently paints **nine visible layer families**. Source
files live under `indicators/OpenRange/src/main/java/com/openrange/`.

| # | Layer | Source class | What it draws | Category | Direction semantics | Lifetime | Toggle |
|---|---|---|---|---|---|---|---|
| 1 | OR High / Low / Mid lines | `PaxOpeningRangeModule.drawDay()` ~L2211–2225 | Horizontal lines + text labels | Context | High=cyan (bull), Low=tomato (bear), Mid=gold (neutral) | Persistent across session | "Show mid line" only |
| 2 | Dynamic OR extension levels | same file L2222–2232 | Horizontal lines + labels at ±N×OR range | Context | Inherit OR color | Persistent | Implicit |
| 3 | Institutional entry markers (INS-L / INS-S) | `drawInstitutionalMarker()` L2062–2127 | Small text label at fill price + time | **Trade signal** | LONG=green text, SHORT=red text | Durable deque, FIFO cap 30, **no time decay** | "Show Institutional Chart Events" |
| 4 | Institutional non-entry markers (WATCH / STND / SCR / ICE / SPD) | same method L2081–2097 | Small text label | Diagnostic / context | **AMBIGUOUS**: yellow/orange/gray — no clear bull/bear | Same FIFO deque cap 30 | Same checkbox as #3 |
| 5 | Chart event evidence trail (SWP / ABS / ICE / SPD / PULL / STACK / ACC / REJ) | `addAllChartEvents()` L1983–1996; `drawInstitutionalChartEvent()` L2006–2049 | 13px glyph (▲/▼/◉) on dark badge + 3-letter label | Raw event | LONG aggressor=▲ green, SHORT aggressor=▼ red, neutral=◉ gray | Durable deque, FIFO cap 50; collision bucketing 5 s × 8 ticks | Same checkbox as #3 |
| 6 | Native Bookmap signal markers (OpenRange buy/sell) | `addNativeSignalMarkerIndicator()` L491; `NativeSignalMarkerIndicator` | Bookmap's native marker (white dot) at bid/ask | Trade signal | Position-only (no color) | **One-shot, lost every poll** | `gateNativeMarkersOnInstitutional` (suppress when institutional pipeline is active) |
| 7 | Pax Heatwave Quant Box | `PaxHeatwavePainter.render()` L28–103; `addHeatwaveBox()` L2163–2194 | Screen-space HUD top-left: 12-row monospaced quant dashboard | Interpreted data + summary verdict | Header verdict tone (BULL=green, BEAR=red, NEUTRAL=gray); per-row tint | **Disappears at 30 s stale (`STALE_AGE_MS`)**; yellow warn at 5 s | "Show Heatwave Quant Box" |
| 8 | Pax AI Level-Edge mini-box / glyphs | `PaxLevelEdgePainter.render()` L56–104; `addLevelEdges()` L1738–1772 | Compact 2–4 line box anchored right of OR-L/OR-H label with LONG/SHORT + confidence + score_R + drivers + STOP | **Trade signal** | LONG=green text, SHORT=red text, WAIT=gray | Per-fetch (≈1 s); rerenders or vanishes when no actionable rows | **NO TOGGLE** (hardcoded on at module attach) |
| 9 | Legacy trend triangles | `PaxTrendTrianglePainter` | (Disabled) | — | — | — | Checkbox grayed out, hardcoded false |

### Layer-by-layer "should it remain visible during live trading?"

- **OR lines + extensions (#1, #2):** Yes. These are the trading frame. Always-on context.
- **INS-L / INS-S entry markers (#3):** Yes, but with time decay and proximity-only persistence.
- **WATCH / STND / SCR / ICE / SPD (#4):** **Move off the price lane.** These are diagnostics, not trades. They belong in a status row, not as price-anchored labels stacked on top of entries.
- **Chart event trail (#5):** Yes, but **collapsed into short-lived pulses**, not durable badges with text. Right now SWP / ABS / ICE clutter the chart for minutes after the event has ceased to matter.
- **Native markers (#6):** Decide one way or the other. They are currently masked by the institutional pipeline gate but still wired. If institutional is the source of truth, retire native markers cleanly to remove a dead layer.
- **Heatwave box (#7):** Yes, but it must not vanish in <5 s. Stale handling currently fails the operator.
- **Level-edge glyph (#8):** Yes — this is the trade read. **It must be the dominant visual.** Today it competes with everything else.
- **Trend triangles (#9):** Already disabled; remove the placeholder checkbox.

---

## 2. Why the current chart is unreadable

These are the specific failures, traced to code:

**F1. OR / Institutional / Level-Edge labels all anchor the same price band.**
OR-H label, INS-L entry marker, ACC/REJ chart event, and the Level-Edge mini-box
all sit at OR-H ± a few ticks within the same 5 s window. The Level-Edge box is
offset only ~60 px right of the OR label. With a tight OR (the Premarket audit
shows 4.75 pt OR widths last night), three to five overlapping labels stack in
the same 8-pixel vertical band.

**F2. Heatwave box disappears too fast.** `PaxHeatwavePainter.STALE_AGE_MS =
30_000` decays the box at 30 s with a yellow warn at 5 s. When the
dashboard/bridge connection flickers — which happens during heavy data bursts —
the operator sees the box phase to yellow then vanish, exactly the "appears but
disappears too quickly to read or screenshot" complaint. **There is no pin and
no minimum visible time.**

**F3. WATCH / STND / SCR / ICE / SPD use ambiguous colors.** In a chart whose
grammar is green=bull / red=bear, putting yellow `WATCH`, orange `STND`, and
gray `SCR` inside the price lane reads as "another bullish signal" or
"another bearish signal" until the operator parses the text. These are
**status codes, not direction signals**, and should not occupy the same visual
class as INS-L / INS-S.

**F4. Chart events outlive their relevance.** `MAX_LIVE_CHART_EVENT_MARKERS =
50` with FIFO eviction means a SWP marker from 90 s ago renders identically to
one that just fired. The operator cannot tell at a glance which events are
*now*. The Premarket audit's observation that "4 OR-L breaks in 22 s failed"
becomes 4 stacked SHORT badges on the chart, undifferentiated.

**F5. Level-edge cannot be toggled.** Per `PaxOpeningRangeModule` L767, the
level-edge fetcher starts unconditionally on module attach. The operator has no
way to mute it during operator-driven manual review. (And conversely, there is
no settings checkbox to confirm "yes, I want this on" — discoverability is
zero.)

**F6. No single dominant trade read.** The Pax AI verdict — the thing the
operator wants to act on — is one of nine layers. The level-edge mini-box is
visually the same size as OR-H labels; it does not dominate. The
`pax-ai-live-edge-audit` already flags this: "Read is text-only in a separate
pywebview window. Operator must alt-tab off the chart to read it, then back to
act."

**F7. Native markers are a dead layer that still consumes attention.** Even
when gated off, they leave a configuration surface the operator has to know
about.

**F8. Raw events (SWP, ABS, ICE, SPD, PULL, ACC, REJ) and interpreted trade
reads (INS-L, INS-S, level-edge) share the same visual class.** Both render as
small price-anchored text/glyph blocks. A SWP near OR-H is a raw microstructure
event; an INS-L is a trade entry. The chart does not distinguish them.

---

## 3. Proposed three-layer hierarchy

Borrowed from cockpit / HUD design: every pixel belongs to exactly one of three
visual classes. No class crosses lanes.

### Layer A — Always-on context (quiet, low-opacity, persistent)

Purpose: the trading frame. Never demands attention; never moves; never
flashes.

- OR-H / OR-L / OR-MID lines and labels
- Dynamic OR extension levels (±N×range)
- VWAP line (currently rendered by Bookmap natively if enabled; respect it)
- Heatwave Quant Box (re-classed — see §5; it is context, not a trade read)

Visual treatment:
- Thin lines (≤2 px); subdued color saturation (~70 % of current)
- Labels right-justified at chart edge, **never inside the price tape**
- Heatwave box top-left HUD, never overlapping price lane

### Layer B — Raw event pulse (short-lived, distinct, directional)

Purpose: "something just happened at this level." Visible long enough to read
(2–4 s default), then fades. Cannot persist past relevance.

- Sweep (SWP)
- Absorption / iceberg (ABS, ICE)
- Spoof risk (SPD)
- Pull / stack shift (PULL, STACK)
- Acceptance / rejection (ACC, REJ)
- Tape burst (currently in digest only — surface as pulse)

Visual treatment:
- Small pulse glyph (10–14 px) with directional shape: ▲ bull aggressor, ▼ bear aggressor, ◉ neutral
- Saturated color (full green / full red / gray) so they pop briefly
- **TTL = 4 s** with fade-out, replacing the current FIFO-only deque (`MAX_LIVE_CHART_EVENT_MARKERS = 50` becomes a hard upper bound only, not the primary lifetime)
- Collision bucketing already present (`collapseChartEventsForDisplay`) — keep, but bucket by 1.5 s × 4 ticks instead of 5 s × 8 ticks
- Pulses are **diagnostic**: they explain *why* a Layer C read fired, they do not act as a trade signal themselves
- **No text label inside the price lane.** Glyph only; text moves to a tooltip on hover or to the Layer C drivers row

### Layer C — Trade read (dominant, single, anchored)

Purpose: the one thing the operator should act on right now. There is **at
most one Layer C glyph visible per side** at any time.

Content:
- BIAS: LONG / SHORT / STAND DOWN
- SETUP: e.g. SWEEP REVERSAL, OR BREAK FOLLOW, ICEBERG DEFEND, OR FADE, LEVEL HOLD
- Top 2–3 reasons (from `top_drivers`, already produced by `level_edge.py`)
- Confidence (0.00–1.00) and score_R (`~` suffix to mark "score not measured EV", per `level_edge_audit`)
- Invalidation / stop price
- Anchor mode (LIVE / LAST_KNOWN_STALE / FALLBACK)

Visual treatment:
- **Larger than any other layer.** Approximately 2× the current
  `PaxLevelEdgePainter` mini-box footprint
- Anchored at the price level the trade is keyed to (OR-L, OR-H, magnet level)
- Heavy border in direction color: thick green = LONG, thick red = SHORT,
  thick gray = STAND DOWN
- Lives until invalidated (price moves through stop, anchor exits LIVE, news
  blackout fires, or upstream `actionable` flips to false). Replaces, never
  stacks with, a prior Layer C glyph on the same side.
- INS-L / INS-S markers (current entry confirmations) become a small "FILLED"
  badge attached to the Layer C glyph, not a separate label

### Hierarchy rule (the only one)

> Layer A whispers. Layer B blinks. Layer C speaks.

If a new chart element doesn't fit one of the three, it doesn't belong on the
chart — it belongs in the Heatwave box, the side panel, or the JSONL log.

---

## 4. Visual vocabulary (closed)

Every chart element draws from this fixed palette. No exceptions, no
introduction of new colors without a vocabulary update.

| Meaning | Color | Shape / cue | Where it appears |
|---|---|---|---|
| Bullish (continuation or entry) | Green `#3CDC7D` | ▲ filled, up arrow, right-leaning chevron | Layer B (▲ pulse), Layer C (LONG border / text) |
| Bearish (continuation or entry) | Red `#EB6464` | ▼ filled, down arrow, right-leaning chevron | Layer B (▼ pulse), Layer C (SHORT border / text) |
| Reversal (curved direction change) | Same as direction (green or red), curved arrow ↻ / ↺ | Reversal arc | Layer C SETUP cue when setup is `*_REV` or `LEVEL_FADE_*` |
| Continuation (straight) | Same as direction, straight arrow → | Layer C SETUP cue when setup is `OR_BREAK_FOLLOW` |
| Stand down / blocked | Gray `#9C9C9C` with yellow `#E0C850` highlight on the `blocked_reason` field | Shield ⛨ or X | Layer C only, when `actionable=false` or `direction=WAIT` |
| Context (OR / VWAP / extensions) | OR-H cyan `#5CC8E0`, OR-L tomato `#E07060`, OR-MID gold `#D8B848` | Thin line + small label | Layer A only |
| Diagnostic status (WATCH / SCRATCH) | Pale gray `#7A7A7A` | No glyph in price lane — relegated to Heatwave row | Layer A status row only |
| Severity highlight | Bright yellow `#FFD040` | Single-pixel outline | Used on any layer when severity > threshold (e.g. very large sweep, breaking iceberg) |

**Removed from the visual lexicon:**
- Orange (255, 153, 0) currently used for STND/ICE/SPD in `drawInstitutionalMarker()` — confuses bull/bear reading
- Yellow as a direction (only allowed as severity outline or as the blocked-reason text accent)
- White native markers (#6) — retire after toggle decision
- Any unanchored blue / purple

**Bullish/bearish disambiguation rule:** if an operator pauses the chart and
looks at any single glyph, the color + shape combination must encode bias.
Color alone is not sufficient (color-blindness, monitor calibration); shape
alone is not sufficient (sweep arrow vs entry arrow look similar at small
size). Both must agree.

---

## 5. Data box behavior — fix specification

The "new data box that disappears too quickly" maps to the **Heatwave Quant
Box** (`PaxHeatwavePainter`) and/or the **Level-Edge mini-box**
(`PaxLevelEdgePainter`). Today, both auto-hide on staleness and neither has a
pin.

Proposed fixed behavior — applies to any persistent on-chart info box:

| Property | Today | Proposed |
|---|---|---|
| Minimum visible time after first render | None (vanishes immediately on stale flip) | **15 s minimum**, then enter STALE display state, never hide |
| Stale state | Disappears at `STALE_AGE_MS = 30_000` | Show "STALE — last update Ns ago" banner in box, keep last good content visible |
| Bridge offline | Vanishes | Show "BRIDGE OFFLINE" banner, last good content visible (already implemented for Heatwave per `PaxHeatwaveModel.State.BRIDGE_OFFLINE`; extend to all boxes) |
| Pin behavior | None | Right-click box → "Pin (do not refresh)"; pinned box freezes content + shows pin icon |
| Hotkey | None | `P` while box is hovered → toggle pin |
| Screenshot mode | None | `Shift+P` → pin + flash a thin white border for 200 ms (operator confirmation), then call existing `bookmap_screenshot` MCP tool if available |
| Snapshot-mode field set | n/a | When pinned, append a footer line: `pinned at HH:MM:SS.mmm CT — alias — anchor mode` |
| Anchoring | Heatwave: screen-space top-left, configurable XY. Level-Edge: chart-space right-of OR labels | Keep. **Never** anchored to mouse — boxes do not follow the cursor |
| Avoid covering price | No explicit rule — `heatwaveBoxX`/`heatwaveBoxY` user-set | Auto-detect if box overlaps current `book.mid` price lane in chart-space; if so, slide it to the opposite side of the chart for one render cycle |

Inside-box content rule (Heatwave):
- Today: 12 rows × variable score/hint. Operator must parse the column.
- Proposed: header verdict (BIAS + confidence + age) in one large row, then
  exactly **6 rows**: OR, FLOW, VWAP, VP, MICRO, TA. The remaining sources
  (`tape_buckets`, `lt_liquidity`, `pull_stack` raw, etc.) compress into a
  Heatwave "more" expandable. The `pax-ai-live-edge-audit` already recommends
  cutting 17 conviction sources to 3–6 in the digest; mirror that here.

Inside-box content rule (Level-Edge):
- Today: 2–4 line micro-box.
- Proposed: when it becomes Layer C (§3), it expands to a single
  ~250×120 px card with:
  ```
  LONG  conf 0.62  R~1.45
  OR BREAK FOLLOW
  Pull · VWAP · Absorb
  STOP 21036.50   anchor LIVE
  ```
  Setup line and STOP line are mandatory. `top_drivers` already produces the
  drivers row. `setup` already produces the setup label.

---

## 6. Event interpretation rules

How raw Layer B events map into Layer C trade reads. These rules are already
implicit in `edge_calculus` / `level_edge.py` / the institutional pipeline; the
job here is to make them visually explicit on the chart.

**Sweep near level:**
- Raw: SWP pulse fires at OR-H or OR-L.
- Reversal read (Layer C `LEVEL_FADE_*`) **only if** price fails to hold beyond
  the level AND (`absorption` confirms OR orderbook flips against the sweep
  direction).
- Continuation read (Layer C `OR_BREAK_FOLLOW`) **only if** reclaim holds AND
  `tape_flow` confirms AND `pull_stack` does not show stacking against.

**Iceberg / absorption at level:**
- Raw: ICE / ABS pulse at magnet price.
- Default Layer C: **STAND DOWN** at that level until invalidated (price breaks
  through the iceberg or absorption disperses). This already exists as
  `STAND_DOWN` in the institutional pipeline; visually it must dominate, not
  hide.

**Tape burst through level:**
- Raw: tape pulse (currently in digest only — see §3 Layer B).
- Continuation read **only if** pull/stack agrees AND book is not thin on the
  far side.
- Otherwise: raw pulse only, no Layer C upgrade.

**VWAP stretch:**
- Raw: not a pulse — slow state. Display via Heatwave VWAP row only.
- Layer C revert read **only if** sigma_z exceeds threshold AND a rejection
  event (SWP at the stretch extreme, then reclaim) fires.

**OR break follow vs OR fade:**
- These are visually adjacent setups (same level, opposite direction). Layer C
  must use the SETUP label to disambiguate: `OR BREAK FOLLOW` (straight arrow)
  vs `OR FADE` (curved arrow). Color alone is insufficient — both can be green
  (LONG follow vs LONG fade of a SHORT break).

**Stand-down precedence:**
- If any of: news blackout, anchor not LIVE, stale snapshot, confidence <
  0.35, size_tier == NONE — Layer C must render as STAND DOWN with the
  specific `blocked_reason` from `level_edge.py:372–384`. No raw pulses or
  context layers change; only Layer C disables.

---

## 7. Implementation slices

Each slice is small, reversible, independently mergeable, and adds no new
framework. Slices are ordered so each one is observably better than the last
without depending on subsequent slices. **Slices land after RTH so live trading
is not disturbed.**

### Slice UI-1 — Visibility audit + layer registry (no behavior change)

- Add a single static `PaxChartLayerRegistry.java` enumerating all 9 layers
  with category (CONTEXT / PULSE / TRADE_READ / DIAGNOSTIC) and current toggle
  binding. Used as a single source of truth for downstream slices and for the
  settings panel.
- No painter changes. Pure refactor.
- **Files touched:** new file under `indicators/OpenRange/src/main/java/com/openrange/`; reference from `PaxOpeningRangeModule.java`.
- **Tests:** `PaxChartLayerRegistryTest` — every painter class referenced by `PaxOpeningRangeModule` has exactly one registry entry.
- **Must not change:** any rendered pixel, any settings persistence format, any HTTP fetcher behavior.

### Slice UI-2 — Closed visual vocabulary (§4) applied consistently

- Centralize colors in `PaxHeatwaveColors.java` (already exists) → rename to
  `PaxChartPalette.java` and expand to cover Layer A / B / C.
- Replace inline orange/yellow/gray RGB triples in `drawInstitutionalMarker()` L2081–2097 with palette references.
- Replace ambiguous WATCH/STND/SCR/ICE/SPD price-anchored labels with a single status indicator in the Heatwave box "STATUS" row. They stop appearing in the price lane.
- **Files touched:** `PaxChartPalette.java` (renamed), `PaxOpeningRangeModule.java` (drawInstitutionalMarker), `PaxHeatwavePainter.java`.
- **Tests:** `PaxChartPaletteTest` — no painter pulls a Color directly outside the palette; status codes (WAIT/STND/SCR) do not appear via `drawInstitutionalMarker` text path.
- **Must not change:** OR line colors (operator-configurable), Heatwave box position, INS-L / INS-S directional colors (green/red), any HTTP / fetcher logic.

### Slice UI-3 — Persistent / pinnable data box (§5)

- Drop `STALE_AGE_MS` auto-hide. Replace with STALE banner inside the box; box never hides on stale.
- Add pin state to `PaxHeatwavePainter` and `PaxLevelEdgePainter`. Pin via right-click context menu (Java AWT popup) and `P` hotkey while hovered.
- Add `Shift+P` "screenshot mode": pin + 200 ms white flash; if `bookmap_screenshot` MCP tool is reachable, invoke it.
- Auto-slide rule: if box overlaps `book.mid` band, slide to opposite half of chart for one render.
- **Files touched:** `PaxHeatwavePainter.java`, `PaxLevelEdgePainter.java`, `PaxHeatwaveFetcher.java` (no fetcher changes — purely render), `PaxOpeningRangeModule.java` (input listener for `P` / `Shift+P`).
- **Tests:** `PaxHeatwavePainterTest` — pinned box does not advance content on new fetch; stale box continues to render last good content; box flagged for slide when overlap-band detected.
- **Must not change:** snapshot fetch logic, dashboard URL, poll interval, any field that the Python dashboard produces.

### Slice UI-4 — Collapse raw events into pulses (§3 Layer B)

- Introduce `TTL_MS = 4_000` for chart event markers in
  `PaxOpeningRangeModule` (replace deque-only lifetime with TTL + cap).
- Reduce collision bucket from 5 s × 8 ticks to 1.5 s × 4 ticks
  (`CHART_EVENT_COLLISION_TIME_MS` and price-bucket equivalent).
- Remove text label from chart events (glyph only); move text to a tooltip
  on hover. Layer B is glyph-only in the price lane.
- **Files touched:** `PaxOpeningRangeModule.java` (drawInstitutionalChartEvent, addAllChartEvents, collapseChartEventsForDisplay), no painter rewrite.
- **Tests:** `ChartEventLifetimeTest` — events expire at 4 s; collision bucketing collapses ≤4-tick / ≤1.5 s clusters; no text drawn inside price lane.
- **Must not change:** the upstream events emitted by the Python dashboard, the institutional pipeline scoring, INS-L / INS-S entry markers.

### Slice UI-5 — Single active primary edge glyph (§3 Layer C)

- Repaint `PaxLevelEdgePainter` as the dominant Layer C glyph: ~250×120 px,
  heavy border, mandatory setup + drivers + stop + anchor mode rows.
- Enforce: **at most one Layer C glyph per side** (LONG / SHORT). New
  actionable signal on the same side replaces, never stacks with, the prior
  one.
- Add settings toggle: "Show Pax AI Trade Read" (default ON). Closes the gap
  flagged as F5.
- INS-L / INS-S entry markers become a small "FILLED" subtitle attached to
  the Layer C glyph when a fill matches the read.
- **Files touched:** `PaxLevelEdgePainter.java`, `PaxOpeningRangeModule.java` (addLevelEdges, drawInstitutionalMarker), `PaxOpeningRangeUiSettings.java` (new toggle).
- **Tests:** `PaxLevelEdgePainterTest` — at most one glyph per side rendered; STAND DOWN renders with correct `blocked_reason`; mandatory rows all present; replaces prior glyph not stacks.
- **Must not change:** the Python `level_edge.py` payload shape, the JSONL log writer, the `score_R` math, anything in `edge_calculus.py`.

### Slice UI-6 — Screenshot / replay review mode

- Add `Ctrl+Shift+R` "review mode": freezes the entire chart overlay (all
  layers pinned) and disables fetcher updates until released.
- Status banner: "REVIEW MODE — overlay frozen" in red across the top of the
  chart screen-space layer.
- Designed for post-event review: operator can screenshot the exact moment a
  setup fired, including all pulses and the Layer C glyph, without race
  conditions.
- **Files touched:** `PaxOpeningRangeModule.java` (input listener + freeze flag), `PaxHeatwavePainter.java`, `PaxLevelEdgePainter.java` (respect freeze flag).
- **Tests:** `ChartReviewModeTest` — when frozen, no painter advances state on fetch; release resumes.
- **Must not change:** fetcher threads continue polling (data still arrives, journal still writes); only the visual layer freezes.

---

## 8. Stop conditions — when NOT to plot

These are the gates that must be enforced **at render time**, not just at
decision time. The chart should never show a misleading actionable read.

Layer C (trade read) must downgrade to STAND DOWN, with `blocked_reason`
shown, when any of the following:

| Condition | Source field | Today's behavior | Required behavior |
|---|---|---|---|
| Anchor not LIVE | `snap.session.anchorMode != "LIVE"` | Already enforced in `level_edge.py` (file:line 372–384) | Render STAND DOWN with `blocked_reason="anchor"`; no green/red |
| News blackout | `snap.news.blocked == true` | Already enforced | STAND DOWN, `blocked_reason="news:<label>"` |
| Stale snapshot | `snap.age_ms > 5_000` | Enforced upstream | STAND DOWN, `blocked_reason="stale"` |
| Health not ok | `snap.health != "ok"` | Enforced | STAND DOWN, `blocked_reason="health"` |
| Session out of window | `snap.session.code in {PRE_MARKET, POST_MARKET, OR_FORMING, CLOSE_RISK}` | Enforced | STAND DOWN, `blocked_reason="session"` |
| Confidence below floor | `confidence < 0.35` | Enforced | STAND DOWN, `blocked_reason="low_confidence"` |
| Size tier below HALF | `size_tier in {NONE}` | Enforced | STAND DOWN, `blocked_reason="size_tier"` |
| Thesis gate blocks | `thesis_gated == NONE` | Enforced | STAND DOWN, `blocked_reason="thesis_gate"` |
| Not near level | `level.proximity == False` | Enforced | Hide glyph entirely (this is "no setup", not "blocked") |
| No directional source | `composite.direction not in {FOLLOW_*, FADE_*}` and `level.decision not actionable` | Enforced | Hide glyph entirely |
| Conflicting raw events | Layer B sweep + opposite-direction absorption within 1.5 s | Not currently a check | Layer C downgrade to STAND DOWN with `blocked_reason="conflicting_events"` (new gate, Slice UI-5) |

Layer B (raw event pulses) must NOT fire when:

- Snapshot age > 5 s (stale; raw event is meaningless without anchoring)
- Health not ok
- Anchor not LIVE (raw events still happen, but the chart is informational only — pulses display in gray with reduced opacity to mark "post-anchor")

Layer A (context) is always on. Context never blocks. The OR / VWAP / extension
lines are part of the trading frame whether the operator is taking trades or
not.

**Diagnostic-only raw events** (WATCH, SCRATCH, status codes from the
institutional pipeline): never plot on chart. They go in the Heatwave STATUS
row or the JSONL log. They do not earn a price-lane label.

---

## What waits until after RTH data review

Per the Premarket audit's GO / NO-GO finding and the operator's "no drift"
rule, the following are **explicitly out of scope for this audit**:

- Strategy tuning (no weight changes, no threshold changes)
- New signal sources
- New decision logic
- New gates beyond the `conflicting_events` Layer C downgrade (which is a
  presentation rule, not a strategy rule)
- Live order routing changes
- Any change to `level_edge.py` payload shape or the JSONL log schema

The audit's job is **presentation hygiene**: surface what we already detect, in
a form the operator can act on without alt-tabbing.

---

## Summary

The chart is unreadable because nine layers share one visual class, status
codes are dressed as direction signals, raw events outlive their relevance,
the trade read does not dominate, and the Heatwave box can vanish in under
five seconds. The fix is a three-layer hierarchy (whisper / blink / speak),
a closed bull/bear vocabulary, a pinnable data box with no auto-hide, a Layer
B pulse pattern with 4-second TTL, and a single dominant Layer C trade-read
glyph. Six small reversible slices land after RTH. No strategy changes; no
new signals; no new framework.
