"""Additional tests for CI shared CLI/subprocess helpers."""

from __future__ import annotations

import asyncio
import subprocess

import click
import pytest
import typer

from lib.update.ci import _cli as ci_cli
from lib.update.ci import _subprocess as ci_subprocess


def test_ci_cli_typer_factory_and_dual_registration() -> None:
    """Build paired Typer apps and register one shared entrypoint."""
    app = ci_cli.make_typer_app(help_text="demo")
    assert isinstance(app, typer.Typer)

    dual = ci_cli.make_dual_typer_apps(help_text="dual", no_args_is_help=True)
    assert isinstance(dual.app, typer.Typer)
    assert isinstance(dual.standalone_app, typer.Typer)

    decorator = ci_cli.register_dual_entrypoint(dual, invoke_without_command=False)

    @decorator
    def _handler() -> int:
        return 0

    assert callable(_handler)


def test_ci_run_main_success_and_click_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Normalize Typer/click outcomes to exit codes."""

    def _returns_int(
        *, args: list[str] | None, prog_name: str, standalone_mode: bool
    ) -> int:
        _ = (args, prog_name, standalone_mode)
        return 7

    assert (
        ci_cli.run_main(
            _returns_int,  # type: ignore[arg-type]
            argv=["--x"],
            prog_name="demo",
            default_exit_code=0,
        )
        == 7
    )

    def _returns_none(
        *, args: list[str] | None, prog_name: str, standalone_mode: bool
    ) -> None:
        _ = (args, prog_name, standalone_mode)

    assert (
        ci_cli.run_main(
            _returns_none,  # type: ignore[arg-type]
            argv=None,
            prog_name="demo",
            default_exit_code=3,
        )
        == 3
    )

    def _raises_exit(
        *, args: list[str] | None, prog_name: str, standalone_mode: bool
    ) -> None:
        _ = (args, prog_name, standalone_mode)
        raise click.exceptions.Exit(5)

    assert (
        ci_cli.run_main(
            _raises_exit,  # type: ignore[arg-type]
            argv=None,
            prog_name="demo",
        )
        == 5
    )

    def _raises_click(
        *, args: list[str] | None, prog_name: str, standalone_mode: bool
    ) -> None:
        _ = (args, prog_name, standalone_mode)
        raise click.ClickException("bad")

    assert (
        ci_cli.run_main(
            _raises_click,  # type: ignore[arg-type]
            argv=None,
            prog_name="demo",
        )
        == 1
    )
    assert "bad" in capsys.readouterr().err


def test_ci_make_main_delegates_to_run_main(monkeypatch: pytest.MonkeyPatch) -> None:
    """Create a conventional main(argv) wrapper with fixed defaults."""
    captured: dict[str, object] = {}

    def _run_main(
        app: typer.Typer,
        *,
        argv: list[str] | None,
        prog_name: str,
        default_exit_code: int,
    ) -> int:
        captured.update({
            "app": app,
            "argv": argv,
            "prog_name": prog_name,
            "default_exit_code": default_exit_code,
        })
        return 9

    monkeypatch.setattr(ci_cli, "run_main", _run_main)
    app = typer.Typer()
    main = ci_cli.make_main(app, prog_name="demo")
    assert main(["--x"]) == 9
    assert captured["app"] is app
    assert captured["prog_name"] == "demo"


class _FakeProc:
    def __init__(self, *, returncode: int, stdout: bytes, stderr: bytes) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


def test_ci_emit_hidden_output_formats_streams(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Render hidden stdout/stderr with stable labels and trailing newlines."""
    ci_subprocess._emit_hidden_output(
        args=["cmd", "arg with space"],
        stdout="out",
        stderr="err\n",
    )
    rendered = capsys.readouterr().err
    assert "Command failed with hidden stdout: cmd 'arg with space'" in rendered
    assert "out\nCommand failed with hidden stderr" in rendered
    assert rendered.endswith("err\n")

    ci_subprocess._emit_hidden_output(args=["cmd"], stdout="out\n", stderr="")
    rendered = capsys.readouterr().err
    assert rendered == "Command failed with hidden stdout: cmd\nout\n"

    ci_subprocess._emit_hidden_output(args=["cmd"], stdout="", stderr="")
    assert capsys.readouterr().err == ""


def test_ci_run_command_async_paths(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Capture output and surface hidden output when failed commands error."""
    seen: dict[str, object] = {}

    async def _create_process(
        *args: str, cwd: str | None, stdout: object, stderr: object
    ) -> _FakeProc:
        seen["stdout"] = stdout
        seen["stderr"] = stderr
        _ = (args, cwd)
        return _FakeProc(returncode=0, stdout=b"ok", stderr=b"")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _create_process)
    result = asyncio.run(
        ci_subprocess.run_command_async(["echo", "ok"], capture_output=True)
    )
    assert result.returncode == 0
    assert result.stdout == "ok"
    assert seen["stdout"] == asyncio.subprocess.PIPE
    assert seen["stderr"] == asyncio.subprocess.PIPE

    async def _create_process_fail(
        *args: str, cwd: str | None, stdout: object, stderr: object
    ) -> _FakeProc:
        seen["fail_stdout"] = stdout
        seen["fail_stderr"] = stderr
        _ = (args, cwd)
        return _FakeProc(returncode=2, stdout=b"hidden out", stderr=b"hidden err")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _create_process_fail)
    with pytest.raises(
        subprocess.CalledProcessError, match="returned non-zero exit status 2"
    ):
        asyncio.run(
            ci_subprocess.run_command_async(
                ["false"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        )
    rendered = capsys.readouterr().err
    assert "Command failed with hidden stdout" in rendered
    assert "hidden out" in rendered
    assert "Command failed with hidden stderr" in rendered
    assert "hidden err" in rendered
    assert seen["fail_stdout"] == asyncio.subprocess.PIPE
    assert seen["fail_stderr"] == asyncio.subprocess.PIPE

    with pytest.raises(
        subprocess.CalledProcessError, match="returned non-zero exit status 2"
    ):
        asyncio.run(ci_subprocess.run_command_async(["false"]))


def test_ci_run_command_async_honors_devnull_when_not_checking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not capture hidden output eagerly when the caller disabled check semantics."""
    seen: dict[str, object] = {}

    async def _create_process(
        *args: str, cwd: str | None, stdout: object, stderr: object
    ) -> _FakeProc:
        seen["stdout"] = stdout
        seen["stderr"] = stderr
        _ = (args, cwd)
        return _FakeProc(returncode=7, stdout=b"ignored", stderr=b"ignored")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _create_process)
    result = asyncio.run(
        ci_subprocess.run_command_async(
            ["false"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    )
    assert result.returncode == 7
    assert seen["stdout"] == subprocess.DEVNULL
    assert seen["stderr"] == subprocess.DEVNULL


def test_ci_run_command_sync_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run sync wrapper via asyncio.run around async command helper."""
    result = subprocess.CompletedProcess(args=["x"], returncode=0, stdout="", stderr="")

    async def _run_command_async(
        *_args: object, **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return result

    monkeypatch.setattr(ci_subprocess, "run_command_async", _run_command_async)
    assert ci_subprocess.run_command(["x"]) is result
