"""Updater for Thorium's macOS release artifacts."""

from typing import ClassVar

from lib.update.updaters import GitHubReleaseAssetURLsUpdater, register_updater


@register_updater
class ThoriumUpdater(GitHubReleaseAssetURLsUpdater):
    """Track the collaborator releases endorsed by Thorium-MacOS upstream."""

    name = "thorium"
    # Alex313031/Thorium-MacOS currently publishes announcements pointing here.
    GITHUB_OWNER = "gz83"
    GITHUB_REPO = "thorium"
    TAG_PREFIX = "M"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "ARM64",
        "x86_64-darwin": "x64",
    }
    ASSET_NAME_TEMPLATE: ClassVar[str] = "Thorium_MacOS_{platform_value}.dmg"
