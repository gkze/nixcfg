"""Tests for low-level nix command process helpers."""

import asyncio
import os
import pickle
import sys
import types
from collections.abc import AsyncGenerator

import pytest

from lib.nix.commands import base
from lib.nix.commands.base import (
    CommandResult,
    HashMismatchError,
    NixCommandError,
    ProcessDone,
    ProcessLine,
    _merge_env,
    _raise_timeout,
    run_nix,
    stream_nix,
    stream_process,
)
from lib.tests._assertions import expect_not_none

PYTHON = sys.executable
_EXIT_STATUS_THREE = 3
_THREE_SECONDS = 3.0


class _NeverEndingStream:
    async def readline(self) -> bytes:
        await asyncio.Future()


class _BlockingLineStream:
    def __init__(self, first_line: bytes | None = None) -> None:
        self._first_line = first_line
        self.cancelled = False

    async def readline(self) -> bytes:
        if self._first_line is not None:
            line = self._first_line
            self._first_line = None
            return line
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def read(self, _size: int) -> bytes:
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class _FiniteStream:
    def __init__(self, lines: list[bytes] | None = None) -> None:
        self._lines = list(lines or [])

    async def readline(self) -> bytes:
        return self._lines.pop(0) if self._lines else b""

    async def read(self, _size: int) -> bytes:
        return b""


class _LiveStreamProc:
    def __init__(self) -> None:
        self.stdout = _BlockingLineStream(b"ready\n")
        self.stderr = _BlockingLineStream()
        self.killed = False
        self.returncode: int | None = None
        self.wait_count = 0

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self.wait_count += 1
        self.returncode = -9
        return self.returncode


class _CompletedStreamProc(_LiveStreamProc):
    def __init__(self) -> None:
        super().__init__()
        self.stdout = _FiniteStream([b"ready\n"])
        self.stderr = _FiniteStream()

    async def wait(self) -> int:
        self.wait_count += 1
        self.returncode = 0
        return self.returncode


