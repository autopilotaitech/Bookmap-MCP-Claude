"""Settings system — schema, validation, persistence, audit, cache identity.

Each test uses ``tmp_path`` to point ``settings.py`` at a throwaway file
location, so production state under ``mcp-server/bookmap_mcp/`` is never
touched."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bookmap_mcp import settings as settings_mod   # noqa: E402
from bookmap_mcp.journal import Journal             # noqa: E402


# ─── fixture: isolated settings cache + on-disk path ────────────────────────


@pytest.fixture
def s(tmp_path, monkeypatch):
    """A pristine settings module pointed at tmp_path.

    Each test gets a fresh in-memory cache and a tmp settings file. No
    fixture leakage between tests because we deepcopy SETTINGS_DEFAULTS
    into a fresh path each time and reset module-level state."""
    path = tmp_path / "pax_settings.json"
    lkg = tmp_path / "pax_settings.last_good.json"
    monkeypatch.setattr(settings_mod, "_SETTINGS_PATH", path)
    monkeypatch.setattr(settings_mod, "_LKG_PATH", lkg)
    # Reset module-level state.
    settings_mod._SETTINGS_CACHE.clear()
    settings_mod._SETTINGS_MTIME = 0.0
    settings_mod._SETTINGS_SOURCE = ""
    settings_mod._SETTINGS_LAST_APPLIED_MS = 0
    settings_mod.set_audit_sink(None)
    settings_mod.set_event_sink(None)
    yield settings_mod
    settings_mod.set_audit_sink(None)
    settings_mod.set_event_sink(None)


# ─── test 1: defaults match current code ────────────────────────────────────


def test_defaults_match_current_constants(s):
    """SETTINGS_DEFAULTS values exactly match the original module-level
    constants in dashboard.py / sim_engine.py. Regression guard."""
    from bookmap_mcp import dashboard, sim_engine

    assert s.SETTINGS_DEFAULTS["magnet_refresh_secs"] == dashboard._MAGNET_REFRESH_SECS

    sim = sim_engine.SimEngine(alias="TEST", db_path=Path(":memory:"),
                                eod_close_hour_ct=15)
    assert s.SETTINGS_DEFAULTS["eod_close_hour_ct"] == 15
    # Construct-time default — check class attribute via __init__ default.
    import inspect
    sig = inspect.signature(sim_engine.SimEngine.__init__)
    assert sig.parameters["eod_close_hour_ct"].default == s.SETTINGS_DEFAULTS["eod_close_hour_ct"]

    assert s.SETTINGS_DEFAULTS["pax_confidence_floor"] == dashboard.PAX_CONFIDENCE_FLOOR
    assert s.SETTINGS_DEFAULTS["pax_confidence_full"]  == dashboard.PAX_CONFIDENCE_FULL
    assert s.SETTINGS_DEFAULTS["pax_min_or_width_pts"] == dashboard.PAX_MIN_OR_WIDTH_PTS
    assert s.SETTINGS_DEFAULTS["pax_max_or_width_pts"] == dashboard.PAX_MAX_OR_WIDTH_PTS

    assert s.SETTINGS_DEFAULTS["level_weights"]              == dashboard._LVL_W
    assert s.SETTINGS_DEFAULTS["level_directional_threshold"] == dashboard._LVL_THR_DIRECTIONAL
    assert s.SETTINGS_DEFAULTS["level_thin_coverage_frac"]    == dashboard._LVL_THIN_COVERAGE_FRAC

    assert s.SETTINGS_DEFAULTS["tape_bucket_weights"]    == dashboard._TAPE_BUCKET_WEIGHTS
    assert s.SETTINGS_DEFAULTS["tape_thin_floor_prints"] == dashboard._TAPE_THIN_FLOOR_PRINTS
    assert s.SETTINGS_DEFAULTS["tape_thin_hedge_prints"] == dashboard._TAPE_THIN_HEDGE_PRINTS
    assert s.SETTINGS_DEFAULTS["tape_align_bonus"]       == dashboard._TAPE_ALIGN_BONUS
    assert s.SETTINGS_DEFAULTS["tape_align_threshold"]   == dashboard._TAPE_ALIGN_THRESHOLD


# ─── test 2: per-field validation ───────────────────────────────────────────


@pytest.mark.parametrize("field,bad_value,reason", [
    ("magnet_refresh_secs",          -1.0,   "negative"),
    ("magnet_refresh_secs",          "abc",  "non-numeric"),
    ("eod_close_hour_ct",            24,     "above range"),
    ("eod_close_hour_ct",            -1,     "below range"),
    ("pax_confidence_floor",         1.5,    "above 1.0"),
    ("pax_confidence_floor",         -0.1,   "below 0"),
    ("pax_min_or_width_pts",         0.0,    "non-positive"),
    ("vwap_stretch_penalty_1_2sigma", 0.1,   "positive penalty"),
    ("vwap_stretch_penalty_2_3sigma", -1.5,  "below -1.0"),
])
def test_validation_rejects_per_field_violations(s, field, bad_value, reason):
    proposal = dict(s.SETTINGS_DEFAULTS)
    proposal[field] = bad_value
    coerced, errs = s.validate(proposal)
    assert errs, f"{reason}: expected error, got none"
    assert any(field in e for e in errs), \
        f"{reason}: errors {errs} should mention {field}"


def test_validation_rejects_bool_in_int_field(s):
    proposal = dict(s.SETTINGS_DEFAULTS)
    proposal["tape_thin_floor_prints"] = True
    _, errs = s.validate(proposal)
    assert errs


def test_validation_rejects_unknown_keys(s):
    proposal = dict(s.SETTINGS_DEFAULTS)
    proposal["definitely_not_a_real_setting"] = 42
    _, errs = s.validate(proposal)
    assert any("unknown" in e for e in errs)


def test_validation_rejects_bad_sub_keys(s):
    proposal = dict(s.SETTINGS_DEFAULTS)
    bad = dict(s.SETTINGS_DEFAULTS["level_weights"])
    bad["new_driver"] = 0.5
    proposal["level_weights"] = bad
    _, errs = s.validate(proposal)
    assert any("unexpected sub-keys" in e for e in errs)


# ─── test 3: cross-field validation ─────────────────────────────────────────


def test_validation_rejects_or_width_min_above_max(s):
    proposal = dict(s.SETTINGS_DEFAULTS)
    proposal["pax_min_or_width_pts"] = 30.0
    proposal["pax_max_or_width_pts"] = 25.0
    _, errs = s.validate(proposal)
    assert any("pax_min_or_width_pts" in e and "pax_max_or_width_pts" in e for e in errs)


def test_validation_rejects_confidence_floor_above_full(s):
    proposal = dict(s.SETTINGS_DEFAULTS)
    proposal["pax_confidence_floor"] = 0.9
    proposal["pax_confidence_full"]  = 0.5
    _, errs = s.validate(proposal)
    assert any("confidence_floor" in e and "confidence_full" in e for e in errs)


def test_validation_rejects_vwap_penalty_ordering(s):
    proposal = dict(s.SETTINGS_DEFAULTS)
    proposal["vwap_stretch_penalty_1_2sigma"]    = -0.50
    proposal["vwap_stretch_penalty_2_3sigma"]    = -0.30
    proposal["vwap_stretch_penalty_3sigma_plus"] = -0.10
    _, errs = s.validate(proposal)
    assert any("vwap_stretch" in e for e in errs)


def test_validation_rejects_thin_floor_above_hedge(s):
    proposal = dict(s.SETTINGS_DEFAULTS)
    proposal["tape_thin_floor_prints"] = 20
    proposal["tape_thin_hedge_prints"] = 10
    _, errs = s.validate(proposal)
    assert any("tape_thin_floor_prints" in e for e in errs)


def test_validation_rejects_zero_level_weight_sum(s):
    proposal = dict(s.SETTINGS_DEFAULTS)
    proposal["level_weights"] = {k: 0.0 for k in s.SETTINGS_DEFAULTS["level_weights"]}
    _, errs = s.validate(proposal)
    assert any("level_weights" in e and "sum" in e for e in errs)


# ─── test 4: apply atomic — no partial writes ───────────────────────────────


def test_apply_atomic_no_partial_writes(s):
    s.load_settings()
    before_file = s._SETTINGS_PATH.read_text()
    before_floor = s.get("pax_confidence_floor")

    result = s.apply_settings({
        "pax_confidence_floor": 0.42,           # valid
        "pax_min_or_width_pts": 100.0,          # > max → cross-field fail
    })
    assert not result.ok
    assert s.get("pax_confidence_floor") == before_floor
    assert s._SETTINGS_PATH.read_text() == before_file
    # No LKG should have been rotated because the apply failed.
    assert not s._LKG_PATH.exists()


# ─── test 5: LKG rotation on successful apply ───────────────────────────────


def test_apply_rotates_last_known_good(s):
    s.load_settings()
    before_disk = s._SETTINGS_PATH.read_text()

    result = s.apply_settings({"pax_confidence_floor": 0.42})
    assert result.ok
    assert s._LKG_PATH.exists()
    # LKG should equal the PRE-apply on-disk content.
    assert s._LKG_PATH.read_text() == before_disk
    # New on-disk file should reflect the change.
    new_disk = json.loads(s._SETTINGS_PATH.read_text())
    assert new_disk["pax_confidence_floor"] == 0.42


# ─── test 6: corrupt file → fall back to LKG ────────────────────────────────


def test_load_corrupt_file_falls_back_to_lkg(s):
    s.load_settings()
    s.apply_settings({"pax_confidence_floor": 0.42})
    # Capture LKG (= the original defaults file).
    lkg_text = s._LKG_PATH.read_text()
    # Corrupt the live settings file.
    s._SETTINGS_PATH.write_text("{ not json }")
    # Reset cache + mtime so the next load() re-reads from disk.
    s._SETTINGS_CACHE.clear()
    s._SETTINGS_MTIME = 0.0

    events: List[tuple] = []
    s.set_event_sink(lambda kind, src, msg, payload: events.append((kind, msg)))

    s.load_settings(force=True)
    assert any(ev[0] == "SETTINGS_REVERT_LKG" for ev in events)
    # Cache now holds LKG values (= the original defaults pre-apply).
    assert s.get("pax_confidence_floor") == s.SETTINGS_DEFAULTS["pax_confidence_floor"]


# ─── test 7: missing file → write defaults ──────────────────────────────────


def test_load_missing_falls_back_to_defaults(s):
    assert not s._SETTINGS_PATH.exists()
    cache = s.load_settings(force=True)
    assert s._SETTINGS_PATH.exists()
    assert cache["pax_confidence_floor"] == s.SETTINGS_DEFAULTS["pax_confidence_floor"]


# ─── test 8: mtime watcher picks up external edit ───────────────────────────


def test_mtime_watcher_picks_up_external_edit(s):
    s.load_settings()
    s.load_settings()                  # second call must be a no-op (mtime same)
    # External edit: rewrite the file with a value that still satisfies
    # floor <= full (default full = 0.50).
    data = json.loads(s._SETTINGS_PATH.read_text())
    data["pax_confidence_floor"] = 0.45
    s._SETTINGS_PATH.write_text(json.dumps(data))
    # Force mtime to be visibly newer (Windows mtime resolution).
    import os
    new_mt = s._SETTINGS_MTIME + 2.0
    os.utime(s._SETTINGS_PATH, (new_mt, new_mt))
    s.load_settings()
    assert s.get("pax_confidence_floor") == 0.45


# ─── test 9: audit log writes one row per changed field ─────────────────────


def test_audit_log_writes_one_row_per_changed_field(s, tmp_path):
    s.load_settings()
    j = Journal(tmp_path / "journal.db")
    j.open()
    j.begin_run(adapter_name="test")
    s.set_audit_sink(j.write_settings_audit)

    result = s.apply_settings({
        "pax_confidence_floor": 0.42,
        "pax_min_or_width_pts": 5.0,
        "tape_align_bonus":     0.20,
    }, source="ui", user="will@autopilotaitech.com")
    assert result.ok
    rows = j.read_settings_audit(limit=10)
    fields = {r["field"] for r in rows}
    assert fields == {"pax_confidence_floor", "pax_min_or_width_pts", "tape_align_bonus"}
    for r in rows:
        assert r["source"] == "ui"
        assert r["user"]   == "will@autopilotaitech.com"
        assert r["reason"] == "apply"
        assert r["old_value_json"] is not None
        assert r["new_value_json"] is not None
        assert r["run_id"] == j.run_id
    j.close()


def test_audit_log_writes_nothing_for_identical_apply(s, tmp_path):
    s.load_settings()
    j = Journal(tmp_path / "journal.db")
    j.open()
    j.begin_run(adapter_name="test")
    s.set_audit_sink(j.write_settings_audit)

    result = s.apply_settings({"pax_confidence_floor": s.get("pax_confidence_floor")})
    assert result.ok
    assert j.read_settings_audit() == []
    j.close()


# ─── test 10: cache identity preserved across reload ────────────────────────


def test_cache_identity_preserved_across_reload(s):
    s.load_settings()
    before_id = id(s._SETTINGS_CACHE)
    s.apply_settings({"pax_confidence_floor": 0.42})
    after_id = id(s._SETTINGS_CACHE)
    assert before_id == after_id

    # External edit + force-reload also preserves identity.
    data = json.loads(s._SETTINGS_PATH.read_text())
    data["pax_confidence_floor"] = 0.50
    s._SETTINGS_PATH.write_text(json.dumps(data))
    import os
    new_mt = s._SETTINGS_MTIME + 2.0
    os.utime(s._SETTINGS_PATH, (new_mt, new_mt))
    s.load_settings()
    assert id(s._SETTINGS_CACHE) == before_id
    assert s.get("pax_confidence_floor") == 0.50


# ─── bonus: restore-defaults and restore-LKG ────────────────────────────────


def test_restore_defaults_reverts_every_field(s):
    s.load_settings()
    s.apply_settings({"pax_confidence_floor": 0.91, "tape_align_bonus": 0.55})
    result = s.restore_defaults()
    assert result.ok
    assert s.get("pax_confidence_floor") == s.SETTINGS_DEFAULTS["pax_confidence_floor"]
    assert s.get("tape_align_bonus")     == s.SETTINGS_DEFAULTS["tape_align_bonus"]


def test_restore_lkg_when_no_lkg_present(s):
    s.load_settings()
    result = s.restore_last_known_good()
    assert not result.ok
    assert any("last-known-good" in e for e in result.errors)


def test_restore_lkg_round_trip(s):
    s.load_settings()
    # Both values must satisfy floor <= full (default full = 0.50).
    s.apply_settings({"pax_confidence_floor": 0.42})    # creates LKG
    s.apply_settings({"pax_confidence_floor": 0.48})    # overwrites
    assert s.get("pax_confidence_floor") == 0.48
    result = s.restore_last_known_good()
    assert result.ok
    # Restore should bring us back to the value that was on disk before the
    # 0.48 apply — i.e. 0.42.
    assert s.get("pax_confidence_floor") == 0.42


# ─── bonus: pax_weights.json is never touched ───────────────────────────────


def test_pax_weights_json_path_untouched(s):
    """Operations on pax_settings.json must never write to pax_weights.json."""
    from bookmap_mcp import dashboard

    weights_path = Path(dashboard._PAX_WEIGHTS_PATH)
    if not weights_path.exists():
        pytest.skip("pax_weights.json not present in this checkout")
    before_mtime = weights_path.stat().st_mtime
    before_bytes = weights_path.read_bytes()
    s.load_settings()
    s.apply_settings({"pax_confidence_floor": 0.42})
    s.restore_defaults()
    after_mtime = weights_path.stat().st_mtime
    after_bytes = weights_path.read_bytes()
    assert before_mtime == after_mtime
    assert before_bytes == after_bytes


# ─── apply with no audit sink registered should not fail ────────────────────


def test_apply_works_with_no_audit_sink(s):
    s.load_settings()
    s.set_audit_sink(None)
    result = s.apply_settings({"pax_confidence_floor": 0.42})
    assert result.ok


def test_status_reports_source_and_lkg_presence(s):
    s.load_settings()
    st = s.status()
    assert st["source"] in ("defaults", "file", "lkg")
    assert "lkg_present" in st


# ─── journal: settings_audit table exists ───────────────────────────────────


def test_journal_creates_settings_audit_table(tmp_path):
    j = Journal(tmp_path / "j.db")
    j.open()
    rows = j._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='settings_audit'"
    ).fetchall()
    assert len(rows) == 1
    j.close()


# ─── S2 wiring: dashboard reads settings at compute-time ────────────────────


def test_pax_decision_reads_or_width_from_settings(s):
    """Mutate `pax_min_or_width_pts`; pax_decision must reject an
    OR-width that was previously acceptable, without restart."""
    from bookmap_mcp import dashboard
    s.load_settings()
    # Build a minimal snap that would otherwise reach the OR-width gate.
    snap = {
        "health": "ok",
        "alias":  "TEST",
        "gates":  {"session": {"code": "ACTIVE"}, "news": {"blocked": False}},
        "or_levels": {
            "orHigh": 100.0, "orLow": 90.0, "orWidthPts": 10.0,
            "middleLock": False, "inProximity": False, "levels": [],
        },
    }
    d0 = dashboard.pax_decision(snap)
    # Stand-down because no proximate level, but OR width gate passed.
    assert "OR width" not in (d0.get("reason") or "")

    # Bump min OR width above the snap's value — the same decision should
    # now reject on OR-width.
    s.apply_settings({"pax_min_or_width_pts": 20.0})
    d1 = dashboard.pax_decision(snap)
    assert "OR width" in (d1.get("reason") or ""), d1


def test_compute_tape_flow_reads_thin_floor_from_settings(s):
    """Tape with prints below the floor returns THIN; raise the floor and
    a previously-acceptable poll becomes THIN."""
    from bookmap_mcp import dashboard
    s.load_settings()
    tape_snap = {
        "tape_buckets": {
            "buckets": [
                {"label": "1-10",  "buyVol30s": 5, "sellVol30s": 5, "prints30s": 4,
                 "buyVol5m": 50, "sellVol5m": 50, "prints5m": 40},
                {"label": "11-25", "buyVol30s": 3, "sellVol30s": 3, "prints30s": 3,
                 "buyVol5m": 30, "sellVol5m": 30, "prints5m": 30},
            ]
        }
    }
    # Default floor is 5; total prints30s = 7 → not THIN.
    flow0 = dashboard.compute_tape_flow(tape_snap)
    assert flow0 is not None
    assert flow0["deltaLabel"] != "THIN", flow0

    s.apply_settings({"tape_thin_floor_prints": 10})
    flow1 = dashboard.compute_tape_flow(tape_snap)
    assert flow1["deltaLabel"] == "THIN"


def test_vwap_stretch_penalty_reads_from_settings(s):
    """Bump the 1-2σ penalty; same stretch produces the new penalty."""
    from bookmap_mcp import dashboard
    s.load_settings()
    vwap_obj = {"vwap": 100.0, "stddev": 1.0, "samples": 200, "lastTradePrice": 101.5}
    pen0, _ = dashboard._vwap_stretch_penalty(vwap_obj, 101.5)
    assert pen0 == s.SETTINGS_DEFAULTS["vwap_stretch_penalty_1_2sigma"]

    s.apply_settings({"vwap_stretch_penalty_1_2sigma": -0.25})
    pen1, _ = dashboard._vwap_stretch_penalty(vwap_obj, 101.5)
    assert pen1 == -0.25


def test_sim_engine_eod_hour_respects_explicit_constructor_value(s, tmp_path):
    """Existing constructor contract: explicit value (incl. None) wins."""
    from bookmap_mcp import sim_engine
    s.load_settings()
    eng = sim_engine.SimEngine(alias="TEST", db_path=tmp_path / "x.db",
                                eod_close_hour_ct=None)
    assert eng.eod_close_hour_ct is None
    # Settings change does NOT alter an explicitly-set value.
    s.apply_settings({"eod_close_hour_ct": 13})
    assert eng.eod_close_hour_ct is None


def test_sim_engine_eod_hour_sentinel_reads_settings(s, tmp_path):
    """Construct with the sentinel; the property consults settings live."""
    from bookmap_mcp import sim_engine
    s.load_settings()
    eng = sim_engine.SimEngine(alias="TEST", db_path=tmp_path / "x.db",
                                eod_close_hour_ct=sim_engine.SimEngine._SETTINGS_SENTINEL)
    assert eng.eod_close_hour_ct == s.SETTINGS_DEFAULTS["eod_close_hour_ct"]
    s.apply_settings({"eod_close_hour_ct": 14})
    assert eng.eod_close_hour_ct == 14
    s.apply_settings({"eod_close_hour_ct": None})
    assert eng.eod_close_hour_ct is None


# ─── S3 HTTP layer ──────────────────────────────────────────────────────────


@pytest.fixture
def http_server(s):
    """Start DashboardHandler on a random port. Returns the base URL."""
    import threading
    from http.server import ThreadingHTTPServer
    from bookmap_mcp.dashboard import DashboardHandler

    s.load_settings()
    DashboardHandler.set_audit_journal(None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    httpd.server_close()
    DashboardHandler.set_audit_journal(None)


def _http_get(url: str):
    import urllib.request
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, resp.read().decode("utf-8"), resp.headers

def _http_post(url: str, body: Dict[str, Any]):
    import urllib.request
    data = json.dumps(body).encode("utf-8") if body is not None else b""
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def test_http_get_api_settings_returns_current_and_defaults(http_server, s):
    status, body, _ = _http_get(http_server + "/api/settings")
    assert status == 200
    payload = json.loads(body)
    assert payload["current"]["pax_confidence_floor"] == s.SETTINGS_DEFAULTS["pax_confidence_floor"]
    assert payload["defaults"]["pax_confidence_floor"] == s.SETTINGS_DEFAULTS["pax_confidence_floor"]
    assert "source" in payload["status"]
    assert "audit" in payload


def test_http_post_api_settings_applies_valid(http_server, s):
    status, body = _http_post(http_server + "/api/settings",
                              {"updates": {"pax_confidence_floor": 0.42}})
    assert status == 200, body
    j = json.loads(body)
    assert j["ok"] is True
    assert j["applied"]["pax_confidence_floor"] == 0.42
    assert s.get("pax_confidence_floor") == 0.42


def test_http_post_api_settings_rejects_invalid(http_server, s):
    before = s.get("pax_confidence_floor")
    status, body = _http_post(http_server + "/api/settings",
                              {"updates": {"pax_min_or_width_pts": 100.0}})
    assert status == 400, body
    j = json.loads(body)
    assert j["ok"] is False
    assert any("pax_min_or_width_pts" in e for e in j["errors"])
    assert s.get("pax_confidence_floor") == before


def test_http_post_api_settings_rejects_invalid_json(http_server, s):
    import urllib.request
    req = urllib.request.Request(
        http_server + "/api/settings", data=b"not json{",
        method="POST", headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5)
        assert False, "expected 400"
    except urllib.error.HTTPError as e:
        assert e.code == 400


def test_http_post_missing_updates_returns_400(http_server, s):
    status, body = _http_post(http_server + "/api/settings", {"source": "ui"})
    assert status == 400
    j = json.loads(body)
    assert any("updates" in e for e in j["errors"])


def test_http_get_settings_page_renders_with_palette(http_server):
    status, body, headers = _http_get(http_server + "/settings")
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    # Tokyo Night palette markers + collapse-state key pattern.
    assert "#0b0e13" in body
    assert "#7aa2f7" in body
    assert "#9ece6a" in body
    assert "pax-settings:" in body
    assert "JetBrains Mono" in body
    # Every V1 setting must be addressable from the page.
    for key in [
        "magnet_refresh_secs","eod_close_hour_ct",
        "pax_confidence_floor","pax_confidence_full",
        "pax_min_or_width_pts","pax_max_or_width_pts","pax_bias_agreement_boost",
        "level_directional_threshold","level_thin_coverage_frac",
        "tape_thin_floor_prints","tape_thin_hedge_prints",
        "tape_align_bonus","tape_align_threshold",
        "vwap_stretch_penalty_1_2sigma","vwap_stretch_penalty_2_3sigma",
        "vwap_stretch_penalty_3sigma_plus",
        "level_weights","tape_bucket_weights",
    ]:
        assert key in body, f"settings page missing field marker {key}"


def test_http_post_reset_endpoint(http_server, s):
    s.apply_settings({"pax_confidence_floor": 0.42})
    assert s.get("pax_confidence_floor") == 0.42
    status, body = _http_post(http_server + "/api/settings/reset", None)
    assert status == 200
    assert s.get("pax_confidence_floor") == s.SETTINGS_DEFAULTS["pax_confidence_floor"]


def test_http_post_restore_lkg_endpoint(http_server, s):
    s.apply_settings({"pax_confidence_floor": 0.42})    # creates LKG
    s.apply_settings({"pax_confidence_floor": 0.48})
    status, body = _http_post(http_server + "/api/settings/restore_lkg", None)
    assert status == 200
    assert s.get("pax_confidence_floor") == 0.42


def test_http_audit_history_populated_when_journal_wired(http_server, s, tmp_path):
    """When DashboardHandler.audit_journal is wired, /api/settings returns
    audit rows from the journal."""
    from bookmap_mcp.dashboard import DashboardHandler
    j = Journal(tmp_path / "journal.db")
    j.open()
    j.begin_run(adapter_name="test")
    DashboardHandler.set_audit_journal(j)
    try:
        _http_post(http_server + "/api/settings",
                   {"updates": {"pax_confidence_floor": 0.42}, "user": "test"})
        _, body, _ = _http_get(http_server + "/api/settings")
        payload = json.loads(body)
        fields = [r["field"] for r in payload["audit"]]
        assert "pax_confidence_floor" in fields
    finally:
        DashboardHandler.set_audit_journal(None)
        j.close()


def test_http_get_other_path_404(http_server):
    import urllib.request, urllib.error
    try:
        urllib.request.urlopen(http_server + "/api/does-not-exist", timeout=5)
        assert False, "expected 404"
    except urllib.error.HTTPError as e:
        assert e.code == 404


def test_index_html_has_settings_nav_link(http_server):
    status, body, _ = _http_get(http_server + "/")
    assert status == 200
    assert 'href="/settings"' in body


# ─── S4 polish ──────────────────────────────────────────────────────────────


def test_end_to_end_apply_reset_restorelkg_does_not_touch_pax_weights(http_server, s):
    """Full UI exercise: apply → reset → restore_lkg → reset.
    The pax_weights.json file must not be touched at any point."""
    from bookmap_mcp import dashboard
    weights_path = Path(dashboard._PAX_WEIGHTS_PATH)
    if not weights_path.exists():
        pytest.skip("pax_weights.json not present in this checkout")
    before_mtime = weights_path.stat().st_mtime
    before_bytes = weights_path.read_bytes()

    status, _ = _http_post(http_server + "/api/settings",
                            {"updates": {"pax_confidence_floor": 0.42}})
    assert status == 200
    status, _ = _http_post(http_server + "/api/settings/reset", None)
    assert status == 200
    # apply again so we have something to restore-LKG to
    _http_post(http_server + "/api/settings",
                {"updates": {"pax_confidence_floor": 0.43}})
    status, _ = _http_post(http_server + "/api/settings/restore_lkg", None)
    assert status == 200

    assert weights_path.stat().st_mtime == before_mtime
    assert weights_path.read_bytes() == before_bytes


def test_settings_page_has_banner_auto_clear_logic(http_server):
    """The settings page must auto-clear the success banner after 5s. Look
    for the setTimeout marker since we can't drive JS in a headless test."""
    status, body, _ = _http_get(http_server + "/settings")
    assert status == 200
    assert "setTimeout" in body
    assert "5000" in body


