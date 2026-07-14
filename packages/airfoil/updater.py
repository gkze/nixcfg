"""Updater for Airfoil macOS releases."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters import SparkleAppcastUpdater, register_updater

DOWNLOAD_URL = "https://cdn.rogueamoeba.com/airfoil/mac/download/Airfoil.zip"


@register_updater
class AirfoilUpdater(SparkleAppcastUpdater):
    """Resolve Airfoil versions from Rogue Amoeba's Sparkle feed."""

    name = "airfoil"
    APPCAST_URL = (
        "https://rogueamoeba.net/ping/versionCheck.cgi?"
        "format=sparkle&system=999&bundleid=com.rogueamoeba.airfoil"
        "&platform=osx&version=51268000"
    )
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": DOWNLOAD_URL,
        "x86_64-darwin": DOWNLOAD_URL,
    }
