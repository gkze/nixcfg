"""Updater for T3 Code's staged runtime Bun cache."""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING, Literal

from lib.update.generated_artifact_commands import stream_command_materialized_artifacts
from lib.update.nix import _build_overlay_attr_expr
from lib.update.paths import REPO_ROOT
from lib.update.updaters._base_proxy import base_module as _base_module
from lib.update.updaters.base import (
    UpdateContext,
    VersionInfo,
    _coerce_context,
    register_updater,
)
from lib.update.updaters.flake_backed import FlakeInputHashUpdater

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry
    from lib.update.events import EventStream

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


@register_updater
class T3CodeUpdater(FlakeInputHashUpdater):
    """Compute only the standalone T3 Code runtime ``node_modules`` hash."""

    name = "t3code"
    input_name = "t3code"
    hash_type: Literal["nodeModulesHash"] = "nodeModulesHash"
    generated_artifact_files = ("bun.lock", "../t3code-desktop/bun.lock")
    materialize_when_current = True
    shows_materialize_artifacts_phase = True
    platform_specific = True
    supported_platforms = ("aarch64-darwin",)

    @classmethod
    def _node_modules_expr(cls, *, system: str | None = None) -> str:
        """Return the package-path expression for this package's FOD."""
        return _build_overlay_attr_expr(cls.name, ".node_modules", system=system)

    def _compute_hash_for_system(
        self,
        info: VersionInfo,
        *,
        system: str | None,
    ) -> EventStream:
        """Hash the runtime cache directly so workspace FODs cannot pollute it."""
        _ = info
        return _base_module().compute_fixed_output_hash(
            self.name,
            self._node_modules_expr(system=system),
            env={"FAKE_HASHES": "1"},
            config=self.config,
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Refresh runtime Bun locks before probing the staged runtime hash."""
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
