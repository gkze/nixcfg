"""Updater for OpenCode desktop Cargo.lock importCargoLock hashes."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

if TYPE_CHECKING:
    import aiohttp

from libnix.models.sources import (
    HashCollection,
    HashEntry,
    HashMapping,
    SourceEntry,
    SourceHashes,
)
from update.events import (
    CapturedValue,
    EventStream,
    UpdateEvent,
    capture_stream_value,
)
from update.flake import get_flake_input_node, get_flake_input_version
from update.nix import compute_import_cargo_lock_output_hashes
from update.updaters.base import CargoLockGitDep, HashEntryUpdater, VersionInfo


class OpencodeDesktopCargoLockUpdater(HashEntryUpdater):
    """Compute importCargoLock output hashes for opencode desktop git deps."""

    name = "opencode-desktop"
    input_name = "opencode"
    required_tools = ("nix",)
    lockfile_path: ClassVar[str] = "packages/desktop/src-tauri/Cargo.lock"
    git_deps: ClassVar[list[CargoLockGitDep]] = [
        CargoLockGitDep("specta-2.0.0-rc.22", "spectaOutputHash", "specta"),
        CargoLockGitDep("tauri-2.9.5", "tauriOutputHash", "tauri"),
        CargoLockGitDep(
            "tauri-specta-2.0.0-rc.21",
            "tauriSpectaOutputHash",
            "tauri-specta",
        ),
    ]

    @property
    def _input(self) -> str:
        if self.input_name is None:
            msg = "Missing input name"
            raise RuntimeError(msg)
        return self.input_name

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Read latest flake input version/revision for opencode."""
        _ = session
        node = get_flake_input_node(self._input)
        version = get_flake_input_version(node)
        locked_rev = node.locked.rev if node.locked else None
        return VersionInfo(
            version=version,
            metadata={"node": node, "commit": locked_rev},
        )

    def _is_latest(self, current: SourceEntry | None, info: VersionInfo) -> bool:
        if current is None:
            return False
        upstream_rev = info.metadata.get("commit")
        if upstream_rev and current.commit:
            return current.commit == upstream_rev
        return False

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Build source entry with commit and structured hash entries."""
        return SourceEntry(
            hashes=HashCollection.from_value(hashes),
            input=self.input_name,
            commit=info.metadata.get("commit"),
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
    ) -> EventStream:
        """Compute all configured Cargo git dependency output hashes."""
        _ = info
        _ = session
        hashes: HashMapping | None = None
        async for item in capture_stream_value(
            compute_import_cargo_lock_output_hashes(
                self.name,
                self._input,
                lockfile_path=self.lockfile_path,
                git_deps=self.git_deps,
                config=self.config,
            ),
            error="Missing importCargoLock output hashes",
        ):
            if isinstance(item, CapturedValue):
                hashes = cast("HashMapping", item.captured)
            else:
                yield item
        if hashes is None:
            msg = "Missing importCargoLock output hashes"
            raise RuntimeError(msg)
        entries = []
        for dep in self.git_deps:
            hash_value = hashes.get(dep.git_dep)
            if not hash_value:
                msg = f"Missing hash for {dep.git_dep}"
                raise RuntimeError(msg)
            entries.append(
                HashEntry.create(
                    dep.hash_type,
                    hash_value,
                    git_dep=dep.git_dep,
                ),
            )
        yield UpdateEvent.value(self.name, entries)
