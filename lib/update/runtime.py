"""Run-owned resource budgets, successful memoization, and aggregate timings."""

import asyncio
import time
from concurrent.futures import CancelledError as ThreadCancelledError
from concurrent.futures import Future as ThreadFuture
from contextlib import asynccontextmanager, contextmanager, suppress
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from lib.diagnostics import redact_urls

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
    from threading import Event

    from lib.update.config import UpdateConfig

type Resource = Literal["eval", "build", "download", "materialize", "source"]


def command_resource(args: Sequence[str]) -> Literal["eval", "build"] | None:
    """Classify constructed Nix command lines consistently in both process adapters."""
    if len(args) <= 1 or Path(args[0]).name != "nix":
        return None
    match args[1]:
        case "eval" | "path-info":
            return "eval"
        case "build" | "run" | "shell":
            return "build"
        case _:
            return None


@dataclass(slots=True)
class OperationTiming:
    """Bounded aggregate for one source and operation, without command arguments."""

    count: int = 0
    failed: int = 0
    nonzero_exits: int = 0
    cancelled: int = 0
    cache_hits: int = 0
    wait_seconds: float = 0.0
    elapsed_seconds: float = 0.0
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    input_bytes: int = 0


@dataclass(slots=True)
class UpdateRuntime:
    """Own concurrency and pending shared work until an update run has joined."""

    config: UpdateConfig
    timings: dict[tuple[str, str], OperationTiming] = field(default_factory=dict)
    pending: dict[tuple[str, str], asyncio.Task[object]] = field(default_factory=dict)
    joining_shared_work: bool = False
    slots: dict[Resource, asyncio.Semaphore] = field(init=False)
    loop: asyncio.AbstractEventLoop = field(init=False)
    workspace_condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    workspace_owner: asyncio.Task[object] | None = None
    workspace_reader_owners: set[asyncio.Task[object] | None] = field(
        default_factory=set
    )
    workspace_writers_waiting: int = 0

    def __post_init__(self) -> None:
        """Create each budget in its owning event loop."""
        self.loop = asyncio.get_running_loop()
        self.slots = {
            "source": asyncio.Semaphore(self.config.max_source_tasks),
            "eval": asyncio.Semaphore(self.config.max_nix_evaluations),
            "build": asyncio.Semaphore(self.config.max_nix_builds),
            "download": asyncio.Semaphore(self.config.max_downloads),
            "materialize": asyncio.Semaphore(self.config.max_materializations),
        }

    @property
    def workspace_readers(self) -> int:
        """Derive admission state from the tasks that own read access."""
        return len(self.workspace_reader_owners)

    def timing(self, source: str, operation: str) -> OperationTiming:
        """Aggregate only stable operation labels and redacted source names."""
        return self.timings.setdefault(
            (redact_urls(source), operation), OperationTiming()
        )

    def report(self) -> list[dict[str, str | int | float]]:
        """Project JSON-safe timings without cache identities or credentials."""
        return [
            {"source": source, "operation": operation, **asdict(timing)}
            for (source, operation), timing in sorted(self.timings.items())
        ]


_ACTIVE_RUNTIME: ContextVar[UpdateRuntime | None] = ContextVar(
    "update_runtime", default=None
)
_ACTIVE_SOURCE: ContextVar[str] = ContextVar("update_source", default="shared")


def current_source() -> str:
    """Attribute shared HTTP and generator helpers to the source task owner."""
    return _ACTIVE_SOURCE.get()


def active_runtime() -> UpdateRuntime | None:
    """Return the current run without creating global mutable caches."""
    return _ACTIVE_RUNTIME.get()


@asynccontextmanager
async def runtime_scope(
    config: UpdateConfig, *, existing: UpdateRuntime | None = None
) -> AsyncIterator[UpdateRuntime]:
    """Share nested scopes and reap memoized work before disposing run state."""
    if (shared := existing or active_runtime()) is not None:
        token = _ACTIVE_RUNTIME.set(shared)
        try:
            yield shared
        finally:
            _ACTIVE_RUNTIME.reset(token)
        return
    runtime = UpdateRuntime(config)
    token = _ACTIVE_RUNTIME.set(runtime)
    try:
        yield runtime
    finally:
        try:
            await join_shared_work()
        finally:
            _ACTIVE_RUNTIME.reset(token)


