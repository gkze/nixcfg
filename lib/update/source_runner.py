"""Source and ref phase execution helpers for update runs."""

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import aiohttp

from lib.update import flake as update_flake
from lib.update import planner as update_planner
from lib.update import process as update_process
from lib.update import refs as update_refs
from lib.update import updaters as updater_module
from lib.update.events import (
    StatusInfo,
    StatusKind,
    UpdateEvent,
    UpdateEventKind,
    expect_artifact_updates,
    expect_source_entry,
)
from lib.update.refs import FlakeInputRef, RefTaskOptions
from lib.update.updaters import UPDATERS, ensure_updaters_loaded
from lib.update.updaters.core import UpdateContext, _call_with_optional_context
from lib.update.updaters.flake_backed import FlakeInputHashUpdater

_AIOHTTP_MAX_FIELD_SIZE = 64 * 1024
_SUMMARY_STATUS_PRIORITY = {"no_change": 0, "updated": 1, "error": 2}

if TYPE_CHECKING:
    from collections.abc import Awaitable
    from pathlib import Path

    from lib.nix.models.sources import SourceEntry, SourcesFile
    from lib.update.artifacts import GeneratedArtifact
    from lib.update.config import UpdateConfig
    from lib.update.ui_state import SummaryStatus
    from lib.update.updaters import UpdaterClass


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
    config: UpdateConfig
    dry_run: bool = False
    effective_sources: dict[str, SourceEntry] = field(default_factory=dict)


@dataclass(frozen=True)
class SourcesPhaseContext:
    """Context shared by all source update tasks in one run."""

    source_names: list[str]
    sources: SourcesFile
    queue: asyncio.Queue[UpdateEvent | None]
    update_input: bool
    native_only: bool
    config: UpdateConfig
    dry_run: bool = False


@dataclass(frozen=True)
class SourceTaskResult:
    """Result from one source update task."""

    completed: bool
    artifacts: tuple[GeneratedArtifact, ...] = field(default_factory=tuple)
    source_update: SourceEntry | None = None


@dataclass(frozen=True)
class UpdatePhaseResult:
    """Authoritative domain outcome from one update phase."""

    details: dict[str, SummaryStatus] = field(default_factory=dict)
    source_updates: dict[str, SourceEntry] = field(default_factory=dict)
    artifact_updates: dict[str, tuple[GeneratedArtifact, ...]] = field(
        default_factory=dict
    )

    @property
    def errors(self) -> int:
        """Return the number of failed update targets."""
        return sum(status == "error" for status in self.details.values())

    def merged(self, other: UpdatePhaseResult) -> UpdatePhaseResult:
        """Combine sequential phase outcomes into one run result."""
        details = dict(self.details)
        for name, status in other.details.items():
            details[name] = max(
                details.get(name, "no_change"),
                status,
                key=_SUMMARY_STATUS_PRIORITY.__getitem__,
            )
        return UpdatePhaseResult(
            details=details,
            source_updates={**self.source_updates, **other.source_updates},
            artifact_updates={**self.artifact_updates, **other.artifact_updates},
        )


def _summarize_source_results(
    source_names: list[str],
    results: dict[str, SourceTaskResult],
) -> UpdatePhaseResult:
    observations: dict[Path, dict[str, GeneratedArtifact]] = {}
    artifact_updates: dict[str, list[GeneratedArtifact]] = {}
    details: dict[str, SummaryStatus] = {}
    source_updates: dict[str, SourceEntry] = {}
    for name in source_names:
        result = results.get(name, SourceTaskResult(completed=False))
        if not result.completed:
            details[name] = "error"
            continue
        if result.source_update is not None:
            source_updates[name] = result.source_update
        details[name] = "updated" if result.source_update is not None else "no_change"
        for artifact in result.artifacts:
            path = artifact.resolved_path()
            observations.setdefault(path, {})[name] = artifact
            changed = artifact.changed_from_snapshot
            if changed if changed is not None else artifact.has_changed():
                artifact_updates.setdefault(name, []).append(artifact)
                details[name] = "updated"

    for producers in observations.values():
        if len({artifact.content for artifact in producers.values()}) > 1:
            for name, artifact in producers.items():
                updates = artifact_updates.setdefault(name, [])
                if artifact not in updates:
                    updates.append(artifact)
    return UpdatePhaseResult(
        details=details,
        source_updates=source_updates,
        artifact_updates={
            name: tuple(updates) for name, updates in artifact_updates.items()
        },
    )


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
    """Run one source updater and collect its source and artifact results."""
    artifacts_by_path: dict[Path, GeneratedArtifact] = {}
    source_update: SourceEntry | None = None
    completed = False

    async def _run() -> None:
        nonlocal completed, source_update
        current = context.sources.entries.get(name)
        updater = _get_updaters()[name](config=context.config)
        if isinstance(updater, FlakeInputHashUpdater):
            updater.native_only = context.native_only
        input_name = getattr(updater, "input_name", None)
        input_names = (
            *((input_name,) if input_name else ()),
            *getattr(updater, "additional_input_names", ()),
        )
        put = context.queue.put
        update_context = UpdateContext(
            current=current,
            dry_run=context.dry_run,
            generated_artifacts=context.generated_artifacts,
            effective_sources=context.effective_sources,
        )

        await put(
            UpdateEvent.status(
                name,
                "Starting update",
                operation="check_version",
            )
        )
        if context.update_input:
            for refresh_input_name in dict.fromkeys(input_names):
                await _ensure_input_refreshed(
                    name,
                    refresh_input_name,
                    context=context,
                )

        async for event in _call_with_optional_context(
            updater.update_stream,
            current,
            context.session,
            context=update_context,
        ):
            if event.kind is UpdateEventKind.ARTIFACT and event.payload is not None:
                for artifact in expect_artifact_updates(event.payload):
                    artifacts_by_path[artifact.path] = artifact
            elif event.kind is UpdateEventKind.RESULT and event.payload is not None:
                source_update = expect_source_entry(event.payload)
            await put(event)

        completed = True

    await update_process.run_queue_task(source=name, queue=context.queue, task=_run)
    return SourceTaskResult(
        completed=completed,
        artifacts=tuple(artifacts_by_path[path] for path in sorted(artifacts_by_path)),
        source_update=source_update,
    )


