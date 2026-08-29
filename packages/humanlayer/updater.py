"""Updater for stable HumanLayer macOS releases."""

import re
from typing import TYPE_CHECKING, ClassVar

from lib.update.derivation_validation import DerivationValidation
from lib.update.net import fetch_url
from lib.update.updaters import (
    DownloadUrlMetadataUpdater,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import DownloadUrlMetadata

if TYPE_CHECKING:
    import aiohttp

_CASK_URL = (
    "https://raw.githubusercontent.com/humanlayer/homebrew-humanlayer/"
    "main/Casks/humanlayer.rb"
)
_VERSION_PATTERN = re.compile(
    r'^\s*version\s+"(?P<version>\d+(?:\.\d+)+)"\s*$',
    re.MULTILINE,
)
_SHA256_PATTERN = re.compile(r'^\s*sha256\s+"[0-9a-f]{64}"\s*$', re.MULTILINE)
_URL_PATTERN = re.compile(
    r'^\s*url\s+"(?P<url>'
    r"https://github\.com/humanlayer/homebrew-humanlayer/releases/download/"
    r"riptide-v(?P<version>\d+(?:\.\d+)+)/Riptide-darwin-arm64\.dmg"
    r')"\s*,?\s*$',
    re.MULTILINE,
)


@register_updater
class HumanLayerUpdater(DownloadUrlMetadataUpdater):
    """Track the signed arm64 DMG named by HumanLayer's stable public cask."""

    name = "humanlayer"
    CASK_URL = _CASK_URL
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
    }
    derivation_validations = (
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            systems=("aarch64-darwin",),
            mode="build",
        ),
    )

    @staticmethod
    def _parse_cask(payload: bytes) -> tuple[str, str]:
        """Return the version and immutable DMG URL from the official cask."""
        cask = payload.decode()
        version_match = _VERSION_PATTERN.search(cask)
        url_match = _URL_PATTERN.search(cask)
        if (
            version_match is None
            or url_match is None
            or _SHA256_PATTERN.search(cask) is None
            or version_match.group("version") != url_match.group("version")
        ):
            msg = f"Could not parse stable HumanLayer release from {_CASK_URL}"
            raise RuntimeError(msg)
        return version_match.group("version"), url_match.group("url")

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve the stable versioned artifact from the upstream cask."""
        payload = await fetch_url(
            session,
            self.CASK_URL,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        version, url = self._parse_cask(payload)
        return VersionInfo(
            version=version,
            metadata=DownloadUrlMetadata(url=url),
        )
