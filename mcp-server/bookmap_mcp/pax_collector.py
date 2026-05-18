"""P3 — Proximity-triggered data collector.

When `or_levels.inProximity` flips False→True, opens a recording file in
D:\\BookmapLogs\\pax-recordings\\YYYYMMDD_HHMMSS_LEVEL.csv and spools every
subsequent snapshot's tick state until proximity flips back to False.

When proximity ends, writes a summary row with TREND or REGRESSION tag based
on price exit direction relative to the level:
  TREND      — price exited past the level (beyond the rung extension)
  REGRESSION — price exited back inside the OR / prior rung
  TIMEOUT    — proximity ended but neither TREND nor REGRESSION clear

Public API:
  collector = PaxCollector(out_dir=PAX_LOG_DIR / "pax-recordings")
  collector.tick(snap)   # call every poll
"""

from __future__ import annotations

import csv
import datetime as dt
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


PAX_LOG_DIR = Path(os.environ.get("PAX_LOG_DIR", r"D:\BookmapLogs"))
DEFAULT_OUT  = PAX_LOG_DIR / "pax-recordings"


RECORDING_COLUMNS = [
    "ts_utc", "ts_ct", "mid", "best_bid", "best_ask", "spread",
    "level", "level_price", "level_decision", "level_confidence",
    "regime", "regime_conf", "biasScore", "biasTraj",
    "vwapSlope", "vwapBias", "vpBias", "conviction_score", "conviction_trend",
    "ofiZ", "cvdDeltaZ", "vptZ",
    "pax_decision", "pax_size", "pax_size_tier",
]

SUMMARY_COLUMNS = [
    "session", "level", "level_price", "level_decision",
    "started_utc", "ended_utc", "duration_sec", "n_rows",
    "entry_mid", "exit_mid", "max_fav_pts", "max_adv_pts",
    "label", "exit_reason",
]


class _Recording:
    __slots__ = ("path", "level_label", "level_price", "level_decision",
                 "started_utc", "started_mid", "rows_written",
                 "max_fav", "max_adv", "_fh", "_writer")

    def __init__(self, path: Path, level: Dict[str, Any], snap: Dict[str, Any]):
        self.path = path
        self.level_label = level.get("label", "?")
        self.level_price = float(level.get("price") or 0)
        self.level_decision = level.get("decision", "WAIT")
        self.started_utc = dt.datetime.now(dt.timezone.utc)
        self.started_mid = _safe(snap.get("book", {}).get("mid"), self.level_price)
        self.rows_written = 0
        self.max_fav = 0.0
        self.max_adv = 0.0
        self._fh = open(path, "w", encoding="utf-8", newline="")
        self._writer = csv.DictWriter(self._fh, fieldnames=RECORDING_COLUMNS)
        self._writer.writeheader()

    def write_row(self, snap: Dict[str, Any]) -> None:
        book = snap.get("book") or {}
        flow = snap.get("flow") or {}
        conv = snap.get("conviction") or {}
        pax = snap.get("pax") or {}
        mid = _safe(book.get("mid"))
        if mid is not None:
            # Track max favorable/adverse excursion relative to direction inferred
            # from level_decision
            d = self.level_decision or ""
            sign = +1 if "LONG" in d else (-1 if "SHORT" in d else 0)
            dev = (mid - self.started_mid) * sign
            if dev > self.max_fav: self.max_fav = dev
            if dev < self.max_adv: self.max_adv = dev
        now = dt.datetime.now(dt.timezone.utc)
        self._writer.writerow({
            "ts_utc": now.isoformat(timespec="seconds"),
            "ts_ct":  now.astimezone(dt.timezone(dt.timedelta(hours=-5))).isoformat(timespec="seconds"),
            "mid": mid, "best_bid": book.get("bestBid"), "best_ask": book.get("bestAsk"),
            "spread": book.get("spread"),
            "level": self.level_label, "level_price": self.level_price,
            "level_decision": self.level_decision,
            "level_confidence": _safe(_find_level_field(snap, self.level_label, "confidence")),
            "regime": flow.get("regime"), "regime_conf": flow.get("regimeConfidence"),
            "biasScore": flow.get("biasScore"), "biasTraj": flow.get("biasTrajectory"),
            "vwapSlope": (flow.get("vwapSlope") or {}).get("label"),
            "vwapBias": (snap.get("vwap_bias") or {}).get("label"),
            "vpBias":   (snap.get("vp_bias") or {}).get("label"),
            "conviction_score": conv.get("score"),
            "conviction_trend": conv.get("trend"),
            "ofiZ":      flow.get("ofiZ"),
            "cvdDeltaZ": flow.get("cvdDeltaZ"),
            "vptZ":      flow.get("vptZ"),
            "pax_decision": pax.get("decision"),
            "pax_size": pax.get("size"),
            "pax_size_tier": pax.get("size_tier"),
        })
        self.rows_written += 1

    def close(self, exit_mid: Optional[float], exit_reason: str) -> Dict[str, Any]:
        try: self._fh.close()
        except Exception: pass
        ended = dt.datetime.now(dt.timezone.utc)
        # Decide TREND/REGRESSION/TIMEOUT
        d = self.level_decision or ""
        sign = +1 if "LONG" in d else (-1 if "SHORT" in d else 0)
        label = "TIMEOUT"
        if exit_mid is not None and sign != 0:
            move = (exit_mid - self.level_price) * sign
            if   move >  3.0: label = "TREND"
            elif move < -3.0: label = "REGRESSION"
        elif exit_mid is not None:
            # No direction known — classify by magnitude only
            move = abs(exit_mid - self.level_price)
            label = "TREND" if move > 3.0 else "TIMEOUT"
        summary = {
            "session": self.started_utc.date().isoformat(),
            "level": self.level_label, "level_price": self.level_price,
            "level_decision": self.level_decision,
            "started_utc": self.started_utc.isoformat(timespec="seconds"),
            "ended_utc":   ended.isoformat(timespec="seconds"),
            "duration_sec": int((ended - self.started_utc).total_seconds()),
            "n_rows": self.rows_written,
            "entry_mid": round(self.started_mid, 2) if self.started_mid else None,
            "exit_mid":  round(exit_mid, 2) if exit_mid else None,
            "max_fav_pts": round(self.max_fav, 2),
            "max_adv_pts": round(self.max_adv, 2),
            "label": label, "exit_reason": exit_reason,
        }
        return summary


