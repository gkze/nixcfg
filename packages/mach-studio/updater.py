"""Updater for stable Mach Studio macOS releases."""

from typing import TYPE_CHECKING, ClassVar

from lib.update.derivation_validation import DerivationValidation
from lib.update.updaters import ElectronBuilderAssetURLsUpdater, register_updater

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


@register_updater
class MachStudioUpdater(ElectronBuilderAssetURLsUpdater):
    """Track Mach Studio's signed arm64 DMG from its electron-builder feed."""

    name = "mach-studio"
    FEED_URL: ClassVar[str] = (
        "https://api.maniac.ai/storage/v1/object/public/"
        "desktop-releases/stable/mac-arm64/latest-mac.yml"
    )
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
    }
    SELECTORS: ClassVar[Mapping[str, Callable[[str, str], bool]]] = {
        "aarch64-darwin": lambda version, url: url.endswith(
            f"/Mach-Studio-{version}-arm64.dmg"
        ),
    }
    DOWNLOAD_URL_TEMPLATE: ClassVar[str] = (
        "https://api.maniac.ai/storage/v1/object/public/"
        "desktop-releases/stable/mac-arm64/"
        "Mach-Studio-{version}-{platform_value}.dmg"
    )
    derivation_validations = (
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            systems=("aarch64-darwin",),
            mode="build",
        ),
    )
