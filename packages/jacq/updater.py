"""Updater for Jacq macOS releases."""

from __future__ import annotations

import re
from typing import ClassVar
from urllib.parse import urlparse

import aiohttp

from lib.update.net import HTTP_BAD_REQUEST
from lib.update.updaters import DownloadHashUpdater, VersionInfo, register_updater

_LATEST_URL = "https://downloads.jacquard.dev/latest/mac-arm64.dmg"
_VERSION_PATTERN = re.compile(
    r"/releases/(?P<version>[^/]+)/Jacq-(?P=version)-arm64\.dmg"
)


@register_updater
class JacqUpdater(DownloadHashUpdater):
    """Resolve the latest Jacq release and hash its macOS DMG."""

    name = "jacq"
    PLATFORMS: ClassVar[dict[str, str]] = {"aarch64-darwin": "arm64"}
    DOWNLOAD_URL_TEMPLATE = (
        "https://downloads.jacquard.dev/releases/{version}/"
        "Jacq-{version}-{platform_value}.dmg"
    )

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve the current version from Jacq's official latest redirect."""
        timeout = aiohttp.ClientTimeout(total=self.config.default_timeout)
        async with session.request(
            "HEAD",
            _LATEST_URL,
            allow_redirects=True,
            timeout=timeout,
        ) as response:
            if response.status >= HTTP_BAD_REQUEST:
                msg = (
                    f"Failed to resolve Jacq latest URL {_LATEST_URL}: "
                    f"HTTP {response.status} {response.reason}"
                )
                raise RuntimeError(msg)
            resolved_url = str(response.url)

        parsed_url = urlparse(resolved_url)
        match = _VERSION_PATTERN.fullmatch(parsed_url.path)
        if (
            parsed_url.scheme != "https"
            or parsed_url.netloc != "downloads.jacquard.dev"
            or match is None
        ):
            msg = f"Could not extract Jacq version from resolved URL: {resolved_url}"
            raise RuntimeError(msg)
        return VersionInfo(version=match.group("version"))
