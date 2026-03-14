"""Updater for ChatGPT desktop app releases."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from defusedxml import ElementTree

if TYPE_CHECKING:
    from xml.etree.ElementTree import Element

    import aiohttp

from lib.update.net import fetch_url
from lib.update.updaters.base import DownloadHashUpdater, VersionInfo

_SPARKLE_NS = {"sparkle": "http://www.andymatuschak.org/xml-namespaces/sparkle"}


class ChatGPTUpdater(DownloadHashUpdater):
    """Resolve latest ChatGPT appcast version and artifact URL."""

    name = "chatgpt"

    APPCAST_URL = (
        "https://persistent.oaistatic.com/sidekick/public/sparkle_public_appcast.xml"
    )

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

    def _extract_version(self, item: Element) -> str:
        version = item.findtext("sparkle:shortVersionString", namespaces=_SPARKLE_NS)
        if version is None or not version.strip():
            msg = "No version found in appcast"
            raise RuntimeError(msg)
        return version.strip()

    def _extract_download_url(self, item: Element) -> str:
        enclosure = item.find("enclosure")
        if enclosure is None:
            msg = "No enclosure found in appcast"
            raise RuntimeError(msg)

        url = enclosure.get("url")
        if url is None or not url.strip():
            msg = "No URL found in enclosure"
            raise RuntimeError(msg)
        return url.strip()

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch latest appcast entry and return version + download URL."""
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
        version = self._extract_version(item)
        url = self._extract_download_url(item)
        return VersionInfo(version=version, metadata={"url": url})

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return the appcast-provided download URL for Darwin builds."""
        _ = platform
        url = info.metadata.get("url")
        if isinstance(url, str):
            return url
        msg = f"Missing ChatGPT download URL in metadata: {url!r}"
        raise RuntimeError(msg)
