"""Typed metadata models for updater version resolution."""

from dataclasses import dataclass, fields, is_dataclass
from typing import override

from pydantic import TypeAdapter, ValidationError

from lib import json_utils
from lib.nix.models.flake_lock import FlakeLockNode

type JsonObject = json_utils.JsonObject


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


NO_METADATA = NoMetadata()


@dataclass(frozen=True, slots=True)
class GitHubReleaseMetadata(MappingMetadata):
    """Metadata for GitHub latest-release lookups."""

    tag: str


@dataclass(frozen=True, slots=True)
class DownloadUrlMetadata(MappingMetadata):
    """Metadata carrying one resolved download URL."""

    url: str


@dataclass(frozen=True, slots=True)
class GitHubRawFileMetadata(MappingMetadata):
    """Metadata for a GitHub raw-file revision lookup."""

    rev: str
    branch: str


@dataclass(frozen=True, slots=True)
class GranolaFeedMetadata(MappingMetadata):
    """Metadata for Granola's Electron updater feed."""

    path: str
    sha512: str


@dataclass(frozen=True, slots=True)
class AssetURLsMetadata(MappingMetadata):
    """Metadata carrying resolved per-platform asset URLs."""

    asset_urls: dict[str, str]


@dataclass(frozen=True, slots=True)
class FlakeInputMetadata(MappingMetadata):
    """Metadata for updaters backed by a flake.lock node."""

    node: FlakeLockNode
    commit: str | None = None

    @override
    def to_dict(self) -> dict[str, object]:
        """Return flake metadata with the live validated node object."""
        payload: dict[str, object] = {"node": self.node}
        if self.commit is not None:
            payload["commit"] = self.commit
        return payload

    @classmethod
    def from_json(cls, payload: dict[str, object]) -> FlakeInputMetadata:
        """Hydrate flake metadata from a JSON-compatible mapping."""
        raw_node = payload.get("node")
        if not isinstance(raw_node, dict):
            msg = f"Flake metadata has invalid node metadata: {raw_node!r}"
            raise TypeError(msg)
        try:
            node = FlakeLockNode.model_validate(raw_node)
        except ValidationError as exc:
            msg = f"Flake metadata has invalid node metadata: {raw_node!r}"
            raise TypeError(msg) from exc
        raw_commit = payload.get("commit")
        if raw_commit is not None and not isinstance(raw_commit, str):
            msg = f"Flake metadata has invalid commit metadata: {raw_commit!r}"
            raise TypeError(msg)
        return cls(node=node, commit=raw_commit)

    @classmethod
    def from_metadata(
        cls, metadata: object | None, *, context: str
    ) -> FlakeInputMetadata | None:
        """Coerce runtime metadata into typed flake-input metadata."""
        if metadata is None:
            return None
        if isinstance(metadata, cls):
            return metadata
        if not isinstance(metadata, MappingMetadata | dict):
            return None
        payload = metadata_as_mapping(metadata, context=context)
        raw_node = payload.get("node")
        if raw_node is None:
            return None
        if isinstance(raw_node, FlakeLockNode):
            raw_commit = payload.get("commit")
            if raw_commit is not None and not isinstance(raw_commit, str):
                msg = f"Flake metadata has invalid commit metadata: {raw_commit!r}"
                raise TypeError(msg)
            return cls(node=raw_node, commit=raw_commit)
        if isinstance(raw_node, dict):
            return cls.from_json(payload)
        msg = f"Expected flake lock node in metadata, got {type(raw_node)}"
        raise TypeError(msg)


@dataclass(frozen=True, slots=True)
class PlatformAPIMetadata(MappingMetadata):
    """Metadata for platform API responses and equality fields."""

    platform_info: dict[str, JsonObject]
    equality_fields: dict[str, str]
    commit: str | None = None

    @override
    def to_dict(self) -> dict[str, object]:
        """Return platform API metadata in its legacy mapping form."""
        payload: dict[str, object] = {
            "platform_info": self.platform_info,
            **self.equality_fields,
        }
        if self.commit is not None:
            payload["commit"] = self.commit
        return payload

    @classmethod
    def from_metadata(
        cls, metadata: object | None, *, context: str
    ) -> PlatformAPIMetadata:
        """Coerce runtime metadata into validated platform API metadata."""
        if isinstance(metadata, cls):
            return metadata

        if not isinstance(metadata, MappingMetadata | dict):
            msg = f"Expected platform_info mapping in {context}"
            raise TypeError(msg)

        metadata_map = metadata_as_mapping(metadata, context=context)
        platform_info_obj = metadata_map.get("platform_info")
        if not isinstance(platform_info_obj, dict):
            msg = f"Expected platform_info mapping in {context}"
            raise TypeError(msg)
        try:
            platform_info = TypeAdapter(dict[str, JsonObject]).validate_python(
                platform_info_obj,
                strict=True,
            )
        except ValidationError as exc:
            msg = f"Malformed platform payload for {context.removesuffix(' metadata')}"
            raise TypeError(msg) from exc
        equality_fields = {
            key: value
            for key, value in metadata_map.items()
            if key not in {"commit", "platform_info"} and isinstance(value, str)
        }
        return cls(
            platform_info=platform_info,
            equality_fields=equality_fields,
            commit=metadata_get_str(metadata_map, "commit"),
        )


@dataclass(frozen=True, slots=True)
class ReleasePayloadMetadata(MappingMetadata):
    """Metadata carrying one validated upstream release payload."""

    release: JsonObject


type VersionMetadata = (
    AssetURLsMetadata
    | DownloadUrlMetadata
    | FlakeInputMetadata
    | GranolaFeedMetadata
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


__all__ = [
    "NO_METADATA",
    "AssetURLsMetadata",
    "DownloadUrlMetadata",
    "FlakeInputMetadata",
    "GitHubRawFileMetadata",
    "GitHubReleaseMetadata",
    "GranolaFeedMetadata",
    "NoMetadata",
    "PlatformAPIMetadata",
    "ReleasePayloadMetadata",
    "VersionInfo",
    "VersionMetadata",
    "metadata_as_mapping",
    "metadata_get",
    "metadata_get_str",
    "require_metadata_str",
]
