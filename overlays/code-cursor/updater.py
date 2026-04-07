"""Updater for Cursor editor release metadata and downloads."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from lib.update.updaters.base import register_updater
from lib.update.updaters.platform_api import DownloadingPlatformAPIUpdater

if TYPE_CHECKING:
    from lib.update.updaters.base import VersionInfo


@register_updater
class CodeCursorUpdater(DownloadingPlatformAPIUpdater):
    """Resolve Cursor versions and platform-specific download URLs."""

    name = "code-cursor"
    API_BASE = "https://www.cursor.com/api/download"
    VERSION_KEY = "version"
    EXTRA_EQUALITY_KEYS = ("commitSha",)
    COMMIT_METADATA_KEY = "commitSha"
    required_tools = ("nix", "nix-prefetch-url")
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin-arm64",
        "x86_64-darwin": "darwin-x64",
        "aarch64-linux": "linux-arm64",
        "x86_64-linux": "linux-x64",
    }

    def _api_url(self, _api_platform: str) -> str:
        return f"{self.API_BASE}?platform={_api_platform}&releaseTrack=stable"

    def _download_url(self, _api_platform: str, info: VersionInfo) -> str:
        # platform_info is keyed by nix platform (aarch64-darwin, etc.);
        # reverse-lookup the nix key for the given API platform name.
        nix_plat = next(n for n, a in self.PLATFORMS.items() if a == _api_platform)
        metadata = self._metadata(info)
        payload = metadata.platform_info.get(nix_plat)
        if not isinstance(payload, dict):
            msg = f"Expected platform payload for {nix_plat}"
            raise TypeError(msg)
        download_url = payload.get("downloadUrl")
        if not isinstance(download_url, str):
            msg = f"Expected downloadUrl string for {nix_plat}"
            raise TypeError(msg)
        return download_url
