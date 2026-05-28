"""Phase 1: pure-Python signal engine — the Bookmap-independent core.

This module is the canonical import target for any non-dashboard consumer of
Pax decision logic (the upcoming Phase 3 paper-trading daemon, the Phase 5
replay tooling, future research notebooks). Everything exported here is a
pure function over a normalized snapshot dict — no HTTP, no live broker, no
process-side effects beyond module-level caches that callers can clear.

Implementation lives in `dashboard.py` for historical reasons (the snapshot
composer evolved alongside the live HUD). This module re-exports those pure
symbols under a stable name. Daemons should import from `signal_engine`, not
`dashboard`, so the boundary is enforced by convention.

The dashboard process still owns:
  - `fetch_snapshot()` — orchestrates bridge HTTP calls
  - `_sync_magnet_levels()` — posts OR grid to the bridge
  - HTTP server + UI rendering

Those are NOT re-exported here.
"""

from __future__ import annotations

# Module-level constants -----------------------------------------------------
from .dashboard import (
    ET,
    DISPLAY_TZ,
    DISPLAY_TZ_LABEL,
    NEWS_CALENDAR_PATH,
    OR_SIGNAL_GLOBS,
    NQ_RUNG_PTS,
    NQ_TICK,
    PROX_TICKS,
)

# Per-magnet composite tuning ------------------------------------------------
from .dashboard import (
    _LVL_W,
    _LVL_THR_DIRECTIONAL,
    _LVL_THIN_COVERAGE_FRAC,
    _CONVICTION_TRAJ_NUDGE,
)

# Tape-flow tuning -----------------------------------------------------------
from .dashboard import (
    _TAPE_BUCKET_WEIGHTS,
    _TAPE_LARGE_LABELS,
    _TAPE_BLOCK_LABELS,
    _TAPE_THIN_FLOOR_PRINTS,
    _TAPE_THIN_HEDGE_PRINTS,
    _TAPE_ALIGN_BONUS,
    _TAPE_ALIGN_THRESHOLD,
)

# Trajectory modulation (V8/V9/V10) ------------------------------------------
from .dashboard import (
    _BIAS_SCORE_TRAJ_NUDGE,
    _LEVEL_REACTION_TRAJ_NUDGE,
    _LEVEL_REACTION_TRAJ_STRONG_DELTA,
    _LEVEL_REACTION_TRAJ_NORMAL_DELTA,
    _LEVEL_REACTION_MAX_DT_SEC,
)

# Conviction v2 config + state -----------------------------------------------
from .dashboard import (
    CONVICTION_WEIGHTS,
    CONVICTION_HALFLIFE_SEC,
    CONVICTION_METHOD_VERSION,
    CONVICTION_SOURCE_WEIGHTS,
    CONVICTION_CLUSTERS,
    CONVICTION_CLUSTER_CAPS,
    CONVICTION_WINDOWS_SEC,
    CONVICTION_AGG_WEIGHTS,
    CONVICTION_THRESHOLDS,
    _CONVICTION_LEGACY_KEY_MAP,
    _CONVICTION_SOURCES,
    _CONVICTION_STATE,
    _LAST_CONVICTION,
    _LAST_LEVEL_REACTION,
    _PAX_WEIGHTS_CACHE,
    _PAX_WEIGHTS_PATH,
)

# Pax decision config --------------------------------------------------------
from .dashboard import (
    PAX_LOG_DIR,
    PAX_CONFIDENCE_FLOOR,
    PAX_CONFIDENCE_FULL,
    PAX_MIN_OR_WIDTH_PTS,
    PAX_MAX_OR_WIDTH_PTS,
    _PAX_REGIME_BOOST_DEFAULT,
)

# Pure helpers ---------------------------------------------------------------
from .dashboard import (
    session_state,
    news_blackout,
    vwap_from_trades,
    imbalance,
    momentum_flag,
    or_latest_row,
    compute_stretch,
    vwap_or_gate,
    _demote,
    _safe_num,
    _tanh,
    _clip,
    _as_float,
    _imb,
    _strip_meta,
    _load_pax_weights,
    _rolling_sma,
    _prune_ring,
    _conv_session_anchor,
    _regime_to_signal,
    _slope_to_signal,
    _level_to_signal,
    _conv_init_source_state,
    _conv_init_alias_state,
    _conv_aggregate_source,
    _conv_apply_cluster_caps,
    _pax_boost,
    _demote_conf,
)

