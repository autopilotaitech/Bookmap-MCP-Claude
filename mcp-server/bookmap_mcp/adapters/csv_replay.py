"""CsvReplayAdapter — feed any time-ordered CSV as a snapshot stream.

Designed to consume the existing `pax-recordings/*.csv` produced by
`pax_collector`, but works with any CSV that has a timestamp column and at
least a `mid` (or `bid`+`ask`) column. Missing fields are either synthesized
from what's available (with the synthesis recorded in `_synthetic`) or left
as `None`, which makes the relevant signal-engine source return reliability
0 — graceful degradation, never a crash.

Column recognition (case-insensitive header, takes the first match found):

  timestamp:        ts_ms | ts_utc | ts_iso | ts
  alias / symbol:   alias | symbol
  mid / book:       mid; bid | best_bid; ask | best_ask
  trade print:      trade_price + trade_side + trade_size (optional)
  OR levels:        or_high; or_low; or_width
  VWAP:             vwap; vwap_stddev; vwap_upper1; vwap_lower1
  Flow / regime:    regime; regime_conf; bias_score; bias_trajectory;
                    ofi_z; cvd_delta_z; vpt_z
  Tape buckets:     not supported in CSV (set to None — _source_tape_*
                    returns reliability 0)
"""

from __future__ import annotations

import csv
import datetime as dt
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import AdapterHealth, Snapshot


# ─── helpers ─────────────────────────────────────────────────────────────

