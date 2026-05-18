package com.openrange;

public class PaxOpeningRangeSignalEngine {
    private final PaxOpeningRangeSignalSettings settings;
    private final double tickSize;

    public PaxOpeningRangeSignalEngine() {
        this(PaxOpeningRangeSignalSettings.defaults(), 0.25);
    }

    public PaxOpeningRangeSignalEngine(PaxOpeningRangeSignalSettings settings, double tickSize) {
        this.settings = settings == null ? PaxOpeningRangeSignalSettings.defaults() : settings;
        this.tickSize = tickSize <= 0 ? 0.25 : tickSize;
    }

    public PaxOpeningRangeSignal evaluate(PaxOpeningRangeDayState day, PaxOpeningRangeMarketState market) {
        if (day == null || !day.isComplete()) {
            return PaxOpeningRangeSignal.waitSignal("Opening range is not complete.");
        }
        if (market == null || Double.isNaN(market.lastPrice())) {
            return PaxOpeningRangeSignal.waitSignal("Waiting for live market data.");
        }

        if (market.lastPrice() > day.getHigh()) {
            int distanceTicks = ticks(market.lastPrice() - day.getHigh());
            double rangeWidth = day.getHigh() - day.getLow();
            long ageSeconds = ageSeconds(day, market);
            PaxOpeningRangeSignal distanceSignal = breakoutDistanceSignal("ORH", distanceTicks, rangeWidth, ageSeconds, market);
            if (distanceSignal != null) {
                return distanceSignal;
            }
            return directionalSignal(PaxOpeningRangeSignalBias.LONG, market,
                    "Price is above ORH", longScore(market), "ORH", distanceTicks, rangeWidth, ageSeconds);
        }
        if (market.lastPrice() < day.getLow()) {
            int distanceTicks = -ticks(day.getLow() - market.lastPrice());
            double rangeWidth = day.getHigh() - day.getLow();
            long ageSeconds = ageSeconds(day, market);
            PaxOpeningRangeSignal distanceSignal = breakoutDistanceSignal("ORL", Math.abs(distanceTicks), rangeWidth, ageSeconds, market);
            if (distanceSignal != null) {
                return distanceSignal;
            }
            return directionalSignal(PaxOpeningRangeSignalBias.SHORT, market,
                    "Price is below ORL", shortScore(market), "ORL", distanceTicks, rangeWidth, ageSeconds);
        }

        return new PaxOpeningRangeSignal(PaxOpeningRangeSignalAction.WAIT,
                PaxOpeningRangeSignalBias.NEUTRAL,
                PaxOpeningRangeSignalConfidence.NONE,
                "Price is inside opening range.",
                0,
                4,
                "",
                "INSIDE",
                0,
                day.getHigh() - day.getLow(),
                ageSeconds(day, market),
                market.cvdDelta(),
                market.netDepthDelta());
    }

    private PaxOpeningRangeSignal directionalSignal(PaxOpeningRangeSignalBias bias,
            PaxOpeningRangeMarketState market, String reasonLocation, int score, String location, int distanceTicks,
            double rangeWidth, long ageSeconds) {
        String evidence = bias == PaxOpeningRangeSignalBias.LONG ? longEvidence(market) : shortEvidence(market);
        if (score < settings.minScore()) {
            return new PaxOpeningRangeSignal(PaxOpeningRangeSignalAction.BLOCK_SIGNAL, bias,
                    PaxOpeningRangeSignalConfidence.LOW,
                    reasonLocation + " but confirmation needs " + settings.minScore() + "/4.",
                    score, 4, evidence, location, distanceTicks, rangeWidth, ageSeconds, market.cvdDelta(), market.netDepthDelta(),
                    0, 0);
        }
        if (score >= 3) {
            return new PaxOpeningRangeSignal(PaxOpeningRangeSignalAction.ALLOW_SIGNAL, bias,
                    PaxOpeningRangeSignalConfidence.HIGH, reasonLocation + " and order-flow confirmation is strong.",
                    score, 4, evidence, location, distanceTicks, rangeWidth, ageSeconds, market.cvdDelta(), market.netDepthDelta(),
                    0, 0);
        }
        if (score >= 2) {
            return new PaxOpeningRangeSignal(PaxOpeningRangeSignalAction.ALLOW_SIGNAL, bias,
                    PaxOpeningRangeSignalConfidence.MEDIUM, reasonLocation + " and order-flow confirmation is acceptable.",
                    score, 4, evidence, location, distanceTicks, rangeWidth, ageSeconds, market.cvdDelta(), market.netDepthDelta(),
                    0, 0);
        }
        return new PaxOpeningRangeSignal(PaxOpeningRangeSignalAction.BLOCK_SIGNAL, bias,
                PaxOpeningRangeSignalConfidence.LOW, reasonLocation + " but order-flow confirmation is weak.",
                score, 4, evidence, location, distanceTicks, rangeWidth, ageSeconds, market.cvdDelta(), market.netDepthDelta(),
                0, 0);
    }

