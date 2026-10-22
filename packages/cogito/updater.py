"""Updater for the Cogito signed macOS releases."""

from typing import ClassVar

from lib.update.updaters import SparkleAppcastUrlUpdater, register_updater


@register_updater
class CogitoUpdater(SparkleAppcastUrlUpdater):
    """Follow the installed app's release feed."""

    name = "cogito"
    PLATFORMS: ClassVar[dict[str, str]] = {"aarch64-darwin": "arm64"}
    APPCAST_URL = "https://cogito.md/updates/appcast.xml"
    VERSION_FIELD = "short_version"
