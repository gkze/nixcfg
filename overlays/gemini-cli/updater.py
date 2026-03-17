"""Updater for gemini-cli source and npm dependency hashes."""

from __future__ import annotations

import json
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
from lib.update.nix import (
    _build_fetch_from_github_expr,
    _build_overlay_expr,
    compute_fixed_output_hash,
)
from lib.update.updaters.base import UpdateContext, VersionInfo, register_updater
from lib.update.updaters.github_release import GitHubReleaseUpdater

if TYPE_CHECKING:
    import aiohttp


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

    @staticmethod
    def _override_env(version: str, src_hash: str, fake_hash: str) -> dict[str, str]:
        payload = {
            "gemini-cli": {
                "version": version,
                "hashes": [
                    {"hashType": "srcHash", "hash": src_hash},
                    {"hashType": "npmDepsHash", "hash": fake_hash},
                ],
            },
        }
        return {"UPDATE_SOURCE_OVERRIDES_JSON": json.dumps(payload)}

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | object | None = None,
    ) -> EventStream:
        """Compute source and npm dependency fixed-output hashes."""
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

        npm_deps_hash_drain = ValueDrain[str]()
        async for event in drain_value_events(
            compute_fixed_output_hash(
                self.name,
                _build_overlay_expr(self.name),
                env=self._override_env(info.version, src_hash, self.config.fake_hash),
                config=self.config,
            ),
            npm_deps_hash_drain,
            parse=expect_str,
        ):
            yield event
        npm_deps_hash = require_value(
            npm_deps_hash_drain,
            "Missing npmDepsHash output",
        )

        hashes: SourceHashes = [
            HashEntry.create("srcHash", src_hash),
            HashEntry.create("npmDepsHash", npm_deps_hash),
        ]
        yield UpdateEvent.value(self.name, hashes)
