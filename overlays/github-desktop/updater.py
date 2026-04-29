"""Updater for the GitHub Desktop beta overlay."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry, SourceHashes
from lib.update.events import UpdateEvent, ValueDrain, drain_value_events, require_value
from lib.update.nix import _build_overlay_attr_expr
from lib.update.updaters.base import (
    FlakeInputUpdater,
    UpdateContext,
    VersionInfo,
    _coerce_context,
    compute_drv_fingerprint,
    compute_fixed_output_hash,
    expect_str,
    get_flake_input_node,
    get_flake_input_version,
    register_updater,
)
from lib.update.updaters.metadata import FlakeInputMetadata

if TYPE_CHECKING:
    import aiohttp

    from lib.update.events import EventStream

type GitHubDesktopHashType = Literal["yarnRootHash", "yarnAppHash"]

_RELEASE_PREFIX = "release-"
_TAG_REF_PREFIX = "refs/tags/"


def _version_from_release_ref(ref: str) -> str:
    tag = ref.removeprefix(_TAG_REF_PREFIX)
    if not tag.startswith(_RELEASE_PREFIX):
        msg = f"Expected GitHub Desktop release ref, got {ref!r}"
        raise RuntimeError(msg)
    version = tag.removeprefix(_RELEASE_PREFIX)
    if not version:
        msg = f"Empty GitHub Desktop version in ref {ref!r}"
        raise RuntimeError(msg)
    return version


@register_updater
class GitHubDesktopUpdater(FlakeInputUpdater):
    """Track the GitHub Desktop beta flake input and its Yarn caches."""

    name = "github-desktop"
    input_name = "github-desktop"
    _CACHE_ATTRS: ClassVar[tuple[tuple[GitHubDesktopHashType, str], ...]] = (
        ("yarnRootHash", ".cacheRoot"),
        ("yarnAppHash", ".cacheApp"),
    )
    _REQUIRED_HASH_TYPES: ClassVar[set[str]] = {
        hash_type for hash_type, _attr in _CACHE_ATTRS
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve the currently locked beta tag from the flake input."""
        _ = session
        node = get_flake_input_node(self._input)
        ref = get_flake_input_version(node)
        commit = node.locked.rev if node.locked is not None else None
        return VersionInfo(
            version=_version_from_release_ref(ref),
            metadata=FlakeInputMetadata(node=node, commit=commit),
        )

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist the cache hashes with the backing flake input name."""
        return SourceEntry(
            version=info.version,
            hashes=HashCollection.from_value(hashes),
            input=self._input,
        )

    @classmethod
    def _has_required_hashes(cls, entry: SourceEntry) -> bool:
        if entry.hashes.entries is None:
            return False
        present = {
            hash_entry.hash_type
            for hash_entry in entry.hashes.entries
            if hash_entry.platform is None
        }
        return present >= cls._REQUIRED_HASH_TYPES

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        """Require current version, cache hashes, input name, and drv fingerprint."""
        update_context = _coerce_context(context)
        current = update_context.current
        if (
            current is None
            or current.version != info.version
            or current.input != self._input
            or current.drv_hash is None
            or not self._has_required_hashes(current)
        ):
            return False
        try:
            drv_hash = await compute_drv_fingerprint(self.name, config=self.config)
        except RuntimeError:
            return False
        update_context.drv_fingerprint = drv_hash
        return current.drv_hash == drv_hash

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Compute the two fixed-output Yarn caches."""
        _ = (info, session, _coerce_context(context))
        entries: list[HashEntry] = []
        for hash_type, attr_path in self._CACHE_ATTRS:
            hash_drain = ValueDrain[str]()
            async for event in drain_value_events(
                compute_fixed_output_hash(
                    self.name,
                    _build_overlay_attr_expr(self.name, attr_path),
                    env={"FAKE_HASHES": "1"},
                    config=self.config,
                ),
                hash_drain,
                parse=expect_str,
            ):
                yield event
            hash_value = require_value(hash_drain, f"Missing {hash_type} output")
            entries.append(HashEntry.create(hash_type, hash_value))
        yield UpdateEvent.value(self.name, entries)

    async def _finalize_result(
        self,
        result: SourceEntry,
        *,
        info: VersionInfo | None = None,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Attach a fake-hash drv fingerprint for precise staleness checks."""
        _ = info
        update_context = _coerce_context(context)
        yield UpdateEvent.status(
            self.name,
            "Computing derivation fingerprint...",
            operation="compute_hash",
            status="computing_hash",
            detail="derivation fingerprint",
        )
        try:
            drv_hash = update_context.drv_fingerprint
            if drv_hash is None:
                drv_hash = await compute_drv_fingerprint(
                    self.name,
                    config=self.config,
                )
            result = result.model_copy(update={"drv_hash": drv_hash})
        except RuntimeError as exc:
            yield UpdateEvent.status(
                self.name,
                f"Warning: derivation fingerprint unavailable ({exc})",
                operation="compute_hash",
            )
        yield UpdateEvent.value(self.name, result)
