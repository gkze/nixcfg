"""Updater for Loom desktop macOS releases."""

from __future__ import annotations

from lib.update.updaters.base import electron_builder_asset_urls_updater

LoomUpdater = electron_builder_asset_urls_updater(
    "loom",
    feed_url="https://packages.loom.com/desktop-packages/latest-mac.yml",
    platforms={
        "aarch64-darwin": "arm64",
        "x86_64-darwin": "x64",
    },
    selectors={
        "aarch64-darwin": lambda _version, url: url.endswith("-arm64.dmg"),
        "x86_64-darwin": lambda version, url: url.endswith(f"Loom-{version}.dmg"),
    },
    fallback_url=lambda version, _platform, platform_value: (
        "https://packages.loom.com/desktop-packages/"
        f"Loom-{version}{'-arm64' if platform_value == 'arm64' else ''}.dmg"
    ),
    module=__name__,
)
