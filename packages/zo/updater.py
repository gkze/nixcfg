"""Fail-closed updater for official Zo desktop releases."""

import re
from typing import TYPE_CHECKING, ClassVar

from lib.update.net import fetch_github_api
from lib.update.updaters import (
    GitHubReleaseAssetURLsUpdater,
    UpdateContext,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import AssetURLsMetadata
from lib.update.updaters.vendor_feeds import fetch_electron_builder_manifest

if TYPE_CHECKING:
    import aiohttp

    from lib.update.net import JSONValue

_MAC_FEED = "latest-mac.yml"
_EXACT_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


@register_updater
class ZoUpdater(GitHubReleaseAssetURLsUpdater):
    """Track Zo's immutable universal macOS release ZIP."""

    name = "zo"
    GITHUB_OWNER = "zocomputer"
    GITHUB_REPO = "Zo"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "universal",
        "x86_64-darwin": "universal",
    }
    supported_platforms = tuple(PLATFORMS)
    ASSET_NAME_TEMPLATE: ClassVar[str] = "Zo-{version}-universal-mac.zip"

    def _asset_urls_from_payload(
        self,
        payload: dict[str, JSONValue],
        *,
        version: str,
        tag_name: str,
    ) -> dict[str, str]:
        urls = super()._asset_urls_from_payload(
            payload,
            version=version,
            tag_name=tag_name,
        )
        expected = self._fallback_url(version, "universal")
        if any(url != expected for url in urls.values()):
            msg = (
                "Zo release metadata did not return the canonical immutable "
                f"GitHub asset URL {expected!r}"
            )
            raise RuntimeError(msg)
        return urls

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Follow Zo's macOS update feed, not the newest tag.

        v1.5.14 shipped Linux and Windows builds only. Its latest-mac.yml still
        names the 1.5.13 ZIP, so the newest Mac build lives in an older tag.
        """
        _ = context
        payload = await self._fetch_latest_release_payload(session)
        tag_name = self._release_tag_from_payload(payload)
        feed_url = self._named_asset_url(payload, _MAC_FEED, tag_name)
        feed = await fetch_electron_builder_manifest(
            session, feed_url, config=self.config
        )
        version = feed.version
        if _EXACT_VERSION.fullmatch(version) is None:
            msg = f"Zo {_MAC_FEED} in tag {tag_name} has no exact version"
            raise RuntimeError(msg)
        if feed.path != self._asset_name(version, "universal"):
            msg = f"Zo {_MAC_FEED} in tag {tag_name} names an unexpected macOS asset"
            raise RuntimeError(msg)
        mac_tag = f"{self.TAG_PREFIX}{version}"
        if mac_tag != tag_name:
            tagged = await fetch_github_api(
                session,
                f"repos/{self.GITHUB_OWNER}/{self.GITHUB_REPO}/releases/tags/{mac_tag}",
                config=self.config,
            )
            if not isinstance(tagged, dict):
                msg = f"Unexpected release payload type: {type(tagged).__name__}"
                raise TypeError(msg)
            payload = tagged
        return VersionInfo(
            version=version,
            metadata=AssetURLsMetadata(
                self._asset_urls_from_payload(
                    payload, version=version, tag_name=mac_tag
                )
            ),
        )

    def _named_asset_url(
        self, payload: dict[str, JSONValue], name: str, tag_name: str
    ) -> str:
        assets = payload.get("assets")
        urls = (
            [
                asset.get("browser_download_url")
                for asset in assets
                if isinstance(asset, dict) and asset.get("name") == name
            ]
            if isinstance(assets, list)
            else []
        )
        if len(urls) != 1 or not isinstance(urls[0], str):
            raise RuntimeError(self._missing_asset_message(name, tag_name))
        return urls[0]
