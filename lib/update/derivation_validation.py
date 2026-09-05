"""Target-aware Nix derivation validation after updater persistence."""

import subprocess
import time
from contextlib import nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from lib.system_policy import RootClosureKind, required_root_kinds
from lib.update import persistence as update_persistence
from lib.update.nix import (
    get_current_nix_platform,
    is_retryable_nix_network_failure,
)
from lib.update.paths import get_repo_root

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from pathlib import Path


type DerivationValidationMode = Literal["eval", "build"]


@dataclass(frozen=True)
class DerivationValidation:
    """One updater-owned derivation installable template to validate."""

    installable: str
    systems: tuple[str, ...] | None = None
    mode: DerivationValidationMode = "eval"


@dataclass(frozen=True)
class DerivationValidationRequest:
    """One concrete derivation validation for an update target."""

    source: str
    installable: str
    mode: DerivationValidationMode = "eval"


@dataclass(frozen=True)
class DerivationValidationFailure:
    """A failed derivation validation with user-facing diagnostics."""

    source: str
    installable: str
    message: str


type _RunResult = subprocess.CompletedProcess[str]
type _Runner = Callable[..., _RunResult]
type _Sleeper = Callable[[float], None]

_ROOT_CLOSURE_VALIDATION_SOURCE = "root-closures"
_ROOT_CLOSURE_MANIFEST_INSTALLABLE = "path:.#lib.rootClosureManifest"
ROOT_CLOSURE_VALIDATION_TIMEOUT_SECONDS = 6 * 60 * 60
_VALIDATION_MAX_ATTEMPTS = 3
_VALIDATION_RETRY_BACKOFF_SECONDS = 1.0


class _RootClosureIdentity(BaseModel):
    """Identity constraints shared by required and configured root records."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: RootClosureKind
    name: str = Field(min_length=1)


class RootClosureManifestRoot(_RootClosureIdentity):
    """One configured root described by the candidate flake."""

    system: str = Field(min_length=1)


class RootClosureManifestIdentity(_RootClosureIdentity):
    """One source-discovered root that must remain configured."""


class RootClosureManifest(BaseModel):
    """Versioned candidate-flake protocol for root closure validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[2] = Field(alias="schemaVersion")
    required_kinds: tuple[RootClosureKind, ...] = Field(alias="requiredKinds")
    required_roots: tuple[RootClosureManifestIdentity, ...] = Field(
        alias="requiredRoots",
    )
    roots: tuple[RootClosureManifestRoot, ...]

    @model_validator(mode="after")
    def require_declared_root_kinds(self) -> RootClosureManifest:
        """Reject a manifest whose required root categories disappeared."""
        expected = required_root_kinds()
        if self.required_kinds != expected:
            msg = (
                "requiredKinds does not match system policy: "
                f"expected {expected}, got {self.required_kinds}"
            )
            raise ValueError(msg)
        configured = {root.kind for root in self.roots}
        if missing := tuple(
            kind for kind in self.required_kinds if kind not in configured
        ):
            msg = f"required root kinds have no configured roots: {', '.join(missing)}"
            raise ValueError(msg)
        configured_roots = {(root.kind, root.name) for root in self.roots}
        if missing_roots := tuple(
            root
            for root in self.required_roots
            if (root.kind, root.name) not in configured_roots
        ):
            rendered = ", ".join(f"{root.kind}:{root.name}" for root in missing_roots)
            msg = f"required root closures are not configured: {rendered}"
            raise ValueError(msg)
        return self


class _RootClosureManifestError(RuntimeError):
    """The candidate flake could not provide a valid root manifest."""


def _source_required_roots(
    snapshot_root: Path,
) -> tuple[RootClosureManifestIdentity, ...]:
    """Discover root entrypoints independently of flake output wiring."""

    def _nix_entrypoints(directory: Path) -> tuple[str, ...]:
        if not directory.is_dir():
            return ()
        return tuple(
            sorted(
                entry.stem
                for entry in directory.iterdir()
                if entry.suffix == ".nix" and entry.is_file() and not entry.is_symlink()
            ),
        )

    roots = [
        RootClosureManifestIdentity(kind=kind, name=name)
        for kind in ("darwin", "nixos")
        for name in _nix_entrypoints(snapshot_root / kind)
    ]
    home_root = snapshot_root / "home"
    if home_root.is_dir():
        roots.extend(
            RootClosureManifestIdentity(kind="home", name=entry.name)
            for entry in sorted(home_root.iterdir())
            if entry.is_dir()
            and not entry.is_symlink()
            and (entry / "default.nix").exists()
        )
    return tuple(roots)


