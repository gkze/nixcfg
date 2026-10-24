"""Updater for Superconductor nightly macOS app archives."""

import re
import urllib.parse
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, ClassVar

from lib.update.net import request
from lib.update.updaters import (
    AssetURLsMetadataUpdater,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.core import verify_platform_versions
from lib.update.updaters.metadata import AssetURLsMetadata
from lib.update.updaters.strategies import ResolvedArtifact, url_without_query

if TYPE_CHECKING:
    import aiohttp

    from lib.update.updaters import UpdateContext


@register_updater
class SuperconductorUpdater(AssetURLsMetadataUpdater):
    """Resolve Superconductor's mutable nightly download endpoint."""

    name = "superconductor"
    DISCOVERY_URL = "https://super.engineering/api/download"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
    }
    # Recompute hashes because the nightly discovery endpoint is mutable.
    always_rehash = True

    @staticmethod
    def _version_from_url(url: str, last_modified: str | None) -> str:
        filename = urllib.parse.urlsplit(url).path.rsplit("/", maxsplit=1)[-1]
        match = re.fullmatch(
            r"(?:Superconductor|super\.engineering)-nightly-([^-]+)-[^.]+\.dmg",
            filename,
        )
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
    ) -> ResolvedArtifact:
        query = urllib.parse.urlencode({"arch": self.PLATFORMS[platform]})
        response = await request(
            session,
            f"{self.DISCOVERY_URL}?{query}",
            method="HEAD",
            config=self.config,
        )
        url = url_without_query(response.url)
        return ResolvedArtifact(
            version=self._version_from_url(url, response.headers.get("Last-Modified")),
            url=url,
        )

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Infer the current nightly version and resolved asset URL."""
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
            context="Superconductor release metadata returned mismatched versions",
        )
        return VersionInfo(
            version=version,
            metadata=AssetURLsMetadata({
                platform: artifact.url for platform, artifact in artifacts.items()
            }),
        )
