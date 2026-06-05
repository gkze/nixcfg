"""Updater for the pinned Mole helper binary archives."""

from __future__ import annotations

from lib.update.updaters.base import pinned_source_download_updater

MoleAppUpdater = pinned_source_download_updater(
    "mole-app",
    platforms={
        "aarch64-darwin": "darwin-arm64",
        "x86_64-darwin": "darwin-amd64",
    },
    download_url=(
        "https://github.com/tw93/Mole/releases/download/"
        "V{version}/binaries-{platform_value}.tar.gz"
    ),
    module=__name__,
)
