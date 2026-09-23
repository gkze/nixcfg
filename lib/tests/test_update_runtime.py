"""Behavioral contracts for run-owned work, resource admission, and telemetry."""

import asyncio
import json
import threading
from concurrent.futures import CancelledError as ThreadCancelledError
from dataclasses import replace
from types import SimpleNamespace

import pytest

from lib.tests._updater_helpers import run_async
from lib.update import runtime
from lib.update.config import default_config


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ([], None),
        (["nix"], None),
        (["python", "build"], None),
        (["nix", "eval"], "eval"),
        (["/nix/store/immutable/bin/nix", "path-info"], "eval"),
        (["nix", "build"], "build"),
        (["nix", "run"], "build"),
        (["nix", "shell"], "build"),
        (["nix", "hash", "path"], None),
    ],
)
def test_process_adapters_share_resource_classification(
    args: list[str], expected: str | None
) -> None:
    """Only bounded Nix operations use a pool, including absolute executable paths."""
    assert runtime.command_resource(args) == expected


def test_runtime_scopes_reuse_parent_and_dispose_cache() -> None:
    """Nested callers share budgets; sequential invocations retain no prior results."""
    calls = 0

    async def compute() -> int:
        nonlocal calls
        calls += 1
        return calls

    async def run() -> None:
        assert runtime.active_runtime() is None
        assert await runtime.memoize("source", "same", compute) == 1
        assert await runtime.memoize("source", "same", compute) == 2
        with runtime.measure("standalone", "parse") as timing:
            pass
        assert timing.count == 1
        async with runtime.runtime_scope(default_config()) as first:
            async with runtime.runtime_scope(default_config()) as nested:
                assert nested is first
                assert runtime.active_runtime() is first
                assert await runtime.memoize("source", "same", compute) == 3
            assert await runtime.memoize("source", "same", compute) == 3
            assert first.timing("shared", "source").cache_hits == 1
        assert runtime.active_runtime() is None
        async with runtime.runtime_scope(default_config()) as second:
            assert second is not first
            assert await runtime.memoize("source", "same", compute) == 4

    run_async(run())


def test_runtime_memo_identity_includes_namespace_and_input() -> None:
    """Unrelated operations or changed content must not share a result."""
    values = iter(["a", "b", "c"])

    async def compute() -> str:
        return next(values)

    async def run() -> None:
        async with runtime.runtime_scope(default_config()):
            assert await runtime.memoize("first", "same", compute) == "a"
            assert await runtime.memoize("second", "same", compute) == "b"
            assert await runtime.memoize("first", "different", compute) == "c"

    run_async(run())


def test_cancelled_memo_consumer_does_not_cancel_shared_producer() -> None:
    """One consumer can leave while another still needs the same source download."""
    started = asyncio.Event()
    finish = asyncio.Event()
    calls = 0

    async def compute() -> str:
        nonlocal calls
        calls += 1
        started.set()
        await finish.wait()
        return "complete"

    async def run() -> None:
        async with runtime.runtime_scope(default_config()) as owner:
            first = asyncio.create_task(runtime.memoize("source", "same", compute))
            await started.wait()
            second = asyncio.create_task(runtime.memoize("source", "same", compute))
            await asyncio.sleep(0)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert not owner.pending["source", "same"].done()
            finish.set()
            assert await second == "complete"
            assert calls == 1

    run_async(run())


def test_runtime_exit_cancels_and_joins_abandoned_producer() -> None:
    """Scope exit waits for producer cleanup even after its consumer was cancelled."""
    started = asyncio.Event()
    cleaned_up = asyncio.Event()

    async def compute() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned_up.set()

    async def run() -> None:
        async with runtime.runtime_scope(default_config()) as owner:
            consumer = asyncio.create_task(runtime.memoize("source", "same", compute))
            await started.wait()
            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consumer
            producer = owner.pending["source", "same"]
        assert cleaned_up.is_set()
        assert producer.cancelled()
        assert ("source", "same") not in owner.pending
        assert runtime.active_runtime() is None

    run_async(run())


