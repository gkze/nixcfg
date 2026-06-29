"""Updater for macai macOS releases."""

from __future__ import annotations

from lib.update.updaters.base import sparkle_appcast_updater

MacAIUpdater = sparkle_appcast_updater(
    "macai",
    appcast_url="https://renset.dev/macai/appcast.xml",
    platforms={
        "aarch64-darwin": "darwin",
        "x86_64-darwin": "darwin",
    },
    version_field="short_or_version",
    appcast_url_metadata=True,
    url_metadata_context="macai metadata",
    module=__name__,
)
