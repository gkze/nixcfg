"""Fail-closed updater for official Zo desktop releases."""

from typing import ClassVar

from lib.update.updaters import GitHubReleaseAssetURLsUpdater, register_updater


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
        payload: dict[str, object],
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
