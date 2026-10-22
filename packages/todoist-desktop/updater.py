"""Updater for Todoist's current native macOS releases."""

from typing import TYPE_CHECKING, ClassVar

from lib.update.updaters import ElectronBuilderAssetURLsUpdater, register_updater

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


@register_updater
class TodoistDesktopUpdater(ElectronBuilderAssetURLsUpdater):
    """Resolve the ARM64 DMG from Todoist's own update feed."""

    name = "todoist-desktop"
    FEED_URL = "https://electron-dl.todoist.net/mac/latest-mac.yml"
    PLATFORMS: ClassVar[dict[str, str]] = {"aarch64-darwin": "arm64"}
    SELECTORS: ClassVar[Mapping[str, Callable[[str, str], bool]]] = {
        "aarch64-darwin": lambda version, url: url.endswith(
            f"/Todoist-darwin-{version}-arm64-latest.dmg"
        ),
    }
    DOWNLOAD_URL_TEMPLATE = (
        "https://electron-dl.todoist.net/mac/"
        "Todoist-darwin-{version}-{platform_value}-latest.dmg"
    )
