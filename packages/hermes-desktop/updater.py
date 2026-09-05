"""Updater for Hermes Desktop's shared hermes-agent flake source."""

from typing import TYPE_CHECKING

from lib.update.derivation_validation import DerivationValidation
from lib.update.electron_manifest import (
    ElectronManifestMetadata,
    fetch_flake_electron_manifest,
)
from lib.update.updaters import (
    FlakeInputMetadataUpdater,
    VersionInfo,
    register_updater,
)

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry, SourceHashes


@register_updater
class HermesDesktopUpdater(FlakeInputMetadataUpdater):
    """Track the same authoritative source revision as hermes-agent."""

    name = "hermes-desktop"
    aggregate_into = ("electron-runtimes",)
    input_name = "hermes-agent"
    supported_platforms = ("aarch64-darwin",)
    derivation_validations = (
        DerivationValidation(
            installable=".#packages.aarch64-darwin.hermes-desktop",
            systems=supported_platforms,
            mode="build",
        ),
    )

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve Electron from the desktop manifest at the locked input commit."""
        info = await super().fetch_latest(session)
        metadata = await fetch_flake_electron_manifest(
            session,
            node=self._resolve_flake_node(info),
            manifest_path="apps/desktop/package.json",
            dependency_group="devDependencies",
            context="Hermes Desktop flake input",
            config=self.config,
        )
        return VersionInfo(version=info.version, metadata=metadata)

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist the immutable source and its manifest-selected Electron runtime."""
        if not isinstance(info.metadata, ElectronManifestMetadata):
            msg = "Hermes Desktop metadata is missing its resolved Electron manifest"
            raise TypeError(msg)
        return (
            super()
            .build_result(info, hashes)
            .model_copy(update={"electron_version": info.metadata.electron_version})
        )
