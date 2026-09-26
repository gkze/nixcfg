"""Target-aware Nix derivation validation after updater persistence."""

import os
import re
import shlex
import subprocess
import tempfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, CancelledError, ThreadPoolExecutor, wait
from contextlib import nullcontext
from dataclasses import dataclass
from io import BufferedRandom
from typing import TYPE_CHECKING, Literal, cast

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.primitive import StringPrimitive
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from lib.diagnostics import redact_urls
from lib.nix.models.derivation import DerivationInputs  # noqa: TC001 -- Pydantic field
from lib.system_policy import RootClosureKind, required_root_kinds
from lib.update import persistence as update_persistence
from lib.update.nix import (
    get_current_nix_platform,
    is_retryable_nix_network_failure,
)
from lib.update.nix_expr import compact_nix_expr, identifier_attr_path
from lib.update.paths import get_repo_root
from lib.update.runtime import measure

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from concurrent.futures import Future
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


class ValidationIncompleteError(RuntimeError):
    """A command could not finish; no target failure or acceptance can be inferred."""


type _RunResult = subprocess.CompletedProcess[str]
type _Runner = Callable[..., _RunResult]
type _Sleeper = Callable[[float], None]
type ValidationCancellationCheck = Callable[[], None]


@dataclass(frozen=True)
class ValidationCommandStarted:
    """One validation command attempt began."""

    command: str


@dataclass(frozen=True)
class ValidationCommandOutput:
    """One raw output line from a specific validation command."""

    command: str
    line: str


@dataclass(frozen=True)
class ValidationCommandFinished:
    """One validation command attempt completed."""

    command: str
    succeeded: bool


type ValidationProgressEvent = (
    str | ValidationCommandStarted | ValidationCommandOutput | ValidationCommandFinished
)
type ValidationProgress = Callable[[ValidationProgressEvent], None]

_ROOT_CLOSURE_VALIDATION_SOURCE = "root-closures"
_ROOT_CLOSURE_MANIFEST_INSTALLABLE = "path:.#lib.rootClosureManifest"
ROOT_CLOSURE_VALIDATION_TIMEOUT_SECONDS = 6 * 60 * 60
_VALIDATION_MAX_ATTEMPTS = 3
_VALIDATION_INDIVIDUAL_THRESHOLD = 4
_VALIDATION_RETRY_BACKOFF_SECONDS = 1.0
_VALIDATION_DIAGNOSTIC_LIMIT = 16 * 1024
_SIMPLE_ATTRIBUTE_PATH = re.compile(
    r"[A-Za-z_][A-Za-z0-9_'-]*(?:\.[A-Za-z_][A-Za-z0-9_'-]*)+"
)


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


class _RootDerivation(BaseModel):
    """Only the dependency boundary is needed from Nix's derivation-v4 schema."""

    version: Literal[4]
    system: str
    inputs: DerivationInputs


class _RootDerivationGraph(BaseModel):
    """Nix owns graph discovery; CI only selects the native boundary outputs."""

    version: Literal[4]
    derivations: dict[str, _RootDerivation]

    @classmethod
    def from_result(cls, result: _RunResult) -> _RootDerivationGraph:
        if result.returncode:
            msg = result.stderr.strip() or "Root graph discovery failed"
            raise ValueError(msg)
        return cls.model_validate_json(result.stdout)

    def dependencies(self, systems: tuple[str, ...]) -> tuple[str, ...]:
        selected = set()
        for parent in self.derivations.values():
            for path in parent.inputs.drvs:
                if path not in self.derivations:
                    msg = f"Incomplete root derivation graph: {path}"
                    raise ValueError(msg)
                child = self.derivations[path]
                if child.system in systems and child.system != parent.system:
                    selected.add(f"/nix/store/{path}^*")
        return tuple(sorted(selected))


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


def _ignore_validation_cancellation() -> None:
    """Allow synchronous callers to validate without an async owner."""


