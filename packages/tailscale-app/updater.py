"""Updater for Tailscale app."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters import SparkleAppcastUpdater, register_updater


@register_updater
class TailscaleAppUpdater(SparkleAppcastUpdater):
    """Resolve Tailscale versions from its Sparkle feed and versioned pkg URL."""

    name = "tailscale-app"
    APPCAST_URL = "https://pkgs.tailscale.com/stable/appcast.xml"
    VERSION_FIELD = "short_or_version"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin",
    }
    DOWNLOAD_URL_TEMPLATE = (
        "https://pkgs.tailscale.com/stable/Tailscale-{version}-macos.pkg"
    )
