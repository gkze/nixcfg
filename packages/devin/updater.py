"""Updater for the Devin Desktop macOS release feed.

Devin Desktop is the renamed Windsurf/VS Code fork, so it exposes the same
vendor update API. The API path selects the architecture, and the payload
carries both the artifact URL and the app version. ``version`` in that payload
is the VS Code commit, and ``productVersion`` is the upstream VS Code base, so
neither identifies the app release; ``windsurfVersion`` is the version the app
reports in its own bundle metadata.
"""

from typing import TYPE_CHECKING, ClassVar

from lib import json_utils
from lib.update.net import fetch_json
from lib.update.updaters import (
    DownloadUrlMetadataUpdater,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import DownloadUrlMetadata
from lib.update.updaters.vendor_feeds import require_url, require_version

if TYPE_CHECKING:
    import aiohttp

    from lib.update.updaters import UpdateContext


@register_updater
class DevinUpdater(DownloadUrlMetadataUpdater):
    """Track the arm64 Devin Desktop artifact the vendor API serves."""

    name = "devin"
    RELEASE_URL: ClassVar[str] = (
        "https://windsurf-stable.codeium.com/api/update/darwin-arm64/stable/latest"
    )
    URL_METADATA_CONTEXT: ClassVar[str | None] = "Devin Desktop release metadata"
    PLATFORMS: ClassVar[dict[str, str]] = {"aarch64-darwin": "arm64"}

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Read the vendor payload for both the app version and artifact URL."""
        _ = context
        payload = await fetch_json(
            session,
            self.RELEASE_URL,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        release = json_utils.as_object_dict(payload, context=self.RELEASE_URL)
        version = require_version(
            json_utils.get_required_str(
                release,
                "windsurfVersion",
                context=self.RELEASE_URL,
            ),
            context=self.RELEASE_URL,
        )
        url = require_url(
            json_utils.get_required_str(release, "url", context=self.RELEASE_URL),
            context=self.RELEASE_URL,
        )
        return VersionInfo(version=version, metadata=DownloadUrlMetadata(url=url))
