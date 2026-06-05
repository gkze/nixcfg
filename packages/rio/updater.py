"""Updater for Rio."""

from __future__ import annotations

from lib.update.updaters.base import github_release_asset_urls_updater

RioUpdater = github_release_asset_urls_updater(
    "rio",
    github_owner="raphamorim",
    github_repo="rio",
    platforms={
        "aarch64-darwin": "darwin",
    },
    asset_name="rio.dmg",
    module=__name__,
)