def test_settings_page_lists_audit_panel(http_server):
    status, body, _ = _http_get(http_server + "/settings")
    assert 'id="sec-audit"' in body
    assert 'id="audit-box"' in body


# ─── V2: OR start time / VWAP mean-revert / Volume Profile ──────────────────


def test_v2_defaults_match_pre_settings_constants(s):
    """V2 defaults match the inline constants that lived in dashboard.py
    before we made them tunable."""
    s.load_settings()
    # VWAP mean-revert — matched against the original _vwap_stretch_directional
    # literals (0.3/0.7/1.0 reliabilities; 0.2/0.5/0.8 score magnitudes; 0.6 blend).
    assert s.SETTINGS_DEFAULTS["vwap_inside_band_reliability"]          == 0.3
    assert s.SETTINGS_DEFAULTS["vwap_mean_revert_1_2sigma_score"]       == 0.2
    assert s.SETTINGS_DEFAULTS["vwap_mean_revert_1_2sigma_reliability"] == 0.7
    assert s.SETTINGS_DEFAULTS["vwap_mean_revert_2_3sigma_score"]       == 0.5
    assert s.SETTINGS_DEFAULTS["vwap_mean_revert_2_3sigma_reliability"] == 1.0
    assert s.SETTINGS_DEFAULTS["vwap_mean_revert_3sigma_plus_score"]    == 0.8
    assert s.SETTINGS_DEFAULTS["vwap_composite_stretch_weight"]         == 0.6
    # Volume profile.
    assert s.SETTINGS_DEFAULTS["vp_context_proximity_ticks"] == 5
    assert s.SETTINGS_DEFAULTS["vp_bin_proximity_ticks"]     == 5
    assert s.SETTINGS_DEFAULTS["vp_hvn_score"]               == 0.2
    assert s.SETTINGS_DEFAULTS["vp_far_reliability"]         == 0.3
    assert s.SETTINGS_DEFAULTS["vp_lvn_reliability"]         == 0.5
    assert s.SETTINGS_DEFAULTS["vp_neutral_reliability"]     == 0.5


