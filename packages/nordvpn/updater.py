"""Updater for NordVPN."""

from typing import ClassVar

from lib.update.updaters import SparkleAppcastUpdater, register_updater


@register_updater
class NordvpnUpdater(SparkleAppcastUpdater):
    """Resolve NordVPN versions from its Sparkle feed and versioned pkg URL."""

    name = "nordvpn"
    APPCAST_URL = (
        "https://downloads.nordcdn.com/apps/macos/generic/NordVPN-OpenVPN/"
        "latest/update_pkg.xml"
    )
    VERSION_FIELD = "short_or_version"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin",
    }
    DOWNLOAD_URL_TEMPLATE = (
        "https://downloads.nordcdn.com/apps/macos/generic/NordVPN-OpenVPN/"
        "{version}/NordVPN.pkg"
    )
