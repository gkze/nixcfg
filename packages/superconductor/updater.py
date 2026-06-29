"""Updater for Superconductor nightly macOS app archives."""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, ClassVar

import aiohttp

from lib.update.updaters.base import (
    AssetURLsMetadataUpdater,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import AssetURLsMetadata

if TYPE_CHECKING:
    from lib.nix.models.sources import SourceEntry
    from lib.update.updaters.base import UpdateContext

HTTP_BAD_REQUEST = 400


@dataclass(frozen=True, slots=True)
class _ResolvedArtifact:
    version: str
    url: str


@register_updater
class SuperconductorUpdater(AssetURLsMetadataUpdater):
    """Resolve Superconductor's mutable nightly download endpoint."""

    name = "superconductor"
    DISCOVERY_URL = "https://super.engineering/api/download"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
    }

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

    @staticmethod
    def _version_from_url(url: str, last_modified: str | None) -> str:
        filename = urllib.parse.urlsplit(url).path.rsplit("/", maxsplit=1)[-1]
        match = re.fullmatch(r"Superconductor-nightly-([^-]+)-[^.]+\.dmg", filename)
        if not match:
            msg = f"Could not parse Superconductor nightly version from {url}"
            raise RuntimeError(msg)
        if not last_modified:
            msg = f"Missing Last-Modified header for Superconductor nightly {url}"
            raise RuntimeError(msg)
        date = parsedate_to_datetime(last_modified).date().isoformat()
        return f"{date}-{match.group(1)}"

    async def _fetch_resolved_artifact(
        self,
        session: aiohttp.ClientSession,
        platform: str,
    ) -> _ResolvedArtifact:
        _ = self.PLATFORMS[platform]
        timeout = aiohttp.ClientTimeout(total=self.config.default_timeout)
        async with session.head(
            self.DISCOVERY_URL,
            allow_redirects=True,
            timeout=timeout,
        ) as response:
            if response.status >= HTTP_BAD_REQUEST:
                msg = (
                    f"Superconductor metadata request for {platform} failed with "
                    f"HTTP {response.status}"
                )
                raise RuntimeError(msg)
            url = self._url_without_query(str(response.url))
            return _ResolvedArtifact(
                version=self._version_from_url(
                    url,
                    response.headers.get("Last-Modified"),
                ),
                url=url,
            )

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Infer the current nightly version and resolved asset URL."""
        artifacts = {
            platform: await self._fetch_resolved_artifact(session, platform)
            for platform in self.PLATFORMS
        }
        versions = {artifact.version for artifact in artifacts.values()}
        if len(versions) != 1:
            version_map = {
                platform: artifact.version for platform, artifact in artifacts.items()
            }
            msg = (
                "Superconductor release metadata returned mismatched versions: "
                f"{version_map}"
            )
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
        """Recompute hashes because the nightly discovery endpoint is mutable."""
        _ = (context, info)
        return False
