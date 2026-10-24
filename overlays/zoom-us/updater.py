"""Updater for Zoom macOS downloads."""

import asyncio
import re
from typing import TYPE_CHECKING, ClassVar
from urllib.parse import urlparse

from lib.update.net import request
from lib.update.updaters import DownloadHashUpdater, VersionInfo, register_updater
from lib.update.updaters.core import verify_platform_versions
from lib.update.updaters.metadata import NO_METADATA

if TYPE_CHECKING:
    import aiohttp

    from lib.update.updaters.core import UpdateContext

_VERSION_PATTERN = re.compile(r"/prod/(?P<version>[^/]+)/")


@register_updater
class ZoomUsUpdater(DownloadHashUpdater):
    """Resolve the latest Zoom macOS version and pinned download URLs."""

    name = "zoom-us"

    _LATEST_URLS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "https://zoom.us/client/latest/zoomusInstallerFull.pkg?archType=arm64",
        "x86_64-darwin": "https://zoom.us/client/latest/zoomusInstallerFull.pkg",
    }
    _VERSIONED_URLS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "https://zoom.us/client/{version}/zoomusInstallerFull.pkg?archType=arm64",
        "x86_64-darwin": "https://zoom.us/client/{version}/zoomusInstallerFull.pkg",
    }
    PLATFORMS: ClassVar[dict[str, str]] = dict.fromkeys(_VERSIONED_URLS, "")

    @staticmethod
    def _extract_version(resolved_url: str) -> str:
        path = urlparse(resolved_url).path
        match = _VERSION_PATTERN.search(path)
        if match is None:
            msg = f"Could not extract Zoom version from resolved URL: {resolved_url}"
            raise RuntimeError(msg)
        version = match.group("version")
        if not version:
            msg = f"Resolved Zoom URL did not include a version: {resolved_url}"
            raise RuntimeError(msg)
        return version

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Resolve the latest stable Zoom version from the platform redirect URLs."""
        _ = context
        responses = await asyncio.gather(
            *(
                request(session, latest_url, method="HEAD", config=self.config)
                for latest_url in self._LATEST_URLS.values()
            )
        )
        versions = {
            platform: self._extract_version(response.url)
            for platform, response in zip(self._LATEST_URLS, responses, strict=True)
        }
        return VersionInfo(
            version=verify_platform_versions(
                versions,
                context="Zoom latest URLs resolved to mismatched versions",
            ),
            metadata=NO_METADATA,
        )

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return the pinned version-specific Zoom download URL for ``platform``."""
        template = self._VERSIONED_URLS.get(platform)
        if template is None:
            msg = f"Unsupported platform for zoom-us updater: {platform}"
            raise RuntimeError(msg)
        return template.format(version=info.version)
