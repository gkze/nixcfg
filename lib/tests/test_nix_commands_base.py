"""Tests for low-level nix command process helpers."""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from lib.nix.commands.base import (
    CommandResult,
    HashMismatchError,
    NixCommandError,
    ProcessDone,
    ProcessLine,
    _merge_env,
    _raise_timeout,
    _resolve_timeout_alias,
    run_nix,
    stream_nix,
    stream_process,
)
from lib.tests._assertions import check, expect_not_none

PYTHON = sys.executable
_EXIT_STATUS_THREE = 3
_THREE_SECONDS = 3.0


def test_raise_timeout_raises_timeout_error() -> None:
    """Run this test case."""
    with pytest.raises(TimeoutError):
        _raise_timeout()


def test_merge_env_sets_term_and_merges_input() -> None:
    """Run this test case."""
    merged = _merge_env({"X_TEST": "1"})
    check(merged["TERM"] == "dumb")
    check(merged["X_TEST"] == "1")


def test_nix_command_error_str_shows_tail_only() -> None:
    """Run this test case."""
    stderr = "\n".join(f"line-{i}" for i in range(30))
    result = CommandResult(
        args=["nix", "build"], returncode=1, stdout="", stderr=stderr
    )
    text = str(NixCommandError(result, "failed"))

    check("NixCommandError: failed" in text)
    check("stderr (last 20 lines):" in text)
    check("line-0" not in text)
    check("line-29" in text)


def test_nix_command_error_str_shows_full_stderr_when_short() -> None:
    """Run this test case."""
    result = CommandResult(
        args=["nix", "build"], returncode=1, stdout="", stderr="oops"
    )
    text = str(NixCommandError(result, "failed"))

    check("stderr:" in text)
    check("last 20 lines" not in text)


def test_resolve_timeout_alias_validates_kwargs() -> None:
    """Run this test case."""
    check(
        _resolve_timeout_alias(command_timeout=_THREE_SECONDS, kwargs={})
        == _THREE_SECONDS
    )

    with pytest.raises(TypeError, match=r"Unexpected keyword argument\(s\): extra"):
        _resolve_timeout_alias(command_timeout=_THREE_SECONDS, kwargs={"extra": True})

    with pytest.raises(TypeError, match=r"Unexpected keyword argument\(s\): extra"):
        _resolve_timeout_alias(
            command_timeout=_THREE_SECONDS,
            kwargs={"extra": True, "timeout": 1.0},
        )

    with pytest.raises(TypeError, match="timeout must be a number"):
        _resolve_timeout_alias(
            command_timeout=_THREE_SECONDS, kwargs={"timeout": "slow"}
        )


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
    check(parsed.hash.startswith("sha256-"))
    specified = expect_not_none(parsed.specified)
    check(specified.startswith("sha256-"))
    check(parsed.drv_path == "/nix/store/abc123.drv")
    check(parsed.is_sri)


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
    check(parsed.hash == "sha256:ef01")
    check(parsed.specified == "sha256:abcd")
    check(not parsed.is_sri)

    check(HashMismatchError.from_output("plain error", result) is None)