def _stream_validation_output(
    streams: tuple[BufferedRandom, BufferedRandom],
    stopped: threading.Event,
    progress: Callable[[str], None],
) -> None:
    """Read independent file offsets without moving the child writer's position."""
    offsets = [0, 0]
    pending = [b"", b""]

    def drain() -> None:
        for index, stream in enumerate(streams):
            while chunk := os.pread(stream.fileno(), 65536, offsets[index]):
                offsets[index] += len(chunk)
                lines = (pending[index] + chunk).split(b"\n")
                pending[index] = lines.pop()
                for line in lines:
                    progress(line.decode(errors="replace"))

    while not stopped.wait(0.1):
        drain()
    drain()
    for tail in pending:
        if tail:
            progress(tail.decode(errors="replace"))


def _run_with_validation_progress(
    args: list[str],
    *,
    cwd: Path,
    timeout: float | None,
    run: _Runner | None,
    progress: ValidationProgress | None,
    check_cancelled: ValidationCancellationCheck = _ignore_validation_cancellation,
) -> _RunResult:
    """Tee captured output while retaining synchronous ownership of the child."""
    command = shlex.join(args)
    attributed_progress: Callable[[str], None] | None
    if progress is None:
        attributed_progress = None
    else:

        def attributed_progress(line: str) -> None:
            progress(ValidationCommandOutput(command, line))

    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        stopped = threading.Event()
        reader_context = (
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="validation-output")
            if attributed_progress is not None
            else nullcontext()
        )
        try:
            with reader_context as executor:
                reader = (
                    executor.submit(
                        _stream_validation_output,
                        (stdout, stderr),
                        stopped,
                        attributed_progress,
                    )
                    if executor is not None and attributed_progress is not None
                    else None
                )
                try:
                    if run is None:
                        result = _run_owned_validation_process(
                            args,
                            cwd=cwd,
                            stdout=stdout,
                            stderr=stderr,
                            timeout=timeout,
                            reader=reader,
                            check_cancelled=check_cancelled,
                        )
                    else:
                        result = run(
                            args,
                            cwd=cwd,
                            text=True,
                            stdout=stdout,
                            stderr=stderr,
                            check=False,
                            timeout=timeout,
                        )
                        # A runner that returns captured text instead of writing
                        # to the provided streams is still observed and retained.
                        for stream, text in (
                            (stdout, result.stdout),
                            (stderr, result.stderr),
                        ):
                            if isinstance(text, str) and text:
                                stream.write(text.encode())
                                stream.flush()
                finally:
                    # The child has completed or been reaped after an error. Join
                    # before the snapshot/files can be removed.
                    stopped.set()
                    if reader is not None:
                        reader.result()
        except subprocess.TimeoutExpired as exc:
            error = _incomplete_validation_error(
                args,
                str(exc),
                stdout=stdout,
                stderr=stderr,
                fallback_stdout=exc.stdout,
                fallback_stderr=exc.stderr,
            )
        else:
            stdout.seek(0)
            stderr.seek(0)
            return subprocess.CompletedProcess(
                args,
                result.returncode,
                stdout=stdout.read().decode(errors="replace"),
                stderr=stderr.read().decode(errors="replace"),
            )
        # Raise outside the handler so the raw exception is not chained.
        raise error


def _run_owned_validation_process(
    args: list[str],
    *,
    cwd: Path,
    stdout: BufferedRandom,
    stderr: BufferedRandom,
    timeout: float | None,
    reader: Future[None] | None,
    check_cancelled: ValidationCancellationCheck,
) -> _RunResult:
    """Reap even on Ctrl-C, when subprocess.run can defer reaping its child."""
    deadline = None if timeout is None else time.monotonic() + timeout
    check_cancelled()
    with subprocess.Popen(  # noqa: S603 -- argv is supplied by validation
        args, cwd=cwd, stdout=stdout, stderr=stderr
    ) as process:
        try:
            while True:
                check_cancelled()
                if reader is not None and reader.done():
                    reader.result()
                remaining = 0.1 if deadline is None else deadline - time.monotonic()
                try:
                    returncode = process.wait(timeout=min(0.1, max(0, remaining)))
                    check_cancelled()
                    break
                except subprocess.TimeoutExpired:
                    if deadline is not None and time.monotonic() >= deadline:
                        raise subprocess.TimeoutExpired(
                            args, cast("float", timeout)
                        ) from None
        except BaseException:
            process.kill()
            process.wait()
            raise
    return subprocess.CompletedProcess(args, returncode)


