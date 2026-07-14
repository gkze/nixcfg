"""Updater for Wave."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from lib.update.updaters import ElectronBuilderAssetURLsUpdater, register_updater

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


@register_updater
class WaveUpdater(ElectronBuilderAssetURLsUpdater):
    """Track Wave terminal DMG assets from the electron-builder feed."""

    name = "wave"
    FEED_URL: ClassVar[str] = "https://dl.waveterm.dev/releases-w2/latest-mac.yml"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
    }
    SELECTORS: ClassVar[Mapping[str, Callable[[str, str], bool]]] = {
        "aarch64-darwin": lambda version, url: url.endswith(
            f"Wave-darwin-arm64-{version}.dmg"
        ),
    }
    DOWNLOAD_URL_TEMPLATE: ClassVar[str] = (
        "https://dl.waveterm.dev/releases-w2/Wave-darwin-{platform_value}-{version}.dmg"
    )
