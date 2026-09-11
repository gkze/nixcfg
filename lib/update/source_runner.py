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
)
from lib.update.outcomes import SummaryStatus, merge_statuses
from lib.update.refs import FlakeInputRef, RefTaskOptions
from lib.update.runtime import join_shared_work, measure, resource_slot, runtime_scope
from lib.update.updaters import UPDATERS, ensure_updaters_loaded
from lib.update.updaters.core import UpdateContext
from lib.update.updaters.flake_backed import FlakeInputHashUpdater

_AIOHTTP_MAX_FIELD_SIZE = 64 * 1024

if TYPE_CHECKING:
    from collections.abc import Awaitable
    from pathlib import Path

    from lib.nix.models.sources import SourceEntry, SourcesFile
    from lib.update.artifacts import GeneratedArtifact
    from lib.update.config import UpdateConfig
    from lib.update.flake import FlakeInputState
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
    effective_sources: dict[str, SourceEntry] = field(default_factory=dict)
    input_refreshes: dict[str, FlakeInputState] = field(default_factory=dict)


@dataclass(frozen=True)
class SourcesPhaseContext:
    """Context shared by all source update tasks in one run."""

    source_names: list[str]
    sources: SourcesFile
    queue: asyncio.Queue[UpdateEvent | None]
    update_input: bool
    native_only: bool
    config: UpdateConfig
    input_refreshes: dict[str, FlakeInputState] = field(default_factory=dict)


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
    input_refreshes: dict[str, FlakeInputState] = field(default_factory=dict)
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
        return UpdatePhaseResult(
            details=merge_statuses(self.details, other.details),
            input_refreshes={**self.input_refreshes, **other.input_refreshes},
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
    config: UpdateConfig,
) -> None:
    await put(
        UpdateEvent.status(
            source,
            f"Updating flake input '{input_name}'...",
            operation="refresh_lock",
            status=StatusInfo(kind=StatusKind.REFRESH_LOCK, value=input_name),
        )
    )
    await update_flake.update_flake_input(
        input_name, source=source, emit=put, config=config
    )


