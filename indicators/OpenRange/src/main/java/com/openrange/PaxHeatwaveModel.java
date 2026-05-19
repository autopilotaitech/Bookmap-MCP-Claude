package com.openrange;

final class PaxHeatwaveModel {

    enum Tone { BULL, BEAR, AMBER, NEUTRAL }

    enum AgeState { FRESH, WARN, STALE, NO_DATA }

    /**
     * Discriminates how the chart overlay should render this model.
     *
     * <ul>
     *   <li>{@link #LIVE} — dashboard returned health=ok; row data is real.</li>
     *   <li>{@link #NO_DATA} — fetcher has never had a successful response
     *       (cold start). Default state of {@link #noData(long)}.</li>
     *   <li>{@link #BRIDGE_OFFLINE} — dashboard reachable but the Bookmap
     *       bridge addon is unreachable (dashboard returned health=offline).
     *       This is the most common failure: Bookmap not running, bridge
     *       addon not attached, or stale jar wins the classloader race.</li>
     *   <li>{@link #DASHBOARD_OFFLINE} — fetcher can't even reach
     *       /api/snapshot. Dashboard process isn't running on
     *       http://127.0.0.1:18888, or something else holds the port.</li>
     * </ul>
     */
    enum State { LIVE, NO_DATA, BRIDGE_OFFLINE, DASHBOARD_OFFLINE }

    static final class Row {
        final String label;
        final String scoreText;
        final Tone tone;
        final String hint;

        Row(String label, String scoreText, Tone tone, String hint) {
            this.label = label == null ? "" : label;
            this.scoreText = scoreText == null ? "--" : scoreText;
            this.tone = tone == null ? Tone.NEUTRAL : tone;
            this.hint = hint == null ? "" : hint;
        }
    }

    static final String[] LABELS = new String[] {
            "OR", "FLOW", "OFI", "CVD", "ABSORB", "VWAP", "VP", "PS", "TAPE", "BOOK", "MICRO"
    };
    static final int ROW_COUNT = LABELS.length;

    final String verdict;
    final Tone verdictTone;
    final String scoreText;
    final Row[] rows;
    final long fetchedAtMs;
    final boolean ok;
    final State state;
    /** For OFFLINE states only — URL the dashboard/bridge was probed against. */
    final String offlineUrl;
    /** For OFFLINE states only — short single-line reason. Never contains the token. */
    final String offlineReason;

    PaxHeatwaveModel(String verdict, Tone verdictTone, String scoreText, Row[] rows, long fetchedAtMs, boolean ok) {
        this(verdict, verdictTone, scoreText, rows, fetchedAtMs, ok,
             ok ? State.LIVE : State.NO_DATA, "", "");
    }

    PaxHeatwaveModel(String verdict, Tone verdictTone, String scoreText, Row[] rows,
            long fetchedAtMs, boolean ok, State state, String offlineUrl, String offlineReason) {
        this.verdict = verdict == null ? "WAIT" : verdict;
        this.verdictTone = verdictTone == null ? Tone.NEUTRAL : verdictTone;
        this.scoreText = scoreText == null ? "--" : scoreText;
        this.rows = rows;
        this.fetchedAtMs = fetchedAtMs;
        this.ok = ok;
        this.state = state == null ? (ok ? State.LIVE : State.NO_DATA) : state;
        this.offlineUrl = offlineUrl == null ? "" : offlineUrl;
        this.offlineReason = offlineReason == null ? "" : offlineReason;
    }

    static PaxHeatwaveModel noData(long nowMs) {
        Row[] rows = new Row[ROW_COUNT];
        for (int i = 0; i < ROW_COUNT; i++) {
            rows[i] = new Row(LABELS[i], "--", Tone.NEUTRAL, "");
        }
        return new PaxHeatwaveModel("NO DATA", Tone.AMBER, "--", rows, nowMs, false,
                State.NO_DATA, "", "");
    }

    /** Bridge addon unreachable; dashboard reachable. Operator sees "BRIDGE OFFLINE". */
    static PaxHeatwaveModel bridgeOffline(long fetchedAtMs, String bridgeUrl, String reason) {
        Row[] rows = offlineRows("BRIDGE OFFLINE", reason);
        return new PaxHeatwaveModel("BRIDGE OFFLINE", Tone.AMBER, "--", rows, fetchedAtMs, false,
                State.BRIDGE_OFFLINE, bridgeUrl == null ? "" : bridgeUrl,
                reason == null ? "" : reason);
    }

    /** Dashboard HTTP endpoint unreachable from OpenRange. Operator sees "DASHBOARD OFFLINE". */
    static PaxHeatwaveModel dashboardOffline(long fetchedAtMs, String dashboardUrl, String reason) {
        Row[] rows = offlineRows("DASHBOARD OFFLINE", reason);
        return new PaxHeatwaveModel("DASHBOARD OFFLINE", Tone.AMBER, "--", rows, fetchedAtMs, false,
                State.DASHBOARD_OFFLINE, dashboardUrl == null ? "" : dashboardUrl,
                reason == null ? "" : reason);
    }

    /** Row content for offline overlays — first two rows carry the URL + reason. */
    private static Row[] offlineRows(String state, String reason) {
        Row[] rows = new Row[ROW_COUNT];
        rows[0] = new Row("STATE",  state, Tone.AMBER, "");
        rows[1] = new Row("REASON", "--",  Tone.NEUTRAL,
                reason == null || reason.isEmpty() ? "no detail" : reason);
        for (int i = 2; i < ROW_COUNT; i++) {
            rows[i] = new Row(LABELS[i], "--", Tone.NEUTRAL, "");
        }
        return rows;
    }

    AgeState ageState(long nowMs, long warnMs, long staleMs) {
        if (!ok) {
            return AgeState.NO_DATA;
        }
        long age = Math.max(0L, nowMs - fetchedAtMs);
        if (age >= staleMs) {
            return AgeState.STALE;
        }
        if (age >= warnMs) {
            return AgeState.WARN;
        }
        return AgeState.FRESH;
    }

    String ageText(long nowMs, long staleMs) {
        if (!ok) {
            // Offline states show their own status word in the header — the
            // age slot reflects the disposition rather than a duration.
            switch (state) {
                case BRIDGE_OFFLINE:    return "BRIDGE OFF";
                case DASHBOARD_OFFLINE: return "DASH OFF";
                default:                return "NO DATA";
            }
        }
        long age = Math.max(0L, nowMs - fetchedAtMs);
        if (age >= staleMs) {
            return "STALE";
        }
        long secs = age / 1000L;
        if (secs <= 0) {
            return "0s";
        }
        if (secs < 60) {
            return secs + "s";
        }
        if (secs < 3600) {
            return (secs / 60) + "m";
        }
        return (secs / 3600) + "h";
    }
}
