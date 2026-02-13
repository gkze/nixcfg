"""CLI-level tests for the nixcfg Typer entrypoint.

These tests focus on argument parsing/dispatch glue, not the update pipeline
implementation (which is covered elsewhere).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from typer.testing import CliRunner

import nixcfg

if TYPE_CHECKING:
    from collections.abc import MutableMapping

    from lib.update.cli import UpdateOptions


class _MonkeyPatchLike(Protocol):
    def setattr(self, target: str, value: object) -> None: ...

    def setitem(
        self, mapping: MutableMapping[object, object], name: object, value: object
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

    assert result.exit_code == 0  # noqa: S101
    assert called["opts"].native_only is True  # noqa: S101


def test_nixcfg_ci_registers_sources_json_diff(monkeypatch: _MonkeyPatchLike) -> None:
    """Ensure `nixcfg ci sources-json-diff` exists as a callable command."""
    from lib.update import ci

    called: list[list[str]] = []

    def _fake(args: list[str]) -> int:
        called.append(list(args))
        return 0

    monkeypatch.setitem(
        ci.CI_COMMANDS,  # type: ignore[arg-type]
        "sources-json-diff",
        ci.CICommand(func=_fake, help="test"),
    )

    runner = CliRunner()
    result = runner.invoke(nixcfg.app, ["ci", "sources-json-diff"])

    assert result.exit_code == 0  # noqa: S101
    assert called == [[]]  # noqa: S101
