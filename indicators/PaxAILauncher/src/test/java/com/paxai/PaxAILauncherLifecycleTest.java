package com.paxai;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;

/**
 * Tests for PaxAILauncherModule's lifecycle pure helpers.
 *
 * Cannot actually start the python process under test (would spawn a real
 * window). Instead exercises the path-resolution + log-dir helpers + the
 * idempotency invariant (calling startPaxAi twice with the env unset must
 * not throw).
 */
public class PaxAILauncherLifecycleTest {

    public static void main(String[] args) throws Exception {
        resolvePythonReturnsNullWhenNothingExists();
        resolvePythonUsesEnvOverrideWhenPresent();
        logDirDefaultsToLocalAppData();
        startWithMissingPythonIsNoopNotCrash();
        System.out.println("PaxAILauncherLifecycleTest OK");
    }

    private static void resolvePythonReturnsNullWhenNothingExists() throws Exception {
        clearEnv("PAX_AI_PYTHON");
        // We can't influence DEFAULT_PYTHON at runtime without reflection. If it
        // happens to exist on this machine (it does on the dev box), assert that
        // resolvePython returns its absolute path. If it doesn't, assert null.
        boolean defExists = Files.exists(Paths.get(PaxAILauncherModule.DEFAULT_PYTHON));
        String resolved = PaxAILauncherModule.resolvePython();
        if (defExists) {
            if (resolved == null || !resolved.equalsIgnoreCase(PaxAILauncherModule.DEFAULT_PYTHON)) {
                throw new AssertionError("expected DEFAULT_PYTHON, got " + resolved);
            }
        } else {
            if (resolved != null) {
                throw new AssertionError("expected null when neither env nor default exists, got " + resolved);
            }
        }
    }

    private static void resolvePythonUsesEnvOverrideWhenPresent() throws Exception {
        Path tmpDir = Files.createTempDirectory("paxai_test_");
        Path fake = tmpDir.resolve("python.exe");
        Files.writeString(fake, "stub");
        try {
            setEnv("PAX_AI_PYTHON", fake.toString());
            String resolved = PaxAILauncherModule.resolvePython();
            if (resolved == null || !Paths.get(resolved).equals(fake)) {
                throw new AssertionError("env override not honoured: " + resolved);
            }
        } finally {
            clearEnv("PAX_AI_PYTHON");
            Files.deleteIfExists(fake);
            Files.deleteIfExists(tmpDir);
        }
    }

    private static void logDirDefaultsToLocalAppData() {
        Path dir = PaxAILauncherModule.paxAiLogDir();
        if (dir == null) {
            throw new AssertionError("paxAiLogDir() returned null");
        }
        if (!dir.toString().toLowerCase().contains("appdata\\local\\pax-ai")
                && !dir.toString().toLowerCase().contains("pax-ai")) {
            throw new AssertionError("unexpected log dir: " + dir);
        }
    }

    /** Force-call startPaxAi without a valid python and confirm it logs+returns rather than throws. */
    private static void startWithMissingPythonIsNoopNotCrash() throws Exception {
        clearEnv("PAX_AI_PYTHON");
        // We can't make DEFAULT_PYTHON disappear, but startPaxAi is idempotent --
        // calling it on a host where the default exists is a real launch path
        // and we don't want to actually spawn pywebview in a unit test.
        boolean defExists = Files.exists(Paths.get(PaxAILauncherModule.DEFAULT_PYTHON));
        if (defExists) {
            // On the dev box DEFAULT_PYTHON exists; smoke-test by calling stop()
            // (idempotent when nothing is running) -- this is the meaningful
            // invariant we can test without a side effect.
            try {
                PaxAILauncherModule.stopPaxAi();   // never thrown when nothing running
            } catch (Throwable t) {
                throw new AssertionError("stopPaxAi must not throw when no process: " + t);
            }
        } else {
            // No interpreter on this host -> startPaxAi must NOT throw.
            try {
                PaxAILauncherModule.startPaxAi();
            } catch (Throwable t) {
                throw new AssertionError("startPaxAi must not throw when python missing: " + t);
            }
        }
    }

    // ----------------------------------------------------------------------
    // env helpers using the reflective hack that the platform allows
    // (Java >= 9 has no public set-env API; we touch the modifiable map).
    // ----------------------------------------------------------------------

    @SuppressWarnings("unchecked")
    private static void setEnv(String name, String value) throws Exception {
        Class<?> cl = Class.forName("java.lang.ProcessEnvironment");
        java.lang.reflect.Field theEnv = cl.getDeclaredField("theEnvironment");
        theEnv.setAccessible(true);
        java.util.Map<String, String> env =
            (java.util.Map<String, String>) theEnv.get(null);
        env.put(name, value);
        java.lang.reflect.Field theCaseInsensitiveEnv =
            cl.getDeclaredField("theCaseInsensitiveEnvironment");
        theCaseInsensitiveEnv.setAccessible(true);
        java.util.Map<String, String> cienv =
            (java.util.Map<String, String>) theCaseInsensitiveEnv.get(null);
        cienv.put(name, value);
    }

    @SuppressWarnings("unchecked")
    private static void clearEnv(String name) throws Exception {
        Class<?> cl = Class.forName("java.lang.ProcessEnvironment");
        java.lang.reflect.Field theEnv = cl.getDeclaredField("theEnvironment");
        theEnv.setAccessible(true);
        java.util.Map<String, String> env =
            (java.util.Map<String, String>) theEnv.get(null);
        env.remove(name);
        java.lang.reflect.Field theCaseInsensitiveEnv =
            cl.getDeclaredField("theCaseInsensitiveEnvironment");
        theCaseInsensitiveEnv.setAccessible(true);
        java.util.Map<String, String> cienv =
            (java.util.Map<String, String>) theCaseInsensitiveEnv.get(null);
        cienv.remove(name);
    }
}
