package com.openrange;

import java.awt.Color;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;

import velox.api.layer1.annotations.Layer1ApiVersion;
import velox.api.layer1.annotations.Layer1ApiVersionValue;
import velox.api.layer1.annotations.Layer1SimpleAttachable;
import velox.api.layer1.annotations.Layer1StrategyName;
import velox.api.layer1.data.InstrumentInfo;
import velox.api.layer1.data.TradeInfo;
import velox.api.layer1.messages.indicators.Layer1ApiUserMessageModifyIndicator.GraphType;
import velox.api.layer1.simplified.Api;
import velox.api.layer1.simplified.CustomModule;
import velox.api.layer1.simplified.Indicator;
import velox.api.layer1.simplified.InitialState;
import velox.api.layer1.simplified.TimeListener;
import velox.api.layer1.simplified.TradeDataListener;

@Layer1SimpleAttachable
@Layer1StrategyName("OpenRange simple diagnostic")
@Layer1ApiVersion(Layer1ApiVersionValue.VERSION2)
public class PaxOpeningRangeSimpleModule implements CustomModule, TradeDataListener, TimeListener {
    private static final ZoneId EXCHANGE_ZONE = ZoneId.of("America/Chicago");

    private Indicator lastTrade;
    private Indicator orHigh;
    private Indicator orLow;
    private PaxOpeningRangeCalculator calculator;
    private long currentTime;

    @Override
    public void initialize(String alias, InstrumentInfo info, Api api, InitialState initialState) {
        double pips = info.pips <= 0 ? 1 : info.pips;
        calculator = new PaxOpeningRangeCalculator(PaxOpeningRangeSettings.defaults(), info.symbol, pips);

        lastTrade = api.registerIndicator("OpenRange diagnostic last trade", GraphType.PRIMARY);
        lastTrade.setColor(Color.WHITE);
        lastTrade.setWidth(1);

        orHigh = api.registerIndicator("OpenRange OR high", GraphType.PRIMARY);
        orHigh.setColor(new Color(0, 191, 255));
        orHigh.setWidth(3);

        orLow = api.registerIndicator("OpenRange OR low", GraphType.PRIMARY);
        orLow.setColor(new Color(255, 99, 71));
        orLow.setWidth(3);
    }

    @Override
    public void stop() {
    }

    @Override
    public void onTimestamp(long t) {
        currentTime = t;
    }

    @Override
    public void onTrade(double price, int size, TradeInfo tradeInfo) {
        lastTrade.addPoint(price);
        calculator.onTrade(toLocalDateTime(currentTime), price);
        PaxOpeningRangeDayState day = calculator.getDay(toLocalDateTime(currentTime).toLocalDate());
        if (day.isComplete()) {
            orHigh.addPoint(day.getHigh());
            orLow.addPoint(day.getLow());
        }
    }

    private static LocalDateTime toLocalDateTime(long nanos) {
        if (nanos <= 0) {
            return LocalDateTime.now(EXCHANGE_ZONE);
        }
        return LocalDateTime.ofInstant(Instant.ofEpochSecond(0, nanos), EXCHANGE_ZONE);
    }
}
