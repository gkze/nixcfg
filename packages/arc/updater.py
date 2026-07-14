"""Updater for Arc macOS releases."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar

from lib.update.updaters import SparkleAppcastUrlUpdater, register_updater
from lib.update.updaters.vendor_feeds import require_version

if TYPE_CHECKING:
    from lib.update.updaters import SparkleAppcastItem


@register_updater
class ArcUpdater(SparkleAppcastUrlUpdater):
    """Resolve Arc versions and download URLs from its Sparkle feed."""

    name = "arc"
    APPCAST_URL = "https://releases.arc.net/updates.xml"
    URL_METADATA_CONTEXT: ClassVar[str | None] = "Arc metadata"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin",
        "x86_64-darwin": "darwin",
    }

    def item_version(self, item: SparkleAppcastItem) -> str | None:
        """Parse the numeric Arc version from the newest appcast item."""
        raw_version = require_version(
            item.short_version or item.version, context=self.APPCAST_URL
        )
        version_match = re.match(r"([0-9]+(?:\.[0-9]+)+)", raw_version)
        if version_match is None:
            msg = f"Could not parse Arc version from {raw_version!r}"
            raise RuntimeError(msg)
        return version_match.group(1)
