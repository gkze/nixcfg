"""Updater for Comet browser macOS releases."""

import asyncio
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from lib.update.updaters import (
    DownloadHashUpdater,
    VersionInfo,
    register_updater,
    stream_url_hash_mapping,
)

if TYPE_CHECKING:
    from lib.nix.models.sources import SourceEntry
    from lib.update.events import EventStream
    from lib.update.updaters import UpdateContext


_HTTP_REDIRECT_MIN = 300
_HTTP_BAD_REQUEST = 400
_ARTIFACT_HOST = (
    "pplx-browser-binaries.a0adf9b772aecba4fa8883581f3c9180.r2.cloudflarestorage.com"
)
_ARTIFACT_PATH = re.compile(r"/(?P<version>\d+(?:\.\d+){3})/comet_latest\.dmg")


@dataclass(frozen=True, slots=True)
class _ResolvedArtifact:
    version: str
    signed_url: str
    identity_url: str


@register_updater
class CometUpdater(DownloadHashUpdater):
    """Resolve Comet from Perplexity's canonical installer redirects."""

    name = "comet"
    materialize_when_current = True
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": (
            "https://www.perplexity.ai/rest/browser/download?"
            "channel=stable&platform=mac_arm64"
        ),
        "x86_64-darwin": (
            "https://www.perplexity.ai/rest/browser/download?"
            "channel=stable&platform=mac_x64"
        ),
    }

    @staticmethod
    def _parse_artifact(url: str) -> _ResolvedArtifact:
        parsed = urlsplit(url)
        match = _ARTIFACT_PATH.fullmatch(parsed.path)
        if parsed.scheme != "https" or parsed.netloc != _ARTIFACT_HOST or match is None:
            msg = f"Could not extract Comet version from download redirect: {url}"
            raise RuntimeError(msg)
        identity_url = urlunsplit((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            "",
            "",
        ))
        return _ResolvedArtifact(
            version=match.group("version"),
            signed_url=url,
            identity_url=identity_url,
        )

    async def _resolve_latest_url(
        self,
        session: aiohttp.ClientSession,
        download_url: str,
    ) -> _ResolvedArtifact:
        timeout = aiohttp.ClientTimeout(total=self.config.default_timeout)
        async with session.request(
            "GET",
            download_url,
            allow_redirects=False,
            timeout=timeout,
        ) as response:
            if not _HTTP_REDIRECT_MIN <= response.status < _HTTP_BAD_REQUEST:
                msg = (
                    f"Expected Comet download redirect from {download_url}, "
                    f"got HTTP {response.status} {response.reason}"
                )
                raise RuntimeError(msg)
            location = response.headers.get("Location")
            if not location:
                msg = f"Comet download redirect from {download_url} did not include Location"
                raise RuntimeError(msg)
        return self._parse_artifact(location)

    async def _resolve_artifacts(
        self,
        session: aiohttp.ClientSession,
    ) -> dict[str, _ResolvedArtifact]:
        artifacts = await asyncio.gather(
            *(
                self._resolve_latest_url(session, download_url)
                for download_url in self.PLATFORMS.values()
            )
        )
        return dict(zip(self.PLATFORMS, artifacts, strict=True))

    @staticmethod
    def _uniform_version(artifacts: dict[str, _ResolvedArtifact]) -> str:
        versions = {
            platform: artifact.version for platform, artifact in artifacts.items()
        }
        unique_versions = set(versions.values())
        if len(unique_versions) != 1:
            msg = "Comet download redirects returned mismatched versions: " + ", ".join(
                f"{platform}={version}"
                for platform, version in sorted(versions.items())
            )
            raise RuntimeError(msg)
        return unique_versions.pop()

    @staticmethod
    def _deduplicated_hash_urls(
        artifacts: dict[str, _ResolvedArtifact],
    ) -> dict[str, str]:
        signed_urls_by_identity: dict[str, str] = {}
        urls_by_platform: dict[str, str] = {}
        for platform, artifact in artifacts.items():
            signed_url = signed_urls_by_identity.setdefault(
                artifact.identity_url,
                artifact.signed_url,
            )
            urls_by_platform[platform] = signed_url
        return urls_by_platform

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve one current version from the official installer redirects."""
        artifacts = await self._resolve_artifacts(session)
        return VersionInfo(version=self._uniform_version(artifacts))

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Hash freshly signed artifacts that still match the resolved version."""
        _ = context
        artifacts = await self._resolve_artifacts(session)
        resolved_version = self._uniform_version(artifacts)
        if resolved_version != info.version:
            msg = (
                f"Comet download version changed from {info.version} "
                f"to {resolved_version} while fetching hashes"
            )
            raise RuntimeError(msg)
        async for event in stream_url_hash_mapping(
            self.name,
            self._deduplicated_hash_urls(artifacts),
            config=self.config,
        ):
            yield event
