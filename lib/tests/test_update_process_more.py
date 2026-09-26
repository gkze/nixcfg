"""Additional tests for subprocess/process helpers in update flows."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from lib.nix.commands.base import CommandResult as LibCommandResult
from lib.nix.commands.base import NixCommandError, ProcessDone, ProcessLine
from lib.tests._updater_helpers import collect_events
from lib.update.config import resolve_config
from lib.update.events import (
    CommandResult,
    EventSink,
    StatusInfo,
    StatusKind,
    StatusPayload,
    UpdateEvent,
    UpdateEventKind,
    ignore_event,
)
from lib.update.process import (
    NixBuildOptions,
    RunCommandOptions,
    _emit_successful_command,
    _nix_prefetch_name,
    _sanitize_log_line,
    _truncate_command,
    compute_sri_hash,
    compute_url_hashes,
    convert_nix_hash_to_sri,
    run_command,
    run_nix_build,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _collect_stream(operation):
    return asyncio.run(collect_events(operation))


def test_sanitize_and_truncate_helpers() -> None:
    """Strip ANSI/control chars and cap long command strings."""
    assert _sanitize_log_line("\x1b[31mhello\x1b[0m\r") == "hello"
    assert _truncate_command("short", max_len=20) == "short"

    escaped = _truncate_command("abc\ndef", max_len=20)
    assert "\\n" in escaped

    truncated = _truncate_command("x" * 40, max_len=8)
    assert truncated.endswith(" [...]")


def test_run_command_success_and_tail_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Emit command lifecycle events and preserve nix-build stderr tail lines."""

    async def _fake_stream_process(
        args: list[str],
        *,
        timeout: float,
        env: object,
        output_limit: int | None,
    ) -> AsyncIterator[ProcessLine | ProcessDone]:
        assert args == ["nix", "build", "demo"]
        assert timeout == 5
        assert output_limit is None
        assert env == {"NIX_CONFIG": "accept-flake-config = true"}
        yield ProcessLine("stderr", "line-one\n")
        yield ProcessLine("stderr", "noise line\n")
        yield ProcessDone(
            LibCommandResult(
                args=["nix", "build"],
                returncode=0,
                stdout="out",
                stderr="err",
            )
        )

    monkeypatch.setattr("lib.update.process.stream_process", _fake_stream_process)
    events = _collect_stream(
        lambda emit: run_command(
            ["nix", "build", "demo"],
            options=RunCommandOptions(
                source="demo",
                env={"NIX_CONFIG": "accept-flake-config = true"},
                allow_failure=True,
                suppress_patterns=("noise",),
                config=resolve_config(subprocess_timeout=5),
            ),
            emit=emit,
        )
    )

    kinds = [event.kind for event in events]
    assert kinds == [
        UpdateEventKind.COMMAND_START,
        UpdateEventKind.LINE,
        UpdateEventKind.COMMAND_END,
    ]
    assert events[-1].payload == events.result
    assert events.result == CommandResult(
        args=["nix", "build", "demo"],
        returncode=0,
        stdout="out",
        stderr="err",
        allow_failure=True,
        tail_lines=("[stderr] line-one",),
    )


