"""Updater for Melty Conductor release artifacts."""

import re
from email.message import Message
from typing import TYPE_CHECKING, ClassVar

import aiohttp

if TYPE_CHECKING:
    from lib.update.updaters import UpdateContext

from lib.http_utils import HTTP_BAD_REQUEST
from lib.update.updaters import (
    AssetURLsMetadataUpdater,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.core import verify_platform_versions
from lib.update.updaters.metadata import AssetURLsMetadata
from lib.update.updaters.strategies import ResolvedArtifact, url_without_query


@register_updater
class ConductorUpdater(AssetURLsMetadataUpdater):
    """Resolve latest Conductor artifacts to immutable CDN asset URLs."""

    name = "conductor"
    BASE_URL = "https://cdn.crabnebula.app/download/melty/conductor/latest/platform"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "dmg-aarch64",
        "x86_64-darwin": "dmg-x86_64",
    }
    # Recompute hashes for mutable latest-channel artifacts before comparing.
    always_rehash = True

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

    async def _fetch_resolved_artifact(
        self,
        session: aiohttp.ClientSession,
        platform: str,
    ) -> ResolvedArtifact:
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
            return ResolvedArtifact(
                version=version,
                url=url_without_query(str(response.url)),
            )

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Infer the current version and resolved asset URLs from redirects."""
        _ = context
        artifacts = {
            platform: await self._fetch_resolved_artifact(session, platform)
            for platform in self.PLATFORMS
        }
        version_map = {
            platform: artifact.version for platform, artifact in artifacts.items()
        }
        version = verify_platform_versions(
            version_map,
            context="Conductor release metadata returned mismatched versions",
        )
        return VersionInfo(
            version=version,
            metadata=AssetURLsMetadata({
                platform: artifact.url for platform, artifact in artifacts.items()
            }),
        )