def test_v2_validation_rejects_vwap_mean_revert_out_of_order(s):
    proposal = dict(s.SETTINGS_DEFAULTS)
    proposal["vwap_mean_revert_1_2sigma_score"] = 0.6
    proposal["vwap_mean_revert_2_3sigma_score"] = 0.5
    proposal["vwap_mean_revert_3sigma_plus_score"] = 0.4
    _, errs = s.validate(proposal)
    assert any("vwap_mean_revert" in e for e in errs)


def test_session_state_uses_hardcoded_literals_not_settings(s):
    """session_state() must NOT consult settings. OR window is owned by the
    OR-Strategy addon, not by this dashboard."""
    from bookmap_mcp import dashboard
    import datetime as dt
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
    s.load_settings()

    # 09:40 → OR_FORMING (between 09:30 and 09:45 ET).
    now = dt.datetime(2026, 5, 18, 9, 40, tzinfo=ET)
    state, _ = dashboard.session_state(now)
    assert state == "OR_FORMING"

    # No session_*_min_et key should exist in settings any more.
    for legacy_key in [
        "session_rth_start_min_et", "session_or_end_min_et",
        "session_late_morning_min_et", "session_chop_start_min_et",
        "session_chop_end_min_et", "session_close_risk_min_et",
        "session_post_market_min_et",
    ]:
        assert legacy_key not in s.SETTINGS_DEFAULTS
        assert legacy_key not in s.SETTINGS_SCHEMA


