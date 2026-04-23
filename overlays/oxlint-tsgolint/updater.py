"""Updater for oxlint-tsgolint source and vendor hashes."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry

from lib.nix.models.sources import HashCollection, HashEntry, SourceHashes
from lib.update.events import (
    EventStream,
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    expect_str,
    require_value,
)
from lib.update.nix import (
    _build_fetchgit_expr,
    _build_overlay_expr,
    compute_fixed_output_hash,
)
from lib.update.updaters.base import UpdateContext, VersionInfo, register_updater
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

    @staticmethod
    def _override_env(version: str, src_hash: str, fake_hash: str) -> dict[str, str]:
        payload = {
            "oxlint-tsgolint": {
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
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Compute source and vendor hashes for the latest released backend."""
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