def test_runtime_exit_finishes_producer_cleanup_despite_repeated_cancellation() -> None:
    """Repeated cancellation cannot release workspace ownership during cleanup."""
    started = asyncio.Event()
    cleanup_started = asyncio.Event()
    finish_cleanup = asyncio.Event()
    cleaned_up = asyncio.Event()
    owner_state: runtime.UpdateRuntime | None = None

    async def successful() -> str:
        return "cached"

    async def compute() -> None:
        async with runtime.workspace_access(write=True):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup_started.set()
                await finish_cleanup.wait()
                cleaned_up.set()

    async def own_runtime() -> None:
        nonlocal owner_state
        async with runtime.runtime_scope(default_config()) as owner_state:
            await runtime.memoize("source", "complete", successful)
            await runtime.memoize("source", "pending", compute)

    async def run() -> None:
        async with asyncio.timeout(1):
            owner = asyncio.create_task(own_runtime())
            await started.wait()
            owner.cancel()
            await cleanup_started.wait()
            assert owner_state is not None
            producer = owner_state.pending["source", "pending"]
            for _ in range(2):
                owner.cancel()
                await asyncio.sleep(0)
                assert not owner.done()
                assert not producer.done()
                assert owner_state.workspace_owner is producer
                assert owner_state.joining_shared_work
            finish_cleanup.set()
            with pytest.raises(asyncio.CancelledError):
                await owner
            assert owner.cancelling() == 3
            assert cleaned_up.is_set()
            assert owner_state.workspace_owner is None
            assert not owner_state.joining_shared_work
            assert ("source", "pending") not in owner_state.pending
            assert owner_state.pending["source", "complete"].result() == "cached"

    run_async(run())


@pytest.mark.parametrize("already_done", [False, True])
def test_await_cleanup_preserves_successful_result(*, already_done: bool) -> None:
    """A cleanup result is unchanged whether it finishes before or during the join."""

    async def run() -> None:
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        if already_done:
            future.set_result("restored")
        else:
            asyncio.get_running_loop().call_soon(future.set_result, "restored")
        assert await runtime.await_cleanup(future) == "restored"

    run_async(run())


@pytest.mark.parametrize("cancel_owner", [False, True])
def test_await_cleanup_preserves_failure_even_after_caller_cancellation(
    *, cancel_owner: bool
) -> None:
    """A cleanup failure remains visible when cancellation also requests teardown."""

    async def run() -> None:
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(runtime.await_cleanup(future))
        await asyncio.sleep(0)
        if cancel_owner:
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        error = RuntimeError("workspace restoration failed")
        future.set_exception(error)
        with pytest.raises(RuntimeError, match="restoration failed") as caught:
            await task
        assert caught.value is error

    run_async(run())


def test_await_cleanup_propagates_independent_cleanup_cancellation() -> None:
    """Cancellation of the owned work itself must not leave the join waiting."""

    async def run() -> None:
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(runtime.await_cleanup(future))
        await asyncio.sleep(0)
        future.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run_async(run())


def test_failed_memo_waiter_cannot_evict_new_retry() -> None:
    """A late waiter observing failure A must not remove an already-running retry B."""
    fail = asyncio.Event()
    retry_started = asyncio.Event()
    finish_retry = asyncio.Event()

    async def initial() -> str:
        await fail.wait()
        msg = "transient failure"
        raise RuntimeError(msg)

    async def retry() -> str:
        retry_started.set()
        await finish_retry.wait()
        return "recovered"

    async def first() -> str:
        try:
            return await runtime.memoize("source", "same", initial)
        except RuntimeError:
            return await runtime.memoize("source", "same", retry)

    async def second() -> None:
        with pytest.raises(RuntimeError, match="transient failure"):
            await runtime.memoize("source", "same", initial)

    async def run() -> None:
        async with runtime.runtime_scope(default_config()) as owner:
            one = asyncio.create_task(first())
            two = asyncio.create_task(second())
            await asyncio.sleep(0)
            fail.set()
            await retry_started.wait()
            assert ("source", "same") in owner.pending
            assert not owner.pending["source", "same"].done()
            finish_retry.set()
            assert await one == "recovered"
            await two
            assert await runtime.memoize("source", "same", retry) == "recovered"

    run_async(run())


def test_cancelled_producer_is_evicted_and_can_be_retried() -> None:
    """Producer cancellation must not become a cached cancellation for later users."""
    calls = 0

    async def compute() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise asyncio.CancelledError
        return "retried"

    async def run() -> None:
        async with runtime.runtime_scope(default_config()) as owner:
            with pytest.raises(asyncio.CancelledError):
                await runtime.memoize("source", "same", compute)
            assert not owner.pending
            assert await runtime.memoize("source", "same", compute) == "retried"

    run_async(run())


