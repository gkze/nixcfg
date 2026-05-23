"""Updater for the pinned Mole helper binary archives."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from lib.update.updaters.base import (
    DownloadHashUpdater,
    VersionInfo,
    read_pinned_source_version,
    register_updater,
)

if TYPE_CHECKING:
    import aiohttp


@register_updater
class MoleAppUpdater(DownloadHashUpdater):
    """Rehash the pinned Mole assets without advancing the source tarball."""

    name = "mole-app"
    materialize_when_current = True
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin-arm64",
        "x86_64-darwin": "darwin-amd64",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Return the pinned version until the source tarball hash is managed."""
        _ = session
        return VersionInfo(version=read_pinned_source_version(self.name))

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return the pinned Mole binary archive URL for the platform."""
        asset = self.PLATFORMS[platform]
        return (
            "https://github.com/tw93/Mole/releases/download/"
            f"V{info.version}/binaries-{asset}.tar.gz"
        )
