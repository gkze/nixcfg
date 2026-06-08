"""Updater for the pinned Pica macOS app archive."""

from __future__ import annotations

from lib.update.updaters.base import pinned_source_download_updater

PicaUpdater = pinned_source_download_updater(
    "pica",
    platforms={"aarch64-darwin": "arm64"},
    download_url=(
        "https://f6n9fvfeuhzxxji6.public.blob.vercel-storage.com/Pica-{version}.dmg"
    ),
    module=__name__,
)
