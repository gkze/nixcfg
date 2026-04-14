"""Typed metadata models for updater version resolution."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from typing import ClassVar

from pydantic import BaseModel, ValidationError

from lib import json_utils
from lib.nix.models.flake_lock import FlakeLockNode

type JsonObject = json_utils.JsonObject
type JsonValue = json_utils.JsonValue

_METADATA_KIND_KEY = "__kind__"
_METADATA_PAYLOAD_KEY = "payload"


def _dataclass_payload(obj: object) -> dict[str, object]:
    if not is_dataclass(obj) or isinstance(obj, type):
        msg = f"Expected dataclass instance, got {type(obj).__name__}"
        raise TypeError(msg)
    return {field.name: getattr(obj, field.name) for field in fields(obj) if field.init}


class MappingMetadata:
    """Small dict-like compatibility layer for typed metadata objects."""

    def to_dict(self) -> dict[str, object]:
        payload = _dataclass_payload(self)
        return {str(key): value for key, value in payload.items()}

    def __getitem__(self, key: str) -> object:
        return self.to_dict()[key]

    def get(self, key: str, default: object = None) -> object:
        return self.to_dict().get(key, default)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key in self.to_dict()


@dataclass(frozen=True, slots=True)
class NoMetadata(MappingMetadata):
    """Marker metadata for updaters that need no auxiliary fields."""

    KIND: ClassVar[str] = "none"


NO_METADATA = NoMetadata()


@dataclass(frozen=True, slots=True)
class GitHubReleaseMetadata(MappingMetadata):
    """Metadata for GitHub latest-release lookups."""

    tag: str

    KIND: ClassVar[str] = "github_release"


@dataclass(frozen=True, slots=True)
class DownloadUrlMetadata(MappingMetadata):
    """Metadata carrying one resolved download URL."""

    url: str

    KIND: ClassVar[str] = "download_url"


@dataclass(frozen=True, slots=True)
class GitHubRawFileMetadata(MappingMetadata):
    """Metadata for a GitHub raw-file revision lookup."""

    rev: str
    branch: str

    KIND: ClassVar[str] = "github_raw_file"


@dataclass(frozen=True, slots=True)
class AssetURLsMetadata(MappingMetadata):
    """Metadata carrying resolved per-platform asset URLs."""

    asset_urls: dict[str, str]

    KIND: ClassVar[str] = "asset_urls"


@dataclass(frozen=True, slots=True)
class FlakeInputMetadata(MappingMetadata):
    """Metadata for updaters backed by a flake.lock node."""

    node: FlakeLockNode
    commit: str | None = None

    KIND: ClassVar[str] = "flake_input"

    def to_dict(self) -> dict[str, object]:
        """Return flake metadata with the live validated node object."""
        payload: dict[str, object] = {"node": self.node}
        if self.commit is not None:
            payload["commit"] = self.commit
        return payload

    @classmethod
    def from_json(cls, payload: dict[str, object]) -> FlakeInputMetadata:
        """Hydrate flake metadata from serialized pinned-version payloads."""
        raw_node = payload.get("node")
        if not isinstance(raw_node, dict):
            msg = f"Pinned version entry has invalid node metadata: {raw_node!r}"
            raise TypeError(msg)
        try:
            node = FlakeLockNode.model_validate(raw_node)
        except ValidationError as exc:
            msg = f"Pinned version entry has invalid node metadata: {raw_node!r}"
            raise TypeError(msg) from exc
        raw_commit = payload.get("commit")
        if raw_commit is not None and not isinstance(raw_commit, str):
            msg = f"Pinned version entry has invalid commit metadata: {raw_commit!r}"
            raise TypeError(msg)
        return cls(node=node, commit=raw_commit)


@dataclass(frozen=True, slots=True)
class PlatformAPIMetadata(MappingMetadata):
    """Metadata for platform API responses and equality fields."""

    platform_info: dict[str, JsonObject]
    equality_fields: dict[str, str]
    commit: str | None = None

    KIND: ClassVar[str] = "platform_api"

    def to_dict(self) -> dict[str, object]:
        """Return platform API metadata in its legacy mapping form."""
        payload: dict[str, object] = {
            "platform_info": self.platform_info,
            **self.equality_fields,
        }
        if self.commit is not None:
            payload["commit"] = self.commit
        return payload


@dataclass(frozen=True, slots=True)
class ReleasePayloadMetadata(MappingMetadata):
    """Metadata carrying one validated upstream release payload."""

    release: JsonObject

    KIND: ClassVar[str] = "release_payload"


type VersionMetadata = (
    AssetURLsMetadata
    | DownloadUrlMetadata
    | FlakeInputMetadata
    | GitHubRawFileMetadata
    | GitHubReleaseMetadata
    | NoMetadata
    | PlatformAPIMetadata
    | ReleasePayloadMetadata
    | JsonObject
)


@dataclass(frozen=True, slots=True)
class VersionInfo:
    """Latest upstream version metadata fetched by an updater."""

    version: str
    metadata: VersionMetadata | object | None = None

    @property
    def commit(self) -> str | None:
        """Return commit-like equality metadata when present."""
        return metadata_get_str(self.metadata, "commit")


def metadata_as_mapping(metadata: object | None, *, context: str) -> dict[str, object]:
    """Return metadata as a ``dict[str, object]`` compatibility mapping."""
    if isinstance(metadata, MappingMetadata):
        return metadata.to_dict()
    try:
        return json_utils.as_object_dict(metadata, context=context)
    except TypeError as exc:
        msg = f"Expected mapping metadata for {context}"
        raise TypeError(msg) from exc


def metadata_get(
    metadata: object | None,
    key: str,
    *,
    context: str = "metadata",
) -> object | None:
    """Return one metadata field from a mapping or typed metadata object."""
    if metadata is None:
        return None
    if isinstance(metadata, MappingMetadata):
        return metadata.get(key)
    if isinstance(metadata, dict):
        return metadata_as_mapping(metadata, context=context).get(key)
    return getattr(metadata, key, None)


def metadata_get_str(
    metadata: object | None,
    key: str,
    *,
    context: str = "metadata",
) -> str | None:
    """Return one metadata field as ``str`` when present and well typed."""
    value = metadata_get(metadata, key, context=context)
    return value if isinstance(value, str) else None


def require_metadata_str(
    metadata: object | None,
    key: str,
    *,
    context: str,
    allow_empty: bool = False,
) -> str:
    """Return one required string metadata field or raise ``TypeError``."""
    value = metadata_get_str(metadata, key, context=context)
    if value is None or (not allow_empty and not value):
        msg = f"Expected string field {key!r} in {context}"
        raise TypeError(msg)
    return value


_METADATA_TYPES: dict[str, type[MappingMetadata]] = {
    AssetURLsMetadata.KIND: AssetURLsMetadata,
    DownloadUrlMetadata.KIND: DownloadUrlMetadata,
    FlakeInputMetadata.KIND: FlakeInputMetadata,
    GitHubRawFileMetadata.KIND: GitHubRawFileMetadata,
    GitHubReleaseMetadata.KIND: GitHubReleaseMetadata,
    NoMetadata.KIND: NoMetadata,
    PlatformAPIMetadata.KIND: PlatformAPIMetadata,
    ReleasePayloadMetadata.KIND: ReleasePayloadMetadata,
}


def _json_safe_value(value: object) -> JsonValue:
    if isinstance(value, BaseModel):
        return _json_safe_value(value.model_dump())
    if is_dataclass(value) and not isinstance(value, type):
        payload = _dataclass_payload(value)
        return {key: _json_safe_value(item) for key, item in payload.items()}
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    msg = f"Value is not JSON-serializable: {value!r}"
    raise TypeError(msg)


def serialize_metadata(metadata: object | None) -> JsonValue | None:
    """Return JSON-safe serialized metadata with type markers when needed."""
    if metadata is None:
        return None
    if isinstance(metadata, dict):
        return _json_safe_value(metadata)
    kind = getattr(type(metadata), "KIND", None)
    if (
        isinstance(kind, str)
        and is_dataclass(metadata)
        and not isinstance(metadata, type)
    ):
        payload = _dataclass_payload(metadata)
        return {
            _METADATA_KIND_KEY: kind,
            _METADATA_PAYLOAD_KEY: _json_safe_value(payload),
        }
    return _json_safe_value(metadata)


def _deserialize_dataclass_metadata(
    kind: str, payload: dict[str, object]
) -> MappingMetadata:
    metadata_type = _METADATA_TYPES.get(kind)
    if metadata_type is None:
        msg = f"Unknown pinned version metadata kind: {kind!r}"
        raise TypeError(msg)
    if metadata_type is FlakeInputMetadata:
        return FlakeInputMetadata.from_json(payload)
    if metadata_type is NoMetadata:
        return NO_METADATA
    return metadata_type(**payload)


def deserialize_metadata(payload: object) -> object | None:
    """Hydrate metadata serialized by :func:`serialize_metadata`."""
    if payload is None:
        return None
    if not isinstance(payload, dict):
        return payload
    payload_map = {str(key): value for key, value in payload.items()}

    kind = payload_map.get(_METADATA_KIND_KEY)
    if isinstance(kind, str):
        data = payload_map.get(_METADATA_PAYLOAD_KEY, {})
        if not isinstance(data, dict):
            msg = f"Pinned version metadata payload must be an object: {payload!r}"
            raise TypeError(msg)
        normalized = {str(key): value for key, value in data.items()}
        return _deserialize_dataclass_metadata(kind, normalized)

    legacy_node = payload_map.get("node")
    if isinstance(legacy_node, dict):
        return FlakeInputMetadata.from_json({
            str(key): value for key, value in payload_map.items()
        })
    return {str(key): value for key, value in payload_map.items()}


__all__ = [
    "NO_METADATA",
    "AssetURLsMetadata",
    "DownloadUrlMetadata",
    "FlakeInputMetadata",
    "GitHubRawFileMetadata",
    "GitHubReleaseMetadata",
    "NoMetadata",
    "PlatformAPIMetadata",
    "ReleasePayloadMetadata",
    "VersionInfo",
    "VersionMetadata",
    "deserialize_metadata",
    "metadata_as_mapping",
    "metadata_get",
    "metadata_get_str",
    "require_metadata_str",
    "serialize_metadata",
]
