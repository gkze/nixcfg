"""Updater for Linear CLI's native release archives."""

from typing import ClassVar

from lib.update.updaters import GitHubReleaseAssetURLsUpdater, register_updater


@register_updater
class LinearCliUpdater(GitHubReleaseAssetURLsUpdater):
    """Track the stable native CLI for every previously supported platform."""

    name = "linear-cli"
    GITHUB_OWNER = "schpet"
    GITHUB_REPO = "linear-cli"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "aarch64-apple-darwin",
        "aarch64-linux": "aarch64-unknown-linux-gnu",
        "x86_64-darwin": "x86_64-apple-darwin",
        "x86_64-linux": "x86_64-unknown-linux-gnu",
    }
    ASSET_NAME_TEMPLATE = "linear-{platform_value}.tar.xz"
