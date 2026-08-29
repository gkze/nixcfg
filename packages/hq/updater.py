"""Updater for official HQ universal macOS releases."""

from typing import ClassVar

from lib.update.derivation_validation import DerivationValidation
from lib.update.updaters import GitHubReleaseAssetURLsUpdater, register_updater


@register_updater
class HQUpdater(GitHubReleaseAssetURLsUpdater):
    """Track the exact universal HQ app archive from GitHub releases."""

    name = "hq"
    GITHUB_OWNER = "indigoai-us"
    GITHUB_REPO = "hq-desktop-app"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "universal",
    }
    ASSET_NAME_TEMPLATE: ClassVar[str] = "HQ_{version}_{platform_value}.app.tar.gz"
    derivation_validations = (
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            systems=("aarch64-darwin",),
            mode="build",
        ),
    )
