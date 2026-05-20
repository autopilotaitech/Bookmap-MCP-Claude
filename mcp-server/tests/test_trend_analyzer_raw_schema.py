"""Tests proving the trend_analyzer raw payload schema is stable across
every state (MISSING / ERROR / WARMING / STALE / LIVE)."""
from __future__ import annotations

import time

from bookmap_mcp import dashboard


# Keys that MUST be present in raw across every state.
_RAW_SCHEMA_KEYS = {
    "status", "_present", "warmedUp", "score",
    "fastDirection", "slowDirection",
    "fastSign", "slowSign", "fastConf", "slowConf",
    "fastChop", "slowChop",
    "reliabilityHint", "ageSec",
    "eventMs", "updatedAtMs", "error",
}


def _raw(snap_ta):
    result = dashboard._source_trend_analyzer({"trend_analyzer": snap_ta})
    raw = result["raw"]
    missing = _RAW_SCHEMA_KEYS - set(raw.keys())
    assert not missing, f"raw missing keys: {missing}"
    extra = set(raw.keys()) - _RAW_SCHEMA_KEYS
    assert not extra, f"raw has extra keys: {extra}"
    return result, raw


def test_missing_payload_status_and_schema():
    result = dashboard._source_trend_analyzer({})
    raw = result["raw"]
    assert raw["status"] == "MISSING"
    assert result["score"] == 0.0
    assert result["reliability"] == 0.0
    assert _RAW_SCHEMA_KEYS == set(raw.keys())


def test_error_payload_status():
    _, raw = _raw({"_error": "bridge timeout"})
    assert raw["status"] == "ERROR"
    assert raw["error"] == "bridge timeout"


def test_warming_status_carries_partial_fields():
    _, raw = _raw({
        "warmedUp": False,
        "fast": {"direction": "UP", "directionSign": 1, "confidence": 30},
        "slow": {"direction": "FLAT", "directionSign": 0, "confidence": 10},
        "updatedAtMs": int(time.time() * 1000),
        "eventMs": 1779228000000,
    })
    assert raw["status"] == "WARMING"
    assert raw["fastDirection"] == "UP"
    assert raw["slowDirection"] == "FLAT"
    assert raw["score"] == 0.0


def test_stale_status_when_old():
    _, raw = _raw({
        "warmedUp": True,
        "fast": {"direction": "UP", "directionSign": 1, "confidence": 80},
        "slow": {"direction": "UP", "directionSign": 1, "confidence": 70},
        "updatedAtMs": int(time.time() * 1000) - 60_000,   # 60s old
        "eventMs": 1779228000000,
    })
    assert raw["status"] == "STALE"
    assert raw["ageSec"] is not None and raw["ageSec"] >= 30.0


def test_live_status_carries_full_payload():
    now_ms = int(time.time() * 1000)
    result, raw = _raw({
        "warmedUp": True,
        "fast": {"direction": "UP", "directionSign": 1, "confidence": 82},
        "slow": {"direction": "UP", "directionSign": 1, "confidence": 76},
        "updatedAtMs": now_ms,
        "eventMs": 1779228000000,
        "reliabilityHint": "engines aligned",
    })
    assert raw["status"] == "LIVE"
    assert raw["fastDirection"] == "UP"
    assert raw["slowDirection"] == "UP"
    assert raw["fastConf"] == 82
    assert raw["slowConf"] == 76
    assert raw["reliabilityHint"] == "engines aligned"
    assert result["score"] > 0.5
    assert result["reliability"] == 1.0


def test_raw_schema_is_identical_across_all_states():
    """All five states must produce raw dicts with the same key set."""
    states = []
    states.append(dashboard._source_trend_analyzer({}))                                    # MISSING
    states.append(dashboard._source_trend_analyzer({"trend_analyzer": {"_error": "x"}}))  # ERROR
    states.append(dashboard._source_trend_analyzer({
        "trend_analyzer": {"warmedUp": False, "fast": {}, "slow": {},
                            "updatedAtMs": int(time.time() * 1000)},
    }))                                                                                    # WARMING
    states.append(dashboard._source_trend_analyzer({
        "trend_analyzer": {"warmedUp": True, "fast": {"directionSign": 1, "confidence": 50},
                            "slow": {"directionSign": 1, "confidence": 50},
                            "updatedAtMs": int(time.time() * 1000) - 60_000},
    }))                                                                                    # STALE
    states.append(dashboard._source_trend_analyzer({
        "trend_analyzer": {"warmedUp": True, "fast": {"directionSign": 1, "confidence": 80},
                            "slow": {"directionSign": 1, "confidence": 70},
                            "updatedAtMs": int(time.time() * 1000)},
    }))                                                                                    # LIVE

    key_sets = [set(s["raw"].keys()) for s in states]
    for ks in key_sets:
        assert ks == _RAW_SCHEMA_KEYS, ks
