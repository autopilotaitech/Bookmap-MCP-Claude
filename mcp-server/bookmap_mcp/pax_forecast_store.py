"""Durable store for validated Pax AI forecast records.

Forecasts are persisted to a dedicated SQLite database so the calibration,
research, and replay tools can later score them against forward outcomes.

Hard rules:
- Read-only relative to ``pax_ai_config.json``, ``pax_weights.json``, and
  production prompts; this module never touches them.
- Never imported by ``feature_bus.py`` (its writer path is frozen).
- ``forecast_id`` is the primary key, so re-recording the same forecast is a
  no-op.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Optional

from . import pax_forecast_schema as schema


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS forecasts (
    forecast_id        TEXT    PRIMARY KEY,
    schema_version     INTEGER NOT NULL,
    ts_ms              INTEGER NOT NULL,
    source_turn_id     INTEGER,
    alias              TEXT    NOT NULL,
    level              TEXT    NOT NULL,
    thesis             TEXT    NOT NULL,
    execution_read     TEXT    NOT NULL,
    direction          TEXT    NOT NULL,
    horizon_sec        INTEGER NOT NULL,
    prob_success       REAL    NOT NULL,
    expected_r         REAL    NOT NULL,
    invalidation       TEXT    NOT NULL,
    features_used      TEXT    NOT NULL,
    setup_bucket       TEXT    NOT NULL,
    probability_bucket TEXT    NOT NULL,
    ingested_ms        INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_forecasts_ts ON forecasts(ts_ms);
CREATE INDEX IF NOT EXISTS idx_forecasts_alias_ts ON forecasts(alias, ts_ms);
CREATE INDEX IF NOT EXISTS idx_forecasts_setup ON forecasts(setup_bucket);
CREATE INDEX IF NOT EXISTS idx_forecasts_prob_bucket ON forecasts(probability_bucket);
CREATE INDEX IF NOT EXISTS idx_forecasts_source_turn ON forecasts(source_turn_id);
"""

_INSERT_SQL = """
INSERT OR REPLACE INTO forecasts (
    forecast_id, schema_version, ts_ms, source_turn_id,
    alias, level, thesis, execution_read, direction, horizon_sec,
    prob_success, expected_r, invalidation, features_used,
    setup_bucket, probability_bucket, ingested_ms
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_COLS = (
    "forecast_id, schema_version, ts_ms, source_turn_id, alias, level, "
    "thesis, execution_read, direction, horizon_sec, prob_success, "
    "expected_r, invalidation, features_used, setup_bucket, "
    "probability_bucket, ingested_ms"
)


class PaxForecastStore:
    """SQLite-backed store for validated forecast records."""

    def __init__(self, path: Path | str):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

    # Context manager sugar so tests / scripts can use ``with``.
    def __enter__(self) -> "PaxForecastStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ----------------------------------------------------------------- writes

    def record(self,
               raw: Any,
               *,
               ts_ms: Optional[int] = None,
               source_turn_id: Optional[int] = None) -> Dict[str, Any]:
        """Validate ``raw`` and persist the resulting forecast record."""
        validated = schema.validate_forecast(
            raw, ts_ms=ts_ms, source_turn_id=source_turn_id)
        return self.record_validated(validated)

    def record_validated(self, forecast: Dict[str, Any]) -> Dict[str, Any]:
        """Persist a forecast that has already been validated."""
        ts_ms = forecast.get("ts_ms")
        if ts_ms is None:
            raise ValueError("ts_ms is required to persist a forecast")
        setup_bucket = schema.forecast_setup_bucket(forecast)
        prob_bucket = schema.probability_bucket(forecast["prob_success"])
        ingested_ms = int(time.time() * 1000)
        self._conn.execute(_INSERT_SQL, (
            forecast["forecast_id"],
            int(forecast.get("schema_version") or schema.SCHEMA_VERSION),
            int(ts_ms),
            (None if forecast.get("source_turn_id") is None
             else int(forecast["source_turn_id"])),
            forecast["alias"],
            forecast["level"],
            forecast["thesis"],
            forecast["execution_read"],
            forecast["direction"],
            int(forecast["horizon_sec"]),
            float(forecast["prob_success"]),
            float(forecast["expected_r"]),
            forecast["invalidation"],
            json.dumps(list(forecast["features_used"]), separators=(",", ":")),
            setup_bucket,
            prob_bucket,
            ingested_ms,
        ))
        self._conn.commit()
        return forecast

    # ------------------------------------------------------------------ reads

    def count(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM forecasts")
        return int(cur.fetchone()[0])

    def iter_forecasts(self,
                       *,
                       start_ms: Optional[int] = None,
                       end_ms: Optional[int] = None,
                       alias: Optional[str] = None) -> Iterator[Dict[str, Any]]:
        """Yield forecast records ordered by ``ts_ms``.

        The window is half-open: ``ts_ms >= start_ms AND ts_ms < end_ms`` to
        keep UTC-day queries collision-free (see ``half_open_utc_day_convention``
        in the memory index).
        """
        where: list[str] = []
        params: list[Any] = []
        if start_ms is not None:
            where.append("ts_ms >= ?")
            params.append(int(start_ms))
        if end_ms is not None:
            where.append("ts_ms < ?")
            params.append(int(end_ms))
        if alias is not None:
            where.append("alias = ?")
            params.append(alias)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        sql = (
            f"SELECT {_SELECT_COLS} FROM forecasts {clause} "
            "ORDER BY ts_ms ASC, forecast_id ASC"
        )
        cur = self._conn.execute(sql, tuple(params))
        for row in cur:
            yield _row_to_dict(row)


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "forecast_id": row["forecast_id"],
        "schema_version": int(row["schema_version"]),
        "ts_ms": int(row["ts_ms"]),
        "source_turn_id": (None if row["source_turn_id"] is None
                           else int(row["source_turn_id"])),
        "alias": row["alias"],
        "level": row["level"],
        "thesis": row["thesis"],
        "execution_read": row["execution_read"],
        "direction": row["direction"],
        "horizon_sec": int(row["horizon_sec"]),
        "prob_success": float(row["prob_success"]),
        "expected_r": float(row["expected_r"]),
        "invalidation": row["invalidation"],
        "features_used": json.loads(row["features_used"]),
        "setup_bucket": row["setup_bucket"],
        "probability_bucket": row["probability_bucket"],
        "ingested_ms": int(row["ingested_ms"]),
    }


__all__ = ["PaxForecastStore"]