def test_vwap_stretch_directional_reads_settings(s):
    """Change the 1-2σ score; _vwap_stretch_directional uses the new value."""
    from bookmap_mcp import dashboard
    s.load_settings()
    vwap_obj = {"vwap": 100.0, "stddev": 1.0, "samples": 200}
    # price 101.5 → +1.5σ (1-2σ band)
    score, rel, reason = dashboard._vwap_stretch_directional(vwap_obj, 101.5)
    # Default magnitude 0.2, sign negative because price > vwap (mean-revert).
    assert score == pytest.approx(-0.2)
    assert rel  == pytest.approx(0.7)

    s.apply_settings({"vwap_mean_revert_1_2sigma_score": 0.35,
                       "vwap_mean_revert_1_2sigma_reliability": 0.9})
    score2, rel2, _ = dashboard._vwap_stretch_directional(vwap_obj, 101.5)
    assert score2 == pytest.approx(-0.35)
    assert rel2   == pytest.approx(0.9)


def test_vwap_inside_band_reliability_setting(s):
    """Inside the 1σ band: score 0 with the settings-driven reliability."""
    from bookmap_mcp import dashboard
    s.load_settings()
    vwap_obj = {"vwap": 100.0, "stddev": 1.0, "samples": 200}
    # price 100.5 → +0.5σ → inside band
    _, rel0, _ = dashboard._vwap_stretch_directional(vwap_obj, 100.5)
    assert rel0 == pytest.approx(0.3)

    s.apply_settings({"vwap_inside_band_reliability": 0.55})
    _, rel1, _ = dashboard._vwap_stretch_directional(vwap_obj, 100.5)
    assert rel1 == pytest.approx(0.55)


