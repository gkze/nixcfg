"""Pydantic models for Nix source tracking (sources.json).

Defines the schema for package source entries, hashes, and the
top-level sources file used by the update machinery.
"""

from __future__ import annotations

import json
import os
import re
from typing import TYPE_CHECKING, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lib.update import io as update_io

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

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
    "rustyV8ArchiveHash",  # Prebuilt librusty_v8 release artifact
    "rustyV8BindingHash",  # Prebuilt rusty_v8 src_binding artifact
    "sha256",
    "srcHash",
    "spectaOutputHash",  # For specta git dependency hash
    "tauriOutputHash",  # For tauri git dependency hash
    "tauriSpectaOutputHash",  # For tauri-specta git dependency hash
    "uvLockHash",  # For uv.lock fixed-output derivation hash
    "vendorHash",
    "yarnAppHash",  # For nested app/yarn.lock fetchYarnDeps caches
    "yarnRootHash",  # For root yarn.lock fetchYarnDeps caches
]

type JsonObject = dict[str, object]

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
    hash: str
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
    def create(
        cls,
        hash_type: HashType,
        hash_value: str,
        **kwargs: object,
    ) -> HashEntry:
        """Build a validated hash entry from plain values."""

        def _optional_str(name: str, value: object) -> str | None:
            if value is None or isinstance(value, str):
                return value
            msg = f"{name} must be a string when provided"
            raise TypeError(msg)

        def _optional_urls(value: object) -> dict[str, str] | None:
            if value is None:
                return None
            if not isinstance(value, dict):
                msg = "urls must be a mapping of platform to URL"
                raise TypeError(msg)
            parsed: dict[str, str] = {}
            for key, item in value.items():
                if not isinstance(key, str) or not isinstance(item, str):
                    msg = "urls must contain only string keys and values"
                    raise TypeError(msg)
                parsed[key] = item
            return parsed

        git_dep = _optional_str("git_dep", kwargs.pop("git_dep", None))
        platform = _optional_str("platform", kwargs.pop("platform", None))
        url = _optional_str("url", kwargs.pop("url", None))
        urls = _optional_urls(kwargs.pop("urls", None))
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            msg = f"Unexpected HashEntry.create kwargs: {unexpected}"
            raise TypeError(msg)

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

    def to_dict(self) -> JsonObject:
        """Return this entry as a stable, JSON-serializable mapping."""
        result: JsonObject = {
            "hash": self.hash,
            "hashType": self.hash_type,
        }
        if self.git_dep is not None:
            result["gitDep"] = self.git_dep
        if self.platform is not None:
            result["platform"] = self.platform
        if self.url is not None:
            result["url"] = self.url
        if self.urls is not None:
            result["urls"] = dict(sorted(self.urls.items()))
        return result

    def equivalence_key(
        self,
    ) -> tuple[
        str,
        str,
        str,
        str,
        tuple[tuple[str, str], ...],
        str,
    ]:
        """Return a stable key for order-insensitive semantic comparison."""
        return (
            self.hash_type,
            "" if self.platform is None else self.platform,
            "" if self.git_dep is None else self.git_dep,
            "" if self.url is None else self.url,
            tuple(sorted((self.urls or {}).items())),
            self.hash,
        )

    def merge_key(
        self,
    ) -> tuple[
        str,
        str | None,
        str | None,
        str | None,
        tuple[tuple[str, str], ...] | None,
    ]:
        """Return this entry's identity for artifact merge conflict checks."""
        urls_key = tuple(sorted(self.urls.items())) if self.urls is not None else None
        return (
            self.hash_type,
            self.platform,
            self.git_dep,
            self.url,
            urls_key,
        )

    def normalized_dict(self) -> JsonObject:
        """Return this entry in canonical form for semantic equality."""
        return self.to_dict()


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
            data_dict: dict[str, object] = {}
            for key, value in data.items():
                if not isinstance(key, str):
                    msg = "Hash mapping keys must be strings"
                    raise TypeError(msg)
                data_dict[key] = value

            if "entries" in data_dict or "mapping" in data_dict:
                return data_dict

            mapping: HashMapping = {}
            for platform, hash_value in data_dict.items():
                if not isinstance(hash_value, str):
                    msg = "Hash mapping values must be strings"
                    raise TypeError(msg)
                _validate_sri_hash(hash_value)
                mapping[platform] = hash_value
            return {"mapping": mapping}
        if isinstance(data, list):
            return {"entries": data}
        if isinstance(data, HashCollection):
            return {"entries": data.entries, "mapping": data.mapping}
        msg = "Hashes must be a list or dict"
        raise ValueError(msg)

    def to_json(self) -> HashMapping | list[JsonObject]:
        """Return hashes in their canonical JSON representation."""
        if self.entries is not None:
            return [
                entry.normalized_dict()
                for entry in sorted(self.entries, key=HashEntry.equivalence_key)
            ]
        if self.mapping is not None:
            return dict(sorted(self.mapping.items()))
        return {}

    def equivalent_to(self, other: HashCollection) -> bool:
        """Return whether *other* has the same semantic hash content."""
        return self.to_json() == other.to_json()

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
            by_key: dict[HashEntryMergeKey, HashEntry] = {}
            for entry in [*self.entries, *other.entries]:
                if entry.hash.startswith(self.FAKE_HASH_PREFIX):
                    continue
                by_key[entry.merge_key()] = entry  # last wins
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

    def merge_artifact(
        self,
        other: HashCollection,
        *,
        platform: str | None,
        baseline: HashCollection | None = None,
    ) -> HashCollection:
        """Merge one CI artifact's hashes, filtering platform-scoped updates."""
        if self.entries is not None and other.entries is not None:
            return HashCollection(
                entries=_merge_hash_entries_for_artifact(
                    self.entries,
                    other.entries,
                    platform=platform,
                    baseline=None if baseline is None else baseline.entries,
                ),
            )
        if self.mapping is not None and other.mapping is not None:
            return HashCollection(
                mapping=_merge_hash_mapping_for_artifact(
                    self.mapping,
                    other.mapping,
                    platform=platform,
                    baseline=None if baseline is None else baseline.mapping,
                ),
            )
        return self.merge(other)


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

    def to_dict(self) -> JsonObject:
        """Return this source entry as a stable, JSON-serializable mapping."""
        result: JsonObject = {
            "hashes": self.hashes.to_json(),
        }
        if self.drv_hash is not None:
            result["drvHash"] = self.drv_hash
        if self.commit is not None:
            result["commit"] = self.commit
        if self.input is not None:
            result["input"] = self.input
        if self.urls is not None:
            result["urls"] = dict(sorted(self.urls.items()))
        if self.version is not None:
            result["version"] = self.version
        return result

    def equivalent_to(self, other: SourceEntry) -> bool:
        """Return whether *other* represents the same semantic source state."""
        return self.to_dict() == other.to_dict()

    def merge(self, other: SourceEntry) -> SourceEntry:
        """Merge *other* into this entry (other takes priority for scalars)."""
        merged_hashes = self.hashes.merge(other.hashes)
        merged_urls: dict[str, str] | None = None
        if self.urls or other.urls:
            merged_urls = {**(self.urls or {}), **(other.urls or {})}
        return SourceEntry.model_validate(
            {
                "hashes": merged_hashes,
                "version": other.version or self.version,
                "input": other.input or self.input,
                "urls": merged_urls,
                "commit": other.commit or self.commit,
                "drvHash": other.drv_hash or self.drv_hash,
            },
        )

    def merge_artifact(
        self,
        other: SourceEntry,
        *,
        platform: str | None,
        baseline: SourceEntry | None = None,
    ) -> SourceEntry:
        """Merge one platform artifact into this source entry."""
        return SourceEntry.model_validate({
            "hashes": self.hashes.merge_artifact(
                other.hashes,
                platform=platform,
                baseline=None if baseline is None else baseline.hashes,
            ),
            "version": _merge_optional_scalar(
                "version",
                self.version,
                other.version,
                baseline=None if baseline is None else baseline.version,
            ),
            "input": _merge_optional_scalar(
                "input",
                self.input,
                other.input,
                baseline=None if baseline is None else baseline.input,
            ),
            "commit": _merge_optional_scalar(
                "commit",
                self.commit,
                other.commit,
                baseline=None if baseline is None else baseline.commit,
            ),
            "drvHash": _merge_drv_hash(
                self.drv_hash,
                other.drv_hash,
                baseline=None if baseline is None else baseline.drv_hash,
            ),
            "urls": _merge_urls(
                self.urls,
                other.urls,
                baseline=None if baseline is None else baseline.urls,
            ),
        })


