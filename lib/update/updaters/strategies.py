"""Declarative strategy bases for common updater shapes.

Each class here captures one recurring update pattern as class-attribute
configuration. Per-package ``updater.py`` files subclass exactly one strategy
(or a deeper base) and register with ``register_updater``; value-only
updaters are a bare class body of attributes, and updaters that need extra
logic override the relevant hook method on the same class.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal

from lib import json_utils
from lib.update.net import fetch_json, fetch_url
from lib.update.sources import read_pinned_source_version
from lib.update.updaters.core import DownloadHashUpdater, DownloadUrlMetadataUpdater
from lib.update.updaters.metadata import (
    AssetURLsMetadata,
    DownloadUrlMetadata,
    VersionInfo,
)
from lib.update.updaters.vendor_feeds import (
    fetch_electron_builder_asset_urls,
    fetch_head_artifact_version,
    fetch_sparkle_appcast_items,
    require_url,
    require_version,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    import aiohttp

    from lib.update.updaters.vendor_feeds import SparkleAppcastItem

type ElectronAssetSelector = Callable[[str, str], bool]
type SparkleVersionField = Literal["version", "short_version", "short_or_version"]


def _json_path_str(payload: object, path: tuple[str, ...], *, context: str) -> str:
    data = json_utils.as_object_dict(payload, context=context)
    for segment in path[:-1]:
        data = json_utils.as_object_dict(
            data.get(segment),
            context=f"{context} {segment}",
        )
    return json_utils.get_required_str(data, path[-1], context=context)


class VersionEndpointDownloadUpdater(DownloadHashUpdater):
    """Download updater driven by a plain-text version endpoint."""

    VERSION_URL: ClassVar[str]

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch and validate the plain-text version payload."""
        payload = await fetch_url(
            session,
            self.VERSION_URL,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        version = payload.decode().strip()
        if not version:
            msg = f"Missing {self.name} version in {self.VERSION_URL}"
            raise RuntimeError(msg)
        return VersionInfo(version=version)


class JsonFieldDownloadUpdater(DownloadHashUpdater):
    """Download updater driven by a version field in a JSON endpoint."""

    JSON_URL: ClassVar[str]
    VERSION_PATH: ClassVar[tuple[str, ...]] = ("version",)

    def transform_version(self, raw: str) -> str:
        """Normalize the raw JSON version field; identity by default."""
        return raw

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch the JSON payload and extract the configured version field."""
        payload = await fetch_json(
            session,
            self.JSON_URL,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        raw = _json_path_str(payload, self.VERSION_PATH, context=self.JSON_URL)
        version = self.transform_version(raw.strip())
        if not version:
            msg = f"Missing {self.name} version in {self.JSON_URL}"
            raise RuntimeError(msg)
        return VersionInfo(version=version)


class HeadArtifactDownloadUpdater(DownloadHashUpdater):
    """Download updater versioned by mutable URL response headers."""

    HEAD_URL: ClassVar[str]

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Build a version token from the artifact's response headers."""
        version = await fetch_head_artifact_version(
            session,
            self.HEAD_URL,
            config=self.config,
        )
        return VersionInfo(version=version)


class PinnedSourceDownloadUpdater(DownloadHashUpdater):
    """Download updater that rehashes a pinned ``sources.json`` version."""

    materialize_when_current: ClassVar[bool] = True

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Return the version already pinned in the package's sources.json."""
        _ = session
        return VersionInfo(version=read_pinned_source_version(self.name))


class SparkleAppcastUpdater(DownloadHashUpdater):
    """Download updater driven by a Sparkle appcast feed."""

    APPCAST_URL: ClassVar[str]
    VERSION_FIELD: ClassVar[SparkleVersionField] = "version"

    def item_version(self, item: SparkleAppcastItem) -> str | None:
        """Extract the version from the newest appcast item."""
        if self.VERSION_FIELD == "version":
            return item.version
        if self.VERSION_FIELD == "short_version":
            return item.short_version
        return item.short_version or item.version

    def item_metadata(self, item: SparkleAppcastItem) -> DownloadUrlMetadata | None:
        """Build version metadata from the newest appcast item."""
        _ = item
        return None

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch the appcast and resolve version details from its newest item."""
        items = await fetch_sparkle_appcast_items(
            session,
            self.APPCAST_URL,
            config=self.config,
        )
        item = items[0]
        version = require_version(self.item_version(item), context=self.APPCAST_URL)
        return VersionInfo(version=version, metadata=self.item_metadata(item))


class SparkleAppcastUrlUpdater(SparkleAppcastUpdater, DownloadUrlMetadataUpdater):
    """Sparkle updater whose download URL comes from the appcast enclosure."""

    def item_metadata(self, item: SparkleAppcastItem) -> DownloadUrlMetadata:
        """Capture the enclosure URL so downloads use the appcast's artifact."""
        return DownloadUrlMetadata(url=require_url(item.url, context=self.APPCAST_URL))


class ElectronBuilderAssetURLsUpdater(DownloadHashUpdater):
    """Electron-builder feed updater with per-platform asset URLs."""

    FEED_URL: ClassVar[str]
    SELECTORS: ClassVar[Mapping[str, ElectronAssetSelector]]

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch the feed and select one artifact URL per platform."""
        version, asset_urls = await fetch_electron_builder_asset_urls(
            session,
            self.FEED_URL,
            self.SELECTORS,
            config=self.config,
        )
        return VersionInfo(version=version, metadata=AssetURLsMetadata(asset_urls))

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Prefer the feed-resolved asset URL, falling back to the template."""
        if isinstance(info.metadata, AssetURLsMetadata):
            url = info.metadata.asset_urls.get(platform)
            if isinstance(url, str) and url:
                return url
        return super().get_download_url(platform, info)


__all__ = [
    "ElectronAssetSelector",
    "ElectronBuilderAssetURLsUpdater",
    "HeadArtifactDownloadUpdater",
    "JsonFieldDownloadUpdater",
    "PinnedSourceDownloadUpdater",
    "SparkleAppcastUpdater",
    "SparkleAppcastUrlUpdater",
    "SparkleVersionField",
    "VersionEndpointDownloadUpdater",
]
