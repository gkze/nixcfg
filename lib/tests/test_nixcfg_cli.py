"""CLI-level tests for the nixcfg Typer entrypoint.

These tests focus on argument parsing/dispatch glue, not the update pipeline
implementation (which is covered elsewhere).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import click
from typer.main import get_command
from typer.testing import CliRunner

import nixcfg
from lib.tests._assertions import check

if TYPE_CHECKING:
    from lib.update.cli import UpdateOptions


class _MonkeyPatchLike(Protocol):
    def setattr(self, target: str, value: object) -> None: ...


def test_nixcfg_update_parses_native_only(monkeypatch: _MonkeyPatchLike) -> None:
    """Ensure `nixcfg update --native-only` maps to UpdateOptions.native_only."""
    called: dict[str, UpdateOptions] = {}

    async def _fake_run_updates(opts: UpdateOptions) -> int:
        called["opts"] = opts
        return 0

    monkeypatch.setattr("lib.update.cli.check_required_tools", lambda **_kw: [])
    monkeypatch.setattr("lib.update.cli.run_updates", _fake_run_updates)

    runner = CliRunner()
    result = runner.invoke(nixcfg.app, ["update", "--native-only"])

    check(result.exit_code == 0)
    check(called["opts"].native_only is True)


def test_nixcfg_update_help_includes_typer_options() -> None:
    """Ensure `nixcfg update --help` shows typed option definitions."""
    runner = CliRunner()
    result = runner.invoke(nixcfg.app, ["update", "--help"])

    check(result.exit_code == 0)
    check("--native-only" in result.output)
    check("--pinned-versions" in result.output)
    check("--no-sources" in result.output)


def test_nixcfg_all_commands_support_short_help_alias() -> None:
    """Ensure every command exposes `-h` alongside `--help`."""
    root = get_command(nixcfg.app)
    runner = CliRunner()
    failures: list[str] = []
    command_paths: list[list[str]] = []

    def _walk(cmd: click.Command, path: list[str]) -> None:
        command_paths.append(path)
        if not isinstance(cmd, click.Group):
            return
        for name, subcommand in cmd.commands.items():
            _walk(subcommand, [*path, name])

    _walk(root, [])

    for path in command_paths:
        result = runner.invoke(nixcfg.app, [*path, "-h"])
        if result.exit_code != 0:
            path_display = "nixcfg" if not path else f"nixcfg {' '.join(path)}"
            failures.append(f"{path_display} (-h) -> exit {result.exit_code}")

    check(failures == [], failures)


def test_nixcfg_ci_registers_sources_json_diff() -> None:
    """Ensure nested `nixcfg ci diff sources` is available."""
    runner = CliRunner()
    result = runner.invoke(nixcfg.app, ["ci", "diff", "sources", "--help"])

    check(result.exit_code == 0)
    check("--format" in result.output)


def test_nixcfg_ci_subcommand_help_includes_resolve_options() -> None:
    """Ensure mounted CI apps expose their native option help."""
    runner = CliRunner()
    result = runner.invoke(
        nixcfg.app,
        ["ci", "pipeline", "versions", "--help"],
    )

    check(result.exit_code == 0)
    check("--output" in result.output)


def test_nixcfg_ci_cache_generations_help_exposes_profile_options() -> None:
    """Ensure mounted generation profiling command is registered."""
    runner = CliRunner()
    result = runner.invoke(
        nixcfg.app,
        ["ci", "cache", "generations", "--help"],
    )

    check(result.exit_code == 0)
    check("--profile-output" in result.output)


def test_nixcfg_tree_shows_declared_command_descriptions() -> None:
    """Ensure `nixcfg tree` includes declared command help descriptions."""
    runner = CliRunner()
    result = runner.invoke(nixcfg.app, ["tree"])

    check(result.exit_code == 0)
    check("ci - CI helper tools for update pipelines." in result.output)
    check("pr-body - Pull request body generation workflow step." in result.output)
    check(
        "update - Update source versions/hashes and flake input refs." in result.output
    )


def test_nixcfg_all_custom_options_have_short_and_long_forms() -> None:
    """Require short+long aliases for every non-built-in CLI option."""
    root = get_command(nixcfg.app)
    exempt_names = {"help", "install_completion", "show_completion"}
    missing: list[str] = []

    def _walk(cmd: click.Command, path: list[str]) -> None:
        for param in cmd.params:
            if not isinstance(param, click.Option):
                continue
            if param.name in exempt_names:
                continue

            names = [*param.opts, *param.secondary_opts]
            has_long = any(name.startswith("--") for name in names)
            has_short = any(
                name.startswith("-") and not name.startswith("--") for name in names
            )
            if not (has_long and has_short):
                missing.append(f"{'/'.join(path)}:{param.name} -> {names}")

        if not isinstance(cmd, click.Group):
            return
        for name, subcommand in cmd.commands.items():
            _walk(subcommand, [*path, name])

    _walk(root, ["nixcfg"])
    check(missing == [])


def test_nixcfg_main_uses_stable_prog_name(monkeypatch: _MonkeyPatchLike) -> None:
    """Ensure help usage keeps `nixcfg` instead of wrapper/store paths."""
    called: dict[str, str] = {}

    def _fake_app(*, prog_name: str) -> None:
        called["prog_name"] = prog_name

    monkeypatch.setattr("nixcfg.app", _fake_app)

    nixcfg.main()

    check(called["prog_name"] == "nixcfg")
