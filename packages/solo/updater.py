"""Updater for Solo's fully published macOS releases."""

import re
from typing import TYPE_CHECKING, ClassVar

from lib import json_utils
from lib.update.net import fetch_json
from lib.update.updaters import (
    DownloadUrlMetadataUpdater,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import DownloadUrlMetadata

if TYPE_CHECKING:
    import aiohttp

    from lib.update.updaters import UpdateContext


@register_updater
class SoloUpdater(DownloadUrlMetadataUpdater):
    """Follow the publisher's manifest used by its official release packaging."""

    name = "solo"
    MANIFEST_URL = (
        "https://soloterm.com/api/v1/updates/manifest?platform=darwin-universal"
    )
    PLATFORMS: ClassVar[dict[str, str]] = {"aarch64-darwin": "universal"}
    DOWNLOAD_URL_TEMPLATE = (
        "https://releases.soloterm.com/darwin/{platform_value}/"
        "Solo_{version}_{platform_value}.app.tar.gz"
    )

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Require the fully published native archive for a valid release version."""
        _ = context
        payload = await fetch_json(
            session,
            self.MANIFEST_URL,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        data = json_utils.as_object_dict(payload, context=self.MANIFEST_URL)
        version = json_utils.get_required_str(
            data, "version", context=self.MANIFEST_URL
        )
        url = json_utils.get_required_str(data, "url", context=self.MANIFEST_URL)
        if (
            re.fullmatch(
                r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", version
            )
            is None
        ):
            msg = "Solo release manifest has an invalid version"
            raise ValueError(msg)
        expected = self.DOWNLOAD_URL_TEMPLATE.format(
            version=version, platform_value="universal"
        )
        if url != expected:
            msg = "Solo release manifest has an unexpected native archive URL"
            raise ValueError(msg)
        return VersionInfo(version=version, metadata=DownloadUrlMetadata(url=url))
