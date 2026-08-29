"""Updater for Screen Studio macOS releases."""

import asyncio
import json
import re
from dataclasses import dataclass
from typing import ClassVar
from urllib.parse import urlsplit

import aiohttp

from lib import json_utils
from lib.update.net import HTTP_BAD_REQUEST
from lib.update.updaters import (
    AssetURLsMetadataUpdater,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import AssetURLsMetadata

_FEED_VERSION = "0.0.0"
_FEED_MACHINE_ID = "nixcfg-updater"
_ARTIFACT_HOST = "screenstudioassets.com"
_VERSION_PATTERN = r"[0-9]+(?:\.[0-9]+)+-[0-9]+"
_ARTIFACT_PATHS: dict[str, re.Pattern[str]] = {
    "aarch64-darwin": re.compile(
        rf"/releases/(?P<version>{_VERSION_PATTERN})/"
        rf"Screen%20Studio-(?P=version)-arm64-mac\.zip"
    ),
    "x86_64-darwin": re.compile(
        rf"/releases/(?P<version>{_VERSION_PATTERN})/"
        rf"Screen%20Studio-(?P=version)-mac\.zip"
    ),
}


@dataclass(frozen=True, slots=True)
class _ResolvedArtifact:
    version: str
    url: str


@register_updater
class ScreenStudioUpdater(AssetURLsMetadataUpdater):
    """Resolve stable Screen Studio ZIPs from its Squirrel.Mac update feed."""

    name = "screen-studio"
    FEED_URL = "https://screen.studio/api/app-update/v2"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
        "x86_64-darwin": "x64",
    }

    @staticmethod
    def _parse_artifact_url(platform: str, url: str) -> _ResolvedArtifact:
        parsed = urlsplit(url)
        match = _ARTIFACT_PATHS[platform].fullmatch(parsed.path)
        if (
            parsed.scheme != "https"
            or parsed.netloc != _ARTIFACT_HOST
            or parsed.query
            or parsed.fragment
            or match is None
        ):
            msg = (
                f"Screen Studio feed returned an invalid {platform} artifact URL: {url}"
            )
            raise RuntimeError(msg)
        return _ResolvedArtifact(version=match.group("version"), url=url)

    async def _fetch_artifact(
        self,
        session: aiohttp.ClientSession,
        platform: str,
    ) -> _ResolvedArtifact:
        headers = {
            "User-Agent": self.config.default_user_agent,
            "x-screen-studio-architecture": self.PLATFORMS[platform],
            "x-screen-studio-platform": "darwin",
            "x-screen-studio-version": _FEED_VERSION,
            "x-screen-studio-updates-channel": "stable",
            "x-screen-studio-machine-id": _FEED_MACHINE_ID,
        }
        timeout = aiohttp.ClientTimeout(total=self.config.default_timeout)
        async with session.request(
            "GET",
            self.FEED_URL,
            headers=headers,
            allow_redirects=True,
            timeout=timeout,
        ) as response:
            payload_bytes = await response.read()
            if response.status >= HTTP_BAD_REQUEST:
                msg = (
                    f"Screen Studio update feed request for {platform} failed with "
                    f"HTTP {response.status} {response.reason}"
                )
                raise RuntimeError(msg)

        try:
            payload_value = json.loads(payload_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            msg = f"Screen Studio update feed returned invalid JSON for {platform}"
            raise RuntimeError(msg) from exc
        payload = json_utils.as_object_dict(
            payload_value,
            context=f"Screen Studio update feed for {platform}",
        )
        url = json_utils.get_required_str(
            payload,
            "url",
            context=f"Screen Studio update feed for {platform}",
        )
        feed_version = json_utils.get_required_str(
            payload,
            "name",
            context=f"Screen Studio update feed for {platform}",
        )
        artifact = self._parse_artifact_url(platform, url)
        if artifact.version != feed_version:
            msg = (
                f"Screen Studio update feed version {feed_version!r} does not match "
                f"its {platform} artifact version {artifact.version!r}"
            )
            raise RuntimeError(msg)
        return artifact

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve one stable version and its official per-architecture ZIPs."""
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
        versions = {
            platform: artifact.version for platform, artifact in artifacts.items()
        }
        if len(set(versions.values())) != 1:
            msg = f"Screen Studio update feed returned mismatched versions: {versions}"
            raise RuntimeError(msg)
        return VersionInfo(
            version=next(iter(versions.values())),
            metadata=AssetURLsMetadata({
                platform: artifact.url for platform, artifact in artifacts.items()
            }),
        )