def test_vp_context_reads_proximity_setting(s):
    """Bumping vp_context_proximity_ticks makes a farther price 'near POC'."""
    from bookmap_mcp import dashboard
    s.load_settings()
    # NQ_TICK = 0.25; default 5 ticks = 1.25 pts.
    vp_obj = {"poc": 100.0, "vah": 110.0, "val": 95.0}
    # Price 102.0 is 2.0 pts away → outside default proximity.
    assert dashboard._vp_context(vp_obj, 102.0) == ""
    # Bump to 10 ticks = 2.50 pts; same price is now "near POC".
    s.apply_settings({"vp_context_proximity_ticks": 10})
    assert "POC" in dashboard._vp_context(vp_obj, 102.0)


def test_vp_at_level_reads_hvn_score_and_proximity(s):
    """HVN bin within proximity returns a bias whose magnitude is the
    settings-driven hvn_score, signed by side."""
    from bookmap_mcp import dashboard
    s.load_settings()
    # Build VP with a clearly-dominant volume bin at 100.0.
    vp_obj = {
        "totalVolume": 1000,
        "levels": [
            {"price": 90.0,  "volume": 10},
            {"price": 95.0,  "volume": 20},
            {"price": 100.0, "volume": 500},   # HVN
            {"price": 105.0, "volume": 30},
            {"price": 110.0, "volume": 15},
        ],
    }
    score_above, _, _ = dashboard._vp_at_level(vp_obj, 100.0, "above")
    score_below, _, _ = dashboard._vp_at_level(vp_obj, 100.0, "below")
    assert score_above == pytest.approx(-0.2)
    assert score_below == pytest.approx(+0.2)

    s.apply_settings({"vp_hvn_score": 0.45})
    score_above2, _, _ = dashboard._vp_at_level(vp_obj, 100.0, "above")
    score_below2, _, _ = dashboard._vp_at_level(vp_obj, 100.0, "below")
    assert score_above2 == pytest.approx(-0.45)
    assert score_below2 == pytest.approx(+0.45)