async def join_shared_work() -> None:
    """Reap orphaned producers before their owning source workspace can close."""
    runtime = active_runtime()
    if runtime is None:
        return
    runtime.joining_shared_work = True
    tasks = tuple(runtime.pending.values())
    try:
        for task in tasks:
            if not task.done():
                task.cancel()
        await await_cleanup(asyncio.gather(*tasks, return_exceptions=True))
    finally:
        for identity, task in tuple(runtime.pending.items()):
            if task.done() and (task.cancelled() or task.exception() is not None):
                del runtime.pending[identity]
        runtime.joining_shared_work = False


async def await_cleanup[T](future: asyncio.Future[T]) -> T:
    """Join owned cleanup before propagating repeated caller cancellation.

    The owner must first signal or cancel the work once. Further cancellation
    requests interrupt only this wait, never the work's cleanup. Its failure
    takes precedence over deferred caller cancellation, preserving cleanup errors.
    """
    cancellation: asyncio.CancelledError | None = None
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError as error:
            cancellation = error
    result = future.result()
    if cancellation is not None:
        raise cancellation
    return result


@contextmanager
def measure(source: str, operation: str) -> Iterator[OperationTiming]:
    """Measure active time and failures, including synchronous validation work."""
    runtime = active_runtime()
    timing = OperationTiming() if runtime is None else runtime.timing(source, operation)
    timing.count += 1
    started = time.monotonic()
    try:
        yield timing
    except asyncio.CancelledError:
        timing.cancelled += 1
        raise
    except BaseException:
        timing.failed += 1
        raise
    finally:
        timing.elapsed_seconds += time.monotonic() - started


@asynccontextmanager
async def resource_slot(
    kind: Resource, *, source: str, config: UpdateConfig
) -> AsyncIterator[OperationTiming]:
    """Separate admission waiting from active resource use."""
    async with runtime_scope(config) as runtime:
        timing = runtime.timing(source, kind)
        started = time.monotonic()
        try:
            await runtime.slots[kind].acquire()
        finally:
            timing.wait_seconds += time.monotonic() - started
        try:
            token = _ACTIVE_SOURCE.set(source) if kind == "source" else None
            with measure(source, kind) as active:
                yield active
        finally:
            if token is not None:
                _ACTIVE_SOURCE.reset(token)
            runtime.slots[kind].release()


@contextmanager
def thread_resource_slot(
    kind: Resource,
    *,
    source: str,
    config: UpdateConfig,
    cancel_event: Event | None = None,
) -> Iterator[OperationTiming]:
    """Admit a joined worker's command through its owning event loop's budget.

    The caller already owns any required workspace access until the worker joins.
    Only admission and release run on the loop; blocking waits stay in the worker.
    The yielded command counters merge on the loop before this context returns.
    """
    runtime = active_runtime()
    if runtime is None:
        with measure(source, kind) as timing:
            yield timing
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        msg = "thread_resource_slot must run in a worker without an event loop"
        raise RuntimeError(msg)

    started: ThreadFuture[asyncio.Task[object]] = ThreadFuture()
    admitted: ThreadFuture[OperationTiming] = ThreadFuture()
    release = asyncio.Event()
    command = OperationTiming()

    async def lease() -> None:
        started.set_result(cast("asyncio.Task[object]", asyncio.current_task()))
        try:
            async with resource_slot(kind, source=source, config=config) as aggregate:
                admitted.set_result(command)
                try:
                    await release.wait()
                finally:
                    aggregate.failed += command.failed
                    aggregate.cancelled += command.cancelled
                    aggregate.nonzero_exits += command.nonzero_exits
                    aggregate.stdout_bytes += command.stdout_bytes
                    aggregate.stderr_bytes += command.stderr_bytes
                    aggregate.input_bytes += command.input_bytes
        except BaseException as error:
            if not admitted.done():
                admitted.set_exception(error)
            raise

    completed = asyncio.run_coroutine_threadsafe(lease(), runtime.loop)
    task = started.result()
    timing = _wait_for_thread_resource(
        admitted, completed, task, runtime.loop, cancel_event
    )
    try:
        yield timing
    except BaseException as error:
        if isinstance(error, (asyncio.CancelledError, ThreadCancelledError)) or (
            cancel_event is not None and cancel_event.is_set()
        ):
            timing.cancelled += 1
        else:
            timing.failed += 1
        raise
    finally:
        runtime.loop.call_soon_threadsafe(release.set)
        completed.result()


