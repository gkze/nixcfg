"""Updater for Zoom macOS downloads."""

from __future__ import annotations

import asyncio
import re
from typing import ClassVar
from urllib.parse import urlparse

import aiohttp

from lib.update.net import HTTP_BAD_REQUEST
from lib.update.updaters.base import DownloadHashUpdater, VersionInfo, register_updater
from lib.update.updaters.metadata import NO_METADATA

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

    async def _resolve_latest_url(
        self, session: aiohttp.ClientSession, latest_url: str
    ) -> str:
        timeout = aiohttp.ClientTimeout(total=self.config.default_timeout)
        async with session.request(
            "HEAD",
            latest_url,
            allow_redirects=True,
            timeout=timeout,
        ) as response:
            if response.status >= HTTP_BAD_REQUEST:
                msg = (
                    f"Failed to resolve Zoom latest URL {latest_url}: "
                    f"HTTP {response.status} {response.reason}"
                )
                raise RuntimeError(msg)
            return str(response.url)

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve the latest stable Zoom version from the platform redirect URLs."""
        resolved_pairs = await asyncio.gather(
            *(
                self._resolve_platform_latest(session, platform, latest_url)
                for platform, latest_url in self._LATEST_URLS.items()
            )
        )
        versions = {
            platform: self._extract_version(url) for platform, url in resolved_pairs
        }
        unique_versions = set(versions.values())
        if len(unique_versions) != 1:
            msg = "Zoom latest URLs resolved to mismatched versions: " + ", ".join(
                f"{platform}={version}"
                for platform, version in sorted(versions.items())
            )
            raise RuntimeError(msg)
        return VersionInfo(version=unique_versions.pop(), metadata=NO_METADATA)

    async def _resolve_platform_latest(
        self,
        session: aiohttp.ClientSession,
        platform: str,
        latest_url: str,
    ) -> tuple[str, str]:
        return platform, await self._resolve_latest_url(session, latest_url)

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return the pinned version-specific Zoom download URL for ``platform``."""
        template = self._VERSIONED_URLS.get(platform)
        if template is None:
            msg = f"Unsupported platform for zoom-us updater: {platform}"
            raise RuntimeError(msg)
        return template.format(version=info.version)
