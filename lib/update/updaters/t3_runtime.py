"""Shared updater behavior for T3 Code runtime Bun caches."""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING, Literal

from lib.update.generated_artifact_commands import stream_command_materialized_artifacts
from lib.update.paths import REPO_ROOT
from lib.update.updaters.core import _coerce_context
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
_RUNTIME_LOCK_DETAIL = "T3 runtime Bun locks"


def _runtime_lock_command() -> list[str]:
    repo_root = shlex.quote(str(REPO_ROOT))
    return [
        "sh",
        "-c",
        f"cd {repo_root} && nix run .#t3code-desktop.passthru.updateRuntimeLocks",
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

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Refresh shared runtime locks before probing this package's cache."""
        context = _coerce_context(context)
        async for event in stream_command_materialized_artifacts(
            self.name,
            args=_runtime_lock_command(),
            artifact_paths=_RUNTIME_LOCK_ARTIFACTS,
            inner=super().fetch_hashes(info, session, context=context),
            dry_run=context.dry_run,
            config=self.config,
            detail=_RUNTIME_LOCK_DETAIL,
        ):
            yield event


__all__ = ["T3RuntimeUpdater"]
