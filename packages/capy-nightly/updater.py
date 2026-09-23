"""Updater for Capy Nightly desktop macOS releases."""

from typing import TYPE_CHECKING, ClassVar
from urllib.parse import quote, unquote

from lib.update.updaters import ElectronBuilderAssetURLsUpdater, register_updater

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from lib.update.updaters import VersionInfo


@register_updater
class CapyNightlyUpdater(ElectronBuilderAssetURLsUpdater):
    """Track architecture-specific ZIPs from Capy's nightly update feed."""

    name = "capy-nightly"
    FEED_URL: ClassVar[str] = (
        "https://d1lfowv2t69uz0.cloudfront.net/nightly/latest-mac.yml"
    )
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
        "x86_64-darwin": "x64",
    }
    SELECTORS: ClassVar[Mapping[str, Callable[[str, str], bool]]] = {
        "aarch64-darwin": lambda version, url: unquote(url).endswith(
            f"/Capy Nightly-{version}-arm64.zip"
        ),
        "x86_64-darwin": lambda version, url: unquote(url).endswith(
            f"/Capy Nightly-{version}-x64.zip"
        ),
    }
    DOWNLOAD_URL_TEMPLATE: ClassVar[str] = (
        "https://d1lfowv2t69uz0.cloudfront.net/nightly/"
        "Capy%20Nightly-{version}-{platform_value}.zip"
    )

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Encode feed filenames containing spaces without double-encoding escapes."""
        return quote(super().get_download_url(platform, info), safe=":/%")
