"""Updater for goose-cli source hashes.

This updater intentionally only touches the upstream Goose source hash in
sources.json. crate2nix artifacts (Cargo.nix, crate-hashes, path normalization,
V8 lock patching, and crate overrides) are maintained separately; see
overlays/goose-cli/README.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.nix.models.sources import HashEntry, SourceHashes
from lib.update.events import (
    EventStream,
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    expect_str,
    require_value,
)
from lib.update.nix import _build_fetch_from_github_expr, compute_fixed_output_hash
from lib.update.updaters.base import UpdateContext, VersionInfo, register_updater
from lib.update.updaters.github_release import GitHubReleaseUpdater

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry


@register_updater
class GooseCliUpdater(GitHubReleaseUpdater):
    """Resolve the latest Goose release and compute its source hash."""

    name = "goose-cli"
    GITHUB_OWNER = "block"
    GITHUB_REPO = "goose"

    @staticmethod
    def _src_expr(version: str) -> str:
        return _build_fetch_from_github_expr(
            "block",
            "goose",
            tag=f"v{version}",
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Compute the fixed-output source hash for Goose."""
        _ = (session, context)

        src_hash_drain = ValueDrain[str]()
        async for event in drain_value_events(
            compute_fixed_output_hash(
                self.name,
                self._src_expr(info.version),
                config=self.config,
            ),
            src_hash_drain,
            parse=expect_str,
        ):
            yield event
        src_hash = require_value(src_hash_drain, "Missing srcHash output")

        hashes: SourceHashes = [HashEntry.create("srcHash", src_hash)]
        yield UpdateEvent.value(self.name, hashes)
