package com.bookmapmcp.trend;

public enum TrendDirection {
    UP(1),
    DOWN(-1),
    NEUTRAL(0);

    private final int sign;

    TrendDirection(int sign) {
        this.sign = sign;
    }

    public int sign() {
        return sign;
    }
}
