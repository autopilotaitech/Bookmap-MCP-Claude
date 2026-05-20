package com.bookmapmcp.state;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assertions.assertFalse;

import java.time.LocalDate;
import java.time.LocalTime;
import java.time.ZoneId;
import java.time.ZonedDateTime;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * v19 anchor audit: AnchoredVwapTracker and InitialBalanceTracker must
 * anchor at the OR session open (the dashboard pushes this via
 * InstrumentState.applyConfig). The legacy 08:30 / 09:30 hard-coded
 * times must no longer drive these weighted sources.
 */
class OrAnchoredTrackersTest {

    private static final ZoneId CT = ZoneId.of("America/Chicago");
    private static final LocalDate D = LocalDate.of(2025, 6, 12);

    private LocalTime originalSessionOpen;

    @BeforeEach
    void saveOriginal() {
        originalSessionOpen = InstrumentState.configSessionOpen();
    }

    @AfterEach
    void restoreOriginal() {
        // Restore default 08:30 so other tests are unaffected.
        InstrumentState.applyConfig(originalSessionOpen, null, null, null);
    }

    private static long ctMs(int hour, int minute, int second) {
        return ZonedDateTime.of(D, LocalTime.of(hour, minute, second), CT)
                .toInstant().toEpochMilli();
    }

    @Test
    void anchoredVwapFollowsOrSessionAt0930() {
        // Operator pushes 09:30 OR start.
        InstrumentState.applyConfig(LocalTime.of(9, 30), null, null, null);
        AnchoredVwapTracker t = new AnchoredVwapTracker();

        // Trade at 08:31 CT — BEFORE the 09:30 OR open. Tracker must
        // NOT register this trade inside the drive window.
        t.onTrade(100.0, 5, ctMs(8, 31, 0));
        AnchoredVwapTracker.AnchoredVwapSnapshot s1 = t.snapshot();
        // sessionAnchor for an 08:31 trade under 09:30 OR = yesterday 09:30.
        ZonedDateTime expectedAnchor = ZonedDateTime.of(D.minusDays(1), LocalTime.of(9, 30), CT);
        assertEquals(expectedAnchor.toInstant().toEpochMilli(), s1.sessionAnchorMs,
                "08:31 trade under 09:30 OR must anchor at YESTERDAY's 09:30, not today's 08:30");

        // Trade at 09:31 CT — inside the 09:30 + 5min drive window.
        t.onTrade(101.0, 5, ctMs(9, 31, 0));
        AnchoredVwapTracker.AnchoredVwapSnapshot s2 = t.snapshot();
        ZonedDateTime todayOpen = ZonedDateTime.of(D, LocalTime.of(9, 30), CT);
        assertEquals(todayOpen.toInstant().toEpochMilli(), s2.sessionAnchorMs,
                "09:31 trade under 09:30 OR must anchor at TODAY's 09:30");
        assertEquals(s2.driveOpenMs, s2.sessionAnchorMs,
                "driveOpenMs must equal sessionAnchorMs");
        assertEquals(s2.driveOpenMs + 300L * 1000L, s2.driveCloseMs,
                "driveCloseMs must be driveOpenMs + 300s (default drive seconds)");
        assertEquals(101.0, s2.driveHigh, 1e-9, "trade inside drive window must register");
    }

    @Test
    void anchoredVwapFollowsOrSessionAt1700Overnight() {
        // Operator pushes 17:00 OR start (overnight futures session).
        InstrumentState.applyConfig(LocalTime.of(17, 0), null, null, null);
        AnchoredVwapTracker t = new AnchoredVwapTracker();

        // Trade at 17:02 CT — inside the 17:00 + 5min drive.
        t.onTrade(200.0, 10, ctMs(17, 2, 0));
        AnchoredVwapTracker.AnchoredVwapSnapshot s = t.snapshot();
        ZonedDateTime expected = ZonedDateTime.of(D, LocalTime.of(17, 0), CT);
        assertEquals(expected.toInstant().toEpochMilli(), s.sessionAnchorMs,
                "17:02 trade under 17:00 OR must anchor at today's 17:00, not 08:30");
        assertEquals(200.0, s.driveHigh, 1e-9);
    }

    @Test
    void initialBalanceFollowsOrSessionAt0930() {
        InstrumentState.applyConfig(LocalTime.of(9, 30), null, null, null);
        InitialBalanceTracker t = new InitialBalanceTracker();

        // Trades inside the 09:30 + 1h IB window.
        t.onTrade(50.0,  ctMs(9, 31, 0));
        t.onTrade(55.0,  ctMs(9, 35, 0));
        t.onTrade(48.0,  ctMs(10, 5, 0));
        // Trade after IB close (10:30).
        t.onTrade(60.0,  ctMs(10, 35, 0));

        InitialBalanceTracker.InitialBalanceSnapshot ib = t.snapshot();
        ZonedDateTime todayOpen = ZonedDateTime.of(D, LocalTime.of(9, 30), CT);
        assertEquals(todayOpen.toInstant().toEpochMilli(), ib.sessionStartMs,
                "IB sessionStart must equal today's 09:30 (OR anchor), not 08:30");
        assertEquals(ib.ibOpenMs, ib.sessionStartMs);
        assertEquals(ib.ibOpenMs + 3600L * 1000L, ib.ibCloseMs,
                "ibCloseMs must be ibOpenMs + 3600s");
        assertEquals(55.0, ib.ibHigh, 1e-9, "IB high captured inside window");
        assertEquals(48.0, ib.ibLow,  1e-9, "IB low captured inside window");
        assertTrue(ib.ibComplete, "IB must be complete after 10:30");
        assertEquals(60.0, ib.sessionHigh, 1e-9,
                "post-IB trade must extend sessionHigh but NOT ibHigh");
    }

    @Test
    void initialBalanceFollowsOrSessionAt1700Overnight() {
        InstrumentState.applyConfig(LocalTime.of(17, 0), null, null, null);
        InitialBalanceTracker t = new InitialBalanceTracker();

        t.onTrade(80.0,  ctMs(17, 5, 0));
        t.onTrade(85.0,  ctMs(17, 30, 0));
        t.onTrade(78.0,  ctMs(17, 55, 0));

        InitialBalanceTracker.InitialBalanceSnapshot ib = t.snapshot();
        ZonedDateTime todayOpen = ZonedDateTime.of(D, LocalTime.of(17, 0), CT);
        assertEquals(todayOpen.toInstant().toEpochMilli(), ib.sessionStartMs,
                "17:00 OR anchor must drive IB sessionStart");
        assertEquals(85.0, ib.ibHigh, 1e-9);
        assertEquals(78.0, ib.ibLow,  1e-9);
        assertFalse(ib.ibComplete, "IB should not be complete before 18:00 with 1h duration");
    }

    // ─── v20 seconds-precision invariant ────────────────────────────────
    //
    // The bridge must honor startSecond from the OR UI exactly. The dashboard
    // pushes "17:00:15" via /config; applyConfig stores the full LocalTime in
    // RTH_OPEN; rthAnchorMs / sessionAnchorMs flip at the precise second.

    @Test
    void applyConfigPreservesSecondsInSessionOpen() {
        InstrumentState.applyConfig(LocalTime.of(17, 0, 15), null, null, null);
        assertEquals(LocalTime.of(17, 0, 15), InstrumentState.configSessionOpen(),
                "applyConfig must preserve startSecond in RTH_OPEN");
        assertEquals(LocalTime.of(17, 0, 15), InstrumentState.configRthOpen(),
                "configRthOpen (legacy alias) must also reflect seconds");
    }

