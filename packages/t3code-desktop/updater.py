"""Updater registration for T3 Code Desktop's staged runtime Bun cache."""

from typing import TYPE_CHECKING, ClassVar

from lib.update.electron_manifest import (
    ElectronManifestMetadata,
    fetch_flake_electron_manifest,
)
from lib.update.updaters import VersionInfo, register_updater
from lib.update.updaters.t3_runtime import T3RuntimeUpdater

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry, SourceHashes


@register_updater
class T3CodeDesktopUpdater(T3RuntimeUpdater):
    """Compute only the desktop runtime ``node_modules`` hash."""

    name = "t3code-desktop"
    aggregate_into = ("electron-runtimes",)
    compatibility_pin_rationale = (
        "T3 Code requires the reviewed Electron Builder line and local backport "
        "until the upstream packaging seam is compatible."
    )
    compatibility_pins: ClassVar[dict[str, str]] = {"electronBuilderVersion": "26.15.7"}
    generated_artifact_files = ("../t3code/bun.lock", "bun.lock")

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve Electron from the desktop manifest at the locked input commit."""
        info = await super().fetch_latest(session)
        metadata = await fetch_flake_electron_manifest(
            session,
            node=self._resolve_flake_node(info),
            manifest_path="apps/desktop/package.json",
            dependency_group="dependencies",
            context="T3 Code Desktop flake input",
            config=self.config,
        )
        return VersionInfo(version=info.version, metadata=metadata)

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist runtime hashes, compatibility pin, and exact Electron runtime."""
        if not isinstance(info.metadata, ElectronManifestMetadata):
            msg = "T3 Code Desktop metadata is missing its resolved Electron manifest"
            raise TypeError(msg)
        return (
            super()
            .build_result(info, hashes)
            .model_copy(update={"electron_version": info.metadata.electron_version})
        )
