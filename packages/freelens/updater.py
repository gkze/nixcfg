"""Updater for Freelens."""

from __future__ import annotations

from lib.update.updaters.base import github_release_asset_urls_updater

FreelensUpdater = github_release_asset_urls_updater(
    "freelens",
    github_owner="freelensapp",
    github_repo="freelens",
    platforms={
        "aarch64-darwin": "arm64",
    },
    asset_name="Freelens-{version}-macos-{platform_value}.dmg",
    module=__name__,
)
