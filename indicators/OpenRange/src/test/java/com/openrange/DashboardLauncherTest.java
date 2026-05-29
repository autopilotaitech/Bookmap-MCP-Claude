package com.openrange;

import java.net.ServerSocket;

/** main()-based test (matches the OpenRange build.ps1 runner style). */
public class DashboardLauncherTest {

    public static void main(String[] args) throws Exception {
        // stop() with nothing running must be a safe no-op (never throws).
        DashboardLauncher.stop();

        // portInUse: a live listener on a port reports in-use.
        try (ServerSocket ss = new ServerSocket(0)) {
            int held = ss.getLocalPort();
            check(DashboardLauncher.portInUse(held),
                  "a held listener port must report in-use");
        }

        // portInUse: a port nothing listens on reports not-in-use.
        int freePort;
        try (ServerSocket ss = new ServerSocket(0)) {
            freePort = ss.getLocalPort();
        }
        check(!DashboardLauncher.portInUse(freePort),
              "a closed port should report not-in-use");

        // resolvePython tolerates a bogus env override without throwing.
        // (We don't assert the result: DEFAULT_PYTHON may or may not exist here.)
        DashboardLauncher.resolvePython();

        // log dir resolves to a non-null path.
        check(DashboardLauncher.dashboardLogDir() != null, "log dir must resolve");

        System.out.println("DashboardLauncherTest OK");
    }

    private static void check(boolean cond, String msg) {
        if (!cond) {
            System.err.println("FAIL: " + msg);
            System.exit(1);
        }
    }
}
