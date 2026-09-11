"""Credentials stay out of diagnostics while request and hash data stay intact."""

import asyncio
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

import click
import pytest
import typer
from typer.testing import CliRunner

from lib.diagnostics import redact_urls
from lib.nix.commands.base import CommandResult as ProcessResult
from lib.nix.commands.base import (
    HashMismatchError,
    NixCommandError,
    ProcessDone,
    ProcessLine,
)
from lib.nix.models.sources import SourceEntry
from lib.update import cli, cli_validation, process
from lib.update.artifacts import GeneratedArtifact
from lib.update.cli_options import UpdateOptions
from lib.update.config import resolve_config
from lib.update.errors import format_exception
from lib.update.events import (
    CommandResult,
    StatusInfo,
    StatusKind,
    UpdateEvent,
    UpdateEventKind,
    expect_command_result,
    raise_failed_command,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_URL = "https://downloads.example/app.dmg?X-Amz-Credential=SYNTHETIC&X-Amz-Signature=fixture"
_SAFE = "https://downloads.example/app.dmg?REDACTED"
_HASH = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("no URL", "no URL"),
        ("https://example.test/file", "https://example.test/file"),
        (_URL, _SAFE),
        (
            "https://example.test/file?unknown-secret=fixture",
            "https://example.test/file?REDACTED",
        ),
        (
            "https://user:fixture@example.test/file#token",
            "https://REDACTED@example.test/file#REDACTED",
        ),
        ("https://[::1]/file?sig=fixture", "https://[::1]/file?REDACTED"),
        ("https://[invalid?sig=fixture", "REDACTED_URL"),
        (
            f"download '{_URL}' failed\nretry {_URL}",
            f"download '{_SAFE}' failed\nretry {_SAFE}",
        ),
    ],
)
def test_url_diagnostics_hide_all_credentials(message: str, expected: str) -> None:
    """Unknown signing schemes and malformed URLs cannot leak query credentials."""
    assert redact_urls(message) == expected
    assert redact_urls(expected) == expected


@pytest.mark.parametrize(
    ("url", "safe_url"),
    [
        (_URL, _SAFE),
        (
            f"https://user:{'SYNTHETIC' * 12}@downloads.example/app",
            "https://REDACTED@downloads.example/app",
        ),
    ],
)
def test_command_events_are_safe_without_changing_execution_results(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    safe_url: str,
) -> None:
    """Command argv and returned output stay raw; emitted projections are redacted."""
    args = ["download", url]
    stderr = f"fetching {url}\ngot: {_HASH}\n"

    async def stream(
        command: list[str], **_kwargs: object
    ) -> AsyncIterator[ProcessLine | ProcessDone]:
        assert command == args
        yield ProcessLine("stderr", stderr)
        yield ProcessDone(ProcessResult(args, 1, url, stderr))

    monkeypatch.setattr(process, "stream_process", stream)
    events: list[UpdateEvent] = []

    async def emit(event: UpdateEvent) -> None:
        events.append(event)

    result = asyncio.run(
        process.run_command(
            args, options=process.RunCommandOptions(source="demo"), emit=emit
        )
    )
    assert result.args == args
    assert result.stdout == url
    assert result.stderr == stderr
    assert [event.kind for event in events] == [
        UpdateEventKind.COMMAND_START,
        UpdateEventKind.LINE,
        UpdateEventKind.COMMAND_END,
    ]
    assert "SYNTHETIC" not in json.dumps([asdict(event) for event in events])
    diagnostic = expect_command_result(events[-1].payload)
    assert diagnostic.args == ["download", safe_url]
    assert diagnostic.stderr == f"fetching {safe_url}\ngot: {_HASH}\n"
    with pytest.raises(RuntimeError) as error:
        raise_failed_command("download", result)
    assert safe_url in str(error.value)
    assert "SYNTHETIC" not in str(error.value)


