"""Shared updater abstractions for artifact materialization phases."""

from typing import TYPE_CHECKING, ClassVar

from lib.update.crate2nix import TARGETS, stream_crate2nix_artifact_updates
from lib.update.derivation_validation import DerivationValidation
from lib.update.events import EventStream, UpdateEvent
from lib.update.updaters.core import _coerce_context
from lib.update.updaters.flake_backed import FlakeInputMetadataUpdater

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry, SourceHashes
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

    @classmethod
    def get_derivation_validations(cls) -> tuple[DerivationValidation, ...]:
        """Validate final package assembly on each registered target platform."""
        target = TARGETS.get(cls.name)
        if target is None:
            return ()
        return (
            DerivationValidation(
                installable=".#pkgs.{system}.{name}.drvPath",
                systems=target.supported_platforms,
            ),
        )

    async def stream_materialized_artifacts(
        self,
        *,
        source_overrides: dict[str, SourceEntry] | None = None,
    ) -> EventStream:
        """Emit crate2nix artifact events using the standard materialization phase."""
        stream = (
            stream_crate2nix_artifact_updates(
                self.name,
                operation=self.artifact_operation,
                source_overrides=source_overrides,
            )
            if source_overrides is not None
            else stream_crate2nix_artifact_updates(
                self.name,
                operation=self.artifact_operation,
            )
        )
        async for event in stream:
            yield event


class Crate2NixMetadataUpdater(Crate2NixArtifactsMixin, FlakeInputMetadataUpdater):
    """Metadata-only flake updater that also refreshes crate2nix artifacts."""

    @staticmethod
    def _preserved_source_hashes(source: SourceEntry | None) -> SourceHashes:
        """Return persisted hashes in the structured updater representation."""
        if source is None:
            return []
        if source.hashes.entries is not None:
            return list(source.hashes.entries)
        return dict(source.hashes.mapping or {})

    def materialization_source_overrides(
        self,
        info: VersionInfo,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> dict[str, SourceEntry]:
        """Build the complete candidate source consumed during materialization."""
        current = _coerce_context(context).current
        hashes = self._preserved_source_hashes(current)
        return {self.name: self.build_result(info, hashes)}

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        """Compare metadata while retaining any independently persisted hashes."""
        current = _coerce_context(context).current
        if current is None:
            return False
        expected = self.materialization_source_overrides(
            info,
            context=current,
        )[self.name]
        return current.equivalent_to(expected)

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Refresh crate2nix artifacts without changing source hashes."""
        _ = session
        source_overrides = self.materialization_source_overrides(
            info,
            context=context,
        )

        async for event in self.stream_materialized_artifacts(
            source_overrides=source_overrides
        ):
            yield event

        yield UpdateEvent.value(
            self.name,
            self._preserved_source_hashes(source_overrides[self.name]),
        )


__all__ = [
    "Crate2NixArtifactsMixin",
    "Crate2NixMetadataUpdater",
    "MaterializesArtifactsMixin",
]
