"""Track BetterDisplay's official stable macOS release."""

from typing import ClassVar

from lib.update.updaters import GitHubReleaseAssetURLsUpdater, register_updater


@register_updater
class BetterDisplayUpdater(GitHubReleaseAssetURLsUpdater):
    """Resolve the vendor's universal DMG without advancing root nixpkgs."""

    name = "betterdisplay"
    GITHUB_OWNER = "waydabber"
    GITHUB_REPO = "BetterDisplay"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin",
        "x86_64-darwin": "darwin",
    }
    ASSET_NAME_TEMPLATE = "BetterDisplay-v{version}.dmg"
