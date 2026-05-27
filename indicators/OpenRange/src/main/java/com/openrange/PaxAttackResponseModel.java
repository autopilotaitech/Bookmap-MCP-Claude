package com.openrange;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * Immutable carrier for one /api/pax/attack-response payload.
 *
 * <p>Stage 7 of reports/pax-ai-attack-response-plan-2026-05-27.md. The
 * Python side (pax_ai.attack_response) emits a closed vocabulary of
 * states and biases; this model + the parser are defensive about the
 * shape (parser DEFAULTS reject unknown enums) so a partial payload
 * cannot accidentally render.</p>
 *
 * <p>Hard contract: when {@code health != ok} or {@code blocked.anchor}
 * is true, the parser returns an empty {@code rows} list. The painter
 * draws NOTHING in those cases - this is enforced one extra time inside
 * the painter as defense in depth.</p>
 */
final class PaxAttackResponseModel {

    /** Closed bias vocabulary. */
    enum Bias {
        BULL_WATCH, BEAR_WATCH, NEUTRAL;

        static Bias of(String s) {
            if (s == null) return NEUTRAL;
            switch (s) {
                case "BULL_WATCH": return BULL_WATCH;
                case "BEAR_WATCH": return BEAR_WATCH;
                case "NEUTRAL":    return NEUTRAL;
                default:           return NEUTRAL;
            }
        }
    }

    /** Closed state vocabulary. Anything else parses to UNKNOWN, which
     *  the painter treats as draw-nothing. */
    enum State {
        OR_L_SWEEP_RECLAIM, OR_H_SWEEP_FAIL,
        OR_L_ABSORB_HOLD,   OR_H_ABSORB_HOLD,
        OR_L_BREAK_ACCEPT,  OR_H_BREAK_ACCEPT,
        EXT_LOW_EXHAUST,    EXT_HIGH_EXHAUST,
        NO_EDGE,
        UNKNOWN;

        static State of(String s) {
            if (s == null) return UNKNOWN;
            switch (s) {
                case "OR_L_SWEEP_RECLAIM": return OR_L_SWEEP_RECLAIM;
                case "OR_H_SWEEP_FAIL":    return OR_H_SWEEP_FAIL;
                case "OR_L_ABSORB_HOLD":   return OR_L_ABSORB_HOLD;
                case "OR_H_ABSORB_HOLD":   return OR_H_ABSORB_HOLD;
                case "OR_L_BREAK_ACCEPT":  return OR_L_BREAK_ACCEPT;
                case "OR_H_BREAK_ACCEPT":  return OR_H_BREAK_ACCEPT;
                case "EXT_LOW_EXHAUST":    return EXT_LOW_EXHAUST;
                case "EXT_HIGH_EXHAUST":   return EXT_HIGH_EXHAUST;
                case "NO_EDGE":            return NO_EDGE;
                default:                    return UNKNOWN;
            }
        }
    }

    static final class Blocked {
        final boolean health;
        final boolean stale;
        final boolean anchor;

        Blocked(boolean health, boolean stale, boolean anchor) {
            this.health = health;
            this.stale = stale;
            this.anchor = anchor;
        }

        boolean any() {
            return health || stale || anchor;
        }

        static final Blocked NONE = new Blocked(false, false, false);
        static final Blocked ALL  = new Blocked(true, true, true);
    }

    static final class Row {
        final String id;
        final String location;
        final double levelPrice;
        final State state;
        final Bias bias;
        final String attack;
        final String response;
        final List<String> drivers;
        final double confidence;
        final boolean provenEdge;
        final long timestampMs;

        Row(String id, String location, double levelPrice, State state,
                Bias bias, String attack, String response, List<String> drivers,
                double confidence, boolean provenEdge, long timestampMs) {
            this.id = id == null ? "" : id;
            this.location = location == null ? "" : location;
            this.levelPrice = levelPrice;
            this.state = state == null ? State.UNKNOWN : state;
            this.bias = bias == null ? Bias.NEUTRAL : bias;
            this.attack = attack == null ? "" : attack;
            this.response = response == null ? "" : response;
            this.drivers = drivers == null ? Collections.<String>emptyList()
                    : Collections.unmodifiableList(new ArrayList<>(drivers));
            this.confidence = confidence;
            this.provenEdge = provenEdge;
            this.timestampMs = timestampMs;
        }

        /** Sweep-style compact driver tokens used in the small chart
         *  label, e.g. SL+BI+BS. Filters drivers to the recognized
         *  attack-response vocabulary. */
        String compactDriverTags() {
            if (drivers.isEmpty()) return "";
            Set<String> out = new LinkedHashSet<>();
            for (String d : drivers) {
                if (d == null) continue;
                String tag = driverTag(d);
                if (tag != null) out.add(tag);
            }
            if (out.isEmpty()) return "";
            StringBuilder sb = new StringBuilder();
            boolean first = true;
            for (String t : out) {
                if (!first) sb.append('+');
                sb.append(t);
                first = false;
            }
            return sb.toString();
        }

        private static String driverTag(String d) {
            switch (d) {
                case "sweep_low":    return "SL";
                case "sweep_high":   return "SH";
                case "bid_iceberg":  return "BI";
                case "ask_iceberg":  return "AI";
                case "bid_absorb":   return "BA";
                case "ask_absorb":   return "AA";
                case "bid_stack":    return "BS";
                case "ask_stack":    return "AS";
                case "bid_pull":     return "BP";
                case "ask_pull":     return "AP";
                case "buy_tape":     return "BT";
                case "sell_tape":    return "ST";
                case "break_up":     return "BU";
                case "break_down":   return "BD";
                case "touch":        return "T";
                default:              return null;
            }
        }

        boolean isRenderable() {
            return !id.isEmpty()
                    && Double.isFinite(levelPrice) && levelPrice > 0.0
                    && timestampMs > 0L
                    && state != State.UNKNOWN
                    && state != State.NO_EDGE
                    && bias != Bias.NEUTRAL;
        }
    }

    final String alias;
    final long asOfMs;
    final String health;
    final Blocked blocked;
    final List<Row> rows;
    final long fetchedAtMs;

    PaxAttackResponseModel(String alias, long asOfMs, String health,
            Blocked blocked, List<Row> rows, long fetchedAtMs) {
        this.alias = alias == null ? "" : alias;
        this.asOfMs = asOfMs;
        this.health = health == null ? "" : health;
        this.blocked = blocked == null ? Blocked.NONE : blocked;
        this.rows = rows == null ? Collections.<Row>emptyList()
                : Collections.unmodifiableList(new ArrayList<>(rows));
        this.fetchedAtMs = fetchedAtMs;
    }

    static PaxAttackResponseModel empty(long fetchedAtMs) {
        return new PaxAttackResponseModel("", 0L, "ok", Blocked.NONE,
                Collections.<Row>emptyList(), fetchedAtMs);
    }

    static PaxAttackResponseModel blocked(String reason, long fetchedAtMs) {
        Blocked b = "stale".equals(reason)
                ? new Blocked(false, true, false)
                : ("anchor".equals(reason)
                        ? new Blocked(false, false, true)
                        : new Blocked(true, false, false));
        return new PaxAttackResponseModel("", 0L,
                "stale".equals(reason) ? "stale"
                        : ("anchor".equals(reason) ? "ok" : "offline"),
                b, Collections.<Row>emptyList(), fetchedAtMs);
    }
}
