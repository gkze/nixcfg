"""Updater for Comet browser macOS releases."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from lib import json_utils
from lib.update.net import fetch_json
from lib.update.updaters.base import DownloadHashUpdater, VersionInfo, register_updater

if TYPE_CHECKING:
    import aiohttp


@register_updater
class CometUpdater(DownloadHashUpdater):
    """Resolve Comet's version from Perplexity's browser update API."""

    name = "comet"
    UPDATE_URL = (
        "https://www.perplexity.ai/rest/browser/update?"
        "browser=1.1.1.1&channel=stable&machine=0&platform=mac_arm64"
    )
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "https://www.perplexity.ai/rest/browser/download?channel=stable&platform=mac_arm64",
        "x86_64-darwin": "https://www.perplexity.ai/rest/browser/download?channel=stable&platform=mac_x64",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch the latest Comet version from the vendor update API."""
        payload = await fetch_json(
            session,
            self.UPDATE_URL,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        data = json_utils.as_object_dict(payload, context=self.UPDATE_URL)
        body = json_utils.as_object_dict(
            data.get("body"),
            context=f"{self.UPDATE_URL} body",
        )
        version = json_utils.get_required_str(
            body,
            "browser_version",
            context=self.UPDATE_URL,
        ).strip()
        if not version:
            msg = f"Missing Comet browser_version in {self.UPDATE_URL}"
            raise RuntimeError(msg)
        return VersionInfo(version=version)
