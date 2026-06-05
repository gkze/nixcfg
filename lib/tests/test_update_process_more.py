"""Additional tests for subprocess/process helpers in update flows."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from lib.nix.commands.base import CommandResult as LibCommandResult
from lib.nix.commands.base import NixCommandError, ProcessDone, ProcessLine
from lib.update.config import resolve_config
from lib.update.events import GatheredValues, UpdateEvent, UpdateEventKind
from lib.update.process import (
    NixBuildOptions,
    RunCommandOptions,
    StreamCommandOptions,
    _emit_successful_command,
    _nix_prefetch_name,
    _sanitize_log_line,
    _truncate_command,
    compute_sri_hash,
    compute_url_hashes,
    convert_nix_hash_to_sri,
    run_command,
    run_nix_build,
    stream_command,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _collect_stream(stream: AsyncIterator[UpdateEvent]) -> list[UpdateEvent]:
    async def _run() -> list[UpdateEvent]:
        items: list[UpdateEvent] = []
        async for item in stream:
            items.append(item)
        return items

    return asyncio.run(_run())


def test_sanitize_and_truncate_helpers() -> None:
    """Strip ANSI/control chars and cap long command strings."""
    assert _sanitize_log_line("\x1b[31mhello\x1b[0m\r") == "hello"
    assert _truncate_command("short", max_len=20) == "short"

    escaped = _truncate_command("abc\ndef", max_len=20)
    assert "\\n" in escaped

    truncated = _truncate_command("x" * 40, max_len=8)
    assert truncated.endswith(" [...]")


def test_stream_command_success_and_tail_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Emit command lifecycle events and preserve nix-build stderr tail lines."""

    async def _fake_stream_process(
        _args: list[str],
        *,
        timeout: float,
        env: object,
    ) -> AsyncIterator[ProcessLine | ProcessDone]:
        _ = (timeout, env)
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
        stream_command(
            ["nix", "build", "demo"],
            options=StreamCommandOptions(
                source="demo",
                suppress_patterns=("noise",),
                config=resolve_config(subprocess_timeout=5),
            ),
        )
    )

    kinds = [event.kind for event in events]
    assert kinds == [
        UpdateEventKind.COMMAND_START,
        UpdateEventKind.LINE,
        UpdateEventKind.COMMAND_END,
    ]
    end_payload = events[-1].payload
    if not isinstance(end_payload, type(events[-1].payload)):
        raise AssertionError
    assert hasattr(end_payload, "tail_lines")
    assert tuple(end_payload.tail_lines) == ("[stderr] line-one",)


