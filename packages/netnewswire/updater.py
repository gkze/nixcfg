"""Updater for NetNewsWire macOS Sparkle releases."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from defusedxml import ElementTree

if TYPE_CHECKING:
    from xml.etree.ElementTree import Element

    import aiohttp

from lib.update.net import fetch_url
from lib.update.updaters.base import DownloadHashUpdater, VersionInfo, register_updater
from lib.update.updaters.metadata import DownloadUrlMetadata, require_metadata_str

_SPARKLE_NS = "http://www.andymatuschak.org/xml-namespaces/sparkle"
_SPARKLE_VERSION_ATTR = f"{{{_SPARKLE_NS}}}shortVersionString"


@register_updater
class NetNewsWireUpdater(DownloadHashUpdater):
    """Resolve the latest NetNewsWire macOS ZIP from its Sparkle appcast."""

    name = "netnewswire"
    APPCAST_URL = "https://ranchero.com/downloads/netnewswire-release.xml"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin",
        "x86_64-darwin": "darwin",
    }

    def _parse_appcast(self, xml_data: str) -> Element:
        try:
            return ElementTree.fromstring(xml_data)
        except ElementTree.ParseError as exc:
            snippet = xml_data[:200].replace("\n", " ").strip()
            msg = f"Invalid appcast XML from {self.APPCAST_URL}; snippet: {snippet}"
            raise RuntimeError(msg) from exc

    def _extract_item(self, root: Element) -> Element:
        item = root.find("./channel/item")
        if item is None:
            msg = "No items found in appcast"
            raise RuntimeError(msg)
        return item

    def _extract_enclosure(self, item: Element) -> Element:
        enclosure = item.find("enclosure")
        if enclosure is None:
            msg = "No enclosure found in appcast"
            raise RuntimeError(msg)
        return enclosure

    def _extract_version(self, enclosure: Element) -> str:
        version = enclosure.get(_SPARKLE_VERSION_ATTR)
        if version is None or not version.strip():
            msg = "No version found in appcast enclosure"
            raise RuntimeError(msg)
        return version.strip()

    def _extract_download_url(self, enclosure: Element) -> str:
        url = enclosure.get("url")
        if url is None or not url.strip():
            msg = "No URL found in enclosure"
            raise RuntimeError(msg)
        return url.strip()

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch the latest appcast item and return version plus download URL."""
        xml_payload = await fetch_url(
            session,
            self.APPCAST_URL,
            user_agent="Sparkle/2.0",
            timeout=self.config.default_timeout,
            config=self.config,
        )
        xml_data = xml_payload.decode()
        root = self._parse_appcast(xml_data)
        item = self._extract_item(root)
        enclosure = self._extract_enclosure(item)
        version = self._extract_version(enclosure)
        url = self._extract_download_url(enclosure)
        return VersionInfo(version=version, metadata=DownloadUrlMetadata(url=url))

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return the appcast-provided download URL for Darwin builds."""
        _ = platform
        try:
            return require_metadata_str(
                info.metadata,
                "url",
                context="NetNewsWire metadata",
            )
        except TypeError as exc:
            msg = f"Missing NetNewsWire download URL in metadata: {info.metadata!r}"
            raise RuntimeError(msg) from exc