class PaxCollector:
    """Watches snapshot for proximity transitions, manages recordings."""

    def __init__(self, out_dir: Optional[Path] = None,
                 summary_file: Optional[Path] = None) -> None:
        self.out_dir = Path(out_dir) if out_dir else DEFAULT_OUT
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.summary_file = Path(summary_file) if summary_file else \
                             (self.out_dir / "_summary.csv")
        if not self.summary_file.exists():
            with open(self.summary_file, "w", encoding="utf-8", newline="") as fh:
                csv.DictWriter(fh, fieldnames=SUMMARY_COLUMNS).writeheader()
        self._active: Optional[_Recording] = None
        self._last_proxy_level: Optional[str] = None

    def tick(self, snap: Dict[str, Any]) -> Dict[str, Any]:
        ol = (snap or {}).get("or_levels") or {}
        levels = ol.get("levels") or []
        prox_now = [l for l in levels if l.get("proximity")]
        # Pick the closest proximate level to mid
        if prox_now:
            prox_now.sort(key=lambda l: abs(float(l.get("distance") or 0)))
            current_level = prox_now[0]
            current_label = current_level.get("label")
        else:
            current_level = None
            current_label = None

        # State machine
        action: Optional[str] = None
        summary_emitted: Optional[Dict[str, Any]] = None

        # Case 1: no recording, proximity entered → start one
        if self._active is None and current_level is not None:
            self._start(current_level, snap)
            action = "STARTED"

        # Case 2: active recording, level still in proximity → write row
        elif self._active is not None and current_level is not None:
            # If level changed, close old and start new
            if current_label != self._active.level_label:
                summary_emitted = self._close_with_exit(snap, "LEVEL_CHANGE")
                self._start(current_level, snap)
                action = "ROTATED"
            else:
                self._active.write_row(snap)
                action = "ROW"

        # Case 3: active recording, proximity exited → close + write summary
        elif self._active is not None and current_level is None:
            summary_emitted = self._close_with_exit(snap, "PROX_EXIT")
            action = "CLOSED"

        self._last_proxy_level = current_label

        return {
            "action": action,
            "active": bool(self._active),
            "rows":   self._active.rows_written if self._active else 0,
            "level":  self._active.level_label if self._active else None,
            "summary": summary_emitted,
        }

    def _start(self, level: Dict[str, Any], snap: Dict[str, Any]) -> None:
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_lvl = (level.get("label") or "X").replace("/", "_").replace("+", "p").replace("-", "m")
        path = self.out_dir / f"{ts}_{safe_lvl}.csv"
        self._active = _Recording(path, level, snap)
        # First row
        self._active.write_row(snap)

    def _close_with_exit(self, snap: Dict[str, Any], reason: str) -> Dict[str, Any]:
        rec = self._active
        if rec is None: return {}
        mid = _safe((snap.get("book") or {}).get("mid"))
        summary = rec.close(mid, reason)
        # Append summary row
        try:
            with open(self.summary_file, "a", encoding="utf-8", newline="") as fh:
                csv.DictWriter(fh, fieldnames=SUMMARY_COLUMNS).writerow(summary)
        except Exception:
            pass
        self._active = None
        return summary


# ─── helpers ────────────────────────────────────────────────────────────────

def _safe(x: Any, default: Optional[float] = None) -> Optional[float]:
    if x is None: return default
    try:
        f = float(x)
        if f != f: return default
        return f
    except (TypeError, ValueError):
        return default


def _find_level_field(snap: Dict[str, Any], label: str, field: str) -> Any:
    ol = (snap or {}).get("or_levels") or {}
    for l in ol.get("levels") or []:
        if l.get("label") == label: return l.get(field)
    return None
