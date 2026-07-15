"""Target-aware Nix derivation evaluation after updater persistence."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lib.update.nix import get_current_nix_platform
from lib.update.paths import get_repo_root

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class DerivationValidation:
    """One updater-owned derivation installable template to evaluate."""

    installable: str
    systems: tuple[str, ...] | None = None


@dataclass(frozen=True)
class DerivationValidationRequest:
    """One concrete derivation evaluation for an update target."""

    source: str
    installable: str


@dataclass(frozen=True)
class DerivationValidationFailure:
    """A failed derivation evaluation with user-facing diagnostics."""

    source: str
    installable: str
    message: str


type _RunResult = subprocess.CompletedProcess[str]
type _Runner = Callable[..., _RunResult]

_DEADLINE_EXHAUSTED_MESSAGE = "skipped: total derivation validation deadline exhausted"


def resolve_derivation_validations(
    source_names: Iterable[str],
    *,
    updaters: Mapping[str, type[object]],
    all_declared_systems: bool = False,
) -> tuple[DerivationValidationRequest, ...]:
    """Resolve concrete validation requests for selected updater targets."""
    current_system = get_current_nix_platform()
    requests: list[DerivationValidationRequest] = []
    seen: set[tuple[str, str]] = set()

    for source in source_names:
        updater = updaters.get(source)
        if updater is None:
            continue
        get_validations = getattr(updater, "get_derivation_validations", None)
        validations: Sequence[DerivationValidation] = (
            get_validations()
            if callable(get_validations)
            else getattr(updater, "derivation_validations", ())
        )
        for validation in validations:
            systems = (
                validation.systems
                if all_declared_systems and validation.systems is not None
                else (current_system,)
            )
            for system in systems:
                if (
                    not all_declared_systems
                    and validation.systems is not None
                    and system not in validation.systems
                ):
                    continue
                installable = validation.installable.format(
                    name=source,
                    system=system,
                )
                key = (source, installable)
                if key in seen:
                    continue
                seen.add(key)
                requests.append(
                    DerivationValidationRequest(
                        source=source,
                        installable=installable,
                    )
                )

    return tuple(requests)


def validate_derivations(
    source_names: Iterable[str],
    *,
    updaters: Mapping[str, type[object]],
    timeout: float | None = None,
    all_declared_systems: bool = False,
    run: _Runner | None = None,
    clock: Callable[[], float] | None = None,
) -> tuple[DerivationValidationFailure, ...]:
    """Evaluate declared derivations and return every failure."""
    runner = subprocess.run if run is None else run
    monotonic = time.monotonic if clock is None else clock
    failures: list[DerivationValidationFailure] = []
    requests = resolve_derivation_validations(
        source_names,
        updaters=updaters,
        all_declared_systems=all_declared_systems,
    )
    deadline = None if timeout is None else monotonic() + timeout
    for index, request in enumerate(requests):
        request_timeout = None
        if deadline is not None:
            request_timeout = deadline - monotonic()
            if request_timeout <= 0:
                failures.extend(
                    DerivationValidationFailure(
                        source=remaining.source,
                        installable=remaining.installable,
                        message=_DEADLINE_EXHAUSTED_MESSAGE,
                    )
                    for remaining in requests[index:]
                )
                break
        args = ["nix", "eval", "--raw", request.installable]
        try:
            result = runner(
                args,
                cwd=get_repo_root(),
                text=True,
                capture_output=True,
                check=False,
                timeout=request_timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            message = str(exc)
        else:
            if result.returncode == 0:
                continue
            message = (
                result.stderr.strip() or result.stdout.strip() or "nix eval failed"
            )
        failures.append(
            DerivationValidationFailure(
                source=request.source,
                installable=request.installable,
                message=message,
            )
        )

    return tuple(failures)


__all__ = [
    "DerivationValidation",
    "DerivationValidationFailure",
    "DerivationValidationRequest",
    "resolve_derivation_validations",
    "validate_derivations",
]
