"""CLI-level tests for the nixcfg Typer entrypoint.

These tests focus on argument parsing/dispatch glue, not the update pipeline
implementation (which is covered elsewhere).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

import click
from typer.main import get_command
from typer.testing import CliRunner

import nixcfg
from lib.schema_codegen.runner import SchemaTargetSummary
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


def test_nixcfg_recover_snapshot_parses_flags(monkeypatch: _MonkeyPatchLike) -> None:
    """Ensure recover snapshot forwards its argument and flags."""
    called: dict[str, object] = {}

    def _fake_run(
        generation: str = "/run/current-system",
        *,
        json_output: bool = False,
    ) -> int:
        called.update(generation=generation, json_output=json_output)
        return 0

    monkeypatch.setattr("lib.recover.cli.run_snapshot_recovery", _fake_run)

    runner = CliRunner()
    result = runner.invoke(
        nixcfg.app, ["recover", "snapshot", "/run/current-system", "-j"]
    )

    check(result.exit_code == 0)
    check(called == {"generation": "/run/current-system", "json_output": True})


def test_nixcfg_recover_files_parses_flags(monkeypatch: _MonkeyPatchLike) -> None:
    """Ensure recover files forwards selectors and flags."""
    called: dict[str, object] = {}

    def _fake_run(
        generation: str = "/run/current-system",
        *,
        apply: bool = False,
        globs: tuple[str, ...] = (),
        json_output: bool = False,
        paths: tuple[str, ...] = (),
        stage: bool = False,
        sync: bool = False,
    ) -> int:
        called.update(
            generation=generation,
            apply=apply,
            globs=globs,
            json_output=json_output,
            paths=paths,
            stage=stage,
            sync=sync,
        )
        return 0

    monkeypatch.setattr("lib.recover.cli.run_file_recovery", _fake_run)

    runner = CliRunner()
    result = runner.invoke(
        nixcfg.app,
        [
            "recover",
            "files",
            "/run/current-system",
            "-a",
            "-g",
            "-s",
            "-j",
            "-p",
            "flake.lock",
            "-G",
            "docs/*.md",
        ],
    )

    check(result.exit_code == 0)
    check(
        called
        == {
            "generation": "/run/current-system",
            "apply": True,
            "globs": ("docs/*.md",),
            "json_output": True,
            "paths": ("flake.lock",),
            "stage": True,
            "sync": True,
        }
    )


def test_nixcfg_recover_hashes_parses_flags(monkeypatch: _MonkeyPatchLike) -> None:
    """Ensure recover hashes forwards its argument and flags."""
    called: dict[str, object] = {}

    def _fake_run(
        generation: str = "/run/current-system",
        *,
        apply: bool = False,
        json_output: bool = False,
        stage: bool = False,
        sync: bool = False,
    ) -> int:
        called.update(
            generation=generation,
            apply=apply,
            json_output=json_output,
            stage=stage,
            sync=sync,
        )
        return 0

    monkeypatch.setattr("lib.recover.cli.run_hash_recovery", _fake_run)

    runner = CliRunner()
    result = runner.invoke(
        nixcfg.app,
        ["recover", "hashes", "/run/current-system", "-a", "-g", "-s", "-j"],
    )

    check(result.exit_code == 0)
    check(
        called
        == {
            "generation": "/run/current-system",
            "apply": True,
            "json_output": True,
            "stage": True,
            "sync": True,
        }
    )


def test_nixcfg_update_help_includes_typer_options() -> None:
    """Ensure `nixcfg update --help` shows typed option definitions."""
    runner = CliRunner()
    result = runner.invoke(nixcfg.app, ["update", "--help"])

    check(result.exit_code == 0)
    check("--native-only" in result.output)
    check("--pinned-versions" in result.output)
    check("--no-sources" in result.output)


def test_nixcfg_schema_targets_lists_configured_targets(
    monkeypatch: _MonkeyPatchLike,
) -> None:
    """Ensure `nixcfg schema targets` renders target summaries."""
    monkeypatch.setattr(
        "nixcfg.list_schema_codegen_targets",
        lambda *, config_path: (
            SchemaTargetSummary(name="demo", output=config_path.parent / "demo.py"),
        ),
    )

    runner = CliRunner()
    result = runner.invoke(nixcfg.app, ["schema", "targets"])

    check(result.exit_code == 0)
    check("demo\tdemo.py" in result.output)


def test_nixcfg_schema_generate_forwards_target_and_config(
    monkeypatch: _MonkeyPatchLike,
) -> None:
    """Ensure `nixcfg schema generate` forwards target and config path."""
    called: dict[str, object] = {}

    def _fake_generate(
        *, config_path: object, progress: object, target_name: str
    ) -> Path:
        called.update(
            config_path=config_path, progress=progress, target_name=target_name
        )
        return Path("/tmp/generated.py")

    monkeypatch.setattr("nixcfg.generate_schema_codegen_target", _fake_generate)

    runner = CliRunner()
    result = runner.invoke(
        nixcfg.app,
        ["schema", "generate", "demo", "-c", "alt-config.yaml"],
    )

    check(result.exit_code == 0)
    check(called["target_name"] == "demo")
    check(called["progress"] is nixcfg._schema_progress)
    check(called["config_path"] == Path("alt-config.yaml"))
    check("Generated /tmp/generated.py" in result.output)


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


def test_nixcfg_ci_subcommand_help_includes_crate2nix_options() -> None:
    """Ensure mounted crate2nix CI app is registered with its flags."""
    runner = CliRunner()
    result = runner.invoke(
        nixcfg.app,
        ["ci", "pipeline", "crate2nix", "--help"],
    )

    check(result.exit_code == 0)
    check("--package" in result.output)
    check("--write" in result.output)


def test_nixcfg_ci_cache_generations_help_exposes_profile_options() -> None:
    """Ensure mounted generation profiling command is registered."""
    runner = CliRunner()
    result = runner.invoke(
        nixcfg.app,
        ["ci", "cache", "generations", "--help"],
    )

    check(result.exit_code == 0)
    check("--profile-output" in result.output)


def test_nixcfg_recover_snapshot_help_exposes_recovery_options() -> None:
    """Ensure mounted snapshot recovery command is registered with its flags."""
    runner = CliRunner()
    result = runner.invoke(
        nixcfg.app,
        ["recover", "snapshot", "--help"],
    )

    check(result.exit_code == 0)
    check("--json" in result.output)


def test_nixcfg_recover_files_help_exposes_recovery_options() -> None:
    """Ensure mounted file recovery command is registered with its flags."""
    runner = CliRunner()
    result = runner.invoke(
        nixcfg.app,
        ["recover", "files", "--help"],
    )

    check(result.exit_code == 0)
    check("--apply" in result.output)
    check("--path" in result.output)
    check("--glob" in result.output)
    check("--stage" in result.output)
    check("--sync" in result.output)
    check("--json" in result.output)


def test_nixcfg_recover_hashes_help_exposes_recovery_options() -> None:
    """Ensure mounted hash recovery command is registered with its flags."""
    runner = CliRunner()
    result = runner.invoke(
        nixcfg.app,
        ["recover", "hashes", "--help"],
    )

    check(result.exit_code == 0)
    check("--apply" in result.output)
    check("--stage" in result.output)
    check("--sync" in result.output)
    check("--json" in result.output)


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


def test_nixcfg_tree_colors_empty_groups_like_leaf_commands() -> None:
    """Color callable groups without visible children like leaf commands."""
    root = cast("click.Group", get_command(nixcfg.app))
    ci = cast("click.Group", root.commands["ci"])
    cache = cast("click.Group", ci.commands["cache"])
    closure = cache.commands["closure"]

    check(
        nixcfg._command_label("cache", cache).startswith("[bold cyan]cache[/bold cyan]")
    )
    check(
        nixcfg._command_label("closure", closure).startswith("[green]closure[/green]")
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
