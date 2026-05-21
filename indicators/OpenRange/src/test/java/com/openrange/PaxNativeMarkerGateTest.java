package com.openrange;

public class PaxNativeMarkerGateTest {

    public static void main(String[] args) {
        gateOnSuppressesPublish();
        gateOffAllowsPublish();
        System.out.println("PaxNativeMarkerGateTest OK");
    }

    private static void gateOnSuppressesPublish() {
        if (!PaxNativeMarkerGate.shouldSuppress(true)) {
            throw new AssertionError("gate=ON must suppress native marker publish");
        }
    }

    private static void gateOffAllowsPublish() {
        if (PaxNativeMarkerGate.shouldSuppress(false)) {
            throw new AssertionError("gate=OFF must allow native marker publish");
        }
    }
}
