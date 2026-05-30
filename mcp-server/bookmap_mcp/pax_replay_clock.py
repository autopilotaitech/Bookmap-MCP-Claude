"""Pure offline/weekend replay clock helper.

The single source of truth for "what time is it, for this replay record?" when
PAX is re-run over a SAVED tape while the market/Bookmap is closed. It reads
ONLY the saved record / snapshot / replay_input -- it NEVER reads the wall clock.
That is the whole point: replaying a Wednesday tape on a Saturday must use the
Wednesday recorded time, never the Saturday wall clock.

Clock source priority (highest first):
    1. replay_input.now_ms       -- what the live system stamped this beat with
    2. record.ts_ms              -- the heartbeat/log record time
    3. snapshot.marketDataAsOfMs -- real bridge/feed timestamp
    4. snapshot.marketAsOfMs     -- real bridge/feed timestamp
    5. snapshot.eventMs          -- real feed/event time
    6. snapshot.updatedAtMs      -- real feed/event time
    7. (none)                    -- ts_ms=0, reported as a limitation; NEVER
                                    silently substituted with the wall clock.

``composedAtMs`` (dashboard compose time) is DELIBERATELY excluded as both a
clock source and a market-freshness source: it only proves the dashboard kept
composing, not that the underlying feed advanced.

Everything here is pure and deterministic: same inputs -> same output, with no
I/O, no network, no broker, no LLM, no wall-clock read.
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, NamedTuple, Optional

try:
    from zoneinfo import ZoneInfo
    _CT = ZoneInfo("America/Chicago")
except Exception:   # pragma: no cover - zoneinfo always present on 3.9+
    _CT = None

_UTC = datetime.timezone.utc
_EPOCH = datetime.datetime(1970, 1, 1, tzinfo=_UTC)

# Ordered (source_label, lookup) pairs. ``lookup`` returns a candidate int|None.
CLOCK_SOURCE_PRIORITY = (
    "replay_input.now_ms",
    "record.ts_ms",
    "snapshot.marketDataAsOfMs",
    "snapshot.marketAsOfMs",
    "snapshot.eventMs",
    "snapshot.updatedAtMs",
)

# Market-freshness timestamps (real feed times). composedAtMs is intentionally
# absent -- see module docstring.
_MARKET_TS_KEYS = ("marketDataAsOfMs", "marketAsOfMs")
_MARKET_AGE_KEYS = ("ageMs", "snapshot_age_ms")


class ClockResolution(NamedTuple):
    ts_ms: int
    source: str

    @property
    def has_real_clock(self) -> bool:
        return self.source != "none" and self.ts_ms > 0


def _coerce_ms(x: Any) -> Optional[int]:
    """Coerce to a positive int millisecond value, else None."""
    try:
        v = int(x)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _num(x: Any) -> Optional[float]:
    try:
        v = float(x)
        return v if v == v else None   # reject NaN
    except (TypeError, ValueError):
        return None


def resolve_clock(record: Optional[Dict[str, Any]],
                  snapshot: Optional[Dict[str, Any]],
                  replay_input: Optional[Dict[str, Any]]) -> ClockResolution:
    """Resolve the replay clock for one record, with its source label.

    Pure: derives time only from the inputs. Returns ts_ms=0 / source="none"
    when no real timestamp exists -- the caller reports that as a limitation
    rather than falling back to the wall clock."""
    record = record or {}
    snapshot = snapshot or {}
    candidates = (
        ("replay_input.now_ms",
         (replay_input or {}).get("now_ms") if replay_input else None),
        ("record.ts_ms", record.get("ts_ms")),
        ("snapshot.marketDataAsOfMs", snapshot.get("marketDataAsOfMs")),
        ("snapshot.marketAsOfMs", snapshot.get("marketAsOfMs")),
        ("snapshot.eventMs", snapshot.get("eventMs")),
        ("snapshot.updatedAtMs", snapshot.get("updatedAtMs")),
    )
    for label, raw in candidates:
        ms = _coerce_ms(raw)
        if ms is not None:
            return ClockResolution(ms, label)
    return ClockResolution(0, "none")


def replay_now_ms(record: Optional[Dict[str, Any]],
                  snapshot: Optional[Dict[str, Any]],
                  replay_input: Optional[Dict[str, Any]]) -> int:
    """The replay clock in epoch ms (0 when no real timestamp exists)."""
    return resolve_clock(record, snapshot, replay_input).ts_ms


def replay_now_dt_utc(ts_ms: int) -> datetime.datetime:
    """UTC datetime for a replay clock. ts_ms<=0 -> epoch (never wall clock)."""
    if ts_ms and ts_ms > 0:
        return datetime.datetime.fromtimestamp(ts_ms / 1000.0, tz=_UTC)
    return _EPOCH


def replay_now_dt_ct(ts_ms: int) -> datetime.datetime:
    """America/Chicago datetime for a replay clock (pure projection)."""
    dt_utc = replay_now_dt_utc(ts_ms)
    if _CT is None:   # pragma: no cover
        return dt_utc
    return dt_utc.astimezone(_CT)


def market_age_sec_for_replay(snapshot: Optional[Dict[str, Any]],
                              now_ms: int,
                              replay_input: Optional[Dict[str, Any]]
                              ) -> Optional[float]:
    """Seconds since the snapshot's MARKET/FEED data was fresh, for replay.

    Priority: the embedded ``replay_input.market_age_sec`` (exactly what the
    live system saw) -> real feed timestamp (``marketDataAsOfMs`` /
    ``marketAsOfMs``) vs ``now_ms`` -> explicit data-age field. NEVER uses
    ``composedAtMs``. Returns None when no real market timestamp exists; the
    caller reports that as a limitation rather than faking a freshness pass."""
    if replay_input is not None and "market_age_sec" in replay_input:
        return _num(replay_input.get("market_age_sec"))
    snapshot = snapshot or {}
    for key in _MARKET_TS_KEYS:
        as_of = _num(snapshot.get(key))
        if as_of is not None:
            return max(0.0, (now_ms - as_of) / 1000.0)
    for key in _MARKET_AGE_KEYS:
        age_ms = _num(snapshot.get(key))
        if age_ms is not None:
            return max(0.0, age_ms / 1000.0)
    return None


def heartbeat_age_sec_for_replay(record: Optional[Dict[str, Any]],
                                 replay_input: Optional[Dict[str, Any]]
                                 ) -> Optional[float]:
    """Heartbeat age (seconds) from the embedded replay_input, then a legacy
    top-level record field, then None. Pure -- no derivation from wall clock."""
    if replay_input is not None and "heartbeat_age_sec" in replay_input:
        return _num(replay_input.get("heartbeat_age_sec"))
    if record is not None and "heartbeat_age_sec" in record:
        return _num(record.get("heartbeat_age_sec"))
    return None


def _session_label_ct(dt_ct: datetime.datetime) -> Optional[str]:
    """Coarse session label from the CT wall time of the replay clock.

    Heuristic only (RTH/ETH/EU windows); used for human-readable replay
    reporting, NOT for trading decisions. Returns None on weekends/holidays
    where the session is ambiguous from time-of-day alone."""
    wd = dt_ct.weekday()   # Mon=0 .. Sun=6
    minutes = dt_ct.hour * 60 + dt_ct.minute
    # Sunday before 17:00 CT and all of Saturday: no active futures session.
    if wd == 5:
        return None
    if wd == 6 and minutes < 17 * 60:
        return None
    if 8 * 60 + 30 <= minutes < 15 * 60:
        return "RTH"
    if 2 * 60 <= minutes < 8 * 60 + 30:
        return "EU"
    # 17:00 CT onward (Globex open) and overnight up to EU open.
    return "ETH"


def session_clock_summary(record: Optional[Dict[str, Any]],
                          snapshot: Optional[Dict[str, Any]],
                          replay_input: Optional[Dict[str, Any]]
                          ) -> Dict[str, Any]:
    """Human-readable replay-clock summary for one record. Pure.

    Reports the resolved timestamp source, UTC + CT ISO strings, the CT
    weekday (so a weekend wall clock can never masquerade as the recorded
    weekday), and a coarse derived session label."""
    res = resolve_clock(record, snapshot, replay_input)
    dt_utc = replay_now_dt_utc(res.ts_ms)
    dt_ct = replay_now_dt_ct(res.ts_ms)
    return {
        "ts_ms": res.ts_ms,
        "source": res.source,
        "has_real_clock": res.has_real_clock,
        "utc_iso": dt_utc.isoformat(),
        "ct_iso": dt_ct.isoformat(),
        "weekday_ct": dt_ct.strftime("%a") if res.has_real_clock else "",
        "session_label": _session_label_ct(dt_ct) if res.has_real_clock else None,
    }
