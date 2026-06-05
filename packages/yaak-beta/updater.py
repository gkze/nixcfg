"""Updater for Yaak beta."""

from __future__ import annotations

from lib.update.updaters.base import github_release_asset_urls_updater

YaakBetaUpdater = github_release_asset_urls_updater(
    "yaak-beta",
    github_owner="mountain-loop",
    github_repo="yaak",
    platforms={
        "aarch64-darwin": "aarch64",
    },
    asset_name="Yaak_{version}_{platform_value}.dmg",
    module=__name__,
)
