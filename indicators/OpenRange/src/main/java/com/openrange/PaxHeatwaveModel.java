package com.openrange;

final class PaxHeatwaveModel {

    enum Tone { BULL, BEAR, AMBER, NEUTRAL }

    enum AgeState { FRESH, WARN, STALE, NO_DATA }

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

    PaxHeatwaveModel(String verdict, Tone verdictTone, String scoreText, Row[] rows, long fetchedAtMs, boolean ok) {
        this.verdict = verdict == null ? "WAIT" : verdict;
        this.verdictTone = verdictTone == null ? Tone.NEUTRAL : verdictTone;
        this.scoreText = scoreText == null ? "--" : scoreText;
        this.rows = rows;
        this.fetchedAtMs = fetchedAtMs;
        this.ok = ok;
    }

    static PaxHeatwaveModel noData(long nowMs) {
        Row[] rows = new Row[ROW_COUNT];
        for (int i = 0; i < ROW_COUNT; i++) {
            rows[i] = new Row(LABELS[i], "--", Tone.NEUTRAL, "");
        }
        return new PaxHeatwaveModel("NO DATA", Tone.AMBER, "--", rows, nowMs, false);
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
            return "NO DATA";
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
