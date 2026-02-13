"""Pydantic models for Nix source tracking (sources.json).

Defines the schema for package source entries, hashes, and the
top-level sources file used by the update machinery.
"""

import json
import os
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .hash import NixHash  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import Mapping

# ---------------------------------------------------------------------------
# SRI hash validation (sha256-only, matching the existing sources.json format)
# ---------------------------------------------------------------------------

_SRI_HASH_PATTERN = re.compile(r"^sha256-[A-Za-z0-9+/]+=*$")


def _validate_sri_hash(value: str) -> str:
    if not _SRI_HASH_PATTERN.match(value):
        msg = f"Hash must be in SRI format (sha256-...): {value!r}"
        raise ValueError(msg)
    return value


# ---------------------------------------------------------------------------
# Literal types
# ---------------------------------------------------------------------------

HashType = Literal[
    "cargoHash",
    "denoDepsHash",
    "nodeModulesHash",  # For node_modules built via bun/custom builders
    "npmDepsHash",
    "sha256",
    "srcHash",
    "spectaOutputHash",  # For specta git dependency hash
    "tauriOutputHash",  # For tauri git dependency hash
    "tauriSpectaOutputHash",  # For tauri-specta git dependency hash
    "uvLockHash",  # For uv.lock fixed-output derivation hash
    "vendorHash",
]

# ---------------------------------------------------------------------------
# HashEntry
# ---------------------------------------------------------------------------


class HashEntry(BaseModel):
    """A single structured hash entry in ``sources.json``."""

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )

    hash_type: HashType = Field(alias="hashType")
    hash: NixHash
    platform: str | None = None  # Optional platform for platform-specific hashes
    url: str | None = None
    urls: dict[str, str] | None = None
    git_dep: str | None = Field(
        default=None,
        alias="gitDep",
    )  # Git dependency name (for importCargoLock)

    @field_validator("hash")
    @classmethod
    def validate_hash(cls, v: str) -> str:
        """Validate hash values are sha256 SRI strings."""
        return _validate_sri_hash(v)

    @classmethod
    def create(  # noqa: PLR0913
        cls,
        hash_type: HashType,
        hash_value: str,
        *,
        git_dep: str | None = None,
        platform: str | None = None,
        url: str | None = None,
        urls: dict[str, str] | None = None,
    ) -> HashEntry:
        """Build a validated hash entry from plain values."""
        return cls.model_validate(
            {
                "gitDep": git_dep,
                "hashType": hash_type,
                "hash": hash_value,
                "platform": platform,
                "url": url,
                "urls": urls,
            },
        )

    def to_dict(self) -> dict[str, Any]:
        """Return this entry as a stable, JSON-serializable mapping."""
        return dict(
            sorted(
                {
                    k: v
                    for k, v in {
                        "gitDep": self.git_dep,
                        "hash": self.hash,
                        "hashType": self.hash_type,
                        "platform": self.platform,
                        "url": self.url,
                        "urls": self.urls,
                    }.items()
                    if v is not None
                }.items(),
            ),
        )


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

type HashMapping = dict[str, str]
type HashEntries = list[HashEntry]
type SourceHashes = HashMapping | HashEntries

# ---------------------------------------------------------------------------
# HashCollection
# ---------------------------------------------------------------------------