def test_vp_at_level_far_reliability_setting(s):
    """When nearest bin is beyond proximity, reliability uses
    vp_far_reliability (default 0.3)."""
    from bookmap_mcp import dashboard
    s.load_settings()
    # Bins only at 50 and 150 — far from a query at 100.
    vp_obj = {
        "totalVolume": 100,
        "levels": [
            {"price":  50.0, "volume": 50},
            {"price": 150.0, "volume": 50},
        ],
    }
    _, rel0, _ = dashboard._vp_at_level(vp_obj, 100.0, "above")
    assert rel0 == pytest.approx(0.3)

    s.apply_settings({"vp_far_reliability": 0.05})
    _, rel1, _ = dashboard._vp_at_level(vp_obj, 100.0, "above")
    assert rel1 == pytest.approx(0.05)


def test_vwap_composite_stretch_weight_setting(s):
    """The per-magnet vwap driver blends stretch + or-gate; the weight is
    runtime-tunable. Pick inputs where stretch and gate scores differ so
    the blend weight produces visibly different outputs."""
    from bookmap_mcp import dashboard
    s.load_settings()
    # mid = 101.5 with vwap=100, sigma=1 → dev = +1.5σ → 1-2σ band → stretch
    # score = -0.2 (sign by mean-revert). ALLOW_SHORT gate on an above-side
    # magnet typically returns ~-0.5. With these two distinct magnitudes,
    # a 0.6 vs 0.9 blend produces a measurable composite delta.
    snap = {
        "alias": "T",
        "vwap_obj": {"vwap": 100.0, "stddev": 1.0, "samples": 200},
        "gates": {"vwap_or": {"state": "ALLOW_SHORT"}},
    }
    comp_default = dashboard._level_composite("above", 101.5, 101.5, snap)
    s.apply_settings({"vwap_composite_stretch_weight": 0.9})
    comp_changed = dashboard._level_composite("above", 101.5, 101.5, snap)
    assert comp_default["score"] != comp_changed["score"], (
        f"composite did not respond to blend change: default={comp_default['score']}, "
        f"changed={comp_changed['score']}"
    )


