"""Updater for the Rex Beta snapshot appcast.

Rex publishes a Sparkle feed per architecture. Only the arm64 snapshot channel
exists today, and its items carry a date-based snapshot build alongside a
long-lived marketing version, so the version token has to combine both or the
updater would see 0.1.0 forever.
"""

from typing import TYPE_CHECKING, ClassVar

from lib.update.updaters import SparkleAppcastUrlUpdater, register_updater
from lib.update.updaters.vendor_feeds import require_version

if TYPE_CHECKING:
    from lib.update.updaters import SparkleAppcastItem


@register_updater
class RexBetaUpdater(SparkleAppcastUrlUpdater):
    """Resolve Rex snapshot builds from its arm64 Sparkle appcast."""

    name = "rex-beta"
    APPCAST_URL = (
        "https://blob.superlogical.com/public/rex-app/snapshot-arm64/appcast.xml"
    )
    URL_METADATA_CONTEXT: ClassVar[str | None] = "Rex Beta snapshot metadata"
    PLATFORMS: ClassVar[dict[str, str]] = {"aarch64-darwin": "arm64"}

    def item_version(self, item: SparkleAppcastItem) -> str | None:
        """Combine the marketing version and snapshot build into one token."""
        short_version = require_version(item.short_version, context=self.APPCAST_URL)
        build_version = require_version(item.version, context=self.APPCAST_URL)
        return f"{short_version}-{build_version}"
