"""Updater for oxlint-tsgolint source and vendor hashes."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry
    from lib.update.events import EventStream

from lib.nix.models.sources import HashCollection
from lib.update.nix import _build_fetchgit_expr, _build_overlay_expr
from lib.update.updaters.base import (
    UpdateContext,
    VersionInfo,
    register_updater,
    stream_source_then_overlay_hashes,
)
from lib.update.updaters.github_release import GitHubReleaseUpdater


@register_updater
class OxlintTsgolintUpdater(GitHubReleaseUpdater):
    """Resolve tsgolint releases and refresh the checked-in Nix source hashes."""

    name = "oxlint-tsgolint"
    GITHUB_OWNER = "oxc-project"
    GITHUB_REPO = "tsgolint"

    @staticmethod
    def _src_expr(version: str) -> str:
        return _build_fetchgit_expr(
            "https://github.com/oxc-project/tsgolint.git",
            f"v{version}",
            fetch_submodules=True,
        )

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        """Treat fake source hashes as stale so placeholder entries rehash once."""
        if isinstance(context, UpdateContext):
            update_context = context
        else:
            update_context = UpdateContext(current=context)
        current = update_context.current
        if current is None or current.version != info.version:
            return False

        hashes = current.hashes
        entries = hashes.entries
        if entries is not None:
            if not entries:
                return False
            return not any(
                entry.hash.startswith(HashCollection.FAKE_HASH_PREFIX)
                for entry in entries
            )

        mapping = hashes.mapping
        if mapping is not None:
            if not mapping:
                return False
            return not any(
                hash_value.startswith(HashCollection.FAKE_HASH_PREFIX)
                for hash_value in mapping.values()
            )

        return False

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Compute source and vendor hashes for the latest released backend."""
        _ = (session, context)

        async for event in stream_source_then_overlay_hashes(
            self.name,
            version=info.version,
            src_expr=self._src_expr(info.version),
            overlay_expr=_build_overlay_expr(self.name),
            dependency_hash_type="vendorHash",
            config=self.config,
        ):
            yield event
