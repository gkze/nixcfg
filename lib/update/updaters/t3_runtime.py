"""Shared updater behavior for T3 Code runtime Bun caches."""

import hashlib
import json
from functools import partial
from typing import TYPE_CHECKING, Literal

from lib.update import events as update_events
from lib.update import paths as update_paths
from lib.update import process as update_process
from lib.update.events import EventSink, ignore_event
from lib.update.generated_artifact_commands import stream_command_materialized_artifacts
from lib.update.nix import _build_package_path_attr_expr
from lib.update.updaters.flake_backed import FlakeInputHashUpdater

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry
    from lib.update.config import UpdateConfig
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


async def _runtime_lock_key(
    source_overrides: dict[str, SourceEntry] | None,
    *,
    source: str,
    config: UpdateConfig,
) -> str:
    """Identify the generator and every repository file it reads at execution.

    The generator is owned by desktop: its derivation binds the common source,
    Bun, Python, desktop pin, and source revision. Standalone hash metadata is
    deliberately not part of that derivation, so both consumers share a result
    even while their independently computed hashes are published in sequence.
    """
    expression = _build_package_path_attr_expr(
        "t3code-desktop",
        ".passthru.updateRuntimeLocks.drvPath",
        source_overrides=source_overrides,
        fake_hashes=True if source_overrides is not None else None,
    )
    result = await update_process.run_command(
        ["nix", "eval", "--impure", "--raw", "--expr", expression],
        options=update_process.RunCommandOptions(source=source, config=config),
    )
    update_events.raise_failed_command("Resolve T3 runtime lock generator", result)
    generator = result.stdout.strip()
    if not generator:
        msg = "T3 runtime lock generator resolved to an empty derivation path"
        raise RuntimeError(msg)
    root = update_paths.get_repo_root()
    files = (
        *_RUNTIME_LOCK_ARTIFACTS,
        "packages/t3code-desktop/render_runtime_package_json.py",
    )
    identity = {
        "generator": generator,
        "files": {
            path: hashlib.sha256((root / path).read_bytes()).hexdigest()
            for path in files
        },
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


class T3RuntimeUpdater(FlakeInputHashUpdater):
    """Compute one T3 runtime cache hash after refreshing shared Bun locks."""

    input_name = "t3code"
    hash_type: Literal["nodeModulesHash"] = "nodeModulesHash"
    hash_attr_path = ".node_modules"
    materialize_when_current = True
    shows_materialize_artifacts_phase = True
    platform_specific = True
    supported_platforms = ("aarch64-darwin",)

    async def _is_latest(self, context: UpdateContext, info: VersionInfo) -> bool:
        """Report version freshness without evaluating locks that will be replaced."""
        # This only chooses the progress message: T3 always materializes locks,
        # hashes the candidate and fingerprints it before deciding what changed.
        return context.current is not None and context.current.version == info.version

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
        emit: EventSink = ignore_event,
    ) -> SourceEntry | None:
        """Keep shared runtime locks materialized through finalization."""
        source_overrides = self._runtime_lock_source_overrides(info, context)
        context.drv_fingerprint = None
        context.drv_fingerprints.clear()
        context.prepared_probes.clear()
        return await stream_command_materialized_artifacts(
            self.name,
            args=_runtime_lock_command(source_overrides),
            artifact_paths=_RUNTIME_LOCK_ARTIFACTS,
            inner=lambda: super(T3RuntimeUpdater, self)._candidate_update_stream(
                info, session, context=context, emit=emit
            ),
            config=self.config,
            detail=_RUNTIME_LOCK_DETAIL,
            materialization_key=partial(
                _runtime_lock_key,
                source_overrides,
                source=self.name,
                config=self.config,
            ),
            emit=emit,
        )


__all__ = ["T3RuntimeUpdater"]
