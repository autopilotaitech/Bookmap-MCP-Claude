package com.bookmapmcp.state;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;
import java.util.logging.Level;
import java.util.logging.Logger;

/**
 * Append-only CSV trade journal — one row per {@code onExecution}, captures the
 * fill plus the surrounding microstructure context at fill time so the user
 * can postmortem every trade taken in playback or live.
 *
 * <p>One file per alias per ET date: {@code <dir>/mcp-journal-<sanitized-alias>-YYYY-MM-DD.csv}.
 * Lazily opened on first write. Header row written on file creation. Writes are
 * append-only, fsynced after each row so a crash doesn't lose the most recent fills.
 *
 * <p>Disable by leaving the journal directory unset; the journal is then a no-op.
 */
public final class TradeJournal {

    private static final Logger LOG = Logger.getLogger(TradeJournal.class.getName());
    private static final ZoneId ET = ZoneId.of("America/New_York");
    private static final DateTimeFormatter ISO_INSTANT = DateTimeFormatter.ISO_INSTANT;
    private static final DateTimeFormatter ET_DATE = DateTimeFormatter.ofPattern("yyyy-MM-dd").withZone(ET);

    private static final String HEADER = String.join(",",
            "timestamp_iso", "timestamp_ms", "alias", "side", "price", "size",
            "order_id", "simulated",
            "pos_before", "pos_after", "avg_after", "realized_pnl_delta", "realized_pnl_after",
            "vwap", "vwap_stddev", "sigma_dev",
            "mid", "microprice");

    private final Path dir;
    private final ConcurrentMap<String, Boolean> headerWritten = new ConcurrentHashMap<>();

    public TradeJournal(Path dir) {
        this.dir = dir;
    }

    /** Returns null if no directory is configured (journaling disabled). */
    public static TradeJournal fromEnvOrConfig(String configuredDir) {
        String dir = System.getProperty("bookmap.mcp.journal.dir",
                System.getenv("BOOKMAP_MCP_JOURNAL_DIR"));
        if ((dir == null || dir.isEmpty()) && configuredDir != null) dir = configuredDir;
        if (dir == null || dir.isEmpty()) return null;
        try {
            Path p = Paths.get(dir);
            Files.createDirectories(p);
            return new TradeJournal(p);
        } catch (IOException e) {
            LOG.log(Level.WARNING, "TradeJournal disabled: cannot create directory " + dir, e);
            return null;
        }
    }

    public synchronized void writeRow(JournalRow row) {
        try {
            Path file = pathFor(row.alias, row.timestampMs);
            String key = file.toString();
            if (headerWritten.putIfAbsent(key, Boolean.TRUE) == null && !Files.exists(file)) {
                Files.writeString(file, HEADER + System.lineSeparator(),
                        StandardCharsets.UTF_8,
                        StandardOpenOption.CREATE, StandardOpenOption.APPEND);
            } else if (!Files.exists(file)) {
                // Map says we already wrote, but file got moved/deleted — rewrite header.
                Files.writeString(file, HEADER + System.lineSeparator(),
                        StandardCharsets.UTF_8,
                        StandardOpenOption.CREATE, StandardOpenOption.APPEND);
            }
            Files.writeString(file, row.toCsvRow() + System.lineSeparator(),
                    StandardCharsets.UTF_8, StandardOpenOption.APPEND);
        } catch (IOException e) {
            LOG.log(Level.WARNING, "TradeJournal write failed", e);
        } catch (UncheckedIOException e) {
            LOG.log(Level.WARNING, "TradeJournal write failed (unchecked)", e);
        }
    }

    private Path pathFor(String alias, long timestampMs) {
        String safeAlias = alias.replaceAll("[^A-Za-z0-9._-]", "_");
        String date = ET_DATE.format(Instant.ofEpochMilli(timestampMs));
        return dir.resolve("mcp-journal-" + safeAlias + "-" + date + ".csv");
    }

    /** One immutable journal row. Built at fill time by InstrumentState. */
    public static final class JournalRow {
        public final long timestampMs;
        public final String alias;
        public final String side;
        public final double price;
        public final int size;
        public final String orderId;
        public final boolean simulated;
        public final int posBefore;
        public final int posAfter;
        public final double avgAfter;
        public final double realizedPnlDelta;
        public final double realizedPnlAfter;
        public final double vwap;
        public final double vwapStddev;
        public final double sigmaDev;
        public final double mid;
        public final double microprice;

        public JournalRow(long timestampMs, String alias, String side, double price, int size,
                          String orderId, boolean simulated,
                          int posBefore, int posAfter, double avgAfter,
                          double realizedPnlDelta, double realizedPnlAfter,
                          double vwap, double vwapStddev, double sigmaDev,
                          double mid, double microprice) {
            this.timestampMs = timestampMs;
            this.alias = alias;
            this.side = side;
            this.price = price;
            this.size = size;
            this.orderId = orderId == null ? "" : orderId;
            this.simulated = simulated;
            this.posBefore = posBefore;
            this.posAfter = posAfter;
            this.avgAfter = avgAfter;
            this.realizedPnlDelta = realizedPnlDelta;
            this.realizedPnlAfter = realizedPnlAfter;
            this.vwap = vwap;
            this.vwapStddev = vwapStddev;
            this.sigmaDev = sigmaDev;
            this.mid = mid;
            this.microprice = microprice;
        }

        String toCsvRow() {
            StringBuilder sb = new StringBuilder(192);
            sb.append(ISO_INSTANT.format(Instant.ofEpochMilli(timestampMs))).append(',');
            sb.append(timestampMs).append(',');
            sb.append(csvSafe(alias)).append(',');
            sb.append(csvSafe(side)).append(',');
            sb.append(num(price)).append(',');
            sb.append(size).append(',');
            sb.append(csvSafe(orderId)).append(',');
            sb.append(simulated).append(',');
            sb.append(posBefore).append(',');
            sb.append(posAfter).append(',');
            sb.append(num(avgAfter)).append(',');
            sb.append(num(realizedPnlDelta)).append(',');
            sb.append(num(realizedPnlAfter)).append(',');
            sb.append(num(vwap)).append(',');
            sb.append(num(vwapStddev)).append(',');
            sb.append(num(sigmaDev)).append(',');
            sb.append(num(mid)).append(',');
            sb.append(num(microprice));
            return sb.toString();
        }

        private static String num(double d) {
            if (!Double.isFinite(d)) return "";
            return String.format("%.6f", d);
        }
        private static String csvSafe(String s) {
            if (s == null) return "";
            if (s.indexOf(',') < 0 && s.indexOf('"') < 0 && s.indexOf('\n') < 0) return s;
            return "\"" + s.replace("\"", "\"\"") + "\"";
        }
    }
}
