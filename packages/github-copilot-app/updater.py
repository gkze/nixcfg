"""Updater for official GitHub Copilot desktop releases."""

from typing import ClassVar

from lib.update.derivation_validation import DerivationValidation
from lib.update.updaters import GitHubReleaseAssetURLsUpdater, register_updater


@register_updater
class GitHubCopilotAppUpdater(GitHubReleaseAssetURLsUpdater):
    """Track GitHub's signed arm64 desktop DMG."""

    name = "github-copilot-app"
    GITHUB_OWNER = "github"
    GITHUB_REPO = "app"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
    }
    ASSET_NAME_TEMPLATE: ClassVar[str] = "GitHub-Copilot-darwin-{platform_value}.dmg"
    derivation_validations = (
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            systems=("aarch64-darwin",),
            mode="build",
        ),
    )
