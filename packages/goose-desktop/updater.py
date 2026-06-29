"""Updater for Goose desktop's pinned pnpm dependency cache."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, ClassVar

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry, SourceHashes
from lib.update import sources as update_sources
from lib.update.nix import _build_flake_attr_expr
from lib.update.paths import local_flake_url, sources_file_for
from lib.update.updaters._base_proxy import base_module as _base_module
from lib.update.updaters.base import (
    HashEntryUpdater,
    VersionInfo,
    register_updater,
)

if TYPE_CHECKING:
    import aiohttp

    from lib.update.events import EventStream
    from lib.update.updaters.base import UpdateContext


@register_updater
class GooseDesktopUpdater(HashEntryUpdater):
    """Hash Goose desktop dependencies from the overlay-managed Goose source."""

    DARWIN_PLATFORM: ClassVar[str] = "aarch64-darwin"

    name = "goose-desktop"
    companion_of = "goose-cli"
    supported_platforms = (DARWIN_PLATFORM,)

    def _dependency_hash_override_env(self, version: str) -> dict[str, str]:
        payload = {
            self.name: {
                "version": version,
                "hashes": [
                    {
                        "hashType": "nodeModulesHash",
                        "hash": self.config.fake_hash,
                        "platform": self.DARWIN_PLATFORM,
                    }
                ],
            },
        }
        return {"UPDATE_SOURCE_OVERRIDES_JSON": json.dumps(payload)}

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Use the Goose CLI source version that provides the desktop source."""
        _ = session
        source_file = sources_file_for("goose-cli")
        if source_file is None:
            msg = "goose-cli sources.json was not found"
            raise RuntimeError(msg)
        entry = update_sources.load_source_entry(source_file)
        if not entry.version:
            msg = "goose-cli sources.json is missing a pinned version"
            raise RuntimeError(msg)
        return VersionInfo(version=entry.version)

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Compute the desktop pnpm dependency cache hash directly."""
        _ = (info, session, context)
        hash_stream = _base_module().compute_fixed_output_hash(
            self.name,
            _build_flake_attr_expr(
                local_flake_url(),
                "packages",
                self.DARWIN_PLATFORM,
                self.name,
                "pnpmDeps",
                quoted_indices=(1, 2),
            ),
            env=self._dependency_hash_override_env(info.version),
            config=self.config,
        )
        async for event in self._emit_single_hash_entry(
            hash_stream,
            error="Missing nodeModulesHash output",
            hash_type="nodeModulesHash",
        ):
            yield event

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist the Goose source version with a platform-specific dependency hash."""
        hash_collection = HashCollection.from_value(hashes)
        if hash_collection.entries is None:
            msg = "goose-desktop updater expected structured hash entries"
            raise RuntimeError(msg)
        return SourceEntry(
            version=info.version,
            hashes=HashCollection.from_value([
                HashEntry.create(
                    "nodeModulesHash",
                    hash_entry.hash,
                    platform=self.DARWIN_PLATFORM,
                )
                for hash_entry in hash_collection.entries
            ]),
        )