type HashEntryMergeKey = tuple[
    str,
    str | None,
    str | None,
    str | None,
    tuple[tuple[str, str], ...] | None,
]


def _merge_hash_entries_for_artifact(
    base: list[HashEntry],
    incoming: list[HashEntry],
    *,
    platform: str | None,
    baseline: list[HashEntry] | None = None,
) -> list[HashEntry]:
    by_key: dict[HashEntryMergeKey, HashEntry] = {
        entry.merge_key(): entry for entry in base
    }
    baseline_by_key = (
        {entry.merge_key(): entry for entry in baseline} if baseline is not None else {}
    )

    for entry in incoming:
        if entry.hash.startswith(HashCollection.FAKE_HASH_PREFIX):
            continue
        key = entry.merge_key()

        if entry.platform is None:
            existing = by_key.get(key)
            if existing is not None and existing.hash != entry.hash:
                baseline_entry = baseline_by_key.get(key)
                if baseline_entry is not None:
                    if existing.hash == baseline_entry.hash:
                        by_key[key] = entry
                        continue
                    if entry.hash == baseline_entry.hash:
                        continue
                msg = (
                    "Conflicting non-platform hash entry for "
                    f"{entry.hash_type}: {existing.hash} vs {entry.hash}"
                )
                raise RuntimeError(msg)
            by_key[key] = entry
            continue

        if platform is None:
            by_key[key] = entry
            continue

        if entry.platform == platform:
            by_key[key] = entry

    return list(by_key.values())