def _wait_for_thread_resource(
    admitted: ThreadFuture[OperationTiming],
    completed: ThreadFuture[None],
    task: asyncio.Task[object],
    loop: asyncio.AbstractEventLoop,
    cancel_event: Event | None,
) -> OperationTiming:
    """Poll worker cancellation and join the actual lease before reporting failure."""
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                loop.call_soon_threadsafe(task.cancel)
                # Cancelling the concurrent future itself would report completion
                # before asyncio had released the slot. Join the lease instead.
                completed.result()
            try:
                return admitted.result(timeout=0.05)
            except TimeoutError:
                continue
    except BaseException:
        loop.call_soon_threadsafe(task.cancel)
        with suppress(BaseException):
            completed.result()
        raise


@asynccontextmanager
async def workspace_access(*, write: bool = False) -> AsyncIterator[None]:
    """Keep temporary artifact mutations invisible to unrelated evaluations.

    Reentrancy belongs to the current task, so a spawned shared producer cannot
    accidentally inherit permission to mutate after its consumer has cancelled.
    Prepare probes before spawning child builds; exact derivation builds do not
    need the mutable checkout once evaluation has completed.
    """
    runtime = active_runtime()
    owner = asyncio.current_task()
    if (
        runtime is None
        or runtime.workspace_owner is owner
        or (not write and owner in runtime.workspace_reader_owners)
    ):
        yield
        return
    if write and owner in runtime.workspace_reader_owners:
        msg = "Workspace readers must release access before requesting a write"
        raise RuntimeError(msg)
    timing = runtime.timing(current_source(), "workspace")
    started = time.monotonic()
    async with runtime.workspace_condition:
        try:
            if write:
                runtime.workspace_writers_waiting += 1
                try:
                    await runtime.workspace_condition.wait_for(
                        lambda: (
                            runtime.workspace_owner is None
                            and runtime.workspace_readers == 0
                        )
                    )
                finally:
                    runtime.workspace_writers_waiting -= 1
                    runtime.workspace_condition.notify_all()
                runtime.workspace_owner = owner
            else:
                await runtime.workspace_condition.wait_for(
                    lambda: (
                        runtime.workspace_owner is None
                        and runtime.workspace_writers_waiting == 0
                    )
                )
                runtime.workspace_reader_owners.add(owner)
        finally:
            timing.wait_seconds += time.monotonic() - started
    try:
        with measure(current_source(), "workspace"):
            yield
    finally:
        async with runtime.workspace_condition:
            if write:
                runtime.workspace_owner = None
            else:
                runtime.workspace_reader_owners.remove(owner)
            runtime.workspace_condition.notify_all()


async def memoize[T](
    namespace: str, key: str, factory: Callable[[], Awaitable[T]]
) -> T:
    """Share exact-input work within a run, never retaining failed results."""
    runtime = active_runtime()
    if runtime is None:
        return await factory()
    if runtime.joining_shared_work:
        msg = "Cannot start shared update work during producer cleanup"
        raise RuntimeError(msg)
    identity = (namespace, key)
    task = runtime.pending.get(identity)
    if task is None:

        async def compute() -> T:
            return await factory()

        task = asyncio.create_task(compute())
        runtime.pending[identity] = task
    else:
        runtime.timing("shared", namespace).cache_hits += 1
    try:
        # A cancelled consumer cannot cancel work still needed by another.
        return cast("T", await asyncio.shield(task))
    except BaseException:
        if (
            runtime.pending.get(identity) is task
            and task.done()
            and (task.cancelled() or task.exception() is not None)
        ):
            runtime.pending.pop(identity, None)
        raise
