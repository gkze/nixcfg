"""Updater for GitButler source metadata and crate2nix artifacts."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Literal

from lib.nix.models.sources import HashEntry
from lib.update import crate2nix as _crate2nix
from lib.update.events import (
    EventStream,
    UpdateEvent,
    UpdateEventKind,
    ValueDrain,
    drain_value_events,
    expect_str,
    require_value,
)
from lib.update.flake import flake_fetch_expression
from lib.update.nix import (
    _build_fetch_pnpm_deps_expr,
    _build_pnpm_10_nodejs_22_expr,
    compute_fixed_output_hash,
)
from lib.update.paths import get_repo_file
from lib.update.updaters.base import (
    Crate2NixArtifactsMixin,
    UpdateContext,
    VersionInfo,
    _coerce_context,
    register_updater,
)
from lib.update.updaters.flake_backed import FlakeInputHashUpdater
from lib.update.updaters.metadata import FlakeInputMetadata

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry


def _patch_installed_crate2nix_target(name: str) -> None:
    """Keep worktree updaters compatible with older installed nixcfg CLIs."""
    if hasattr(_crate2nix, "_local_flake_installable"):
        return
    target = _crate2nix.TARGETS.get(name)
    if target is None or not target.patched_src_installable.startswith("path:.#"):
        return
    attr = target.patched_src_installable.removeprefix("path:.#")
    _crate2nix.TARGETS[name] = replace(
        target,
        patched_src_installable=f"git+file://{get_repo_file('.').resolve()}?dirty=1#{attr}",
    )


_patch_installed_crate2nix_target("gitbutler")


@register_updater
class GitButlerUpdater(Crate2NixArtifactsMixin, FlakeInputHashUpdater):
    """Track the GitButler release input, pnpm cache, and crate2nix output."""

    name = "gitbutler"
    input_name = "gitbutler"
    hash_type: Literal["npmDepsHash"] = "npmDepsHash"
    supported_platforms = ("aarch64-darwin", "x86_64-linux")

    async def fetch_latest(
        self,
        session: aiohttp.ClientSession,
    ) -> VersionInfo:
        """Resolve the package version from the locked release ref."""
        _ = session
        node = self._resolve_flake_node(VersionInfo(version="ignored"))
        ref = node.original.ref if node.original is not None else None
        if not isinstance(ref, str) or not ref.startswith("release/"):
            msg = "gitbutler flake input must be pinned to a release/<version> ref"
            raise RuntimeError(msg)
        commit = node.locked.rev if node.locked is not None else None
        return VersionInfo(
            version=ref.removeprefix("release/"),
            metadata=FlakeInputMetadata(node=node, commit=commit),
        )

    def _compute_hash_for_system(
        self,
        info: VersionInfo,
        *,
        system: str | None,
    ) -> EventStream:
        """Hash GitButler's pnpm dependency cache for the locked source."""
        _ = system
        node = self._resolve_flake_node(info)
        source_expr = flake_fetch_expression(node)
        pnpm_expr = _build_fetch_pnpm_deps_expr(
            source_expr,
            pname=self.name,
            version=info.version,
            fetcher_version=3,
            pnpm=_build_pnpm_10_nodejs_22_expr(),
        )
        return compute_fixed_output_hash(
            self.name,
            pnpm_expr,
            config=self.config,
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Refresh crate2nix artifacts before computing the pnpm hash."""
        _ = session
        context = _coerce_context(context)
        async for event in self.stream_materialized_artifacts():
            if event.kind is UpdateEventKind.ARTIFACT:
                context.hashes_fully_computed = False
            yield event

        hash_drain = ValueDrain[str]()
        async for event in drain_value_events(
            self._compute_hash(info),
            hash_drain,
            parse=expect_str,
        ):
            yield event
        hash_value = require_value(hash_drain, "Missing npmDepsHash output")
        yield UpdateEvent.value(
            self.name,
            [HashEntry.create(self.hash_type, hash_value)],
        )
