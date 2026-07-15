"""Source and ref phase execution helpers for update runs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import aiohttp

from lib.update import flake as update_flake
from lib.update import planner as update_planner
from lib.update import process as update_process
from lib.update import refs as update_refs
from lib.update import updaters as updater_module
from lib.update.config import UpdateConfig, resolve_active_config
from lib.update.events import (
    StatusInfo,
    StatusKind,
    UpdateEvent,
    UpdateEventKind,
    expect_artifact_updates,
)
from lib.update.refs import FlakeInputRef, RefTaskOptions
from lib.update.updaters import UPDATERS, ensure_updaters_loaded
from lib.update.updaters.core import UpdateContext, _call_with_optional_context
from lib.update.updaters.flake_backed import FlakeInputHashUpdater

_AIOHTTP_MAX_FIELD_SIZE = 64 * 1024

if TYPE_CHECKING:
    from collections.abc import Awaitable
    from pathlib import Path

    from lib.nix.models.sources import SourcesFile
    from lib.update.artifacts import GeneratedArtifact
    from lib.update.updaters import UpdaterClass
    from lib.update.updaters.metadata import VersionInfo


class EventPut(Protocol):
    def __call__(self, event: UpdateEvent | None, /) -> Awaitable[None]: ...


def _get_updaters() -> dict[str, UpdaterClass]:
    return updater_module.resolve_registry_alias(UPDATERS, ensure_updaters_loaded)


@dataclass(frozen=True)
class SourceTaskContext:
    """Context shared by one source update task."""

    sources: SourcesFile
    update_input: bool
    native_only: bool
    session: aiohttp.ClientSession
    update_input_lock: asyncio.Lock
    update_input_tasks: dict[str, asyncio.Task[None]]
    queue: asyncio.Queue[UpdateEvent | None]
    generated_artifacts: dict[Path, str]
    config: UpdateConfig | None = None
    pinned_version: VersionInfo | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class SourcesPhaseContext:
    """Context shared by all source update tasks in one run."""

    source_names: list[str]
    sources: SourcesFile
    queue: asyncio.Queue[UpdateEvent | None]
    update_input: bool
    native_only: bool
    config: UpdateConfig
    pinned: dict[str, VersionInfo]
    dry_run: bool = False


@dataclass(frozen=True)
class SourceTaskResult:
    """Result from one source update task."""

    completed: bool
    artifacts: tuple[GeneratedArtifact, ...] = field(default_factory=tuple)


async def _refresh_input_task(
    *,
    input_name: str,
    source: str,
    put: EventPut,
) -> None:
    await put(
        UpdateEvent.status(
            source,
            f"Updating flake input '{input_name}'...",
            operation="refresh_lock",
            status=StatusInfo(kind=StatusKind.REFRESH_LOCK, value=input_name),
        )
    )
    async for event in update_flake.update_flake_input(input_name, source=source):
        await put(event)


async def _ensure_input_refreshed(
    name: str,
    input_name: str,
    *,
    context: SourceTaskContext,
) -> None:
    put = context.queue.put
    async with context.update_input_lock:
        task = context.update_input_tasks.get(input_name)
        if task is None:
            task = asyncio.create_task(
                _refresh_input_task(
                    input_name=input_name,
                    source=name,
                    put=put,
                )
            )
            context.update_input_tasks[input_name] = task
            reuse_existing = False
        else:
            reuse_existing = True
        if reuse_existing:
            await put(
                UpdateEvent.status(
                    name,
                    f"Reusing flake input '{input_name}' refresh...",
                    operation="refresh_lock",
                    status=StatusInfo(
                        kind=StatusKind.REFRESH_LOCK,
                        value=input_name,
                    ),
                )
            )
        # Every refresh rewrites the shared flake.lock. Keep the lock held
        # until the command finishes so different inputs cannot race and lose
        # each other's updates.
        await task


async def update_source_task(
    name: str,
    *,
    context: SourceTaskContext,
) -> SourceTaskResult:
    """Run one source updater and collect emitted generated artifacts."""
    artifacts_by_path: dict[Path, GeneratedArtifact] = {}
    completed = False

    async def _run() -> None:
        nonlocal completed
        resolved_config = resolve_active_config(context.config)
        current = context.sources.entries.get(name)
        updater = _get_updaters()[name](config=resolved_config)
        if isinstance(updater, FlakeInputHashUpdater):
            updater.native_only = context.native_only
        input_name = getattr(updater, "input_name", None)
        put = context.queue.put
        update_context = UpdateContext(
            current=current,
            dry_run=context.dry_run,
            generated_artifacts=context.generated_artifacts,
        )

        await put(
            UpdateEvent.status(
                name,
                "Starting update",
                operation="check_version",
            )
        )
        if context.update_input and input_name:
            await _ensure_input_refreshed(
                name,
                input_name,
                context=context,
            )

        async for event in _call_with_optional_context(
            updater.update_stream,
            current,
            context.session,
            pinned_version=context.pinned_version,
            context=update_context,
        ):
            if event.kind is UpdateEventKind.ARTIFACT and event.payload is not None:
                for artifact in expect_artifact_updates(event.payload):
                    artifacts_by_path[artifact.path] = artifact
            await put(event)

        completed = True

    await update_process.run_queue_task(source=name, queue=context.queue, task=_run)
    return SourceTaskResult(
        completed=completed,
        artifacts=tuple(
            artifact
            for _, artifact in sorted(
                artifacts_by_path.items(),
                key=lambda item: item[0],
            )
        ),
    )


async def run_ref_phase(
    *,
    ref_inputs: list[FlakeInputRef],
    queue: asyncio.Queue[UpdateEvent | None],
    dry_run: bool,
    config: UpdateConfig,
) -> None:
    """Run the flake ref update phase."""
    async with aiohttp.ClientSession(
        max_field_size=_AIOHTTP_MAX_FIELD_SIZE,
    ) as session:
        flake_edit_lock = asyncio.Lock()
        async with asyncio.TaskGroup() as group:
            for inp in ref_inputs:
                group.create_task(
                    update_refs.update_refs_task(
                        inp,
                        session,
                        queue,
                        options=RefTaskOptions(
                            dry_run=dry_run,
                            flake_edit_lock=flake_edit_lock,
                            config=config,
                        ),
                    ),
                )


async def run_sources_phase(context: SourcesPhaseContext) -> None:
    """Run source update tasks in dependency-respecting waves."""
    async with aiohttp.ClientSession(
        max_field_size=_AIOHTTP_MAX_FIELD_SIZE,
    ) as session:
        update_input_lock = asyncio.Lock()
        update_input_tasks: dict[str, asyncio.Task[None]] = {}
        generated_artifacts: dict[Path, str] = {}
        updaters = _get_updaters()
        source_waves = update_planner.source_update_waves(
            context.source_names, updaters
        )
        source_task_slots = asyncio.Semaphore(context.config.max_nix_builds)

        def _source_task_context(name: str) -> SourceTaskContext:
            return SourceTaskContext(
                sources=context.sources,
                update_input=context.update_input,
                native_only=context.native_only,
                session=session,
                update_input_lock=update_input_lock,
                update_input_tasks=update_input_tasks,
                queue=context.queue,
                generated_artifacts=generated_artifacts,
                config=context.config,
                pinned_version=context.pinned.get(name),
                dry_run=context.dry_run,
            )

        async def _run_source_with_limit(name: str) -> SourceTaskResult:
            async with source_task_slots:
                return await update_source_task(
                    name,
                    context=_source_task_context(name),
                )

        completed_sources: dict[str, bool] = {}
        for wave in source_waves:
            runnable: list[str] = []
            for name in wave:
                parent = getattr(updaters.get(name), "companion_of", None)
                if (
                    isinstance(parent, str)
                    and parent in completed_sources
                    and not completed_sources[parent]
                ):
                    await context.queue.put(
                        UpdateEvent.error(name, f"Prerequisite update failed: {parent}")
                    )
                    completed_sources[name] = False
                    continue
                runnable.append(name)

            if not runnable:
                continue

            wave_results: dict[str, SourceTaskResult] = {}
            if context.config.max_nix_builds == 1 or len(runnable) == 1:
                for name in runnable:
                    result = await update_source_task(
                        name,
                        context=_source_task_context(name),
                    )
                    wave_results[name] = result
            else:
                async with asyncio.TaskGroup() as group:
                    tasks = {
                        name: group.create_task(_run_source_with_limit(name))
                        for name in runnable
                    }
                wave_results = {name: task.result() for name, task in tasks.items()}

            for name in runnable:
                result = wave_results[name]
                completed_sources[name] = result.completed
                if not result.completed:
                    continue
                for artifact in result.artifacts:
                    generated_artifacts[artifact.path] = artifact.content


__all__ = [
    "SourceTaskContext",
    "SourceTaskResult",
    "SourcesPhaseContext",
    "run_ref_phase",
    "run_sources_phase",
    "update_source_task",
]
