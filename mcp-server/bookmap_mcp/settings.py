"""Dashboard-side runtime settings.

Hot-reloadable subset (~20 keys) of trading-model tunables that previously
lived as hard-coded constants in dashboard.py / sim_engine.py. Persisted in
pax_settings.json next to this file; audited to pax-journal.db on every
change. Fails closed to a last-known-good file on validation error.

Design contract:
  - Defaults match the original constants exactly (regression guard, test 1).
  - `_SETTINGS_CACHE` is mutated in place via clear()+update() so any
    consumer holding a reference sees the latest values (mirrors the
    _PAX_WEIGHTS_CACHE pattern in dashboard.py).
  - Read at compute-time via `get(key)`. Module-level constants in
    dashboard.py remain as the authoritative defaults.
  - `pax_weights.json` is NEVER touched by anything in this module.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


_SETTINGS_PATH = Path(__file__).parent / "pax_settings.json"
_LKG_PATH = Path(__file__).parent / "pax_settings.last_good.json"


# ─── defaults ────────────────────────────────────────────────────────────────
#
# These MUST match the original constants in dashboard.py / sim_engine.py.
# test_defaults_match_current_constants pins this.

SETTINGS_DEFAULTS: Dict[str, Any] = {
    # General
    "magnet_refresh_secs":              60.0,
    "eod_close_hour_ct":                15,

    # Pax decision / risk gates
    "pax_confidence_floor":             0.55,
    "pax_confidence_full":              0.70,
    "pax_min_or_width_pts":             3.0,
    "pax_max_or_width_pts":             25.0,
    "pax_bias_agreement_boost":         0.10,

    # Level composite (per-magnet)
    "level_weights": {
        "pull_stack":          0.22,
        "tape":                0.18,
        "micro":               0.16,
        "lt_liquidity":        0.12,
        "orderbook":           0.12,
        "vwap":                0.08,
        "volume_profile":      0.07,
        "session_conviction":  0.05,
    },
    "level_directional_threshold":      0.20,
    "level_thin_coverage_frac":         0.30,

    # Tape flow
    "tape_bucket_weights": {
        "1-10":   0.10,
        "11-25":  0.20,
        "26-50":  0.40,
        "51-99":  0.80,
        "100+":   1.00,
    },
    "tape_thin_floor_prints":           5,
    "tape_thin_hedge_prints":           15,
    "tape_align_bonus":                 0.10,
    "tape_align_threshold":             0.25,

    # VWAP stretch penalties (FOLLOW signals only)
    "vwap_stretch_penalty_1_2sigma":    -0.15,
    "vwap_stretch_penalty_2_3sigma":    -0.35,
    "vwap_stretch_penalty_3sigma_plus": -0.60,

    # NOTE: session-window times are NOT here. The OR window is owned by the
    # OR-Strategy Bookmap addon (orStartHour/Minute/Seconds in its
    # @StrategySettingsVersion settings) — change it inside Bookmap's OR
    # Strategy panel. session_state() in dashboard.py uses fixed ET labels
    # for HUD/Pax gating only; those are dashboard-internal and not tunable.

    # ─── V2: VWAP mean-revert directional driver ────────────────────────
    # _vwap_stretch_directional() in dashboard.py — the per-magnet vwap
    # composite driver (sigma-band magnitudes + reliabilities + the blend
    # between stretch and vwap_or gate).
    "vwap_inside_band_reliability":          0.3,
    "vwap_mean_revert_1_2sigma_score":       0.2,
    "vwap_mean_revert_1_2sigma_reliability": 0.7,
    "vwap_mean_revert_2_3sigma_score":       0.5,
    "vwap_mean_revert_2_3sigma_reliability": 1.0,
    "vwap_mean_revert_3sigma_plus_score":    0.8,
    "vwap_composite_stretch_weight":         0.6,   # gate weight = 1 - this

    # ─── V2: Volume Profile at-level driver ─────────────────────────────
    # _vp_context() and _vp_at_level() in dashboard.py — POC/VAH/VAL
    # proximity in ticks, HVN magnitude, LVN / far / neutral reliabilities.
    # NQ_TICK = 0.25 so "5 ticks" = 1.25 pts.
    "vp_context_proximity_ticks":  5,
    "vp_bin_proximity_ticks":      5,
    "vp_hvn_score":                0.2,
    "vp_far_reliability":          0.3,
    "vp_lvn_reliability":          0.5,
    "vp_neutral_reliability":      0.5,

    # ─── V3: Java-bridge runtime config ─────────────────────────────────
    # Pushed to the bridge's POST /config endpoint by _sync_bridge_config()
    # in dashboard.py. The bridge's session anchor (rth_open) is published
    # from the OpenRange UI via or_session.effective_session_anchor() —
    # not a dashboard setting. Only vp_value_area_pct is mutable here.
    "bridge_vp_value_area_pct":   0.70,
}


# Canonical key sets for compound fields. Reject extra/missing sub-keys.
_LEVEL_WEIGHT_KEYS = frozenset(SETTINGS_DEFAULTS["level_weights"].keys())
_TAPE_BUCKET_KEYS = frozenset(SETTINGS_DEFAULTS["tape_bucket_weights"].keys())


# ─── schema ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FieldSpec:
    """Per-field declaration: type, allowed range, optional custom validator.

    Numeric fields use (min, max) inclusive. ``allow_none=True`` is a special
    case for ``eod_close_hour_ct`` which accepts either an int hour or None
    (auto-flatten disabled). ``hhmm`` accepts a strict ``HH:MM`` string
    (used for Java-side VWAP anchor times configured via the bridge).
    """
    kind: str                       # 'float' | 'int' | 'dict_float' | 'hhmm'
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    allow_none: bool = False
    sub_keys: Optional[frozenset] = None   # for dict_float fields
    sub_min: Optional[float] = None
    sub_max: Optional[float] = None


SETTINGS_SCHEMA: Dict[str, FieldSpec] = {
    "magnet_refresh_secs":              FieldSpec("float", minimum=0.5,  maximum=86400.0),
    "eod_close_hour_ct":                FieldSpec("int",   minimum=0,    maximum=23, allow_none=True),
    "pax_confidence_floor":             FieldSpec("float", minimum=0.0,  maximum=1.0),
    "pax_confidence_full":              FieldSpec("float", minimum=0.0,  maximum=1.0),
    "pax_min_or_width_pts":             FieldSpec("float", minimum=0.01, maximum=1000.0),
    "pax_max_or_width_pts":             FieldSpec("float", minimum=0.01, maximum=1000.0),
    "pax_bias_agreement_boost":         FieldSpec("float", minimum=0.0,  maximum=1.0),
    "level_weights":                    FieldSpec("dict_float",
                                                  sub_keys=_LEVEL_WEIGHT_KEYS,
                                                  sub_min=0.0, sub_max=1.0),
    "level_directional_threshold":      FieldSpec("float", minimum=0.0,  maximum=1.0),
    "level_thin_coverage_frac":         FieldSpec("float", minimum=0.0,  maximum=1.0),
    "tape_bucket_weights":              FieldSpec("dict_float",
                                                  sub_keys=_TAPE_BUCKET_KEYS,
                                                  sub_min=0.0, sub_max=1.0),
    "tape_thin_floor_prints":           FieldSpec("int",   minimum=0,    maximum=10000),
    "tape_thin_hedge_prints":           FieldSpec("int",   minimum=0,    maximum=10000),
    "tape_align_bonus":                 FieldSpec("float", minimum=0.0,  maximum=1.0),
    "tape_align_threshold":             FieldSpec("float", minimum=0.0,  maximum=1.0),
    "vwap_stretch_penalty_1_2sigma":    FieldSpec("float", minimum=-1.0, maximum=0.0),
    "vwap_stretch_penalty_2_3sigma":    FieldSpec("float", minimum=-1.0, maximum=0.0),
    "vwap_stretch_penalty_3sigma_plus": FieldSpec("float", minimum=-1.0, maximum=0.0),

    # V2: VWAP mean-revert
    "vwap_inside_band_reliability":          FieldSpec("float", minimum=0.0, maximum=1.0),
    "vwap_mean_revert_1_2sigma_score":       FieldSpec("float", minimum=0.0, maximum=1.0),
    "vwap_mean_revert_1_2sigma_reliability": FieldSpec("float", minimum=0.0, maximum=1.0),
    "vwap_mean_revert_2_3sigma_score":       FieldSpec("float", minimum=0.0, maximum=1.0),
    "vwap_mean_revert_2_3sigma_reliability": FieldSpec("float", minimum=0.0, maximum=1.0),
    "vwap_mean_revert_3sigma_plus_score":    FieldSpec("float", minimum=0.0, maximum=1.0),
    "vwap_composite_stretch_weight":         FieldSpec("float", minimum=0.0, maximum=1.0),

    # V2: Volume Profile
    "vp_context_proximity_ticks": FieldSpec("int",   minimum=0,   maximum=10000),
    "vp_bin_proximity_ticks":     FieldSpec("int",   minimum=0,   maximum=10000),
    "vp_hvn_score":               FieldSpec("float", minimum=0.0, maximum=1.0),
    "vp_far_reliability":         FieldSpec("float", minimum=0.0, maximum=1.0),
    "vp_lvn_reliability":         FieldSpec("float", minimum=0.0, maximum=1.0),
    "vp_neutral_reliability":     FieldSpec("float", minimum=0.0, maximum=1.0),

    # V3: Java-bridge runtime config (session anchor comes from OR UI)
    "bridge_vp_value_area_pct": FieldSpec("float", minimum=0.01, maximum=1.0),
}


_HHMM_RE = None
def _parse_hhmm(s: Any) -> Tuple[Optional[Tuple[int, int]], Optional[str]]:
    """Validate ``HH:MM`` string. Returns ((hour, minute), None) on success
    or (None, error_message)."""
    import re
    global _HHMM_RE
    if _HHMM_RE is None:
        # Strict 2-digit HH:MM. Matches HTML5 <input type="time"> output and
        # Java's LocalTime.parse(). Bare "9:30" is rejected; use "09:30".
        _HHMM_RE = re.compile(r"^(\d{2}):(\d{2})$")
    if not isinstance(s, str):
        return None, f"expected HH:MM string, got {type(s).__name__}"
    m = _HHMM_RE.match(s.strip())
    if not m:
        return None, f"expected HH:MM, got {s!r}"
    h, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mm <= 59):
        return None, f"HH:MM out of range: {s!r}"
    return (h, mm), None


# ─── state ───────────────────────────────────────────────────────────────────


_SETTINGS_CACHE: Dict[str, Any] = {}     # mutated in place; identity preserved
_SETTINGS_MTIME: float = 0.0
_SETTINGS_SOURCE: str = ""               # 'defaults' / 'file' / 'lkg' / 'ui'
_SETTINGS_LAST_APPLIED_MS: int = 0
_LOAD_LOCK = threading.Lock()


# Audit-sink hook. The dashboard wires this to the live Journal at startup.
# Signature: (rows: list[dict]) -> None.
_audit_sink: Optional[Callable[[List[Dict[str, Any]]], None]] = None
# Event-sink hook for SETTINGS_REVERT_LKG / SETTINGS_REVERT_DEFAULTS.
# Signature: (kind, source, message, payload) -> None.
_event_sink: Optional[Callable[[str, str, str, Optional[Dict[str, Any]]], None]] = None


def set_audit_sink(sink: Optional[Callable[[List[Dict[str, Any]]], None]]) -> None:
    """Wire an audit sink at daemon startup. ``None`` clears it."""
    global _audit_sink
    _audit_sink = sink


def set_event_sink(sink: Optional[Callable[[str, str, str, Optional[Dict[str, Any]]], None]]) -> None:
    """Wire an event sink at daemon startup. ``None`` clears it."""
    global _event_sink
    _event_sink = sink


# ─── validation ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ApplyResult:
    ok: bool
    applied: Dict[str, Any]
    errors: List[str]
    audit_rows: List[Dict[str, Any]]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _coerce_scalar(kind: str, value: Any, allow_none: bool) -> Any:
    """Cast incoming JSON values to the expected scalar type. Returns the
    casted value or raises ValueError with a human-readable message."""
    if value is None:
        if allow_none:
            return None
        raise ValueError("null not allowed")
    if kind == "float":
        return float(value)
    if kind == "int":
        if isinstance(value, bool):
            raise ValueError("bool not accepted as int")
        if isinstance(value, float) and not value.is_integer():
            raise ValueError(f"non-integer float {value}")
        return int(value)
    raise ValueError(f"unknown kind {kind}")


def _validate_field(key: str, value: Any, spec: FieldSpec) -> Tuple[Optional[Any], List[str]]:
    """Validate a single proposed field value. Returns (coerced_value, errors).
    A non-empty errors list means the value is rejected."""
    errors: List[str] = []
    if spec.kind == "hhmm":
        parsed, err = _parse_hhmm(value)
        if err:
            return None, [f"{key}: {err}"]
        return value.strip(), []
    if spec.kind == "dict_float":
        if not isinstance(value, dict):
            return None, [f"{key}: expected object, got {type(value).__name__}"]
        if spec.sub_keys is not None:
            extra = set(value.keys()) - spec.sub_keys
            missing = spec.sub_keys - set(value.keys())
            if extra:
                errors.append(f"{key}: unexpected sub-keys {sorted(extra)}")
            if missing:
                errors.append(f"{key}: missing sub-keys {sorted(missing)}")
        coerced: Dict[str, float] = {}
        for sk, sv in value.items():
            try:
                fv = float(sv)
            except (TypeError, ValueError):
                errors.append(f"{key}.{sk}: not a number ({sv!r})")
                continue
            if spec.sub_min is not None and fv < spec.sub_min:
                errors.append(f"{key}.{sk}: {fv} < min {spec.sub_min}")
            if spec.sub_max is not None and fv > spec.sub_max:
                errors.append(f"{key}.{sk}: {fv} > max {spec.sub_max}")
            coerced[sk] = fv
        if errors:
            return None, errors
        return coerced, errors
    try:
        coerced_val = _coerce_scalar(spec.kind, value, spec.allow_none)
    except (TypeError, ValueError) as exc:
        return None, [f"{key}: {exc}"]
    if coerced_val is None:
        return None, errors
    if spec.minimum is not None and coerced_val < spec.minimum:
        errors.append(f"{key}: {coerced_val} < min {spec.minimum}")
    if spec.maximum is not None and coerced_val > spec.maximum:
        errors.append(f"{key}: {coerced_val} > max {spec.maximum}")
    if errors:
        return None, errors
    return coerced_val, errors


def _cross_field_errors(merged: Dict[str, Any]) -> List[str]:
    """Cross-field invariants. Run AFTER every field has passed per-field
    validation so we can trust the types."""
    errors: List[str] = []
    if merged["pax_min_or_width_pts"] >= merged["pax_max_or_width_pts"]:
        errors.append(
            "pax_min_or_width_pts must be < pax_max_or_width_pts "
            f"(got {merged['pax_min_or_width_pts']} >= {merged['pax_max_or_width_pts']})"
        )
    if merged["tape_thin_floor_prints"] > merged["tape_thin_hedge_prints"]:
        errors.append(
            "tape_thin_floor_prints must be <= tape_thin_hedge_prints "
            f"(got {merged['tape_thin_floor_prints']} > {merged['tape_thin_hedge_prints']})"
        )
    if merged["pax_confidence_floor"] > merged["pax_confidence_full"]:
        errors.append(
            "pax_confidence_floor must be <= pax_confidence_full "
            f"(got {merged['pax_confidence_floor']} > {merged['pax_confidence_full']})"
        )
    p12 = merged["vwap_stretch_penalty_1_2sigma"]
    p23 = merged["vwap_stretch_penalty_2_3sigma"]
    p3p = merged["vwap_stretch_penalty_3sigma_plus"]
    if not (p3p <= p23 <= p12 <= 0):
        errors.append(
            "vwap_stretch penalties must satisfy 3sigma_plus <= 2_3sigma <= 1_2sigma <= 0 "
            f"(got {p3p}, {p23}, {p12})"
        )
    if sum(merged["level_weights"].values()) <= 0:
        errors.append("level_weights: sum must be > 0")
    if sum(merged["tape_bucket_weights"].values()) <= 0:
        errors.append("tape_bucket_weights: sum must be > 0")
    # V2: VWAP mean-revert magnitudes must be non-decreasing across bands.
    m12 = merged["vwap_mean_revert_1_2sigma_score"]
    m23 = merged["vwap_mean_revert_2_3sigma_score"]
    m3p = merged["vwap_mean_revert_3sigma_plus_score"]
    if not (m12 <= m23 <= m3p):
        errors.append(
            "vwap_mean_revert scores must satisfy 1_2 <= 2_3 <= 3_plus "
            f"(got {m12}, {m23}, {m3p})"
        )
    # V3: bridge session anchor is sourced from the OR UI (or_session),
    # not from these settings. Nothing to cross-validate here.
    return errors


def validate(values: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Validate a *complete* settings dict (all keys present). Returns the
    coerced dict and a list of error strings (empty = valid)."""
    coerced: Dict[str, Any] = {}
    errors: List[str] = []
    for key, spec in SETTINGS_SCHEMA.items():
        if key not in values:
            errors.append(f"{key}: missing")
            continue
        cv, errs = _validate_field(key, values[key], spec)
        if errs:
            errors.extend(errs)
        else:
            coerced[key] = cv
    extra = set(values.keys()) - set(SETTINGS_SCHEMA.keys())
    extra_known_metadata = {k for k in extra if k.startswith("_")}
    extra_unknown = extra - extra_known_metadata
    if extra_unknown:
        errors.append(f"unknown keys: {sorted(extra_unknown)}")
    if not errors:
        errors.extend(_cross_field_errors(coerced))
    return coerced, errors