def _normalize_local_installable(
    installable: str,
    *,
    flake_root: Path | None,
) -> str:
    """Make local validation include the complete candidate source tree."""
    if installable.startswith(".#"):
        fragment = installable.removeprefix(".#")
    elif flake_root is not None and installable.startswith("path:.#"):
        fragment = installable.removeprefix("path:.#")
    else:
        return installable

    flake_url = "path:." if flake_root is None else f"path:{flake_root}"
    return f"{flake_url}#{fragment}"


def _is_candidate_flake_installable(installable: str) -> bool:
    """Return whether validation targets the mutable local candidate flake."""
    return installable.startswith((".#", "path:", "git+file:"))


def _run_validation_command(
    args: list[str],
    *,
    cwd: Path,
    timeout: float | None,
    run: _Runner,
    sleep: _Sleeper,
) -> _RunResult:
    """Run once for deterministic failures and retry transient Nix I/O failures."""
    for attempt in range(_VALIDATION_MAX_ATTEMPTS):
        result = run(
            args,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
        if (
            result.returncode == 0
            or attempt + 1 == _VALIDATION_MAX_ATTEMPTS
            or not is_retryable_nix_network_failure(
                stdout=result.stdout,
                stderr=result.stderr,
            )
        ):
            return result
        sleep(_VALIDATION_RETRY_BACKOFF_SECONDS * (2**attempt))
    raise AssertionError  # pragma: no cover -- finite loop always returns


def resolve_derivation_validations(
    source_names: Iterable[str],
    *,
    updaters: Mapping[str, type[object]],
    all_declared_systems: bool = False,
) -> tuple[DerivationValidationRequest, ...]:
    """Resolve concrete validation requests for selected updater targets."""
    current_system = get_current_nix_platform()
    requests: list[DerivationValidationRequest] = []
    seen: set[tuple[str, str, DerivationValidationMode]] = set()

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
                key = (source, installable, validation.mode)
                if key in seen:
                    continue
                seen.add(key)
                requests.append(
                    DerivationValidationRequest(
                        source=source,
                        installable=installable,
                        mode=validation.mode,
                    )
                )

    return tuple(requests)


def validate_derivation_requests(
    requests: Iterable[DerivationValidationRequest],
    *,
    timeout: float | None = None,
    run: _Runner | None = None,
    flake_root: Path | None = None,
    sleep: _Sleeper | None = None,
) -> tuple[DerivationValidationFailure, ...]:
    """Validate concrete derivation requests with a timeout for each request."""
    runner = subprocess.run if run is None else run
    sleeper = time.sleep if sleep is None else sleep
    command_root = get_repo_root() if flake_root is None else flake_root
    failures: list[DerivationValidationFailure] = []
    for request in requests:
        candidate_flake = _is_candidate_flake_installable(request.installable)
        installable = _normalize_local_installable(
            request.installable,
            flake_root=flake_root,
        )
        args = (
            [
                "nix",
                "build",
                *(["--no-update-lock-file"] if candidate_flake else []),
                "--no-link",
                installable,
            ]
            if request.mode == "build"
            else [
                "nix",
                "eval",
                *(["--no-update-lock-file"] if candidate_flake else []),
                "--option",
                "allow-import-from-derivation",
                "false",
                "--raw",
                installable,
            ]
        )
        try:
            result = _run_validation_command(
                args,
                cwd=command_root,
                timeout=timeout,
                run=runner,
                sleep=sleeper,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            message = str(exc)
        else:
            if result.returncode == 0:
                continue
            message = (
                result.stderr.strip()
                or result.stdout.strip()
                or f"nix {request.mode} failed"
            )
        failures.append(
            DerivationValidationFailure(
                source=request.source,
                installable=request.installable,
                message=message,
            )
        )

    return tuple(failures)


def validate_derivations(
    source_names: Iterable[str],
    *,
    updaters: Mapping[str, type[object]],
    timeout: float | None = None,
    all_declared_systems: bool = False,
    run: _Runner | None = None,
    sleep: _Sleeper | None = None,
) -> tuple[DerivationValidationFailure, ...]:
    """Validate updater-declared derivations."""
    requests = resolve_derivation_validations(
        source_names,
        updaters=updaters,
        all_declared_systems=all_declared_systems,
    )
    return validate_derivation_requests(
        requests,
        timeout=timeout,
        run=run,
        sleep=sleep,
    )


def _load_root_closure_manifest(
    snapshot_root: Path,
    *,
    timeout: float,
    run: _Runner,
    sleep: _Sleeper,
) -> RootClosureManifest:
    installable = _normalize_local_installable(
        _ROOT_CLOSURE_MANIFEST_INSTALLABLE,
        flake_root=snapshot_root,
    )
    args = ["nix", "eval", "--no-update-lock-file", "--json", installable]
    try:
        result = _run_validation_command(
            args,
            cwd=snapshot_root,
            timeout=timeout,
            run=run,
            sleep=sleep,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _RootClosureManifestError(str(exc)) from exc
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "nix eval failed"
        raise _RootClosureManifestError(message)
    try:
        manifest = RootClosureManifest.model_validate_json(result.stdout)
    except ValidationError as exc:
        msg = f"invalid root closure manifest: {exc}"
        raise _RootClosureManifestError(msg) from exc
    expected_roots = _source_required_roots(snapshot_root)
    if manifest.required_roots != expected_roots:
        msg = (
            "invalid root closure manifest: requiredRoots does not match "
            f"source entrypoints: expected {expected_roots}, "
            f"got {manifest.required_roots}"
        )
        raise _RootClosureManifestError(msg)
    return manifest


def validate_root_closures(
    *,
    flake_root: Path | None = None,
    timeout: float | None = None,
    run: _Runner | None = None,
    sleep: _Sleeper | None = None,
) -> tuple[DerivationValidationFailure, ...]:
    """Build roots with a six-hour default or the caller's per-process bound."""
    runner = subprocess.run if run is None else run
    sleeper = time.sleep if sleep is None else sleep
    root_timeout = (
        ROOT_CLOSURE_VALIDATION_TIMEOUT_SECONDS if timeout is None else timeout
    )
    snapshot = (
        update_persistence.visible_source_snapshot(get_repo_root())
        if flake_root is None
        else nullcontext(flake_root)
    )
    with snapshot as snapshot_root:
        try:
            manifest = _load_root_closure_manifest(
                snapshot_root,
                timeout=root_timeout,
                run=runner,
                sleep=sleeper,
            )
        except _RootClosureManifestError as exc:
            return (
                DerivationValidationFailure(
                    source=_ROOT_CLOSURE_VALIDATION_SOURCE,
                    installable=_ROOT_CLOSURE_MANIFEST_INSTALLABLE,
                    message=str(exc),
                ),
            )

        root_systems = tuple(dict.fromkeys(root.system for root in manifest.roots))
        requests = tuple(
            DerivationValidationRequest(
                source=_ROOT_CLOSURE_VALIDATION_SOURCE,
                installable=f"path:.#checks.{system}.root-closures",
                mode="build",
            )
            for system in root_systems
        )
        return validate_derivation_requests(
            requests,
            timeout=root_timeout,
            run=runner,
            flake_root=snapshot_root,
            sleep=sleeper,
        )


__all__ = [
    "ROOT_CLOSURE_VALIDATION_TIMEOUT_SECONDS",
    "DerivationValidation",
    "DerivationValidationFailure",
    "DerivationValidationMode",
    "DerivationValidationRequest",
    "RootClosureManifest",
    "RootClosureManifestIdentity",
    "RootClosureManifestRoot",
    "resolve_derivation_validations",
    "validate_derivation_requests",
    "validate_derivations",
    "validate_root_closures",
]
