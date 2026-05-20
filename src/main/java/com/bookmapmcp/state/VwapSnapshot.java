package com.bookmapmcp.state;

/**
 * Session-anchored VWAP plus ±1σ/±2σ/±3σ extension bands.
 *
 * <p>Session anchor is the operator-configured OR start time (pushed from
 * the dashboard via POST /config; stored in
 * {@link InstrumentState#configSessionOpen()}). Accumulators reset whenever
 * a trade arrives in a new session window so playback or overnight restarts
 * get a clean slate.
 *
 * <p>If no trades have arrived this session, {@link #samples()} is 0 and
 * the band fields are {@code NaN}. Handlers should serialize NaN as null.
 */
public final class VwapSnapshot {

    public static final VwapSnapshot EMPTY = new VwapSnapshot(
            0L, 0L,
            Double.NaN, Double.NaN,
            Double.NaN, Double.NaN,
            Double.NaN, Double.NaN,
            Double.NaN, Double.NaN);

    private final long samples;
    private final long sessionStartMs;
    private final double vwap;
    private final double stddev;
    private final double upper1;
    private final double lower1;
    private final double upper2;
    private final double lower2;
    private final double upper3;
    private final double lower3;

    public VwapSnapshot(long samples, long sessionStartMs,
                        double vwap, double stddev,
                        double upper1, double lower1,
                        double upper2, double lower2,
                        double upper3, double lower3) {
        this.samples = samples;
        this.sessionStartMs = sessionStartMs;
        this.vwap = vwap;
        this.stddev = stddev;
        this.upper1 = upper1;
        this.lower1 = lower1;
        this.upper2 = upper2;
        this.lower2 = lower2;
        this.upper3 = upper3;
        this.lower3 = lower3;
    }

    public long samples()        { return samples; }
    public long sessionStartMs() { return sessionStartMs; }
    public double vwap()         { return vwap; }
    public double stddev()       { return stddev; }
    public double upper1()       { return upper1; }
    public double lower1()       { return lower1; }
    public double upper2()       { return upper2; }
    public double lower2()       { return lower2; }
    public double upper3()       { return upper3; }
    public double lower3()       { return lower3; }
}
