"""Updater for Energy desktop macOS releases."""

from typing import TYPE_CHECKING, ClassVar

from lib.update.derivation_validation import DerivationValidation
from lib.update.updaters import ElectronBuilderAssetURLsUpdater, register_updater

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


@register_updater
class EnergyUpdater(ElectronBuilderAssetURLsUpdater):
    """Track Energy's signed arm64 DMG from its electron-builder feed."""

    name = "energy"
    FEED_URL: ClassVar[str] = (
        "https://static.getenergy.com/desktop/beta/arm64/latest-mac.yml"
    )
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
    }
    SELECTORS: ClassVar[Mapping[str, Callable[[str, str], bool]]] = {
        "aarch64-darwin": lambda version, url: url.endswith(f"/{version}/arm64.dmg"),
    }
    DOWNLOAD_URL_TEMPLATE: ClassVar[str] = (
        "https://static.getenergy.com/desktop/beta/arm64/{version}/{platform_value}.dmg"
    )
    derivation_validations = (
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            systems=("aarch64-darwin",),
            mode="build",
        ),
    )