def test_prefetch_keeps_signed_request_and_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The signed redirect still reaches Nix verbatim and returns its real hash."""

    async def prefetch(url: str, **_kwargs: object) -> str:
        assert url == _URL
        return _HASH

    monkeypatch.setattr(process, "libnix_prefetch_url", prefetch)
    events: list[UpdateEvent] = []

    async def emit(event: UpdateEvent) -> None:
        events.append(event)

    assert (
        asyncio.run(
            process.compute_sri_hash("demo", _URL, config=resolve_config(), emit=emit)
        )
        == _HASH
    )
    assert events[0].message is not None
    assert _SAFE in events[0].message
    assert "SYNTHETIC" not in json.dumps([asdict(event) for event in events])


@pytest.mark.parametrize("timeout", [False, True])
def test_incomplete_command_and_low_level_hash_errors_redact_only_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    *,
    timeout: bool,
) -> None:
    """Incomplete commands and Nix errors conceal credentials without losing hashes."""

    async def stream(_args: list[str], **_kwargs: object) -> AsyncIterator[ProcessLine]:
        yield ProcessLine("stderr", "waiting")
        if timeout:
            raise TimeoutError

    monkeypatch.setattr(process, "stream_process", stream)
    with pytest.raises(RuntimeError) as failure:
        asyncio.run(
            process.run_command(
                ["download", _URL], options=process.RunCommandOptions(source="demo")
            )
        )
    assert _SAFE in str(failure.value)
    assert "SYNTHETIC" not in str(failure.value)

    result = ProcessResult(["nix-prefetch-url", _URL], 1, "", f"{_URL}\ngot: {_HASH}")
    error = HashMismatchError.from_output(result.stderr, result)
    assert isinstance(error, NixCommandError)
    assert error.hash == _HASH
    assert error.result is result
    assert _SAFE in str(error)
    assert "SYNTHETIC" not in str(error)


@pytest.mark.parametrize("unexpected", [False, True])
def test_task_debug_logs_and_tracebacks_are_redacted(
    caplog: pytest.LogCaptureFixture, *, unexpected: bool
) -> None:
    """Debug exception reporting cannot bypass the safe event boundary."""

    class UnexpectedError(Exception):
        pass

    async def task() -> None:
        error = UnexpectedError(_URL) if unexpected else RuntimeError(_URL)
        error.add_note(f"request {_URL}")
        raise error

    queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
    with caplog.at_level(logging.DEBUG, logger="lib.update.process"):
        if unexpected:
            with pytest.raises(UnexpectedError):
                asyncio.run(
                    process.run_queue_task(source="demo", queue=queue, task=task)
                )
        else:
            asyncio.run(process.run_queue_task(source="demo", queue=queue, task=task))
            event = queue.get_nowait()
            assert isinstance(event, UpdateEvent)
            assert event.message == _SAFE
    assert _SAFE in caplog.text
    assert "SYNTHETIC" not in caplog.text
    assert "SYNTHETIC" not in format_exception(
        RuntimeError(_URL), include_traceback=True
    )


def test_status_fields_and_command_tails_are_safe() -> None:
    """Typed status projections and fallback command tails share the boundary."""
    status = StatusInfo(StatusKind.UPDATED, value=_URL, current=_URL, latest=_URL)
    assert status.value == status.current == status.latest == _SAFE
    raw = CommandResult(["download"], 1, "", "", tail_lines=(_URL,))
    event = UpdateEvent("demo", UpdateEventKind.COMMAND_END, payload=raw)
    assert expect_command_result(event.payload).tail_lines == (_SAFE,)
    assert raw.tail_lines == (_URL,)


def test_functional_source_and_artifact_payloads_are_not_rewritten() -> None:
    """Redaction cannot alter persisted sources or generated artifact contents."""
    source = SourceEntry(hashes={}, urls={"download": _URL})
    artifact = GeneratedArtifact(path=Path("generated.txt"), content=_URL)
    assert UpdateEvent.result("demo", source).payload is source
    assert UpdateEvent.artifact("demo", artifact).payload == [artifact]
    # Legacy producers may supply a non-command payload; it remains the
    # consumer's responsibility to recognize it as something other than argv.
    payload = [artifact]
    assert (
        UpdateEvent("demo", UpdateEventKind.COMMAND_START, payload=payload).payload
        is payload
    )
    assert source.urls == {"download": _URL}
    assert artifact.content == _URL


def test_cli_json_and_human_errors_are_redacted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Final errors remain valid JSON and safe text even with quoted URLs."""
    message = f'failed "{_URL}"'
    outcome = cli._RunOutcome(
        summary=cli.UpdateSummary(statuses={_URL: "error"}),
        had_errors=True,
        workspace_error=message,
        plan_error=cli._RunPlanError(message, (_URL,), ("demo",)),
    )
    assert (
        cli._emit_run_outcome(
            outcome, out=cli.OutputOptions(json_output=True), dry_run=False
        )
        == 1
    )
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert payload["error"] == payload["planError"] == f'failed "{_SAFE}"'
    assert payload["unknownTargets"] == [_SAFE]
    assert payload["errors"] == [_SAFE]
    assert outcome.summary.errors == [_URL]
    assert "SYNTHETIC" not in output.out

    options = cli.OutputOptions()
    options.print(message)
    options.print_error(message)
    output = capsys.readouterr()
    assert "SYNTHETIC" not in output.out + output.err


@pytest.mark.parametrize(
    "arguments", [[], ["--verbose"], ["--json"], ["--json", "--verbose"], ["--quiet"]]
)
def test_unexpected_cli_errors_are_safe_failures(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str]
) -> None:
    """Unhandled workflow failures exit nonzero without exposing a raw traceback."""

    def run(**_kwargs: object) -> int:
        error = ArithmeticError(_URL)
        error.add_note(f"download {_URL}")
        raise error

    monkeypatch.setattr(cli, "run_update_command", run)
    result = CliRunner().invoke(cli.app, arguments)
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "SYNTHETIC" not in result.output
    if "--json" in arguments:
        payload = json.loads(result.stdout)
        assert payload["success"] is False
        message = payload["error"]
        assert result.stderr == ""
    else:
        message = result.stderr
        assert result.stdout == ""
    assert _SAFE in message
    assert ("Traceback" in message) is ("--verbose" in arguments)


@pytest.mark.parametrize(
    "error",
    [
        click.BadParameter("invalid option"),
        click.exceptions.Exit(7),
        click.Abort(),
        typer.BadParameter("invalid option"),
        typer.Exit(7),
        typer.Abort(),
        KeyboardInterrupt(),
        SystemExit(7),
    ],
)
def test_cli_preserves_usage_exits_and_interrupts(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    """Diagnostic handling cannot rewrite CLI control flow or cancellation."""

    def run(**_kwargs: object) -> int:
        raise error

    monkeypatch.setattr(cli, "run_update_command", run)
    with pytest.raises(type(error)) as raised:
        cli.cli()
    assert raised.value is error


def test_validate_json_error_is_redacted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The validation-only CLI cannot bypass final run error redaction."""

    def load() -> None:
        raise ValueError(_URL)

    monkeypatch.setattr(cli_validation.update_sources, "load_all_sources", load)
    assert (
        cli_validation.handle_validate_request(
            UpdateOptions(validate=True, json=True), cli.OutputOptions(json_output=True)
        )
        == 1
    )
    assert json.loads(capsys.readouterr().out) == {"valid": False, "error": _SAFE}
