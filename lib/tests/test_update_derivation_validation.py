"""Behavioral tests for target-aware update derivation evaluation."""

from __future__ import annotations

import subprocess
from io import StringIO

import pytest

from lib.update import derivation_validation as validation
from lib.update.ci.workflow_core import cmd_validate_update_derivations
from lib.update.derivation_validation import (
    DerivationValidation,
    DerivationValidationFailure,
    DerivationValidationRequest,
)
from lib.update.paths import get_repo_root
from lib.update.updaters import Crate2NixArtifactsMixin, Updater


class _DarwinAndLinuxUpdater:
    derivation_validations = (
        DerivationValidation(
            installable=".#pkgs.{system}.{name}.drvPath",
            systems=("aarch64-darwin", "x86_64-linux"),
        ),
    )


class _LinuxOnlyUpdater:
    derivation_validations = (
        DerivationValidation(
            installable=".#pkgs.{system}.{name}.drvPath",
            systems=("x86_64-linux",),
        ),
    )


class _PortableUpdater:
    derivation_validations = (DerivationValidation(installable=".#portable.drvPath"),)


class _DuplicateUpdater:
    derivation_validations = (
        DerivationValidation(
            installable=".#duplicate.drvPath",
            systems=("aarch64-darwin", "x86_64-linux"),
        ),
    )


class _NoValidationUpdater:
    pass


class _GooseCrate2NixUpdater(Crate2NixArtifactsMixin):
    name = "goose-cli"


class _UnknownCrate2NixUpdater(Crate2NixArtifactsMixin):
    name = "unknown"


