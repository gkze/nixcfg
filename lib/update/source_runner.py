"""Source and ref phase execution helpers for update runs."""

import asyncio
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

import aiohttp
from dbos import DBOS

from lib.update import durable
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
from lib.update.runtime import (
    active_runtime,
    join_shared_work,
    measure,
    resource_slot,
    runtime_scope,
)
from lib.update.updaters import UPDATERS, ensure_updaters_loaded
from lib.update.updaters.core import UpdateContext
from lib.update.updaters.flake_backed import FlakeInputHashUpdater

_AIOHTTP_MAX_FIELD_SIZE = 64 * 1024

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from lib.nix.models.sources import SourceEntry, SourcesFile
    from lib.update.artifacts import GeneratedArtifact
    from lib.update.candidate import Preparation
    from lib.update.config import UpdateConfig
    from lib.update.flake import FlakeInputState
    from lib.update.updaters import UpdaterClass


def _get_updaters() -> dict[str, UpdaterClass]:
    return updater_module.resolve_registry_alias(UPDATERS, ensure_updaters_loaded)


@dataclass(frozen=True)
class SourceTaskContext:
    """Context shared by one source update task.

    Input refreshes finish before any task starts (see
    :func:`_refresh_source_inputs`), so tasks never refresh locks themselves.
    """

    sources: SourcesFile
    native_only: bool
    session: aiohttp.ClientSession
    queue: asyncio.Queue[UpdateEvent | None]
    generated_artifacts: dict[Path, str]
    config: UpdateConfig
    effective_sources: dict[str, SourceEntry] = field(default_factory=dict)
    preparation: Preparation | None = None


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
    preparation: Preparation | None = None


@dataclass(frozen=True)
class SourceTaskResult:
    """Result from one source update task."""

    completed: bool
    artifacts: tuple[GeneratedArtifact, ...] = field(default_factory=tuple)
    source_update: SourceEntry | None = None
    error: UpdateEvent | None = None


@dataclass(frozen=True)
class UpdatePhaseResult:
    """Authoritative domain outcome from one update phase."""

    details: dict[str, SummaryStatus] = field(default_factory=dict)
    input_refreshes: dict[str, FlakeInputState] = field(default_factory=dict)
    source_updates: dict[str, SourceEntry] = field(default_factory=dict)
    artifact_updates: dict[str, tuple[GeneratedArtifact, ...]] = field(
        default_factory=dict
    )
    terminal_events: tuple[UpdateEvent, ...] = ()

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
            terminal_events=(*self.terminal_events, *other.terminal_events),
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


def _source_input_requests(
    source_names: list[str],
    source_entries: Mapping[str, SourceEntry],
) -> tuple[dict[str, str], dict[str, tuple[str, ...]]]:
    """Map each distinct backing input to its first requester.

    Also return each source's own input closure for failure attribution.
    """
    updaters = _get_updaters()
    requested: dict[str, str] = {}
    closures: dict[str, tuple[str, ...]] = {}
    for name in source_names:
        updater = updaters[name]
        backing = update_planner.source_backing_input_name(
            name, updater, source_entries.get(name)
        )
        inputs = tuple(
            dict.fromkeys((
                *((backing,) if backing else ()),
                *update_planner.source_additional_input_names(updater),
            ))
        )
        for input_name in inputs:
            requested.setdefault(input_name, name)
        closures[name] = inputs
    return requested, closures


async def _refresh_source_inputs(
    context: SourcesPhaseContext,
) -> dict[str, UpdateEvent]:
    """Resolve every selected source's input closure before any task starts.

    Inputs already covered by a matching receipt are skipped; the rest refresh
    in one ``nix flake update`` so N lock evaluations become one. A failed
    resolution fails every source whose closure includes a pending input;
    independent sources continue.
    """
    put = context.queue.put
    requested, closures = _source_input_requests(
        context.source_names, context.sources.entries
    )
    covered = tuple(
        input_name
        for input_name in requested
        if context.input_refreshes.get(input_name)
        == update_flake.read_flake_input_state(input_name)
    )
    pending = tuple(name for name in requested if name not in covered)

    def refresh_status(input_name: str, message: str) -> UpdateEvent:
        return UpdateEvent.status(
            requested[input_name],
            message,
            operation="refresh_lock",
            status=StatusInfo(kind=StatusKind.REFRESH_LOCK, value=input_name),
        )

    for input_name in covered:
        await put(
            refresh_status(input_name, f"Reusing flake input '{input_name}' refresh...")
        )
    for input_name in pending:
        await put(refresh_status(input_name, f"Updating flake input '{input_name}'..."))
    if not pending:
        return {}

    try:
        await update_flake.update_flake_inputs(
            pending,
            source=requested[pending[0]],
            emit=put,
            config=context.config,
        )
    except Exception as exc:  # noqa: BLE001 -- a failed resolution fails every dependent source
        message = f"{type(exc).__name__}: {exc}"
        pending_set = set(pending)
        failed = {
            name
            for name, inputs in closures.items()
            if pending_set.intersection(inputs)
        }
        return {name: UpdateEvent.error(name, message) for name in sorted(failed)}
    for input_name in pending:
        context.input_refreshes[input_name] = update_flake.read_flake_input_state(
            input_name
        )
    return {}


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
        put = context.queue.put
        update_context = UpdateContext(
            current=current,
            generated_artifacts=context.generated_artifacts,
            effective_sources=context.effective_sources,
            resolved_version=(
                context.preparation.resolved(name) if context.preparation else None
            ),
            preparing=context.preparation is not None,
        )

        await put(
            UpdateEvent.status(
                name,
                "Starting update",
                operation="check_version",
            )
        )

        async def emit(event: UpdateEvent) -> None:
            if event.kind is UpdateEventKind.ARTIFACT and event.payload is not None:
                for artifact in expect_artifact_updates(event.payload):
                    portable = replace(artifact, path=artifact.repo_relative_path())
                    artifacts_by_path[portable.path] = portable
            await put(event)

        try:
            source_update = await updater.update_stream(
                current,
                context.session,
                context=update_context,
                emit=emit,
            )
        finally:
            if context.preparation is not None:
                context.preparation.record(name, update_context.resolved_version)

        completed = True

    failure = await update_process.run_queue_task(
        source=name, queue=context.queue, task=_run
    )
    return SourceTaskResult(
        completed=completed,
        artifacts=tuple(artifacts_by_path[path] for path in sorted(artifacts_by_path)),
        source_update=source_update,
        error=failure,
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
        terminal_events: list[UpdateEvent] = []
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
                            terminal_events=terminal_events,
                        ),
                    ),
                )
                for inp in ref_inputs
            }
        details = {name: task.result() for name, task in tasks.items()}
        return UpdatePhaseResult(
            details=details,
            terminal_events=tuple(terminal_events),
            # Receipts exist only for tasks that completed a verified refresh
            # or remote check; failed tasks keep their receipts local so they
            # can never seed a later source phase.
            input_refreshes={
                name: state
                for name, state in input_refreshes.items()
                if details.get(name) != "error"
            },
        )


