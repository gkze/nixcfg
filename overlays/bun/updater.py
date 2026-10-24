"""Updater for nixcfg's single Bun runtime."""

from typing import ClassVar

from lib.system_policy import bun_artifact_names, supported_systems
from lib.update.derivation_validation import DerivationValidation
from lib.update.updaters import GitHubReleaseAssetURLsUpdater, register_updater

_SYSTEMS = supported_systems()
_ASSETS = bun_artifact_names()


@register_updater
class BunUpdater(GitHubReleaseAssetURLsUpdater):
    """Track the official Bun release binaries that override ``pkgs.bun``.

    Asset names come from the shared system policy, limited to the systems the
    nixpkgs Bun derivation can unpack. ``x86_64-darwin`` has an upstream zip,
    but that derivation only sets ``sourceRoot`` for ``aarch64-darwin`` and is
    not exported here.
    """

    name = "bun"
    GITHUB_OWNER = "oven-sh"
    GITHUB_REPO = "bun"
    TAG_PREFIX = "bun-v"
    PLATFORMS: ClassVar[dict[str, str]] = {
        system: _ASSETS[system] for system in _SYSTEMS
    }
    ASSET_NAME_TEMPLATE: ClassVar[str] = "{platform_value}"
    derivation_validations = (
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            systems=_SYSTEMS,
            mode="build",
        ),
    )
