"""Updater for Capy desktop macOS releases."""

from typing import TYPE_CHECKING, ClassVar

from lib.update.updaters import ElectronBuilderAssetURLsUpdater, register_updater

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


@register_updater
class CapyUpdater(ElectronBuilderAssetURLsUpdater):
    """Track architecture-specific ZIPs from Capy's stable update feed."""

    name = "capy"
    FEED_URL: ClassVar[str] = (
        "https://d1lfowv2t69uz0.cloudfront.net/stable/latest-mac.yml"
    )
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
        "x86_64-darwin": "x64",
    }
    SELECTORS: ClassVar[Mapping[str, Callable[[str, str], bool]]] = {
        "aarch64-darwin": lambda version, url: url.endswith(
            f"/Capy-{version}-arm64.zip"
        ),
        "x86_64-darwin": lambda version, url: url.endswith(f"/Capy-{version}-x64.zip"),
    }
    DOWNLOAD_URL_TEMPLATE: ClassVar[str] = (
        "https://d1lfowv2t69uz0.cloudfront.net/stable/"
        "Capy-{version}-{platform_value}.zip"
    )
