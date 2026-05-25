package com.openrange;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.LocalDateTime;
import java.util.Objects;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;

public class PaxOpeningRangeSignalCsvLogger implements AutoCloseable {
    private static final String HEADER = "time,symbol,price,orHigh,orLow,cvdDelta,bidDepthDelta,askDepthDelta,"
            + "netDepthDelta,cvdZ,psZ,cvdPercentile,psPercentile,location,distanceTicks,rangeWidth,rangeQuality,"
            + "ageSeconds,bias,action,confidence,score,maxScore,evidence,reason";
    private static final String SHUTDOWN_SENTINEL = " __SHUTDOWN__ ";
    private static final long FLUSH_INTERVAL_MS = 250;
    private static final int QUEUE_CAPACITY = 8192;

    private final Path file;
    private final LinkedBlockingQueue<String> queue = new LinkedBlockingQueue<>(QUEUE_CAPACITY);
    private final Thread writerThread;
    private final Object dedupLock = new Object();
    private String lastKey = "";
    private volatile boolean closed;

    public PaxOpeningRangeSignalCsvLogger(Path file) {
        this.file = Objects.requireNonNull(file, "file");
        this.writerThread = new Thread(this::drainLoop, "OpenRange-CsvLogger-" + file.getFileName());
        this.writerThread.setDaemon(true);
        this.writerThread.start();
    }

    public Path file() {
        return file;
    }

    public void logIfChanged(String symbol, LocalDateTime time, double orHigh, double orLow,
            PaxOpeningRangeMarketState market, PaxOpeningRangeSignal signal) {
        if (closed || market == null || signal == null || Double.isNaN(market.lastPrice())) {
            return;
        }

        String sessionDate = time == null ? "" : time.toLocalDate().toString();
        String key = symbol + "|" + sessionDate + "|" + orHigh + "|" + orLow
                + "|" + signal.bias() + "|" + signal.action() + "|" + signal.confidence()
                + "|" + signal.score() + "|" + signal.evidence();
        synchronized (dedupLock) {
            if (key.equals(lastKey)) {
                return;
            }
            lastKey = key;
        }

        queue.offer(row(symbol, time, orHigh, orLow, market, signal));
    }

    @Override
    public void close() {
        if (closed) {
            return;
        }
        closed = true;
        queue.offer(SHUTDOWN_SENTINEL);
        try {
            writerThread.join(TimeUnit.SECONDS.toMillis(5));
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private void drainLoop() {
        try {
            ensureHeader();
        } catch (IOException e) {
            throw new IllegalStateException("Unable to initialize OpenRange signal log: " + file, e);
        }
        try (BufferedWriter writer = Files.newBufferedWriter(file, StandardCharsets.UTF_8,
                StandardOpenOption.CREATE, StandardOpenOption.APPEND)) {
            long lastFlushNanos = System.nanoTime();
            while (true) {
                String row = queue.poll(FLUSH_INTERVAL_MS, TimeUnit.MILLISECONDS);
                if (row == SHUTDOWN_SENTINEL) {
                    drainRemaining(writer);
                    writer.flush();
                    return;
                }
                if (row != null) {
                    writer.write(row);
                    writer.write(System.lineSeparator());
                }
                long now = System.nanoTime();
                if (row == null || now - lastFlushNanos >= TimeUnit.MILLISECONDS.toNanos(FLUSH_INTERVAL_MS)) {
                    writer.flush();
                    lastFlushNanos = now;
                }
            }
        } catch (IOException e) {
            throw new IllegalStateException("Unable to write OpenRange signal log: " + file, e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private void drainRemaining(BufferedWriter writer) throws IOException {
        for (String row; (row = queue.poll()) != null;) {
            if (row == SHUTDOWN_SENTINEL) {
                continue;
            }
            writer.write(row);
            writer.write(System.lineSeparator());
        }
    }

    private void ensureHeader() throws IOException {
        Path parent = file.getParent();
        if (parent != null) {
            Files.createDirectories(parent);
        }
        if (!Files.exists(file) || Files.size(file) == 0) {
            Files.writeString(file, HEADER + System.lineSeparator(), StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE, StandardOpenOption.APPEND);
        }
    }

    private String row(String symbol, LocalDateTime time, double orHigh, double orLow,
            PaxOpeningRangeMarketState market, PaxOpeningRangeSignal signal) {
        return time + "," + safeSymbol(symbol) + "," + market.lastPrice() + "," + orHigh + "," + orLow + ","
                + market.cvdDelta() + "," + market.bidDepthDelta() + "," + market.askDepthDelta() + ","
                + market.netDepthDelta() + "," + signal.cvdZScore() + "," + signal.pullingStackingZScore() + ","
                + signal.cvdPercentile() + "," + signal.pullingStackingPercentile() + ","
                + csv(signal.location()) + "," + signal.distanceTicks() + "," + signal.rangeWidth() + ","
                + csv(signal.rangeQuality()) + ","
                + signal.ageSeconds() + ","
                + signal.bias() + "," + signal.action() + ","
                + signal.confidence() + "," + signal.score() + "," + signal.maxScore() + ","
                + csv(signal.evidence()) + "," + csv(signal.reason());
    }

    private static String csv(String value) {
        String safe = value == null ? "" : value;
        return "\"" + safe.replace("\"", "\"\"") + "\"";
    }

    private static String safeSymbol(String value) {
        return value == null ? "" : value.replace(",", "");
    }
}
