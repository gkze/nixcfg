"""Updater for Superset Desktop Linux AppImage metadata."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters.base import register_updater
from lib.update.updaters.github_release import GitHubReleaseAssetURLsUpdater


@register_updater
class SupersetUpdater(GitHubReleaseAssetURLsUpdater):
    """Track Superset Desktop AppImage URL and hash for Linux."""

    name = "superset"
    GITHUB_OWNER = "superset-sh"
    GITHUB_REPO = "superset"
    TAG_PREFIX = "desktop-v"

    PLATFORMS: ClassVar[dict[str, str]] = {
        "x86_64-linux": "x86_64",
    }

    def _asset_name(self, version: str, platform_value: str) -> str:
        return f"superset-{version}-{platform_value}.AppImage"

    def _fallback_url(self, version: str, platform_value: str) -> str:
        return (
            "https://github.com/superset-sh/superset/releases/download/"
            f"{self.TAG_PREFIX}{version}/{self._asset_name(version, platform_value)}"
        )

    def _normalize_release_version(self, tag_name: str) -> str:
        if not tag_name.startswith(self.TAG_PREFIX):
            msg = f"Unexpected Superset release tag format: {tag_name}"
            raise RuntimeError(msg)

        version = tag_name.removeprefix(self.TAG_PREFIX)
        if not version:
            msg = f"Missing version segment in Superset release tag: {tag_name}"
            raise RuntimeError(msg)
        return version

    def _missing_asset_message(self, expected_name: str, tag_name: str) -> str:
        return (
            "Could not find Superset desktop release asset "
            f"{expected_name!r} in tag {tag_name}"
        )
