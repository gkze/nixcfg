"""Updater for Ghostty tip."""

import re
from typing import TYPE_CHECKING, ClassVar

from lib.update.updaters import SparkleAppcastUrlUpdater, VersionInfo, register_updater
from lib.update.updaters.vendor_feeds import (
    fetch_sparkle_appcast_items,
    require_url,
    require_version,
)

if TYPE_CHECKING:
    import aiohttp

    from lib.update.updaters import SparkleAppcastItem, UpdateContext


@register_updater
class GhosttyTipUpdater(SparkleAppcastUrlUpdater):
    """Resolve Ghostty tip builds from the nightly Sparkle feed."""

    name = "ghostty-tip"
    APPCAST_URL = "https://tip.files.ghostty.org/appcast.xml"
    URL_METADATA_CONTEXT: ClassVar[str | None] = "Ghostty tip metadata"
    PLATFORMS: ClassVar[dict[str, str]] = {"aarch64-darwin": "darwin"}

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Select the greatest build; Ghostty publishes its feed oldest first."""
        _ = context
        items = await fetch_sparkle_appcast_items(
            session, self.APPCAST_URL, config=self.config
        )

        def build_number(item: SparkleAppcastItem) -> int:
            build = require_version(item.version, context=self.APPCAST_URL)
            if re.fullmatch(r"[0-9]+", build) is None:
                msg = f"Invalid Ghostty tip build number: {build}"
                raise RuntimeError(msg)
            return int(build)

        item = max(items, key=build_number)
        return VersionInfo(
            version=require_version(self.item_version(item), context=self.APPCAST_URL),
            metadata=self.item_metadata(item),
        )

    def item_version(self, item: SparkleAppcastItem) -> str | None:
        """Combine the build number and commit hash into a version token."""
        build = require_version(item.version, context=self.APPCAST_URL)
        url = require_url(item.url, context=self.APPCAST_URL)
        match = re.search(r"/([0-9a-f]{40})/Ghostty\.dmg$", url)
        if match is None:
            msg = f"Could not parse Ghostty tip commit from URL: {url}"
            raise RuntimeError(msg)
        return f"{build}-{match.group(1)}"
