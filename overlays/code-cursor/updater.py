"""Updater for Cursor editor release metadata and downloads."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from lib.update.updaters.platform_api import PlatformAPIUpdater

if TYPE_CHECKING:
    from lib.update.updaters.base import VersionInfo


class CodeCursorUpdater(PlatformAPIUpdater):
    """Resolve Cursor versions and platform-specific download URLs."""

    name = "code-cursor"
    API_BASE = "https://www.cursor.com/api/download"
    VERSION_KEY = "version"
    EXTRA_EQUALITY_KEYS = ("commitSha",)
    COMMIT_METADATA_KEY = "commitSha"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin-arm64",
        "x86_64-darwin": "darwin-x64",
        "aarch64-linux": "linux-arm64",
        "x86_64-linux": "linux-x64",
    }

    def _api_url(self, _api_platform: str) -> str:
        return f"{self.API_BASE}?platform={_api_platform}&releaseTrack=stable"

    def _download_url(self, _api_platform: str, info: VersionInfo) -> str:
        platform_info = info.metadata["platform_info"]
        return platform_info[_api_platform]["downloadUrl"]
