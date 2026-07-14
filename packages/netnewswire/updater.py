"""Updater for NetNewsWire macOS Sparkle releases."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters import SparkleAppcastUrlUpdater, register_updater


@register_updater
class NetNewsWireUpdater(SparkleAppcastUrlUpdater):
    """Resolve NetNewsWire versions and download URLs from its Sparkle feed."""

    name = "netnewswire"
    APPCAST_URL = "https://ranchero.com/downloads/netnewswire-release.xml"
    VERSION_FIELD = "short_version"
    URL_METADATA_CONTEXT: ClassVar[str | None] = "NetNewsWire metadata"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin",
        "x86_64-darwin": "darwin",
    }
