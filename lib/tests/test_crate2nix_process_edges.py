"""Focused process, cancellation, and retry tests for crate2nix regeneration."""

import errno
import signal
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib.update import crate2nix


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
