package com.openrange;

import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;

import velox.api.layer1.Layer1ApiProvider;

public class PaxOpeningRangeDiagnosticsTest {
    public static void main(String[] args) {
        formatsUsefulCacheDiagnostics();
        handlesMissingSnapshot();
        moduleDiagnosticsSurvivesUnavailableCurrentTime();
    }

    private static void formatsUsefulCacheDiagnostics() {
        PaxOpeningRangeFeatureCache cache = new PaxOpeningRangeFeatureCache(3);
        cache.update(1_000_000_000L, new PaxOpeningRangeMarketState(6400.25, 100, 200, -100, 300), signal());

        String text = PaxOpeningRangeDiagnostics.format(cache, 2_500_000_000L, "build\\logs\\ES.csv");

        assertContains(text, "Snapshots: 1", "snapshots");
        assertContains(text, "Last update: 1s ago", "age");
        assertContains(text, "CVD: p82", "cvd");
        assertContains(text, "PS: p91", "ps");
        assertContains(text, "Range: OK", "range");
        assertContains(text, "CSV: build\\logs\\ES.csv", "csv");
    }

    private static void handlesMissingSnapshot() {
        PaxOpeningRangeFeatureCache cache = new PaxOpeningRangeFeatureCache(3);

        String text = PaxOpeningRangeDiagnostics.format(cache, 2_500_000_000L, "build\\logs");

        assertContains(text, "Snapshots: 0", "snapshots");
        assertContains(text, "Last update: none", "age");
    }

    private static void moduleDiagnosticsSurvivesUnavailableCurrentTime() {
        Layer1ApiProvider provider = throwingCurrentTimeProvider();
        PaxOpeningRangeModule module = new PaxOpeningRangeModule(provider);

        String text = invokeDiagnosticsText(module, "MNQM6.CME@RITHMIC");

        assertContains(text, "Snapshots: 0", "snapshots");
        assertContains(text, "Last update: none", "age");
    }

    private static PaxOpeningRangeSignal signal() {
        return new PaxOpeningRangeSignal(
                PaxOpeningRangeSignalAction.ALLOW_SIGNAL,
                PaxOpeningRangeSignalBias.LONG,
                PaxOpeningRangeSignalConfidence.HIGH,
                "Price is above ORH and order-flow confirmation is strong.",
                4,
                4,
                "CVD+ BID+ ASK PULL NET+",
                "ORH",
                6,
                10.0,
                45,
                400,
                800,
                1.8,
                2.1,
                82,
                91,
                "OK");
    }

    private static void assertContains(String actual, String expected, String message) {
        if (!actual.contains(expected)) {
            throw new AssertionError(message + ": expected \"" + actual + "\" to contain \"" + expected + "\"");
        }
    }

    private static String invokeDiagnosticsText(PaxOpeningRangeModule module, String alias) {
        try {
            Method method = PaxOpeningRangeModule.class.getDeclaredMethod("diagnosticsText", String.class);
            method.setAccessible(true);
            return (String) method.invoke(module, alias);
        } catch (Exception e) {
            throw new AssertionError("diagnosticsText should not throw", e);
        }
    }

    private static Layer1ApiProvider throwingCurrentTimeProvider() {
        InvocationHandler handler = (proxy, method, args) -> {
            if ("getCurrentTime".equals(method.getName())) {
                throw new IllegalStateException("Strategy life cycle is violated");
            }
            Class<?> returnType = method.getReturnType();
            if (returnType == Boolean.TYPE) {
                return false;
            }
            if (returnType == Long.TYPE) {
                return 0L;
            }
            if (returnType == Integer.TYPE) {
                return 0;
            }
            if (returnType == Double.TYPE) {
                return 0.0;
            }
            if (returnType == Float.TYPE) {
                return 0.0f;
            }
            if (returnType == Void.TYPE) {
                return null;
            }
            return null;
        };
        return (Layer1ApiProvider) Proxy.newProxyInstance(
                PaxOpeningRangeDiagnosticsTest.class.getClassLoader(),
                new Class<?>[] {Layer1ApiProvider.class},
                handler);
    }
}
