"""Updater for VS Code Insiders platform builds."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.update.updaters.platform_api import PlatformAPIUpdater

if TYPE_CHECKING:
    from lib.update.updaters.base import VersionInfo

VSCODE_PLATFORMS = {
    "aarch64-darwin": "darwin-arm64",
    "aarch64-linux": "linux-arm64",
    "x86_64-linux": "linux-x64",
}


class VSCodeInsidersUpdater(PlatformAPIUpdater):
    """Updater for upstream VS Code Insiders builds."""

    name = "vscode-insiders"
    PLATFORMS = VSCODE_PLATFORMS
    VERSION_KEY = "productVersion"
    CHECKSUM_KEY = "sha256hash"

    def _api_url(self, _api_platform: str) -> str:
        return (
            f"https://update.code.visualstudio.com/api/update/{_api_platform}/"
            "insider/latest"
        )

    def _download_url(self, _api_platform: str, info: VersionInfo) -> str:
        return f"https://update.code.visualstudio.com/{info.version}/{_api_platform}/insider"
