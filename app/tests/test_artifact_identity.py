from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from model_buk.artifact_identity import (
    ArtifactIdentityError,
    build_artifact_manifest,
    fingerprint_files,
    sha256_file,
)


def test_sha256_file_is_streaming_compatible_and_deterministic(tmp_path: Path) -> None:
    source = tmp_path / "private-model.bin"
    payload = b"model-bytes\x00" * 100
    source.write_bytes(payload)

    expected = hashlib.sha256(payload).hexdigest()
    assert sha256_file(source, chunk_size=3) == expected
    assert sha256_file(source, chunk_size=17) == expected


def test_fingerprint_of_named_set_is_order_independent_but_content_sensitive(tmp_path: Path) -> None:
    first = tmp_path / "first.bin"
    second = tmp_path / "second.bin"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    left = fingerprint_files({"weights.bin": first, "features.bin": second}, chunk_size=2)
    right = fingerprint_files(
        [("features.bin", second), ("weights.bin", first)], chunk_size=11
    )
    assert left == right

    second.write_bytes(b"changed")
    assert fingerprint_files({"weights.bin": first, "features.bin": second}) != left


def test_manifest_contains_only_logical_names_sizes_hashes_and_engine_id(tmp_path: Path) -> None:
    source = tmp_path / "secret" / "weights.bin"
    source.parent.mkdir()
    source.write_bytes(b"weights")

    manifest = build_artifact_manifest("pure", "2026.09", {"model/weights.bin": source})
    encoded = json.dumps(manifest)

    assert manifest["artifact_name"] == "pure"
    assert manifest["artifact_version"] == "2026.09"
    assert manifest["engine_id"].startswith("pure:2026.09:")
    assert manifest["engine_id"].endswith(manifest["fingerprint"])
    assert manifest["files"] == [
        {
            "name": "model/weights.bin",
            "size_bytes": len(b"weights"),
            "sha256": hashlib.sha256(b"weights").hexdigest(),
        }
    ]
    assert str(tmp_path) not in encoded
    assert "secret" not in encoded


@pytest.mark.parametrize(
    "files, message",
    [
        ({"same": "missing-a", "other": "missing-b"}, "missing"),
        ({"a": "."}, "directory"),
        ([('.', "missing")], "logical file names"),
    ],
)
def test_invalid_sources_are_rejected(tmp_path: Path, files: object, message: str) -> None:
    # Resolve the two relative fixtures here while retaining logical names.
    if files == {"same": "missing-a", "other": "missing-b"}:
        files = {"same": tmp_path / "missing-a", "other": tmp_path / "missing-b"}
    elif files == {"a": "."}:
        files = {"a": tmp_path}
    else:
        files = [("../private.bin", tmp_path / "missing")]
    with pytest.raises((ArtifactIdentityError, FileNotFoundError), match=message):
        fingerprint_files(files)  # type: ignore[arg-type]


def test_duplicate_and_absolute_logical_names_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "data"
    source.write_bytes(b"data")
    with pytest.raises(ArtifactIdentityError, match="duplicate"):
        fingerprint_files([("data.bin", source), ("data.bin", source)])
    with pytest.raises(ArtifactIdentityError, match="absolute"):
        fingerprint_files({str(tmp_path / "private.bin"): source})


def test_empty_inputs_and_invalid_chunk_size_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ArtifactIdentityError, match="at least one"):
        fingerprint_files([])
    source = tmp_path / "data"
    source.write_bytes(b"data")
    with pytest.raises(ValueError, match="chunk_size"):
        sha256_file(source, chunk_size=0)
