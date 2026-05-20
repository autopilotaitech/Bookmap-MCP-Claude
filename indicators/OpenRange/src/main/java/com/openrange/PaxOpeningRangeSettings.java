package com.openrange;

import java.time.LocalTime;
import java.time.LocalDate;
import java.time.LocalDateTime;

public class PaxOpeningRangeSettings {
    private final LocalTime rangeStart;
    private final int rangeSeconds;
    private final LocalTime lineEnd;
    private final int daysToDisplay;
    private final boolean showMid;
    private final String labelPrefix;

    public PaxOpeningRangeSettings(LocalTime rangeStart, int rangeSeconds, LocalTime lineEnd,
            int daysToDisplay, boolean showMid, String labelPrefix) {
        this.rangeStart = rangeStart;
        this.rangeSeconds = rangeSeconds;
        this.lineEnd = lineEnd;
        this.daysToDisplay = daysToDisplay;
        this.showMid = showMid;
        this.labelPrefix = labelPrefix;
    }

    public static PaxOpeningRangeSettings defaults() {
        return new PaxOpeningRangeSettings(LocalTime.of(9, 30), 30, LocalTime.of(17, 0), 8, false, "OpenRange");
    }

    public LocalTime rangeStart() {
        return rangeStart;
    }

    public int rangeSeconds() {
        return rangeSeconds;
    }

    public LocalTime rangeEnd() {
        return rangeStart.plusSeconds(rangeSeconds);
    }

    public LocalTime lineEnd() {
        return lineEnd;
    }

    public LocalDateTime rangeEndDateTime(LocalDate date) {
        return date.atTime(rangeEnd());
    }

    public LocalDateTime lineEndDateTime(LocalDate date) {
        // Institutional anchor is 08:30 CT (the OR start). lineEnd is the clock
        // time at which the OR overlay stops extending. When lineEnd is on or
        // before rangeEnd on the calendar day, the operator means "next session
        // start" — extend the line to the same clock time the next day. The
        // drawing path clamps this maxEnd against the day's lastUpdateTime so
        // today's line still ends at "now"; past days are bounded by the
        // calculator's lastUpdateTime maintenance in onInterval.
        LocalDateTime rangeEndDateTime = rangeEndDateTime(date);
        LocalDateTime lineEndDateTime = date.atTime(lineEnd);
        if (!lineEndDateTime.isAfter(rangeEndDateTime)) {
            return lineEndDateTime.plusDays(1);
        }
        return lineEndDateTime;
    }

    public int daysToDisplay() {
        return daysToDisplay;
    }

    public boolean showMid() {
        return showMid;
    }

    public String labelPrefix() {
        return labelPrefix;
    }
}
