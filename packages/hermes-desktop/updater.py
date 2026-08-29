"""Updater for Hermes Desktop's shared hermes-agent flake source."""

from lib.update.derivation_validation import DerivationValidation
from lib.update.updaters import FlakeInputMetadataUpdater, register_updater


@register_updater
class HermesDesktopUpdater(FlakeInputMetadataUpdater):
    """Track the same authoritative source revision as hermes-agent."""

    name = "hermes-desktop"
    input_name = "hermes-agent"
    supported_platforms = ("aarch64-darwin",)
    derivation_validations = (
        DerivationValidation(
            installable=".#packages.aarch64-darwin.hermes-desktop",
            systems=supported_platforms,
            mode="build",
        ),
    )