def test_stream_command_timeout_and_missing_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raise user-facing errors for timeout and malformed stream output."""

    async def _timeout_stream(
        *_args: object, **_kwargs: object
    ) -> AsyncIterator[object]:
        msg = "timeout"
        raise TimeoutError(msg)
        yield UpdateEvent.status("never", "never")

    monkeypatch.setattr("lib.update.process.stream_process", _timeout_stream)
    with pytest.raises(RuntimeError, match="Command timed out"):
        _collect_stream(
            stream_command(["echo", "x"], options=StreamCommandOptions(source="demo"))
        )

    async def _missing_done(*_args: object, **_kwargs: object) -> AsyncIterator[object]:
        yield ProcessLine("stdout", "line\n")

    monkeypatch.setattr("lib.update.process.stream_process", _missing_done)
    with pytest.raises(RuntimeError, match="without result"):
        _collect_stream(
            stream_command(["echo", "x"], options=StreamCommandOptions(source="demo"))
        )


def test_run_command_and_run_nix_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """Capture command result as VALUE event and build proper nix args."""

    async def _fake_stream(
        _args: list[str],
        *,
        options: StreamCommandOptions,
    ) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent(
            source=options.source,
            kind=UpdateEventKind.COMMAND_END,
            payload=UpdateEvent.value(
                options.source,
                "ignored",
            ).payload,
        )
        yield UpdateEvent(
            source=options.source,
            kind=UpdateEventKind.COMMAND_END,
            payload=type(
                "_Result",
                (),
                {
                    "args": ["x"],
                    "returncode": 0,
                    "stdout": "s",
                    "stderr": "",
                    "allow_failure": False,
                    "tail_lines": (),
                },
            )(),
        )

    # use a real CommandResult payload on second event to set drain.value
    async def _fake_stream_real(
        _args: list[str],
        *,
        options: StreamCommandOptions,
    ) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent(
            source=options.source,
            kind=UpdateEventKind.COMMAND_END,
            payload=type("_Ignore", (), {})(),
        )
        from lib.update.events import CommandResult

        yield UpdateEvent(
            source=options.source,
            kind=UpdateEventKind.COMMAND_END,
            payload=CommandResult(args=["x"], returncode=0, stdout="ok", stderr=""),
        )

    monkeypatch.setattr("lib.update.process.stream_command", _fake_stream_real)
    out_events = _collect_stream(
        run_command(
            ["echo", "x"],
            options=RunCommandOptions(source="demo", error="failed"),
        )
    )
    assert out_events[-1].kind == UpdateEventKind.VALUE

    async def _fake_stream_missing(
        _args: list[str],
        *,
        options: StreamCommandOptions,
    ) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.status(options.source, "only status")

    monkeypatch.setattr("lib.update.process.stream_command", _fake_stream_missing)
    with pytest.raises(RuntimeError, match="did not return output"):
        _collect_stream(
            run_command(
                ["echo", "x"],
                options=RunCommandOptions(source="demo", error="did not return output"),
            )
        )

    captured: dict[str, object] = {}

    async def _fake_run_command(
        args: list[str],
        *,
        options: RunCommandOptions,
    ) -> AsyncIterator[UpdateEvent]:
        captured["args"] = args
        captured["options"] = options
        yield UpdateEvent.status(options.source, "ok")

    monkeypatch.setattr("lib.update.process.run_command", _fake_run_command)
    events = _collect_stream(
        run_nix_build(
            "pkgs.hello",
            options=NixBuildOptions(source="demo", verbose=True),
        )
    )
    assert events[0].kind == UpdateEventKind.STATUS
    args = captured["args"]
    assert isinstance(args, list)
    assert args[:4] == ["nix", "build", "-L", "--verbose"]


def test_emit_successful_command_hash_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Emit command start/end/value around hash conversion helpers."""
    events = _collect_stream(
        _emit_successful_command(
            source="demo",
            args=["echo", "hi"],
            message="echo hi",
            runner=lambda: asyncio.sleep(0, result="hi"),
        )
    )
    assert [event.kind for event in events] == [
        UpdateEventKind.COMMAND_START,
        UpdateEventKind.COMMAND_END,
        UpdateEventKind.VALUE,
    ]
    monkeypatch.setattr(
        "lib.update.process.libnix_hash_convert",
        lambda _hash: asyncio.sleep(0, result="sha256-AAA="),
    )
    convert_events = _collect_stream(convert_nix_hash_to_sri("demo", "deadbeef"))
    assert convert_events[-1].payload == "sha256-AAA="

    prefetch_calls: list[tuple[str, str | None]] = []

    async def _prefetch_url(url: str, *, name: str | None = None) -> str:
        prefetch_calls.append((url, name))
        return "sha256-BBB="

    monkeypatch.setattr("lib.update.process.libnix_prefetch_url", _prefetch_url)
    prefetch_events = _collect_stream(
        compute_sri_hash(
            "demo",
            "https://example.com/releases/Town%20Assistant-1.8-33.dmg",
        )
    )
    start_message = prefetch_events[0].message
    assert start_message is not None
    assert "--name Town-Assistant-1.8-33.dmg" in start_message
    assert prefetch_events[-1].payload == "sha256-BBB="

    _collect_stream(compute_sri_hash("demo", "https://example.com/app.dmg"))
    assert _nix_prefetch_name("https://example.com/releases/") is None
    assert prefetch_calls == [
        (
            "https://example.com/releases/Town%20Assistant-1.8-33.dmg",
            "Town-Assistant-1.8-33.dmg",
        ),
        ("https://example.com/app.dmg", None),
    ]


def test_compute_sri_hash_retries_transient_prefetch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry transient nix-prefetch-url failures before surfacing an error."""
    calls = 0

    async def _prefetch_url(url: str, *, name: str | None = None) -> str:
        nonlocal calls
        calls += 1
        assert url == "https://example.com/archive.tar.gz"
        assert name is None
        if calls == 1:
            raise NixCommandError(
                LibCommandResult(
                    args=["nix-prefetch-url"],
                    returncode=1,
                    stdout="",
                    stderr="Failure when receiving data from the peer",
                ),
                "prefetch failed",
            )
        return "sha256-CCC="

    monkeypatch.setattr("lib.update.process.libnix_prefetch_url", _prefetch_url)
    monkeypatch.setattr(
        "lib.update.process.resolve_active_config",
        lambda _config: resolve_config(retries=2, retry_backoff=0),
    )

    events = _collect_stream(
        compute_sri_hash("demo", "https://example.com/archive.tar.gz")
    )

    assert calls == 2
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
        event.message == "nix-prefetch-url hit a transient failure; retrying..."
        and event.payload["detail"] == "attempt 2/2"
        for event in events
        if event.kind is UpdateEventKind.STATUS
    )
    assert events[-1].payload == "sha256-CCC="


def test_compute_url_hashes_gather_and_type_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gather per-URL hash values into a single mapping payload."""

    async def _fake_compute_sri_hash(
        source: str, url: str
    ) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.status(source, f"hashing {url}")
        yield UpdateEvent.value(source, f"hash:{url}")

    monkeypatch.setattr("lib.update.process.compute_sri_hash", _fake_compute_sri_hash)
    events = _collect_stream(
        compute_url_hashes("demo", ["https://a", "https://a", "https://b"])
    )
    status_count = sum(1 for event in events if event.kind == UpdateEventKind.STATUS)
    assert status_count == 2
    value_payload = events[-1].payload
    assert value_payload == {
        "https://a": "hash:https://a",
        "https://b": "hash:https://b",
    }

    async def _fake_gather(_streams: object) -> AsyncIterator[object]:
        yield GatheredValues(values={1: "x"})

    monkeypatch.setattr("lib.update.process.gather_event_streams", _fake_gather)
    with pytest.raises(TypeError, match="Expected URL key to be str"):
        _collect_stream(compute_url_hashes("demo", ["https://a"]))
