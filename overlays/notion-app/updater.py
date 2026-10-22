"""Track Notion's stable Apple Silicon desktop release."""

from typing import TYPE_CHECKING, ClassVar

from lib.update.updaters import ElectronBuilderAssetURLsUpdater, register_updater

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


@register_updater
class NotionAppUpdater(ElectronBuilderAssetURLsUpdater):
    """Reuse the vendor feed and ZIP format consumed by nixpkgs."""

    name = "notion-app"
    FEED_URL = "https://desktop-release.notion-static.com/arm64-mac.yml"
    PLATFORMS: ClassVar[dict[str, str]] = {"aarch64-darwin": "arm64"}
    SELECTORS: ClassVar[Mapping[str, Callable[[str, str], bool]]] = {
        "aarch64-darwin": lambda version, url: url.endswith(
            f"/Notion-arm64-{version}.zip"
        ),
    }
    DOWNLOAD_URL_TEMPLATE = (
        "https://desktop-release.notion-static.com/Notion-arm64-{version}.zip"
    )