def _parse_float(v: Any) -> Optional[float]:
    if v is None: return None
    if isinstance(v, (int, float)):
        return float(v) if not (isinstance(v, float) and math.isnan(v)) else None
    s = str(v).strip()
    if not s or s.lower() in ("none", "null", "nan"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_int(v: Any) -> Optional[int]:
    f = _parse_float(v)
    return int(f) if f is not None else None


def _row_get(row: Dict[str, str], *keys: str) -> Optional[str]:
    """Return the first non-empty value among `keys` (case-insensitive)."""
    if not row: return None
    # Build a lookup once per call — CSV rows are small.
    lower = {k.lower(): k for k in row.keys()}
    for k in keys:
        actual = lower.get(k.lower())
        if actual is None:
            continue
        v = row.get(actual)
        if v is not None and str(v).strip() != "":
            return v
    return None


# ─── adapter ─────────────────────────────────────────────────────────────

class CsvReplayAdapter:
    """Time-ordered CSV → normalized snapshot stream."""

    name = "csv_replay"

    def __init__(self, path: Path, alias: str = "REPLAY",
                  default_tick: float = 0.25) -> None:
        self.path = Path(path)
        self._alias_default = alias
        self.default_tick = default_tick
        self._fh = None
        self._reader = None
        self._eof = False
        self._row_count = 0
        self._last_snapshot_ms = 0
        self._last_mid: Optional[float] = None
        self._synthesized_nanos = 0
        self._errors: List[str] = []

    # ─── DataAdapter interface ───────────────────────────────────────

    def start(self) -> None:
        if self._fh is not None:
            return
        if not self.path.exists():
            raise FileNotFoundError(f"CSV not found: {self.path}")
        self._fh = open(self.path, "r", encoding="utf-8", newline="")
        self._reader = csv.DictReader(self._fh)
        self._eof = False

    def stop(self) -> None:
        if self._fh is not None:
            try: self._fh.close()
            except Exception: pass
        self._fh = None
        self._reader = None

    def next_snapshot(self) -> Optional[Snapshot]:
        if self._reader is None:
            return None
        if self._eof:
            return None
        try:
            row = next(self._reader)
        except StopIteration:
            self._eof = True
            return None
        self._row_count += 1
        try:
            return self._row_to_snapshot(row)
        except Exception as exc:    # pragma: no cover — best-effort guard
            self._errors.append(f"row {self._row_count}: {type(exc).__name__}: {exc}")
            return None

    def health(self) -> AdapterHealth:
        if self._eof:
            return AdapterHealth(status="eof",
                                  detail=f"replayed {self._row_count} rows",
                                  last_snapshot_ms=self._last_snapshot_ms,
                                  snapshots_emitted=self._row_count,
                                  errors=list(self._errors))
        if self._errors and self._row_count > 0:
            return AdapterHealth(status="error",
                                  detail=f"{len(self._errors)} bad rows",
                                  last_snapshot_ms=self._last_snapshot_ms,
                                  snapshots_emitted=self._row_count,
                                  errors=list(self._errors))
        return AdapterHealth(status="ok",
                              last_snapshot_ms=self._last_snapshot_ms,
                              snapshots_emitted=self._row_count,
                              errors=list(self._errors))

    # ─── row → snapshot ──────────────────────────────────────────────

    def _row_to_snapshot(self, row: Dict[str, str]) -> Snapshot:
        synthetic: List[str] = []

        # ── timestamp ──
        ts_iso = _row_get(row, "ts_iso", "ts_utc", "ts")
        ts_ms = _parse_int(_row_get(row, "ts_ms"))
        if ts_ms is None and ts_iso:
            try:
                # Accept ISO with or without timezone.
                t = dt.datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
                ts_ms = int(t.timestamp() * 1000)
            except (ValueError, TypeError):
                ts_ms = None
        if ts_ms is not None:
            self._last_snapshot_ms = ts_ms

        # ── alias ──
        alias = _row_get(row, "alias", "symbol") or self._alias_default

        # ── book ──
        mid = _parse_float(_row_get(row, "mid"))
        bid = _parse_float(_row_get(row, "bid", "best_bid", "bestbid"))
        ask = _parse_float(_row_get(row, "ask", "best_ask", "bestask"))
        if mid is None and bid is not None and ask is not None:
            mid = (bid + ask) / 2.0
            synthetic.append("book.mid")
        if bid is None and mid is not None:
            bid = mid - self.default_tick / 2.0
            synthetic.append("book.bestBid")
        if ask is None and mid is not None:
            ask = mid + self.default_tick / 2.0
            synthetic.append("book.bestAsk")
        spread = (ask - bid) if (ask is not None and bid is not None) else None
        book: Dict[str, Any] = {"bestBid": bid, "bestAsk": ask,
                                  "mid": mid, "spread": spread}

        # ── trades ──
        # Prefer an explicit print row if present.
        trades: List[Dict[str, Any]] = []
        t_price = _parse_float(_row_get(row, "trade_price", "print_price"))
        t_size  = _parse_int(_row_get(row, "trade_size", "print_size")) or 1
        t_side  = (_row_get(row, "trade_side", "print_side") or "").lower()
        if t_price is not None:
            trades.append({"price": t_price, "size": t_size,
                            "side": t_side, "nanos": self._synth_nanos(ts_ms)})
        elif mid is not None and self._last_mid is not None and mid != self._last_mid:
            # Synthesize a single print at the new mid, side from direction.
            side = "buy" if mid > self._last_mid else "sell"
            trades.append({"price": mid, "size": 1, "side": side,
                            "nanos": self._synth_nanos(ts_ms)})
            synthetic.append("trades")
        if mid is not None:
            self._last_mid = mid

        # ── OR row ──
        or_high = _parse_float(_row_get(row, "or_high", "orhigh"))
        or_low  = _parse_float(_row_get(row, "or_low", "orlow"))
        or_row: Optional[Dict[str, Any]] = None
        if or_high is not None and or_low is not None:
            or_row = {"orHigh": str(or_high), "orLow": str(or_low),
                       "orWidthPts": or_high - or_low}

        # ── VWAP ──
        vwap = _parse_float(_row_get(row, "vwap"))
        vwap_stddev = _parse_float(_row_get(row, "vwap_stddev", "vwap_sigma"))
        vwap_obj: Optional[Dict[str, Any]] = None
        if vwap is not None:
            vwap_obj = {"vwap": vwap, "stddev": vwap_stddev or 0.0,
                         "lastTradePrice": mid}
            if vwap_stddev is None:
                synthetic.append("vwap_obj.stddev")

        # ── flow ──
        flow: Dict[str, Any] = {}
        col_map = [
            ("regime",          "regime",            False),
            ("regime_conf",     "regimeConfidence",  True),
            ("bias_score",      "biasScore",         True),
            ("bias_trajectory", "biasTrajectory",    False),
            ("ofi_z",           "ofiZ",              True),
            ("cvd_delta_z",     "cvdDeltaZ",         True),
            ("vpt_z",           "vptZ",              True),
        ]
        for src_col, dst_key, as_float in col_map:
            raw = _row_get(row, src_col)
            if raw is None: continue
            if as_float:
                v = _parse_float(raw)
                if v is not None:
                    flow[dst_key] = v
            else:
                flow[dst_key] = raw

        snap: Snapshot = {
            "alias":       alias,
            "health":      "ok",
            "ts":          ts_iso or (
                dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.timezone.utc).isoformat()
                if ts_ms else ""),
            "book":        book,
            "trades":      trades,
            "or_row":      or_row,
            "vwap_obj":    vwap_obj,
            "flow":        flow or None,
            "gates":       {},      # caller (daemon) fills from session_state()
            "_source":     self.name,
            "_synthetic":  synthetic,
        }
        return snap

    def _synth_nanos(self, ts_ms: Optional[int]) -> int:
        """Monotonic nanosecond counter for synthetic trades. Uses ts_ms if
        present so signal-engine windows align with replay wall-clock."""
        self._synthesized_nanos += 1
        if ts_ms is not None:
            return ts_ms * 1_000_000 + self._synthesized_nanos
        return self._synthesized_nanos
