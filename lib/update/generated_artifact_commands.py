"""Helpers for command-backed generated artifact refreshes."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING

from lib.update.artifacts import GeneratedArtifact
from lib.update.events import (
    CommandResult,
    EventStream,
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    expect_command_result,
    require_value,
)
from lib.update.paths import REPO_ROOT
from lib.update.process import RunCommandOptions
from lib.update.process import run_command as _run_command

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from lib.update.config import UpdateConfig


type ArtifactSnapshot = dict[Path, str | None]

_ARTIFACT_LOCKS: dict[tuple[int, Path], asyncio.Lock] = {}


def _artifact_path(path: str | Path, *, repo_root: Path) -> Path:
    return GeneratedArtifact.text(path, "").resolved_path(repo_root=repo_root)


def _artifact_locks(
    artifact_paths: Iterable[str | Path],
    *,
    repo_root: Path,
) -> tuple[asyncio.Lock, ...]:
    loop_key = id(asyncio.get_running_loop())
    resolved_paths = sorted({
        _artifact_path(path, repo_root=repo_root) for path in artifact_paths
    })
    return tuple(
        _ARTIFACT_LOCKS.setdefault((loop_key, path), asyncio.Lock())
        for path in resolved_paths
    )


def _snapshot_artifacts(
    artifact_paths: Iterable[str | Path],
    *,
    repo_root: Path,
) -> ArtifactSnapshot:
    snapshot: ArtifactSnapshot = {}
    for path in artifact_paths:
        resolved = _artifact_path(path, repo_root=repo_root)
        snapshot[resolved] = (
            resolved.read_text(encoding="utf-8") if resolved.exists() else None
        )
    return snapshot


def _restore_artifacts(snapshot: ArtifactSnapshot) -> None:
    for path, content in snapshot.items():
        if content is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _read_artifacts(
    artifact_paths: Iterable[str | Path],
    *,
    repo_root: Path,
) -> tuple[GeneratedArtifact, ...]:
    artifacts: list[GeneratedArtifact] = []
    for path in artifact_paths:
        resolved = _artifact_path(path, repo_root=repo_root)
        if not resolved.is_file():
            msg = f"Generated artifact was not produced: {path}"
            raise RuntimeError(msg)
        artifacts.append(GeneratedArtifact.text(path, resolved.read_text("utf-8")))
    return tuple(artifacts)


def _raise_failed_command(action: str, result: CommandResult) -> None:
    if result.returncode == 0:
        return
    detail = result.stderr.strip() or result.stdout.strip()
    message = f"{action} failed (exit {result.returncode})"
    if detail:
        message = f"{message}: {detail}"
    raise RuntimeError(message)


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
) -> EventStream:
    """Refresh command-generated artifacts, hash against them, then restore files."""
    _ = dry_run
    async with AsyncExitStack() as stack:
        for lock in _artifact_locks(artifact_paths, repo_root=repo_root):
            await stack.enter_async_context(lock)

        snapshot = _snapshot_artifacts(artifact_paths, repo_root=repo_root)
        try:
            yield UpdateEvent.status(
                source,
                f"Refreshing {detail}...",
                operation=operation,
                status="computing_hash",
                detail=detail,
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

            artifacts = _read_artifacts(artifact_paths, repo_root=repo_root)
            yield UpdateEvent.artifact(source, list(artifacts))
            yield UpdateEvent.status(
                source,
                f"Prepared {detail}",
                operation=operation,
                status="updated",
                detail=detail,
            )

            async for event in inner:
                yield event
        finally:
            _restore_artifacts(snapshot)


__all__ = [
    "stream_command_materialized_artifacts",
]
