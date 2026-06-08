"""Updater for the pinned Cogito macOS app archive."""

from __future__ import annotations

from lib.update.updaters.base import pinned_source_download_updater

CogitoUpdater = pinned_source_download_updater(
    "cogito",
    platforms={"aarch64-darwin": "arm64"},
    download_url="https://downloads.cogito.md/releases/{version}/1/Cogito-{version}-1-mac.zip",
    module=__name__,
)
