"""Updater for ChatGPT desktop app releases."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    import aiohttp

from lib.update.net import fetch_url
from lib.update.updaters.base import DownloadHashUpdater, VersionInfo

_ITEM_RE = re.compile(r"<item\b.*?</item>", re.DOTALL | re.IGNORECASE)
_SHORT_VERSION_RE = re.compile(
    r"<sparkle:shortVersionString>\s*([^<]+?)\s*</sparkle:shortVersionString>",
    re.IGNORECASE,
)
_ENCLOSURE_RE = re.compile(r"<enclosure\b[^>]*>", re.IGNORECASE)
_ENCLOSURE_URL_RE = re.compile(r"\burl\s*=\s*(['\"])([^'\"]+)\1", re.IGNORECASE)


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

    def _extract_item(self, xml_data: str) -> str:
        match = _ITEM_RE.search(xml_data)
        if match is None:
            msg = "No items found in appcast"
            raise RuntimeError(msg)
        return match.group(0)

    def _extract_version(self, item_xml: str) -> str:
        match = _SHORT_VERSION_RE.search(item_xml)
        if match is None:
            msg = "No version found in appcast"
            raise RuntimeError(msg)
        return match.group(1).strip()

    def _extract_download_url(self, item_xml: str) -> str:
        enclosure_match = _ENCLOSURE_RE.search(item_xml)
        if enclosure_match is None:
            msg = "No enclosure found in appcast"
            raise RuntimeError(msg)

        url_match = _ENCLOSURE_URL_RE.search(enclosure_match.group(0))
        if url_match is None:
            msg = "No URL found in enclosure"
            raise RuntimeError(msg)
        return url_match.group(2).strip()

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
        if "<rss" not in xml_data or "</rss>" not in xml_data:
            snippet = xml_data[:200].replace("\n", " ").strip()
            msg = f"Invalid appcast XML from {self.APPCAST_URL}; snippet: {snippet}"
            raise RuntimeError(msg)
        item_xml = self._extract_item(xml_data)
        version = self._extract_version(item_xml)
        url = self._extract_download_url(item_xml)
        return VersionInfo(version=version, metadata={"url": url})

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return the appcast-provided download URL for Darwin builds."""
        _ = platform
        url = info.metadata.get("url")
        if isinstance(url, str):
            return url
        msg = f"Missing ChatGPT download URL in metadata: {url!r}"
        raise RuntimeError(msg)
