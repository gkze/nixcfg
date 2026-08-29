"""Behavioral checks for command-specific Typer app loading."""

import sys
from types import ModuleType

import pytest
import typer
from typer.core import TyperGroup
from typer.main import get_command
from typer.testing import CliRunner

from lib.cli import make_lazy_typer_app


def _install_module(monkeypatch, module_name: str, app: typer.Typer) -> None:
    module = ModuleType(module_name)
    module.app = app
    monkeypatch.setitem(sys.modules, module_name, module)


def _mount_lazy_app(
    module_name: str,
    help_text: str,
) -> tuple[typer.Typer, TyperGroup]:
    parent = typer.Typer()
    parent.add_typer(
        make_lazy_typer_app(module_name=module_name, help_text=help_text),
        name="lazy",
    )
    root = get_command(parent)
    command = root.commands["lazy"]
    assert isinstance(command, TyperGroup)
    return parent, command


def test_lazy_typer_app_dispatches_and_exposes_group_metadata(
    monkeypatch,
    capsys,
) -> None:
    """Dispatch, help, and command discovery should use the loaded real group."""
    real_app = typer.Typer(help="Real command group.")

    @real_app.callback()
    def root() -> None:
        return None

    @real_app.command()
    def hello() -> None:
        typer.echo("hello")

    _install_module(monkeypatch, "tests.fake_lazy_group", real_app)
    parent, command = _mount_lazy_app(
        "tests.fake_lazy_group",
        "Deferred group.",
    )
    context = command.make_context("lazy", [], resilient_parsing=True)

    assert command.list_commands(context) == ["hello"]
    assert command.get_command(context, "hello") is not None
    command.get_help(context)
    assert "Real command group." in capsys.readouterr().out

    result = CliRunner().invoke(parent, ["lazy", "hello"])

    assert result.exit_code == 0
    assert result.output == "hello\n"


def test_lazy_typer_app_rejects_a_single_command(monkeypatch) -> None:
    """A mounted lazy app must resolve to a group, not a standalone command."""
    single_command_app = typer.Typer()

    @single_command_app.command()
    def hello() -> None:
        return None

    _install_module(monkeypatch, "tests.fake_lazy_command", single_command_app)
    _parent, command = _mount_lazy_app(
        "tests.fake_lazy_command",
        "Invalid deferred command.",
    )
    context = command.make_context("lazy", [], resilient_parsing=True)

    with pytest.raises(
        TypeError,
        match=r"tests\.fake_lazy_command\.app is not a command group",
    ):
        command.get_help(context)
