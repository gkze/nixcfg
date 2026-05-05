"""Updater for Codex desktop Sparkle releases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, cast

from defusedxml import ElementTree

from lib.update.net import fetch_url
from lib.update.updaters.base import (
    DownloadHashUpdater,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import AssetURLsMetadata, metadata_get

if TYPE_CHECKING:
    from xml.etree.ElementTree import Element

    import aiohttp

_SPARKLE_NS = "http://www.andymatuschak.org/xml-namespaces/sparkle"
_SPARKLE_SHORT_VERSION = f"{{{_SPARKLE_NS}}}shortVersionString"
_SPARKLE_BUILD_VERSION = f"{{{_SPARKLE_NS}}}version"


@dataclass(frozen=True, slots=True)
class _CodexAppcastItem:
    short_version: str
    build_version: str
    url: str


@register_updater
class CodexDesktopUpdater(DownloadHashUpdater):
    """Track immutable Codex desktop archives from the Sparkle appcasts."""

    name = "codex-desktop"
    ARCHIVE_BASE_URL = "https://persistent.oaistatic.com/codex-app-prod"
    APPCASTS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "https://persistent.oaistatic.com/codex-app-prod/appcast.xml",
        "x86_64-darwin": "https://persistent.oaistatic.com/codex-app-prod/appcast-x64.xml",
    }
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
        "x86_64-darwin": "x64",
    }

    def _parse_appcast(self, xml_data: str, *, appcast_url: str) -> Element:
        try:
            return ElementTree.fromstring(xml_data)
        except ElementTree.ParseError as exc:
            snippet = xml_data[:200].replace("\n", " ").strip()
            msg = f"Invalid Codex appcast XML from {appcast_url}; snippet: {snippet}"
            raise RuntimeError(msg) from exc

    @staticmethod
    def _extract_items(root: Element, *, appcast_url: str) -> tuple[Element, ...]:
        items = tuple(root.findall("./channel/item"))
        if not items:
            msg = f"No items found in Codex appcast {appcast_url}"
            raise RuntimeError(msg)
        return items

    @staticmethod
    def _required_text(item: Element, tag: str, label: str) -> str:
        node = item.find(tag)
        if node is None:
            msg = f"No {label} found in Codex appcast"
            raise RuntimeError(msg)
        value = (node.text or "").strip()
        if not value:
            msg = f"Blank {label} found in Codex appcast"
            raise RuntimeError(msg)
        return value

    @staticmethod
    def _extract_enclosure(item: Element) -> Element:
        enclosure = item.find("enclosure")
        if enclosure is None:
            msg = "No enclosure found in Codex appcast"
            raise RuntimeError(msg)
        return enclosure

    @staticmethod
    def _extract_download_url(enclosure: Element) -> str:
        value = (enclosure.get("url") or "").strip()
        if not value:
            msg = "No URL found in Codex appcast enclosure"
            raise RuntimeError(msg)
        return value

    def _extract_appcast_item(self, item: Element) -> _CodexAppcastItem:
        enclosure = self._extract_enclosure(item)
        return _CodexAppcastItem(
            short_version=self._required_text(
                item,
                _SPARKLE_SHORT_VERSION,
                "short version",
            ),
            build_version=self._required_text(
                item,
                _SPARKLE_BUILD_VERSION,
                "build version",
            ),
            url=self._extract_download_url(enclosure),
        )

    def _extract_appcast_items(
        self,
        root: Element,
        *,
        appcast_url: str,
    ) -> tuple[_CodexAppcastItem, ...]:
        return tuple(
            self._extract_appcast_item(item)
            for item in self._extract_items(root, appcast_url=appcast_url)
        )

    @staticmethod
    def _release_key(item: _CodexAppcastItem) -> tuple[str, str]:
        return item.short_version, item.build_version

    @classmethod
    def _format_version(cls, item: _CodexAppcastItem) -> str:
        short_version, build_version = cls._release_key(item)
        return f"{short_version}-{build_version}"

    def _select_common_items(
        self,
        items_by_platform: dict[str, tuple[_CodexAppcastItem, ...]],
    ) -> dict[str, _CodexAppcastItem]:
        keyed_items: dict[str, dict[tuple[str, str], _CodexAppcastItem]] = {}
        for platform, items in items_by_platform.items():
            platform_items: dict[tuple[str, str], _CodexAppcastItem] = {}
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
        msg = f"No common Codex desktop release across platform appcasts: {versions}"
        raise RuntimeError(msg)

    async def _fetch_appcast_items(
        self,
        session: aiohttp.ClientSession,
        platform: str,
    ) -> tuple[_CodexAppcastItem, ...]:
        appcast_url = self.APPCASTS[platform]
        xml_payload = await fetch_url(
            session,
            appcast_url,
            user_agent="Sparkle/2.0",
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        root = self._parse_appcast(xml_payload.decode(), appcast_url=appcast_url)
        return self._extract_appcast_items(root, appcast_url=appcast_url)

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch both appcasts and return a release plus per-platform archive URLs."""
        items_by_platform = {
            platform: await self._fetch_appcast_items(session, platform)
            for platform in self.PLATFORMS
        }
        items = self._select_common_items(items_by_platform)
        selected_item = next(iter(items.values()))
        return VersionInfo(
            version=self._format_version(selected_item),
            metadata=AssetURLsMetadata(
                asset_urls={platform: item.url for platform, item in items.items()},
            ),
        )

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return the appcast-provided versioned ZIP URL for ``platform``."""
        asset_urls = metadata_get(
            info.metadata,
            "asset_urls",
            context="Codex desktop metadata",
        )
        if asset_urls is None:
            short_version = info.version.rsplit("-", maxsplit=1)[0]
            arch = self.PLATFORMS[platform]
            return f"{self.ARCHIVE_BASE_URL}/Codex-darwin-{arch}-{short_version}.zip"
        if not isinstance(asset_urls, dict):
            msg = f"Invalid Codex desktop asset URLs in metadata: {info.metadata!r}"
            raise TypeError(msg)
        asset_urls_map = cast("dict[str, object]", asset_urls)
        url = asset_urls_map.get(platform)
        if not isinstance(url, str) or not url.strip():
            msg = f"Missing Codex desktop URL for platform {platform!r}"
            raise RuntimeError(msg)
        return url
