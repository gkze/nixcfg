"""Updater for macFUSE."""

from __future__ import annotations

import plistlib
from typing import TYPE_CHECKING, ClassVar

from lib import json_utils
from lib.update.net import fetch_url
from lib.update.updaters.base import (
    DownloadUrlMetadataUpdater,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import DownloadUrlMetadata

if TYPE_CHECKING:
    import aiohttp


@register_updater
class MacfuseUpdater(DownloadUrlMetadataUpdater):
    """Resolve macFUSE from the upstream release plist."""

    name = "macfuse"
    RELEASE_PLIST_URL = "https://macfuse.github.io/releases/CurrentRelease.plist"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin",
    }
    URL_METADATA_CONTEXT = "macFUSE metadata"

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch the latest macFUSE version and DMG URL."""
        payload = await fetch_url(
            session,
            self.RELEASE_PLIST_URL,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        data = json_utils.as_object_dict(
            plistlib.loads(payload),
            context=self.RELEASE_PLIST_URL,
        )
        rules = json_utils.as_object_list(
            data.get("Rules"),
            context=f"{self.RELEASE_PLIST_URL} Rules",
        )
        if not rules:
            msg = f"No macFUSE release rules found in {self.RELEASE_PLIST_URL}"
            raise RuntimeError(msg)
        rule = json_utils.as_object_dict(
            rules[0],
            context=f"{self.RELEASE_PLIST_URL} rule",
        )
        version = json_utils.get_required_str(
            rule,
            "Version",
            context=f"{self.RELEASE_PLIST_URL} rule",
        )
        url = json_utils.get_required_str(
            rule,
            "Codebase",
            context=f"{self.RELEASE_PLIST_URL} rule",
        )
        return VersionInfo(version=version, metadata=DownloadUrlMetadata(url=url))
