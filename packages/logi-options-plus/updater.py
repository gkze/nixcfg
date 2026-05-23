"""Updater for Logitech Options+."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from lib.update.updaters.base import DownloadHashUpdater, VersionInfo, register_updater
from lib.update.updaters.vendor_feeds import fetch_head_artifact_version

if TYPE_CHECKING:
    import aiohttp


@register_updater
class LogiOptionsPlusUpdater(DownloadHashUpdater):
    """Track Logitech's stable Options+ installer URL by vendor HTTP metadata."""

    name = "logi-options-plus"
    DOWNLOAD_URL = (
        "https://download01.logi.com/web/ftp/pub/techsupport/optionsplus/"
        "logioptionsplus_installer.zip"
    )
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": DOWNLOAD_URL,
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch a version token from Logitech's mutable installer headers."""
        version = await fetch_head_artifact_version(
            session,
            self.DOWNLOAD_URL,
            config=self.config,
        )
        return VersionInfo(version=version)