def _diagnostic_tail(output: str | bytes | BufferedRandom | None) -> str:
    """Bound retained diagnostics without exposing a severed URL suffix."""
    if isinstance(output, BufferedRandom):
        size = output.seek(0, os.SEEK_END)
        output.seek(max(0, size - _VALIDATION_DIAGNOSTIC_LIMIT - 1))
        output = output.read()
    if not output:
        return ""
    truncated = len(output) > _VALIDATION_DIAGNOSTIC_LIMIT
    tail = output[-_VALIDATION_DIAGNOSTIC_LIMIT:]
    if isinstance(tail, bytes):
        tail = tail.decode(errors="replace")
    if truncated:
        # A partial line may have lost its URL scheme and credential boundary.
        tail = "[earlier output omitted]\n" + tail.partition("\n")[2]
    return redact_urls(tail).rstrip()


def _incomplete_validation_error(
    args: list[str],
    reason: str,
    *,
    stdout: str | bytes | BufferedRandom | None = None,
    stderr: str | bytes | BufferedRandom | None = None,
    fallback_stdout: str | bytes | None = None,
    fallback_stderr: str | bytes | None = None,
) -> ValidationIncompleteError:
    """Own safe diagnostics for incomplete commands across all consumers."""
    message = redact_urls(f"Validation incomplete: {shlex.join(args)}: {reason}")
    for label, output, fallback in (
        ("stdout", stdout, fallback_stdout),
        ("stderr", stderr, fallback_stderr),
    ):
        tail = _diagnostic_tail(output) or _diagnostic_tail(fallback)
        if tail:
            message += f"\n{label}:\n{tail}"
    return ValidationIncompleteError(message)


