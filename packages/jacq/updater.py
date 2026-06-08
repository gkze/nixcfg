"""Updater for the pinned Jacq macOS app archive."""

from __future__ import annotations

from lib.update.updaters.base import pinned_source_download_updater

JacqUpdater = pinned_source_download_updater(
    "jacq",
    platforms={"aarch64-darwin": "arm64"},
    download_url="https://downloads.jacquard.dev/releases/{version}/Jacq-darwin-arm64-{version}.zip",
    module=__name__,
)
