"""Updater for Aside browser macOS releases."""

import re
from typing import ClassVar
from urllib.parse import urlsplit

import aiohttp

from lib.update.net import HTTP_BAD_REQUEST
from lib.update.updaters import (
    DownloadUrlMetadataUpdater,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import DownloadUrlMetadata

_HTTP_REDIRECT_MIN = 300
_DOWNLOAD_URL = "https://aside.com/api/download/macos"
_ARTIFACT_HOST = "releases.aside.com"
_ARTIFACT_PATH = re.compile(r"/dev-updater/Aside-(?P<version>[0-9]+(?:\.[0-9]+)+)\.dmg")


@register_updater
class AsideUpdater(DownloadUrlMetadataUpdater):
    """Resolve Aside's versioned DMG from its canonical download redirect."""

    name = "aside"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin",
        "x86_64-darwin": "darwin",
    }
    URL_METADATA_CONTEXT = "Aside download redirect"

    @staticmethod
    def _parse_artifact_url(url: str) -> tuple[str, str]:
        parsed = urlsplit(url)
        match = _ARTIFACT_PATH.fullmatch(parsed.path)
        if (
            parsed.scheme != "https"
            or parsed.netloc != _ARTIFACT_HOST
            or parsed.query
            or parsed.fragment
            or match is None
        ):
            msg = f"Could not extract Aside version from download redirect: {url}"
            raise RuntimeError(msg)
        return match.group("version"), url

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve the latest immutable Aside artifact without downloading it."""
        timeout = aiohttp.ClientTimeout(total=self.config.default_timeout)
        async with session.head(
            _DOWNLOAD_URL,
            allow_redirects=False,
            timeout=timeout,
        ) as response:
            if not _HTTP_REDIRECT_MIN <= response.status < HTTP_BAD_REQUEST:
                msg = (
                    f"Expected Aside download redirect from {_DOWNLOAD_URL}, "
                    f"got HTTP {response.status} {response.reason}"
                )
                raise RuntimeError(msg)
            location = response.headers.get("Location")
            if not location:
                msg = f"Aside download redirect from {_DOWNLOAD_URL} omitted Location"
                raise RuntimeError(msg)

        version, artifact_url = self._parse_artifact_url(location)
        return VersionInfo(
            version=version,
            metadata=DownloadUrlMetadata(url=artifact_url),
        )
