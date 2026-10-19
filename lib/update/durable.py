"""DBOS execution supervised by the CLI's repository/workspace lifetime.

Each invocation opens one run database. DBOS recovers the root and source
workflows; the CLI supplies their repository lock, workspace and resources.
Only durable inputs and outputs cross workflow boundaries.
"""

import asyncio
import hashlib
import platform
import sys
from contextlib import nullcontext
from contextvars import Context
from dataclasses import dataclass, field, fields, replace
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from dbos import DBOS, SetWorkflowID
from filelock import FileLock, Timeout
from pydantic import TypeAdapter

from lib.update.cli_options import UpdateOptions
from lib.update.config import (
    UpdateConfig,  # noqa: TC001 -- Pydantic resolves RunRequest fields
)
from lib.update.run_monitor import default_run_log_root
from lib.update.run_store import DATABASE_FILE, RunStore
from lib.update.runtime import await_cleanup

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from lib.update.events import UpdateEvent
    from lib.update.persistence import IsolatedUpdateWorkspace
    from lib.update.runtime import UpdateRuntime


@dataclass(frozen=True)
class SourceEnvironment:
    """Resources made available only after the root restores resolved inputs."""

    queue: asyncio.Queue[UpdateEvent | None]
    resources: UpdateRuntime | None
    reported_sources: set[str] = field(default_factory=set)


@dataclass
class Run:
    """One admitted CLI session; DBOS owns every durable execution record.

    DBOS adopts the CLI event loop at launch. Track recovered domain tasks so
    subprocess cleanup completes before their workspace is removed. Recovered
    sources await input restoration before touching files.
    """

    store: RunStore
    workspace: IsolatedUpdateWorkspace
    config: UpdateConfig
    options: UpdateOptions
    loop: asyncio.AbstractEventLoop = field(default_factory=asyncio.get_running_loop)
    tasks: set[asyncio.Task[object]] = field(default_factory=set)
    closing: bool = False
    sources_ready: asyncio.Future[SourceEnvironment] = field(
        default_factory=lambda: asyncio.get_running_loop().create_future()
    )


_SESSION: Run | None = None
_PRESENTATION_OPTIONS = frozenset({
    "json",
    "quiet",
    "verbose",
    "timings",
    "tty",
    "patch",
})


def current_run() -> Run | None:
    """Return the CLI session admitted by the process/repository workspace lock."""
    return _SESSION


async def supervise[T](operation: Callable[[], Awaitable[T]]) -> T:
    """Join DBOS recovery to the CLI's event loop and cancellation lifetime."""
    run = current_run()
    if run is None:
        msg = "Durable execution requires an active update session"
        raise RuntimeError(msg)

    if run.closing:
        raise asyncio.CancelledError
    if asyncio.get_running_loop() is not run.loop:
        msg = "DBOS must execute updater work on the CLI event loop"
        raise RuntimeError(msg)
    task = asyncio.current_task()
    assert task is not None  # noqa: S101 -- a running coroutine always has an owning task
    run.tasks.add(task)
    try:
        return await operation()
    finally:
        run.tasks.remove(task)


def workspace(
    root: Path,
) -> nullcontext[IsolatedUpdateWorkspace] | IsolatedUpdateWorkspace:
    """Reuse the supervised workspace, or own one for direct library callers."""
    from lib.update.persistence import (  # noqa: PLC0415 -- workspace lifecycle avoids import cycle
        IsolatedUpdateWorkspace,
    )

    run = current_run()
    return nullcontext(run.workspace) if run else IsolatedUpdateWorkspace(root)


def _checked[T](name: str, recorded: tuple[str, T]) -> T:
    if recorded[0] != name:
        msg = f"Durable step changed: expected {recorded[0]}, got {name}"
        raise ValueError(msg)
    return recorded[1]


@DBOS.step()
async def _checkpoint[T](
    name: str, operation: Callable[[], Awaitable[T]]
) -> tuple[str, T]:
    return name, await operation()


async def checkpoint[T](name: str, operation: Callable[[], Awaitable[T]]) -> T:
    """Reuse completed results, rejecting changed ordering or input identities."""
    return _checked(name, await _checkpoint(name, operation))


@DBOS.step()
def _checkpoint_sync[T](name: str, operation: Callable[[], T]) -> tuple[str, T]:
    return name, operation()


def checkpoint_sync[T](name: str, operation: Callable[[], T]) -> T:
    """Checkpoint synchronous domain decisions and validation results."""
    return _checked(name, _checkpoint_sync(name, operation))


async def phase[T](
    name: str,
    operation: Callable[[], Awaitable[T]],
    *,
    replay: Callable[[T], Awaitable[None]] | None = None,
) -> T:
    """Recreate filesystem effects even when DBOS skips completed execution."""
    run = current_run()
    performed = False

    async def perform() -> tuple[T, str | None]:
        nonlocal performed
        result = await operation()
        performed = True
        return result, None if run is None else run.workspace.checkpoint()

    result, snapshot = await checkpoint(name, perform)
    if snapshot is not None and run is not None:
        run.workspace.restore_checkpoint(snapshot)
    if not performed and replay is not None:
        await replay(result)
    return result


def source_task[T](
    name: str, operation: Callable[[], Awaitable[T]], group: asyncio.TaskGroup
) -> asyncio.Task[T]:
    """Start independent histories without sharing a concurrent parent step counter.

    The operation invokes a registered source workflow with serializable inputs.
    DBOS can recover it before the parent reaches this call again.
    """

    async def invoke() -> T:
        with SetWorkflowID(f"source:{name}"):
            return await operation()

    return group.create_task(invoke(), context=Context())


