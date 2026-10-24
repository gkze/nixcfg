"""Updater for Aside browser macOS releases."""

import asyncio
import re
from typing import TYPE_CHECKING, ClassVar
from urllib.parse import urlencode, urlsplit

from lib.update.net import fetch_redirect_location
from lib.update.updaters import (
    AssetURLsMetadataUpdater,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.core import verify_platform_versions
from lib.update.updaters.metadata import AssetURLsMetadata
from lib.update.updaters.strategies import ResolvedArtifact

if TYPE_CHECKING:
    import aiohttp

    from lib.update.updaters.core import UpdateContext

_DOWNLOAD_URL = "https://aside.com/api/download/macos"
_ARTIFACT_HOST = "releases.aside.com"
_VERSION_PATTERN = r"[0-9]+(?:\.[0-9]+)+"


@register_updater
class AsideUpdater(AssetURLsMetadataUpdater):
    """Resolve Aside's versioned per-architecture DMGs from its download redirect.

    Aside now ships separate ``-arm64`` / ``-x64`` DMGs (the unsuffixed DMG is
    gone), and the bare endpoint redirects to the HTML download page (Update
    run 39829870830). An explicit ``arch`` query redirects to the immutable
    DMG for that architecture.
    """

    name = "aside"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
        "x86_64-darwin": "x64",
    }
    ASSET_URLS_METADATA_CONTEXT = "Aside download redirects"

    @classmethod
    def _download_url(cls, platform: str) -> str:
        return f"{_DOWNLOAD_URL}?{urlencode({'arch': cls.PLATFORMS[platform]})}"

    @classmethod
    def _parse_artifact_url(cls, platform: str, url: str) -> ResolvedArtifact:
        arch = re.escape(cls.PLATFORMS[platform])
        pattern = rf"/dev-updater/Aside-(?P<version>{_VERSION_PATTERN})-{arch}\.dmg"
        parsed = urlsplit(url)
        match = re.fullmatch(pattern, parsed.path)
        if (
            parsed.scheme != "https"
            or parsed.netloc != _ARTIFACT_HOST
            or parsed.query
            or parsed.fragment
            or match is None
        ):
            msg = (
                f"Could not extract Aside {platform} version from download "
                f"redirect: {url}"
            )
            raise RuntimeError(msg)
        return ResolvedArtifact(version=match.group("version"), url=url)

    async def _fetch_artifact(
        self,
        session: aiohttp.ClientSession,
        platform: str,
    ) -> ResolvedArtifact:
        location = await fetch_redirect_location(
            session, self._download_url(platform), config=self.config
        )
        return self._parse_artifact_url(platform, location)

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Resolve one Aside version and its immutable per-architecture DMGs."""
        _ = context
        artifacts = dict(
            zip(
                self.PLATFORMS,
                await asyncio.gather(
                    *(
                        self._fetch_artifact(session, platform)
                        for platform in self.PLATFORMS
                    )
                ),
                strict=True,
            )
        )
        return VersionInfo(
            version=verify_platform_versions(
                {platform: a.version for platform, a in artifacts.items()},
                context="Aside download redirects returned mismatched versions",
            ),
            metadata=AssetURLsMetadata({
                platform: artifact.url for platform, artifact in artifacts.items()
            }),
        )
