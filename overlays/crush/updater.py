"""Updater for crush source and Go vendor hashes."""

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
class CrushUpdater(GitHubReleaseUpdater):
    """Resolve latest crush tag and compute src/vendor fixed-output hashes."""

    name = "crush"
    GITHUB_OWNER = "charmbracelet"
    GITHUB_REPO = "crush"

    @staticmethod
    def _src_expr(version: str) -> str:
        return _build_fetch_from_github_expr(
            "charmbracelet",
            "crush",
            tag=f"v{version}",
        )

    @staticmethod
    def _override_env(version: str, src_hash: str, fake_hash: str) -> dict[str, str]:
        payload = {
            "crush": {
                "version": version,
                "hashes": [
                    {"hashType": "srcHash", "hash": src_hash},
                    {"hashType": "vendorHash", "hash": fake_hash},
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
        """Compute source and vendor fixed-output hashes for the release."""
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

        vendor_hash_drain = ValueDrain[str]()
        async for event in drain_value_events(
            compute_fixed_output_hash(
                self.name,
                _build_overlay_expr(self.name),
                env=self._override_env(info.version, src_hash, self.config.fake_hash),
                config=self.config,
            ),
            vendor_hash_drain,
            parse=expect_str,
        ):
            yield event
        vendor_hash = require_value(vendor_hash_drain, "Missing vendorHash output")

        hashes: SourceHashes = [
            HashEntry.create("srcHash", src_hash),
            HashEntry.create("vendorHash", vendor_hash),
        ]
        yield UpdateEvent.value(self.name, hashes)
