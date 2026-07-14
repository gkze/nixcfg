"""Updater for the Town Assistant nightly macOS appcast."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from lib.update.updaters import SparkleAppcastUrlUpdater, register_updater
from lib.update.updaters.vendor_feeds import require_version

if TYPE_CHECKING:
    from lib.update.updaters import SparkleAppcastItem


@register_updater
class TownAssistantNightlyUpdater(SparkleAppcastUrlUpdater):
    """Resolve Town Assistant nightly builds from its Sparkle feed."""

    name = "town-assistant-nightly"
    APPCAST_URL = (
        "https://town-macos-app.s3.us-east-1.amazonaws.com/desktop/nightly/appcast.xml"
    )
    URL_METADATA_CONTEXT: ClassVar[str | None] = "Town Assistant nightly metadata"
    PLATFORMS: ClassVar[dict[str, str]] = {"aarch64-darwin": "darwin-aarch64"}

    def item_version(self, item: SparkleAppcastItem) -> str | None:
        """Combine the short version and build number into a version token."""
        short_version = require_version(item.short_version, context=self.APPCAST_URL)
        build_version = require_version(item.version, context=self.APPCAST_URL)
        return f"{short_version}-{build_version}"
