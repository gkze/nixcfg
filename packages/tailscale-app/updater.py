"""Updater for Tailscale app."""

from typing import ClassVar

from lib.update.updaters import SparkleAppcastUpdater, register_updater


@register_updater
class TailscaleAppUpdater(SparkleAppcastUpdater):
    """Keep the package aligned with the installed app's unstable channel."""

    name = "tailscale-app"
    APPCAST_URL = "https://pkgs.tailscale.com/unstable/appcast.xml"
    VERSION_FIELD = "short_or_version"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin",
    }
    DOWNLOAD_URL_TEMPLATE = (
        "https://pkgs.tailscale.com/unstable/Tailscale-{version}-macos.pkg"
    )
