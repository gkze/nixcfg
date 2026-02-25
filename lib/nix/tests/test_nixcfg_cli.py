"""CLI-level tests for the nixcfg Typer entrypoint.

These tests focus on argument parsing/dispatch glue, not the update pipeline
implementation (which is covered elsewhere).
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Protocol, TypeVar

from typer.testing import CliRunner

import nixcfg
from lib.nix.tests._assertions import check
from lib.update import ci

if TYPE_CHECKING:
    from collections.abc import MutableMapping

    from lib.update.cli import UpdateOptions


_KT = TypeVar("_KT")
_VT = TypeVar("_VT")


class _MonkeyPatchLike(Protocol):
    def setattr(self, target: str, value: object) -> None: ...

    def setitem(
        self,
        mapping: MutableMapping[_KT, _VT],
        name: _KT,
        value: _VT,
    ) -> None: ...


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


def test_nixcfg_update_help_includes_forwarded_options() -> None:
    """Ensure `nixcfg update --help` shows argparse-defined options."""
    runner = CliRunner()
    result = runner.invoke(nixcfg.app, ["update", "--help"])

    check(result.exit_code == 0)
    check("--native-only" in result.output)
    check("--pinned-versions" in result.output)
    check("--no-sources" in result.output)


def test_nixcfg_ci_registers_sources_json_diff(monkeypatch: _MonkeyPatchLike) -> None:
    """Ensure `nixcfg ci sources-json-diff` exists as a callable command."""
    called: list[list[str]] = []

    def _fake(args: list[str]) -> int:
        called.append(list(args))
        return 0

    monkeypatch.setitem(
        ci.CI_COMMANDS,
        "sources-json-diff",
        ci.CICommand(func=_fake, help="test"),
    )

    runner = CliRunner()
    result = runner.invoke(nixcfg.app, ["ci", "sources-json-diff"])

    check(result.exit_code == 0)
    check(called == [[]])


def test_nixcfg_ci_subcommand_help_is_forwarded(
    monkeypatch: _MonkeyPatchLike,
) -> None:
    """Ensure `nixcfg ci <cmd> --help` is handled by the command parser."""

    def _fake(args: list[str]) -> int:
        parser = argparse.ArgumentParser(prog="nixcfg ci sources-json-diff")
        parser.add_argument("--example-opt", action="store_true", help="example")
        parser.parse_args(args)
        return 0

    monkeypatch.setitem(
        ci.CI_COMMANDS,
        "sources-json-diff",
        ci.CICommand(func=_fake, help="test"),
    )

    runner = CliRunner()
    result = runner.invoke(nixcfg.app, ["ci", "sources-json-diff", "--help"])

    check(result.exit_code == 0)
    check("--example-opt" in result.output)


def test_nixcfg_main_uses_stable_prog_name(monkeypatch: _MonkeyPatchLike) -> None:
    """Ensure help usage keeps `nixcfg` instead of wrapper/store paths."""
    called: dict[str, str] = {}

    def _fake_app(*, prog_name: str) -> None:
        called["prog_name"] = prog_name

    monkeypatch.setattr("nixcfg.app", _fake_app)

    nixcfg.main()

    check(called["prog_name"] == "nixcfg")
