"""UTC cleanup: feature_bus._date_partition and bus_digest._format_ts must
use timezone-aware datetime.fromtimestamp(..., timezone.utc) instead of
the deprecated datetime.utcfromtimestamp(). Both must produce identical
output strings to the prior implementation for the supported ts_ms range.

Also pins via AST scan that no production module references
utcfromtimestamp - prevents regression."""
from __future__ import annotations

import ast
import datetime as _dt
from pathlib import Path

import pytest

from pax_ai import bus_digest, feature_bus


@pytest.mark.parametrize("ts_ms", [
    1,
    86_400_000,             # 1970-01-02 UTC
    1_704_067_200_000,      # 2024-01-01 UTC
    1_715_000_000_000,      # 2024-05-06 UTC
    1_768_478_400_000,      # 2026-01-15 UTC
    2_524_608_000_000,      # 2050-01-01 UTC
])
def test_date_partition_equivalence(ts_ms):
    """The new _date_partition must produce the same YYYY-MM-DD string as
    the deprecated utcfromtimestamp(...).strftime('%Y-%m-%d') would have."""
    got = feature_bus._date_partition(ts_ms)
    expected = _dt.datetime.fromtimestamp(
        ts_ms / 1000.0, _dt.timezone.utc).strftime("%Y-%m-%d")
    assert got == expected


@pytest.mark.parametrize("ts_ms", [
    1,
    86_400_000,
    1_715_000_000_000,
    1_768_478_400_000,
])
def test_format_ts_equivalence(ts_ms):
    got = bus_digest._format_ts(ts_ms)
    expected = _dt.datetime.fromtimestamp(
        ts_ms / 1000.0, _dt.timezone.utc).strftime("%H:%M:%S")
    assert got == expected


def test_format_ts_handles_falsy():
    assert bus_digest._format_ts(0)    == "??:??:??"
    assert bus_digest._format_ts(None) == "??:??:??"


def test_format_ts_handles_overflow():
    """Out-of-range ts must NOT raise; returns sentinel."""
    huge = 10 ** 18
    assert bus_digest._format_ts(huge) == "??:??:??"


def test_no_utcfromtimestamp_in_feature_bus():
    """AST scan: feature_bus.py must not call datetime.utcfromtimestamp."""
    tree = ast.parse(Path(feature_bus.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "utcfromtimestamp":
            pytest.fail(f"feature_bus.py still references utcfromtimestamp "
                          f"at line {node.lineno}")


def test_no_utcfromtimestamp_in_bus_digest():
    """AST scan: bus_digest.py must not call datetime.utcfromtimestamp."""
    tree = ast.parse(Path(bus_digest.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "utcfromtimestamp":
            pytest.fail(f"bus_digest.py still references utcfromtimestamp "
                          f"at line {node.lineno}")