class HashCollection(BaseModel):
    """Either a list of hash entries or a platform-to-hash mapping."""

    model_config = ConfigDict(extra="forbid")

    entries: HashEntries | None = None
    mapping: HashMapping | None = None

    @model_validator(mode="before")
    @classmethod
    def parse_input(cls, data: object) -> dict[str, object]:
        """Normalize list/dict input into the model's internal shape."""
        if isinstance(data, dict):
            data_dict = cast("dict[str, object]", data)
            if "entries" in data or "mapping" in data:
                return data_dict
            for hash_value in data_dict.values():
                if not isinstance(hash_value, str):
                    msg = "Hash mapping values must be strings"
                    raise TypeError(msg)
                _validate_sri_hash(hash_value)
            return {"mapping": cast("HashMapping", data_dict)}
        if isinstance(data, list):
            return {"entries": data}
        if isinstance(data, HashCollection):
            return {"entries": data.entries, "mapping": data.mapping}
        msg = "Hashes must be a list or dict"
        raise ValueError(msg)

    def to_json(self) -> dict[str, Any] | list[dict[str, Any]]:
        """Return hashes in their canonical JSON representation."""
        if self.entries is not None:
            return [entry.to_dict() for entry in self.entries]
        if self.mapping is not None:
            return dict(self.mapping)
        return {}

    def primary_hash(self) -> str | None:
        """Return the single effective hash when one can be inferred."""
        if self.entries and len(self.entries) == 1:
            return self.entries[0].hash
        if self.mapping:
            values = list(self.mapping.values())
            if len(set(values)) == 1:
                return values[0]
        return None

    @classmethod
    def from_value(cls, data: SourceHashes) -> HashCollection:
        """Create a collection from either accepted hash representation."""
        return cls.model_validate(data)

    FAKE_HASH_PREFIX: ClassVar[str] = os.environ.get(
        "UPDATE_FAKE_HASH",
        "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    )

    def merge(self, other: HashCollection) -> HashCollection:
        """Merge *other* into this collection (last-wins, skips fake hashes)."""
        if self.entries is not None and other.entries is not None:
            by_key: dict[
                tuple[
                    str | None,
                    str | None,
                    str | None,
                    str | None,
                    tuple[tuple[str, str], ...] | None,
                ],
                HashEntry,
            ] = {}
            for entry in [*self.entries, *other.entries]:
                if entry.hash.startswith(self.FAKE_HASH_PREFIX):
                    continue
                urls_key = (
                    tuple(sorted(entry.urls.items()))
                    if entry.urls is not None
                    else None
                )
                key = (
                    entry.hash_type,
                    entry.platform,
                    entry.git_dep,
                    entry.url,
                    urls_key,
                )
                by_key[key] = entry  # last wins
            return HashCollection(entries=list(by_key.values()))
        if self.mapping is not None and other.mapping is not None:
            merged: dict[str, str] = {}
            for d in (self.mapping, other.mapping):
                for platform, hash_val in d.items():
                    if hash_val.startswith(self.FAKE_HASH_PREFIX):
                        continue
                    merged[platform] = hash_val  # last wins
            return HashCollection(mapping=merged)
        if self.entries is not None and other.mapping is not None:
            msg = "Cannot merge hash entries with hash mapping"
            raise ValueError(msg)
        if self.mapping is not None and other.entries is not None:
            msg = "Cannot merge hash mapping with hash entries"
            raise ValueError(msg)
        return other


# ---------------------------------------------------------------------------
# SourceEntry
# ---------------------------------------------------------------------------


class SourceEntry(BaseModel):
    """A package source entry containing hashes and source metadata."""

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )

    hashes: HashCollection
    version: str | None = None
    input: str | None = None
    urls: dict[str, str] | None = None
    commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    drv_hash: str | None = Field(default=None, alias="drvHash")

    def to_dict(self) -> dict[str, Any]:
        """Return this source entry as a stable, JSON-serializable mapping."""
        return dict(
            sorted(
                {
                    k: v
                    for k, v in {
                        "drvHash": self.drv_hash,
                        "hashes": self.hashes.to_json(),
                        "commit": self.commit,
                        "input": self.input,
                        "urls": self.urls,
                        "version": self.version,
                    }.items()
                    if v is not None
                }.items(),
            ),
        )

    def merge(self, other: SourceEntry) -> SourceEntry:
        """Merge *other* into this entry (other takes priority for scalars)."""
        merged_hashes = self.hashes.merge(other.hashes)
        merged_urls: dict[str, str] | None = None
        if self.urls or other.urls:
            merged_urls = {**(self.urls or {}), **(other.urls or {})}
        return SourceEntry(
            hashes=merged_hashes,
            version=other.version or self.version,
            input=other.input or self.input,
            urls=merged_urls,
            commit=other.commit or self.commit,
            drv_hash=other.drv_hash or self.drv_hash,
        )


# ---------------------------------------------------------------------------
# SourcesFile
# ---------------------------------------------------------------------------


class SourcesFile(BaseModel):
    """In-memory representation of the top-level ``sources.json`` file."""

    model_config = ConfigDict(extra="forbid")

    entries: dict[str, SourceEntry]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SourcesFile:
        """Parse raw ``sources.json`` data into typed source entries."""
        entries = {}
        for name, entry in data.items():
            if name == "$schema":
                continue
            entries[name] = SourceEntry.model_validate(entry)
        return cls(entries=entries)

    @classmethod
    def load(cls, path: Path) -> SourcesFile:
        """Load ``sources.json`` from *path*, returning an empty file if missing."""
        if not path.exists():
            return cls(entries={})
        return cls.from_dict(json.loads(path.read_text()))

    def to_dict(self) -> dict[str, Any]:
        """Serialize all entries to plain Python objects."""
        return {name: entry.to_dict() for name, entry in self.entries.items()}

    def merge(self, other: SourcesFile) -> SourcesFile:
        """Merge *other* into this file (union of entries, per-entry merge)."""
        merged = dict(self.entries)
        for name, entry in other.entries.items():
            if name in merged:
                merged[name] = merged[name].merge(entry)
            else:
                merged[name] = entry
        return SourcesFile(entries=merged)

    def save(self, path: Path) -> None:
        """Atomically write the file contents to *path*."""
        data = self.to_dict()
        payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = path.stat().st_mode & 0o777 if path.exists() else None
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as tmp_file:
                tmp_file.write(payload)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
                tmp_path = Path(tmp_file.name)
                if mode is not None:
                    os.fchmod(tmp_file.fileno(), mode)
            tmp_path.replace(path)
        finally:
            if tmp_path is not None and tmp_path.exists():
                tmp_path.unlink()

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        """Return the JSON schema for the top-level ``sources.json`` object."""
        entry_schema = SourceEntry.model_json_schema()
        defs = dict(entry_schema.get("$defs", {}))

        source_entry_def = {
            k: v for k, v in entry_schema.items() if k not in ("$defs", "$schema")
        }
        defs["SourceEntry"] = source_entry_def

        defs["HashCollection"] = {
            "title": "Hashes",
            "description": "Hashes as list of entries or platform-to-hash mapping",
            "oneOf": [
                {
                    "type": "array",
                    "items": {"$ref": "#/$defs/HashEntry"},
                    "description": "List of structured hash entries",
                },
                {
                    "type": "object",
                    "additionalProperties": {
                        "type": "string",
                        "pattern": "^sha256-[A-Za-z0-9+/]+=*$",
                    },
                    "description": "Platform to SRI hash mapping",
                },
            ],
        }

        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "Nix Sources",
            "description": "Source package versions and hashes for Nix derivations",
            "type": "object",
            "additionalProperties": {
                "$ref": "#/$defs/SourceEntry",
            },
            "$defs": defs,
        }