async def _ensure_input_refreshed(
    name: str,
    input_name: str,
    *,
    context: SourceTaskContext,
) -> None:
    put = context.queue.put
    async with context.update_input_lock:
        task = context.update_input_tasks.get(input_name)
        receipt = context.input_refreshes.get(input_name)
        reused_ref_phase = (
            task is None
            and receipt is not None
            and receipt == update_flake.read_flake_input_state(input_name)
        )
        if task is None and not reused_ref_phase:
            task = asyncio.create_task(
                _refresh_input_task(
                    input_name=input_name,
                    source=name,
                    put=put,
                    config=context.config,
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
        if task is not None:
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
            *update_planner.source_additional_input_names(type(updater)),
        )
        put = context.queue.put
        update_context = UpdateContext(
            current=current,
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

        async def emit(event: UpdateEvent) -> None:
            if event.kind is UpdateEventKind.ARTIFACT and event.payload is not None:
                for artifact in expect_artifact_updates(event.payload):
                    artifacts_by_path[artifact.path] = artifact
            await put(event)

        source_update = await updater.update_stream(
            current,
            context.session,
            context=update_context,
            emit=emit,
        )

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
    config: UpdateConfig,
) -> UpdatePhaseResult:
    """Run the flake ref update phase."""
    async with aiohttp.ClientSession(
        max_field_size=_AIOHTTP_MAX_FIELD_SIZE,
    ) as session:
        flake_edit_lock = asyncio.Lock()
        input_refreshes: dict[str, FlakeInputState] = {}
        async with asyncio.TaskGroup() as group:
            tasks = {
                inp.name: group.create_task(
                    update_refs.update_refs_task(
                        inp,
                        session,
                        queue,
                        options=RefTaskOptions(
                            flake_edit_lock=flake_edit_lock,
                            config=config,
                            input_refreshes=input_refreshes,
                        ),
                    ),
                )
                for inp in ref_inputs
            }
        details = {name: task.result() for name, task in tasks.items()}
        return UpdatePhaseResult(
            details=details,
            input_refreshes={
                name: state
                for name, state in input_refreshes.items()
                if details[name] == "updated"
            },
        )


async def _refresh_source_inputs(name: str, context: SourceTaskContext) -> bool:
    """Finish selected lock writes before any concurrent candidate evaluation."""
    completed = False

    async def refresh() -> None:
        nonlocal completed
        updater = _get_updaters()[name]
        input_name = update_planner.source_backing_input_name(
            name, updater, context.sources.entries.get(name)
        )
        inputs = (
            *((input_name,) if input_name else ()),
            *update_planner.source_additional_input_names(updater),
        )
        for input_name in dict.fromkeys(inputs):
            await _ensure_input_refreshed(name, input_name, context=context)
        completed = True

    await update_process.run_queue_task(source=name, queue=context.queue, task=refresh)
    return completed


async def run_sources_phase(context: SourcesPhaseContext) -> UpdatePhaseResult:
    """Release dependencies after publication, independently of unrelated work."""
    async with (
        runtime_scope(context.config),
        aiohttp.ClientSession(max_field_size=_AIOHTTP_MAX_FIELD_SIZE) as session,
    ):
        updaters = _get_updaters()
        selected = set(context.source_names)
        prerequisites = {
            name: update_planner.source_prerequisites(updaters, name, selected=selected)
            for name in context.source_names
        }
        source_order = update_planner.source_dependency_order(prerequisites)
        shared = SourceTaskContext(
            sources=context.sources,
            update_input=False,
            native_only=context.native_only,
            session=session,
            update_input_lock=asyncio.Lock(),
            update_input_tasks={},
            queue=context.queue,
            generated_artifacts={},
            effective_sources=dict(context.sources.entries),
            input_refreshes=context.input_refreshes,
            config=context.config,
        )
        refresh_failures: set[str] = set()
        if context.update_input:
            for name in context.source_names:
                if not await _refresh_source_inputs(name, shared):
                    refresh_failures.add(name)

        tasks: dict[str, asyncio.Task[SourceTaskResult]] = {}

        async def run_ready(name: str) -> SourceTaskResult:
            with measure(name, "dependencies"):
                failed = [
                    prerequisite
                    for prerequisite in prerequisites[name]
                    if not (await tasks[prerequisite]).completed
                ]
            if failed:
                await context.queue.put(
                    UpdateEvent.error(
                        name, f"Prerequisite update failed: {', '.join(failed)}"
                    )
                )
            if failed or name in refresh_failures:
                return SourceTaskResult(completed=False)
            async with resource_slot("source", source=name, config=context.config):
                result = await update_source_task(name, context=shared)
            if result.completed:
                # No suspension between publishing an immutable result and
                # completing this task: children observe the complete result.
                for artifact in result.artifacts:
                    shared.generated_artifacts[artifact.path] = artifact.content
                if result.source_update is not None:
                    current = shared.effective_sources.get(name)
                    shared.effective_sources[name] = (
                        current.merge_native_update(result.source_update)
                        if context.native_only and current is not None
                        else result.source_update
                    )
            return result

        try:
            async with asyncio.TaskGroup() as group:
                # Parents must exist even when the loop starts tasks eagerly.
                for name in source_order:
                    tasks[name] = group.create_task(run_ready(name))
        finally:
            # Shared artifact producers can outlive one cancelled consumer. Reap
            # them while the disposable workspace still exists, before CLI
            # teardown restores cwd/REPO_ROOT or removes temporary files.
            await join_shared_work()
        return _summarize_source_results(
            context.source_names, {name: task.result() for name, task in tasks.items()}
        )


__all__ = [
    "SourceTaskContext",
    "SourceTaskResult",
    "SourcesPhaseContext",
    "UpdatePhaseResult",
    "run_ref_phase",
    "run_sources_phase",
    "update_source_task",
]
