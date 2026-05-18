package com.bookmapmcp;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.time.Instant;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import com.bookmapmcp.state.InstrumentState;

class BridgeRegistryTest {

    @BeforeEach
    @AfterEach
    void reset() {
        BridgeRegistry.INSTANCE.clearForTests();
    }

    @Test
    void attachAddsInstrument() {
        BridgeRegistry.INSTANCE.attach(new InstrumentState(
                "ES-CME", "ES", "E-mini S&P", 0.25, 50, Instant.now()));
        assertEquals(1, BridgeRegistry.INSTANCE.size());
        assertNotNull(BridgeRegistry.INSTANCE.get("ES-CME"));
        assertEquals("ES", BridgeRegistry.INSTANCE.get("ES-CME").symbol());
    }

    @Test
    void attachReplacesByAlias() {
        BridgeRegistry.INSTANCE.attach(new InstrumentState(
                "ES-CME", "ES", "old", 0.25, 50, Instant.now()));
        BridgeRegistry.INSTANCE.attach(new InstrumentState(
                "ES-CME", "ES", "new", 0.25, 50, Instant.now()));
        assertEquals(1, BridgeRegistry.INSTANCE.size());
        assertEquals("new", BridgeRegistry.INSTANCE.get("ES-CME").fullName());
    }

    @Test
    void detachRemoves() {
        BridgeRegistry.INSTANCE.attach(new InstrumentState(
                "ES-CME", "ES", "", 0.25, 50, Instant.now()));
        BridgeRegistry.INSTANCE.attach(new InstrumentState(
                "NQ-CME", "NQ", "", 0.25, 20, Instant.now()));
        BridgeRegistry.INSTANCE.detach("ES-CME");
        assertEquals(1, BridgeRegistry.INSTANCE.size());
        assertNull(BridgeRegistry.INSTANCE.get("ES-CME"));
        assertNotNull(BridgeRegistry.INSTANCE.get("NQ-CME"));
    }

    @Test
    void identityAwareDetachRemovesMatching() {
        InstrumentState s = new InstrumentState(
                "ES-CME", "ES", "", 0.25, 50, Instant.now());
        BridgeRegistry.INSTANCE.attach(s);
        assertTrue(BridgeRegistry.INSTANCE.detach("ES-CME", s));
        assertNull(BridgeRegistry.INSTANCE.get("ES-CME"));
    }

    @Test
    void identityAwareDetachKeepsNewerStateAfterReattach() {
        InstrumentState oldState = new InstrumentState(
                "ES-CME", "ES", "old", 0.25, 50, Instant.now());
        BridgeRegistry.INSTANCE.attach(oldState);
        // Simulate re-attach: a new module came up with a fresh state.
        InstrumentState newState = new InstrumentState(
                "ES-CME", "ES", "new", 0.25, 50, Instant.now());
        BridgeRegistry.INSTANCE.attach(newState);
        // Old module's stop() races in afterwards with its stale snapshot.
        assertFalse(BridgeRegistry.INSTANCE.detach("ES-CME", oldState));
        // The newer entry must survive the stale detach.
        assertEquals(1, BridgeRegistry.INSTANCE.size());
        assertSame(newState, BridgeRegistry.INSTANCE.get("ES-CME"));
    }

    @Test
    void identityAwareDetachIgnoresNulls() {
        InstrumentState s = new InstrumentState(
                "ES-CME", "ES", "", 0.25, 50, Instant.now());
        BridgeRegistry.INSTANCE.attach(s);
        assertFalse(BridgeRegistry.INSTANCE.detach(null, s));
        assertFalse(BridgeRegistry.INSTANCE.detach("ES-CME", null));
        assertNotNull(BridgeRegistry.INSTANCE.get("ES-CME"));
    }
}
