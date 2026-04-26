"""Updater for the Zen Twilight channel DMG."""

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

    from lib.nix.models.sources import SourceEntry
    from lib.update.updaters.base import UpdateContext


@register_updater
class ZenTwilightUpdater(DownloadHashUpdater):
    """Track the channel-pinned Twilight DMG and recompute its source hash."""

    name = "zen-twilight"
    TWILIGHT_DMG_URL = (
        "https://github.com/zen-browser/desktop/releases/download/"
        "twilight-1/zen.macos-universal.dmg"
    )
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": TWILIGHT_DMG_URL,
        "x86_64-darwin": TWILIGHT_DMG_URL,
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Keep the release build ID explicit until Twilight exposes update metadata."""
        _ = session
        return VersionInfo(version=read_pinned_source_version(self.name))

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        """Recompute hashes for the pinned channel artifact before comparing."""
        _ = (context, info)
        return False
