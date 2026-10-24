"""Bun node_modules updaters whose installer is the manifest's exact Bun release.

A Bun lockfile is only reproducible under the Bun release that wrote it, and
``bun install --frozen-lockfile`` rejects a lockfile another release would
re-resolve. The source manifest's ``packageManager`` is the authority for that
release; ``lib/exact-bun.nix`` asserts the persisted pin equals it, so these
updaters persist the pin and one ``bunRuntimeHash`` per supported system.
"""

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, override

from lib.nix.models.flake_lock import (
    FlakeLockNode,  # noqa: TC001 -- candidate metadata is validated at runtime
)
from lib.nix.models.sources import HashCollection, HashEntry
from lib.system_policy import supported_systems
from lib.update import process as update_process
from lib.update.bun_toolchain import bun_release_urls, bun_runtime_hash_entries
from lib.update.events import EventSink, ignore_event
from lib.update.updaters.flake_backed import BunNodeModulesHashUpdater
from lib.update.updaters.metadata import MappingMetadata, VersionInfo

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry, SourceHashes
    from lib.update.updaters.core import UpdateContext

BUN_VERSION_PIN = "bunVersion"


@dataclass(frozen=True, slots=True, kw_only=True)
class ExactBunMetadata(MappingMetadata):
    """Immutable source identity and the exact Bun release its manifest pins."""

    node: FlakeLockNode
    commit: str
    bun_version: str
    bun_runtime_hashes: tuple[HashEntry, ...] = ()

    @override
    def to_dict(self) -> dict[str, object]:
        """Expose fields through the updater metadata compatibility mapping."""
        return {
            "node": self.node,
            "commit": self.commit,
            BUN_VERSION_PIN: self.bun_version,
        }


class ExactBunNodeModulesHashUpdater(BunNodeModulesHashUpdater):
    """Hash node_modules with the exact Bun release the source manifest pins.

    Subclasses resolve :class:`ExactBunMetadata` in ``fetch_latest``.
    """

    bun_runtime_systems: tuple[str, ...] = supported_systems()

    def exact_bun_metadata(self, info: VersionInfo) -> ExactBunMetadata:
        """Return the resolved source identity, failing closed when absent."""
        metadata = info.metadata
        if not isinstance(metadata, ExactBunMetadata):
            msg = f"{self.name} version metadata is missing its resolved release source"
            raise TypeError(msg)
        return metadata

    def with_bun_runtime_hashes(
        self,
        info: VersionInfo,
        hashes: list[HashEntry],
    ) -> VersionInfo:
        """Return *info* carrying already-hashed exact Bun runtime sources."""
        return VersionInfo(
            version=info.version,
            metadata=replace(
                self.exact_bun_metadata(info), bun_runtime_hashes=tuple(hashes)
            ),
        )

    def source_pins_for(self, info: VersionInfo) -> dict[str, str]:
        """Persist the source manifest's exact Bun runtime identity."""
        return {BUN_VERSION_PIN: self.exact_bun_metadata(info).bun_version}

    @override
    async def _is_latest(
        self,
        context: UpdateContext,
        info: VersionInfo,
    ) -> bool:
        """Fingerprint the package with its persisted exact Bun sources."""
        current = context.current
        if current is None or current.hashes.entries is None:
            return False
        bun_hashes = [
            entry
            for entry in current.hashes.entries
            if entry.hash_type == "bunRuntimeHash"
        ]
        expected_urls = bun_release_urls(
            self.exact_bun_metadata(info).bun_version,
            self.bun_runtime_systems,
        )
        if {(entry.platform, entry.url) for entry in bun_hashes} != set(
            expected_urls.items()
        ):
            return False
        return await super()._is_latest(
            context,
            self.with_bun_runtime_hashes(info, bun_hashes),
        )

    async def _fetch_bun_runtime_hashes(
        self,
        info: VersionInfo,
        *,
        emit: EventSink,
    ) -> list[HashEntry]:
        """Hash the exact Bun release archives for every supported system.

        The archives are fixed URLs, so any builder can hash every system.
        """
        bun_version = self.exact_bun_metadata(info).bun_version
        bun_urls = bun_release_urls(bun_version, self.bun_runtime_systems)
        hashes_by_url = await update_process.compute_url_hashes(
            self.name, bun_urls.values(), config=self.config, emit=emit
        )
        return bun_runtime_hash_entries(
            bun_version,
            self.bun_runtime_systems,
            hashes_by_url,
        )

    @override
    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext,
        emit: EventSink = ignore_event,
    ) -> SourceHashes:
        """Hash exact Bun runtimes before probing Bun's node_modules tree."""
        bun_hashes = await self._fetch_bun_runtime_hashes(info, emit=emit)
        node_hashes = await super().fetch_hashes(
            self.with_bun_runtime_hashes(info, bun_hashes),
            session,
            context=context,
            emit=emit,
        )
        if not isinstance(node_hashes, list):
            msg = f"{self.name} node_modules hashes must use structured hash entries"
            raise TypeError(msg)
        return [*bun_hashes, *node_hashes]

    @override
    async def _finalize_result(
        self,
        result: SourceEntry,
        *,
        info: VersionInfo | None = None,
        context: UpdateContext,
        emit: EventSink = ignore_event,
    ) -> SourceEntry:
        """Give a bumped pin its exact Bun runtimes before fingerprinting.

        The pin stage drops hashes on a version bump, but lib/exact-bun.nix
        cannot evaluate the package without one bunRuntimeHash per system.
        Without them the pin persisted no drvHash, and every partial native
        prepare then refused to preserve one (Update run 39760093470). The
        archives are platform-independent, so the pin stage hashes them here;
        native jobs still re-hash them alongside node_modules.
        """
        if context.pins_only and info is not None:
            if result.hashes.mapping:
                msg = f"{self.name} candidate hashes must use structured hash entries"
                raise TypeError(msg)
            entries = list(result.hashes.entries or ())
            if not any(entry.hash_type == "bunRuntimeHash" for entry in entries):
                bun_hashes = await self._fetch_bun_runtime_hashes(info, emit=emit)
                result = result.model_copy(
                    update={
                        "hashes": HashCollection.from_value([*bun_hashes, *entries])
                    }
                )
        return await super()._finalize_result(
            result, info=info, context=context, emit=emit
        )

    @override
    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist hashed Bun runtimes ahead of the node_modules hashes."""
        metadata = self.exact_bun_metadata(info)
        if metadata.bun_runtime_hashes:
            if not isinstance(hashes, list):
                msg = f"{self.name} candidate hashes must use structured hash entries"
                raise TypeError(msg)
            if any(entry.hash_type == "bunRuntimeHash" for entry in hashes):
                msg = f"{self.name} candidate contains duplicate Bun runtime hashes"
                raise RuntimeError(msg)
            hashes = [*metadata.bun_runtime_hashes, *hashes]
        return super().build_result(info, hashes)


__all__ = [
    "BUN_VERSION_PIN",
    "ExactBunMetadata",
    "ExactBunNodeModulesHashUpdater",
]