def test_cancellation_racing_completed_producer_preserves_success() -> None:
    """A cancelled waiter cannot discard a result the shared producer already made."""

    async def run() -> None:
        async with runtime.runtime_scope(default_config()) as owner:

            async def compute() -> str:
                asyncio.get_running_loop().call_soon(consumer.cancel)
                return "complete"

            consumer = asyncio.create_task(runtime.memoize("source", "same", compute))
            with pytest.raises(asyncio.CancelledError):
                await consumer
            assert owner.pending["source", "same"].result() == "complete"
            assert await runtime.memoize("source", "same", compute) == "complete"

    run_async(run())


def test_resource_budgets_are_independent_and_release_on_failure() -> None:
    """Build saturation cannot block downloads, and failed work releases capacity."""
    config = replace(default_config(), max_nix_builds=1, max_downloads=1)
    started = asyncio.Event()
    release = asyncio.Event()
    entered: list[str] = []

    async def first_build() -> None:
        async with runtime.resource_slot("build", source="first", config=config):
            started.set()
            await release.wait()

    async def later_build() -> None:
        async with runtime.resource_slot("build", source="second", config=config):
            entered.append("second")

    async def run() -> None:
        async with runtime.runtime_scope(config):
            first = asyncio.create_task(first_build())
            await started.wait()
            second = asyncio.create_task(later_build())
            await asyncio.sleep(0)
            async with runtime.resource_slot(
                "download", source="download", config=config
            ):
                entered.append("download")
            assert entered == ["download"]
            release.set()
            await asyncio.gather(first, second)
            assert entered == ["download", "second"]
            failure = RuntimeError("failed build")
            with pytest.raises(RuntimeError, match="failed build"):
                async with runtime.resource_slot(
                    "build", source="failed", config=config
                ):
                    raise failure
            async with runtime.resource_slot("build", source="last", config=config):
                entered.append("last")

    run_async(run())


def test_cancelled_resource_waiter_does_not_release_owners_slot() -> None:
    """Cancelling a queued evaluation must not admit another one before the owner exits."""
    config = replace(default_config(), max_nix_evaluations=1)
    entered: list[str] = []

    async def waiting() -> None:
        async with runtime.resource_slot("eval", source="waiting", config=config):
            entered.append("waiting")

    async def run() -> None:
        async with runtime.runtime_scope(config) as owner:
            async with runtime.resource_slot("eval", source="owner", config=config):
                waiter = asyncio.create_task(waiting())
                await asyncio.sleep(0)
                waiter.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await waiter
                assert owner.slots["eval"].locked()
                assert not entered
            await waiting()
            assert entered == ["waiting"]

    run_async(run())


def test_source_attribution_is_scoped_and_restored() -> None:
    """Nested operations inherit source identity without leaking it to later work."""

    async def run() -> None:
        assert runtime.current_source() == "shared"
        async with runtime.resource_slot(
            "source", source="one", config=default_config()
        ):
            assert runtime.current_source() == "one"
            async with runtime.resource_slot(
                "eval", source="child", config=default_config()
            ):
                assert runtime.current_source() == "one"
        assert runtime.current_source() == "shared"
        assert runtime.active_runtime() is None

    run_async(run())


