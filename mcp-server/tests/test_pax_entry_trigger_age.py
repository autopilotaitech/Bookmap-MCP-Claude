"""Tests for pax.entry_trigger_age_ms (M4 dial-in change 2026-05-28).

Quantifies entry lateness: how long the actionable ENTER decision has been
live (= how stale the data-driven trigger is if acted on now). Measured from
the snapshot timestamp the decision first became actionable; grows while the
same decision persists; resets on a new/changed decision; None when not
actionable.

Tested against the thin post-processor _attach_entry_trigger_age so we do not
need to construct a fully gate-passing pax snapshot.
"""

from __future__ import annotations

import pytest

import bookmap_mcp.dashboard as dash


_ALIAS = "NQM6.CME@RITHMIC"


@pytest.fixture(autouse=True)
def _clear_cache():
    dash._PAX_TRIGGER_SINCE_MS.clear()
    yield
    dash._PAX_TRIGGER_SINCE_MS.clear()


def _snap(ts_ms: int) -> dict:
    return {"alias": _ALIAS, "ts_ms": ts_ms}


def test_wait_decision_has_none_age():
    dec = {"decision": "WAIT", "size": 0}
    dash._attach_entry_trigger_age(_snap(1_000), dec)
    assert dec["entry_trigger_age_ms"] is None


def test_stand_down_has_none_age():
    dec = {"decision": "STAND_DOWN", "size": 0}
    dash._attach_entry_trigger_age(_snap(1_000), dec)
    assert dec["entry_trigger_age_ms"] is None


def test_first_actionable_tick_is_zero_age():
    dec = {"decision": "ENTER_LONG_FOLLOW", "size": 3}
    dash._attach_entry_trigger_age(_snap(10_000), dec)
    assert dec["entry_trigger_age_ms"] == 0


def test_age_grows_while_same_decision_persists():
    d1 = {"decision": "ENTER_LONG_FOLLOW", "size": 3}
    dash._attach_entry_trigger_age(_snap(10_000), d1)
    d2 = {"decision": "ENTER_LONG_FOLLOW", "size": 3}
    dash._attach_entry_trigger_age(_snap(12_500), d2)
    assert d2["entry_trigger_age_ms"] == 2_500


def test_age_resets_when_decision_label_changes():
    d1 = {"decision": "ENTER_LONG_FOLLOW", "size": 3}
    dash._attach_entry_trigger_age(_snap(10_000), d1)
    d2 = {"decision": "ENTER_SHORT_FOLLOW", "size": 3}
    dash._attach_entry_trigger_age(_snap(15_000), d2)
    assert d2["entry_trigger_age_ms"] == 0


def test_non_actionable_clears_cache_so_reentry_restarts():
    d1 = {"decision": "ENTER_LONG_FOLLOW", "size": 3}
    dash._attach_entry_trigger_age(_snap(10_000), d1)
    # Decision lapses to WAIT...
    dwait = {"decision": "WAIT", "size": 0}
    dash._attach_entry_trigger_age(_snap(11_000), dwait)
    assert dwait["entry_trigger_age_ms"] is None
    # ...then a fresh ENTER later must restart the clock at 0, not 4000.
    d2 = {"decision": "ENTER_LONG_FOLLOW", "size": 3}
    dash._attach_entry_trigger_age(_snap(14_000), d2)
    assert d2["entry_trigger_age_ms"] == 0


def test_pax_decision_attaches_field_on_non_actionable_path():
    # Health not ok -> immediate STAND_DOWN; field must still be present.
    out = dash.pax_decision({"alias": _ALIAS, "ts_ms": 1, "health": "offline"})
    assert "entry_trigger_age_ms" in out
    assert out["entry_trigger_age_ms"] is None
