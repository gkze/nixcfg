"""Shared updater abstractions for artifact materialization phases."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from lib.update.crate2nix import stream_crate2nix_artifact_updates
from lib.update.events import EventStream, UpdateEvent
from lib.update.updaters.flake_backed import FlakeInputMetadataUpdater

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry
    from lib.update.updaters.core import UpdateContext
    from lib.update.updaters.metadata import VersionInfo


class MaterializesArtifactsMixin:
    """Mixin for updaters that expose a dedicated artifact materialization phase."""

    name: ClassVar[str]
    materialize_when_current: ClassVar[bool] = True
    shows_materialize_artifacts_phase: ClassVar[bool] = True
    artifact_operation: ClassVar[str] = "materialize_artifacts"


class Crate2NixArtifactsMixin(MaterializesArtifactsMixin):
    """Mixin for updaters that materialize checked-in crate2nix artifacts."""

    async def stream_materialized_artifacts(self) -> EventStream:
        """Emit crate2nix artifact events using the standard materialization phase."""
        async for event in stream_crate2nix_artifact_updates(
            self.name,
            operation=self.artifact_operation,
        ):
            yield event


class Crate2NixMetadataUpdater(Crate2NixArtifactsMixin, FlakeInputMetadataUpdater):
    """Metadata-only flake updater that also refreshes crate2nix artifacts."""

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Refresh crate2nix artifacts without changing source hashes."""
        _ = (info, session, context)

        async for event in self.stream_materialized_artifacts():
            yield event

        yield UpdateEvent.value(self.name, [])


__all__ = [
    "Crate2NixArtifactsMixin",
    "Crate2NixMetadataUpdater",
    "MaterializesArtifactsMixin",
]
