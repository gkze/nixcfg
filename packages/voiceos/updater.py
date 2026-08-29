"""Updater for VoiceOS desktop macOS releases."""

from typing import TYPE_CHECKING, ClassVar

from lib.update.updaters import ElectronBuilderAssetURLsUpdater, register_updater

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


@register_updater
class VoiceOSUpdater(ElectronBuilderAssetURLsUpdater):
    """Track VoiceOS's signed universal ZIP from its electron-builder feed."""

    name = "voiceos"
    FEED_URL: ClassVar[str] = (
        "https://voiceos-staging-releases.s3.amazonaws.com/releases/latest-mac.yml"
    )
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "universal",
        "x86_64-darwin": "universal",
    }
    SELECTORS: ClassVar[Mapping[str, Callable[[str, str], bool]]] = {
        platform: lambda version, url: url.endswith(
            f"/VoiceOS-{version}-universal-mac.zip"
        )
        for platform in PLATFORMS
    }
    DOWNLOAD_URL_TEMPLATE: ClassVar[str] = (
        "https://voiceos-staging-releases.s3.amazonaws.com/releases/"
        "VoiceOS-{version}-universal-mac.zip"
    )
