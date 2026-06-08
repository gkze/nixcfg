"""Updater for the pinned Solo macOS app archive."""

from __future__ import annotations

from lib.update.updaters.base import pinned_source_download_updater

SoloUpdater = pinned_source_download_updater(
    "solo",
    platforms={"aarch64-darwin": "universal"},
    download_url=(
        "https://releases.soloterm.com/darwin/{platform_value}/"
        "Solo_{version}_{platform_value}.app.tar.gz"
    ),
    module=__name__,
)
