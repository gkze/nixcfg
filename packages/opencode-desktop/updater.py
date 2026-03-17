"""Updater for opencode-desktop importCargoLock git dependency hashes."""

from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiohttp

from lib.nix.models.sources import (
    HashCollection,
    HashEntry,
    HashType,
    SourceEntry,
    SourceHashes,
)
from lib.update.events import EventStream, UpdateEvent, UpdateEventKind
from lib.update.net import fetch_url
from lib.update.nix_cargo import compute_import_cargo_lock_output_hashes
from lib.update.updaters.base import (
    CargoLockGitDep,
    FlakeInputUpdater,
    UpdateContext,
    VersionInfo,
    register_updater,
)


@register_updater
class OpencodeDesktopUpdater(FlakeInputUpdater):
    """Resolve Cargo git deps from upstream lockfile and refresh output hashes."""

    name = "opencode-desktop"
    input_name: str | None = "opencode"

    _LOCKFILE_PATH = "packages/desktop/src-tauri/Cargo.lock"
    _TARGET_DEPS: tuple[tuple[str, HashType], ...] = (
        ("specta", "spectaOutputHash"),
        ("tauri", "tauriOutputHash"),
        ("tauri-specta", "tauriSpectaOutputHash"),
    )

    @classmethod
    def _resolve_git_dep_keys(cls, lockfile_content: str) -> dict[str, str]:
        payload = tomllib.loads(lockfile_content)
        packages = payload.get("package")
        if not isinstance(packages, list):
            msg = "Cargo.lock is missing a top-level package array"
            raise TypeError(msg)

        keys_by_match: dict[str, str] = {}
        for package in packages:
            if not isinstance(package, dict):
                continue
            current_name = package.get("name")
            current_version = package.get("version")
            source = package.get("source")
            if not isinstance(current_name, str) or not isinstance(
                current_version, str
            ):
                continue
            if not isinstance(source, str) or not source.startswith("git+"):
                continue

            dep_key = f"{current_name}-{current_version}"
            for match_name, _hash_type in cls._TARGET_DEPS:
                if current_name != match_name:
                    continue
                existing = keys_by_match.get(match_name)
                if existing is None:
                    keys_by_match[match_name] = dep_key
                elif existing != dep_key:
                    msg = (
                        f"Multiple git dependency keys matched {match_name!r}: "
                        f"{existing!r}, {dep_key!r}"
                    )
                    raise RuntimeError(msg)

        missing = [
            match_name
            for match_name, _ in cls._TARGET_DEPS
            if match_name not in keys_by_match
        ]
        if missing:
            msg = f"Missing git dependencies in Cargo.lock: {missing}"
            raise RuntimeError(msg)

        return keys_by_match

    async def _fetch_lockfile_content(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
    ) -> str:
        node = self._resolve_flake_node(info)
        locked = node.locked
        owner = locked.owner if locked is not None else None
        repo = locked.repo if locked is not None else None
        rev = locked.rev if locked is not None else None
        if not all(isinstance(value, str) and value for value in (owner, repo, rev)):
            msg = "opencode flake input is missing owner/repo/rev metadata"
            raise RuntimeError(msg)

        lockfile_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{rev}/{self._LOCKFILE_PATH}"
        payload = await fetch_url(
            session,
            lockfile_url,
            request_timeout=self.config.default_timeout,
            config=self.config,
            user_agent=self.config.default_user_agent,
        )
        return payload.decode(errors="replace")

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Compute importCargoLock output hashes for specta/tauri git deps."""
        _ = context
        lockfile_content = await self._fetch_lockfile_content(info, session)
        keys_by_match = self._resolve_git_dep_keys(lockfile_content)

        git_deps = [
            CargoLockGitDep(
                git_dep=keys_by_match[match_name],
                hash_type=hash_type,
                match_name=keys_by_match[match_name],
            )
            for match_name, hash_type in self._TARGET_DEPS
        ]

        hashes_by_git_dep: dict[str, str] | None = None
        async for event in compute_import_cargo_lock_output_hashes(
            self.name,
            self._input,
            lockfile_path=self._LOCKFILE_PATH,
            git_deps=git_deps,
            lockfile_content=lockfile_content,
            config=self.config,
        ):
            if event.kind == UpdateEventKind.VALUE:
                payload = event.payload
                if not isinstance(payload, dict):
                    msg = (
                        f"Expected hash mapping from cargo updater, got {type(payload)}"
                    )
                    raise TypeError(msg)
                converted: dict[str, str] = {}
                for key, value in payload.items():
                    if not isinstance(key, str) or not isinstance(value, str):
                        msg = "Expected string key/value hash mapping for opencode-desktop"
                        raise TypeError(msg)
                    converted[key] = value
                hashes_by_git_dep = converted
                continue
            yield event

        if hashes_by_git_dep is None:
            msg = "Missing opencode-desktop cargo hash output"
            raise RuntimeError(msg)

        entries = [
            HashEntry.create(
                dep.hash_type, hashes_by_git_dep[dep.git_dep], git_dep=dep.git_dep
            )
            for dep in git_deps
        ]
        yield UpdateEvent.value(self.name, entries)

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Build a source entry including the current input commit."""
        return SourceEntry(
            version=info.version,
            hashes=HashCollection.from_value(hashes),
            input=self._input,
            commit=info.commit,
        )
