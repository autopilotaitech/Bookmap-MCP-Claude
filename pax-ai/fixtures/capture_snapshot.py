"""Phase 0: capture /api/snapshot from the live dashboard for fixture-driven tests.

Hits 127.0.0.1:18888/api/snapshot, saves the JSON to
pax-ai/fixtures/snapshot_<ISO>.json. Skips and prints a clear error if the
dashboard is offline (so the schema test can still run against any previously
captured fixtures).

Usage:
    python C:\\Bookmap\\addons\\MCP\\Bookmap\\pax-ai\\fixtures\\capture_snapshot.py
    python C:\\Bookmap\\addons\\MCP\\Bookmap\\pax-ai\\fixtures\\capture_snapshot.py --label or_forming
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

DASHBOARD_URL = "http://127.0.0.1:18888/api/snapshot"
FIXTURE_DIR = Path(__file__).parent
TIMEOUT_S = 5.0


def capture(label: str | None = None) -> int:
    try:
        with urllib.request.urlopen(DASHBOARD_URL, timeout=TIMEOUT_S) as resp:
            raw = resp.read()
    except urllib.error.URLError as exc:
        sys.stderr.write(f"[capture] dashboard unreachable at {DASHBOARD_URL}: "
                          f"{exc.reason}\n")
        sys.stderr.write("[capture] start the dashboard (.\\dashboard-start.ps1) "
                          "and re-run.\n")
        return 2

    try:
        body = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"[capture] dashboard returned non-JSON: {exc}\n")
        return 3

    if body.get("health") == "offline":
        sys.stderr.write("[capture] dashboard returned health=offline -- "
                          "the bridge is not reachable from the dashboard.\n")
        for step in body.get("nextSteps") or []:
            sys.stderr.write(f"          {step}\n")
        sys.stderr.write("[capture] saving anyway; schema test will recognize "
                          "the offline shape.\n")

    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    suffix = f"_{label}" if label else ""
    out = FIXTURE_DIR / f"snapshot_{stamp}{suffix}.json"
    out.write_bytes(raw)
    size_kb = len(raw) / 1024.0
    print(f"[capture] wrote {out.name} ({size_kb:.1f} KB) "
          f"health={body.get('health')} alias={body.get('alias')}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Capture dashboard /api/snapshot fixture.")
    ap.add_argument("--label", type=str, default=None,
                    help="Optional label suffix (e.g. or_forming, post_or, at_level).")
    ap.add_argument("--repeat", type=int, default=1,
                    help="Capture N snapshots (~1s apart).")
    args = ap.parse_args()
    rc = 0
    for i in range(max(1, args.repeat)):
        rc = capture(args.label)
        if rc != 0:
            return rc
        if i + 1 < args.repeat:
            time.sleep(1.0)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
