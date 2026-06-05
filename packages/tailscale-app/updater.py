"""Updater for Tailscale app."""

from __future__ import annotations

from lib.update.updaters.base import sparkle_appcast_updater

TailscaleAppUpdater = sparkle_appcast_updater(
    "tailscale-app",
    appcast_url="https://pkgs.tailscale.com/stable/appcast.xml",
    platforms={
        "aarch64-darwin": "darwin",
    },
    download_url="https://pkgs.tailscale.com/stable/Tailscale-{version}-macos.pkg",
    version_field="short_or_version",
    module=__name__,
)
