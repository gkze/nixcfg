"""Updater for Melty Conductor release artifacts."""

from __future__ import annotations

import re
from email.message import Message
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    import aiohttp

from lib.update.net import fetch_headers
from lib.update.updaters.base import DownloadHashUpdater, VersionInfo, register_updater
from lib.update.updaters.metadata import NO_METADATA


@register_updater
class ConductorUpdater(DownloadHashUpdater):
    """Resolve latest Conductor version from artifact headers."""

    name = "conductor"
    BASE_URL = "https://cdn.crabnebula.app/download/melty/conductor/latest/platform"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "dmg-aarch64",
        "x86_64-darwin": "dmg-x86_64",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Infer the current version from the download filename."""
        url = f"{self.BASE_URL}/dmg-aarch64"
        headers = await fetch_headers(
            session,
            url,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        header = headers.get("Content-Disposition", "")
        msg = Message()
        msg["Content-Disposition"] = header
        filename = msg.get_filename() or ""
        match = re.search(r"Conductor_([0-9.]+)_", filename)
        if not match:
            err = "Could not parse version from Content-Disposition filename"
            raise RuntimeError(err)
        return VersionInfo(version=match.group(1), metadata=NO_METADATA)
