package com.bookmapmcp;

import java.time.Instant;

import velox.api.layer1.annotations.Layer1ApiVersion;
import velox.api.layer1.annotations.Layer1ApiVersionValue;
import velox.api.layer1.annotations.Layer1SimpleAttachable;
import velox.api.layer1.annotations.Layer1StrategyName;
import velox.api.layer1.annotations.Layer1TradingStrategy;
import velox.api.layer1.data.BalanceInfo;
import velox.api.layer1.data.ExecutionInfo;
import velox.api.layer1.data.InstrumentInfo;
import velox.api.layer1.data.OrderInfoUpdate;
import velox.api.layer1.data.StatusInfo;
import velox.api.layer1.data.TradeInfo;
import velox.api.layer1.simplified.Api;
import velox.api.layer1.simplified.BalanceListener;
import velox.api.layer1.simplified.CustomModule;
import velox.api.layer1.simplified.DepthDataListener;
import velox.api.layer1.simplified.InitialState;
import velox.api.layer1.simplified.MarketByOrderDepthDataListener;
import velox.api.layer1.simplified.OrdersListener;
import velox.api.layer1.simplified.PositionListener;
import velox.api.layer1.simplified.TimeListener;
import velox.api.layer1.simplified.TradeDataListener;

import com.bookmapmcp.state.InstrumentState;

@Layer1SimpleAttachable
@Layer1StrategyName("MCP Bridge v2")
@Layer1ApiVersion(Layer1ApiVersionValue.VERSION2)
@Layer1TradingStrategy
public class BookmapMcpBridgeModule implements
        CustomModule,
        DepthDataListener,
        TradeDataListener,
        MarketByOrderDepthDataListener,
        OrdersListener,
        PositionListener,
        BalanceListener,
        TimeListener {

    private volatile String alias;
    private volatile InstrumentState state;

    @Override
    public void initialize(String alias, InstrumentInfo info, Api api, InitialState initialState) {
        this.alias = alias;
        this.state = new InstrumentState(
                alias, info.symbol, info.fullName,
                info.pips <= 0 ? 1 : info.pips,
                info.multiplier <= 0 ? 1 : info.multiplier,
                Instant.now());
        this.state.setApi(api);
        BridgeRegistry.INSTANCE.attach(state);
        BridgeRegistry.INSTANCE.ensureJournal("D:\\BookmapLogs");
        BridgeServer.ensureStarted();
    }

    @Override
    public void stop() {
        // Snapshot both alias AND state, then null our refs so any listener
        // callback already in flight sees null and short-circuits. The detach
        // is identity-aware: if another module already re-attached the same
        // alias with a newer InstrumentState, ConcurrentMap.remove(k,v) is a
        // no-op and the newer entry stays put.
        String a = this.alias;
        InstrumentState s = this.state;
        this.alias = null;
        this.state = null;
        if (a != null && s != null) BridgeRegistry.INSTANCE.detach(a, s);
    }

    @Override public void onTimestamp(long t) { if (state != null) state.onTimestamp(t); }
    @Override public void onDepth(boolean isBid, int price, int size) { if (state != null) state.onDepth(isBid, price, size); }
    @Override public void onTrade(double price, int size, TradeInfo tradeInfo) {
        if (state != null) state.onTrade(price, size, tradeInfo);
    }
    // MarketByOrderDepthDataListener — per-order book events. Used by spoof,
    // iceberg, and pull/stack detectors. Gracefully tolerated when the feed
    // doesn't supply MBO (degrades to depth-only inference).
    @Override public void send(String orderId, boolean isBid, int price, int size) {
        if (state != null) state.onMboSend(orderId, isBid, price, size);
    }
    @Override public void replace(String orderId, int price, int size) {
        if (state != null) state.onMboReplace(orderId, price, size);
    }
    @Override public void cancel(String orderId) {
        if (state != null) state.onMboCancel(orderId);
    }
    @Override public void onOrderUpdated(OrderInfoUpdate update) { if (state != null) state.onOrderUpdate(update); }
    @Override public void onOrderExecuted(ExecutionInfo executionInfo) { if (state != null) state.onExecution(executionInfo); }
    @Override public void onPositionUpdate(StatusInfo statusInfo) { if (state != null) state.onPositionUpdate(statusInfo); }
    @Override public void onBalance(BalanceInfo balanceInfo) { if (state != null) state.onBalance(balanceInfo); }
}
