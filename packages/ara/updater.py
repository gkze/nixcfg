"""Resolve Reason (formerly Ara) through its official desktop update feed."""

from typing import TYPE_CHECKING, ClassVar

from lib import json_utils
from lib.update.net import fetch_json
from lib.update.updaters import DownloadHashUpdater, VersionInfo, register_updater

if TYPE_CHECKING:
    import aiohttp

    from lib.update.updaters import UpdateContext


@register_updater
class AraUpdater(DownloadHashUpdater):
    """Keep the existing package identity while following the vendor rebrand."""

    name = "ara"
    FEED_URL = (
        "https://reasonmachines.com/api/desktop-update"
        "?target=darwin&arch=aarch64&current_version=0.0.0&channel=stable"
    )
    # The vendor only exposes signed release URLs. Persist its stable redirect
    # instead; Nix pins the bytes, and the package verifies their bundle version.
    # Rebuilding older releases after a vendor update requires the binary cache.
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "https://reasonmachines.com/api/desktop-download?arch=aarch64"
    }

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Read the release version without persisting expiring signed URLs."""
        _ = context
        payload = json_utils.as_object_dict(
            await fetch_json(session, self.FEED_URL, config=self.config),
            context="Reason desktop feed",
        )
        return VersionInfo(
            version=json_utils.get_required_str(
                payload, "name", context="Reason desktop feed"
            )
        )
