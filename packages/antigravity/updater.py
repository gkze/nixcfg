"""Updater for Google Antigravity."""

import re
from typing import TYPE_CHECKING, ClassVar

from lib.update.derivation_validation import DerivationValidation
from lib.update.updaters import (
    DownloadUrlMetadataUpdater,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import DownloadUrlMetadata
from lib.update.updaters.vendor_feeds import fetch_electron_builder_feed

if TYPE_CHECKING:
    import aiohttp


@register_updater
class AntigravityUpdater(DownloadUrlMetadataUpdater):
    """Resolve Google Antigravity from its packaged electron-builder feed."""

    name = "antigravity"
    FEED_URL = (
        "https://antigravity-hub-auto-updater-974169037036.us-central1.run.app/"
        "manifest/latest-arm64-mac.yml"
    )
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin-arm",
    }
    URL_METADATA_CONTEXT = "Google Antigravity metadata"
    _ARTIFACT_URL = re.compile(
        r"https://storage\.googleapis\.com/antigravity-public/antigravity-hub/"
        r"(?P<release>[^/]+)/darwin-arm/Antigravity\.zip"
    )
    derivation_validations = (
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            systems=("aarch64-darwin",),
            mode="build",
        ),
    )

    @classmethod
    def _parse_artifact_url(cls, url: str, product_version: str) -> tuple[str, str]:
        """Validate one vendor ZIP and return its release token and DMG peer."""
        match = cls._ARTIFACT_URL.fullmatch(url)
        if match is None:
            msg = f"Unexpected Google Antigravity artifact URL: {url}"
            raise RuntimeError(msg)
        release = match.group("release")
        build_id = release.removeprefix(f"{product_version}-")
        if build_id == release or not build_id.isdigit():
            msg = (
                "Google Antigravity artifact version does not match feed: "
                f"{release} != {product_version}"
            )
            raise RuntimeError(msg)
        return release, url.removesuffix(".zip") + ".dmg"

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch the latest Antigravity version and immutable DMG URL."""
        product_version, artifact_urls = await fetch_electron_builder_feed(
            session,
            self.FEED_URL,
            config=self.config,
        )
        for artifact_url in artifact_urls:
            try:
                version, dmg_url = self._parse_artifact_url(
                    artifact_url,
                    product_version,
                )
            except RuntimeError:
                continue
            return VersionInfo(
                version=version,
                metadata=DownloadUrlMetadata(url=dmg_url),
            )
        msg = f"No matching Google Antigravity artifact in {self.FEED_URL}"
        raise RuntimeError(msg)