# ─── load / persistence ──────────────────────────────────────────────────────


def _strip_meta(d: Any) -> Any:
    """Strip top-level ``_*`` metadata keys so they cannot leak into validation."""
    if isinstance(d, dict):
        return {k: v for k, v in d.items() if not (isinstance(k, str) and k.startswith("_"))}
    return d


def _read_json_file(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    """Write JSON to ``path`` via tmp + replace so a crashed write cannot leave
    a half-formed file on disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, path)


def _set_cache(values: Dict[str, Any], source: str) -> None:
    """Replace cache contents in place. Preserves dict identity so any
    long-lived references (e.g. test fixtures, future consumers) see updates.
    Caller MUST have already validated ``values``."""
    global _SETTINGS_SOURCE, _SETTINGS_LAST_APPLIED_MS, _SETTINGS_MTIME
    _SETTINGS_CACHE.clear()
    _SETTINGS_CACHE.update(copy.deepcopy(values))
    _SETTINGS_SOURCE = source
    _SETTINGS_LAST_APPLIED_MS = _now_ms()
    try:
        _SETTINGS_MTIME = _SETTINGS_PATH.stat().st_mtime
    except OSError:
        _SETTINGS_MTIME = 0.0


def _emit_event(kind: str, source: str, message: str, payload: Optional[Dict[str, Any]] = None) -> None:
    if _event_sink is None:
        return
    try:
        _event_sink(kind, source, message, payload)
    except Exception:
        pass


def _emit_audit(rows: List[Dict[str, Any]]) -> None:
    if _audit_sink is None or not rows:
        return
    try:
        _audit_sink(rows)
    except Exception:
        pass


def load_settings(force: bool = False) -> Dict[str, Any]:
    """Load (or hot-reload) settings into the in-memory cache.

    Order of attempts on validation failure: on-disk file -> LKG -> defaults.
    Returns the resolved cache dict (callers get a live view; do not mutate)."""
    with _LOAD_LOCK:
        try:
            mt = _SETTINGS_PATH.stat().st_mtime
        except OSError:
            mt = 0.0
        if not force and _SETTINGS_CACHE and mt == _SETTINGS_MTIME:
            return _SETTINGS_CACHE

        # Attempt 1: on-disk settings file.
        raw = _read_json_file(_SETTINGS_PATH)
        file_exists = _SETTINGS_PATH.exists()
        if raw is not None:
            old_values = dict(_SETTINGS_CACHE) if _SETTINGS_CACHE else dict(SETTINGS_DEFAULTS)
            old_mtime = _SETTINGS_MTIME
            merged = _merge_with_defaults(_strip_meta(raw))
            coerced, errs = validate(merged)
            if not errs:
                _set_cache(coerced, "file")
                # Audit when the file changed without going through
                # apply_settings (mtime moved while we weren't looking).
                if old_values and old_mtime != mt:
                    rows = _diff_audit_rows(old_values, coerced, source="file", user=None, reason="mtime")
                    _emit_audit(rows)
                return _SETTINGS_CACHE
            _emit_event(
                "SETTINGS_REVERT_LKG", "settings",
                "pax_settings.json failed validation; reverting to last-known-good",
                {"errors": errs},
            )
        elif file_exists:
            # File exists but is unparseable. Fall through to LKG and emit.
            _emit_event(
                "SETTINGS_REVERT_LKG", "settings",
                "pax_settings.json unparseable; reverting to last-known-good",
                None,
            )

        # Attempt 2: last-known-good file.
        lkg_raw = _read_json_file(_LKG_PATH)
        if lkg_raw is not None:
            merged_lkg = _merge_with_defaults(_strip_meta(lkg_raw))
            coerced, errs = validate(merged_lkg)
            if not errs:
                try:
                    shutil.copyfile(_LKG_PATH, _SETTINGS_PATH)
                except OSError:
                    pass
                _set_cache(coerced, "lkg")
                return _SETTINGS_CACHE
            _emit_event(
                "SETTINGS_REVERT_DEFAULTS", "settings",
                "pax_settings.last_good.json also invalid; reverting to coded defaults",
                {"errors": errs},
            )

        # Attempt 3: coded defaults. Write them to disk so the file exists.
        _set_cache(SETTINGS_DEFAULTS, "defaults")
        try:
            _atomic_write_json(_SETTINGS_PATH, _seedable_payload(SETTINGS_DEFAULTS))
        except OSError:
            pass
        if not raw and not lkg_raw:
            _emit_event(
                "SETTINGS_INITIALIZED", "settings",
                "no pax_settings.json or LKG present; wrote defaults",
                None,
            )
        return _SETTINGS_CACHE


def _seedable_payload(values: Dict[str, Any]) -> Dict[str, Any]:
    """Wrap raw values with light metadata for on-disk persistence."""
    payload = {
        "_comment": "Dashboard-side runtime settings. Hot-reloaded on mtime change. Edit via /settings UI for an auditable trail, or by hand for emergency tuning.",
        "_version": 1,
    }
    payload.update(copy.deepcopy(values))
    return payload


def _merge_with_defaults(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Fill any missing keys with defaults so a partial JSON file is still
    loadable. Sub-dicts (level_weights / tape_bucket_weights) are merged
    key-by-key with defaults."""
    merged: Dict[str, Any] = {}
    for k, default_v in SETTINGS_DEFAULTS.items():
        if k not in raw:
            merged[k] = copy.deepcopy(default_v)
            continue
        rv = raw[k]
        if isinstance(default_v, dict) and isinstance(rv, dict):
            sub = dict(default_v)
            sub.update(rv)
            merged[k] = sub
        else:
            merged[k] = rv
    return merged


# ─── apply / restore ─────────────────────────────────────────────────────────


def _diff_audit_rows(old: Dict[str, Any], new: Dict[str, Any],
                     source: str, user: Optional[str],
                     reason: str) -> List[Dict[str, Any]]:
    """One audit row per top-level key whose value changed."""
    rows: List[Dict[str, Any]] = []
    ts_ms = _now_ms()
    for key in SETTINGS_SCHEMA:
        ov = old.get(key)
        nv = new.get(key)
        if ov == nv:
            continue
        rows.append({
            "ts_ms":          ts_ms,
            "source":         source,
            "user":           user,
            "field":          key,
            "old_value_json": json.dumps(ov, default=str),
            "new_value_json": json.dumps(nv, default=str),
            "reason":         reason,
        })
    return rows


def apply_settings(updates: Dict[str, Any], source: str = "ui",
                   user: Optional[str] = None) -> ApplyResult:
    """Validate ``updates`` (a partial dict), merge with current cache, write
    atomically, rotate the LKG file, and emit audit rows.

    Atomic: if validation fails, neither the cache nor the on-disk file is
    touched."""
    with _LOAD_LOCK:
        if not _SETTINGS_CACHE:
            # Cold path: load (or seed) before applying updates.
            load_settings()
        if not isinstance(updates, dict):
            return ApplyResult(ok=False, applied={},
                               errors=["updates must be an object"],
                               audit_rows=[])
        current = dict(_SETTINGS_CACHE)
        proposed: Dict[str, Any] = dict(current)
        unknown: List[str] = []
        for k, v in updates.items():
            if k in SETTINGS_SCHEMA:
                if isinstance(SETTINGS_DEFAULTS[k], dict) and isinstance(v, dict):
                    sub = dict(SETTINGS_DEFAULTS[k])
                    sub.update(current.get(k, {}) or {})
                    sub.update(v)
                    proposed[k] = sub
                else:
                    proposed[k] = v
            elif k.startswith("_"):
                continue
            else:
                unknown.append(k)
        coerced, errors = validate(proposed)
        if unknown:
            errors.append(f"unknown keys: {sorted(unknown)}")
        if errors:
            return ApplyResult(ok=False, applied={}, errors=errors, audit_rows=[])
        # Rotate the existing on-disk file to LKG (only if it parses as valid).
        existing = _read_json_file(_SETTINGS_PATH)
        if existing is not None:
            try:
                _atomic_write_json(_LKG_PATH, existing)
            except OSError:
                pass
        # Write the new file.
        _atomic_write_json(_SETTINGS_PATH, _seedable_payload(coerced))
        old_values = dict(_SETTINGS_CACHE) if _SETTINGS_CACHE else dict(SETTINGS_DEFAULTS)
        _set_cache(coerced, source)
        rows = _diff_audit_rows(old_values, coerced, source=source, user=user, reason="apply")
        _emit_audit(rows)
        return ApplyResult(ok=True, applied=dict(coerced), errors=[], audit_rows=rows)


def restore_defaults(source: str = "reset",
                     user: Optional[str] = None) -> ApplyResult:
    """Revert every setting to its coded default."""
    return apply_settings(dict(SETTINGS_DEFAULTS), source=source, user=user)


def restore_last_known_good(source: str = "lkg",
                            user: Optional[str] = None) -> ApplyResult:
    """Copy the LKG file over the live settings file and apply it."""
    raw = _read_json_file(_LKG_PATH)
    if raw is None:
        return ApplyResult(ok=False, applied={},
                           errors=["no last-known-good file present"],
                           audit_rows=[])
    merged = _merge_with_defaults(_strip_meta(raw))
    return apply_settings(merged, source=source, user=user)


# ─── reader API ──────────────────────────────────────────────────────────────


def get(key: str, default: Any = None) -> Any:
    """Read a single setting. Returns ``default`` (or the coded default if
    omitted) when the cache is empty or the key is unknown."""
    if not _SETTINGS_CACHE:
        load_settings()
    if key in _SETTINGS_CACHE:
        return _SETTINGS_CACHE[key]
    if default is not None:
        return default
    return SETTINGS_DEFAULTS.get(key)


def current() -> Dict[str, Any]:
    """Snapshot of the live cache (deep-copied so callers cannot mutate it)."""
    if not _SETTINGS_CACHE:
        load_settings()
    return copy.deepcopy(_SETTINGS_CACHE)


def status() -> Dict[str, Any]:
    """Operational status for the UI status panel."""
    if not _SETTINGS_CACHE:
        load_settings()
    return {
        "source":              _SETTINGS_SOURCE,
        "last_applied_ms":     _SETTINGS_LAST_APPLIED_MS,
        "settings_path":       str(_SETTINGS_PATH),
        "lkg_path":            str(_LKG_PATH),
        "lkg_present":         _LKG_PATH.exists(),
        "mtime":               _SETTINGS_MTIME,
    }