def test_settings_page_has_v2_sections(http_server):
    """Page must expose the new V2 setting controls (no session-window
    section — OR window is owned by the OR-Strategy addon)."""
    status, body, _ = _http_get(http_server + "/settings")
    assert status == 200
    for marker in [
        "sec-vwap_meanrevert", "sec-volume_profile",
        "vwap_mean_revert_1_2sigma_score", "vwap_composite_stretch_weight",
        "vp_context_proximity_ticks", "vp_hvn_score",
        # The OR-Strategy pointer panel must explain where to change OR time.
        "sec-or_strategy_pointer", "OR-Strategy", "OR Strategy",
    ]:
        assert marker in body, f"settings page missing marker {marker}"


def test_settings_page_does_not_expose_session_window_fields(http_server):
    """The 7 session_*_min_et fields were a misleading first attempt and
    must be gone — they were not OR-Strategy control."""
    status, body, _ = _http_get(http_server + "/settings")
    for legacy_marker in [
        "f_session_rth_start_min_et",
        "f_session_or_end_min_et",
        "f_session_late_morning_min_et",
        "session_chop_start_min_et",
        "session_chop_end_min_et",
        "session_close_risk_min_et",
        "session_post_market_min_et",
    ]:
        assert legacy_marker not in body, f"legacy session field still present: {legacy_marker}"


# ─── V3: Java-bridge VWAP / VP runtime config ───────────────────────────────


def test_v3_defaults_match_java_constants(s):
    """V3 defaults match the values that lived in InstrumentState.java
    before they became runtime-mutable."""
    s.load_settings()
    assert s.SETTINGS_DEFAULTS["bridge_rth_open_hhmm_ct"]  == "08:30"
    assert s.SETTINGS_DEFAULTS["bridge_rth_close_hhmm_ct"] == "15:00"
    assert s.SETTINGS_DEFAULTS["bridge_eth_open_hhmm_ct"]  == "17:00"
    assert s.SETTINGS_DEFAULTS["bridge_vp_value_area_pct"] == 0.70


