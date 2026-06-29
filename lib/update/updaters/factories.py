"""Factory helpers for common updater subclasses."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Literal

from lib import json_utils
from lib.update.net import fetch_json, fetch_url
from lib.update.updaters.base import (
    DenoDepsHashUpdater,
    DenoManifestUpdater,
    DownloadHashUpdater,
    DownloadUrlMetadataUpdater,
    FlakeInputHashUpdater,
    UvLockUpdater,
    VersionInfo,
    read_pinned_source_version,
)
from lib.update.updaters.github_release import GitHubReleaseAssetURLsUpdater
from lib.update.updaters.metadata import AssetURLsMetadata, DownloadUrlMetadata
from lib.update.updaters.registry import register_updater
from lib.update.updaters.vendor_feeds import (
    SparkleAppcastItem,
    fetch_electron_builder_asset_urls,
    fetch_head_artifact_version,
    fetch_sparkle_appcast_items,
    require_url,
    require_version,
)

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import HashType
type AssetNameTemplate = str | Callable[[str, str], str]
type DownloadURLTemplate = str | Callable[[str, str, str], str]
type DownloadURLMethod = Callable[[DownloadHashUpdater, str, VersionInfo], str]
type ElectronAssetSelector = Callable[[str, str], bool]
type JsonVersionTransform = Callable[[str], str]
type SparkleVersionField = Literal["version", "short_version", "short_or_version"]
type SparkleVersionTransform = Callable[[SparkleAppcastItem], str]


def _resolve_module_name(module: str | None) -> str:
    return __name__ if module is None else module


def _render_asset_name(
    template: AssetNameTemplate,
    *,
    version: str,
    platform_value: str,
) -> str:
    if isinstance(template, str):
        return template.format(version=version, platform_value=platform_value)
    return template(version, platform_value)


def _render_download_url(
    template: DownloadURLTemplate,
    *,
    version: str,
    platform: str,
    platform_value: str,
) -> str:
    if isinstance(template, str):
        return template.format(
            version=version,
            platform=platform,
            platform_value=platform_value,
        )
    return template(version, platform, platform_value)


def _templated_download_url(template: DownloadURLTemplate) -> DownloadURLMethod:
    def get_download_url(
        self: DownloadHashUpdater,
        platform: str,
        info: VersionInfo,
    ) -> str:
        return _render_download_url(
            template,
            version=info.version,
            platform=platform,
            platform_value=self.PLATFORMS[platform],
        )

    return get_download_url


def _sparkle_item_version(
    item: SparkleAppcastItem,
    *,
    version_field: SparkleVersionField,
) -> str | None:
    match version_field:
        case "version":
            return item.version
        case "short_version":
            return item.short_version
        case "short_or_version":
            return item.short_version or item.version


def _json_path_str(payload: object, path: tuple[str, ...], *, context: str) -> str:
    data = json_utils.as_object_dict(payload, context=context)
    for segment in path[:-1]:
        data = json_utils.as_object_dict(
            data.get(segment),
            context=f"{context} {segment}",
        )
    return json_utils.get_required_str(data, path[-1], context=context)


def flake_input_hash_updater(
    name: str,
    hash_type: HashType,
    *,
    input_name: str | None = None,
    module: str | None = None,
    platform_specific: bool = False,
    supported_platforms: tuple[str, ...] | None = None,
) -> type[FlakeInputHashUpdater]:
    """Create and register a flake-input-backed hash updater.

    If ``supported_platforms`` is provided, the updater short-circuits on
    unsupported platforms and preserves existing hashes, so per-platform CI
    runners can skip packages whose system constraint excludes them without
    tripping over missing flake attributes.
    """
    attrs: dict[str, object] = {
        "__module__": _resolve_module_name(module),
        "name": name,
        "input_name": input_name,
        "hash_type": hash_type,
        "platform_specific": platform_specific,
        "supported_platforms": supported_platforms,
    }
    return register_updater(type(f"{name}Updater", (FlakeInputHashUpdater,), attrs))


def github_release_asset_urls_updater(
    name: str,
    *,
    github_owner: str,
    github_repo: str,
    platforms: Mapping[str, str],
    asset_name: AssetNameTemplate,
    tag_prefix: str = "v",
    module: str | None = None,
) -> type[GitHubReleaseAssetURLsUpdater]:
    """Create and register a GitHub release asset URL updater."""

    def _asset_name(
        self: GitHubReleaseAssetURLsUpdater,
        version: str,
        platform_value: str,
    ) -> str:
        _ = self
        return _render_asset_name(
            asset_name,
            version=version,
            platform_value=platform_value,
        )

    attrs: dict[str, object] = {
        "__module__": _resolve_module_name(module),
        "name": name,
        "GITHUB_OWNER": github_owner,
        "GITHUB_REPO": github_repo,
        "TAG_PREFIX": tag_prefix,
        "PLATFORMS": dict(platforms),
        "_asset_name": _asset_name,
    }
    return register_updater(
        type(f"{name}Updater", (GitHubReleaseAssetURLsUpdater,), attrs)
    )


def version_endpoint_download_updater(
    name: str,
    *,
    version_url: str,
    platforms: Mapping[str, str],
    download_url: DownloadURLTemplate,
    display_name: str | None = None,
    module: str | None = None,
) -> type[DownloadHashUpdater]:
    """Create a download updater driven by a plain-text version endpoint."""

    async def fetch_latest(
        self: DownloadHashUpdater,
        session: aiohttp.ClientSession,
    ) -> VersionInfo:
        payload = await fetch_url(
            session,
            version_url,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        version = payload.decode().strip()
        if not version:
            msg = f"Missing {display_name or name} version in {version_url}"
            raise RuntimeError(msg)
        return VersionInfo(version=version)

    attrs: dict[str, object] = {
        "__module__": _resolve_module_name(module),
        "name": name,
        "VERSION_URL": version_url,
        "PLATFORMS": dict(platforms),
        "fetch_latest": fetch_latest,
        "get_download_url": _templated_download_url(download_url),
    }
    return register_updater(type(f"{name}Updater", (DownloadHashUpdater,), attrs))


def electron_builder_asset_urls_updater(
    name: str,
    *,
    feed_url: str,
    platforms: Mapping[str, str],
    selectors: Mapping[str, ElectronAssetSelector],
    fallback_url: DownloadURLTemplate,
    module: str | None = None,
) -> type[DownloadHashUpdater]:
    """Create an Electron-builder feed updater with per-platform asset URLs."""

    async def fetch_latest(
        self: DownloadHashUpdater,
        session: aiohttp.ClientSession,
    ) -> VersionInfo:
        version, asset_urls = await fetch_electron_builder_asset_urls(
            session,
            feed_url,
            selectors,
            config=self.config,
        )
        return VersionInfo(version=version, metadata=AssetURLsMetadata(asset_urls))

    def get_download_url(
        self: DownloadHashUpdater,
        platform: str,
        info: VersionInfo,
    ) -> str:
        if isinstance(info.metadata, AssetURLsMetadata):
            url = info.metadata.asset_urls.get(platform)
            if isinstance(url, str) and url:
                return url
        return _render_download_url(
            fallback_url,
            version=info.version,
            platform=platform,
            platform_value=self.PLATFORMS[platform],
        )

    attrs: dict[str, object] = {
        "__module__": _resolve_module_name(module),
        "name": name,
        "FEED_URL": feed_url,
        "PLATFORMS": dict(platforms),
        "fetch_latest": fetch_latest,
        "get_download_url": get_download_url,
    }
    return register_updater(type(f"{name}Updater", (DownloadHashUpdater,), attrs))


def head_artifact_download_updater(
    name: str,
    *,
    download_url: str,
    platforms: Mapping[str, str],
    materialize_when_current: bool = False,
    module: str | None = None,
) -> type[DownloadHashUpdater]:
    """Create a download updater versioned by mutable URL response headers."""

    async def fetch_latest(
        self: DownloadHashUpdater,
        session: aiohttp.ClientSession,
    ) -> VersionInfo:
        version = await fetch_head_artifact_version(
            session,
            download_url,
            config=self.config,
        )
        return VersionInfo(version=version)

    attrs: dict[str, object] = {
        "__module__": _resolve_module_name(module),
        "name": name,
        "DOWNLOAD_URL": download_url,
        "PLATFORMS": dict(platforms),
        "materialize_when_current": materialize_when_current,
        "fetch_latest": fetch_latest,
    }
    return register_updater(type(f"{name}Updater", (DownloadHashUpdater,), attrs))


def json_field_download_updater(
    name: str,
    *,
    json_url: str,
    version_path: tuple[str, ...],
    platforms: Mapping[str, str],
    download_url: DownloadURLTemplate | None = None,
    display_name: str | None = None,
    version_transform: JsonVersionTransform | None = None,
    module: str | None = None,
) -> type[DownloadHashUpdater]:
    """Create a download updater driven by a version field in a JSON endpoint."""

    async def fetch_latest(
        self: DownloadHashUpdater,
        session: aiohttp.ClientSession,
    ) -> VersionInfo:
        payload = await fetch_json(
            session,
            json_url,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        raw_version = _json_path_str(payload, version_path, context=json_url).strip()
        version = (
            version_transform(raw_version)
            if version_transform is not None
            else raw_version
        )
        if not version:
            msg = f"Missing {display_name or name} version in {json_url}"
            raise RuntimeError(msg)
        return VersionInfo(version=version)

    attrs: dict[str, object] = {
        "__module__": _resolve_module_name(module),
        "name": name,
        "JSON_URL": json_url,
        "PLATFORMS": dict(platforms),
        "fetch_latest": fetch_latest,
    }
    if download_url is not None:
        attrs["get_download_url"] = _templated_download_url(download_url)
    return register_updater(type(f"{name}Updater", (DownloadHashUpdater,), attrs))


def pinned_source_download_updater(
    name: str,
    *,
    platforms: Mapping[str, str],
    download_url: DownloadURLTemplate,
    materialize_when_current: bool = True,
    module: str | None = None,
) -> type[DownloadHashUpdater]:
    """Create a download updater that rehashes a pinned source version."""

    async def fetch_latest(
        self: DownloadHashUpdater,
        session: aiohttp.ClientSession,
    ) -> VersionInfo:
        _ = session
        return VersionInfo(version=read_pinned_source_version(self.name))

    attrs: dict[str, object] = {
        "__module__": _resolve_module_name(module),
        "name": name,
        "PLATFORMS": dict(platforms),
        "materialize_when_current": materialize_when_current,
        "fetch_latest": fetch_latest,
        "get_download_url": _templated_download_url(download_url),
    }
    return register_updater(type(f"{name}Updater", (DownloadHashUpdater,), attrs))


def sparkle_appcast_updater(
    name: str,
    *,
    appcast_url: str,
    platforms: Mapping[str, str],
    download_url: DownloadURLTemplate | None = None,
    version_field: SparkleVersionField = "version",
    version_transform: SparkleVersionTransform | None = None,
    appcast_url_metadata: bool = False,
    url_metadata_context: str | None = None,
    module: str | None = None,
) -> type[DownloadHashUpdater]:
    """Create a download updater driven by a Sparkle appcast feed."""

    async def fetch_latest(
        self: DownloadHashUpdater,
        session: aiohttp.ClientSession,
    ) -> VersionInfo:
        items = await fetch_sparkle_appcast_items(
            session,
            appcast_url,
            config=self.config,
        )
        item = items[0]
        version = require_version(
            version_transform(item)
            if version_transform is not None
            else _sparkle_item_version(item, version_field=version_field),
            context=appcast_url,
        )
        metadata = (
            DownloadUrlMetadata(url=require_url(item.url, context=appcast_url))
            if appcast_url_metadata
            else None
        )
        return VersionInfo(version=version, metadata=metadata)

    attrs: dict[str, object] = {
        "__module__": _resolve_module_name(module),
        "name": name,
        "APPCAST_URL": appcast_url,
        "PLATFORMS": dict(platforms),
        "fetch_latest": fetch_latest,
    }
    if url_metadata_context is not None:
        attrs["URL_METADATA_CONTEXT"] = url_metadata_context
    if download_url is not None:
        attrs["get_download_url"] = _templated_download_url(download_url)

    if appcast_url_metadata:
        return register_updater(
            type(f"{name}Updater", (DownloadUrlMetadataUpdater,), attrs)
        )
    return register_updater(type(f"{name}Updater", (DownloadHashUpdater,), attrs))


def go_vendor_updater(
    name: str,
    *,
    input_name: str | None = None,
    module: str | None = None,
    **_kw: object,
) -> type[FlakeInputHashUpdater]:
    r"""Shorthand: ``flake_input_hash_updater(name, "vendorHash", ...)``."""
    return flake_input_hash_updater(
        name,
        "vendorHash",
        input_name=input_name,
        module=module,
    )


def cargo_vendor_updater(
    name: str,
    *,
    input_name: str | None = None,
    module: str | None = None,
    **_kw: object,
) -> type[FlakeInputHashUpdater]:
    r"""Shorthand: ``flake_input_hash_updater(name, "cargoHash", ...)``."""
    return flake_input_hash_updater(
        name,
        "cargoHash",
        input_name=input_name,
        module=module,
    )


def npm_deps_updater(
    name: str,
    *,
    input_name: str | None = None,
    module: str | None = None,
) -> type[FlakeInputHashUpdater]:
    r"""Shorthand: ``flake_input_hash_updater(name, "npmDepsHash", ...)``."""
    return flake_input_hash_updater(
        name,
        "npmDepsHash",
        input_name=input_name,
        module=module,
    )


def bun_node_modules_updater(
    name: str,
    *,
    input_name: str | None = None,
    module: str | None = None,
    supported_platforms: tuple[str, ...] | None = None,
) -> type[FlakeInputHashUpdater]:
    """Shorthand for platform-specific Bun ``nodeModulesHash`` updaters."""
    return flake_input_hash_updater(
        name,
        "nodeModulesHash",
        input_name=input_name,
        module=module,
        platform_specific=True,
        supported_platforms=supported_platforms,
    )


def uv_lock_hash_updater(
    name: str,
    *,
    input_name: str | None = None,
    module: str | None = None,
) -> type[FlakeInputHashUpdater]:
    r"""Shorthand: ``flake_input_hash_updater(name, "uvLockHash", ...)``."""
    return flake_input_hash_updater(
        name,
        "uvLockHash",
        input_name=input_name,
        module=module,
    )


def uv_lock_updater(
    name: str,
    *,
    input_name: str | None = None,
    lock_file: str = "uv.lock",
    lock_env: dict[str, str] | None = None,
    module: str | None = None,
) -> type[UvLockUpdater]:
    """Create and register a :class:`UvLockUpdater` subclass."""
    attrs: dict[str, object] = {
        "__module__": _resolve_module_name(module),
        "name": name,
        "input_name": input_name,
        "lock_file": lock_file,
        "lock_env": dict(lock_env or {}),
    }
    return register_updater(type(f"{name}Updater", (UvLockUpdater,), attrs))


def deno_deps_updater(
    name: str,
    *,
    input_name: str | None = None,
    module: str | None = None,
) -> type[DenoDepsHashUpdater]:
    """Create and register a :class:`DenoDepsHashUpdater` subclass."""
    attrs: dict[str, object] = {
        "__module__": _resolve_module_name(module),
        "name": name,
        "input_name": input_name,
    }
    return register_updater(type(f"{name}Updater", (DenoDepsHashUpdater,), attrs))


def deno_manifest_updater(
    name: str,
    *,
    input_name: str | None = None,
    lock_file: str = "deno.lock",
    manifest_file: str = "deno-deps.json",
    module: str | None = None,
) -> type[DenoManifestUpdater]:
    """Create and register a :class:`DenoManifestUpdater` subclass."""
    attrs: dict[str, object] = {
        "__module__": _resolve_module_name(module),
        "name": name,
        "input_name": input_name,
        "lock_file": lock_file,
        "manifest_file": manifest_file,
    }
    return register_updater(type(f"{name}Updater", (DenoManifestUpdater,), attrs))


__all__ = [
    "bun_node_modules_updater",
    "cargo_vendor_updater",
    "deno_deps_updater",
    "deno_manifest_updater",
    "electron_builder_asset_urls_updater",
    "flake_input_hash_updater",
    "github_release_asset_urls_updater",
    "go_vendor_updater",
    "head_artifact_download_updater",
    "json_field_download_updater",
    "npm_deps_updater",
    "pinned_source_download_updater",
    "sparkle_appcast_updater",
    "uv_lock_hash_updater",
    "uv_lock_updater",
    "version_endpoint_download_updater",
]
