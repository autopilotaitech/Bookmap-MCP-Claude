"""Blob store invariants: content-addressed, idempotent, atomic, byte-exact round-trip."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pax_ai import feature_bus


def test_canonical_snapshot_json_is_stable_across_dict_iteration():
    a = {"b": 1, "a": 2, "nested": {"y": 3, "x": 4}}
    b = {"nested": {"x": 4, "y": 3}, "a": 2, "b": 1}
    assert feature_bus._canonical_snapshot_json(a) == feature_bus._canonical_snapshot_json(b)


def test_canonical_snapshot_json_no_whitespace():
    s = feature_bus._canonical_snapshot_json({"a": 1})
    assert " " not in s
    assert "\n" not in s


def test_canonical_snapshot_json_handles_non_jsonable_via_default_str():
    """Path objects, datetimes, etc. should serialize to str rather than raising."""
    from pathlib import Path
    s = feature_bus._canonical_snapshot_json({"p": Path("/a/b")})
    assert "/a/b" in s or "\\\\a\\\\b" in s


def test_write_snapshot_blob_returns_sha_and_creates_file(tmp_path: Path):
    snap = {"alias": "NQM6", "book": {"mid": 23450.5}}
    canon = feature_bus._canonical_snapshot_json(snap)
    expected_sha = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    sha = feature_bus._write_snapshot_blob(canon, ts_ms=1715000000000, root=tmp_path)
    assert sha == expected_sha
    assert (tmp_path / "2024-05-06" / f"{sha}.json").exists()       # 2024-05-06 from ts


def test_write_snapshot_blob_idempotent_on_same_content(tmp_path: Path):
    canon = feature_bus._canonical_snapshot_json({"x": 1})
    sha1 = feature_bus._write_snapshot_blob(canon, ts_ms=1715000000000, root=tmp_path)
    sha2 = feature_bus._write_snapshot_blob(canon, ts_ms=1715000000000, root=tmp_path)
    assert sha1 == sha2
    # File written only once; second write is a no-op (mtime can differ).


def test_write_snapshot_blob_byte_exact_roundtrip(tmp_path: Path):
    snap = {"alias": "NQM6", "book": {"mid": 23450.5, "spread": 0.25}}
    canon = feature_bus._canonical_snapshot_json(snap)
    sha = feature_bus._write_snapshot_blob(canon, ts_ms=1715000000000, root=tmp_path)
    blob = (tmp_path / "2024-05-06" / f"{sha}.json").read_bytes()
    assert blob == canon.encode("utf-8")


def test_write_digest_blob_returns_sha_and_creates_file(tmp_path: Path):
    digest = "STATE alias=NQM6 mid=23450.5\nUSER hi\n"
    expected_sha = hashlib.sha256(digest.encode("utf-8")).hexdigest()
    sha = feature_bus._write_digest_blob(digest, ts_ms=1715000000000, root=tmp_path)
    assert sha == expected_sha
    assert (tmp_path / "2024-05-06" / f"{sha}.txt").exists()


def test_write_digest_blob_byte_exact_roundtrip(tmp_path: Path):
    digest = "x\n"
    sha = feature_bus._write_digest_blob(digest, ts_ms=1715000000000, root=tmp_path)
    blob = (tmp_path / "2024-05-06" / f"{sha}.txt").read_bytes()
    assert blob == digest.encode("utf-8")
