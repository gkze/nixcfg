"""Updater for CodeEdit macOS DMG releases."""

from __future__ import annotations

from lib.update.updaters.base import github_release_asset_urls_updater

CodeEditUpdater = github_release_asset_urls_updater(
    "codeedit",
    github_owner="CodeEditApp",
    github_repo="CodeEdit",
    platforms={
        "aarch64-darwin": "darwin",
        "x86_64-darwin": "darwin",
    },
    asset_name="CodeEdit.dmg",
    module=__name__,
)
