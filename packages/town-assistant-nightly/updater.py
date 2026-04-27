"""Updater for the Town Assistant nightly macOS appcast."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from defusedxml import ElementTree

from lib.update.net import fetch_url
from lib.update.updaters.base import DownloadHashUpdater, VersionInfo, register_updater
from lib.update.updaters.metadata import DownloadUrlMetadata, require_metadata_str

if TYPE_CHECKING:
    from xml.etree.ElementTree import Element

    import aiohttp

_SPARKLE_NS = "http://www.andymatuschak.org/xml-namespaces/sparkle"
_SPARKLE_SHORT_VERSION = f"{{{_SPARKLE_NS}}}shortVersionString"
_SPARKLE_BUILD_VERSION = f"{{{_SPARKLE_NS}}}version"


@register_updater
class TownAssistantNightlyUpdater(DownloadHashUpdater):
    """Resolve the latest Town Assistant nightly DMG from Sparkle metadata."""

    name = "town-assistant-nightly"
    APPCAST_URL = (
        "https://town-macos-app.s3.us-east-1.amazonaws.com/desktop/nightly/appcast.xml"
    )
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin-aarch64",
    }

    def _parse_appcast(self, xml_data: str) -> Element:
        try:
            return ElementTree.fromstring(xml_data)
        except ElementTree.ParseError as exc:
            snippet = xml_data[:200].replace("\n", " ").strip()
            msg = f"Invalid Town Assistant appcast XML; snippet: {snippet}"
            raise RuntimeError(msg) from exc

    def _extract_item(self, root: Element) -> Element:
        item = root.find("./channel/item")
        if item is None:
            msg = "No items found in Town Assistant appcast"
            raise RuntimeError(msg)
        return item

    def _extract_enclosure(self, item: Element) -> Element:
        enclosure = item.find("enclosure")
        if enclosure is None:
            msg = "No enclosure found in Town Assistant appcast"
            raise RuntimeError(msg)
        return enclosure

    @staticmethod
    def _required_text(item: Element, tag: str, label: str) -> str:
        node = item.find(tag)
        if node is None:
            msg = f"No {label} found in Town Assistant appcast"
            raise RuntimeError(msg)
        text = node.text
        if text is None:
            msg = f"Empty {label} in Town Assistant appcast"
            raise RuntimeError(msg)
        value = text.strip()
        if not value:
            msg = f"Blank {label} in Town Assistant appcast"
            raise RuntimeError(msg)
        return value

    def _extract_version(self, item: Element) -> str:
        short_version = self._required_text(
            item,
            _SPARKLE_SHORT_VERSION,
            "short version",
        )
        build_version = self._required_text(
            item,
            _SPARKLE_BUILD_VERSION,
            "build version",
        )
        return f"{short_version}-{build_version}"

    @staticmethod
    def _extract_download_url(enclosure: Element) -> str:
        url = enclosure.get("url")
        if url is None:
            msg = "No URL found in Town Assistant appcast enclosure"
            raise RuntimeError(msg)
        value = url.strip()
        if not value:
            msg = "Blank URL found in Town Assistant appcast enclosure"
            raise RuntimeError(msg)
        return value

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch the current nightly appcast item and return its DMG URL."""
        xml_payload = await fetch_url(
            session,
            self.APPCAST_URL,
            user_agent="Sparkle/2.0",
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        root = self._parse_appcast(xml_payload.decode())
        item = self._extract_item(root)
        enclosure = self._extract_enclosure(item)
        return VersionInfo(
            version=self._extract_version(item),
            metadata=DownloadUrlMetadata(url=self._extract_download_url(enclosure)),
        )

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return the appcast-provided nightly DMG URL for Darwin builds."""
        _ = platform
        return require_metadata_str(
            info.metadata,
            "url",
            context="Town Assistant nightly metadata",
        )
