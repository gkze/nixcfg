"""Track official standalone uv and uvx release archives."""

from typing import ClassVar

from lib.update.updaters import GitHubReleaseAssetURLsUpdater, register_updater


@register_updater
class UvCliUpdater(GitHubReleaseAssetURLsUpdater):
    """Keep all supported native archives on the same stable release."""

    name = "uv-cli"
    GITHUB_OWNER = "astral-sh"
    GITHUB_REPO = "uv"
    TAG_PREFIX = ""
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "aarch64-apple-darwin",
        "x86_64-darwin": "x86_64-apple-darwin",
        "aarch64-linux": "aarch64-unknown-linux-musl",
        "x86_64-linux": "x86_64-unknown-linux-musl",
    }
    ASSET_NAME_TEMPLATE = "uv-{platform_value}.tar.gz"
