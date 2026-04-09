"""Updater for Raycast DMG releases."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters.base import VersionInfo, register_updater
from lib.update.updaters.platform_api import DownloadingPlatformAPIUpdater


@register_updater
class RaycastUpdater(DownloadingPlatformAPIUpdater):
    """Resolve Raycast versions from the upstream per-arch JSON feeds."""

    name = "raycast"
    VERSION_KEY = "version"
    COMMIT_METADATA_KEY = "targetCommitish"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm",
        "x86_64-darwin": "x86_64",
    }

    def _api_url(self, _api_platform: str) -> str:
        return f"https://releases.raycast.com/releases/latest?build={_api_platform}"

    def _download_url(self, _api_platform: str, info: VersionInfo) -> str:
        return f"https://releases.raycast.com/releases/{info.version}/download?build={_api_platform}"
