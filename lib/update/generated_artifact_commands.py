"""Helpers for command-backed generated artifact refreshes."""

import asyncio
import shutil
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from lib.update import events as update_events
from lib.update.artifacts import GeneratedArtifact
from lib.update.events import (
    CommandResult,
    EventStream,
    StatusInfo,
    StatusKind,
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    expect_command_result,
    require_value,
)
from lib.update.io import atomic_write_bytes
from lib.update.paths import REPO_ROOT
from lib.update.process import RunCommandOptions
from lib.update.process import run_command as _run_command

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterable, Mapping

    from lib.update.config import UpdateConfig

    type ArtifactNormalizer = Callable[[str], str]


@dataclass(frozen=True, slots=True)
class _ArtifactState:
    content: bytes
    mode: int


type ArtifactSnapshot = dict[Path, _ArtifactState | None]

_ARTIFACT_LOCKS: dict[
    tuple[asyncio.AbstractEventLoop, Path], tuple[asyncio.Lock, int]
] = {}
_raise_failed_command = update_events.raise_failed_command


def _artifact_path(path: str | Path, *, repo_root: Path) -> Path:
    return GeneratedArtifact.text(path, "").resolved_path(repo_root=repo_root)


@asynccontextmanager
async def _artifact_locks(
    artifact_paths: Iterable[str | Path],
    *,
    repo_root: Path,
) -> AsyncIterator[None]:
    loop = asyncio.get_running_loop()
    resolved_paths = sorted({
        _artifact_path(path, repo_root=repo_root) for path in artifact_paths
    })
    registrations: list[
        tuple[tuple[asyncio.AbstractEventLoop, Path], asyncio.Lock]
    ] = []
    for path in resolved_paths:
        key = (loop, path)
        lock, users = _ARTIFACT_LOCKS.get(key, (asyncio.Lock(), 0))
        _ARTIFACT_LOCKS[key] = (lock, users + 1)
        registrations.append((key, lock))
    try:
        async with AsyncExitStack() as stack:
            for _key, lock in registrations:
                await stack.enter_async_context(lock)
            yield
    finally:
        for key, lock in registrations:
            _registered, users = _ARTIFACT_LOCKS[key]
            if users == 1:
                del _ARTIFACT_LOCKS[key]
            else:
                _ARTIFACT_LOCKS[key] = (lock, users - 1)


def _snapshot_path(path: Path) -> _ArtifactState | None:
    if not path.exists():
        return None
    if not path.is_file():
        msg = f"Generated artifact path is not a regular file: {path}"
        raise RuntimeError(msg)
    return _ArtifactState(path.read_bytes(), path.stat().st_mode & 0o777)


def _snapshot_artifacts(
    artifact_paths: Iterable[str | Path],
    *,
    repo_root: Path,
) -> ArtifactSnapshot:
    return {
        resolved: _snapshot_path(resolved)
        for path in artifact_paths
        for resolved in (_artifact_path(path, repo_root=repo_root),)
    }


def _restore_artifacts(snapshot: ArtifactSnapshot) -> None:
    for path, state in snapshot.items():
        try:
            current = _snapshot_path(path)
        except RuntimeError:
            current = object()
        if current == state:
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
        if state is None:
            continue
        atomic_write_bytes(path, state.content, mkdir=True)
        path.chmod(state.mode)


def _read_artifacts(
    artifact_paths: Iterable[str | Path],
    *,
    snapshot: ArtifactSnapshot,
    repo_root: Path,
    artifact_normalizers: Mapping[str | Path, ArtifactNormalizer] | None = None,
) -> tuple[GeneratedArtifact, ...]:
    resolved_normalizers = {
        _artifact_path(path, repo_root=repo_root): normalizer
        for path, normalizer in (artifact_normalizers or {}).items()
    }
    artifacts: list[GeneratedArtifact] = []
    for path in artifact_paths:
        resolved = _artifact_path(path, repo_root=repo_root)
        if not resolved.is_file():
            msg = f"Generated artifact was not produced: {path}"
            raise RuntimeError(msg)
        content = resolved.read_text("utf-8")
        normalizer = resolved_normalizers.get(resolved)
        if normalizer is not None:
            content = normalizer(content)
        original = snapshot[resolved]
        artifacts.append(
            GeneratedArtifact.text(
                path,
                content,
                changed_from_snapshot=(
                    original is None or original.content != content.encode()
                ),
            )
        )
    return tuple(artifacts)


async def stream_command_materialized_artifacts(
    source: str,
    *,
    args: list[str],
    artifact_paths: tuple[str | Path, ...],
    inner: EventStream,
    dry_run: bool,
    config: UpdateConfig | None = None,
    detail: str = "generated artifacts",
    env: Mapping[str, str] | None = None,
    operation: str = "materialize_artifacts",
    repo_root: Path = REPO_ROOT,
    artifact_normalizers: Mapping[str | Path, ArtifactNormalizer] | None = None,
) -> EventStream:
    """Refresh artifacts inside the run workspace, hash, and restore them."""
    if dry_run:
        async for event in inner:
            yield event
        return

    async with _artifact_locks(artifact_paths, repo_root=repo_root):
        snapshot = _snapshot_artifacts(artifact_paths, repo_root=repo_root)
        try:
            yield UpdateEvent.status(
                source,
                f"Refreshing {detail}...",
                operation=operation,
                status=StatusInfo(kind=StatusKind.COMPUTING_HASH, value=detail),
            )
            result_drain = ValueDrain[CommandResult]()
            async for event in drain_value_events(
                _run_command(
                    args,
                    options=RunCommandOptions(
                        source=source,
                        error=f"Missing {detail} command result",
                        env=env,
                        config=config,
                    ),
                ),
                result_drain,
                parse=expect_command_result,
            ):
                yield event
            result = require_value(result_drain, f"Missing {detail} command result")
            _raise_failed_command(f"Refresh {detail}", result)

            artifacts = _read_artifacts(
                artifact_paths,
                snapshot=snapshot,
                repo_root=repo_root,
                artifact_normalizers=artifact_normalizers,
            )
            yield UpdateEvent.artifact(source, list(artifacts))
            yield UpdateEvent.status(
                source,
                f"Prepared {detail}",
                operation=operation,
                status=StatusInfo(kind=StatusKind.UPDATED, value=detail),
            )

            async for event in inner:
                yield event
        finally:
            _restore_artifacts(snapshot)


__all__ = [
    "stream_command_materialized_artifacts",
]
