"""Updater for Codex desktop DMG releases."""

from __future__ import annotations

import base64
import binascii
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from collections.abc import Mapping

    import aiohttp

from lib.update.net import fetch_headers
from lib.update.updaters.base import DownloadHashUpdater, VersionInfo, register_updater
from lib.update.updaters.metadata import NO_METADATA


@register_updater
class CodexDesktopUpdater(DownloadHashUpdater):
    """Track the Codex DMG and derive a stable artifact revision."""

    name = "codex-desktop"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "https://persistent.oaistatic.com/codex-app-prod/Codex.dmg",
        "x86_64-darwin": "https://persistent.oaistatic.com/codex-app-prod/Codex.dmg",
    }

    @staticmethod
    def _version_from_headers(headers: Mapping[str, str]) -> str:
        """Derive a stable artifact revision from HTTP metadata."""
        content_md5 = headers.get("Content-MD5", "").strip()
        if content_md5:
            try:
                md5_hex = base64.b64decode(content_md5, validate=True).hex()
            except (ValueError, binascii.Error) as exc:
                msg = f"Invalid Content-MD5 header: {content_md5!r}"
                raise RuntimeError(msg) from exc
            return f"md5.{md5_hex}"

        etag = headers.get("ETag", "").strip().strip('"').lower()
        if etag:
            normalized = etag.removeprefix("0x")
            return f"etag.{normalized}"

        last_modified = headers.get("Last-Modified", "").strip()
        if last_modified:
            try:
                dt = parsedate_to_datetime(last_modified).astimezone(UTC)
            except (TypeError, ValueError) as exc:
                msg = f"Could not parse Last-Modified header: {last_modified!r}"
                raise RuntimeError(msg) from exc
            return f"modified.{dt.strftime('%Y%m%d%H%M%S')}"

        msg = "Missing Content-MD5/ETag/Last-Modified headers for Codex artifact"
        raise RuntimeError(msg)

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Use artifact metadata headers to produce a stable artifact revision."""
        probe_url = self.PLATFORMS["aarch64-darwin"]
        headers = await fetch_headers(
            session,
            probe_url,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )

        version = self._version_from_headers(headers)
        return VersionInfo(version=version, metadata=NO_METADATA)
