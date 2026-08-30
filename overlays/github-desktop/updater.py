"""Updater for the GitHub Desktop beta overlay."""

import re
from typing import TYPE_CHECKING, ClassVar, Literal, cast

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry, SourceHashes
from lib.update import flake as update_flake
from lib.update import nix as update_nix
from lib.update.events import (
    StatusInfo,
    StatusKind,
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    expect_str,
    require_value,
)
from lib.update.net import fetch_json, github_raw_url
from lib.update.nix import _build_overlay_attr_expr
from lib.update.updaters import (
    FlakeInputUpdater,
    UpdateContext,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.core import _coerce_context
from lib.update.updaters.metadata import require_metadata_str

if TYPE_CHECKING:
    import aiohttp

    from lib.update.events import EventStream

type GitHubDesktopHashType = Literal["yarnRootHash", "yarnAppHash"]

_RELEASE_PREFIX = "release-"
_TAG_REF_PREFIX = "refs/tags/"
_EXACT_ELECTRON_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


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

    @staticmethod
    def _electron_version(payload: object) -> str:
        if not isinstance(payload, dict):
            msg = "GitHub Desktop manifest requires an exact Electron version"
            raise TypeError(msg)
        dev_dependencies = cast("dict[str, object]", payload).get("devDependencies")
        if not isinstance(dev_dependencies, dict):
            msg = "GitHub Desktop manifest requires an exact Electron version"
            raise TypeError(msg)
        version = cast("dict[str, object]", dev_dependencies).get("electron")
        if (
            not isinstance(version, str)
            or _EXACT_ELECTRON_VERSION.fullmatch(version) is None
        ):
            msg = "GitHub Desktop manifest requires an exact Electron version"
            raise TypeError(msg)
        return version

    @staticmethod
    def _require_electron_version(info: VersionInfo) -> str:
        return require_metadata_str(
            info.metadata,
            "electronVersion",
            context="GitHub Desktop metadata",
        )

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve the locked beta tag and its exact Electron runtime."""
        node = update_flake.get_flake_input_node(self._input)
        ref = update_flake.get_flake_input_version(node)
        version = _version_from_release_ref(ref)
        commit = node.locked.rev if node.locked is not None else None
        if commit is None:
            msg = "GitHub Desktop flake input is missing an immutable commit"
            raise RuntimeError(msg)
        manifest = await fetch_json(
            session,
            github_raw_url("desktop", "desktop", commit, "package.json"),
            config=self.config,
        )
        return VersionInfo(
            version=version,
            metadata={
                "node": node,
                "commit": commit,
                "electronVersion": self._electron_version(manifest),
            },
        )

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist the cache hashes with the backing flake input name."""
        return SourceEntry(
            version=info.version,
            hashes=HashCollection.from_value(hashes),
            input=self._input,
            electron_version=self._require_electron_version(info),
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

    @staticmethod
    def _fingerprint_override(entry: SourceEntry) -> SourceEntry:
        """Keep candidate metadata while fingerprints use stable fake hashes."""
        return entry.model_copy(
            update={
                "drv_hash": None,
                "hashes": HashCollection(entries=[]),
            }
        )

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
            or current.electron_version != self._require_electron_version(info)
            or current.drv_hash is None
            or not self._has_required_hashes(current)
        ):
            return False
        try:
            drv_hash = await update_nix.compute_drv_fingerprint(
                self.name,
                config=self.config,
                source_overrides={
                    self.name: self._fingerprint_override(current),
                },
                fake_hashes=True,
            )
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
        _ = (session, _coerce_context(context))
        source_override = self.build_result(info, [])
        entries: list[HashEntry] = []
        for hash_type, attr_path in self._CACHE_ATTRS:
            hash_drain = ValueDrain[str]()
            async for event in drain_value_events(
                update_nix.compute_fixed_output_hash(
                    self.name,
                    _build_overlay_attr_expr(
                        self.name,
                        attr_path,
                        source_overrides={self.name: source_override},
                        fake_hashes=True,
                    ),
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
            status=StatusInfo(
                kind=StatusKind.COMPUTING_HASH,
                value="derivation fingerprint",
            ),
        )
        try:
            drv_hash = update_context.drv_fingerprint
            if drv_hash is None:
                drv_hash = await update_nix.compute_drv_fingerprint(
                    self.name,
                    config=self.config,
                    source_overrides={
                        self.name: self._fingerprint_override(result),
                    },
                    fake_hashes=True,
                )
            result = result.model_copy(update={"drv_hash": drv_hash})
        except RuntimeError as exc:
            yield UpdateEvent.status(
                self.name,
                f"Warning: derivation fingerprint unavailable ({exc})",
                operation="compute_hash",
            )
        yield UpdateEvent.value(self.name, result)