def test_timings_separate_wait_active_failures_and_redact_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reports contain stable aggregate evidence without exposing URL credentials or cache keys."""
    ticks = iter([10.0, 12.0, 20.0, 25.0])
    monkeypatch.setattr(runtime, "time", SimpleNamespace(monotonic=lambda: next(ticks)))

    async def run() -> None:
        async with runtime.runtime_scope(default_config()) as owner:
            async with runtime.resource_slot(
                "eval", source="source", config=default_config()
            ) as timing:
                timing.stdout_bytes += 3
            assert timing.wait_seconds == 2.0
            assert timing.elapsed_seconds == 5.0
            assert timing.count == 1
            owner.timing("https://user:secret@example.test/file?token=hidden", "source")
            payload = json.dumps(owner.report())
            assert "secret" not in payload
            assert "hidden" not in payload

    run_async(run())


@pytest.mark.parametrize("error", [RuntimeError("failed"), asyncio.CancelledError()])
def test_measure_records_error_category_and_active_time(error: BaseException) -> None:
    """Cancelled work is not reported as an ordinary failure."""

    async def run() -> None:
        async with runtime.runtime_scope(default_config()) as owner:
            with pytest.raises(type(error)), runtime.measure("source", "operation"):
                raise error
            timing = owner.timing("source", "operation")
            assert timing.count == 1
            assert timing.failed == int(isinstance(error, RuntimeError))
            assert timing.cancelled == int(isinstance(error, asyncio.CancelledError))
            assert timing.elapsed_seconds >= 0

    run_async(run())


def test_workspace_readers_overlap_and_writer_waits_for_every_reader() -> None:
    """Read-only evaluations overlap; materialization starts only after both finish."""
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    first_finish = asyncio.Event()
    second_finish = asyncio.Event()
    writer_started = asyncio.Event()

    async def reader(started: asyncio.Event, finish: asyncio.Event) -> None:
        async with runtime.workspace_access():
            started.set()
            await finish.wait()

    async def writer() -> None:
        async with runtime.workspace_access(write=True):
            writer_started.set()

    async def run() -> None:
        async with runtime.workspace_access():
            assert runtime.active_runtime() is None
        async with runtime.runtime_scope(default_config()) as owner:
            one = asyncio.create_task(reader(first_started, first_finish))
            two = asyncio.create_task(reader(second_started, second_finish))
            await first_started.wait()
            await second_started.wait()
            assert owner.workspace_readers == 2
            write = asyncio.create_task(writer())
            await asyncio.sleep(0)
            assert not writer_started.is_set()
            first_finish.set()
            await one
            assert not writer_started.is_set()
            second_finish.set()
            await two
            await write
            assert writer_started.is_set()
            assert owner.workspace_readers == 0
            assert owner.workspace_owner is None

    run_async(run())


def test_workspace_writer_excludes_readers_and_is_reentrant_only_in_owner_task() -> (
    None
):
    """A temporary-artifact consumer can evaluate inside its write transaction."""
    child_entered = asyncio.Event()

    async def child() -> None:
        async with runtime.workspace_access():
            child_entered.set()

    async def run() -> None:
        async with runtime.runtime_scope(default_config()) as owner:
            async with runtime.workspace_access(write=True):
                async with (
                    runtime.workspace_access(),
                    runtime.workspace_access(write=True),
                ):
                    assert owner.workspace_owner is asyncio.current_task()
                other = asyncio.create_task(child())
                await asyncio.sleep(0)
                assert not child_entered.is_set()
            await other
            assert child_entered.is_set()
            assert owner.workspace_owner is None

    run_async(run())


def test_queued_writer_runs_before_new_reader() -> None:
    """A busy evaluator stream cannot starve a materialization waiting to begin."""
    admitted: list[str] = []

    async def writer() -> None:
        async with runtime.workspace_access(write=True):
            admitted.append("writer")

    async def reader() -> None:
        async with runtime.workspace_access():
            admitted.append("reader")

    async def run() -> None:
        async with runtime.runtime_scope(default_config()) as owner:
            async with runtime.workspace_access():
                write = asyncio.create_task(writer())
                await asyncio.sleep(0)
                assert owner.workspace_writers_waiting == 1
                read = asyncio.create_task(reader())
                await asyncio.sleep(0)
                assert not admitted
            await asyncio.gather(write, read)
            assert admitted == ["writer", "reader"]

    run_async(run())


def test_workspace_reader_reenters_before_waiting_writer() -> None:
    """Fingerprint evaluation can nest a read after a writer starts waiting.

    The writer already waits for the outer read, so blocking that reader's nested
    evaluation behind the writer would deadlock the materialization key lookup.
    """
    admitted: list[str] = []

    async def writer() -> None:
        async with runtime.workspace_access(write=True):
            admitted.append("writer")

    async def run() -> None:
        async with runtime.runtime_scope(default_config()) as owner:
            async with runtime.workspace_access():
                write = asyncio.create_task(writer())
                await asyncio.sleep(0)
                assert owner.workspace_writers_waiting == 1
                async with asyncio.timeout(1), runtime.workspace_access():
                    admitted.append("nested reader")
                assert admitted == ["nested reader"]
            await write
            assert admitted == ["nested reader", "writer"]
            assert owner.workspace_readers == 0

    run_async(run())


def test_cancelled_queued_writer_unblocks_other_readers() -> None:
    """Removing a waiting writer restores read admission without leaking its count."""
    reader_admitted = asyncio.Event()

    async def writer() -> None:
        async with runtime.workspace_access(write=True):
            pytest.fail("writer should still be waiting")

    async def reader() -> None:
        async with runtime.workspace_access():
            reader_admitted.set()

    async def run() -> None:
        async with runtime.runtime_scope(default_config()) as owner:
            async with runtime.workspace_access():
                write = asyncio.create_task(writer())
                await asyncio.sleep(0)
                assert owner.workspace_writers_waiting == 1
                write.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await write
                assert owner.workspace_writers_waiting == 0
                await asyncio.create_task(reader())
                assert reader_admitted.is_set()
            assert owner.workspace_readers == 0

    run_async(run())


def test_workspace_read_to_write_upgrade_fails_without_corrupting_ownership() -> None:
    """An accidental upgrade fails promptly instead of waiting for its own reader."""

    async def run() -> None:
        async with runtime.runtime_scope(default_config()) as owner:
            async with runtime.workspace_access():
                with pytest.raises(RuntimeError, match="release access"):
                    async with runtime.workspace_access(write=True):
                        pytest.fail("reader upgrades cannot acquire write access")
                assert owner.workspace_writers_waiting == 0
                assert owner.workspace_owner is None
                async with runtime.workspace_access():
                    assert owner.workspace_readers == 1
            assert not owner.workspace_reader_owners
            async with runtime.workspace_access(write=True):
                assert owner.workspace_owner is asyncio.current_task()

    run_async(run())


def test_workspace_reader_cancellation_does_not_leak_admission() -> None:
    """Cancelling a reader waiting behind a writer cannot leave a phantom reader."""

    async def reader() -> None:
        async with runtime.workspace_access():
            pytest.fail("reader should still be waiting")

    async def run() -> None:
        async with runtime.runtime_scope(default_config()) as owner:
            async with runtime.workspace_access(write=True):
                read = asyncio.create_task(reader())
                await asyncio.sleep(0)
                read.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await read
                assert owner.workspace_readers == 0
            async with runtime.workspace_access(write=True):
                assert owner.workspace_owner is asyncio.current_task()

    run_async(run())


def test_thread_resource_without_active_runtime_measures_local_work() -> None:
    """Standalone synchronous callers need no owner loop or shared semaphore."""
    with runtime.thread_resource_slot(
        "eval", source="standalone", config=default_config()
    ) as timing:
        timing.stdout_bytes = 7
    assert timing.count == 1
    assert timing.stdout_bytes == 7
    assert timing.elapsed_seconds >= 0
    assert runtime.active_runtime() is None


def test_thread_resource_rejects_blocking_its_owner_loop() -> None:
    """An accidental synchronous call from async code must fail before blocking."""

    async def run() -> None:
        async with runtime.runtime_scope(default_config()):
            with pytest.raises(RuntimeError, match="worker without an event loop"):
                with runtime.thread_resource_slot(
                    "eval", source="invalid", config=default_config()
                ):
                    pytest.fail("event-loop calls cannot enter a blocking lease")

    run_async(run())


def test_thread_resource_contends_with_async_commands_and_joins_release() -> None:
    """A worker waits behind ordinary probes and returns only after releasing its slot."""
    entering = threading.Event()
    admitted = threading.Event()
    config = replace(default_config(), max_nix_evaluations=1)

    def command() -> str:
        entering.set()
        with runtime.thread_resource_slot(
            "eval", source="worker", config=config
        ) as timing:
            admitted.set()
            timing.stdout_bytes = 8
            timing.stderr_bytes = 3
            timing.input_bytes = 5
            timing.nonzero_exits = 1
        return "completed"

    async def run() -> None:
        async with runtime.runtime_scope(config) as owner:
            async with runtime.resource_slot("eval", source="probe", config=config):
                worker = asyncio.create_task(asyncio.to_thread(command))
                assert await asyncio.to_thread(entering.wait, 1)
                # Cover the polling interval while the real semaphore is held.
                await asyncio.sleep(0.075)
                assert not admitted.is_set()
            assert await worker == "completed"
            assert admitted.is_set()
            assert not owner.slots["eval"].locked()
            timing = owner.timing("worker", "eval")
            assert timing.count == 1
            assert timing.wait_seconds > 0
            assert timing.elapsed_seconds >= 0
            assert timing.stdout_bytes == 8
            assert timing.stderr_bytes == 3
            assert timing.input_bytes == 5
            assert timing.nonzero_exits == 1

    run_async(run())


@pytest.mark.parametrize(
    ("error", "signal_cancelled"),
    [
        (ValueError("command failed"), False),
        (ThreadCancelledError(), False),
        (asyncio.CancelledError(), False),
        (RuntimeError("worker cancellation"), True),
    ],
)
def test_thread_resource_records_worker_errors_and_releases(
    error: BaseException, *, signal_cancelled: bool
) -> None:
    """Worker errors retain their identity and count once without leaking capacity."""
    cancel_event = threading.Event()
    config = default_config()

    def fail_command() -> None:
        if signal_cancelled:
            cancel_event.set()
        raise error

    def command() -> None:
        with (
            pytest.raises(type(error)) as caught,
            runtime.thread_resource_slot(
                "build", source="worker", config=config, cancel_event=cancel_event
            ),
        ):
            fail_command()
        assert caught.value is error

    async def run() -> None:
        async with runtime.runtime_scope(config) as owner:
            await asyncio.to_thread(command)
            assert not owner.slots["build"].locked()
            timing = owner.timing("worker", "build")
            cancelled = signal_cancelled or isinstance(
                error, (ThreadCancelledError, asyncio.CancelledError)
            )
            assert timing.failed == int(not cancelled)
            assert timing.cancelled == int(cancelled)
            assert timing.count == 1

    run_async(run())


def test_thread_resource_cancels_queued_admission_without_releasing_owner() -> None:
    """A cancelled worker leaves the async owner's slot occupied and joins its waiter."""
    entering = threading.Event()
    cancel_event = threading.Event()
    config = replace(default_config(), max_nix_evaluations=1)

    def command() -> None:
        entering.set()
        with (
            pytest.raises(ThreadCancelledError),
            runtime.thread_resource_slot(
                "eval", source="worker", config=config, cancel_event=cancel_event
            ),
        ):
            pytest.fail("cancelled worker must not start a command")

    async def run() -> None:
        async with runtime.runtime_scope(config) as owner:
            async with runtime.resource_slot("eval", source="probe", config=config):
                worker = asyncio.create_task(asyncio.to_thread(command))
                assert await asyncio.to_thread(entering.wait, 1)
                await asyncio.sleep(0.075)
                cancel_event.set()
                await worker
                assert owner.slots["eval"].locked()
                assert owner.timing("worker", "eval").count == 0
            async with runtime.resource_slot("eval", source="after", config=config):
                assert owner.slots["eval"].locked()

    run_async(run())


