"""Shared updater behavior for T3 Code runtime Bun caches."""

from typing import TYPE_CHECKING, Literal

from lib.update.generated_artifact_commands import stream_command_materialized_artifacts
from lib.update.nix import _build_package_path_attr_expr
from lib.update.updaters.flake_backed import FlakeInputHashUpdater

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry
    from lib.update.events import EventStream
    from lib.update.updaters import UpdateContext, VersionInfo

_RUNTIME_LOCK_ARTIFACTS = (
    "packages/t3code/bun.lock",
    "packages/t3code-desktop/bun.lock",
)
_RUNTIME_LOCK_SOURCES = ("t3code", "t3code-desktop")
_RUNTIME_LOCK_DETAIL = "T3 runtime Bun locks"


def _runtime_lock_command(
    source_overrides: dict[str, SourceEntry] | None,
) -> list[str]:
    return [
        "nix",
        "run",
        "--impure",
        "--expr",
        _build_package_path_attr_expr(
            "t3code-desktop",
            ".passthru.updateRuntimeLocks",
            source_overrides=source_overrides,
            fake_hashes=True if source_overrides is not None else None,
        ),
    ]


class T3RuntimeUpdater(FlakeInputHashUpdater):
    """Compute one T3 runtime cache hash after refreshing shared Bun locks."""

    input_name = "t3code"
    hash_type: Literal["nodeModulesHash"] = "nodeModulesHash"
    hash_attr_path = ".node_modules"
    materialize_when_current = True
    shows_materialize_artifacts_phase = True
    platform_specific = True
    supported_platforms = ("aarch64-darwin",)

    def _runtime_lock_source_overrides(
        self,
        info: VersionInfo,
        context: UpdateContext,
    ) -> dict[str, SourceEntry] | None:
        """Return the shared-lock sources from one coherent update wave."""
        source_overrides = {
            name: context.effective_sources[name]
            for name in _RUNTIME_LOCK_SOURCES
            if name in context.effective_sources
        }
        if self.source_pins_for(info) is None and not source_overrides:
            return None

        current = source_overrides.get(self.name) or context.current
        candidate = self.build_result(info, [])
        if current is not None:
            candidate = current.model_copy(
                update={
                    "drv_hash": None,
                    "input": candidate.input,
                    "pins": candidate.pins,
                    "version": candidate.version,
                }
            )
        source_overrides[self.name] = candidate
        return source_overrides

    async def _candidate_update_stream(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext,
    ) -> EventStream:
        """Keep shared runtime locks materialized through finalization."""
        source_overrides = self._runtime_lock_source_overrides(info, context)
        context.drv_fingerprint = None
        async for event in stream_command_materialized_artifacts(
            self.name,
            args=_runtime_lock_command(source_overrides),
            artifact_paths=_RUNTIME_LOCK_ARTIFACTS,
            inner=super()._candidate_update_stream(
                info,
                session,
                context=context,
            ),
            dry_run=context.dry_run,
            config=self.config,
            detail=_RUNTIME_LOCK_DETAIL,
        ):
            yield event


__all__ = ["T3RuntimeUpdater"]