def test_run_command_timeout_and_missing_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raise user-facing errors for timeout and malformed stream output."""

    async def _timeout_stream(
        *_args: object, emit: EventSink = ignore_event, **_kwargs: object
    ) -> object:
        msg = "timeout"
        raise TimeoutError(msg)
        yield ProcessLine("stdout", "never")

    monkeypatch.setattr("lib.update.process.stream_process", _timeout_stream)
    with pytest.raises(RuntimeError, match="Command timed out"):
        _collect_stream(
            lambda emit: run_command(
                ["echo", "x"], options=RunCommandOptions(source="demo"), emit=emit
            )
        )

    async def _missing_done(*_args: object, **_kwargs: object) -> AsyncIterator[object]:
        yield ProcessLine("stdout", "line\n")

    monkeypatch.setattr("lib.update.process.stream_process", _missing_done)
    with pytest.raises(RuntimeError, match="without result"):
        _collect_stream(
            lambda emit: run_command(
                ["echo", "x"], options=RunCommandOptions(source="demo"), emit=emit
            )
        )


def test_run_nix_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """Build proper Nix arguments and retain command progress and results."""
    captured: dict[str, object] = {}

    async def _fake_run_command(
        args: list[str], *, options: RunCommandOptions, emit: EventSink = ignore_event
    ) -> CommandResult:
        captured["args"] = args
        captured["options"] = options
        await emit(UpdateEvent.status(options.source, "ok"))
        return CommandResult(args=args, returncode=0, stdout="built", stderr="")

    monkeypatch.setattr("lib.update.process.run_command", _fake_run_command)
    events = _collect_stream(
        lambda emit: run_nix_build(
            "pkgs.hello",
            options=NixBuildOptions(source="demo", verbose=True),
            emit=emit,
        )
    )
    assert events[0].kind == UpdateEventKind.STATUS
    assert events.result.stdout == "built"
    args = captured["args"]
    assert isinstance(args, list)
    assert args[:4] == ["nix", "build", "-L", "--verbose"]


def test_emit_successful_command_hash_helpers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Emit command lifecycle events and return converted hashes."""
    events = _collect_stream(
        lambda emit: _emit_successful_command(
            source="demo",
            args=["echo", "hi"],
            message="echo hi",
            runner=lambda: asyncio.sleep(0, result="hi"),
            emit=emit,
        )
    )
    assert [event.kind for event in events] == [
        UpdateEventKind.COMMAND_START,
        UpdateEventKind.COMMAND_END,
    ]
    monkeypatch.setattr(
        "lib.update.process.libnix_hash_convert",
        lambda _hash: asyncio.sleep(0, result="sha256-AAA="),
    )
    convert_events = _collect_stream(
        lambda emit: convert_nix_hash_to_sri("demo", "deadbeef", emit=emit)
    )
    assert convert_events.result == "sha256-AAA="

    prefetch_calls: list[tuple[str, str | None, float | None]] = []

    async def _prefetch_url(
        url: str,
        *,
        name: str | None = None,
        command_timeout: float | None = None,
    ) -> SimpleNamespace:
        prefetch_calls.append((url, name, command_timeout))
        return SimpleNamespace(hash="sha256-BBB=", storePath="/nix/store/example")

    monkeypatch.setattr("lib.update.process.libnix_prefetch_url_result", _prefetch_url)
    receipts = tmp_path / "prefetch.jsonl"
    monkeypatch.setenv("UPDATE_PREFETCH_RECEIPTS", str(receipts))
    prefetch_events = _collect_stream(
        lambda emit: compute_sri_hash(
            "demo",
            "https://example.com/releases/Town%20Assistant-1.8-33.dmg",
            config=resolve_config(subprocess_timeout=12),
            emit=emit,
        )
    )
    start_message = prefetch_events[0].message
    assert start_message is not None
    assert "--name Town-20Assistant-1.8-33.dmg" in start_message
    assert prefetch_events.result == "sha256-BBB="

    _collect_stream(
        lambda emit: compute_sri_hash(
            "demo",
            "https://example.com/app.dmg",
            config=resolve_config(subprocess_timeout=12),
            emit=emit,
        )
    )
    assert _nix_prefetch_name("https://example.com/releases/") is None
    assert prefetch_calls == [
        (
            "https://example.com/releases/Town%20Assistant-1.8-33.dmg",
            "Town-20Assistant-1.8-33.dmg",
            12,
        ),
        ("https://example.com/app.dmg", None, 12),
    ]
    assert [json.loads(line) for line in receipts.read_text().splitlines()] == [
        {"storePath": "/nix/store/example"},
        {"storePath": "/nix/store/example"},
    ]


@pytest.mark.parametrize(
    ("basename", "expected"),
    [
        ("Coast%20Local.dmg", "Coast-20Local.dmg"),
        ("file%2Fpart.zip", "file-2Fpart.zip"),
        ("...archive.zip", "archive.zip"),
        ("...", "unknown"),
        ("%%", "-"),
        ("a" * 211, "a" * 207),
    ],
)
def test_prefetch_names_match_fetchurl_store_identity(basename, expected) -> None:
    """Percent escapes remain encoded; sanitization follows nixpkgs' name rules."""
    assert _nix_prefetch_name(f"https://example.com/{basename}") == expected


