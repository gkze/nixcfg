"""Updater for Claude desktop macOS releases."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from lib import json_utils
from lib.update.net import fetch_json
from lib.update.updaters.base import (
    DownloadUrlMetadataUpdater,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import DownloadUrlMetadata

if TYPE_CHECKING:
    import aiohttp


@register_updater
class ClaudeUpdater(DownloadUrlMetadataUpdater):
    """Resolve Claude's versioned universal ZIP URL from Anthropic's feed."""

    name = "claude"
    RELEASES_URL = "https://downloads.claude.ai/releases/darwin/universal/RELEASES.json"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin",
        "x86_64-darwin": "darwin",
    }
    URL_METADATA_CONTEXT = "Claude metadata"

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch the latest Claude version and immutable download URL."""
        payload = await fetch_json(
            session,
            self.RELEASES_URL,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        data = json_utils.as_object_dict(payload, context=self.RELEASES_URL)
        releases = json_utils.as_object_list(
            data.get("releases"),
            context=f"{self.RELEASES_URL} releases",
        )
        if not releases:
            msg = f"No Claude releases found in {self.RELEASES_URL}"
            raise RuntimeError(msg)
        latest = json_utils.as_object_dict(
            releases[0],
            context=f"{self.RELEASES_URL} release",
        )
        update_to = json_utils.as_object_dict(
            latest.get("updateTo"),
            context=f"{self.RELEASES_URL} updateTo",
        )
        version = json_utils.get_required_str(
            update_to,
            "version",
            context=f"{self.RELEASES_URL} updateTo",
        )
        url = json_utils.get_required_str(
            update_to,
            "url",
            context=f"{self.RELEASES_URL} updateTo",
        )
        return VersionInfo(version=version, metadata=DownloadUrlMetadata(url=url))
