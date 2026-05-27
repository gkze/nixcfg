"""Updater for Melty Conductor release artifacts."""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from email.message import Message
from typing import TYPE_CHECKING, ClassVar

import aiohttp

if TYPE_CHECKING:
    from lib.nix.models.sources import SourceEntry
    from lib.update.updaters.base import UpdateContext

from lib.update.updaters.base import (
    AssetURLsMetadataUpdater,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import AssetURLsMetadata

HTTP_BAD_REQUEST = 400


@dataclass(frozen=True, slots=True)
class _ResolvedArtifact:
    version: str
    url: str


@register_updater
class ConductorUpdater(AssetURLsMetadataUpdater):
    """Resolve latest Conductor artifacts to immutable CDN asset URLs."""

    name = "conductor"
    BASE_URL = "https://cdn.crabnebula.app/download/melty/conductor/latest/platform"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "dmg-aarch64",
        "x86_64-darwin": "dmg-x86_64",
    }

    @staticmethod
    def _version_from_header(header: str) -> str:
        msg = Message()
        msg["Content-Disposition"] = header
        filename = msg.get_filename() or ""
        match = re.search(r"Conductor_([0-9.]+)_", filename)
        if not match:
            err = "Could not parse version from Content-Disposition filename"
            raise RuntimeError(err)
        return match.group(1)

    @staticmethod
    def _url_without_query(url: str) -> str:
        parsed = urllib.parse.urlsplit(url)
        return urllib.parse.urlunsplit((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            "",
            "",
        ))

    async def _fetch_resolved_artifact(
        self,
        session: aiohttp.ClientSession,
        platform: str,
    ) -> _ResolvedArtifact:
        discovery_url = f"{self.BASE_URL}/{self.PLATFORMS[platform]}"
        timeout = aiohttp.ClientTimeout(total=self.config.default_timeout)
        async with session.head(
            discovery_url,
            allow_redirects=True,
            timeout=timeout,
        ) as response:
            if response.status >= HTTP_BAD_REQUEST:
                msg = (
                    f"Conductor metadata request for {platform} failed with "
                    f"HTTP {response.status}"
                )
                raise RuntimeError(msg)
            version = self._version_from_header(
                response.headers.get("Content-Disposition", "")
            )
            return _ResolvedArtifact(
                version=version,
                url=self._url_without_query(str(response.url)),
            )

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Infer the current version and resolved asset URLs from redirects."""
        artifacts = {
            platform: await self._fetch_resolved_artifact(session, platform)
            for platform in self.PLATFORMS
        }
        versions = {artifact.version for artifact in artifacts.values()}
        if len(versions) != 1:
            version_map = {
                platform: artifact.version for platform, artifact in artifacts.items()
            }
            msg = f"Conductor release metadata returned mismatched versions: {version_map}"
            raise RuntimeError(msg)
        return VersionInfo(
            version=versions.pop(),
            metadata=AssetURLsMetadata({
                platform: artifact.url for platform, artifact in artifacts.items()
            }),
        )

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        """Recompute hashes for mutable latest-channel artifacts before comparing."""
        _ = (context, info)
        return False
