"""Updater for Pica's signed macOS releases."""

from typing import TYPE_CHECKING, ClassVar

from lib.update.updaters import SparkleAppcastUrlUpdater, register_updater
from lib.update.updaters.vendor_feeds import require_version

if TYPE_CHECKING:
    from lib.update.updaters import SparkleAppcastItem


@register_updater
class PicaUpdater(SparkleAppcastUrlUpdater):
    """Track Pica's appcast while preserving its version-plus-build convention."""

    name = "pica"
    APPCAST_URL = "https://pica.joshpuckett.me/appcast.xml"
    PLATFORMS: ClassVar[dict[str, str]] = {"aarch64-darwin": "arm64"}

    def item_version(self, item: SparkleAppcastItem) -> str:
        """Combine both source-owned version fields so build-only updates are seen."""
        short_version = require_version(item.short_version, context=self.APPCAST_URL)
        build_version = require_version(item.version, context=self.APPCAST_URL)
        return f"{short_version}.{build_version}"
