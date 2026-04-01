"""Updater for Wispr Flow Darwin DMG releases."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from lib.update.updaters.base import register_updater
from lib.update.updaters.platform_api import DownloadingPlatformAPIUpdater

if TYPE_CHECKING:
    from lib.update.updaters.base import VersionInfo


@register_updater
class WisprFlowUpdater(DownloadingPlatformAPIUpdater):
    """Resolve Wispr Flow versions from the per-arch JSON release feeds."""

    name = "wispr-flow"
    VERSION_KEY = "currentRelease"
    required_tools = ("nix", "nix-prefetch-url")
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
        "x86_64-darwin": "x64",
    }

    def _api_url(self, _api_platform: str) -> str:
        return (
            f"https://dl.wisprflow.com/wispr-flow/darwin/{_api_platform}/RELEASES.json"
        )

    def _download_url(self, _api_platform: str, info: VersionInfo) -> str:
        return (
            f"https://dl.wisprflow.com/wispr-flow/darwin/{_api_platform}/dmgs/"
            f"Flow-v{info.version}.dmg"
        )