def _run_validation_command_impl(
    args: list[str],
    *,
    cwd: Path,
    timeout: float | None,
    run: _Runner | None,
    sleep: _Sleeper,
    max_attempts: int = _VALIDATION_MAX_ATTEMPTS,
    progress: ValidationProgress | None = None,
    check_cancelled: ValidationCancellationCheck = _ignore_validation_cancellation,
) -> _RunResult:
    """Run once for deterministic failures and retry transient Nix I/O failures."""
    command = shlex.join(args)
    for attempt in range(max_attempts):
        check_cancelled()
        if progress is not None:
            progress(ValidationCommandStarted(command))
        succeeded = False
        try:
            if run is not None and progress is None:
                result = run(
                    args,
                    cwd=cwd,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=timeout,
                )
            else:
                result = _run_with_validation_progress(
                    args,
                    cwd=cwd,
                    timeout=timeout,
                    run=run,
                    progress=progress,
                    check_cancelled=check_cancelled,
                )
            check_cancelled()
            if result.returncode < 0:
                raise _incomplete_validation_error(
                    args,
                    f"terminated by signal {-result.returncode}",
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
            succeeded = result.returncode == 0
        finally:
            if progress is not None:
                progress(ValidationCommandFinished(command, succeeded))
        if (
            result.returncode == 0
            or attempt + 1 == max_attempts
            or not is_retryable_nix_network_failure(
                stdout=result.stdout,
                stderr=result.stderr,
            )
        ):
            return result
        if progress is not None:
            progress(
                f"Retrying transient Nix failure (attempt {attempt + 2}/{max_attempts})"
            )
        sleep(_VALIDATION_RETRY_BACKOFF_SECONDS * (2**attempt))
    raise AssertionError  # pragma: no cover -- finite loop always returns


def _run_validation_command(
    args: list[str],
    *,
    cwd: Path,
    timeout: float | None,
    run: _Runner | None,
    sleep: _Sleeper,
    max_attempts: int = _VALIDATION_MAX_ATTEMPTS,
    progress: ValidationProgress | None = None,
    check_cancelled: ValidationCancellationCheck = _ignore_validation_cancellation,
) -> _RunResult:
    """Measure validation independently from candidate hash preparation."""
    with measure("validation", args[1]) as timing:
        try:
            result = _run_validation_command_impl(
                args,
                cwd=cwd,
                timeout=timeout,
                run=run,
                sleep=sleep,
                max_attempts=max_attempts,
                progress=progress,
                check_cancelled=check_cancelled,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            error = _incomplete_validation_error(
                args,
                str(exc),
                stdout=exc.stdout
                if isinstance(exc, subprocess.TimeoutExpired)
                else None,
                stderr=exc.stderr
                if isinstance(exc, subprocess.TimeoutExpired)
                else None,
            )
        else:
            timing.stdout_bytes += len((result.stdout or "").encode())
            timing.stderr_bytes += len((result.stderr or "").encode())
            if result.returncode:
                timing.failed += 1
            return result
        # No raw command/error survives in an implicit exception context.
        raise error


def resolve_derivation_validations(
    source_names: Iterable[str],
    *,
    updaters: Mapping[str, type[object]],
    all_declared_systems: bool = False,
    native_builds_only: bool = False,
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
                    native_builds_only
                    and validation.mode == "build"
                    and system != current_system
                ):
                    continue
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


def _validation_args(
    request: DerivationValidationRequest,
    *,
    flake_root: Path | None,
    print_build_logs: bool = False,
) -> list[str]:
    """Keep the individual validation command as the diagnostic fallback."""
    return [
        "nix",
        request.mode,
        *(
            ["--no-update-lock-file"]
            if _is_candidate_flake_installable(request.installable)
            else []
        ),
        *(
            # keep-going lets one batch report every failing derivation instead
            # of stopping at the first, so later isolation rounds are cheap.
            ["--no-link", "--keep-going", *(["-L"] if print_build_logs else [])]
            if request.mode == "build"
            else ["--option", "allow-import-from-derivation", "false", "--raw"]
        ),
        _normalize_local_installable(request.installable, flake_root=flake_root),
    ]


def _batch_key(
    request: DerivationValidationRequest,
    *,
    flake_root: Path | None,
) -> tuple[DerivationValidationMode, str] | None:
    """Only batch explicit local attribute paths from one immutable snapshot."""
    if flake_root is None or not request.installable.startswith((".#", "path:.#")):
        return None
    attributes = request.installable.split("#", 1)[1]
    if not _SIMPLE_ATTRIBUTE_PATH.fullmatch(attributes):
        return None
    if request.mode == "eval" and not attributes.endswith(".drvPath"):
        return None
    return request.mode, attributes.split(".", 1)[0]


def _batch_validation_args(
    requests: Sequence[DerivationValidationRequest],
    *,
    flake_root: Path | None,
    print_build_logs: bool = False,
) -> list[str]:
    args = _validation_args(
        requests[0], flake_root=flake_root, print_build_logs=print_build_logs
    )
    if requests[0].mode == "build":
        return [
            *args,
            *(
                _normalize_local_installable(request.installable, flake_root=flake_root)
                for request in requests[1:]
            ),
        ]
    # Concatenation forces every string just as the individual --raw evals do.
    # Nix retains their contexts, including each instantiated derivation path.
    apply = FunctionDefinition(
        argument_set=Identifier(name="root"),
        output=FunctionCall(
            name=FunctionCall(
                name=identifier_attr_path("builtins", "concatStringsSep"),
                argument=StringPrimitive(value=""),
            ),
            argument=NixList(
                value=[
                    identifier_attr_path(
                        "root", *request.installable.split("#", 1)[1].split(".")[1:]
                    )
                    for request in requests
                ]
            ),
        ),
    )
    flake_url, attribute_path = args[-1].split("#", 1)
    return [
        *args[:-1],
        f"{flake_url}#{attribute_path.split('.', 1)[0]}",
        "--apply",
        compact_nix_expr(apply.rebuild()),
    ]


def validate_derivation_requests(
    requests: Iterable[DerivationValidationRequest],
    *,
    timeout: float | None = None,
    run: _Runner | None = None,
    flake_root: Path | None = None,
    sleep: _Sleeper | None = None,
    progress: ValidationProgress | None = None,
    check_cancelled: ValidationCancellationCheck = _ignore_validation_cancellation,
    print_build_logs: bool = False,
    max_eval_workers: int = 1,
    max_build_workers: int = 1,
) -> tuple[DerivationValidationFailure, ...]:
    """Batch snapshot validations, retaining individual failure diagnostics.

    Completed failed batches isolate sparse failures by subdivision.
    Timeouts, signals and launch errors raise ValidationIncompleteError without attribution.
    If both halves fail, fall back to individual diagnostics and the original retry policy.
    Every requested target remains required. *progress* observes output as it
    streams; *print_build_logs* additionally asks Nix for per-derivation logs.
    Groups are independent read-only commands over one immutable snapshot, so
    eval groups run up to *max_eval_workers* at a time and build groups up to
    *max_build_workers*; one worker keeps the historical sequential behavior.
    """
    runner = run
    sleeper = time.sleep if sleep is None else sleep
    command_root = get_repo_root() if flake_root is None else flake_root
    groups: dict[
        tuple[DerivationValidationMode, str] | int,
        list[tuple[int, DerivationValidationRequest]],
    ] = {}
    for index, request in enumerate(requests):
        key = _batch_key(request, flake_root=flake_root)
        groups.setdefault(index if key is None else key, []).append((index, request))

    failures: dict[int, DerivationValidationFailure] = {}
    progress_lock = threading.Lock()
    cancelled = threading.Event()

    def check_group_cancelled() -> None:
        check_cancelled()
        if cancelled.is_set():
            raise CancelledError

    guarded_progress: ValidationProgress | None
    if progress is None:
        guarded_progress = None
    else:

        def guarded_progress(event: ValidationProgressEvent) -> None:
            with progress_lock:
                progress(event)

    def batch(group: list[tuple[int, DerivationValidationRequest]]) -> bool:
        result = _run_validation_command(
            _batch_validation_args(
                [request for _, request in group],
                flake_root=flake_root,
                print_build_logs=print_build_logs,
            ),
            cwd=command_root,
            timeout=timeout,
            run=runner,
            sleep=sleeper,
            max_attempts=1,
            progress=guarded_progress,
            check_cancelled=check_group_cancelled,
        )
        return result.returncode == 0

    def individually(group: list[tuple[int, DerivationValidationRequest]]) -> None:
        for index, request in group:
            failure = _run_single_validation(
                request,
                command_root=command_root,
                flake_root=flake_root,
                timeout=timeout,
                run=runner,
                sleep=sleeper,
                progress=guarded_progress,
                check_cancelled=check_group_cancelled,
                print_build_logs=print_build_logs,
            )
            if failure is not None:
                failures[index] = failure

    def isolate(group: list[tuple[int, DerivationValidationRequest]]) -> None:
        # Probe both halves once. If neither succeeds, stop subdividing so a
        # systemic failure adds at most two attempts at this boundary.
        # Bisection compensates for Nix's evaluator: per-request failures are
        # not attributable inside one batched eval (missing-attribute errors
        # escape builtins.tryEval on supported Nix), and a single command
        # cannot report which requests failed. lib/tests/
        # test_update_tryeval_contract.py pins that property.
        if len(group) <= _VALIDATION_INDIVIDUAL_THRESHOLD:
            individually(group)
            return
        middle = len(group) // 2
        left, right = group[:middle], group[middle:]
        left_ok, right_ok = batch(left), batch(right)
        if not left_ok and not right_ok:
            individually(group)
        elif not left_ok:
            isolate(left)
        elif not right_ok:
            isolate(right)

    def run_group(group: list[tuple[int, DerivationValidationRequest]]) -> None:
        if len(group) == 1:
            individually(group)
        elif not batch(group):
            if guarded_progress is not None:
                guarded_progress(
                    "Batch validation did not succeed; isolating failing targets"
                )
            isolate(group)

    eval_groups, build_groups = _split_groups_by_mode(groups)
    _execute_groups(
        eval_groups,
        run_group=run_group,
        max_workers=max_eval_workers,
        cancelled=cancelled,
    )
    _execute_groups(
        build_groups,
        run_group=run_group,
        max_workers=max_build_workers,
        cancelled=cancelled,
    )

    return tuple(failures[index] for index in sorted(failures))


def _run_single_validation(
    request: DerivationValidationRequest,
    *,
    command_root: Path,
    flake_root: Path | None,
    timeout: float | None,
    run: _Runner | None,
    sleep: _Sleeper,
    progress: ValidationProgress | None,
    check_cancelled: ValidationCancellationCheck,
    print_build_logs: bool,
) -> DerivationValidationFailure | None:
    """Validate one request individually and return its failure, if any."""
    result = _run_validation_command(
        _validation_args(
            request,
            flake_root=flake_root,
            print_build_logs=print_build_logs,
        ),
        cwd=command_root,
        timeout=timeout,
        run=run,
        sleep=sleep,
        progress=progress,
        check_cancelled=check_cancelled,
    )
    if result.returncode == 0:
        return None
    message = (
        result.stderr.strip() or result.stdout.strip() or f"nix {request.mode} failed"
    )
    return DerivationValidationFailure(
        source=request.source,
        installable=request.installable,
        message=message,
    )


def _split_groups_by_mode(
    groups: Mapping[
        tuple[DerivationValidationMode, str] | int,
        list[tuple[int, DerivationValidationRequest]],
    ],
) -> tuple[
    list[list[tuple[int, DerivationValidationRequest]]],
    list[list[tuple[int, DerivationValidationRequest]]],
]:
    """Partition validation groups into eval-mode and build-mode work."""
    eval_groups: list[list[tuple[int, DerivationValidationRequest]]] = []
    build_groups: list[list[tuple[int, DerivationValidationRequest]]] = []
    for key, group in groups.items():
        mode = key[0] if isinstance(key, tuple) else group[0][1].mode
        (eval_groups if mode == "eval" else build_groups).append(group)
    return eval_groups, build_groups


def _execute_groups(
    groups: Sequence[list[tuple[int, DerivationValidationRequest]]],
    *,
    run_group: Callable[[list[tuple[int, DerivationValidationRequest]]], None],
    max_workers: int,
    cancelled: threading.Event,
) -> None:
    """Run independent groups concurrently up to the caller's budget."""
    if max_workers <= 1 or len(groups) <= 1:
        for group in groups:
            run_group(group)
        return
    with ThreadPoolExecutor(max_workers=min(max_workers, len(groups))) as pool:
        remaining = iter(groups)
        pending = {
            pool.submit(run_group, next(remaining))
            for _ in range(min(max_workers, len(groups)))
        }
        try:
            while pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    future.result()
                for _ in done:
                    group = next(remaining, None)
                    if group is not None:
                        pending.add(pool.submit(run_group, group))
        except BaseException:
            cancelled.set()
            for future in pending:
                future.cancel()
            raise


def validate_derivations(
    source_names: Iterable[str] | None,
    *,
    updaters: Mapping[str, type[object]],
    timeout: float | None = None,
    all_declared_systems: bool = False,
    native_builds_only: bool = False,
    flake_root: Path | None = None,
    run: _Runner | None = None,
    sleep: _Sleeper | None = None,
    progress: ValidationProgress | None = None,
    check_cancelled: ValidationCancellationCheck = _ignore_validation_cancellation,
    print_build_logs: bool = False,
    max_eval_workers: int = 1,
    max_build_workers: int = 1,
) -> tuple[DerivationValidationFailure, ...]:
    """Validate selected declarations, or the complete registry when names are None.

    Validation inventory is independent of update eligibility and bulk holds.
    """
    requests = resolve_derivation_validations(
        updaters if source_names is None else source_names,
        updaters=updaters,
        all_declared_systems=all_declared_systems,
        native_builds_only=native_builds_only,
    )
    return validate_derivation_requests(
        requests,
        flake_root=flake_root,
        timeout=timeout,
        run=run,
        sleep=sleep,
        progress=progress,
        check_cancelled=check_cancelled,
        print_build_logs=print_build_logs,
        max_eval_workers=max_eval_workers,
        max_build_workers=max_build_workers,
    )


def _load_root_closure_manifest(
    snapshot_root: Path,
    *,
    timeout: float,
    run: _Runner | None,
    sleep: _Sleeper,
    progress: ValidationProgress | None = None,
    check_cancelled: ValidationCancellationCheck = _ignore_validation_cancellation,
) -> RootClosureManifest:
    installable = _normalize_local_installable(
        _ROOT_CLOSURE_MANIFEST_INSTALLABLE,
        flake_root=snapshot_root,
    )
    args = ["nix", "eval", "--no-update-lock-file", "--json", installable]
    result = _run_validation_command(
        args,
        cwd=snapshot_root,
        timeout=timeout,
        run=run,
        sleep=sleep,
        progress=progress,
        check_cancelled=check_cancelled,
    )
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
    systems: tuple[str, ...] | None = None,
    include_dependencies: bool = False,
    timeout: float | None = None,
    run: _Runner | None = None,
    sleep: _Sleeper | None = None,
    progress: ValidationProgress | None = None,
    check_cancelled: ValidationCancellationCheck = _ignore_validation_cancellation,
    print_build_logs: bool = False,
) -> tuple[DerivationValidationFailure, ...]:
    """Build roots with a six-hour default or the caller's per-process bound."""
    runner = run
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
                progress=progress,
                check_cancelled=check_cancelled,
            )
        except _RootClosureManifestError as exc:
            return (
                DerivationValidationFailure(
                    source=_ROOT_CLOSURE_VALIDATION_SOURCE,
                    installable=_ROOT_CLOSURE_MANIFEST_INSTALLABLE,
                    message=str(exc),
                ),
            )

        root_systems = tuple(
            dict.fromkeys(
                root.system
                for root in manifest.roots
                if systems is None or root.system in systems
            )
        )
        requests = tuple(
            DerivationValidationRequest(
                source=_ROOT_CLOSURE_VALIDATION_SOURCE,
                installable=f"path:.#checks.{system}.root-closures",
                mode="build",
            )
            for system in root_systems
        )
        if include_dependencies:
            # A Darwin root may contain a Linux VM image. Build the native
            # boundary of every root's graph, even on a runner with no roots.
            # Foreign roots must evaluate without import-from-derivation: a
            # Linux runner cannot realize a Darwin derivation during eval.
            graph_systems = sorted({root.system for root in manifest.roots})
            args = [
                "nix",
                "derivation",
                "show",
                "--recursive",
                "--no-update-lock-file",
                "--option",
                "allow-import-from-derivation",
                "false",
                *(
                    f"path:{snapshot_root}#checks.{system}.root-closures"
                    for system in graph_systems
                ),
            ]
            try:
                result = _run_validation_command(
                    args,
                    cwd=snapshot_root,
                    timeout=root_timeout,
                    run=runner,
                    sleep=sleeper,
                    progress=progress,
                    check_cancelled=check_cancelled,
                )
                graph = _RootDerivationGraph.from_result(result)
                dependencies = graph.dependencies(systems or root_systems)
            except ValueError as exc:
                return (
                    DerivationValidationFailure(
                        source=_ROOT_CLOSURE_VALIDATION_SOURCE,
                        installable=" ".join(
                            f"path:.#checks.{system}.root-closures"
                            for system in graph_systems
                        ),
                        message=str(exc),
                    ),
                )
            requests = (
                tuple(
                    DerivationValidationRequest(
                        source=_ROOT_CLOSURE_VALIDATION_SOURCE,
                        installable=installable,
                        mode="build",
                    )
                    for installable in dependencies
                )
                + requests
            )
        return validate_derivation_requests(
            requests,
            timeout=root_timeout,
            run=runner,
            flake_root=snapshot_root,
            sleep=sleeper,
            progress=progress,
            check_cancelled=check_cancelled,
            print_build_logs=print_build_logs,
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
    "ValidationIncompleteError",
    "resolve_derivation_validations",
    "validate_derivation_requests",
    "validate_derivations",
    "validate_root_closures",
]
