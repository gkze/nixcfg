"""Updater for Airfoil macOS releases."""

from __future__ import annotations

from lib.update.updaters.base import sparkle_appcast_updater

DOWNLOAD_URL = "https://cdn.rogueamoeba.com/airfoil/mac/download/Airfoil.zip"

AirfoilUpdater = sparkle_appcast_updater(
    "airfoil",
    appcast_url=(
        "https://rogueamoeba.net/ping/versionCheck.cgi?"
        "format=sparkle&system=999&bundleid=com.rogueamoeba.airfoil"
        "&platform=osx&version=51268000"
    ),
    platforms={
        "aarch64-darwin": DOWNLOAD_URL,
        "x86_64-darwin": DOWNLOAD_URL,
    },
    module=__name__,
)
