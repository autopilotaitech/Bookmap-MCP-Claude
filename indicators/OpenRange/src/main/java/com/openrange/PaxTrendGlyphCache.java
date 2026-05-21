package com.openrange;

import java.util.concurrent.ConcurrentHashMap;

import velox.api.layer1.layers.strategies.interfaces.ScreenSpaceCanvas.PreparedImage;

/**
 * Thread-safe cache of trend-triangle glyph {@link PreparedImage}s keyed by
 * {@code (kind, fontSize)}. The glyphs are immutable - same kind + same font
 * size always renders the same pixels - so the painter has no reason to
 * re-allocate a BufferedImage + Graphics2D on every triangle redraw.
 *
 * <p>Bounded: at most {@code (kindCount x supportedFontSizes)} entries.
 * In practice OpenRange uses 4 renderable kinds x 2 font sizes = 8 entries,
 * but the cache caps at {@link #MAX_ENTRIES} as a hard guard against an
 * unbounded set of font sizes.</p>
 *
 * <p>The supplier interface lets tests inject a deterministic counting
 * builder without pulling in AWT.</p>
 */
final class PaxTrendGlyphCache {

    /** Hard upper bound - beyond this we stop caching and fall through to
     * the supplier on every call. Acts as a safety net; the natural set
     * is < 16. */
    static final int MAX_ENTRIES = 16;

    /** Pluggable image builder so tests can count invocations without AWT. */
    interface Builder {
        PreparedImage build(PaxTrendSignalModel.Kind kind, int fontSize);
    }

    private final Builder builder;
    private final ConcurrentHashMap<Long, PreparedImage> cache = new ConcurrentHashMap<>();

    PaxTrendGlyphCache(Builder builder) {
        if (builder == null) {
            throw new IllegalArgumentException("builder");
        }
        this.builder = builder;
    }

    /** Returns the cached {@link PreparedImage} for the given kind+size,
     *  building (and caching) it if absent. Returns the builder's image
     *  uncached when the cache is at capacity - that path is hit only by
     *  pathological font-size churn. */
    PreparedImage get(PaxTrendSignalModel.Kind kind, int fontSize) {
        long key = pack(kind, fontSize);
        PreparedImage cached = cache.get(key);
        if (cached != null) {
            return cached;
        }
        PreparedImage built = builder.build(kind, fontSize);
        if (cache.size() >= MAX_ENTRIES) {
            return built;
        }
        PreparedImage prior = cache.putIfAbsent(key, built);
        return prior != null ? prior : built;
    }

    int size() {
        return cache.size();
    }

    void clear() {
        cache.clear();
    }

    private static long pack(PaxTrendSignalModel.Kind kind, int fontSize) {
        int kindOrdinal = kind == null ? -1 : kind.ordinal();
        return ((long) (kindOrdinal & 0xFFFF) << 32) | (fontSize & 0xFFFFFFFFL);
    }
}
