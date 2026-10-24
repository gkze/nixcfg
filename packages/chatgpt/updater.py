"""Updater for ChatGPT desktop releases.

macOS ships through Sparkle appcasts; Linux ships from a Debian repository on
the same origin (``/linux/deb``). The macOS appcasts contain no Linux enclosure,
so the deb index is the only source of truth for the Linux artifacts.
"""

from dataclasses import dataclass
from posixpath import basename
from typing import TYPE_CHECKING, ClassVar, cast
from urllib.parse import urlparse

from lib.update.derivation_validation import DerivationValidation
from lib.update.net import fetch_url
from lib.update.updaters import (
    DownloadHashUpdater,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import AssetURLsMetadata, metadata_get
from lib.update.updaters.vendor_feeds import (
    SparkleAppcastItem,
    debian_version_key,
    fetch_sparkle_appcast_items,
    parse_debian_packages,
    require_url,
    require_version,
)

if TYPE_CHECKING:
    import aiohttp

    from lib.update.updaters.core import UpdateContext


@dataclass(frozen=True, slots=True)
class _ChatGPTAppcastItem:
    short_version: str
    build_version: str
    url: str


@register_updater
class ChatGPTUpdater(DownloadHashUpdater):
    """Track immutable ChatGPT desktop archives from Sparkle appcasts and deb."""

    name = "chatgpt"
    ARCHIVE_BASE_URL = "https://persistent.oaistatic.com/codex-app-prod"
    APPCASTS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": f"{ARCHIVE_BASE_URL}/appcast.xml",
        "x86_64-darwin": f"{ARCHIVE_BASE_URL}/appcast-x64.xml",
    }
    DARWIN_ARCHS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
        "x86_64-darwin": "x64",
    }
    DEB_BASE_URL = f"{ARCHIVE_BASE_URL}/linux/deb"
    DEB_PACKAGE = "chatgpt"
    DEB_INDEXES: ClassVar[dict[str, str]] = {
        "aarch64-linux": f"{DEB_BASE_URL}/dists/stable/main/binary-arm64/Packages",
        "x86_64-linux": f"{DEB_BASE_URL}/dists/stable/main/binary-amd64/Packages",
    }
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
        "x86_64-darwin": "x64",
        "aarch64-linux": "arm64",
        "x86_64-linux": "amd64",
    }
    LINUX_PLATFORMS: ClassVar[tuple[str, ...]] = ("aarch64-linux", "x86_64-linux")
    supported_platforms: ClassVar[tuple[str, ...]] = tuple(PLATFORMS)
    # The Linux package is repacked from OpenAI's Debian artifact, so building it
    # is not enough: only starting the app proves the bundle is usable. The
    # Darwin branch of the same check is a no-op, hence the Linux-only systems.
    derivation_validations: ClassVar[tuple[DerivationValidation, ...]] = (
        DerivationValidation(
            installable="path:.#checks.{system}.chatgpt-linux-launch",
            systems=LINUX_PLATFORMS,
            mode="build",
        ),
    )

    @staticmethod
    def _require_version_pinned_url(*, url: str, short_version: str) -> None:
        """Reject latest-only archives whose basename omits the short version.

        OpenAI's ZIP URL is ``ChatGPT-darwin-{arch}-{shortVersion}.zip``. The
        build number is not in the path, so a custom fetchurl name that differs
        from that basename misses the prefetch Cachix path and re-downloads
        bytes that can change in-place (Update 37586805620). Fail closed if the
        enclosure is not even short-version-pinned.
        """
        name = basename(urlparse(url).path)
        if short_version and short_version in name:
            return
        msg = (
            "ChatGPT desktop archive URL is not version-pinned: "
            f"{url} (short version {short_version})"
        )
        raise RuntimeError(msg)

    async def _fetch_deb_artifact_url(
        self,
        session: aiohttp.ClientSession,
        *,
        platform: str,
    ) -> str:
        """Resolve the newest Linux deb pool URL for ``platform``."""
        index_url = self.DEB_INDEXES[platform]
        payload = await fetch_url(
            session,
            index_url,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        packages = parse_debian_packages(
            payload,
            package=self.DEB_PACKAGE,
            architecture=self.PLATFORMS[platform],
            label=f"ChatGPT deb index {index_url}",
        )
        if not packages:
            msg = (
                f"No {self.DEB_PACKAGE} package found in ChatGPT deb index {index_url}"
            )
            raise RuntimeError(msg)
        newest = max(packages, key=lambda package: debian_version_key(package.version))
        return f"{self.DEB_BASE_URL}/{newest.filename}"

    def _validated_appcast_item(
        self, item: SparkleAppcastItem, *, appcast_url: str
    ) -> _ChatGPTAppcastItem:
        context = f"ChatGPT appcast {appcast_url}"
        short_version = require_version(item.short_version, context=context)
        url = require_url(item.url, context=context)
        self._require_version_pinned_url(url=url, short_version=short_version)
        return _ChatGPTAppcastItem(
            short_version=short_version,
            build_version=require_version(item.version, context=context),
            url=url,
        )

    @staticmethod
    def _release_key(item: _ChatGPTAppcastItem) -> tuple[str, str]:
        return item.short_version, item.build_version

    @classmethod
    def _format_version(cls, item: _ChatGPTAppcastItem) -> str:
        short_version, build_version = cls._release_key(item)
        return f"{short_version}-{build_version}"

    def _select_common_items(
        self,
        items_by_platform: dict[str, tuple[_ChatGPTAppcastItem, ...]],
    ) -> dict[str, _ChatGPTAppcastItem]:
        keyed_items: dict[str, dict[tuple[str, str], _ChatGPTAppcastItem]] = {}
        for platform, items in items_by_platform.items():
            platform_items: dict[tuple[str, str], _ChatGPTAppcastItem] = {}
            for item in items:
                platform_items.setdefault(self._release_key(item), item)
            keyed_items[platform] = platform_items

        primary_platform = next(iter(items_by_platform))
        for item in items_by_platform[primary_platform]:
            release_key = self._release_key(item)
            if all(release_key in items for items in keyed_items.values()):
                return {
                    platform: items[release_key]
                    for platform, items in keyed_items.items()
                }

        versions = {
            platform: [self._format_version(item) for item in items]
            for platform, items in items_by_platform.items()
        }
        msg = f"No common ChatGPT desktop release across platform appcasts: {versions}"
        raise RuntimeError(msg)

    async def _fetch_appcast_items(
        self,
        session: aiohttp.ClientSession,
        platform: str,
    ) -> tuple[_ChatGPTAppcastItem, ...]:
        appcast_url = self.APPCASTS[platform]
        return tuple(
            self._validated_appcast_item(item, appcast_url=appcast_url)
            for item in await fetch_sparkle_appcast_items(
                session, appcast_url, config=self.config
            )
        )

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Fetch the appcasts and deb indexes and return per-platform URLs."""
        _ = context
        items_by_platform = {
            platform: await self._fetch_appcast_items(session, platform)
            for platform in self.DARWIN_ARCHS
        }
        items = self._select_common_items(items_by_platform)
        selected_item = next(iter(items.values()))
        asset_urls = {platform: item.url for platform, item in items.items()}
        for platform in self.DEB_INDEXES:
            asset_urls[platform] = await self._fetch_deb_artifact_url(
                session,
                platform=platform,
            )
        return VersionInfo(
            version=self._format_version(selected_item),
            metadata=AssetURLsMetadata(asset_urls=asset_urls),
        )

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return the release URL for ``platform`` (appcast ZIP or deb pool)."""
        asset_urls = metadata_get(
            info.metadata,
            "asset_urls",
            context="ChatGPT desktop metadata",
        )
        if asset_urls is None:
            return self._fallback_darwin_url(platform, info)
        if not isinstance(asset_urls, dict):
            msg = f"Invalid ChatGPT desktop asset URLs in metadata: {info.metadata!r}"
            raise TypeError(msg)
        asset_urls_map = cast("dict[str, object]", asset_urls)
        url = asset_urls_map.get(platform)
        if not isinstance(url, str) or not url.strip():
            msg = f"Missing ChatGPT desktop URL for platform {platform!r}"
            raise RuntimeError(msg)
        return url

    def _fallback_darwin_url(self, platform: str, info: VersionInfo) -> str:
        """Reconstruct a Darwin ZIP URL when metadata carries no asset URLs."""
        arch = self.DARWIN_ARCHS.get(platform)
        if arch is None:
            msg = f"ChatGPT desktop URL metadata is required for platform {platform!r}"
            raise RuntimeError(msg)
        short_version = info.version.rsplit("-", maxsplit=1)[0]
        return f"{self.ARCHIVE_BASE_URL}/ChatGPT-darwin-{arch}-{short_version}.zip"
