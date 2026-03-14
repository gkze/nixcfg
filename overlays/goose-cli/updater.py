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
from lib.update.net import fetch_github_api
from lib.update.nix import _build_fetch_from_github_expr, compute_fixed_output_hash
from lib.update.updaters.base import Updater, VersionInfo

if TYPE_CHECKING:
    import aiohttp


class GooseCliUpdater(Updater):
    """Resolve the latest Goose release and compute its source hash."""

    name = "goose-cli"
    GITHUB_OWNER = "block"
    GITHUB_REPO = "goose"

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Return latest upstream release version metadata."""
        payload = await fetch_github_api(
            session,
            f"repos/{self.GITHUB_OWNER}/{self.GITHUB_REPO}/releases/latest",
            config=self.config,
        )
        if not isinstance(payload, dict):
            msg = f"Unexpected release payload type: {type(payload).__name__}"
            raise TypeError(msg)
        tag_name = payload.get("tag_name")
        if not isinstance(tag_name, str) or not tag_name:
            msg = f"Missing tag_name in release payload: {payload!r}"
            raise RuntimeError(msg)
        version = tag_name.removeprefix("v")
        return VersionInfo(version=version, metadata={"tag": tag_name})

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
    ) -> EventStream:
        """Compute the fixed-output source hash for Goose."""
        _ = session

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
