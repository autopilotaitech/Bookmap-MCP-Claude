package com.bookmapmcp;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.security.SecureRandom;
import java.util.HexFormat;
import java.util.Properties;

/**
 * Loads or creates the bridge config (port + shared secret).
 *
 * <p>Default location: {@code ~/.bookmap-mcp/bridge.properties}. Override with
 * the {@code BOOKMAP_MCP_CONFIG} system property or env var. On first run, a
 * random 32-byte token is generated and written to the file. The token is
 * shared with the Python MCP server via the same file (or env var).
 */
public final class BridgeConfig {

    public static final int DEFAULT_PORT = 8765;

    private final int port;
    private final String token;
    private final Path source;

    private BridgeConfig(int port, String token, Path source) {
        this.port = port;
        this.token = token;
        this.source = source;
    }

    public int port() { return port; }
    public String token() { return token; }
    public Path source() { return source; }

    public static BridgeConfig loadOrCreate() {
        Path configPath = resolveConfigPath();
        Properties props = new Properties();
        try {
            if (Files.exists(configPath)) {
                try (var in = Files.newBufferedReader(configPath)) {
                    props.load(in);
                }
            }
        } catch (IOException e) {
            throw new IllegalStateException("Failed to read MCP bridge config at " + configPath, e);
        }

        boolean dirty = false;
        String portStr = props.getProperty("port");
        int port;
        if (portStr == null || portStr.isBlank()) {
            port = DEFAULT_PORT;
            props.setProperty("port", Integer.toString(port));
            dirty = true;
        } else {
            try {
                port = Integer.parseInt(portStr.trim());
            } catch (NumberFormatException e) {
                throw new IllegalStateException("Invalid port in " + configPath + ": " + portStr, e);
            }
        }

        String token = props.getProperty("token");
        if (token == null || token.isBlank()) {
            token = generateToken();
            props.setProperty("token", token);
            dirty = true;
        }

        if (dirty) {
            try {
                Files.createDirectories(configPath.getParent());
                try (var out = Files.newBufferedWriter(configPath)) {
                    props.store(out, "Bookmap MCP Bridge — auto-generated. Do not commit.");
                }
            } catch (IOException e) {
                throw new IllegalStateException("Failed to write MCP bridge config to " + configPath, e);
            }
        }

        return new BridgeConfig(port, token, configPath);
    }

    private static Path resolveConfigPath() {
        String override = System.getProperty("BOOKMAP_MCP_CONFIG",
                System.getenv("BOOKMAP_MCP_CONFIG"));
        if (override != null && !override.isBlank()) {
            return Paths.get(override);
        }
        return Paths.get(System.getProperty("user.home"), ".bookmap-mcp", "bridge.properties");
    }

    private static String generateToken() {
        byte[] buf = new byte[32];
        new SecureRandom().nextBytes(buf);
        return HexFormat.of().formatHex(buf);
    }
}
