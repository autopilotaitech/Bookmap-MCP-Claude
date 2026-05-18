package com.bookmapmcp;

import java.util.Collection;
import java.util.Collections;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;

import com.bookmapmcp.state.InstrumentState;
import com.bookmapmcp.state.TradeJournal;

public final class BridgeRegistry {

    public static final BridgeRegistry INSTANCE = new BridgeRegistry();

    private final ConcurrentMap<String, InstrumentState> byAlias = new ConcurrentHashMap<>();
    private volatile TradeJournal journal;

    private BridgeRegistry() {}

    public synchronized TradeJournal ensureJournal(String defaultDir) {
        if (journal == null) {
            journal = TradeJournal.fromEnvOrConfig(defaultDir);
        }
        return journal;
    }

    public TradeJournal journal() { return journal; }

    public void attach(InstrumentState state) { byAlias.put(state.alias(), state); }
    public void detach(String alias) { byAlias.remove(alias); }

    /**
     * Identity-aware detach. Only removes the alias->state mapping if the
     * currently-registered state is the same instance as {@code expected}.
     * Used by module shutdown so a stop() running after a re-attach cycle
     * cannot evict the newer live state. Returns true iff the entry was
     * actually removed.
     */
    public boolean detach(String alias, InstrumentState expected) {
        if (alias == null || expected == null) return false;
        return byAlias.remove(alias, expected);
    }

    public InstrumentState get(String alias) { return byAlias.get(alias); }
    public Collection<InstrumentState> all() { return Collections.unmodifiableCollection(byAlias.values()); }
    public int size() { return byAlias.size(); }
    void clearForTests() { byAlias.clear(); }
}
