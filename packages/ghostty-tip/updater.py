"""Updater for Ghostty tip."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar

from lib.update.updaters import SparkleAppcastUrlUpdater, register_updater
from lib.update.updaters.vendor_feeds import require_url, require_version

if TYPE_CHECKING:
    from lib.update.updaters import SparkleAppcastItem


@register_updater
class GhosttyTipUpdater(SparkleAppcastUrlUpdater):
    """Resolve Ghostty tip builds from the nightly Sparkle feed."""

    name = "ghostty-tip"
    APPCAST_URL = "https://tip.files.ghostty.org/appcast.xml"
    URL_METADATA_CONTEXT: ClassVar[str | None] = "Ghostty tip metadata"
    PLATFORMS: ClassVar[dict[str, str]] = {"aarch64-darwin": "darwin"}

    def item_version(self, item: SparkleAppcastItem) -> str | None:
        """Combine the build number and commit hash into a version token."""
        build = require_version(item.version, context=self.APPCAST_URL)
        url = require_url(item.url, context=self.APPCAST_URL)
        match = re.search(r"/([0-9a-f]{40})/Ghostty\.dmg$", url)
        if match is None:
            msg = f"Could not parse Ghostty tip commit from URL: {url}"
            raise RuntimeError(msg)
        return f"{build}-{match.group(1)}"
