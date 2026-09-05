"""Updater for Signal Desktop beta macOS releases."""

import re
import urllib.parse
from functools import partial
from typing import TYPE_CHECKING, ClassVar, cast

from lib.update.updaters import (
    AssetURLsMetadataUpdater,
    UpdateContext,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import AssetURLsMetadata
from lib.update.updaters.vendor_feeds import fetch_electron_builder_feed

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    import aiohttp

    from lib.nix.models.sources import SourceEntry

_BETA_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+-beta\.[0-9]+")
# Signal's build declares this as its release publisher; paths remain feed-owned.
_ARTIFACT_ORIGIN = "updates.signal.org"
_ARCHITECTURE_ALIASES = {
    "arm64": frozenset({"aarch64", "arm64"}),
    "x64": frozenset({"amd64", "x64"}),
}


def _is_signal_beta_macos_zip(version: str, url: str, *, architecture: str) -> bool:
    """Recognize a versioned macOS ZIP without assuming Signal's filename layout."""
    parsed = urllib.parse.urlsplit(url)
    path = urllib.parse.unquote(parsed.path).lower().replace("x86_64", "x64")
    tokens = frozenset(re.findall(r"[a-z0-9]+", path))
    version_pattern = rf"(?<![a-z0-9]){re.escape(version.lower())}(?![a-z0-9])"
    matched_architectures = {
        candidate
        for candidate, aliases in _ARCHITECTURE_ALIASES.items()
        if not aliases.isdisjoint(tokens)
    }
    return (
        parsed.scheme == "https"
        and parsed.netloc == _ARTIFACT_ORIGIN
        and path.endswith(".zip")
        and re.search(version_pattern, path) is not None
        and matched_architectures == {architecture}
    )


@register_updater
class SignalBetaUpdater(AssetURLsMetadataUpdater):
    """Track the macOS artifacts Signal has actually published to its beta feed."""

    name = "signal-beta"
    FEED_URL: ClassVar[str] = "https://updates.signal.org/desktop/beta-mac.yml"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
        "x86_64-darwin": "x64",
    }
    SELECTORS: ClassVar[Mapping[str, Callable[[str, str], bool]]] = {
        platform: partial(
            _is_signal_beta_macos_zip,
            architecture=architecture,
        )
        for platform, architecture in PLATFORMS.items()
    }
    ASSET_URLS_METADATA_CONTEXT = "Signal beta feed metadata"

    @staticmethod
    def _validate_beta_version(version: str) -> str:
        if _BETA_VERSION.fullmatch(version) is None:
            msg = f"Signal beta feed returned a non-beta version: {version!r}"
            raise RuntimeError(msg)
        return version

    @classmethod
    def _select_asset_urls(
        cls,
        version: str,
        urls: tuple[str, ...],
    ) -> dict[str, str]:
        selected: dict[str, str] = {}
        for platform, selector in cls.SELECTORS.items():
            matches = tuple(url for url in urls if selector(version, url))
            if len(matches) != 1:
                msg = (
                    f"Expected exactly one Signal beta URL for {platform!r} in "
                    f"{cls.FEED_URL}, found {len(matches)}"
                )
                raise RuntimeError(msg)
            selected[platform] = matches[0]
        return selected

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve the version and both ZIP URLs from Signal's macOS beta feed."""
        version, urls = await fetch_electron_builder_feed(
            session,
            self.FEED_URL,
            config=self.config,
        )
        version = self._validate_beta_version(version)
        return VersionInfo(
            version=version,
            metadata=AssetURLsMetadata(self._select_asset_urls(version, urls)),
        )

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        """Refresh when Signal republishes a feed URL under the same version."""
        if not await super()._is_latest(context, info):
            return False
        current = context.current if isinstance(context, UpdateContext) else context
        return cast("SourceEntry", current).urls == self._platform_urls(info)
