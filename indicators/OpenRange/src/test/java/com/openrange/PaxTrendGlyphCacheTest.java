package com.openrange;

import java.awt.image.BufferedImage;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

import velox.api.layer1.layers.strategies.interfaces.ScreenSpaceCanvas.PreparedImage;

public class PaxTrendGlyphCacheTest {

    public static void main(String[] args) {
        sameKeyReturnsSameInstance();
        differentKindAllocatesDistinctInstance();
        differentFontSizeAllocatesDistinctInstance();
        builderCalledOncePerKey();
        nullKindIsCacheable();
        capRespectsBoundedSize();
        threadSafetyUnderConcurrentLookup();
        System.out.println("PaxTrendGlyphCacheTest OK");
    }

    private static void sameKeyReturnsSameInstance() {
        AtomicInteger calls = new AtomicInteger(0);
        PaxTrendGlyphCache cache = new PaxTrendGlyphCache((kind, size) -> {
            calls.incrementAndGet();
            return placeholder();
        });
        PreparedImage a = cache.get(PaxTrendSignalModel.Kind.STRONG_BULL, 26);
        PreparedImage b = cache.get(PaxTrendSignalModel.Kind.STRONG_BULL, 26);
        if (a != b) {
            throw new AssertionError("cache must return the same PreparedImage for same (kind, size)");
        }
        if (calls.get() != 1) {
            throw new AssertionError("builder must fire exactly once per (kind, size); got " + calls.get());
        }
    }

    private static void differentKindAllocatesDistinctInstance() {
        AtomicInteger calls = new AtomicInteger(0);
        PaxTrendGlyphCache cache = new PaxTrendGlyphCache((kind, size) -> {
            calls.incrementAndGet();
            return placeholder();
        });
        PreparedImage bull = cache.get(PaxTrendSignalModel.Kind.STRONG_BULL, 26);
        PreparedImage bear = cache.get(PaxTrendSignalModel.Kind.STRONG_BEAR, 26);
        if (bull == bear) {
            throw new AssertionError("distinct kinds must produce distinct cache entries");
        }
        if (calls.get() != 2) {
            throw new AssertionError("builder must fire twice for two distinct kinds; got " + calls.get());
        }
    }

    private static void differentFontSizeAllocatesDistinctInstance() {
        AtomicInteger calls = new AtomicInteger(0);
        PaxTrendGlyphCache cache = new PaxTrendGlyphCache((kind, size) -> {
            calls.incrementAndGet();
            return placeholder();
        });
        PreparedImage strong = cache.get(PaxTrendSignalModel.Kind.STRONG_BULL, 26);
        PreparedImage weak   = cache.get(PaxTrendSignalModel.Kind.STRONG_BULL, 20);
        if (strong == weak) {
            throw new AssertionError("distinct font sizes must produce distinct cache entries");
        }
        if (calls.get() != 2) {
            throw new AssertionError("builder must fire twice for two distinct sizes; got " + calls.get());
        }
    }

    private static void builderCalledOncePerKey() {
        AtomicInteger calls = new AtomicInteger(0);
        PaxTrendGlyphCache cache = new PaxTrendGlyphCache((kind, size) -> {
            calls.incrementAndGet();
            return placeholder();
        });
        for (int i = 0; i < 100; i++) {
            cache.get(PaxTrendSignalModel.Kind.WEAK_BULL, 20);
        }
        if (calls.get() != 1) {
            throw new AssertionError("builder must amortize to a single call across hot path; got " + calls.get());
        }
    }

    private static void nullKindIsCacheable() {
        AtomicInteger calls = new AtomicInteger(0);
        PaxTrendGlyphCache cache = new PaxTrendGlyphCache((kind, size) -> {
            calls.incrementAndGet();
            return placeholder();
        });
        PreparedImage a = cache.get(null, 26);
        PreparedImage b = cache.get(null, 26);
        if (a != b) {
            throw new AssertionError("null kind should still be cacheable");
        }
        if (calls.get() != 1) {
            throw new AssertionError("null kind must amortize too; got " + calls.get());
        }
    }

    private static void capRespectsBoundedSize() {
        AtomicInteger calls = new AtomicInteger(0);
        PaxTrendGlyphCache cache = new PaxTrendGlyphCache((kind, size) -> {
            calls.incrementAndGet();
            return placeholder();
        });
        // Fill past MAX_ENTRIES.
        for (int i = 0; i < PaxTrendGlyphCache.MAX_ENTRIES + 4; i++) {
            cache.get(PaxTrendSignalModel.Kind.STRONG_BULL, 100 + i);
        }
        if (cache.size() > PaxTrendGlyphCache.MAX_ENTRIES) {
            throw new AssertionError("cache exceeded MAX_ENTRIES; size=" + cache.size());
        }
        // Beyond cap: builder fires on every miss but does NOT crash and
        // does NOT silently corrupt cache invariants.
        int callsAfterFill = calls.get();
        cache.get(PaxTrendSignalModel.Kind.STRONG_BULL, 999);
        if (calls.get() <= callsAfterFill) {
            throw new AssertionError("expected builder to fire on cache-full miss");
        }
    }

    private static void threadSafetyUnderConcurrentLookup() throws AssertionError {
        AtomicInteger calls = new AtomicInteger(0);
        PaxTrendGlyphCache cache = new PaxTrendGlyphCache((kind, size) -> {
            calls.incrementAndGet();
            try { Thread.sleep(2); } catch (InterruptedException ie) { /* ignore */ }
            return placeholder();
        });
        AtomicReference<Throwable> failure = new AtomicReference<>();
        int threads = 8;
        Thread[] workers = new Thread[threads];
        AtomicReference<PreparedImage> sample = new AtomicReference<>();
        for (int i = 0; i < threads; i++) {
            workers[i] = new Thread(() -> {
                try {
                    PreparedImage img = cache.get(PaxTrendSignalModel.Kind.STRONG_BULL, 26);
                    sample.compareAndSet(null, img);
                    if (sample.get() != img) {
                        throw new AssertionError("concurrent lookups returned different instances");
                    }
                } catch (Throwable t) {
                    failure.set(t);
                }
            }, "PaxTrendGlyphCacheTest-" + i);
            workers[i].start();
        }
        for (Thread w : workers) {
            try { w.join(); } catch (InterruptedException ie) { Thread.currentThread().interrupt(); }
        }
        if (failure.get() != null) {
            throw new AssertionError("concurrent failure", failure.get());
        }
        if (calls.get() < 1) {
            throw new AssertionError("expected at least one builder call");
        }
    }

    private static PreparedImage placeholder() {
        return new PreparedImage(new BufferedImage(1, 1, BufferedImage.TYPE_INT_ARGB));
    }
}
