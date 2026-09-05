"""Updater for Codex flake metadata and its crate2nix artifacts."""

from typing import TYPE_CHECKING

from lib.update.updaters import Crate2NixMetadataUpdater, register_updater

if TYPE_CHECKING:
    from lib.nix.models.sources import SourceEntry, SourceHashes


@register_updater
class CodexUpdater(Crate2NixMetadataUpdater):
    """Track Codex's flake input and generated crate graph."""

    name = "codex"

    @staticmethod
    def _preserved_source_hashes(source: SourceEntry | None) -> SourceHashes:
        """Discard legacy WebRTC hashes after that dependency left the graph."""
        _ = source
        return []
