"""Updater for Commander DMG releases."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    import aiohttp

from lib.update.net import fetch_headers, fetch_url
from lib.update.updaters.base import DownloadHashUpdater, VersionInfo, register_updater
from lib.update.updaters.metadata import NO_METADATA, DownloadUrlMetadata

_MARKDOWN_CHANGELOG_VERSION_RE = re.compile(
    r"^##\s+([0-9]+(?:\.[0-9]+)+)\s+-\s+", re.MULTILINE
)
_HTML_CHANGELOG_VERSION_RE = re.compile(
    r"<h2\b[^>]*>\s*([0-9]+(?:\.[0-9]+)+)\s+-\s+[^<]+</h2>",
    re.IGNORECASE,
)


def _extract_latest_version(content: str) -> str | None:
    for pattern in (_MARKDOWN_CHANGELOG_VERSION_RE, _HTML_CHANGELOG_VERSION_RE):
        match = pattern.search(content)
        if match is not None:
            return match.group(1)
    return None


@register_updater
class CommanderUpdater(DownloadHashUpdater):
    """Resolve Commander version from the public changelog."""

    name = "commander"
    VERSIONED_URL_TEMPLATE = (
        "https://download.thecommander.app/release/Commander-{version}.dmg"
    )
    LATEST_URL = "https://download.thecommander.app/release/Commander.dmg"
    CHANGELOG_URL = "https://thecommander.app/changelog.html"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin",
        "x86_64-darwin": "darwin",
    }

    @staticmethod
    def _download_url_from_metadata(info: VersionInfo) -> str | None:
        metadata = info.metadata
        if metadata is None:
            return None
        metadata_map = (
            {str(key): value for key, value in metadata.items()}
            if isinstance(metadata, dict)
            else None
        )
        url = (
            metadata_map.get("url")
            if metadata_map is not None
            else getattr(metadata, "url", None)
        )
        return url if isinstance(url, str) else None

    @staticmethod
    def _is_missing_release_error(exc: RuntimeError) -> bool:
        return re.search(r"\b404\b", str(exc)) is not None

    async def _resolve_download_metadata(
        self,
        session: aiohttp.ClientSession,
        *,
        version: str,
    ) -> DownloadUrlMetadata | None:
        versioned_url = self.VERSIONED_URL_TEMPLATE.format(version=version)
        try:
            await fetch_headers(
                session,
                versioned_url,
                request_timeout=self.config.default_timeout,
                config=self.config,
            )
        except RuntimeError as exc:
            if not self._is_missing_release_error(exc):
                raise
            await fetch_headers(
                session,
                self.LATEST_URL,
                request_timeout=self.config.default_timeout,
                config=self.config,
            )
            return DownloadUrlMetadata(url=self.LATEST_URL)
        return None

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return a Commander DMG URL for ``platform``."""
        _ = platform
        return self._download_url_from_metadata(
            info
        ) or self.VERSIONED_URL_TEMPLATE.format(version=info.version)

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Parse the latest release version from the changelog page."""
        payload = await fetch_url(
            session,
            self.CHANGELOG_URL,
            request_timeout=self.config.default_timeout,
            config=self.config,
            user_agent=self.config.default_user_agent,
        )
        content = payload.decode(errors="replace")
        version = _extract_latest_version(content)
        if version is None:
            msg = "Could not parse latest Commander version from changelog"
            raise RuntimeError(msg)
        metadata = await self._resolve_download_metadata(session, version=version)
        return VersionInfo(version=version, metadata=metadata or NO_METADATA)