# Per-level signal helpers ---------------------------------------------------
from .dashboard import (
    _ps_bbo_bias,
    _ps_rotation,
    _lt_lean,
    _tape_bias,
    _micro_at_level,
    _vwap_stretch_penalty,
    _vwap_stretch_directional,
    _vp_context,
    _vp_at_level,
    _book_at_level,
    _vwap_or_at_level,
    _conviction_at_level,
    _detect_hvn_lvn,
    _score_level,
    _level_composite,
)

# Institutional-thesis helpers + cache + code tuples --------------------------
from .dashboard import (
    _LEVEL_TOUCH_STATE,
    _TOUCH_HISTORY_DEPTH,
    _TOUCH_TICKS,
    _APPROACH_PROX_PTS,
    _ACCEPT_HOLD_POLLS,
    _REJECT_BACKOFF_PTS,
    _THESIS_STATE_CODES,
    _THESIS_THESIS_CODES,
    _THESIS_LIQ_CODES,
    _THESIS_AGG_CODES,
    _THESIS_BOOK_CODES,
    _THESIS_EXEC_CODES,
    _thesis_classify_touch_state,
    _thesis_micro_at_price,
    _thesis_liquidity_quality,
    _thesis_select_thesis,
    _thesis_aggressor_flow,
    _thesis_book_state,
    _thesis_execution_read,
    _thesis_confidence,
    _thesis_invalidations,
    _thesis_for_level,
)

# Institutional signal composer ---------------------------------------------
from .dashboard import (
    compute_institutional_signals,
    _signal_type_from_thesis,
    _signal_aggressor_align_required,
    _signal_size_tier_from_confidence,
    _SIGNAL_TYPE_CODES,
    _SIGNAL_DIRECTION_CODES,
)

# Institutional chart-events composer ---------------------------------------
from .dashboard import (
    compute_institutional_chart_events,
    _CHART_EVENT_TYPES,
    _CHART_SEVERITY_RANKS,
    _CHART_COLORS,
)

# Pax AI chart events reader -------------------------------------------------
from .pax_ai_chart_events import (
    compute_pax_ai_chart_events,
    compute_pax_ai_chart_events_status,
    read_pax_ai_chart_events,
    pax_ai_row_to_chart_event,
    AI_BULL_COLOR,
    AI_BEAR_COLOR,
    AI_NEUTRAL_COLOR,
)

# Conviction sources (17) ----------------------------------------------------
from .dashboard import (
    _source_flow_ofi,
    _source_flow_cvd,
    _source_flow_vpt_absorption,
    _source_regime,
    _source_bias_score,
    _source_vwap_dislocation,
    _source_vwap_slope,
    _source_vwap_or_gate,
    _source_volume_profile,
    _source_pull_stack,
    _source_tape_large_lot,
    _source_orderbook,
    _source_lt_liquidity,
    _source_micro_events,
    _source_level_reaction,
    _source_anchored_vwap_opening_drive,
    _source_ib_context,
    _tape_source_from_flow,
)

# Top-level composers --------------------------------------------------------
from .dashboard import (
    compute_or_levels,
    compute_tape_flow,
    compute_vwap_bias,
    compute_vp_bias,
    compute_session_conviction,
    pax_decision,
    trade_decision,
    compute_institutional_thesis,
)