    private int longScore(PaxOpeningRangeMarketState market) {
        int score = 0;
        if (market.cvdDelta() >= settings.minCvdConfirmation()) {
            score++;
        }
        if (market.bidDepthDelta() >= settings.minDepthConfirmation()) {
            score++;
        }
        if (market.askDepthDelta() <= -settings.minDepthConfirmation()) {
            score++;
        }
        if (market.netDepthDelta() >= settings.minDepthConfirmation()) {
            score++;
        }
        return score;
    }

    private int shortScore(PaxOpeningRangeMarketState market) {
        int score = 0;
        if (market.cvdDelta() <= -settings.minCvdConfirmation()) {
            score++;
        }
        if (market.bidDepthDelta() <= -settings.minDepthConfirmation()) {
            score++;
        }
        if (market.askDepthDelta() >= settings.minDepthConfirmation()) {
            score++;
        }
        if (market.netDepthDelta() <= -settings.minDepthConfirmation()) {
            score++;
        }
        return score;
    }

    private String longEvidence(PaxOpeningRangeMarketState market) {
        return String.join(" ",
                market.cvdDelta() >= settings.minCvdConfirmation() ? "CVD+" : "CVD-",
                market.bidDepthDelta() >= settings.minDepthConfirmation() ? "BID+" : "BID-",
                market.askDepthDelta() <= -settings.minDepthConfirmation() ? "ASK PULL" : "ASK STACK",
                market.netDepthDelta() >= settings.minDepthConfirmation() ? "NET+" : "NET-");
    }

    private String shortEvidence(PaxOpeningRangeMarketState market) {
        return String.join(" ",
                market.cvdDelta() <= -settings.minCvdConfirmation() ? "CVD-" : "CVD+",
                market.bidDepthDelta() <= -settings.minDepthConfirmation() ? "BID PULL" : "BID STACK",
                market.askDepthDelta() >= settings.minDepthConfirmation() ? "ASK+" : "ASK-",
                market.netDepthDelta() <= -settings.minDepthConfirmation() ? "NET-" : "NET+");
    }

    private PaxOpeningRangeSignal breakoutDistanceSignal(String location, int distanceTicks, double rangeWidth,
            long ageSeconds,
            PaxOpeningRangeMarketState market) {
        if (settings.minBreakoutTicks() > 0 && distanceTicks < settings.minBreakoutTicks()) {
            return new PaxOpeningRangeSignal(PaxOpeningRangeSignalAction.WAIT,
                    PaxOpeningRangeSignalBias.NEUTRAL,
                    PaxOpeningRangeSignalConfidence.NONE,
                    "Breakout is less than " + settings.minBreakoutTicks() + " ticks beyond opening range.",
                    0,
                    4,
                    "",
                    location,
                    signedDistance(location, distanceTicks),
                    rangeWidth,
                    ageSeconds,
                    market.cvdDelta(),
                    market.netDepthDelta());
        }
        if (settings.maxBreakoutTicks() > 0 && distanceTicks > settings.maxBreakoutTicks()) {
            return new PaxOpeningRangeSignal(PaxOpeningRangeSignalAction.BLOCK_SIGNAL,
                    PaxOpeningRangeSignalBias.NEUTRAL,
                    PaxOpeningRangeSignalConfidence.LOW,
                    "Breakout is more than " + settings.maxBreakoutTicks() + " ticks beyond opening range.",
                    0,
                    4,
                    "",
                    location,
                    signedDistance(location, distanceTicks),
                    rangeWidth,
                    ageSeconds,
                    market.cvdDelta(),
                    market.netDepthDelta());
        }
        return null;
    }

    private int ticks(double distance) {
        return Math.max(0, (int) Math.round(distance / tickSize));
    }

    private int signedDistance(String location, int distanceTicks) {
        return "ORL".equals(location) ? -distanceTicks : distanceTicks;
    }

    private long ageSeconds(PaxOpeningRangeDayState day, PaxOpeningRangeMarketState market) {
        return 0;
    }
}