def test_resolve_derivation_validations_honors_target_platforms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve only current-system checks locally and every declared check in CI."""
    monkeypatch.setattr(
        validation,
        "get_current_nix_platform",
        lambda: "aarch64-darwin",
    )
    updaters = {
        "demo": _DarwinAndLinuxUpdater,
        "linux-only": _LinuxOnlyUpdater,
        "portable": _PortableUpdater,
        "duplicate": _DuplicateUpdater,
        "plain": _NoValidationUpdater,
        "goose-cli": _GooseCrate2NixUpdater,
    }
    selected = [
        "missing",
        "demo",
        "linux-only",
        "portable",
        "duplicate",
        "plain",
        "goose-cli",
    ]

    assert validation.resolve_derivation_validations(
        selected,
        updaters=updaters,
    ) == (
        DerivationValidationRequest(
            source="demo",
            installable=".#pkgs.aarch64-darwin.demo.drvPath",
        ),
        DerivationValidationRequest(
            source="portable",
            installable=".#portable.drvPath",
        ),
        DerivationValidationRequest(
            source="duplicate",
            installable=".#duplicate.drvPath",
        ),
        DerivationValidationRequest(
            source="goose-cli",
            installable=".#pkgs.aarch64-darwin.goose-cli.drvPath",
        ),
    )
    assert validation.resolve_derivation_validations(
        selected,
        updaters=updaters,
        all_declared_systems=True,
    ) == (
        DerivationValidationRequest(
            source="demo",
            installable=".#pkgs.aarch64-darwin.demo.drvPath",
        ),
        DerivationValidationRequest(
            source="demo",
            installable=".#pkgs.x86_64-linux.demo.drvPath",
        ),
        DerivationValidationRequest(
            source="linux-only",
            installable=".#pkgs.x86_64-linux.linux-only.drvPath",
        ),
        DerivationValidationRequest(
            source="portable",
            installable=".#portable.drvPath",
        ),
        DerivationValidationRequest(
            source="duplicate",
            installable=".#duplicate.drvPath",
        ),
        DerivationValidationRequest(
            source="goose-cli",
            installable=".#pkgs.aarch64-darwin.goose-cli.drvPath",
        ),
        DerivationValidationRequest(
            source="goose-cli",
            installable=".#pkgs.x86_64-linux.goose-cli.drvPath",
        ),
    )


def test_updater_validation_metadata_defaults_and_unknown_crate2nix_target() -> None:
    """Keep validation opt-in and skip unregistered crate2nix mixin users."""
    assert Updater.get_derivation_validations() == ()
    assert _UnknownCrate2NixUpdater.get_derivation_validations() == ()


def test_validate_derivations_runs_nix_eval_from_repo_root() -> None:
    """Evaluate a resolved drvPath without building the package."""
    calls: list[tuple[list[str], dict[str, object]]] = []

    def _run(
        args: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args, 0, stdout="/nix/store/demo.drv", stderr=""
        )

    failures = validation.validate_derivations(
        ["portable"],
        updaters={"portable": _PortableUpdater},
        timeout=42,
        run=_run,
        clock=lambda: 100.0,
    )

    assert failures == ()
    assert calls == [
        (
            ["nix", "eval", "--raw", ".#portable.drvPath"],
            {
                "cwd": get_repo_root(),
                "text": True,
                "capture_output": True,
                "check": False,
                "timeout": 42,
            },
        )
    ]


def test_validate_derivations_uses_one_total_deadline() -> None:
    """Skip every remaining request after the shared validation deadline expires."""

    class _FourSystemUpdater:
        derivation_validations = (
            DerivationValidation(
                installable=".#pkgs.{system}.demo.drvPath",
                systems=("system-a", "system-b", "system-c", "system-d"),
            ),
        )

    ticks = iter((100.0, 100.0, 102.0, 106.0))
    timeouts: list[object] = []

    def _run(
        args: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        timeouts.append(kwargs["timeout"])
        return subprocess.CompletedProcess(args, 0, stdout="/nix/store/demo.drv")

    failures = validation.validate_derivations(
        ["demo"],
        updaters={"demo": _FourSystemUpdater},
        timeout=5,
        all_declared_systems=True,
        run=_run,
        clock=lambda: next(ticks),
    )

    assert timeouts == [5.0, 3.0]
    assert failures == (
        DerivationValidationFailure(
            source="demo",
            installable=".#pkgs.system-c.demo.drvPath",
            message="skipped: total derivation validation deadline exhausted",
        ),
        DerivationValidationFailure(
            source="demo",
            installable=".#pkgs.system-d.demo.drvPath",
            message="skipped: total derivation validation deadline exhausted",
        ),
    )


@pytest.mark.parametrize(
    ("stdout", "stderr", "expected"),
    [
        ("", "stderr details", "stderr details"),
        ("stdout details", "", "stdout details"),
        ("", "", "nix eval failed"),
    ],
)
def test_validate_derivations_reports_failed_command_output(
    stdout: str,
    stderr: str,
    expected: str,
) -> None:
    """Prefer stderr, then stdout, then a stable fallback for failed evals."""

    def _run(
        args: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, stdout=stdout, stderr=stderr)

    assert validation.validate_derivations(
        ["portable"],
        updaters={"portable": _PortableUpdater},
        run=_run,
    ) == (
        DerivationValidationFailure(
            source="portable",
            installable=".#portable.drvPath",
            message=expected,
        ),
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (OSError("nix unavailable"), "nix unavailable"),
        (
            subprocess.TimeoutExpired(["nix", "eval"], 5),
            "timed out after 5 seconds",
        ),
    ],
)
def test_validate_derivations_reports_process_errors(
    error: OSError | subprocess.TimeoutExpired,
    expected: str,
) -> None:
    """Convert process startup and timeout errors into target failures."""

    def _run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise error

    failures = validation.validate_derivations(
        ["portable"],
        updaters={"portable": _PortableUpdater},
        run=_run,
    )

    assert len(failures) == 1
    assert failures[0].source == "portable"
    assert expected in failures[0].message


def test_cmd_validate_update_derivations_checks_current_system_and_fails() -> None:
    """Expose runner-native merged-tree validation as a failing command."""
    stderr = StringIO()
    calls: list[tuple[list[str], bool, float]] = []
    failure = DerivationValidationFailure(
        source="demo",
        installable=".#demo.drvPath",
        message="broken graph",
    )

    def _validate(
        names: list[str],
        *,
        updaters: object,
        all_declared_systems: bool,
        timeout: float,
    ) -> tuple[DerivationValidationFailure, ...]:
        _ = updaters
        calls.append((names, all_declared_systems, timeout))
        return (failure,)

    assert (
        cmd_validate_update_derivations(
            validate=_validate,
            updaters={"zeta": _NoValidationUpdater, "demo": _PortableUpdater},
            stderr=stderr,
            timeout=12,
        )
        == 1
    )
    assert calls == [(["demo", "zeta"], False, 12)]
    assert "[demo] Derivation evaluation failed" in stderr.getvalue()
    assert "broken graph" in stderr.getvalue()


def test_cmd_validate_update_derivations_succeeds_without_failures() -> None:
    """Succeed when every declared merged-tree derivation evaluates."""
    assert (
        cmd_validate_update_derivations(
            validate=lambda *_args, **_kwargs: (),
            updaters={"demo": _PortableUpdater},
            stderr=StringIO(),
        )
        == 0
    )
