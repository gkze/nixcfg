"""Updater for Factory desktop macOS releases."""

import re
from typing import TYPE_CHECKING, ClassVar

from lib.update.net import fetch_url
from lib.update.updaters import DownloadHashUpdater, VersionInfo, register_updater

if TYPE_CHECKING:
    import aiohttp


@register_updater
class FactoryUpdater(DownloadHashUpdater):
    """Resolve Factory's signed per-architecture DMGs from its release feed."""

    name = "factory"
    # Factory republishes artifacts at an existing versioned path, so always
    # refresh hashes even when LATEST still names the current version.
    materialize_when_current = True
    LATEST_URL = "https://downloads.factory.ai/factory-desktop/LATEST"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
        "x86_64-darwin": "x64",
    }
    DOWNLOAD_URL_TEMPLATE = (
        "https://downloads.factory.ai/factory-desktop/releases/{version}/"
        "darwin/{platform_value}/Factory-{version}-{platform_value}.dmg"
    )

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch and validate the current Factory desktop version."""
        payload = await fetch_url(
            session,
            self.LATEST_URL,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        match = re.fullmatch(r"v?(?P<version>\d+(?:\.\d+)+)", payload.decode().strip())
        if match is None:
            msg = f"Could not parse Factory version from {self.LATEST_URL}"
            raise RuntimeError(msg)
        return VersionInfo(version=match.group("version"))
