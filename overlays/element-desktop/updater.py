"""Updater for pinned element-desktop source and offline cache hashes."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry
    from lib.update.events import EventStream

from lib.update.nix import (
    _build_fetch_from_github_call,
    _build_fetch_from_github_expr,
    _build_fetch_yarn_deps_expr,
)
from lib.update.updaters.base import (
    FixedOutputHashStep,
    HashEntryUpdater,
    UpdateContext,
    VersionInfo,
    read_pinned_source_version,
    register_updater,
    stream_fixed_output_hashes,
)
from lib.update.updaters.metadata import NO_METADATA


@register_updater
class ElementDesktopUpdater(HashEntryUpdater):
    """Refresh hashes for the currently pinned element-desktop release."""

    name = "element-desktop"

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Read the pinned version from this package's ``sources.json``."""
        _ = session
        version = read_pinned_source_version(self.name)
        return VersionInfo(version=version, metadata=NO_METADATA)

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        """Recompute hashes for pinned versions before equality checks."""
        _ = (context, info)
        return False

    @staticmethod
    def _src_expr(version: str) -> str:
        return _build_fetch_from_github_expr(
            "element-hq",
            "element-desktop",
            rev=f"v{version}",
        )

    @staticmethod
    def _offline_expr(version: str, src_hash: str) -> str:
        src_expr = _build_fetch_from_github_call(
            "element-hq",
            "element-desktop",
            rev=f"v{version}",
            hash_value=src_hash,
        )
        return _build_fetch_yarn_deps_expr(src_expr)

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Compute srcHash first, then sha256 for fetchYarnDeps offline cache."""
        _ = (session, context)

        async for event in stream_fixed_output_hashes(
            self.name,
            steps=(
                FixedOutputHashStep(
                    hash_type="srcHash",
                    error="Missing srcHash output",
                    expr=lambda _resolved: self._src_expr(info.version),
                ),
                FixedOutputHashStep(
                    hash_type="sha256",
                    error="Missing sha256 output",
                    expr=lambda resolved: self._offline_expr(
                        info.version,
                        resolved["srcHash"],
                    ),
                ),
            ),
            config=self.config,
        ):
            yield event