@pytest.mark.parametrize(
    "transient_error",
    [
        "Failure when receiving data from the peer",
        "HTTP protocol violation",
        "HTTP/2 framing layer",
        "HTTP/2 stream",
    ],
)
def test_compute_sri_hash_retries_transient_prefetch_failure(
    monkeypatch: pytest.MonkeyPatch,
    transient_error: str,
    tmp_path: Path,
) -> None:
    """Retry transient nix-prefetch-url failures before surfacing an error."""
    calls = 0
    receipts = tmp_path / "prefetch.jsonl"
    monkeypatch.setenv("UPDATE_PREFETCH_RECEIPTS", str(receipts))

    async def _prefetch_url(
        url: str,
        *,
        name: str | None = None,
        command_timeout: float | None = None,
    ) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        assert url == "https://example.com/archive.tar.gz"
        assert name is None
        assert command_timeout == 19
        if calls == 1:
            raise NixCommandError(
                LibCommandResult(
                    args=["nix-prefetch-url"],
                    returncode=1,
                    stdout="",
                    stderr=transient_error,
                ),
                "prefetch failed",
            )
        return SimpleNamespace(hash="sha256-CCC=", storePath="/nix/store/example")

    monkeypatch.setattr("lib.update.process.libnix_prefetch_url_result", _prefetch_url)

    events = _collect_stream(
        lambda emit: compute_sri_hash(
            "demo",
            "https://example.com/archive.tar.gz",
            config=resolve_config(
                retries=2,
                retry_backoff=0,
                subprocess_timeout=19,
            ),
            emit=emit,
        )
    )

    assert calls == 2
    assert [json.loads(line) for line in receipts.read_text().splitlines()] == [
        {"storePath": "/nix/store/example"}
    ]
    command_starts = [
        event for event in events if event.kind is UpdateEventKind.COMMAND_START
    ]
    command_ends = [
        event for event in events if event.kind is UpdateEventKind.COMMAND_END
    ]
    assert len(command_starts) == len(command_ends) == 2
    retry_end = command_ends[0].payload
    assert retry_end.returncode == 1
    assert retry_end.allow_failure
    assert any(
        event.message == "URL prefetch hit a transient failure; retrying..."
        and isinstance(event.payload, StatusPayload)
        and event.payload.info == StatusInfo(kind=StatusKind.RETRY, value="attempt 2/2")
        for event in events
        if event.kind is UpdateEventKind.STATUS
    )
    assert events.result == "sha256-CCC="


def test_compute_url_hashes_gather_and_type_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gather per-URL hash values into a single mapping payload."""

    async def _fake_compute_sri_hash(
        source: str, url: str, *, config: object, emit: EventSink = ignore_event
    ) -> object:
        assert config is explicit_config
        await emit(UpdateEvent.status(source, f"hashing {url}"))
        return f"hash:{url}"

    monkeypatch.setattr("lib.update.process.compute_sri_hash", _fake_compute_sri_hash)
    explicit_config = resolve_config()
    events = _collect_stream(
        lambda emit: compute_url_hashes(
            "demo",
            ["https://a", "https://a", "https://b"],
            config=explicit_config,
            emit=emit,
        )
    )
    status_count = sum(1 for event in events if event.kind == UpdateEventKind.STATUS)
    assert status_count == 2
    value_payload = events.result
    assert value_payload == {
        "https://a": "hash:https://a",
        "https://b": "hash:https://b",
    }


@pytest.mark.parametrize(
    "failure", [RuntimeError("sink failed"), asyncio.CancelledError()]
)
def test_run_command_closes_process_before_sink_failure_returns(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    """A failed or cancelled progress sink must finish subprocess cleanup first."""
    cleaned = False

    async def _process(*_args: object, **_kwargs: object) -> AsyncIterator[object]:
        nonlocal cleaned
        try:
            yield ProcessLine("stdout", "ready\n")
        finally:
            await asyncio.sleep(0)
            cleaned = True

    async def _emit(event: UpdateEvent) -> None:
        if event.kind is UpdateEventKind.LINE:
            raise failure

    async def _run() -> None:
        with pytest.raises(type(failure)) as caught:
            await run_command(
                ["demo"], options=RunCommandOptions(source="demo"), emit=_emit
            )
        assert caught.value is failure
        assert cleaned

    monkeypatch.setattr("lib.update.process.stream_process", _process)
    asyncio.run(_run())


def test_prefetch_retry_never_repeats_the_runner_timeout() -> None:
    """A command timeout is bounded work, not a transient transfer failure."""
    from lib.nix.commands.base import CommandResult as ProcessResult
    from lib.nix.commands.base import NixCommandError
    from lib.update.process import _is_retryable_prefetch_error

    args = ["nix", "store", "prefetch-file", "https://example.test/a.tgz"]
    timed_out = NixCommandError(
        ProcessResult(args=args, returncode=-1, stdout="", stderr=""),
        message="command timed out after 2400.0s",
    )
    assert not _is_retryable_prefetch_error(timed_out)

    slow_peer = NixCommandError(
        ProcessResult(
            args=args, returncode=1, stdout="", stderr="curl: Operation timed out"
        )
    )
    assert _is_retryable_prefetch_error(slow_peer)
