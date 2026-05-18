package com.bookmapmcp.state;

import java.util.Collections;
import java.util.List;

public record BalanceSnapshot(String accountName, List<BalanceCurrency> currencies) {
    public static final BalanceSnapshot EMPTY = new BalanceSnapshot("", Collections.emptyList());
}
