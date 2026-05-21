package com.openrange;

/**
 * Gate for the native OR breakout marker publisher
 * ({@link PaxOpeningRangeModule}'s {@code publishNativeSignalMarkerIfNeeded}).
 *
 * <p>The native engine ({@link PaxOpeningRangeSignalEngine}) fires LONG/SHORT
 * markers from raw CVD + depth deltas with no acceptance/rejection /
 * iceberg / spoof awareness. Those markers are responsible for the
 * "buy in the middle of the OR" low-edge pattern that the institutional
 * signal pipeline replaces.</p>
 *
 * <p>When {@code gateNativeMarkersOnInstitutional} is on (default), this gate
 * suppresses native marker publishing entirely. Institutional markers from
 * {@code snap["institutional_signals"]} become the only buy/sell source on
 * the chart. Setting the flag off restores legacy behavior for debugging.</p>
 *
 * <p>Pure helper — no state, no side effects, no canvas. The module reads the
 * UI flag once per publish call.</p>
 */
final class PaxNativeMarkerGate {

    private PaxNativeMarkerGate() {}

    /** Should the module suppress native LONG/SHORT publishing this tick?
     *
     * @param gateOn the {@code gateNativeMarkersOnInstitutional} UI flag
     * @return true when the publish call must be skipped
     */
    static boolean shouldSuppress(boolean gateOn) {
        return gateOn;
    }
}
