"""Updater for KeepingYouAwake macOS ZIP releases."""

from __future__ import annotations

from lib.update.updaters.base import github_release_asset_urls_updater

KeepingYouAwakeUpdater = github_release_asset_urls_updater(
    "keepingyouawake",
    github_owner="newmarcel",
    github_repo="KeepingYouAwake",
    tag_prefix="",
    platforms={
        "aarch64-darwin": "darwin",
        "x86_64-darwin": "darwin",
    },
    asset_name="KeepingYouAwake-{version}.zip",
    module=__name__,
)
