"""pax_ai_config.json loader + hot-reload (mtime watcher).

Mirrors the dashboard's pax_weights.json hot-reload contract:
  * mtime change triggers reload on next get_config() call
  * _-prefixed keys are metadata comments stripped at load
  * dict cleared+updated in place so re-exported references stay valid

Schema (with defaults) is defined in DEFAULTS. JSON file lives at
pax-ai/pax_ai_config.json. Missing file -> defaults in memory; nothing crashes.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any, Dict


CONFIG_PATH = Path(__file__).parent.parent / "pax_ai_config.json"

DEFAULTS: Dict[str, Any] = {
    "models": {
        "live":           "claude-haiku-4-5",
        "deep":           "claude-sonnet-4-6",
        "opus_opt_in":    False,
        "deep_when_opus": "claude-opus-4-7",
    },
    "poll_ms": 1000,                  # snapshot poller cadence (clamped [500, 3000])
    "stale_snapshot_ms": 5000,        # consumers refuse to act past this age
    "prox_ticks": 8,                  # LEVEL_APPROACH trigger threshold (8 ticks ~ 2 NQ pts)
    "tick_size": {                    # per-product tick size (points)
        "NQ":  0.25, "MNQ": 0.25,
        "ES":  0.25, "MES": 0.25,
        "RTY": 0.10, "M2K": 0.10,
        "YM":  1.00, "MYM": 1.00,
    },
    "rung_pts": {                     # per-product extension rung size (points)
        "NQ":  65.0, "MNQ": 65.0,
        "ES":  15.0, "MES": 15.0,
    },
    "payline_pts": {                  # "pay for the trade" target (points)
        "NQ":  10.0, "MNQ": 10.0,
        "ES":   2.5, "MES":  2.5,
    },
    "size_tiers": {
        "FULL_min_confidence": 0.50,
        "HALF_min_confidence": 0.35,
    },
    # Calibrated R-multiple expectation table per (composite_dir, flow_regime, level_kind).
    # All values are MEDIAN R-multiples observed historically; product-of-confidence
    # multipliers in edge_calculus narrow these down to per-snapshot EV.
    #
    # Naming convention from pax-or SKILL.md section 11.4:
    #   * ABSORPTION_BID at OR-L  -> FADE_LONG  (bids absorbing sellers -> rotate up)
    #   * ABSORPTION_ASK at OR-H  -> FADE_SHORT (offers absorbing buyers -> rotate down)
    #   * EXHAUSTION_UP at +N     -> FADE_SHORT (price exhausted up; rotate down)
    #   * EXHAUSTION_DOWN at -N   -> FADE_LONG  (price exhausted down; rotate up)
    #   * TRENDING_UP at OR-H/+N  -> FOLLOW_LONG breakout (ride the trend)
    "directional_R_table": {
        "FOLLOW_LONG.TRENDING_UP.OR_LEVEL":       1.8,
        "FOLLOW_LONG.TRENDING_UP.EXT_LEVEL":      1.6,
        "FADE_LONG.ABSORPTION_BID.OR_LEVEL":      2.4,
        "FOLLOW_SHORT.TRENDING_DOWN.OR_LEVEL":    1.8,
        "FOLLOW_SHORT.TRENDING_DOWN.EXT_LEVEL":   1.6,
        "FADE_SHORT.ABSORPTION_ASK.OR_LEVEL":     2.4,
        "FADE_LONG.EXHAUSTION_DOWN.EXT_LEVEL":    1.6,
        "FADE_SHORT.EXHAUSTION_UP.EXT_LEVEL":     1.6,
        "DEFAULT":                                 1.0,
    },
    "feature_bus": {
        "enabled":           False,
        "db_path":           "D:/BookmapLogs/pax-bus.db",
        "snapshot_blob_dir": "D:/BookmapLogs/pax-snapshots",
        "digest_blob_dir":   "D:/BookmapLogs/pax-digests",
        "queue_max":         2000,
        "writer_idle_ms":    100,
        "capture_ms":        1000,
        "retention_days":    30,
    },
    "chat": {
        "use_feature_bus_digest": False,    # Phase 3A: swap legacy digest -> bus digest at Claude input
    },
    "outcomes": {
        "enabled":            False,   # Phase 4A daemon. Default OFF.
        "wake_interval_ms":   900_000, # 15 minutes
        "match_tolerance_ms": 5_000,   # +/- 5 seconds when finding snapshot_features rows
    },
    "forecast": {
        # Self-training research loop forecast capture. Default OFF;
        # operator opts in once the prompt contract has been rolled out
        # and validated.  When True, chat.py extracts and persists a
        # structured <<PAX_FORECAST>> block from each Claude response.
        # Persistence failures (missing mcp-server install, disk error,
        # bad JSON) are silent and never break the chat path.
        "enabled":    False,
        "store_path": "D:/BookmapLogs/pax-forecast.db",
    },
    "trigger_engine": {
        # Background daemon that auto-fires Pax AI on snapshot triggers
        # (LEVEL_APPROACH / TREND_SIGNAL_FIRE / etc.) so the trader does
        # not have to type at a chat box during the session. Each fire
        # is one Claude call -- HARD caps prevent cost runaway.
        # Default OFF; restart Pax-AI after flipping.
        "enabled":                 False,
        "min_global_interval_sec": 30,     # max ~120 fires / hr globally
        "cooldown_per_kind_sec":   120,    # same (kind,label) fires <=1/2min
        "tick_interval_sec":       1.0,    # how often we poll for triggers
        "deep":                    False,  # haiku, not sonnet/opus
        "fireable_kinds": [                # the rest are stand-down chips
            "LEVEL_APPROACH",
            "TREND_SIGNAL_FIRE",
            "CONVICTION_FLIP",
            "REGIME_CHANGE",
            "MICRO_EVENT",
        ],
    },
}


_LOCK = threading.Lock()
_CACHE: Dict[str, Any] = dict(DEFAULTS)        # always populated
_LOADED_MTIME: float = 0.0


def _strip_meta(d: Dict[str, Any]) -> Dict[str, Any]:
    """Drop _-prefixed keys (treated as comments)."""
    if not isinstance(d, dict):
        return d
    return {k: _strip_meta(v) for k, v in d.items() if not k.startswith("_")}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Override wins; nested dicts merge recursively. Returns NEW dict."""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _reload_if_stale() -> None:
    global _LOADED_MTIME
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except FileNotFoundError:
        return  # keep defaults in cache
    if mtime == _LOADED_MTIME:
        return
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"[config] pax_ai_config.json reload failed: {exc}\n")
        return
    merged = _deep_merge(DEFAULTS, _strip_meta(raw))
    with _LOCK:
        _CACHE.clear()
        _CACHE.update(merged)
        _LOADED_MTIME = mtime


def get_config() -> Dict[str, Any]:
    """Return the current config dict. Hot-reloads on mtime change."""
    _reload_if_stale()
    with _LOCK:
        # Shallow copy is fine - consumers should treat the result as read-only.
        return dict(_CACHE)


def get(path: str, default: Any = None) -> Any:
    """Dotted-path getter against the current config.

    Example: get("models.live")  ->  "claude-haiku-4-5"
    Returns `default` if any segment is missing.
    """
    cfg = get_config()
    cur: Any = cfg
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur
