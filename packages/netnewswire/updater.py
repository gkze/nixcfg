"""Updater for NetNewsWire macOS Sparkle releases."""

from __future__ import annotations

from lib.update.updaters.base import sparkle_appcast_updater

NetNewsWireUpdater = sparkle_appcast_updater(
    "netnewswire",
    appcast_url="https://ranchero.com/downloads/netnewswire-release.xml",
    platforms={
        "aarch64-darwin": "darwin",
        "x86_64-darwin": "darwin",
    },
    version_field="short_version",
    appcast_url_metadata=True,
    url_metadata_context="NetNewsWire metadata",
    module=__name__,
)