# `__all__` documents the contract — a daemon importing `from signal_engine
# import *` gets exactly the pure surface, nothing bridge-shaped.
__all__ = [
    # Constants
    "ET", "DISPLAY_TZ", "DISPLAY_TZ_LABEL", "NEWS_CALENDAR_PATH",
    "OR_SIGNAL_GLOBS", "NQ_RUNG_PTS", "NQ_TICK", "PROX_TICKS",
    # Per-magnet composite
    "_LVL_W", "_LVL_THR_DIRECTIONAL", "_LVL_THIN_COVERAGE_FRAC",
    "_CONVICTION_TRAJ_NUDGE",
    # Tape flow
    "_TAPE_BUCKET_WEIGHTS", "_TAPE_LARGE_LABELS", "_TAPE_BLOCK_LABELS",
    "_TAPE_THIN_FLOOR_PRINTS", "_TAPE_THIN_HEDGE_PRINTS",
    "_TAPE_ALIGN_BONUS", "_TAPE_ALIGN_THRESHOLD",
    # Trajectory tuning
    "_BIAS_SCORE_TRAJ_NUDGE", "_LEVEL_REACTION_TRAJ_NUDGE",
    "_LEVEL_REACTION_TRAJ_STRONG_DELTA", "_LEVEL_REACTION_TRAJ_NORMAL_DELTA",
    "_LEVEL_REACTION_MAX_DT_SEC",
    # Conviction config + state
    "CONVICTION_WEIGHTS", "CONVICTION_HALFLIFE_SEC", "CONVICTION_METHOD_VERSION",
    "CONVICTION_SOURCE_WEIGHTS", "CONVICTION_CLUSTERS", "CONVICTION_CLUSTER_CAPS",
    "CONVICTION_WINDOWS_SEC", "CONVICTION_AGG_WEIGHTS", "CONVICTION_THRESHOLDS",
    "_CONVICTION_LEGACY_KEY_MAP", "_CONVICTION_SOURCES", "_CONVICTION_STATE",
    "_LAST_CONVICTION", "_LAST_LEVEL_REACTION", "_PAX_WEIGHTS_CACHE",
    "_PAX_WEIGHTS_PATH",
    # Pax config
    "PAX_LOG_DIR", "PAX_CONFIDENCE_FLOOR", "PAX_CONFIDENCE_FULL",
    "PAX_MIN_OR_WIDTH_PTS", "PAX_MAX_OR_WIDTH_PTS", "_PAX_REGIME_BOOST_DEFAULT",
    # Pure helpers
    "session_state", "news_blackout", "vwap_from_trades", "imbalance",
    "momentum_flag", "or_latest_row", "compute_stretch", "vwap_or_gate",
    "_demote", "_safe_num", "_tanh", "_clip", "_as_float", "_imb",
    "_strip_meta", "_load_pax_weights", "_rolling_sma", "_prune_ring",
    "_conv_session_anchor", "_regime_to_signal", "_slope_to_signal",
    "_level_to_signal", "_conv_init_source_state", "_conv_init_alias_state",
    "_conv_aggregate_source", "_conv_apply_cluster_caps",
    "_pax_boost", "_demote_conf",
    # Per-level helpers
    "_ps_bbo_bias", "_ps_rotation", "_lt_lean", "_tape_bias",
    "_micro_at_level", "_vwap_stretch_penalty", "_vwap_stretch_directional",
    "_vp_context", "_vp_at_level", "_book_at_level", "_vwap_or_at_level",
    "_conviction_at_level", "_detect_hvn_lvn", "_score_level",
    "_level_composite",
    # Institutional thesis
    "_LEVEL_TOUCH_STATE", "_TOUCH_HISTORY_DEPTH", "_TOUCH_TICKS",
    "_APPROACH_PROX_PTS", "_ACCEPT_HOLD_POLLS", "_REJECT_BACKOFF_PTS",
    "_THESIS_STATE_CODES", "_THESIS_THESIS_CODES", "_THESIS_LIQ_CODES",
    "_THESIS_AGG_CODES", "_THESIS_BOOK_CODES", "_THESIS_EXEC_CODES",
    "_thesis_classify_touch_state", "_thesis_micro_at_price",
    "_thesis_liquidity_quality", "_thesis_select_thesis",
    "_thesis_aggressor_flow", "_thesis_book_state",
    "_thesis_execution_read", "_thesis_confidence",
    "_thesis_invalidations", "_thesis_for_level",
    # Institutional signal composer
    "compute_institutional_signals",
    "_signal_type_from_thesis", "_signal_aggressor_align_required",
    "_signal_size_tier_from_confidence",
    "_SIGNAL_TYPE_CODES", "_SIGNAL_DIRECTION_CODES",
    # Institutional chart events
    "compute_institutional_chart_events",
    "_CHART_EVENT_TYPES", "_CHART_SEVERITY_RANKS", "_CHART_COLORS",
    # Pax AI chart events
    "compute_pax_ai_chart_events", "compute_pax_ai_chart_events_status",
    "read_pax_ai_chart_events", "pax_ai_row_to_chart_event",
    "AI_BULL_COLOR", "AI_BEAR_COLOR", "AI_NEUTRAL_COLOR",
    # Sources
    "_source_flow_ofi", "_source_flow_cvd", "_source_flow_vpt_absorption",
    "_source_regime", "_source_bias_score", "_source_vwap_dislocation",
    "_source_vwap_slope", "_source_vwap_or_gate", "_source_volume_profile",
    "_source_pull_stack", "_source_tape_large_lot", "_source_orderbook",
    "_source_lt_liquidity", "_source_micro_events", "_source_level_reaction",
    "_source_anchored_vwap_opening_drive", "_source_ib_context",
    "_tape_source_from_flow",
    # Composers
    "compute_or_levels", "compute_tape_flow", "compute_vwap_bias",
    "compute_vp_bias", "compute_session_conviction", "pax_decision",
    "trade_decision",
    "compute_institutional_thesis",
]