def runtime_identity(root: Path) -> str:
    """Bind recovery to the complete updater/runtime source and SDK version."""
    from lib.update.cli import (  # noqa: PLC0415 -- reuse the packaged runtime contract
        _runtime_source_policy,
        _runtime_source_relpaths,
    )

    digest = hashlib.sha256(
        f"{sys.version}:{platform.system()}:{platform.machine()}:{version('dbos')}".encode()
    )
    policy = _runtime_source_policy(root)
    for relative in sorted(_runtime_source_relpaths(root, policy)):
        path = root / relative
        digest.update(str(relative).encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


@dataclass(frozen=True)
class RunRequest:
    """Versioned invocation bound to a repository and execution environment."""

    root: Path
    runtime: str
    options: UpdateOptions
    config: UpdateConfig
    schema: int = 1


class ResumeError(ValueError):
    """The supplied run cannot be resumed under the requested contract."""


def _resume_request(
    opts: UpdateOptions, store: RunStore, root: Path, identity: str
) -> RunRequest:
    try:
        request = TypeAdapter(RunRequest).validate_python(store.read("request"))
    except (ValueError, FileNotFoundError) as error:
        msg = f"Invalid update run request: {store.path}"
        raise ResumeError(msg) from error
    if request.schema != 1 or request.root != root or request.runtime != identity:
        msg = "Resume requires the original repository, platform and updater runtime"
        raise ResumeError(msg)
    defaults = UpdateOptions()
    expected = request.options if opts.run_id is not None else defaults
    conflicts = [
        item.name
        for item in fields(opts)
        if item.name not in _PRESENTATION_OPTIONS | {"resume", "run_id"}
        and getattr(opts, item.name) != getattr(expected, item.name)
    ]
    if conflicts:
        msg = f"Resume reuses recorded inputs; conflicting options: {', '.join(conflicts)}"
        raise ResumeError(msg)
    display = {
        name: getattr(opts, name)
        for name in _PRESENTATION_OPTIONS
        if getattr(opts, name) != getattr(defaults, name)
    }
    return replace(request, options=replace(request.options, **display))


async def execute(opts: UpdateOptions, root: Path, config: UpdateConfig) -> int:
    """Create or resume a run under exclusive repository ownership."""
    root = root.expanduser().resolve()  # noqa: ASYNC240 -- startup precedes worker execution
    run_root = (config.run_log_dir or default_run_log_root()).expanduser().resolve()
    if opts.resume is not None and opts.run_id is not None:
        msg = "Use either --resume or --run-id"
        raise ResumeError(msg)
    run_id = opts.resume if opts.resume is not None else opts.run_id
    if run_id is None:
        run_id = str(uuid4())
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        msg = "Run ID must be a single directory name"
        raise ResumeError(msg)
    directory = run_root / run_id
    if opts.resume and not (directory / DATABASE_FILE).is_file():
        msg = f"No update run {run_id} under {run_root}"
        raise ResumeError(msg)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    # A caller-supplied ID must not overwrite another process's request before
    # either process acquires the (separate) repository workspace lock.
    try:
        with FileLock(directory / ".lock", timeout=0):
            return await _execute_run(opts, root, config, directory)
    except Timeout as error:
        msg = f"Update run {run_id} is already in use"
        raise ResumeError(msg) from error


async def _execute_run(
    opts: UpdateOptions, root: Path, config: UpdateConfig, directory: Path
) -> int:
    """Open one locked execution history and supervise its domain work."""
    from lib.update.cli import (  # noqa: PLC0415 -- register workflows before launch
        _durable_update,
        emit_run_result,
    )
    from lib.update.persistence import (  # noqa: PLC0415 -- lifecycle avoids import cycle
        IsolatedUpdateWorkspace,
    )

    store = RunStore(directory)
    identity = runtime_identity(root)
    try:
        store.read("request")
    except FileNotFoundError:
        # A process may die after creating SQLite but before saving its request.
        # No workflow or workspace can have started before this record exists.
        existing = False
    else:
        existing = True
    if opts.resume or existing:
        request = _resume_request(opts, store, root, identity)
        opts, config = request.options, request.config
        store = RunStore(directory)
    else:
        request = RunRequest(root, identity, opts, config)
        store.write(
            "request", TypeAdapter(RunRequest).dump_python(request, mode="json")
        )
    global _SESSION  # noqa: PLW0603 -- process workspace lock admits one session
    with IsolatedUpdateWorkspace(root, run_store=store) as candidate:
        run = _SESSION = Run(store, candidate, config, opts)
        try:
            DBOS(
                config={
                    "name": "nixcfg-update",
                    "system_database_url": f"sqlite:///{store.path}",
                    "application_version": identity,
                    # One database per run; the CLI holds its repository lock.
                    "executor_id": "local",
                    "enable_otlp": False,
                    "log_level": "ERROR",
                }
            )
            DBOS.launch()
            with SetWorkflowID("update"):
                handle = await DBOS.start_workflow_async(_durable_update)
            result = await handle.get_result()
        finally:
            run.closing = True
            tasks = tuple(run.tasks)
            for task in tasks:
                task.cancel()
            try:
                await await_cleanup(asyncio.gather(*tasks, return_exceptions=True))
            finally:
                try:
                    await await_cleanup(
                        asyncio.create_task(asyncio.to_thread(DBOS.destroy))
                    )
                finally:
                    _SESSION = None

    return emit_run_result(result, opts)
