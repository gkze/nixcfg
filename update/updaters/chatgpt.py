"""Updater for ChatGPT desktop app releases."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    import aiohttp

    from libnix.models.sources import SourceEntry, SourceHashes

from update.net import fetch_url
from update.updaters.base import DownloadHashUpdater, VersionInfo


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

        try:
            root = ET.fromstring(xml_data)  # noqa: S314 — trusted appcast XML
        except ET.ParseError as exc:
            snippet = xml_data[:200].replace("\n", " ").strip()
            msg = f"Invalid appcast XML from {self.APPCAST_URL}: {exc}; snippet: {snippet}"
            raise RuntimeError(msg) from exc
        item = root.find(".//item")
        if item is None:
            msg = "No items found in appcast"
            raise RuntimeError(msg)

        ns = {"sparkle": "http://www.andymatuschak.org/xml-namespaces/sparkle"}

        version_elem = item.find("sparkle:shortVersionString", ns)
        if version_elem is None or version_elem.text is None:
            msg = "No version found in appcast"
            raise RuntimeError(msg)

        enclosure = item.find("enclosure")
        if enclosure is None:
            msg = "No enclosure found in appcast"
            raise RuntimeError(msg)

        url = enclosure.get("url")
        if url is None:
            msg = "No URL found in enclosure"
            raise RuntimeError(msg)

        return VersionInfo(version=version_elem.text, metadata={"url": url})

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return the appcast-provided download URL for Darwin builds."""
        _ = platform
        return info.metadata["url"]

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Build sources entry with Darwin URL mapping."""
        return self._build_result_with_urls(
            info,
            hashes,
            {"darwin": info.metadata["url"]},
        )
