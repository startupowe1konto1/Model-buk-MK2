"""Stable, privacy-preserving identities for model and data artifacts.

The source paths supplied to this module are only used while reading files.  A
manifest contains logical names, sizes and SHA-256 digests; it never contains
the source path.  This makes the resulting identity safe to persist and to
compare between machines.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from os import PathLike
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, TypeAlias


DEFAULT_CHUNK_SIZE = 1024 * 1024
MANIFEST_SCHEMA_VERSION = 1

SourcePath: TypeAlias = str | PathLike[str]
FileInput: TypeAlias = (
    Mapping[str, SourcePath]
    | Iterable[tuple[str, SourcePath]]
    | Iterable[SourcePath]
)


class ArtifactIdentityError(ValueError):
    """Raised when artifact inputs cannot form a deterministic identity."""


@dataclass(frozen=True, slots=True)
class ArtifactFile:
    """The persisted, path-free description of one artifact input."""

    name: str
    size_bytes: int
    sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class ArtifactManifest:
    """Versioned identity plus path-free file metadata."""

    name: str
    version: str
    fingerprint: str
    files: tuple[ArtifactFile, ...]

    @property
    def engine_id(self) -> str:
        return make_engine_id(self.name, self.version, self.fingerprint)

    @property
    def total_size_bytes(self) -> int:
        return sum(item.size_bytes for item in self.files)

    def as_dict(self) -> dict[str, Any]:
        files = [item.as_dict() for item in self.files]
        return {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "artifact_name": self.name,
            "artifact_version": self.version,
            "engine_id": self.engine_id,
            "fingerprint": self.fingerprint,
            # Keep an explicit sha256 field convenient for consumers that use
            # the same field name for file and set digests.
            "sha256": self.fingerprint,
            "total_size_bytes": self.total_size_bytes,
            "files": files,
        }


def _validate_chunk_size(chunk_size: int) -> int:
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")
    return chunk_size


def _source_path(value: SourcePath) -> Path:
    try:
        return Path(value)
    except (TypeError, ValueError) as exc:
        raise ArtifactIdentityError("artifact source must be a filesystem path") from exc


def _validate_file(path: Path, logical_name: str | None = None) -> None:
    # Deliberately do not include ``path`` in errors: callers can safely log
    # these messages without leaking a private absolute path.
    label = logical_name or "artifact file"
    if not path.exists():
        raise FileNotFoundError(f"missing {label}")
    if path.is_dir():
        raise ArtifactIdentityError(f"{label} must be a file, not a directory")
    if not path.is_file():
        raise ArtifactIdentityError(f"{label} is not a regular file")


def _logical_name(value: Any) -> str:
    if not isinstance(value, str):
        raise ArtifactIdentityError("logical file names must be strings")
    name = value.strip()
    if not name or "\x00" in name:
        raise ArtifactIdentityError("logical file names must be non-empty")
    # Both path syntaxes are checked because manifests can move between
    # Windows and POSIX hosts.  Relative nested logical names are allowed.
    if PurePosixPath(name).is_absolute() or PureWindowsPath(name).is_absolute():
        raise ArtifactIdentityError("logical file names must not be absolute paths")
    if any(part == ".." for part in name.replace("\\", "/").split("/")):
        raise ArtifactIdentityError("logical file names must not escape their namespace")
    return name


def _normalise_inputs(files: FileInput | SourcePath) -> list[tuple[str, Path]]:
    if isinstance(files, (str, PathLike)):
        path = _source_path(files)
        return [(_logical_name(path.name), path)]

    if isinstance(files, Mapping):
        raw_items: Iterable[tuple[Any, Any]] = files.items()
    else:
        try:
            values = list(files)
        except TypeError as exc:
            raise ArtifactIdentityError("files must be a path, mapping, or iterable") from exc
        raw_items_list: list[tuple[Any, Any]] = []
        for value in values:
            if isinstance(value, (str, PathLike)):
                path = _source_path(value)
                raw_items_list.append((path.name, path))
            else:
                try:
                    logical_name, source = value
                except (TypeError, ValueError) as exc:
                    raise ArtifactIdentityError(
                        "file entries must be paths or (logical_name, path) pairs"
                    ) from exc
                raw_items_list.append((logical_name, _source_path(source)))
        raw_items = raw_items_list

    normalised: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for raw_name, path in raw_items:
        name = _logical_name(raw_name)
        if name in seen:
            raise ArtifactIdentityError(f"duplicate logical file name: {name}")
        seen.add(name)
        _validate_file(path, name)
        normalised.append((name, path))
    if not normalised:
        raise ArtifactIdentityError("at least one artifact file is required")
    return sorted(normalised, key=lambda item: item[0])


def sha256_file(path: SourcePath, *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
    """Return the raw SHA-256 digest of one file, reading it in chunks."""

    chunk_size = _validate_chunk_size(chunk_size)
    source = _source_path(path)
    _validate_file(source)
    digest = sha256()
    with source.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


# A descriptive alias for callers that prefer ``fingerprint`` terminology.
fingerprint_file = sha256_file


def _file_records(
    files: FileInput | SourcePath,
    *,
    chunk_size: int,
) -> list[ArtifactFile]:
    return [
        ArtifactFile(name, path.stat().st_size, sha256_file(path, chunk_size=chunk_size))
        for name, path in _normalise_inputs(files)
    ]


def _fingerprint_records(records: Iterable[ArtifactFile]) -> str:
    """Hash sorted metadata with length framing to avoid ambiguous joins."""

    digest = sha256()
    digest.update(b"model-buk-artifact-fingerprint-v1\0")
    for record in records:
        encoded_name = record.name.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(8, "big"))
        digest.update(encoded_name)
        digest.update(record.size_bytes.to_bytes(8, "big"))
        digest.update(bytes.fromhex(record.sha256))
    return digest.hexdigest()


def fingerprint_files(
    files: FileInput | SourcePath,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> str:
    """Return a deterministic SHA-256 fingerprint for explicit file inputs.

    File bytes are read incrementally.  For multiple files, sorted logical
    names, byte sizes and per-file digests are framed into a second SHA-256
    stream, so ordering and source paths cannot affect the result.
    """

    chunk_size = _validate_chunk_size(chunk_size)
    return _fingerprint_records(_file_records(files, chunk_size=chunk_size))


def make_engine_id(name: str, version: str, fingerprint: str) -> str:
    """Build an engine id that visibly carries name, version and full digest."""

    for field, value in (("name", name), ("version", version)):
        if not isinstance(value, str) or not value.strip():
            raise ArtifactIdentityError(f"artifact {field} must be non-empty")
        if any(char in value for char in "/\\\x00\r\n"):
            raise ArtifactIdentityError(f"artifact {field} must not contain path separators")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise ArtifactIdentityError("fingerprint must be a SHA-256 hexadecimal digest")
    try:
        int(fingerprint, 16)
    except ValueError as exc:
        raise ArtifactIdentityError("fingerprint must be a SHA-256 hexadecimal digest") from exc
    return f"{name.strip()}:{version.strip()}:{fingerprint.lower()}"


def build_artifact_manifest(
    name: str,
    version: str,
    files: FileInput | SourcePath,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> dict[str, Any]:
    """Create a JSON-serialisable, path-free artifact manifest."""

    chunk_size = _validate_chunk_size(chunk_size)
    records = tuple(_file_records(files, chunk_size=chunk_size))
    manifest = ArtifactManifest(
        name=name.strip() if isinstance(name, str) else name,
        version=version.strip() if isinstance(version, str) else version,
        fingerprint=_fingerprint_records(records),
        files=records,
    )
    # Validate name/version through engine_id even if the caller only needs a
    # manifest.  This prevents malformed values from entering persisted state.
    make_engine_id(manifest.name, manifest.version, manifest.fingerprint)
    return manifest.as_dict()


# Convenient aliases used by callers that name this operation "identity" or
# "versioning" rather than "manifest".
create_artifact_manifest = build_artifact_manifest
artifact_fingerprint = fingerprint_files


__all__ = [
    "ArtifactFile",
    "ArtifactIdentityError",
    "ArtifactManifest",
    "MANIFEST_SCHEMA_VERSION",
    "artifact_fingerprint",
    "build_artifact_manifest",
    "create_artifact_manifest",
    "fingerprint_file",
    "fingerprint_files",
    "make_engine_id",
    "sha256_file",
]
