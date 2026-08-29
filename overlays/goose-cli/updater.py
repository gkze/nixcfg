"""Updater for Goose's locked source metadata and crate2nix artifacts."""

from typing import TYPE_CHECKING

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
