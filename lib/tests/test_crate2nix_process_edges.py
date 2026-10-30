"""Focused process, cancellation, and retry tests for crate2nix regeneration."""

import asyncio
import errno
import signal
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib.update import crate2nix
from lib.update.config import resolve_config
from lib.update.runtime import runtime_scope


class _StubbornProcess:
    """Minimal process boundary that needs both group and leader escalation."""

    pid = 4242

    def __init__(self) -> None:
        self.killed = False
        self.wait_timeouts: list[float | None] = []

    def poll(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeouts.append(timeout)
        if timeout is not None:
            raise subprocess.TimeoutExpired("crate2nix", timeout)
        return 0

    def kill(self) -> None:
        self.killed = True


class _Pipe:
    """Small closeable stream stand-in for the selector boundary."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Selector:
    """Deterministic selector that exposes each pipe until EOF is observed."""

    def __init__(self, stdout: _Pipe, stderr: _Pipe) -> None:
        self._streams = {"stdout": stdout, "stderr": stderr}
        self.closed = False

    def get_map(self) -> dict[str, _Pipe]:
        return self._streams

    def select(self, _timeout: float) -> list[tuple[SimpleNamespace, int]]:
        return [
            (SimpleNamespace(fd=name, data=name), 0) for name in tuple(self._streams)
        ]

    def unregister(self, stream: _Pipe) -> None:
        name = next(
            name for name, candidate in self._streams.items() if candidate is stream
        )
        del self._streams[name]

    def close(self) -> None:
        self.closed = True


class _IdleThenEofSelector(_Selector):
    """Expose one deterministic idle interval before both streams reach EOF."""

    def __init__(self, stdout: _Pipe, stderr: _Pipe) -> None:
        super().__init__(stdout, stderr)
        self._idle = True

    def select(self, timeout: float) -> list[tuple[SimpleNamespace, int]]:
        if self._idle:
            self._idle = False
            return []
        return super().select(timeout)


class _CompletedProcess:
    """Minimal successful process leader used by the collection loop."""

    def poll(self) -> int:
        return 0

    def wait(self) -> int:
        return 0


class _ContendedLock:
    """Lock stand-in that becomes available after one timed wait."""

    def __init__(self, *, cancel_on_acquire: threading.Event | None = None) -> None:
        self.cancel_on_acquire = cancel_on_acquire
        self.released = False
        self.timed_attempts = 0

    def acquire(
        self,
        *,
        blocking: bool = True,
        timeout: float = -1,
    ) -> bool:
        del timeout
        if not blocking:
            return False
        self.timed_attempts += 1
        if self.timed_attempts == 1:
            return False
        if self.cancel_on_acquire is not None:
            self.cancel_on_acquire.set()
        return True

    def release(self) -> None:
        self.released = True


class _CancelDuringBackoff:
    """Cancellation boundary that flips while a retry delay is pending."""

    def __init__(self) -> None:
        self.cancelled = False

    def is_set(self) -> bool:
        return self.cancelled

    def wait(self, _timeout: float) -> bool:
        self.cancelled = True
        return True


def test_progress_reporting_skips_control_only_lines() -> None:
    """A control-only line should not suppress a later useful progress line."""
    progress: list[str] = []

    assert crate2nix._report_progress_chunk(
        "\x1b[31m\x1b[0m\nfetching source",
        progress.append,
    )
    assert progress == ["fetching source"]


def test_process_group_probe_reports_a_live_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful signal-zero probe means the managed group is still live."""
    monkeypatch.setattr(crate2nix.os, "killpg", lambda _group, _signal: None)

    assert crate2nix._process_group_exists(4242)


def test_process_group_cleanup_escalates_and_reaps_a_stubborn_leader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A group surviving TERM must receive KILL and have its leader reaped."""
    process = _StubbornProcess()
    signals: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(crate2nix, "_process_group_exists", lambda _group: True)
    monkeypatch.setattr(
        crate2nix.os,
        "killpg",
        lambda group, sent_signal: signals.append((group, sent_signal)),
    )

    crate2nix._terminate_process_group(process, grace_seconds=0)  # type: ignore[arg-type]

    assert signals == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)]
    assert process.killed
    assert process.wait_timeouts == [0, None]


def test_process_collection_rejects_a_missing_output_pipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Managed execution must fail closed when Popen omits a requested pipe."""
    process = SimpleNamespace(pid=4242, stdout=None, stderr=object())
    terminated: list[object] = []
    monkeypatch.setattr(
        crate2nix,
        "_terminate_process_group",
        lambda candidate: terminated.append(candidate),
    )

    with pytest.raises(RuntimeError, match="did not expose output pipes"):
        crate2nix._prepare_process_collection(process)  # type: ignore[arg-type]

    assert terminated == [process]


def test_process_collection_retries_a_nonblocking_pipe_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient EAGAIN-style read must not terminate managed collection."""
    stdout = _Pipe()
    stderr = _Pipe()
    selector = _Selector(stdout, stderr)
    captures = {
        "stdout": crate2nix._BoundedCapture(),
        "stderr": crate2nix._BoundedCapture(),
    }
    monkeypatch.setattr(
        crate2nix,
        "_prepare_process_collection",
        lambda _process: (selector, captures, stdout, stderr),
    )
    stdout_reads = 0

    def _read(fd: str, _size: int) -> bytes:
        nonlocal stdout_reads
        if fd == "stdout" and stdout_reads == 0:
            stdout_reads += 1
            raise BlockingIOError
        return b""

    monkeypatch.setattr(crate2nix.os, "read", _read)

    completed = crate2nix._collect_managed_process(
        _CompletedProcess(),  # type: ignore[arg-type]
        ["crate2nix"],
        timeout=1,
    )

    assert completed.returncode == 0
    assert stdout_reads == 1
    assert stdout.closed
    assert stderr.closed
    assert selector.closed


class _GitCacheStallSelector(_Selector):
    """Deliver one git-cache transport error, then stay quiet."""

    def __init__(self, stdout: _Pipe, stderr: _Pipe, clock: dict[str, float]) -> None:
        super().__init__(stdout, stderr)
        self._clock = clock
        self._sent = False

    def select(self, _timeout: float) -> list[tuple[SimpleNamespace, int]]:
        if not self._sent:
            self._sent = True
            return [(SimpleNamespace(fd="stderr", data="stderr"), 0)]
        self._clock["now"] += self._clock["step"]
        return []


class _RecoveringGitCacheSelector(_GitCacheStallSelector):
    """Idle briefly after ENOBUFS, then let both streams finish."""

    def __init__(self, stdout: _Pipe, stderr: _Pipe, clock: dict[str, float]) -> None:
        super().__init__(stdout, stderr, clock)
        self._idled = False

    def select(self, timeout: float) -> list[tuple[SimpleNamespace, int]]:
        if not self._sent:
            return super().select(timeout)
        if not self._idled:
            self._idled = True
            self._clock["now"] += self._clock["step"]
            return []
        return _Selector.select(self, timeout)


def test_process_collection_aborts_a_silent_git_cache_transport_stall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A git-cache fetch that goes silent after ENOBUFS must not sit out the timeout.

    Update 40606669118 prepare-darwin-retry logged ``No buffer space available``
    while unpacking goose, then heartbeats until the 2400s crate2nix budget.
    """
    stdout = _Pipe()
    stderr = _Pipe()
    clock = {"now": 1000.0, "step": crate2nix._CRATE2NIX_TRANSPORT_STALL_SECONDS}
    selector = _GitCacheStallSelector(stdout, stderr, clock)
    captures = {
        "stdout": crate2nix._BoundedCapture(),
        "stderr": crate2nix._BoundedCapture(),
    }
    reads = iter((
        b"unpacking 'github:aaif-goose/goose/9949' into the Git cache...\n"
        b"error (ignored): write of 9157 bytes: No buffer space available\n",
        b"",
    ))
    terminated: list[object] = []
    monkeypatch.setattr(
        crate2nix,
        "_prepare_process_collection",
        lambda _process: (selector, captures, stdout, stderr),
    )
    monkeypatch.setattr(crate2nix.os, "read", lambda _fd, _size: next(reads))
    monkeypatch.setattr(crate2nix.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        crate2nix,
        "_terminate_process_group",
        lambda process: terminated.append(process),
    )
    process = _CompletedProcess()

    with pytest.raises(RuntimeError, match="git fetch stalled") as raised:
        crate2nix._collect_managed_process(
            process,  # type: ignore[arg-type]
            ["nix", "build", "goose-cli-crate2nix-src"],
            timeout=2400,
        )

    message = str(raised.value)
    assert "No buffer space available" in message
    assert "into the Git cache" in message
    assert terminated == [process]
    assert crate2nix._is_retryable_crate2nix_generate_failure(message)


def test_process_collection_keeps_a_git_cache_fetch_that_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ignored ENOBUFS followed by a short pause is not a stalled fetch."""
    stdout = _Pipe()
    stderr = _Pipe()
    clock = {"now": 1000.0, "step": 10.0}
    selector = _RecoveringGitCacheSelector(stdout, stderr, clock)
    captures = {
        "stdout": crate2nix._BoundedCapture(),
        "stderr": crate2nix._BoundedCapture(),
    }
    chunks = {
        "stderr": iter((
            b"error (ignored): write of 9157 bytes: No buffer space available\n",
            b"",
        )),
        "stdout": iter((b"/nix/store/goose\n", b"")),
    }
    monkeypatch.setattr(
        crate2nix,
        "_prepare_process_collection",
        lambda _process: (selector, captures, stdout, stderr),
    )
    monkeypatch.setattr(crate2nix.os, "read", lambda fd, _size: next(chunks[fd]))
    monkeypatch.setattr(crate2nix.time, "monotonic", lambda: clock["now"])

    completed = crate2nix._collect_managed_process(
        _CompletedProcess(),  # type: ignore[arg-type]
        ["nix", "build"],
        timeout=2400,
    )

    assert completed.returncode == 0
    assert completed.stdout == "/nix/store/goose\n"
    assert captures["stderr"].transport_stall


def test_process_collection_reports_a_deterministic_idle_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A quiet live process should emit its elapsed-time heartbeat exactly once."""
    stdout = _Pipe()
    stderr = _Pipe()
    selector = _IdleThenEofSelector(stdout, stderr)
    captures = {
        "stdout": crate2nix._BoundedCapture(),
        "stderr": crate2nix._BoundedCapture(),
    }
    monotonic_values = iter((100.0, 100.0, 131.0, 131.0, 131.0))
    progress: list[str] = []
    monkeypatch.setattr(
        crate2nix,
        "_prepare_process_collection",
        lambda _process: (selector, captures, stdout, stderr),
    )
    monkeypatch.setattr(crate2nix.os, "read", lambda _fd, _size: b"")
    monkeypatch.setattr(crate2nix.time, "monotonic", lambda: next(monotonic_values))

    completed = crate2nix._collect_managed_process(
        _CompletedProcess(),  # type: ignore[arg-type]
        ["crate2nix"],
        timeout=60,
        progress=progress.append,
    )

    assert completed.returncode == 0
    assert progress == ["crate2nix command still running (31s elapsed)"]
    assert stdout.closed
    assert stderr.closed
    assert selector.closed


@pytest.mark.parametrize("command", ["eval", "path-info", "build", "run", "shell"])
@pytest.mark.parametrize("returncode", [0, 1])
def test_worker_nix_commands_share_admission_and_record_process_results(
    monkeypatch: pytest.MonkeyPatch, command: str, returncode: int
) -> None:
    """Worker processes use the same bounded pool and output accounting as async work."""
    stdout = _Pipe()
    stderr = _Pipe()
    selector = _Selector(stdout, stderr)
    captures = {
        "stdout": crate2nix._BoundedCapture(),
        "stderr": crate2nix._BoundedCapture(),
    }
    monkeypatch.setattr(
        crate2nix,
        "_prepare_process_collection",
        lambda _process: (selector, captures, stdout, stderr),
    )
    chunks = {
        "stdout": iter(("résult\n".encode(), b"")),
        "stderr": iter((b"error\n", b"")),
    }
    monkeypatch.setattr(crate2nix.os, "read", lambda fd, _size: next(chunks[fd]))
    process = SimpleNamespace(poll=lambda: returncode, wait=lambda: returncode)
    started: list[str] = []

    def start(*_args: object, **_kwargs: object) -> object:
        started.append(command)
        return process

    monkeypatch.setattr(crate2nix.subprocess, "Popen", start)

    async def exercise() -> None:
        config = resolve_config()
        async with runtime_scope(config) as runtime:
            kind = "eval" if command in {"eval", "path-info"} else "build"
            # Exhaust the actual pool before starting the worker. Admission must
            # wait until the async owner returns capacity.
            semaphore = runtime.slots[kind]
            capacity = (
                config.max_nix_evaluations if kind == "eval" else config.max_nix_builds
            )
            for _ in range(capacity):
                await semaphore.acquire()
            worker = asyncio.create_task(
                asyncio.to_thread(crate2nix._run, ["nix", command])
            )
            await asyncio.sleep(0)
            assert started == []
            for _ in range(capacity):
                semaphore.release()
            if returncode:
                with pytest.raises(RuntimeError, match="error"):
                    await worker
            else:
                result = await worker
                assert result.stdout == "résult\n"
            timing = runtime.timing("shared", kind)
            assert timing.count == 1
            assert timing.failed == returncode
            assert timing.nonzero_exits == returncode
            assert timing.stdout_bytes == len("résult\n".encode())
            assert timing.stderr_bytes == len(b"error\n")
            assert not semaphore.locked()

    asyncio.run(exercise())
    assert started == [command]


def test_worker_cancellation_during_nix_admission_starts_no_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation while queued releases the pending lease before worker cleanup."""
    cancel_event = threading.Event()
    entered = threading.Event()
    original = crate2nix.update_runtime.thread_resource_slot

    def admission(*args: object, **kwargs: object) -> object:
        entered.set()
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(crate2nix.update_runtime, "thread_resource_slot", admission)
    monkeypatch.setattr(
        crate2nix.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("Queued cancellation must not spawn"),
    )

    async def exercise() -> None:
        config = resolve_config()
        async with runtime_scope(config) as runtime:
            for _ in range(config.max_nix_builds):
                await runtime.slots["build"].acquire()
            worker = asyncio.create_task(
                asyncio.to_thread(
                    crate2nix._run, ["nix", "build"], cancel_event=cancel_event
                )
            )
            assert await asyncio.to_thread(entered.wait, 1)
            cancel_event.set()
            with pytest.raises(crate2nix.Crate2NixCommandCancelledError, match="slot"):
                await worker
            for _ in range(config.max_nix_builds):
                runtime.slots["build"].release()

    asyncio.run(exercise())


def test_run_honors_cancellation_before_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-cancelled work must not create a subprocess at all."""
    cancel_event = threading.Event()
    cancel_event.set()
    monkeypatch.setattr(
        crate2nix.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("Popen should not be called"),
    )

    with pytest.raises(
        crate2nix.Crate2NixCommandCancelledError,
        match="cancelled before start",
    ):
        crate2nix._run(["crate2nix"], cancel_event=cancel_event)


@pytest.mark.parametrize(
    ("failure", "expected_exception"),
    [
        (
            OSError(errno.ENOSPC, "No space left on device"),
            crate2nix.Crate2NixResourceError,
        ),
        (OSError(errno.EACCES, "Permission denied"), OSError),
    ],
    ids=("enospc", "other-os-error"),
)
def test_run_classifies_process_start_failures(
    monkeypatch: pytest.MonkeyPatch,
    failure: OSError,
    expected_exception: type[BaseException],
) -> None:
    """Only local storage exhaustion should receive the ENOSPC error class."""
    monkeypatch.setattr(
        crate2nix.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )

    with pytest.raises(expected_exception):
        crate2nix._run(["crate2nix"])


def test_generate_lock_waits_without_a_progress_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Headless callers should wait for the shared cache without reporting progress."""
    lock = _ContendedLock()
    monkeypatch.setattr(crate2nix, "_CRATE2NIX_GENERATE_LOCK", lock)

    crate2nix._acquire_generate_lock(threading.Event(), None)

    assert lock.timed_attempts == 2
    assert not lock.released


def test_generate_lock_releases_when_cancelled_as_it_becomes_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation racing lock acquisition must not strand the shared cache lock."""
    cancel_event = threading.Event()
    lock = _ContendedLock(cancel_on_acquire=cancel_event)
    monkeypatch.setattr(crate2nix, "_CRATE2NIX_GENERATE_LOCK", lock)

    with pytest.raises(crate2nix.Crate2NixCommandCancelledError):
        crate2nix._acquire_generate_lock(cancel_event, None)

    assert lock.released


def test_restore_outputs_propagates_non_storage_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Non-ENOSPC filesystem failures must retain their original exception."""
    output = tmp_path / "Cargo.nix"
    output.write_text("partial", encoding="utf-8")
    failure = OSError(errno.EACCES, "Permission denied")
    monkeypatch.setattr(
        Path,
        "unlink",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )

    with pytest.raises(OSError, match="Permission denied") as raised:
        crate2nix._restore_generated_outputs((output,), {})

    assert raised.value is failure


def test_generate_retry_budget_has_one_terminal_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exhausted shared deadline must stop before starting another attempt."""
    monkeypatch.setattr(crate2nix.time, "monotonic", lambda: 10.0)

    with pytest.raises(
        crate2nix.Crate2NixCommandTimeoutError,
        match="exhausted its 5.0s total timeout budget",
    ):
        crate2nix._remaining_generate_budget(
            9.0,
            total_timeout=5.0,
            args=["crate2nix", "generate"],
        )


def test_managed_generation_forwards_cancellation_and_progress(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Managed generation must pass its lifecycle controls to the process boundary."""
    cancel_event = threading.Event()
    progress: list[str] = []
    seen: dict[str, object] = {}

    def _run(
        args: list[str],
        *,
        env: dict[str, str],
        timeout: float,
        cancel_event: threading.Event,
        progress,
    ) -> subprocess.CompletedProcess[str]:
        seen.update({
            "args": args,
            "env": env,
            "timeout": timeout,
            "cancel_event": cancel_event,
            "progress": progress,
        })
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(crate2nix, "_run", _run)

    completed = crate2nix._run_crate2nix_generate(
        ["crate2nix", "generate"],
        env={"CARGO_HOME": str(tmp_path)},
        generated_outputs=(),
        cancel_event=cancel_event,
        progress=progress.append,
    )

    assert completed.returncode == 0
    assert seen["args"] == ["crate2nix", "generate"]
    assert seen["env"] == {"CARGO_HOME": str(tmp_path)}
    assert seen["cancel_event"] is cancel_event
    assert seen["progress"] == progress.append
    assert isinstance(seen["timeout"], float)


def test_retry_reports_progress_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient failure should be visible to managed callers before retrying."""
    progress: list[str] = []
    calls = 0

    class _NoDelayEvent:
        def is_set(self) -> bool:
            return False

        def wait(self, _timeout: float) -> bool:
            return False

    def _run(*_args, **_kwargs) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("nix-prefetch-git\nfatal: early EOF")
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr(crate2nix, "_run", _run)

    completed = crate2nix._run_crate2nix_generate(
        ["crate2nix", "generate"],
        env={},
        generated_outputs=(),
        cancel_event=_NoDelayEvent(),  # type: ignore[arg-type]
        progress=progress.append,
    )

    assert completed.returncode == 0
    assert calls == 2
    assert progress == [
        "Retrying crate2nix generation after transient network failure (1/3)..."
    ]


def test_retry_cancels_during_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation during retry backoff must prevent the next subprocess."""
    cancel_event = _CancelDuringBackoff()
    calls = 0

    def _run(*_args, **_kwargs) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        raise RuntimeError("nix-prefetch-git\nfatal: early EOF")

    monkeypatch.setattr(crate2nix, "_run", _run)

    with pytest.raises(crate2nix.Crate2NixCommandCancelledError):
        crate2nix._run_crate2nix_generate(
            ["crate2nix", "generate"],
            env={},
            generated_outputs=(),
            cancel_event=cancel_event,  # type: ignore[arg-type]
        )

    assert calls == 1


def _demo_crate2nix_target() -> crate2nix.Crate2NixTarget:
    return crate2nix.Crate2NixTarget(
        name="demo",
        patched_src_installable="path:.#demo",
        cargo_nix=Path("Cargo.nix"),
        crate_hashes=Path("crate-hashes.json"),
        normalizer_path=Path("normalize.py"),
        supported_platforms=("linux",),
    )


_STALLED_GIT_FETCH = (
    "unpacking 'github:aaif-goose/goose/9949' into the Git cache...\n"
    "error (ignored): write of 9157 bytes: No buffer space available"
)


def test_capture_remembers_a_transport_stall_split_across_chunks() -> None:
    """The ENOBUFS marker stays set after later output scrolls past it."""
    capture = crate2nix._BoundedCapture()

    capture.append("error (ignored): write of 9157 bytes: No buffer ")
    assert not capture.transport_stall
    capture.append("space available\n")
    assert capture.transport_stall
    capture.append("still fetching\n")
    assert capture.transport_stall


def test_corrupt_cargo_git_db_is_removed_before_the_next_generate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A pack that does not match its index is deleted under the cargo lock.

    Update 40623890748 prepare-darwin-retry failed zed-editor-nightly because
    cargo kept refetching a corrupt windows-capture git db.
    """
    cargo_home = tmp_path / "cargo"
    db = cargo_home / "git" / "db" / "windows-capture-abc"
    pack = db / "objects" / "pack"
    pack.mkdir(parents=True)
    (pack / "pack-abc.pack").write_text("corrupt", encoding="utf-8")
    other = cargo_home / "git" / "db" / "other-repo"
    other.mkdir(parents=True)
    (other / "keep").write_text("keep", encoding="utf-8")
    checkout = cargo_home / "git" / "checkouts" / "windows-capture-abc"
    checkout.mkdir(parents=True)
    (checkout / "keep").write_text("keep", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("keep", encoding="utf-8")
    message = (
        "`cargo metadata` exited with an error: "
        f"packfile {pack}/pack-abc.pack does not match index\n"
        f"failed to fetch into: {db}\n"
        f"failed to fetch into: {outside}\n"
        "fatal: in the commit graph file but not in the object database.\n"
        "This is probably due to repo corruption."
    )
    calls = 0

    def _run(
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        del env, timeout
        calls += 1
        if calls == 1:
            assert db.exists()
            raise RuntimeError(message)
        assert not db.exists()
        return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(crate2nix, "_crate2nix_cargo_home", lambda: cargo_home)
    monkeypatch.setattr(crate2nix, "_run", _run)
    monkeypatch.setattr(crate2nix.time, "sleep", lambda _seconds: None)

    completed = crate2nix._run_crate2nix_generate(
        ["nix", "run", "nixpkgs#crate2nix"],
        env={},
        generated_outputs=(),
    )

    assert completed.stdout == "ok\n"
    assert calls == 2
    assert other.is_dir()
    assert checkout.is_dir()
    assert (outside / "keep").is_file()
    assert crate2nix._is_retryable_crate2nix_generate_failure(message)


def test_corrupt_cargo_git_db_cleanup_ignores_the_db_root_and_missing_dirs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Do not delete the git db root, and tolerate a db that is already gone."""
    cargo_home = tmp_path / "cargo"
    db_root = cargo_home / "git" / "db"
    db_root.mkdir(parents=True)
    (db_root / "marker").write_text("keep", encoding="utf-8")
    monkeypatch.setattr(crate2nix, "_crate2nix_cargo_home", lambda: cargo_home)

    crate2nix._discard_corrupt_cargo_git_dbs("nothing to do")
    crate2nix._discard_corrupt_cargo_git_dbs(f"packfile {db_root} does not match index")
    crate2nix._discard_corrupt_cargo_git_dbs("nix-prefetch-git\nfatal: early EOF")
    crate2nix._discard_corrupt_cargo_git_dbs(
        "packfile "
        f"{db_root}/missing-repo//objects/pack/pack-abc.pack "
        "does not match index"
    )

    assert (db_root / "marker").is_file()
    assert not (db_root / "missing-repo").exists()


def test_corrupt_cargo_git_db_cleanup_reports_disk_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """ENOSPC while removing a corrupt db is a terminal resource failure."""
    cargo_home = tmp_path / "cargo"
    db = cargo_home / "git" / "db" / "windows-capture-abc"
    db.mkdir(parents=True)
    monkeypatch.setattr(crate2nix, "_crate2nix_cargo_home", lambda: cargo_home)

    def _rmtree(path: Path) -> None:
        raise OSError(errno.ENOSPC, "No space left on device", str(path))

    monkeypatch.setattr(crate2nix.shutil, "rmtree", _rmtree)

    with pytest.raises(crate2nix.Crate2NixResourceError, match="ENOSPC"):
        crate2nix._discard_corrupt_cargo_git_dbs(
            f"failed to fetch into: {db}\ndoes not match index"
        )


def test_corrupt_cargo_git_db_cleanup_reraises_other_os_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A permission error while discarding a db is not reclassified."""
    cargo_home = tmp_path / "cargo"
    db = cargo_home / "git" / "db" / "windows-capture-abc"
    db.mkdir(parents=True)
    monkeypatch.setattr(crate2nix, "_crate2nix_cargo_home", lambda: cargo_home)
    monkeypatch.setattr(
        crate2nix.shutil,
        "rmtree",
        lambda _path: (_ for _ in ()).throw(OSError(errno.EACCES, "Permission denied")),
    )

    with pytest.raises(OSError, match="Permission denied"):
        crate2nix._discard_corrupt_cargo_git_dbs(
            f"failed to fetch into: {db}\nrepo corruption"
        )


def test_patched_src_retries_a_stalled_git_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The goose git-cache stall retries instead of failing the whole prepare."""
    calls = 0
    progress: list[str] = []

    class _NoDelayEvent:
        def is_set(self) -> bool:
            return False

        def wait(self, _timeout: float) -> bool:
            return False

    def _run(
        _args: list[str],
        *,
        cancel_event: threading.Event | None = None,
        progress: object | None = None,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        del cancel_event, progress
        calls += 1
        if calls == 1:
            raise RuntimeError(_STALLED_GIT_FETCH)
        return subprocess.CompletedProcess(
            _args, 0, stdout="/nix/store/goose\n", stderr=""
        )

    monkeypatch.setattr(crate2nix, "_run", _run)

    assert crate2nix._build_patched_src(
        _demo_crate2nix_target(),
        cancel_event=_NoDelayEvent(),  # type: ignore[arg-type]
        progress=progress.append,
    ) == Path("/nix/store/goose")
    assert calls == 2
    assert progress == [
        "Retrying patched source build after a stalled git fetch (1/3)..."
    ]


def test_patched_src_sleeps_between_unstalled_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller without a progress sink still backs off between fetch attempts."""
    calls = 0
    sleeps: list[float] = []

    def _run(_args: list[str]) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        if calls < crate2nix._CRATE2NIX_GENERATE_ATTEMPTS:
            raise RuntimeError(_STALLED_GIT_FETCH)
        return subprocess.CompletedProcess(
            _args, 0, stdout="/nix/store/goose\n", stderr=""
        )

    monkeypatch.setattr(crate2nix, "_run", _run)
    monkeypatch.setattr(crate2nix.time, "sleep", sleeps.append)

    assert crate2nix._build_patched_src(_demo_crate2nix_target()) == Path(
        "/nix/store/goose"
    )
    assert calls == 3
    assert sleeps == [
        crate2nix._CRATE2NIX_TRANSPORT_RETRY_DELAY_SECONDS,
        crate2nix._CRATE2NIX_TRANSPORT_RETRY_DELAY_SECONDS * 2,
    ]


def test_patched_src_stops_after_repeated_git_fetch_stalls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three stalled fetches still fail closed."""
    calls = 0
    sleeps: list[float] = []

    def _run(_args: list[str]) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        raise RuntimeError(_STALLED_GIT_FETCH)

    monkeypatch.setattr(crate2nix, "_run", _run)
    monkeypatch.setattr(crate2nix.time, "sleep", sleeps.append)

    with pytest.raises(RuntimeError, match="No buffer space available"):
        crate2nix._build_patched_src(_demo_crate2nix_target())
    assert calls == crate2nix._CRATE2NIX_GENERATE_ATTEMPTS
    assert sleeps == [
        crate2nix._CRATE2NIX_TRANSPORT_RETRY_DELAY_SECONDS,
        crate2nix._CRATE2NIX_TRANSPORT_RETRY_DELAY_SECONDS * 2,
    ]


def test_patched_src_does_not_retry_a_real_build_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crate2nix source error without a transport marker fails on the first build."""
    calls = 0

    def _run(_args: list[str]) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        raise RuntimeError("error: builder for '/nix/store/goose.drv' failed")

    monkeypatch.setattr(crate2nix, "_run", _run)
    monkeypatch.setattr(
        crate2nix.time,
        "sleep",
        lambda _seconds: pytest.fail("permanent failures must not sleep"),
    )

    with pytest.raises(RuntimeError, match="builder for"):
        crate2nix._build_patched_src(_demo_crate2nix_target())
    assert calls == 1


def test_patched_src_does_not_retry_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fetch that uses the whole timeout stays terminal."""

    def _run(_args: list[str]) -> subprocess.CompletedProcess[str]:
        raise crate2nix.Crate2NixCommandTimeoutError("command timed out after 2400s")

    monkeypatch.setattr(crate2nix, "_run", _run)

    with pytest.raises(crate2nix.Crate2NixCommandTimeoutError):
        crate2nix._build_patched_src(_demo_crate2nix_target())


def test_patched_src_retry_honors_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation during the git-fetch backoff must not start another build."""
    calls = 0

    def _run(
        _args: list[str],
        *,
        cancel_event: threading.Event | None = None,
        progress: object | None = None,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        del cancel_event, progress
        calls += 1
        raise RuntimeError(_STALLED_GIT_FETCH)

    monkeypatch.setattr(crate2nix, "_run", _run)

    with pytest.raises(crate2nix.Crate2NixCommandCancelledError, match="cancelled"):
        crate2nix._build_patched_src(
            _demo_crate2nix_target(),
            cancel_event=_CancelDuringBackoff(),  # type: ignore[arg-type]
        )
    assert calls == 1
