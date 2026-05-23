"""Updater for Freelens."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters.base import register_updater
from lib.update.updaters.github_release import GitHubReleaseAssetURLsUpdater


@register_updater
class FreelensUpdater(GitHubReleaseAssetURLsUpdater):
    """Resolve Freelens from its upstream GitHub release assets."""

    name = "freelens"
    GITHUB_OWNER = "freelensapp"
    GITHUB_REPO = "freelens"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
    }

    def _asset_name(self, version: str, platform_value: str) -> str:
        """Return the expected Freelens macOS asset name."""
        return f"Freelens-{version}-macos-{platform_value}.dmg"