@dataclass(frozen=True)
class SourceRequest:
    """Immutable inputs needed to recover one source independently."""

    name: str
    sources: SourcesFile
    native_only: bool
    generated_artifacts: dict[Path, str]
    effective_sources: dict[str, SourceEntry]


@DBOS.workflow(name="nixcfg.source")
async def _durable_source(request: SourceRequest) -> SourceTaskResult:
    async def perform() -> SourceTaskResult:
        run = durable.current_run()
        assert run is not None  # noqa: S101 -- supervise requires an active session
        environment = await asyncio.shield(run.sources_ready)
        async with (
            runtime_scope(run.config, existing=environment.resources),
            resource_slot("source", source=request.name, config=run.config),
            aiohttp.ClientSession(max_field_size=_AIOHTTP_MAX_FIELD_SIZE) as session,
        ):
            result = await update_source_task(
                request.name,
                context=SourceTaskContext(
                    sources=request.sources,
                    native_only=request.native_only,
                    session=session,
                    queue=environment.queue,
                    generated_artifacts=request.generated_artifacts,
                    effective_sources=request.effective_sources,
                    config=run.config,
                ),
            )
            environment.reported_sources.add(request.name)
            return result

    return await durable.supervise(perform)


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
        if context.preparation is not None:
            context.preparation.dependent_sources.update(
                name for name, parents in prerequisites.items() if parents
            )
        shared = SourceTaskContext(
            sources=context.sources,
            native_only=context.native_only,
            session=session,
            queue=context.queue,
            generated_artifacts={},
            effective_sources=dict(context.sources.entries),
            preparation=context.preparation,
            config=context.config,
        )

        async def refresh() -> tuple[
            dict[str, UpdateEvent], dict[str, FlakeInputState]
        ]:
            failed = await _refresh_source_inputs(context)
            return failed, dict(context.input_refreshes)

        refresh_failures: dict[str, UpdateEvent] = {}
        if context.update_input:
            refresh_failures, receipts = await durable.phase("input-refresh", refresh)
            context.input_refreshes.update(receipts)
            for error in refresh_failures.values():
                await context.queue.put(error)

        if (run := durable.current_run()) is not None:
            run.sources_ready.set_result(
                durable.SourceEnvironment(context.queue, active_runtime())
            )

        tasks: dict[str, asyncio.Task[SourceTaskResult]] = {}

        async def run_ready(name: str) -> SourceTaskResult:
            if prerequisites[name]:
                await context.queue.put(
                    UpdateEvent.status(
                        name,
                        f"Waiting for prerequisites: {', '.join(prerequisites[name])}",
                    )
                )
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
            await context.queue.put(
                UpdateEvent.status(name, "Waiting for source worker")
            )
            if run is None:
                async with resource_slot("source", source=name, config=context.config):
                    result = await update_source_task(name, context=shared)
            else:
                request = SourceRequest(
                    name=name,
                    sources=context.sources,
                    native_only=context.native_only,
                    generated_artifacts=dict(shared.generated_artifacts),
                    effective_sources=dict(shared.effective_sources),
                )
                result = await durable.source_task(
                    name, lambda: _durable_source(request), group
                )
                # Replay needs terminal presentation; live execution already emitted it.
                if name not in run.sources_ready.result().reported_sources:
                    await context.queue.put(
                        UpdateEvent.result(name, result.source_update)
                        if result.completed
                        else result.error
                        or UpdateEvent.error(name, "Source update failed")
                    )
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
        summary = _summarize_source_results(
            context.source_names, {name: task.result() for name, task in tasks.items()}
        )
        return replace(summary, input_refreshes=dict(context.input_refreshes))


__all__ = [
    "SourceTaskContext",
    "SourceTaskResult",
    "SourcesPhaseContext",
    "UpdatePhaseResult",
    "run_ref_phase",
    "run_sources_phase",
    "update_source_task",
]
