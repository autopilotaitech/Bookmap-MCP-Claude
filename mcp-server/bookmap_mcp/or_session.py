"""OpenRange session-config bridge.

The OpenRange Bookmap indicator owns the OR anchor (start time, range
seconds, line end, timezone). It publishes its effective settings to a
local JSON file so the Python dashboard + Java bridge can use the same
anchor for VWAP, Volume Profile, conviction reset, opening-drive logic,
Heatwave display, and trade-decision gating.

File path (in priority order):
  $BOOKMAP_OR_SESSION_CONFIG  (override)
  C:\\BookmapLogs\\or-session-config.json
  D:\\BookmapLogs\\or-session-config.json
  $TEMP\\openrange\\or-session-config.json    (fallback dev path)

JSON schema (v1):
  {
    "version":      1,
    "updatedAtMs":  1779228000000,
    "timezone":     "America/Chicago",
    "startHour":    8,
    "startMinute":  30,
    "startSecond":  0,
    "rangeSeconds": 30,
    "endHour":      8,
    "endMinute":    30,
    "labelPrefix":  "OpenRange",
    "daysToDisplay": 8,
    "source":       "openrange-indicator"
  }

Read semantics:
  - load_effective() returns either {"available": True, ...} with the
    parsed config OR {"available": False, "reason": "...", "fallback": {...}}
    when no file exists / file is unreadable / file is stale.
  - "stale" means updatedAtMs is older than _STALE_MS (24h by default) — a
    Bookmap restart should refresh it within seconds of attaching.
  - Fallback values match the canonical CME RTH open (08:30 CT, 30s OR).
    Production should never run on fallback once the OpenRange indicator
    has been attached at least once.

Thread safety: a single module-level lock guards the in-memory cache.
load_effective() is safe to call from any dashboard thread.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional


_LOCK = threading.Lock()
_CACHE: Optional[Dict[str, Any]] = None
_CACHE_MTIME: float = 0.0
_CACHE_PATH: Optional[Path] = None

_STALE_MS = 24 * 60 * 60 * 1000  # 24h

# Fallback values match historical CME RTH open. Used ONLY when no OR
# indicator has ever published. NEVER hard-code these into decision logic
# — read effective_session_anchor() which reports whether the value is
# operator-controlled or fallback.
_FALLBACK = {
    "version":       1,
    "updatedAtMs":   0,
    "timezone":      "America/Chicago",
    "startHour":     8,
    "startMinute":   30,
    "startSecond":   0,
    "rangeSeconds":  30,
    "endHour":       8,
    "endMinute":     30,
    "labelPrefix":   "OpenRange",
    "daysToDisplay": 8,
    "source":        "fallback-no-indicator-config",
}


def candidate_paths() -> list[Path]:
    paths: list[Path] = []
    override = os.environ.get("BOOKMAP_OR_SESSION_CONFIG")
    if override:
        paths.append(Path(override))
    paths.append(Path("C:/BookmapLogs/or-session-config.json"))
    paths.append(Path("D:/BookmapLogs/or-session-config.json"))
    paths.append(Path(os.environ.get("TEMP", "/tmp")) / "openrange" / "or-session-config.json")
    return paths


def _read_first_existing() -> tuple[Optional[Dict[str, Any]], Optional[Path],
                                    Optional[str], list[tuple[Path, str]]]:
    """Return the freshest VALIDATED config across all candidate paths.

    For each candidate: read → parse → validate → coerce updatedAtMs. Only
    candidates that pass validation are eligible for the "freshest" pick;
    invalid candidates are recorded in the rejections list so consumers
    can audit why a particular file lost the race.

    Returns (best_parsed, best_path, last_error_or_None, rejections).
    rejections is a list of (path, reason) for every candidate that
    parsed but failed validation OR raised on read.
    """
    best_parsed: Optional[Dict[str, Any]] = None
    best_path: Optional[Path] = None
    best_updated_at: int = -1
    rejections: list[tuple[Path, str]] = []
    last_error: Optional[str] = None
    for p in candidate_paths():
        try:
            if not p.is_file():
                continue
        except OSError:
            continue
        # Phase 1 — read + parse.
        try:
            txt = p.read_text(encoding="utf-8")
            parsed = json.loads(txt)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            msg = f"{type(exc).__name__}: {exc}"
            rejections.append((p, msg))
            last_error = msg
            continue
        if not isinstance(parsed, dict):
            msg = f"root is {type(parsed).__name__}, not object"
            rejections.append((p, msg))
            last_error = msg
            continue
        # Phase 2 — validate. Reject invalid candidates entirely; they
        # never win the freshest-valid race even if updatedAtMs is large.
        vmsg = _validate(parsed)
        if vmsg is not None:
            rejections.append((p, f"invalid: {vmsg}"))
            last_error = vmsg
            continue
        # Phase 3 — track the freshest VALID.
        try:
            updated = int(parsed.get("updatedAtMs") or 0)
        except (TypeError, ValueError):
            updated = 0
        if updated > best_updated_at:
            best_updated_at = updated
            best_parsed = parsed
            best_path = p
    return best_parsed, best_path, last_error, rejections


def _validate(cfg: Dict[str, Any]) -> Optional[str]:
    """Return None on success, error string on failure."""
    required = ("version", "timezone", "startHour", "startMinute",
                "rangeSeconds", "endHour", "endMinute")
    for k in required:
        if k not in cfg:
            return f"missing required key '{k}'"
    if not isinstance(cfg.get("startHour"), int) or not (0 <= cfg["startHour"] <= 23):
        return "startHour out of range"
    if not isinstance(cfg.get("startMinute"), int) or not (0 <= cfg["startMinute"] <= 59):
        return "startMinute out of range"
    if not isinstance(cfg.get("rangeSeconds"), int) or not (1 <= cfg["rangeSeconds"] <= 86400):
        return "rangeSeconds out of range"
    if not isinstance(cfg.get("endHour"), int) or not (0 <= cfg["endHour"] <= 23):
        return "endHour out of range"
    if not isinstance(cfg.get("endMinute"), int) or not (0 <= cfg["endMinute"] <= 59):
        return "endMinute out of range"
    if not isinstance(cfg.get("timezone"), str) or not cfg["timezone"]:
        return "timezone must be a non-empty string"
    return None


def load_effective(now_ms: Optional[int] = None) -> Dict[str, Any]:
    """Return the effective OR session config.

    Result shape:
        {
          "available": bool,            # True only if a fresh+valid config exists
          "config":    dict,            # always present (real or fallback)
          "source":    "or_config" | "fallback" | "stale_or_config",
          "anchorMode":                 # explicit policy mode:
              "LIVE"               — fresh+valid operator config (available=True)
              "LAST_KNOWN_STALE"   — valid config but updatedAtMs is stale
              "FALLBACK"           — no valid candidate; using built-in defaults
          "path":      Optional[str],
          "reason":    Optional[str],   # short summary when not LIVE
          "ageMs":     Optional[int],   # age of selected config (None if FALLBACK)
          "updatedAtMs": Optional[int], # publish timestamp of selected config
          "rejections": list[dict],     # per-candidate rejection diagnostics
        }
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    global _CACHE, _CACHE_PATH
    with _LOCK:
        parsed, path, err, rejections = _read_first_existing()
        rej_dicts = [{"path": str(p), "reason": r} for p, r in rejections]
        if parsed is None:
            return {
                "available":   False,
                "config":      dict(_FALLBACK),
                "source":      "fallback",
                "anchorMode":  "FALLBACK",
                "path":        None,
                "reason":      err or "no valid or-session-config.json in any candidate path",
                "ageMs":       None,
                "updatedAtMs": None,
                "rejections":  rej_dicts,
            }
        # parsed has already been validated by _read_first_existing.
        updated_at = int(parsed.get("updatedAtMs") or 0)
        age_ms = max(0, now_ms - updated_at) if updated_at > 0 else None
        if updated_at <= 0 or (age_ms is not None and age_ms > _STALE_MS):
            return {
                "available":   False,
                "config":      parsed,
                "source":      "stale_or_config",
                "anchorMode":  "LAST_KNOWN_STALE",
                "path":        str(path),
                "reason":      f"updatedAtMs is stale (age {age_ms}ms)"
                                if age_ms is not None
                                else "no updatedAtMs",
                "ageMs":       age_ms,
                "updatedAtMs": updated_at,
                "rejections":  rej_dicts,
            }
        _CACHE      = parsed
        _CACHE_PATH = path
        return {
            "available":   True,
            "config":      parsed,
            "source":      "or_config",
            "anchorMode":  "LIVE",
            "path":        str(path),
            "reason":      None,
            "ageMs":       age_ms,
            "updatedAtMs": updated_at,
            "rejections":  rej_dicts,
        }


