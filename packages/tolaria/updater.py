"""Updater for stable Tolaria releases and their native macOS archives."""

from datetime import date
from typing import ClassVar

from lib.update.updaters import GitHubReleaseAssetURLsUpdater, register_updater


@register_updater
class TolariaUpdater(GitHubReleaseAssetURLsUpdater):
    """Resolve the latest release and validate its Apple Silicon asset."""

    name = "tolaria"
    GITHUB_OWNER = "refactoringhq"
    GITHUB_REPO = "tolaria"
    PLATFORMS: ClassVar[dict[str, str]] = {"aarch64-darwin": "Silicon"}
    ASSET_NAME_TEMPLATE = "Tolaria_{version}_macOS_{platform_value}.app.tar.gz"

    def _normalize_release_version(self, tag_name: str) -> str:
        """Convert the publisher's ISO date tag to its numeric app version."""
        release_date = date.fromisoformat(super()._normalize_release_version(tag_name))
        return f"{release_date.year}.{release_date.month}.{release_date.day}"

    def _fallback_url(self, version: str, platform_value: str) -> str:
        """Retain the publisher's dated URL convention for existing metadata."""
        year, month, day = (int(part) for part in version.split("."))
        tag = f"v{date(year, month, day).isoformat()}"
        return (
            f"https://github.com/{self.GITHUB_OWNER}/{self.GITHUB_REPO}/releases/"
            f"download/{tag}/{self._asset_name(version, platform_value)}"
        )
