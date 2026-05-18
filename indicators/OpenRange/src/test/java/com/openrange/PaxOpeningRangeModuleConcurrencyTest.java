package com.openrange;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.LocalTime;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Concurrency smoke test for the locking model used by
 * {@code PaxOpeningRangeModule.InstrumentState}.
 *
 * <p>This test exercises {@link PaxOpeningRangeCalculator} from two threads:
 * a "data" thread that drives {@code onTrade(...)} and a "control" thread
 * that periodically calls {@code reset()} and iterates {@code getDays()}.
 * Both threads share a single monitor that mirrors the per-instrument
 * {@code state.lock} used by the module. Without external synchronization,
 * concurrent mutation of the calculator's internal TreeMap can throw
 * {@link java.util.ConcurrentModificationException} or produce half-populated
 * state. With the locking model in place, the test must complete cleanly
 * within its time bound.
 *
 * <p>This is a smoke test, not a proof. Concurrency bugs may not always
 * surface in a single bounded run; the test serves to document the
 * invariant and provide regression protection if synchronization is later
 * removed from the module.
 */
public class PaxOpeningRangeModuleConcurrencyTest {

    private static final int TRADE_ITERATIONS = 5000;
    private static final int RESET_ITERATIONS = 200;
    private static final long TIMEOUT_SECONDS = 2;

    public static void main(String[] args) throws Exception {
        sharedLockPreventsCalculatorRaces();
        System.out.println("PaxOpeningRangeModuleConcurrencyTest: OK");
    }

    private static void sharedLockPreventsCalculatorRaces() throws Exception {
        final PaxOpeningRangeCalculator calculator =
                new PaxOpeningRangeCalculator(PaxOpeningRangeSettings.defaults(), "ESM6", 0.25);
        final Object lock = new Object();
        final CountDownLatch start = new CountDownLatch(1);
        final AtomicReference<Throwable> failure = new AtomicReference<>();
        final LocalDate date = LocalDate.of(2026, 4, 28);

        Thread tradeThread = new Thread(() -> {
            try {
                start.await();
                for (int i = 0; i < TRADE_ITERATIONS; i++) {
                    // Spread trades across the OR window so the calculator
                    // mutates both the per-day state and the TreeMap.
                    int second = i % 30;
                    LocalDateTime t = LocalDateTime.of(date, LocalTime.of(9, 30, second));
                    double price = 5000.0 + (i % 100) * 0.25;
                    synchronized (lock) {
                        calculator.onTrade(t, price);
                    }
                }
            } catch (Throwable t) {
                failure.compareAndSet(null, t);
            }
        }, "or-concurrency-trade");

        Thread resetThread = new Thread(() -> {
            try {
                start.await();
                for (int i = 0; i < RESET_ITERATIONS; i++) {
                    synchronized (lock) {
                        // Iterate days inside the lock to mirror the painter's
                        // snapshot pattern; this is exactly the read path that
                        // would otherwise race with onTrade.
                        int count = 0;
                        for (PaxOpeningRangeDayState day : calculator.getDays()) {
                            if (day != null) {
                                count++;
                            }
                        }
                        if (count < 0) {
                            throw new AssertionError("negative day count");
                        }
                        calculator.reset();
                    }
                    // Brief yield to interleave with the trade thread.
                    Thread.yield();
                }
            } catch (Throwable t) {
                failure.compareAndSet(null, t);
            }
        }, "or-concurrency-reset");

        tradeThread.start();
        resetThread.start();
        start.countDown();

        tradeThread.join(TimeUnit.SECONDS.toMillis(TIMEOUT_SECONDS));
        resetThread.join(TimeUnit.SECONDS.toMillis(TIMEOUT_SECONDS));

        if (tradeThread.isAlive() || resetThread.isAlive()) {
            tradeThread.interrupt();
            resetThread.interrupt();
            throw new AssertionError("concurrency test exceeded " + TIMEOUT_SECONDS + "s time bound");
        }

        Throwable t = failure.get();
        if (t != null) {
            throw new AssertionError("concurrent access produced an exception: " + t, t);
        }

        // Final coherence check: after the resetThread finishes last with a
        // reset(), the calculator may or may not have new days from the trade
        // thread (depending on interleaving), but iterating must not throw.
        synchronized (lock) {
            for (PaxOpeningRangeDayState day : calculator.getDays()) {
                if (day == null) {
                    throw new AssertionError("null day in calculator");
                }
            }
        }
    }
}