def effective_session_anchor(now_ms: Optional[int] = None) -> Dict[str, Any]:
    """Convenience: return the effective session anchor as a dict the
    dashboard's session gate / bridge sync can consume directly. Carries
    `anchorMode` so consumers can fail safe under LAST_KNOWN_STALE or
    FALLBACK without re-deriving from `available` + `source`.
    """
    eff = load_effective(now_ms)
    cfg = eff["config"]
    return {
        "hour":         int(cfg["startHour"]),
        "minute":       int(cfg["startMinute"]),
        "second":       int(cfg.get("startSecond", 0)),
        "rangeSeconds": int(cfg["rangeSeconds"]),
        "timezone":     str(cfg["timezone"]),
        "source":       eff["source"],
        "anchorMode":   eff["anchorMode"],
        "available":    eff["available"],
        "reason":       eff["reason"],
        "ageMs":        eff["ageMs"],
        "updatedAtMs":  eff["updatedAtMs"],
        "path":         eff["path"],
    }


def _coerce_time_obj(anchor: Dict[str, Any]) -> dt.time:
    return dt.time(int(anchor["hour"]), int(anchor["minute"]), int(anchor.get("second", 0)))


def time_obj_or_fallback() -> dt.time:
    """Shorthand: return a datetime.time for the effective OR session
    anchor (or fallback). Useful for code that just needs the HH:MM."""
    return _coerce_time_obj(effective_session_anchor())
