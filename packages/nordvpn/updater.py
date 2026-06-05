"""Updater for NordVPN."""

from __future__ import annotations

from lib.update.updaters.base import sparkle_appcast_updater

NordvpnUpdater = sparkle_appcast_updater(
    "nordvpn",
    appcast_url=(
        "https://downloads.nordcdn.com/apps/macos/generic/NordVPN-OpenVPN/"
        "latest/update_pkg.xml"
    ),
    platforms={
        "aarch64-darwin": "darwin",
    },
    download_url=(
        "https://downloads.nordcdn.com/apps/macos/generic/NordVPN-OpenVPN/"
        "{version}/NordVPN.pkg"
    ),
    version_field="short_or_version",
    module=__name__,
)