def _merge_hash_mapping_for_artifact(
    base: dict[str, str],
    incoming: dict[str, str],
    *,
    platform: str | None,
    baseline: dict[str, str] | None = None,
) -> dict[str, str]:
    merged = dict(base)
    for incoming_platform, hash_value in incoming.items():
        if hash_value.startswith(HashCollection.FAKE_HASH_PREFIX):
            continue
        if platform is not None and incoming_platform != platform:
            continue
        existing = merged.get(incoming_platform)
        if (
            existing is not None
            and incoming_platform != platform
            and existing != hash_value
        ):
            baseline_value = (
                None if baseline is None else baseline.get(incoming_platform)
            )
            if baseline_value is not None:
                if existing == baseline_value:
                    merged[incoming_platform] = hash_value
                    continue
                if hash_value == baseline_value:
                    continue
            msg = (
                "Conflicting non-platform hash mapping for "
                f"{incoming_platform}: {existing} vs {hash_value}"
            )
            raise RuntimeError(msg)
        merged[incoming_platform] = hash_value
    return merged


def _merge_optional_scalar(
    field_name: str,
    existing: str | None,
    incoming: str | None,
    *,
    baseline: str | None = None,
) -> str | None:
    if existing and incoming and existing != incoming:
        if baseline is not None:
            if existing == baseline:
                return incoming
            if incoming == baseline:
                return existing
        msg = f"Conflicting {field_name}: {existing!r} vs {incoming!r}"
        raise RuntimeError(msg)
    return incoming or existing


def _merge_drv_hash(
    existing: str | None,
    incoming: str | None,
    *,
    baseline: str | None = None,
) -> str | None:
    """Keep drvHash only when it is stable across merged roots."""
    if existing is None and incoming is None:
        return baseline
    if existing is None:
        return incoming if baseline is None or incoming == baseline else None
    if incoming is None:
        return existing if baseline is None or existing == baseline else None
    if existing == incoming or (baseline is not None and existing == baseline):
        return incoming
    if baseline is not None and incoming == baseline:
        return existing
    return None


def _merge_urls(
    existing: dict[str, str] | None,
    incoming: dict[str, str] | None,
    *,
    baseline: dict[str, str] | None = None,
) -> dict[str, str] | None:
    if existing is None and incoming is None:
        return None
    merged: dict[str, str] = dict(existing or {})
    for url_key, url_value in (incoming or {}).items():
        existing_value = merged.get(url_key)
        if existing_value is not None and existing_value != url_value:
            baseline_value = None if baseline is None else baseline.get(url_key)
            if baseline_value is not None:
                if existing_value == baseline_value:
                    merged[url_key] = url_value
                    continue
                if url_value == baseline_value:
                    continue
            msg = f"Conflicting urls entry for {url_key!r}: {existing_value!r} vs {url_value!r}"
            raise RuntimeError(msg)
        merged[url_key] = url_value
    return merged


# ---------------------------------------------------------------------------
# SourcesFile
# ---------------------------------------------------------------------------


class SourcesFile(BaseModel):
    """In-memory representation of the top-level ``sources.json`` file."""

    model_config = ConfigDict(extra="forbid")

    entries: dict[str, SourceEntry]

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> SourcesFile:
        """Parse raw ``sources.json`` data into typed source entries."""
        entries: dict[str, SourceEntry] = {}
        for name, entry in data.items():
            if not isinstance(name, str):
                msg = "sources.json top-level keys must be strings"
                raise TypeError(msg)
            if name == "$schema":
                continue
            entries[name] = SourceEntry.model_validate(entry)
        return cls(entries=entries)

    @classmethod
    def load(cls, path: Path) -> SourcesFile:
        """Load ``sources.json`` from *path*, returning an empty file if missing."""
        if not path.exists():
            return cls(entries={})
        raw = json.loads(path.read_text())
        if not isinstance(raw, dict):
            msg = "sources.json top-level value must be a JSON object"
            raise TypeError(msg)
        data: dict[str, object] = {}
        for key, value in raw.items():
            if not isinstance(key, str):
                msg = "sources.json top-level keys must be strings"
                raise TypeError(msg)
            data[key] = value
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, JsonObject]:
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
        payload = json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"
        update_io.atomic_write_text(path, payload, mkdir=True)

    @classmethod
    def json_schema(cls) -> dict[str, object]:
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
