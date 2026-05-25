"""Pin append_signal + read_active.

Contracts:
1. append_signal writes one complete UTF-8 JSON line in a single OS write,
   followed by a newline. A concurrent reader must never see a half-line.
2. read_active TTL-filters (ms-based), dedupes by id (newest timestamp_ms
   wins), tolerates partial/malformed last line without raising.
3. Missing store file -> empty list, no exception.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from pax_ai.ai_chart_signal_store import (
    append_signal, read_active, DEFAULT_TTL_SEC, DEFAULT_MAX_ROWS,
)


def _sig(**kw):
    base = dict(
        id="a",
        alias="NQM6.CME@RITHMIC",
        label="OR-H",
        price=20000.0,
        side="above",
        action="PAY_FOR_TRADE",
        direction="LONG",
        confidence=0.5,
        reason="r",
        timestamp_ms=0,
        source="pax_ai",
    )
    base.update(kw)
    return base


def test_append_then_read(tmp_path):
    p = tmp_path / "store.jsonl"
    append_signal(_sig(), store_path=p)
    out = read_active(store_path=p, now_ms=1)
    assert len(out) == 1
    assert out[0]["id"] == "a"


def test_ttl_drops_expired(tmp_path):
    p = tmp_path / "store.jsonl"
    append_signal(_sig(), store_path=p)
    too_late = (DEFAULT_TTL_SEC + 10) * 1000
    assert read_active(store_path=p, now_ms=too_late) == []


def test_partial_last_line_is_skipped(tmp_path):
    """Reader must tolerate a partial/truncated final line (e.g. from a
    concurrent append in flight on a non-atomic platform)."""
    p = tmp_path / "store.jsonl"
    append_signal(_sig(id="a"), store_path=p)
    # Now append a partial JSON line (NO trailing newline).
    with open(p, "ab") as f:
        f.write(b'{"id":"b","timest')
    out = read_active(store_path=p, now_ms=1)
    assert len(out) == 1
    assert out[0]["id"] == "a"


def test_malformed_json_in_middle_skipped(tmp_path):
    p = tmp_path / "store.jsonl"
    p.write_text(
        "not json\n"
        + json.dumps(_sig(id="a")) + "\n",
        encoding="utf-8")
    out = read_active(store_path=p, now_ms=1)
    assert len(out) == 1
    assert out[0]["id"] == "a"


def test_dedup_keeps_newest_by_id(tmp_path):
    p = tmp_path / "store.jsonl"
    append_signal(_sig(id="a", confidence=0.4, reason="old", timestamp_ms=0), store_path=p)
    append_signal(_sig(id="a", confidence=0.8, reason="new", timestamp_ms=100), store_path=p)
    out = read_active(store_path=p, now_ms=200)
    assert len(out) == 1
    assert out[0]["confidence"] == 0.8


def test_missing_file_returns_empty():
    out = read_active(store_path=Path("/nonexistent/path.jsonl"),
                       now_ms=int(time.time() * 1000))
    assert out == []


def test_max_rows_cap(tmp_path):
    p = tmp_path / "store.jsonl"
    for i in range(DEFAULT_MAX_ROWS + 10):
        append_signal(_sig(id=f"id{i}", timestamp_ms=i), store_path=p)
    out = read_active(store_path=p, now_ms=DEFAULT_MAX_ROWS + 100)
    assert len(out) == DEFAULT_MAX_ROWS
    # Newest survive
    assert out[-1]["id"] == f"id{DEFAULT_MAX_ROWS + 9}"


def test_concurrent_append_does_not_corrupt(tmp_path):
    """Stress the atomic-write contract: 50 threads each append one signal;
    the file MUST contain 50 complete, parseable lines (one per signal),
    no half-lines, no interleaved bytes."""
    p = tmp_path / "store.jsonl"
    barrier = threading.Barrier(50)
    def worker(i):
        barrier.wait()
        append_signal(_sig(id=f"id{i}", timestamp_ms=i), store_path=p)
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    for t in threads: t.start()
    for t in threads: t.join()
    text = p.read_text(encoding="utf-8")
    lines = [L for L in text.split("\n") if L]
    assert len(lines) == 50
    for L in lines:
        # Must round-trip; any half-line or interleave breaks json.loads.
        obj = json.loads(L)
        assert obj["source"] == "pax_ai"
