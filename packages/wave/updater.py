"""Updater for Wave."""

from __future__ import annotations

from lib.update.updaters.base import electron_builder_asset_urls_updater

WaveUpdater = electron_builder_asset_urls_updater(
    "wave",
    feed_url="https://dl.waveterm.dev/releases-w2/latest-mac.yml",
    platforms={
        "aarch64-darwin": "arm64",
    },
    selectors={
        "aarch64-darwin": lambda version, url: url.endswith(
            f"Wave-darwin-arm64-{version}.dmg"
        ),
    },
    fallback_url=(
        "https://dl.waveterm.dev/releases-w2/Wave-darwin-{platform_value}-{version}.dmg"
    ),
    module=__name__,
)
