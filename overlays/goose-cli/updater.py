"""Updater for Goose's locked source metadata and crate2nix artifacts."""

from typing import TYPE_CHECKING, ClassVar

from lib.nix.models.sources import HashCollection, SourceEntry, SourceHashes
from lib.update.updaters import (
    Crate2NixMetadataUpdater,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import FlakeInputMetadata

if TYPE_CHECKING:
    import aiohttp


@register_updater
class GooseCliUpdater(Crate2NixMetadataUpdater):
    """Track Goose's release input and regenerate its crate2nix artifacts."""

    name = "goose-cli"
    input_name = "goose"
    # crate2nix omits package.rust-version. These are manifest-derived
    # compatibility values for the exact crate releases, and the package
    # override rejects any newly introduced version until it is reviewed.
    compatibility_pin_rationale = (
        "crate2nix omits package.rust-version, so these reviewed values restore "
        "the MSRV metadata required by the package override."
    )
    compatibility_pins: ClassVar[dict[str, str]] = {
        "bitcoinInternals.0.5.0": "1.74.0",
        "bitcoinInternals.0.6.0": "1.74.0",
    }

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist exact crate compatibility metadata with the flake source."""
        return SourceEntry(
            version=info.version,
            hashes=HashCollection.from_value(hashes),
            input=self._input,
            commit=info.commit,
            pins=self.compatibility_pins,
        )

    async def fetch_latest(
        self,
        session: aiohttp.ClientSession,
    ) -> VersionInfo:
        """Resolve the package version from the locked ``v<version>`` ref."""
        _ = session
        node = self._resolve_flake_node(VersionInfo(version="ignored"))
        ref = node.original.ref if node.original is not None else None
        if not isinstance(ref, str) or not ref.startswith("v") or len(ref) == 1:
            msg = "goose flake input must be pinned to a v<version> ref"
            raise RuntimeError(msg)
        commit = node.locked.rev if node.locked is not None else None
        return VersionInfo(
            version=ref.removeprefix("v"),
            metadata=FlakeInputMetadata(node=node, commit=commit),
        )
