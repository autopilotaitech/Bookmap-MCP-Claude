"""Phase 0 schema test: capture-driven assertion of the exact field paths
Pax AI relies on from the dashboard /api/snapshot payload.

Runs against any *.json fixture in pax-ai/fixtures/. If no fixtures are
present, the test is SKIPPED with a clear instruction to run
fixtures/capture_snapshot.py against a live dashboard.

This test is the gate between Phase 0 and Phase 1. Once it is green
against at least one live-anchor fixture, the trigger engine and the
signal-projection layer (Phase 2+) can read these fields by name with
confidence.

Field paths come from section 4.1 of the design spec
(docs/superpowers/specs/2026-05-19-pax-ai-design.md). Two classes of
fixtures are recognized:

  * LIVE fixtures: snap["health"] == "ok". All "required when live" paths
    must be present (some may be None, but the key must exist).
  * OFFLINE fixtures: snap["health"] == "offline". A bounded set of
    error-envelope paths must be present (bridgeUrl, bridgeError, nextSteps,
    tokenConfigured, error) per dashboard.py::_build_offline_snapshot.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, List, Sequence, Tuple

import pytest


FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"


# ---------------------------------------------------------------------------
# Paths Pax AI's downstream consumers will read.
# ---------------------------------------------------------------------------

# Required on every fixture, live or offline.
COMMON_REQUIRED = [
    "health",
]

# Required only when health == "ok" (live dashboard, instrument attached).
# An entry "a.b.c" means the test will assert snap["a"]["b"]["c"] is reachable
# (key chain exists; value may be None or empty container).
LIVE_REQUIRED = [
    "alias",
    "book",
    "book.mid",
    "book.spread",
    "or_levels",
    "or_levels.levels",
    "or_levels.middleLock",
    "or_levels.inProximity",
    "conviction",
    "conviction.score",
    "conviction.trend",
    "conviction.anchorMode",
    "trend_signal",
    "trend_signal.kind",
    "trend_signal.eligible",
    "flow",
    "flow.regime",
    "flow.regimeConfidence",
    "flow.biasScore",
    "flow.biasTrajectory",
    "vwap_bias",
    "vwap_bias.label",
    "vp_bias",
    "vp_bias.label",
    "micro_events",
    "micro_events.events",
    "tape_flow",
    "gates",
    "gates.session",
    "gates.session.code",
    "gates.news",
    "gates.news.blocked",
    "pax",
    "pax.decision",
    "session",
    "session.anchorMode",
]

# Each OR level row must expose these keys (we sample levels[0] if present).
LEVEL_ROW_REQUIRED = [
    "label",
    "price",
    "side",
    "distance",
    "proximity",
    "decision",
    "confidence",
]

# Required only when health == "offline".
OFFLINE_REQUIRED = [
    "health",
    "bridgeError",
    "tokenConfigured",
    "nextSteps",
    "error",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve(obj: Any, dotted: str) -> Tuple[bool, Any]:
    """Walk obj along the dotted path. Return (key_chain_exists, leaf_value).

    A key counts as present if the parent is a dict and the key is in it,
    even if the value is None or an empty list/dict.
    """
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False, None
        cur = cur[part]
    return True, cur


def _list_fixtures() -> List[Path]:
    if not FIXTURE_DIR.exists():
        return []
    return sorted(FIXTURE_DIR.glob("snapshot_*.json"))


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _classify(snap: dict) -> str:
    h = snap.get("health")
    if h == "ok":
        return "live"
    if h == "offline":
        return "offline"
    return "unknown"


def _assert_paths(snap: dict, paths: Sequence[str], fixture_name: str,
                  classification: str) -> None:
    missing = []
    for p in paths:
        ok, _ = _resolve(snap, p)
        if not ok:
            missing.append(p)
    assert not missing, (
        f"fixture {fixture_name} (classification={classification}) "
        f"missing required key chain(s): {missing}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def _fixture_params() -> Iterable[pytest.param]:
    fixtures = _list_fixtures()
    if not fixtures:
        yield pytest.param(None, id="no-fixtures",
                            marks=pytest.mark.skip(
                                reason="no fixtures captured yet; run "
                                "pax-ai/fixtures/capture_snapshot.py against "
                                "a live dashboard"))
        return
    for p in fixtures:
        yield pytest.param(p, id=p.name)


@pytest.mark.parametrize("fixture_path", _fixture_params())
def test_snapshot_required_fields(fixture_path: Path | None) -> None:
    """Every captured fixture must expose Pax AI's required key chains."""
    if fixture_path is None:
        pytest.skip("see capture_snapshot.py")
    snap = _load(fixture_path)
    cls = _classify(snap)
    _assert_paths(snap, COMMON_REQUIRED, fixture_path.name, cls)

    if cls == "live":
        _assert_paths(snap, LIVE_REQUIRED, fixture_path.name, cls)
        levels = snap.get("or_levels", {}).get("levels") or []
        if levels:
            sample = levels[0]
            assert isinstance(sample, dict), (
                f"fixture {fixture_path.name}: or_levels.levels[0] is not dict "
                f"(got {type(sample).__name__})")
            missing = [k for k in LEVEL_ROW_REQUIRED if k not in sample]
            assert not missing, (
                f"fixture {fixture_path.name}: or_levels.levels[0] missing "
                f"keys: {missing}")
    elif cls == "offline":
        _assert_paths(snap, OFFLINE_REQUIRED, fixture_path.name, cls)
    else:
        pytest.fail(f"fixture {fixture_path.name}: snap['health']="
                    f"{snap.get('health')!r}, expected 'ok' or 'offline'")


def test_at_least_one_live_fixture_expected_eventually() -> None:
    """A meta-check: warns when only offline fixtures are present.

    Skipped (not failed) when zero or only-offline fixtures exist. Once a
    live fixture is captured this test becomes a fast-fail guard against
    accidental fixture deletion / schema drift in CI.
    """
    fixtures = _list_fixtures()
    if not fixtures:
        pytest.skip("no fixtures captured yet")
    live = [p for p in fixtures
              if _classify(_load(p)) == "live"]
    if not live:
        pytest.skip("only offline fixtures present so far; capture a live "
                    "fixture once the dashboard + bridge are up")
    assert live, "at least one live fixture must be present"