class _TimeoutProc:
    def __init__(
        self,
        *,
        stdout: _NeverEndingStream | None = None,
        stderr: _NeverEndingStream | None = None,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.killed = False
        self.returncode = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        await asyncio.Future()

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return 0


async def _raise_timeout_immediately(
    awaitable: object,
    *_args: object,
    **_kwargs: object,
) -> object:
    close = getattr(awaitable, "close", None)
    if callable(close):
        close()
    raise TimeoutError


async def _timed_out_stdout_lines(*_args: object, **_kwargs: object):
    if False:
        yield ""
    raise TimeoutError


def test_raise_timeout_raises_timeout_error() -> None:
    """Run this test case."""
    with pytest.raises(TimeoutError):
        _raise_timeout()


def test_merge_env_sets_term_and_merges_input() -> None:
    """Run this test case."""
    merged = _merge_env({"X_TEST": "1"})
    assert merged["TERM"] == "dumb"
    assert merged["X_TEST"] == "1"


def test_nix_command_error_str_shows_tail_only() -> None:
    """Run this test case."""
    stderr = "\n".join(f"line-{i}" for i in range(30))
    result = CommandResult(
        args=["nix", "build"], returncode=1, stdout="", stderr=stderr
    )
    text = str(NixCommandError(result, "failed"))

    assert "NixCommandError: failed" in text
    assert "stderr (last 20 lines):" in text
    assert "line-0" not in text
    assert "line-29" in text


def test_nix_command_error_str_shows_full_stderr_when_short() -> None:
    """Run this test case."""
    result = CommandResult(
        args=["nix", "build"], returncode=1, stdout="", stderr="oops"
    )
    text = str(NixCommandError(result, "failed"))

    assert "stderr:" in text
    assert "last 20 lines" not in text


@pytest.mark.parametrize("hash_mismatch", [False, True])
@pytest.mark.parametrize("with_details", [False, True])
def test_command_errors_preserve_diagnostics_through_pickle(
    *, hash_mismatch: bool, with_details: bool
) -> None:
    """Durable steps preserve typed errors, including within exception groups."""
    result = CommandResult(["nix", "build"], 1, "build output", "transfer failed")
    error = (
        HashMismatchError(
            result,
            got_hash="sha256:abc",
            specified="sha256:def" if with_details else None,
            drv_path="/nix/store/fixture.drv" if with_details else None,
        )
        if hash_mismatch
        else NixCommandError(result, "prefetch failed" if with_details else None)
    )
    error.add_note("while hashing the candidate")
    group = ExceptionGroup("platform hashes", [error])
    restored_group = pickle.loads(pickle.dumps(group))  # noqa: S301 -- local fixture
    (restored,) = restored_group.exceptions
    assert type(restored) is type(error)
    assert restored.result == result
    assert restored.args == error.args
    assert restored.message == error.message
    assert restored.__notes__ == error.__notes__
    if isinstance(error, HashMismatchError):
        assert (restored.hash, restored.specified, restored.drv_path) == (
            error.hash,
            error.specified,
            error.drv_path,
        )
    assert str(restored) == str(error)


def test_hash_mismatch_parsing_and_properties() -> None:
    """Run this test case."""
    output = (
        "error: hash mismatch in fixed-output derivation '/nix/store/abc123.drv'\n"
        "  specified: sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\n"
        "  got: sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=\n"
    )
    result = CommandResult(args=["nix"], returncode=1, stdout="", stderr=output)

    parsed = HashMismatchError.from_output(output, result)
    parsed = expect_not_none(parsed)
    assert parsed.hash.startswith("sha256-")
    specified = expect_not_none(parsed.specified)
    assert specified.startswith("sha256-")
    assert parsed.drv_path == "/nix/store/abc123.drv"
    assert parsed.is_sri


def test_hash_mismatch_ignores_prefetch_yarn_deps_tarball_digests() -> None:
    """A yarn tarball digest is not a fixed-output hash and must not be parsed."""
    output = (
        "Error: hash mismatch, expected "
        f"{'a' * 40}, got da39a3ee5e6b4b0d3255bfef95601890afd80709 for "
        "https://registry.yarnpkg.com/@esbuild/android-x64/-/android-x64-0.25.1.tgz\n"
    )
    result = CommandResult(args=["nix"], returncode=1, stdout="", stderr=output)
    assert HashMismatchError.from_output(output, result) is None


def test_hash_mismatch_fallback_parsing_and_none() -> None:
    """Run this test case."""
    output = (
        "error: ca hash mismatch importing path '/nix/store/zzz.drv'\n"
        "specified: sha256:abcd\n"
        "got: sha256:ef01\n"
    )
    result = CommandResult(args=["nix"], returncode=1, stdout="", stderr=output)
    parsed = HashMismatchError.from_stderr(output, result)

    parsed = expect_not_none(parsed)
    assert parsed.hash == "sha256:ef01"
    assert parsed.specified == "sha256:abcd"
    assert not parsed.is_sri

    assert HashMismatchError.from_output("plain error", result) is None


@pytest.mark.parametrize(
    "other_hash",
    ["sha512-ZYX=", "sha256:abcd", "f" * 64, "z" * 52],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_hash_mismatch_associates_the_last_hash_with_its_own_path(
    other_hash: str, *, reverse: bool
) -> None:
    """Mixed-format diagnostics retain identity regardless of output order."""
    blocks = [
        ("/nix/store/first.drv", "sha256-ABC=", "sha256-DEF="),
        ("/nix/store/second.drv", other_hash, other_hash),
    ]
    if reverse:
        blocks.reverse()
    output = "\n".join(
        f"error: hash mismatch in fixed-output derivation '{path}':\n"
        f"  specified: {specified}\n  got: {got}\n"
        for path, specified, got in blocks
    )
    result = CommandResult(args=["nix"], returncode=1, stdout="", stderr=output)
    parsed = expect_not_none(HashMismatchError.from_output(output, result))
    assert (parsed.drv_path, parsed.specified, parsed.hash) == blocks[-1]


def test_hash_mismatch_does_not_inherit_missing_fields_from_a_previous_failure() -> (
    None
):
    """Unidentified hash output remains unidentified instead of reusing stale proof."""
    output = (
        "error: hash mismatch in fixed-output derivation '/nix/store/first.drv':\n"
        " specified: sha256-ABC=\n got: sha256-DEF=\n"
        "hash mismatch\n got: sha256-XYZ=\n"
    )
    result = CommandResult(args=["nix"], returncode=1, stdout="", stderr=output)
    parsed = expect_not_none(HashMismatchError.from_output(output, result))
    assert parsed.drv_path is None
    assert parsed.specified is None
    assert parsed.hash == "sha256-XYZ="


def test_hash_mismatch_to_sri_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    result = CommandResult(args=["nix"], returncode=1, stdout="", stderr="")
    sri = HashMismatchError(
        result, got_hash="sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    )

    assert asyncio.run(sri.to_sri()) == sri.hash

    non_sri = HashMismatchError(result, got_hash="abcd")

    async def _convert(hash_value: str, *, hash_algo: str = "sha256") -> str:
        return f"converted:{hash_algo}:{hash_value}"

    monkeypatch.setattr(
        "lib.nix.commands.base.importlib.import_module",
        lambda _name: types.SimpleNamespace(nix_hash_convert=_convert),
    )
    assert asyncio.run(non_sri.to_sri(hash_algo="sha512")) == "converted:sha512:abcd"


def test_stream_process_success_events() -> None:
    """Run this test case."""

    async def _run() -> list[object]:
        script = (
            "import sys; print('out-1'); sys.stderr.write('err-1\\n'); print('out-2')"
        )
        return [
            event
            async for event in stream_process(
                [PYTHON, "-c", script], command_timeout=5.0
            )
        ]

    events = asyncio.run(_run())
    lines = [e for e in events if isinstance(e, ProcessLine)]
    done = [e for e in events if isinstance(e, ProcessDone)]

    assert len(done) == 1
    assert done[0].result.returncode == 0
    assert any(line.stream == "stdout" and "out-1" in line.text for line in lines)
    assert any(line.stream == "stderr" and "err-1" in line.text for line in lines)


def test_stream_process_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Timeouts should kill the subprocess and surface ``TimeoutError``."""
    proc = _TimeoutProc(stdout=_NeverEndingStream(), stderr=_NeverEndingStream())

    async def _create_subprocess_exec(
        *_args: object, **_kwargs: object
    ) -> _TimeoutProc:
        return proc

    monkeypatch.setattr(
        "lib.nix.commands.base.asyncio.create_subprocess_exec",
        _create_subprocess_exec,
    )
    monkeypatch.setattr(
        "lib.nix.commands.base.asyncio.wait_for",
        _raise_timeout_immediately,
    )

    async def _run() -> None:
        async for _event in stream_process(["nix"], command_timeout=5.0):
            pass

    with pytest.raises(TimeoutError):
        asyncio.run(_run())

    assert proc.killed


def test_stream_process_timeout_after_output_closes() -> None:
    """Closing output pipes must not let a living child escape its deadline."""
    lines: list[ProcessLine] = []
    script = (
        "import os,time; print(os.getpid(), flush=True); "
        "os.close(1); os.close(2); time.sleep(30)"
    )

    async def consume() -> None:
        async for event in stream_process([PYTHON, "-c", script], command_timeout=1.0):
            assert isinstance(event, ProcessLine)
            lines.append(event)

    async def run() -> None:
        # This outer bound makes a deadline regression fail without a long wait.
        async with asyncio.timeout(5.0):
            with pytest.raises(TimeoutError):
                await consume()

    asyncio.run(run())
    assert len(lines) == 1
    with pytest.raises(ProcessLookupError):
        os.kill(int(lines[0].text), 0)


def test_stream_process_handles_missing_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""

    class _Proc:
        stdout = None
        stderr = None

        async def wait(self) -> int:
            return 0

    async def _create_subprocess_exec(*_args: object, **_kwargs: object) -> _Proc:
        return _Proc()

    monkeypatch.setattr(
        "lib.nix.commands.base.asyncio.create_subprocess_exec",
        _create_subprocess_exec,
    )

    async def _collect() -> list[object]:
        return [event async for event in stream_process(["nix"], command_timeout=1.0)]

    events = asyncio.run(_collect())
    assert len(events) == 1
    assert isinstance(events[0], ProcessDone)


def test_stream_process_zero_timeout_uses_deadline_check() -> None:
    """Run this test case."""

    async def _run() -> None:
        async for _event in stream_process(
            [PYTHON, "-c", "import time; time.sleep(2)"],
            command_timeout=0.0,
        ):
            pass

    with pytest.raises(TimeoutError):
        asyncio.run(_run())


def test_run_nix_success_and_capture_modes() -> None:
    """Run this test case."""
    result = asyncio.run(run_nix([PYTHON, "-c", "print('ok')"], command_timeout=5.0))
    assert result.returncode == 0
    assert result.stdout.strip() == "ok"

    no_capture = asyncio.run(
        run_nix(
            [PYTHON, "-c", "print('ok')"],
            capture=False,
            command_timeout=5.0,
        )
    )
    assert no_capture.returncode == 0
    assert no_capture.stdout == ""


def test_run_nix_nonzero_paths() -> None:
    """Run this test case."""
    script = "import sys; sys.stderr.write('boom\\n'); sys.exit(3)"
    result = asyncio.run(
        run_nix([PYTHON, "-c", script], check=False, command_timeout=5.0)
    )
    assert result.returncode == _EXIT_STATUS_THREE

    with pytest.raises(NixCommandError):
        asyncio.run(run_nix([PYTHON, "-c", script], command_timeout=5.0))


def test_run_nix_hash_mismatch_error() -> None:
    """Run this test case."""
    script = (
        "import sys; "
        "sys.stderr.write(\"hash mismatch in fixed-output derivation '/nix/store/demo.drv'\\n\"); "
        'sys.stderr.write("specified: sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\\n"); '
        'sys.stderr.write("got: sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=\\n"); '
        "sys.exit(1)"
    )
    with pytest.raises(HashMismatchError):
        asyncio.run(run_nix([PYTHON, "-c", script], command_timeout=5.0))


def test_run_nix_timeout_raises_command_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Timeouts should kill the subprocess and raise ``NixCommandError``."""
    proc = _TimeoutProc()

    async def _create_subprocess_exec(
        *_args: object, **_kwargs: object
    ) -> _TimeoutProc:
        return proc

    monkeypatch.setattr(
        "lib.nix.commands.base.asyncio.create_subprocess_exec",
        _create_subprocess_exec,
    )
    monkeypatch.setattr(
        "lib.nix.commands.base.asyncio.wait_for",
        _raise_timeout_immediately,
    )

    with pytest.raises(NixCommandError, match="timed out"):
        asyncio.run(run_nix(["nix"], command_timeout=5.0))

    assert proc.killed


def test_stream_nix_success_and_error_paths() -> None:
    """Run this test case."""

    async def _collect(cmd: list[str], timeout_s: float = 5.0) -> list[str]:
        return [line async for line in stream_nix(cmd, command_timeout=timeout_s)]

    lines = asyncio.run(_collect([PYTHON, "-c", "print('a'); print('b')"]))
    assert lines == ["a", "b"]

    with pytest.raises(NixCommandError):
        asyncio.run(
            _collect([PYTHON, "-c", "import sys; sys.stderr.write('x'); sys.exit(1)"])
        )

    hash_script = (
        "import sys; "
        "sys.stderr.write(\"hash mismatch importing path '/nix/store/demo.drv'\\n\"); "
        'sys.stderr.write("got: sha256:abcd\\n"); '
        "sys.exit(1)"
    )
    with pytest.raises(HashMismatchError):
        asyncio.run(_collect([PYTHON, "-c", hash_script]))


def test_stream_nix_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Timeouts should kill the subprocess and raise ``NixCommandError``."""
    proc = _TimeoutProc()

    async def _create_stream_process(*_args: object, **_kwargs: object):
        stream = _NeverEndingStream()
        return proc, stream, stream

    async def _drain_stderr_stream(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        "lib.nix.commands.base._create_stream_process",
        _create_stream_process,
    )
    monkeypatch.setattr(
        "lib.nix.commands.base._drain_stderr_stream",
        _drain_stderr_stream,
    )
    monkeypatch.setattr(
        "lib.nix.commands.base._iter_timed_stdout_lines",
        _timed_out_stdout_lines,
    )

    async def _run() -> list[str]:
        return [line async for line in stream_nix(["nix"], command_timeout=5.0)]

    with pytest.raises(NixCommandError, match="timed out"):
        asyncio.run(_run())

    assert proc.killed


def _install_live_stream_nix(
    monkeypatch: pytest.MonkeyPatch, proc: _LiveStreamProc
) -> None:
    async def _create_stream_process(*_args: object, **_kwargs: object):
        return proc, proc.stdout, proc.stderr

    monkeypatch.setattr(base, "_create_stream_process", _create_stream_process)


def _install_live_stream_process(
    monkeypatch: pytest.MonkeyPatch, proc: _LiveStreamProc
) -> None:
    async def _create_subprocess_exec(
        *_args: object, **_kwargs: object
    ) -> _LiveStreamProc:
        return proc

    monkeypatch.setattr(base.asyncio, "create_subprocess_exec", _create_subprocess_exec)


def _install_failing_stream_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    stop = base._stop_stream_process

    async def _fail_cleanup(process: object, tasks: list[asyncio.Task[None]]) -> None:
        await stop(process, tasks)
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr(base, "_stop_stream_process", _fail_cleanup)


def _install_blocking_stream_cleanup(
    monkeypatch: pytest.MonkeyPatch, *, cleanup_failure: bool
) -> tuple[asyncio.Event, asyncio.Event]:
    cleanup_started = asyncio.Event()
    finish_cleanup = asyncio.Event()
    stop = base._stop_stream_process

    async def _block_cleanup(process: object, tasks: list[asyncio.Task[None]]) -> None:
        cleanup_started.set()
        await finish_cleanup.wait()
        await stop(process, tasks)
        if cleanup_failure:
            raise RuntimeError("cleanup failed")

    monkeypatch.setattr(base, "_stop_stream_process", _block_cleanup)
    return cleanup_started, finish_cleanup


async def _assert_cancellation_during_close(
    stream: AsyncGenerator[object],
    *,
    cleanup_started: asyncio.Event,
    finish_cleanup: asyncio.Event,
    cleanup_failure: bool,
) -> None:
    close_task = asyncio.create_task(stream.aclose())
    await cleanup_started.wait()
    close_task.cancel()
    await asyncio.sleep(0)
    assert not close_task.done()

    finish_cleanup.set()
    with pytest.raises(asyncio.CancelledError) as caught:
        await close_task
    if cleanup_failure:
        assert any("cleanup failed" in note for note in caught.value.__notes__)


def test_stream_nix_reaps_child_on_early_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closing after a yielded line must synchronously stop and reap the child."""
    proc = _LiveStreamProc()
    _install_live_stream_nix(monkeypatch, proc)

    async def _run() -> None:
        stream = stream_nix(["nix", "build"], command_timeout=5.0)
        assert await anext(stream) == "ready"
        await stream.aclose()

    asyncio.run(_run())
    assert proc.killed
    assert proc.wait_count == 1
    assert proc.stderr.cancelled


@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_stream_nix_close_defers_cancellation_until_cleanup_finishes(
    monkeypatch: pytest.MonkeyPatch, *, cleanup_failure: bool
) -> None:
    """Closing cannot swallow cancellation or interrupt subprocess ownership."""
    proc = _LiveStreamProc()
    _install_live_stream_nix(monkeypatch, proc)
    cleanup_started, finish_cleanup = _install_blocking_stream_cleanup(
        monkeypatch, cleanup_failure=cleanup_failure
    )

    async def _run() -> None:
        stream = stream_nix(["nix"], command_timeout=5.0)
        assert await anext(stream) == "ready"
        await _assert_cancellation_during_close(
            stream,
            cleanup_started=cleanup_started,
            finish_cleanup=finish_cleanup,
            cleanup_failure=cleanup_failure,
        )

    asyncio.run(_run())
    assert proc.killed
    assert proc.wait_count == 1
    assert proc.stderr.cancelled


def test_stream_nix_reaps_child_on_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation while awaiting stdout must not strand the child or stderr task."""
    proc = _LiveStreamProc()
    _install_live_stream_nix(monkeypatch, proc)

    async def _run() -> None:
        first_line_seen = asyncio.Event()

        async def _consume() -> None:
            async for line in stream_nix(["nix", "build"], command_timeout=5.0):
                if line == "ready":
                    first_line_seen.set()

        task = asyncio.create_task(_consume())
        await first_line_seen.wait()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_run())
    assert proc.killed
    assert proc.wait_count == 1
    assert proc.stderr.cancelled


def test_stream_nix_normal_completion_does_not_stop_reaped_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fully drained process is not killed a second time by generator teardown."""
    proc = _CompletedStreamProc()
    _install_live_stream_nix(monkeypatch, proc)

    async def _run() -> list[str]:
        return [line async for line in stream_nix(["nix"], command_timeout=5.0)]

    assert asyncio.run(_run()) == ["ready"]
    assert not proc.killed
    assert proc.wait_count == 1


def test_stream_nix_early_close_surfaces_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An otherwise clean close must not hide a failed cleanup operation."""
    proc = _LiveStreamProc()
    _install_live_stream_nix(monkeypatch, proc)
    _install_failing_stream_cleanup(monkeypatch)

    async def _run() -> None:
        stream = stream_nix(["nix"], command_timeout=5.0)
        assert await anext(stream) == "ready"
        await stream.aclose()

    with pytest.raises(RuntimeError, match="cleanup failed"):
        asyncio.run(_run())
    assert proc.killed
    assert proc.stderr.cancelled


def test_stream_nix_preserves_primary_error_when_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timeout remains the causal error when its teardown also fails."""
    proc = _LiveStreamProc()
    _install_live_stream_nix(monkeypatch, proc)
    monkeypatch.setattr(base, "_iter_timed_stdout_lines", _timed_out_stdout_lines)
    _install_failing_stream_cleanup(monkeypatch)

    async def _run() -> list[str]:
        return [line async for line in stream_nix(["nix"], command_timeout=5.0)]

    with pytest.raises(NixCommandError, match="timed out") as caught:
        asyncio.run(_run())
    assert any("cleanup failed" in note for note in caught.value.__notes__)
    assert proc.killed
    assert proc.stderr.cancelled


def test_stream_nix_preserves_consumer_error_when_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception thrown into the generator remains primary during teardown."""
    proc = _LiveStreamProc()
    _install_live_stream_nix(monkeypatch, proc)
    _install_failing_stream_cleanup(monkeypatch)
    primary_error = RuntimeError("consumer failed")

    async def _run() -> None:
        stream = stream_nix(["nix"], command_timeout=5.0)
        assert await anext(stream) == "ready"
        await stream.athrow(primary_error)

    with pytest.raises(RuntimeError, match="consumer failed") as caught:
        asyncio.run(_run())
    assert caught.value is primary_error
    assert any("cleanup failed" in note for note in caught.value.__notes__)
    assert proc.killed
    assert proc.stderr.cancelled


def test_stream_process_preserves_timeout_when_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stream timeout cannot be replaced by a subprocess teardown failure."""
    proc = _TimeoutProc(stdout=_NeverEndingStream(), stderr=_NeverEndingStream())

    async def _create_subprocess_exec(
        *_args: object, **_kwargs: object
    ) -> _TimeoutProc:
        return proc

    monkeypatch.setattr(base.asyncio, "create_subprocess_exec", _create_subprocess_exec)
    monkeypatch.setattr(base.asyncio, "wait_for", _raise_timeout_immediately)
    _install_failing_stream_cleanup(monkeypatch)

    async def _run() -> None:
        async for _event in stream_process(["nix"], command_timeout=5.0):
            pass

    with pytest.raises(TimeoutError) as caught:
        asyncio.run(_run())
    assert any("cleanup failed" in note for note in caught.value.__notes__)
    assert proc.killed


def test_stream_process_preserves_consumer_error_when_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Consumer failures survive cleanup with the teardown error as diagnostic context."""
    proc = _LiveStreamProc()
    _install_live_stream_process(monkeypatch, proc)
    _install_failing_stream_cleanup(monkeypatch)
    primary_error = RuntimeError("consumer failed")

    async def _run() -> None:
        stream = stream_process(["nix"], command_timeout=5.0)
        assert await anext(stream) == ProcessLine("stdout", "ready\n")
        await stream.athrow(primary_error)

    with pytest.raises(RuntimeError, match="consumer failed") as caught:
        asyncio.run(_run())
    assert caught.value is primary_error
    assert any("cleanup failed" in note for note in caught.value.__notes__)
    assert proc.killed
    assert proc.stdout.cancelled
    assert proc.stderr.cancelled


def test_stream_nix_zero_timeout_uses_deadline_check() -> None:
    """Run this test case."""

    async def _run() -> list[str]:
        return [
            line
            async for line in stream_nix(
                [PYTHON, "-c", "import time; time.sleep(2)"],
                command_timeout=0.0,
            )
        ]

    with pytest.raises(NixCommandError, match="timed out"):
        asyncio.run(_run())


def test_stream_process_reaps_child_when_generator_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closing a streamed command must stop its process and reader tasks."""
    proc = _LiveStreamProc()

    async def _create_subprocess_exec(
        *_args: object, **_kwargs: object
    ) -> _LiveStreamProc:
        return proc

    monkeypatch.setattr(
        "lib.nix.commands.base.asyncio.create_subprocess_exec",
        _create_subprocess_exec,
    )

    async def _run() -> None:
        stream = stream_process(["nix", "build"], command_timeout=5.0)
        assert await anext(stream) == ProcessLine("stdout", "ready\n")
        await stream.aclose()
        await stream.aclose()

    asyncio.run(_run())

    assert proc.killed
    assert proc.wait_count == 1
    assert proc.stdout.cancelled
    assert proc.stderr.cancelled


@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_stream_process_close_defers_cancellation_until_cleanup_finishes(
    monkeypatch: pytest.MonkeyPatch, *, cleanup_failure: bool
) -> None:
    """Event streaming and Nix streaming share close-cancellation semantics."""
    proc = _LiveStreamProc()
    _install_live_stream_process(monkeypatch, proc)
    cleanup_started, finish_cleanup = _install_blocking_stream_cleanup(
        monkeypatch, cleanup_failure=cleanup_failure
    )

    async def _run() -> None:
        stream = stream_process(["nix"], command_timeout=5.0)
        assert await anext(stream) == ProcessLine("stdout", "ready\n")
        await _assert_cancellation_during_close(
            stream,
            cleanup_started=cleanup_started,
            finish_cleanup=finish_cleanup,
            cleanup_failure=cleanup_failure,
        )

    asyncio.run(_run())
    assert proc.killed
    assert proc.wait_count == 1
    assert proc.stdout.cancelled
    assert proc.stderr.cancelled


def test_stream_process_reaps_child_when_consumer_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task cancellation must not leave a streamed subprocess running."""
    proc = _LiveStreamProc()

    async def _create_subprocess_exec(
        *_args: object, **_kwargs: object
    ) -> _LiveStreamProc:
        return proc

    monkeypatch.setattr(
        "lib.nix.commands.base.asyncio.create_subprocess_exec",
        _create_subprocess_exec,
    )

    async def _run() -> None:
        first_line_seen = asyncio.Event()

        async def _consume() -> None:
            async for event in stream_process(["nix", "build"], command_timeout=5.0):
                if event == ProcessLine("stdout", "ready\n"):
                    first_line_seen.set()

        task = asyncio.create_task(_consume())
        await first_line_seen.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_run())

    assert proc.killed
    assert proc.wait_count == 1
    assert proc.stdout.cancelled
    assert proc.stderr.cancelled


def test_stream_nix_raises_when_subprocess_streams_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""

    class _Proc:
        stdout = None
        stderr = None

    async def _create_subprocess_exec(*_args: object, **_kwargs: object) -> _Proc:
        return _Proc()

    monkeypatch.setattr(
        "lib.nix.commands.base.asyncio.create_subprocess_exec",
        _create_subprocess_exec,
    )

    async def _run() -> list[str]:
        return [line async for line in stream_nix(["nix"], command_timeout=5.0)]

    with pytest.raises(RuntimeError, match="Subprocess was not created"):
        asyncio.run(_run())


def test_run_nix_reaps_cancelled_child(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cancelled concurrent prefetches must finish child cleanup before returning."""
    started = asyncio.Event()

    class Process(_LiveStreamProc):
        async def communicate(self) -> tuple[bytes, bytes]:
            started.set()
            await asyncio.Future()

    proc = Process()

    async def spawn(*_args: object, **_kwargs: object) -> Process:
        return proc

    monkeypatch.setattr("lib.nix.commands.base.asyncio.create_subprocess_exec", spawn)

    async def run() -> None:
        task = asyncio.create_task(run_nix(["nix", "store", "prefetch-file"]))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert proc.killed
        assert proc.wait_count == 1

    asyncio.run(run())


@pytest.mark.parametrize("limit", [None, 5])
def test_stream_capture_preserves_events_and_bounds_requested_tail(
    limit: int | None,
) -> None:
    """Parser output remains complete by default while diagnostic capture can be bounded."""

    async def run() -> list[ProcessLine | ProcessDone]:
        return [
            event
            async for event in stream_process(
                [
                    PYTHON,
                    "-c",
                    "import sys; print('abcdefgh'); print('xyz'); print('stderr-value', file=sys.stderr)",
                ],
                output_limit=limit,
            )
        ]

    events = asyncio.run(run())
    stdout = "".join(
        event.text
        for event in events
        if isinstance(event, ProcessLine) and event.stream == "stdout"
    )
    stderr = "".join(
        event.text
        for event in events
        if isinstance(event, ProcessLine) and event.stream == "stderr"
    )
    assert stdout == "abcdefgh\nxyz\n"
    assert stderr == "stderr-value\n"
    result = events[-1]
    assert isinstance(result, ProcessDone)
    assert result.result.stdout == (stdout if limit is None else stdout[-limit:])
    assert result.result.stderr == (stderr if limit is None else stderr[-limit:])


def test_stream_capture_rejects_nonpositive_limit() -> None:
    """Reject an invalid output policy before starting a child."""

    async def run() -> None:
        with pytest.raises(ValueError, match="output_limit must be positive"):
            await anext(stream_process(["unused"], output_limit=0))

    asyncio.run(run())


def test_stream_reader_failure_reaps_child_promptly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed pipe reader must reach the consumer instead of waiting for the timeout."""

    class BrokenStream:
        async def readline(self) -> bytes:
            msg = "line exceeds stream limit"
            raise ValueError(msg)

    class Process(_LiveStreamProc):
        def __init__(self) -> None:
            super().__init__()
            self.stdout = BrokenStream()

    proc = Process()

    async def spawn(*_args: object, **_kwargs: object) -> Process:
        return proc

    monkeypatch.setattr("lib.nix.commands.base.asyncio.create_subprocess_exec", spawn)

    async def run() -> None:
        with pytest.raises(ValueError, match="line exceeds stream limit"):
            async for _event in stream_process(["unused"]):
                pass

    asyncio.run(run())
    assert proc.killed
    assert proc.wait_count == 1


def test_stream_queue_backpressure_stops_reader_fanout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A paused consumer cannot accumulate all available line events in memory."""

    class ManyLines:
        reads = 0

        async def readline(self) -> bytes:
            self.reads += 1
            return b"line\n" if self.reads <= 100 else b""

    class Process(_LiveStreamProc):
        def __init__(self) -> None:
            super().__init__()
            self.stdout = ManyLines()
            self.stderr = None

    proc = Process()

    async def spawn(*_args: object, **_kwargs: object) -> Process:
        return proc

    monkeypatch.setattr("lib.nix.commands.base.asyncio.create_subprocess_exec", spawn)
    monkeypatch.setattr("lib.nix.commands.base._STREAM_QUEUE_SIZE", 2)

    async def run() -> None:
        stream = stream_process(["unused"], output_limit=10)
        assert await anext(stream) == ProcessLine("stdout", "line\n")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert proc.stdout.reads <= 4
        await stream.aclose()

    asyncio.run(run())
    assert proc.killed
