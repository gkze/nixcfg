"""Behavioral tests for the local current-tree CI parity command."""

from __future__ import annotations

import subprocess

import pytest
import typer

from lib.update.ci import test_pipeline
from lib.update.ci.workflow_defs import DEEP_QUALITY_CHECKS, FAST_QUALITY_CHECKS


def test_run_executes_exact_current_tree_ci_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use the same quality, pin, and crate2nix gates as hosted CI."""
    commands: list[tuple[str, ...]] = []

    def fake_run(command: tuple[str, ...]) -> int:
        commands.append(command)
        return 0

    monkeypatch.setattr(test_pipeline, "_run_command", fake_run)

    assert test_pipeline.run() == 0
    assert commands == [
        *[
            ("nix", "build", f"path:.#checks.x86_64-linux.{check}")
            for check in (*FAST_QUALITY_CHECKS, *DEEP_QUALITY_CHECKS)
        ],
        (
            "nix",
            "run",
            "--inputs-from",
            "path:.",
            "nixpkgs#pinact",
            "--",
            "run",
            "--check",
        ),
        ("nix", "run", "path:.#nixcfg", "--", "ci", "pipeline", "crate2nix"),
    ]


def test_run_stops_at_first_failed_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return the failing gate's status without claiming later gates ran."""
    commands: list[tuple[str, ...]] = []

    def fake_run(command: tuple[str, ...]) -> int:
        commands.append(command)
        return 17

    monkeypatch.setattr(test_pipeline, "_run_command", fake_run)

    assert test_pipeline.run() == 17
    assert commands == [
        ("nix", "build", f"path:.#checks.x86_64-linux.{FAST_QUALITY_CHECKS[0]}")
    ]


def test_run_command_executes_from_repo_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run fixed command tuples from the detected checkout."""
    completed = subprocess.CompletedProcess(("nix", "--version"), 9)
    calls: list[tuple[tuple[str, ...], object, bool]] = []

    def fake_subprocess_run(
        command: tuple[str, ...],
        *,
        cwd: object,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, cwd, check))
        return completed

    monkeypatch.setattr(test_pipeline.subprocess, "run", fake_subprocess_run)

    assert test_pipeline._run_command(("nix", "--version")) == 9
    assert calls == [(("nix", "--version"), test_pipeline.get_repo_root(), False)]


def test_cli_exits_with_run_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """Propagate parity failures through the mounted Typer callback."""
    monkeypatch.setattr(test_pipeline, "run", lambda: 23)

    with pytest.raises(typer.Exit) as exc_info:
        test_pipeline.cli()

    assert exc_info.value.exit_code == 23
