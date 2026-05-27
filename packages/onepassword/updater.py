"""Updater for 1Password."""

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
class OnePasswordUpdater(DownloadUrlMetadataUpdater):
    """Resolve 1Password from AgileBits' update check endpoint."""

    name = "onepassword"
    materialize_when_current = True
    UPDATE_URL = "https://app-updates.agilebits.com/check/2/99/aarch64/OPM8/en/0/A1/N"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "aarch64",
    }
    URL_METADATA_CONTEXT = "1Password metadata"

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch the latest 1Password version and ZIP URL."""
        payload = await fetch_json(
            session,
            self.UPDATE_URL,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        data = json_utils.as_object_dict(payload, context=self.UPDATE_URL)
        version = json_utils.get_required_str(data, "version", context=self.UPDATE_URL)
        sources = json_utils.as_object_list(
            data.get("sources"),
            context=f"{self.UPDATE_URL} sources",
        )
        if not sources:
            msg = f"No 1Password sources found in {self.UPDATE_URL}"
            raise RuntimeError(msg)
        source = json_utils.as_object_dict(
            sources[0],
            context=f"{self.UPDATE_URL} source",
        )
        url = json_utils.get_required_str(
            source,
            "url",
            context=f"{self.UPDATE_URL} source",
        )
        return VersionInfo(version=version, metadata=DownloadUrlMetadata(url=url))