@pytest.mark.parametrize("bad", ["", "9:30", "25:00", "12:60", "12-30", "abc", "noon"])
def test_v3_hhmm_validation_rejects_bad_format(s, bad):
    proposal = dict(s.SETTINGS_DEFAULTS)
    proposal["bridge_rth_open_hhmm_ct"] = bad
    _, errs = s.validate(proposal)
    assert errs, f"expected error for {bad!r}"


def test_v3_validation_rejects_rth_open_at_or_after_close(s):
    proposal = dict(s.SETTINGS_DEFAULTS)
    proposal["bridge_rth_open_hhmm_ct"]  = "15:30"
    proposal["bridge_rth_close_hhmm_ct"] = "15:00"
    _, errs = s.validate(proposal)
    assert any("bridge_rth_open" in e and "bridge_rth_close" in e for e in errs)


def test_v3_validation_rejects_vp_pct_out_of_range(s):
    proposal = dict(s.SETTINGS_DEFAULTS)
    proposal["bridge_vp_value_area_pct"] = 0.0
    _, errs = s.validate(proposal)
    assert any("bridge_vp_value_area_pct" in e for e in errs)
    proposal["bridge_vp_value_area_pct"] = 1.5
    _, errs = s.validate(proposal)
    assert any("bridge_vp_value_area_pct" in e for e in errs)


def test_v3_apply_via_http(http_server, s):
    """Push V3 fields via /api/settings; values land in cache.

    Defaults are CME standards: RTH 08:30 CT open, 15:00 CT close, ETH
    17:00 CT, value area 0.70. This test uses *different* values just to
    verify that an apply actually changes the cache."""
    s.load_settings()
    status, body = _http_post(http_server + "/api/settings",
                               {"updates": {
                                   "bridge_rth_open_hhmm_ct":  "08:00",
                                   "bridge_rth_close_hhmm_ct": "15:30",
                                   "bridge_eth_open_hhmm_ct":  "17:30",
                                   "bridge_vp_value_area_pct": 0.68,
                               }})
    assert status == 200, body
    assert s.get("bridge_rth_open_hhmm_ct")  == "08:00"
    assert s.get("bridge_rth_close_hhmm_ct") == "15:30"
    assert s.get("bridge_eth_open_hhmm_ct")  == "17:30"
    assert s.get("bridge_vp_value_area_pct") == 0.68


def test_v3_settings_page_has_bridge_config_section(http_server):
    """Page must expose the Java bridge config controls."""
    status, body, _ = _http_get(http_server + "/settings")
    for marker in [
        "sec-bridge_config",
        "f_bridge_rth_open_hhmm_ct", "f_bridge_rth_close_hhmm_ct",
        "f_bridge_eth_open_hhmm_ct", "f_bridge_vp_value_area_pct",
        "bookmap-mcp-bridge-v11", "POST /config",
    ]:
        assert marker in body, f"missing marker {marker}"


def test_v3_sync_bridge_config_posts_once_then_caches(s, monkeypatch):
    """_sync_bridge_config should POST on first call and skip on second
    (cache hit within TTL)."""
    from bookmap_mcp import dashboard
    s.load_settings()
    # Reset bridge-config sync cache.
    dashboard._LAST_BRIDGE_CONFIG = None

    posts: List[Tuple[str, Dict[str, str]]] = []

    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post_json(self, path, params):
            posts.append((path, dict(params)))
            return {"ok": True}

    monkeypatch.setattr(dashboard, "BridgeClient", FakeClient)
    fake_cfg = object()

    dashboard._sync_bridge_config(fake_cfg)
    assert len(posts) == 1, posts
    assert posts[0][0] == "/config"
    assert posts[0][1]["rth_open"]          == "08:30"
    assert posts[0][1]["rth_close"]         == "15:00"
    assert posts[0][1]["eth_open"]          == "17:00"
    assert float(posts[0][1]["vp_value_area_pct"]) == pytest.approx(0.70)

    # Second call within TTL: should not POST.
    dashboard._sync_bridge_config(fake_cfg)
    assert len(posts) == 1, posts


def test_v3_sync_bridge_config_repushes_on_settings_change(s, monkeypatch):
    """Changing a bridge setting should trigger a fresh POST even within TTL."""
    from bookmap_mcp import dashboard
    s.load_settings()
    dashboard._LAST_BRIDGE_CONFIG = None
    posts: List[Tuple[str, Dict[str, str]]] = []

    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post_json(self, path, params):
            posts.append((path, dict(params)))
            return {"ok": True}

    monkeypatch.setattr(dashboard, "BridgeClient", FakeClient)
    fake_cfg = object()

    dashboard._sync_bridge_config(fake_cfg)
    assert len(posts) == 1

    s.apply_settings({"bridge_vp_value_area_pct": 0.65})
    dashboard._sync_bridge_config(fake_cfg)
    assert len(posts) == 2
    assert float(posts[1][1]["vp_value_area_pct"]) == pytest.approx(0.65)


def test_v3_sync_bridge_config_swallows_bridge_errors(s, monkeypatch):
    """A bridge failure must not propagate to fetch_snapshot."""
    from bookmap_mcp import dashboard
    s.load_settings()
    dashboard._LAST_BRIDGE_CONFIG = None

    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post_json(self, path, params):
            raise dashboard.BridgeError("simulated network failure")

    monkeypatch.setattr(dashboard, "BridgeClient", FakeClient)
    # Must NOT raise.
    dashboard._sync_bridge_config(object())
    # Cache must NOT have been updated on failure (next call should retry).
    assert dashboard._LAST_BRIDGE_CONFIG is None
