"""Pydantic models for sources.json and platform mapping utilities."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lib.config import get_config
from lib.exceptions import ValidationError


# =============================================================================
# Type Definitions
# =============================================================================

# Valid Nix platforms
NixPlatform = Literal[
    "aarch64-darwin", "aarch64-linux", "x86_64-linux", "x86_64-darwin", "darwin"
]

# Valid derivation types
DrvType = Literal[
    "buildGoModule",
    "denoDeps",
    "fetchCargoVendor",
    "fetchFromGitHub",
    "fetchNpmDeps",
    "fetchurl",
]

# Valid hash types
HashType = Literal[
    "cargoHash", "denoDepsHash", "npmDepsHash", "sha256", "srcHash", "vendorHash"
]

# SRI hash validation pattern
_SRI_HASH_PATTERN = re.compile(r"^sha256-[A-Za-z0-9+/]+=*$")


def validate_sri_hash(value: str) -> str:
    """Validate that a hash is in SRI format."""
    if not _SRI_HASH_PATTERN.match(value):
        raise ValidationError(
            f"Hash must be in SRI format (sha256-...): {value!r}",
            field_name="hash",
            value=value,
        )
    return value


# =============================================================================
# Platform Mapping
# =============================================================================

# Common platform API mappings
VSCODE_PLATFORMS: dict[str, str] = {
    "aarch64-darwin": "darwin-arm64",
    "aarch64-linux": "linux-arm64",
    "x86_64-linux": "linux-x64",
}


@dataclass(frozen=True)
class PlatformMapping:
    """Unified platform mapping abstraction.

    Provides consistent handling of nix platform -> API/URL key mappings
    used by various updaters.

    Examples:
        # Direct URL mapping
        chrome = PlatformMapping.from_urls({
            "aarch64-darwin": "https://dl.google.com/chrome/mac/universal/stable/googlechrome.dmg",
            "x86_64-linux": "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb",
        })

        # API key mapping
        datagrip = PlatformMapping.from_api_keys({
            "aarch64-darwin": "macM1",
            "aarch64-linux": "linuxARM64",
            "x86_64-linux": "linux",
        })

        # Template-based URLs
        cursor = PlatformMapping.from_template(
            "https://download.cursor.sh/{platform}/cursor.dmg",
            {"aarch64-darwin": "darwin-arm64", "x86_64-linux": "linux-x64"},
        )
    """

    # nix platform -> value (URL, API key, or template placeholder)
    mapping: dict[str, str]
    # Optional URL template with {platform} placeholder
    url_template: str | None = None

    @classmethod
    def from_urls(cls, urls: dict[str, str]) -> PlatformMapping:
        """Create mapping from direct platform -> URL dict."""
        return cls(mapping=urls)

    @classmethod
    def from_api_keys(cls, keys: dict[str, str]) -> PlatformMapping:
        """Create mapping from platform -> API key dict."""
        return cls(mapping=keys)

    @classmethod
    def from_template(
        cls, template: str, platform_keys: dict[str, str]
    ) -> PlatformMapping:
        """Create mapping from URL template and platform key substitutions."""
        return cls(mapping=platform_keys, url_template=template)

    @property
    def platforms(self) -> list[str]:
        """Get list of supported nix platforms."""
        return list(self.mapping.keys())

    def get_url(self, nix_platform: str, **format_args: str) -> str:
        """Get download URL for a platform.

        If url_template is set, substitutes {platform} and any additional format_args.
        Otherwise returns the mapping value directly.
        """
        value = self.mapping.get(nix_platform)
        if value is None:
            raise KeyError(f"Platform {nix_platform} not in mapping")

        if self.url_template:
            return self.url_template.format(platform=value, **format_args)
        return value

    def get_key(self, nix_platform: str) -> str:
        """Get API key or identifier for a platform."""
        value = self.mapping.get(nix_platform)
        if value is None:
            raise KeyError(f"Platform {nix_platform} not in mapping")
        return value

    def items(self) -> list[tuple[str, str]]:
        """Iterate over (nix_platform, value) pairs."""
        return list(self.mapping.items())


# =============================================================================
# Hash Entry Models
# =============================================================================


class HashEntry(BaseModel):
    """Single hash entry for sources.json."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    drv_type: DrvType = Field(alias="drvType")
    hash_type: HashType = Field(alias="hashType")
    hash: str
    platform: str | None = None
    url: str | None = None
    urls: dict[str, str] | None = None

    @field_validator("hash")
    @classmethod
    def _validate_hash(cls, v: str) -> str:
        return validate_sri_hash(v)

    @classmethod
    def create(
        cls,
        drv_type: DrvType,
        hash_type: HashType,
        hash_value: str,
        *,
        platform: str | None = None,
        url: str | None = None,
        urls: dict[str, str] | None = None,
    ) -> HashEntry:
        """Convenience constructor with positional args."""
        return cls(
            drvType=drv_type,
            hashType=hash_type,
            hash=hash_value,
            platform=platform,
            url=url,
            urls=urls,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict with camelCase keys, sorted."""
        return dict(
            sorted(
                {
                    k: v
                    for k, v in {
                        "drvType": self.drv_type,
                        "hash": self.hash,
                        "hashType": self.hash_type,
                        "platform": self.platform,
                        "url": self.url,
                        "urls": self.urls,
                    }.items()
                    if v is not None
                }.items()
            )
        )


# Type for hashes - either list of entries or platform->hash mapping
SourceHashes = dict[str, str] | list[HashEntry]


class HashCollection(BaseModel):
    """Collection of hashes - either structured entries or platform mapping."""

    model_config = ConfigDict(extra="forbid")

    entries: list[HashEntry] | None = None
    mapping: dict[str, str] | None = None

    @model_validator(mode="before")
    @classmethod
    def _parse_input(cls, data: Any) -> dict[str, Any]:
        """Parse raw input into entries or mapping."""
        if isinstance(data, dict):
            if "entries" in data or "mapping" in data:
                return data
            # Platform -> hash mapping
            for hash_value in data.values():
                validate_sri_hash(hash_value)
            return {"mapping": data}
        if isinstance(data, list):
            return {"entries": data}
        if isinstance(data, HashCollection):
            return {"entries": data.entries, "mapping": data.mapping}
        raise ValueError("Hashes must be a list or dict")

    def to_json(self) -> dict[str, Any] | list[dict[str, Any]]:
        """Serialize to JSON-compatible format."""
        if self.entries is not None:
            return [entry.to_dict() for entry in self.entries]
        if self.mapping is not None:
            return dict(self.mapping)
        return {}

    def primary_hash(self) -> str | None:
        """Return the first/primary hash for display purposes."""
        if self.entries and len(self.entries) == 1:
            return self.entries[0].hash
        if self.mapping:
            values = list(self.mapping.values())
            if len(set(values)) == 1:
                return values[0]
        return None

    @classmethod
    def from_value(cls, data: SourceHashes) -> HashCollection:
        """Create HashCollection from raw hashes data."""
        return cls.model_validate(data)


# =============================================================================
# Source Entry Models
# =============================================================================


class SourceEntry(BaseModel):
    """A source package entry in sources.json."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    hashes: HashCollection
    version: str | None = None
    input: str | None = None
    urls: dict[str, str] | None = None
    commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict with sorted keys."""
        return dict(
            sorted(
                {
                    k: v
                    for k, v in {
                        "hashes": self.hashes.to_json(),
                        "commit": self.commit,
                        "input": self.input,
                        "urls": self.urls,
                        "version": self.version,
                    }.items()
                    if v is not None
                }.items()
            )
        )


class SourcesFile(BaseModel):
    """Container for sources.json entries."""

    model_config = ConfigDict(extra="forbid")

    entries: dict[str, SourceEntry]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourcesFile:
        """Parse raw JSON data into SourcesFile."""
        entries = {}
        for name, entry in data.items():
            if name == "$schema":
                continue
            entries[name] = SourceEntry.model_validate(entry)
        return cls(entries=entries)

    @classmethod
    def load(cls, path: Path | None = None) -> SourcesFile:
        """Load from file path."""
        if path is None:
            path = get_config().paths.sources_file
        if not path.exists():
            return cls(entries={})
        return cls.from_dict(json.loads(path.read_text()))

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {name: entry.to_dict() for name, entry in self.entries.items()}

    def save(self, path: Path | None = None) -> None:
        """Save to file path."""
        if path is None:
            path = get_config().paths.sources_file
        data = self.to_dict()
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        """Generate JSON schema for sources.json file format."""
        entry_schema = SourceEntry.model_json_schema()
        defs = dict(entry_schema.get("$defs", {}))

        source_entry_def = {
            k: v for k, v in entry_schema.items() if k not in ("$defs", "$schema")
        }
        defs["SourceEntry"] = source_entry_def

        # Replace HashCollection with the actual serialization format
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
            "additionalProperties": {"$ref": "#/$defs/SourceEntry"},
            "$defs": defs,
        }


# =============================================================================
# Version Info
# =============================================================================


@dataclass
class VersionInfo:
    """Version and metadata fetched from upstream."""

    version: str
    metadata: dict[str, Any]  # Updater-specific data


def verify_platform_versions(versions: dict[str, str], source_name: str) -> str:
    """Verify all platform versions match and return the common version."""
    unique = set(versions.values())
    if len(unique) != 1:
        raise ValidationError(
            f"{source_name} version mismatch across platforms: {versions}",
            source=source_name,
        )
    return unique.pop()
