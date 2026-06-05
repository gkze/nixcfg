"""Updater for scie-pants preview launcher binaries."""

from __future__ import annotations

from lib.update.updaters.base import github_release_asset_urls_updater

PantsPreviewUpdater = github_release_asset_urls_updater(
    "pants-preview",
    github_owner="pantsbuild",
    github_repo="scie-pants",
    platforms={
        "aarch64-darwin": "macos-aarch64",
        "aarch64-linux": "linux-aarch64",
        "x86_64-linux": "linux-x86_64",
    },
    asset_name="scie-pants-{platform_value}",
    module=__name__,
)
