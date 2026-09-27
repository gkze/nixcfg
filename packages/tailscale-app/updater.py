"""Updater for Tailscale app."""

from typing import ClassVar

from lib.update.updaters import SparkleAppcastUpdater, register_updater


@register_updater
class TailscaleAppUpdater(SparkleAppcastUpdater):
    """Resolve Tailscale versions from its Sparkle feed and versioned pkg URL.

    The standalone macOS app self-updates on Tailscale's unstable track
    (stable lags at 1.102.x while auto-updates ship 1.103.x), so follow the
    unstable appcast to keep preventDowngrade from fighting the app's own
    updater.
    """

    name = "tailscale-app"
    APPCAST_URL = "https://pkgs.tailscale.com/unstable/appcast.xml"
    VERSION_FIELD = "short_or_version"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin",
    }
    DOWNLOAD_URL_TEMPLATE = (
        "https://pkgs.tailscale.com/unstable/Tailscale-{version}-macos.pkg"
    )