def test_thread_resource_cancellation_racing_admission_releases_lease() -> None:
    """A cancellation already signalled when capacity opens cannot leak that slot."""
    cancel_event = threading.Event()
    cancel_event.set()
    config = default_config()

    def command() -> None:
        with (
            pytest.raises(ThreadCancelledError),
            runtime.thread_resource_slot(
                "eval", source="worker", config=config, cancel_event=cancel_event
            ),
        ):
            pytest.fail("cancelled worker must not start a command")

    async def run() -> None:
        async with runtime.runtime_scope(config) as owner:
            await asyncio.to_thread(command)
            assert not owner.slots["eval"].locked()
            assert owner.timing("worker", "eval").cancelled == 1

    run_async(run())


def test_join_shared_work_rejects_new_producers_during_cleanup() -> None:
    """Cleanup cannot recursively create work that escapes the captured join set."""
    from lib.update.config import resolve_config
    from lib.update.runtime import join_shared_work, memoize, runtime_scope

    async def run() -> None:
        await join_shared_work()
        started = asyncio.Event()
        rejected = asyncio.Event()

        async def factory() -> str:
            started.set()
            try:
                await asyncio.Future()
            finally:
                with pytest.raises(RuntimeError, match="during producer cleanup"):
                    await memoize("late", "new", factory)
                rejected.set()
            return "unused"

        async with runtime_scope(resolve_config()) as runtime:
            consumer = asyncio.create_task(memoize("producer", "key", factory))
            await started.wait()
            await join_shared_work()
            with pytest.raises(asyncio.CancelledError):
                await consumer
            assert rejected.is_set()
            assert ("late", "new") not in runtime.pending
            assert not runtime.joining_shared_work

            async def retry() -> str:
                return "recovered"

            assert await memoize("producer", "key", retry) == "recovered"

    asyncio.run(run())