def test_hash_mismatch_to_sri_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    result = CommandResult(args=["nix"], returncode=1, stdout="", stderr="")
    sri = HashMismatchError(
        result, got_hash="sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    )

    check(asyncio.run(sri.to_sri()) == sri.hash)

    non_sri = HashMismatchError(result, got_hash="abcd")

    async def _convert(hash_value: str, *, hash_algo: str = "sha256") -> str:
        return f"converted:{hash_algo}:{hash_value}"

    monkeypatch.setattr(
        "lib.nix.commands.base.importlib.import_module",
        lambda _name: types.SimpleNamespace(nix_hash_convert=_convert),
    )
    check(asyncio.run(non_sri.to_sri(hash_algo="sha512")) == "converted:sha512:abcd")


def test_stream_process_success_events() -> None:
    """Run this test case."""

    async def _run() -> list[object]:
        script = (
            "import sys; print('out-1'); sys.stderr.write('err-1\\n'); print('out-2')"
        )
        return [
            event async for event in stream_process([PYTHON, "-c", script], timeout=5.0)
        ]

    events = asyncio.run(_run())
    lines = [e for e in events if isinstance(e, ProcessLine)]
    done = [e for e in events if isinstance(e, ProcessDone)]

    check(len(done) == 1)
    check(done[0].result.returncode == 0)
    check(any(line.stream == "stdout" and "out-1" in line.text for line in lines))
    check(any(line.stream == "stderr" and "err-1" in line.text for line in lines))


def test_stream_process_timeout() -> None:
    """Run this test case."""

    async def _run() -> None:
        async for _event in stream_process(
            [PYTHON, "-c", "import time; time.sleep(2)"],
            timeout=0.1,
        ):
            pass

    with pytest.raises(TimeoutError):
        asyncio.run(_run())


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
        return [event async for event in stream_process(["nix"], timeout=1.0)]

    events = asyncio.run(_collect())
    check(len(events) == 1)
    check(isinstance(events[0], ProcessDone))


def test_stream_process_zero_timeout_uses_deadline_check() -> None:
    """Run this test case."""

    async def _run() -> None:
        async for _event in stream_process(
            [PYTHON, "-c", "import time; time.sleep(2)"],
            timeout=0.0,
        ):
            pass

    with pytest.raises(TimeoutError):
        asyncio.run(_run())


def test_run_nix_success_and_capture_modes() -> None:
    """Run this test case."""
    result = asyncio.run(run_nix([PYTHON, "-c", "print('ok')"], timeout=5.0))
    check(result.returncode == 0)
    check(result.stdout.strip() == "ok")

    no_capture = asyncio.run(
        run_nix(
            [PYTHON, "-c", "print('ok')"],
            capture=False,
            timeout=5.0,
        )
    )
    check(no_capture.returncode == 0)
    check(no_capture.stdout == "")


def test_run_nix_nonzero_paths() -> None:
    """Run this test case."""
    script = "import sys; sys.stderr.write('boom\\n'); sys.exit(3)"
    result = asyncio.run(run_nix([PYTHON, "-c", script], check=False, timeout=5.0))
    check(result.returncode == _EXIT_STATUS_THREE)

    with pytest.raises(NixCommandError):
        asyncio.run(run_nix([PYTHON, "-c", script], timeout=5.0))


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
        asyncio.run(run_nix([PYTHON, "-c", script], timeout=5.0))


def test_run_nix_timeout_raises_command_error() -> None:
    """Run this test case."""
    with pytest.raises(NixCommandError, match="timed out"):
        asyncio.run(
            run_nix(
                [PYTHON, "-c", "import time; time.sleep(2)"],
                timeout=0.1,
            )
        )


def test_stream_nix_success_and_error_paths() -> None:
    """Run this test case."""

    async def _collect(cmd: list[str], timeout_s: float = 5.0) -> list[str]:
        return [line async for line in stream_nix(cmd, timeout=timeout_s)]

    lines = asyncio.run(_collect([PYTHON, "-c", "print('a'); print('b')"]))
    check(lines == ["a", "b"])

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


def test_stream_nix_timeout() -> None:
    """Run this test case."""

    async def _run() -> list[str]:
        return [
            line
            async for line in stream_nix(
                [PYTHON, "-c", "import time; time.sleep(2)"],
                timeout=0.1,
            )
        ]

    with pytest.raises(NixCommandError, match="timed out"):
        asyncio.run(_run())


def test_stream_nix_zero_timeout_uses_deadline_check() -> None:
    """Run this test case."""

    async def _run() -> list[str]:
        return [
            line
            async for line in stream_nix(
                [PYTHON, "-c", "import time; time.sleep(2)"],
                timeout=0.0,
            )
        ]

    with pytest.raises(NixCommandError, match="timed out"):
        asyncio.run(_run())


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
        return [line async for line in stream_nix(["nix"], timeout=5.0)]

    with pytest.raises(RuntimeError, match="Subprocess was not created"):
        asyncio.run(_run())
