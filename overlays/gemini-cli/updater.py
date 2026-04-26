"""Updater for gemini-cli source and npm dependency hashes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.update.nix import _build_fetch_from_github_expr, _build_overlay_expr
from lib.update.updaters.base import (
    UpdateContext,
    VersionInfo,
    register_updater,
    stream_source_then_overlay_hashes,
)
from lib.update.updaters.github_release import GitHubReleaseUpdater

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry
    from lib.update.events import EventStream


@register_updater
class GeminiCliUpdater(GitHubReleaseUpdater):
    """Resolve latest gemini-cli tag and compute src/npm fixed-output hashes."""

    name = "gemini-cli"
    GITHUB_OWNER = "google-gemini"
    GITHUB_REPO = "gemini-cli"

    @staticmethod
    def _src_expr(version: str) -> str:
        return _build_fetch_from_github_expr(
            "google-gemini",
            "gemini-cli",
            tag=f"v{version}",
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Compute source and npm dependency fixed-output hashes."""
        _ = (session, context)

        async for event in stream_source_then_overlay_hashes(
            self.name,
            version=info.version,
            src_expr=self._src_expr(info.version),
            overlay_expr=_build_overlay_expr(self.name),
            dependency_hash_type="npmDepsHash",
            config=self.config,
        ):
            yield event
