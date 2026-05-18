package com.bookmapmcp;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Properties;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class BridgeConfigTest {

    @Test
    void createsFileWithPortAndTokenOnFirstRun(@TempDir Path tmp) throws Exception {
        Path cfg = tmp.resolve("bridge.properties");
        System.setProperty("BOOKMAP_MCP_CONFIG", cfg.toString());
        try {
            BridgeConfig loaded = BridgeConfig.loadOrCreate();
            assertEquals(BridgeConfig.DEFAULT_PORT, loaded.port());
            assertTrue(loaded.token().length() >= 32, "token should be a hex string");
            assertTrue(Files.exists(cfg));

            Properties props = new Properties();
            try (var in = Files.newBufferedReader(cfg)) {
                props.load(in);
            }
            assertEquals(loaded.token(), props.getProperty("token"));
        } finally {
            System.clearProperty("BOOKMAP_MCP_CONFIG");
        }
    }

    @Test
    void preservesTokenAcrossReloads(@TempDir Path tmp) throws Exception {
        Path cfg = tmp.resolve("bridge.properties");
        System.setProperty("BOOKMAP_MCP_CONFIG", cfg.toString());
        try {
            String first = BridgeConfig.loadOrCreate().token();
            String second = BridgeConfig.loadOrCreate().token();
            assertEquals(first, second);
            // sanity: a second call shouldn't generate a fresh token
            assertNotEquals(0, first.length());
        } finally {
            System.clearProperty("BOOKMAP_MCP_CONFIG");
        }
    }
}
