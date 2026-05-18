package com.bookmapmcp.state;

public record BalanceCurrency(
        String currency,
        double balance,
        double realizedPnl,
        double unrealizedPnl,
        double previousDayBalance,
        double netLiquidityValue,
        Double rateToBase
) {}