async def run_ref_phase(
    *,
    ref_inputs: list[FlakeInputRef],
    queue: asyncio.Queue[UpdateEvent | None],
    dry_run: bool,
    config: UpdateConfig,
) -> UpdatePhaseResult:
    """Run the flake ref update phase."""
    async with aiohttp.ClientSession(
        max_field_size=_AIOHTTP_MAX_FIELD_SIZE,
    ) as session:
        flake_edit_lock = asyncio.Lock()
        async with asyncio.TaskGroup() as group:
            tasks = {
                inp.name: group.create_task(
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
                for inp in ref_inputs
            }
        return UpdatePhaseResult(
            details={name: task.result() for name, task in tasks.items()}
        )


async def run_sources_phase(context: SourcesPhaseContext) -> UpdatePhaseResult:
    """Run source update tasks in dependency-respecting waves."""
    async with aiohttp.ClientSession(
        max_field_size=_AIOHTTP_MAX_FIELD_SIZE,
    ) as session:
        update_input_lock = asyncio.Lock()
        update_input_tasks: dict[str, asyncio.Task[None]] = {}
        generated_artifacts: dict[Path, str] = {}
        effective_sources = dict(context.sources.entries)
        updaters = _get_updaters()
        source_waves = update_planner.source_update_waves(
            context.source_names, updaters
        )
        source_task_slots = asyncio.Semaphore(context.config.max_nix_builds)

        def _source_task_context() -> SourceTaskContext:
            return SourceTaskContext(
                sources=context.sources,
                update_input=context.update_input,
                native_only=context.native_only,
                session=session,
                update_input_lock=update_input_lock,
                update_input_tasks=update_input_tasks,
                queue=context.queue,
                generated_artifacts=generated_artifacts,
                effective_sources=effective_sources,
                config=context.config,
                dry_run=context.dry_run,
            )

        async def _run_source_with_limit(name: str) -> SourceTaskResult:
            async with source_task_slots:
                return await update_source_task(
                    name,
                    context=_source_task_context(),
                )

        all_results: dict[str, SourceTaskResult] = {}
        for wave in source_waves:
            runnable: list[str] = []
            for name in wave:
                parent = getattr(updaters.get(name), "companion_of", None)
                if (
                    isinstance(parent, str)
                    and parent in all_results
                    and not all_results[parent].completed
                ):
                    await context.queue.put(
                        UpdateEvent.error(name, f"Prerequisite update failed: {parent}")
                    )
                    all_results[name] = SourceTaskResult(completed=False)
                    continue
                runnable.append(name)

            if not runnable:
                continue

            async with asyncio.TaskGroup() as group:
                tasks = {
                    name: group.create_task(_run_source_with_limit(name))
                    for name in runnable
                }

            for name in runnable:
                result = tasks[name].result()
                all_results[name] = result
                if not result.completed:
                    continue
                for artifact in result.artifacts:
                    generated_artifacts[artifact.path] = artifact.content
                if result.source_update is not None:
                    current = effective_sources.get(name)
                    effective_sources[name] = (
                        current.merge_native_update(result.source_update)
                        if context.native_only and current is not None
                        else result.source_update
                    )

        return _summarize_source_results(context.source_names, all_results)


__all__ = [
    "SourceTaskContext",
    "SourceTaskResult",
    "SourcesPhaseContext",
    "UpdatePhaseResult",
    "run_ref_phase",
    "run_sources_phase",
    "update_source_task",
]
