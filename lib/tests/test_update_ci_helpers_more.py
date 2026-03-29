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


def test_ci_run_command_async_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Capture output and raise CalledProcessError on failed commands."""

    async def _create_process(
        *args: str, cwd: str | None, stdout: object, stderr: object
    ) -> _FakeProc:
        _ = (args, cwd, stdout, stderr)
        return _FakeProc(returncode=0, stdout=b"ok", stderr=b"")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _create_process)
    result = asyncio.run(
        ci_subprocess.run_command_async(["echo", "ok"], capture_output=True)
    )
    assert result.returncode == 0
    assert result.stdout == "ok"

    async def _create_process_fail(
        *args: str, cwd: str | None, stdout: object, stderr: object
    ) -> _FakeProc:
        _ = (args, cwd, stdout, stderr)
        return _FakeProc(returncode=2, stdout=b"", stderr=b"boom")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _create_process_fail)
    with pytest.raises(
        subprocess.CalledProcessError, match="returned non-zero exit status 2"
    ):
        asyncio.run(ci_subprocess.run_command_async(["false"]))


def test_ci_run_command_sync_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run sync wrapper via asyncio.run around async command helper."""
    result = subprocess.CompletedProcess(args=["x"], returncode=0, stdout="", stderr="")

    async def _run_command_async(
        *_args: object, **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return result

    monkeypatch.setattr(ci_subprocess, "run_command_async", _run_command_async)
    assert ci_subprocess.run_command(["x"]) is result