    @Test
    void anchoredVwapAnchorFlipsAtExactSecondUnderHHMMSS() {
        // Operator pushes 17:00:15 OR start (e.g. tape opens 15s after 17:00).
        InstrumentState.applyConfig(LocalTime.of(17, 0, 15), null, null, null);
        ZonedDateTime expectedToday   = ZonedDateTime.of(D, LocalTime.of(17, 0, 15), CT);
        ZonedDateTime expectedYesterday =
                ZonedDateTime.of(D.minusDays(1), LocalTime.of(17, 0, 15), CT);

        // 1) Trade at 17:00:10 — BEFORE today's 17:00:15 anchor by 5s.
        AnchoredVwapTracker pre = new AnchoredVwapTracker();
        pre.onTrade(100.0, 1, ctMs(17, 0, 10));
        assertEquals(expectedYesterday.toInstant().toEpochMilli(),
                pre.snapshot().sessionAnchorMs,
                "17:00:10 under 17:00:15 OR must anchor at YESTERDAY's 17:00:15");

        // 2) Trade at 17:00:16 — 1s AFTER today's 17:00:15 anchor.
        AnchoredVwapTracker post = new AnchoredVwapTracker();
        post.onTrade(101.0, 1, ctMs(17, 0, 16));
        assertEquals(expectedToday.toInstant().toEpochMilli(),
                post.snapshot().sessionAnchorMs,
                "17:00:16 under 17:00:15 OR must anchor at TODAY's 17:00:15");
    }

    @Test
    void initialBalanceAnchorFlipsAtExactSecondUnderHHMMSS() {
        InstrumentState.applyConfig(LocalTime.of(17, 0, 15), null, null, null);
        ZonedDateTime expectedToday = ZonedDateTime.of(D, LocalTime.of(17, 0, 15), CT);

        // Trade at 17:00:16 — 1s past anchor → today's session.
        InitialBalanceTracker t = new InitialBalanceTracker();
        t.onTrade(50.0, ctMs(17, 0, 16));
        assertEquals(expectedToday.toInstant().toEpochMilli(),
                t.snapshot().sessionStartMs,
                "17:00:16 under 17:00:15 OR must drive IB sessionStart to today's 17:00:15");
    }

    @Test
    void secondsOnlyChangeIsAFreshAnchor() {
        // First push: 17:00:00.
        InstrumentState.applyConfig(LocalTime.of(17, 0, 0), null, null, null);
        AnchoredVwapTracker t = new AnchoredVwapTracker();
        t.onTrade(100.0, 1, ctMs(17, 0, 30));
        long firstAnchor = t.snapshot().sessionAnchorMs;

        // Second push: 17:00:15 — only seconds change. Same tracker; next
        // trade must re-anchor at the new second.
        InstrumentState.applyConfig(LocalTime.of(17, 0, 15), null, null, null);
        t.onTrade(101.0, 1, ctMs(17, 1, 0));
        long secondAnchor = t.snapshot().sessionAnchorMs;
        assertNotEquals(firstAnchor, secondAnchor,
                "seconds-only OR change must trigger session reset in AnchoredVwapTracker");
        // Confirm the new anchor is exactly today 17:00:15, not 17:00:00.
        long expected = ZonedDateTime.of(D, LocalTime.of(17, 0, 15), CT)
                .toInstant().toEpochMilli();
        assertEquals(expected, secondAnchor);
    }

    @Test
    void operatorChangingOrAnchorResetsTrackers() {
        // Start on 08:30 OR.
        InstrumentState.applyConfig(LocalTime.of(8, 30), null, null, null);
        AnchoredVwapTracker t = new AnchoredVwapTracker();
        t.onTrade(100.0, 5, ctMs(8, 31, 0));
        long firstAnchor = t.snapshot().sessionAnchorMs;

        // Operator changes OR to 09:30 mid-day; next tick re-anchors.
        InstrumentState.applyConfig(LocalTime.of(9, 30), null, null, null);
        t.onTrade(101.0, 5, ctMs(10, 0, 0));
        long secondAnchor = t.snapshot().sessionAnchorMs;
        assertNotEquals(firstAnchor, secondAnchor,
                "OR anchor change must trigger session reset in AnchoredVwapTracker");
    }
}
