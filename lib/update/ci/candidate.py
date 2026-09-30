"""Native candidate preparation and validation using the local updater core."""

import asyncio
import json
import math
import sys
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Literal

import typer
from pydantic import BaseModel, ConfigDict

from lib.diagnostics import redact_urls
from lib.system_policy import supported_systems
from lib.update import cli as update_cli
from lib.update import derivation_validation as validation
from lib.update.candidate import Candidate, Preparation
from lib.update.ci import jobs
from lib.update.cli_options import RepairAgent, UpdateOptions
from lib.update.io import atomic_write_text
from lib.update.nix import get_current_nix_platform
from lib.update.paths import get_repo_root
from lib.update.persistence import IsolatedUpdateWorkspace, planned_update_paths
from lib.update.repair import propose_repair
from lib.update.updaters import ensure_updaters_loaded

app = typer.Typer(
    help="Prepare portable candidates and validate them on native builders."
)


@app.command("repair")
def repair(
    evidence: Annotated[Path, typer.Option(help="Directory of failure evidence.")],
    output: Annotated[Path, typer.Option(help="Unvalidated repair patch.")],
    agent: Annotated[
        RepairAgent, typer.Option(help="Installed repair agent.")
    ] = RepairAgent.CODEX,
) -> None:
    """Propose one packaging repair in isolation; never promote or certify it."""
    propose_repair(get_repo_root(), evidence=evidence, output=output, agent=agent)


ValidationScope = Literal["all", "packages", "closures"]
ValidationGate = Literal["packages", "closures"]
# Exit status for a closure shard that stopped on its budget with a consistent
# Cachix handoff. jobs.py treats only this status as continuation, and only
# when the workflow asked the shard to yield.
CLOSURE_YIELD_EXIT = 75
# Graph discovery is minutes, not the build. Keep it inside the job cap so a
# hung eval fails this shard instead of consuming the build budget.
_CLOSURE_DISCOVERY_TIMEOUT_SECONDS = 45 * 60
# Five hours of nix build, inside the 360-minute hosted job, leaves time for
# image cleanup, discovery, and the Cachix daemon flush before GitHub cancels.
HOSTED_DARWIN_CLOSURE_BUILD_BUDGET_SECONDS = 5 * 60 * 60
HOSTED_DARWIN_CLOSURE_SHARDS = 4


class ValidationReport(BaseModel):
    """Evidence from one native validator, bound to the exact proposed Git tree."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tree: str
    system: str
    validate_all_packages: bool = False
    gates: tuple[ValidationGate, ...] = ("packages", "closures")
    failures: tuple[validation.DerivationValidationFailure, ...]


def _output_path(output: Path, root: Path) -> Path:
    """Keep job artifacts outside the source snapshot they describe."""
    output = output.resolve()
    if output.is_relative_to(root.resolve()):
        msg = "Candidate output must be outside the repository"
        raise ValueError(msg)
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def prepare_candidate(
    targets: tuple[str, ...],
    *,
    previous: Candidate | None = None,
    validate_all_packages: bool = False,
) -> tuple[Candidate, int]:
    """Extend one pinned candidate on the current system without live writes."""
    system = get_current_nix_platform()
    if system not in supported_systems():
        msg = f"No configured builder policy for {system}"
        raise ValueError(msg)
    preparation = Preparation(
        system=system,
        targets=targets,
        previous=previous,
        validate_all_packages=validate_all_packages,
    )
    options = UpdateOptions(
        targets=targets,
        check=True,
        strict=True,
        no_refs=previous is not None,
        no_input=previous is not None,
        tty="off",
        timings=True,
    )
    # This synchronous CLI boundary owns stdout: progress goes to the live job
    # log, while stdout remains one machine-readable result.
    with redirect_stdout(sys.stderr):
        result = asyncio.run(
            update_cli.collect_run_outcome(
                options, check_tools=True, preparation=preparation
            )
        )
    status = update_cli.emit_run_result(result, replace(options, json=True))
    if preparation.candidate is None:
        msg = "Preparation failed before a candidate could be captured"
        raise RuntimeError(msg)
    return preparation.candidate, status


def _hosted_validation_progress(source: str) -> validation.ValidationProgress:
    """Stream Nix validation output to the live hosted job log."""

    def emit(event: validation.ValidationProgressEvent) -> None:
        if isinstance(event, validation.ValidationCommandFinished):
            return
        if isinstance(event, validation.ValidationCommandStarted):
            text = f"$ {event.command}"
        elif isinstance(event, validation.ValidationCommandOutput):
            text = event.line.replace("\r", "")
        else:
            text = event.replace("\r", "")
        if not text:
            return
        sys.stderr.write(f"[{source}] {redact_urls(text)}\n")
        sys.stderr.flush()

    return emit


def require_complete_candidate(candidate: Candidate) -> None:
    """Reject failed or incomplete preparation before issuing validation evidence."""
    if not candidate.prepared or (
        len(candidate.systems) != len(set(candidate.systems))
        or set(candidate.systems) != set(supported_systems())
    ):
        msg = "Candidate must finish preparation on every configured system"
        raise ValueError(msg)


def _gates_for_scope(scope: str) -> tuple[ValidationGate, ...]:
    """Return the evidence a scope must produce."""
    if scope == "packages":
        return ("packages",)
    if scope == "closures":
        return ("closures",)
    if scope == "all":
        return ("packages", "closures")
    msg = f"Unknown validation scope: {scope}"
    raise ValueError(msg)


def _require_closure_budget(seconds: float | None) -> float | None:
    """Reject a budget that cannot bound one hosted closure shard."""
    if seconds is None:
        return None
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        msg = "Closure budget must be a positive number of seconds"
        raise TypeError(msg)
    if not math.isfinite(seconds) or seconds <= 0:
        msg = "Closure budget must be a positive number of seconds"
        raise ValueError(msg)
    return float(seconds)


def validate_candidate(
    candidate: Candidate,
    *,
    scope: ValidationScope = "all",
    closure_budget_seconds: float | None = None,
) -> ValidationReport:
    """Validate the assembled tree on this native builder, leaving the repo alone.

    ``packages`` and ``closures`` split one platform across runners. A combined
    ``all`` scope is what Linux validators and local updates use. Closure shards
    do not GC: each starts from an empty store and reuses paths the previous
    shard pushed to Cachix. The combined scope still GCs on hosted Darwin
    because package outputs and the closure fetch share one disk.
    """
    require_complete_candidate(candidate)
    gates = _gates_for_scope(scope)
    budget = _require_closure_budget(closure_budget_seconds)
    system = get_current_nix_platform()
    if system not in candidate.systems:
        msg = f"Unexpected validation platform: {system}"
        raise ValueError(msg)
    updaters = ensure_updaters_loaded()
    with IsolatedUpdateWorkspace(get_repo_root()) as workspace:
        candidate.apply(workspace.root)
        allowed = (
            *(
                path.relative_to(workspace.root)
                for path in planned_update_paths(list(candidate.sources), updaters)
            ),
            Path("flake.nix"),
            Path("flake.lock"),
        )
        workspace.validate_changes(allowed)
        failures: tuple[validation.DerivationValidationFailure, ...] = ()
        with workspace.validation_snapshot() as snapshot:
            if "packages" in gates:
                failures += validation.validate_derivations(
                    None if candidate.validate_all_packages else candidate.sources,
                    updaters=updaters,
                    flake_root=snapshot.root,
                    print_build_logs=True,
                    all_declared_systems=True,
                    native_builds_only=True,
                    progress=_hosted_validation_progress("derivations"),
                )
            if "closures" in gates:
                if scope == "all":
                    # Hosted macos-15 root-closures can fetch tens of GiB after
                    # package validation has already filled the store. GC first
                    # so the Nix daemon is not killed mid-unpack.
                    jobs.reclaim_hosted_store()
                # Root checks are computed from the same independently verified
                # manifest used by local updates; only native execution is sharded.
                # Hosted macos-15 died mid-build when -L streamed 4000+ derivation
                # logs. Build the closure without those logs so Cachix's daemon can
                # upload every realized path. Command progress still reaches the job log.
                closure_progress = _hosted_validation_progress("root-closures")
                if budget is not None:
                    closure_progress(
                        f"Root-closure build budget is {budget:.0f}s; "
                        "realized paths stay in the gkze cache for the next shard"
                    )
                failures += validation.validate_root_closures(
                    flake_root=snapshot.root,
                    systems=(system,),
                    include_dependencies=True,
                    print_build_logs=not jobs.is_hosted_darwin_runner(),
                    progress=closure_progress,
                    timeout=(
                        _CLOSURE_DISCOVERY_TIMEOUT_SECONDS
                        if budget is not None
                        else None
                    ),
                    build_timeout=budget,
                )
        workspace.validate_changes(allowed)
    return ValidationReport(
        tree=candidate.tree,
        system=system,
        gates=gates,
        failures=failures,
        validate_all_packages=candidate.validate_all_packages,
    )


def certified_patch(candidate: Candidate, reports: list[ValidationReport]) -> bytes:
    """Publish only when each required builder validated this exact candidate.

    One system may report package and closure evidence from different runners.
    The union has to cover both gates once, for this tree, with no failures.
    """
    require_complete_candidate(candidate)

    def reject() -> None:
        msg = (
            "Validation reports are missing, duplicated, failed, "
            "or for another tree or validation scope"
        )
        raise ValueError(msg)

    grouped: dict[str, list[ValidationReport]] = {}
    for report in reports:
        grouped.setdefault(report.system, []).append(report)
    if set(grouped) != set(candidate.systems):
        reject()
    for group in grouped.values():
        covered: set[str] = set()
        for report in group:
            if (
                report.tree != candidate.tree
                or report.validate_all_packages != candidate.validate_all_packages
                or report.failures
            ):
                reject()
            for gate in report.gates:
                if gate in covered:
                    reject()
                covered.add(gate)
        if covered != {"packages", "closures"}:
            reject()
    return candidate.patch


@app.command("prepare")
def prepare(
    targets: Annotated[list[str] | None, typer.Argument()] = None,
    *,
    output: Annotated[
        Path, typer.Option(help="Candidate JSON outside the repository.")
    ],
    validate_all_packages: Annotated[
        bool,
        typer.Option(
            help="Validate every package declaration after repair; keep update targets."
        ),
    ] = False,
    previous: Annotated[
        Path | None, typer.Option(help="Previous native candidate.")
    ] = None,
) -> None:
    """Resolve and materialize updates on this runner's native platform."""
    output = _output_path(output, get_repo_root())
    prior = (
        None
        if previous is None
        else Candidate.model_validate_json(previous.read_bytes())
    )
    selected = tuple(targets or ())
    if prior is not None and not selected:
        selected = prior.targets
    candidate, status = prepare_candidate(
        selected, previous=prior, validate_all_packages=validate_all_packages
    )
    atomic_write_text(output, candidate.model_dump_json(indent=2) + "\n")
    raise typer.Exit(status)


def _closure_build_budget_exhausted(
    error: validation.ValidationIncompleteError,
) -> bool:
    """Return whether the root-closure realization itself ran out of budget."""
    text = str(error)
    return "timed out" in text and "nix build" in text


@app.command("validate")
def validate(
    candidate: Annotated[Path, typer.Option(help="Final prepared candidate JSON.")],
    output: Annotated[Path, typer.Option(help="Native validation report.")],
    scope: Annotated[
        ValidationScope,
        typer.Option(help="Package gate, closure gate, or both."),
    ] = "all",
    closure_budget_seconds: Annotated[
        float | None,
        typer.Option(help="Stop the root-closure build after this many seconds."),
    ] = None,
    *,
    closure_yield: Annotated[
        bool,
        typer.Option(
            help="Exit with the continuation status when the closure budget is exhausted."
        ),
    ] = False,
) -> None:
    """Run the existing package and root gates on the exact assembled tree."""
    if closure_yield and (scope != "closures" or closure_budget_seconds is None):
        msg = "Closure yield requires a closure scope and budget"
        raise ValueError(msg)
    output = _output_path(output, get_repo_root())
    try:
        report = validate_candidate(
            Candidate.model_validate_json(candidate.read_bytes()),
            scope=scope,
            closure_budget_seconds=closure_budget_seconds,
        )
    except validation.ValidationIncompleteError as error:
        if closure_yield and _closure_build_budget_exhausted(error):
            raise typer.Exit(CLOSURE_YIELD_EXIT) from error
        raise
    atomic_write_text(output, report.model_dump_json(indent=2) + "\n")
    raise typer.Exit(bool(report.failures))


@app.command("certify")
def certify(
    candidate: Annotated[Path, typer.Option(help="Final prepared candidate JSON.")],
    reports: Annotated[
        list[Path], typer.Option("--report", help="One report per system.")
    ],
    output: Annotated[Path, typer.Option(help="Validated binary Git patch.")],
) -> None:
    """Check all native results before exporting a publishable patch."""
    output = _output_path(output, get_repo_root())
    patch = certified_patch(
        Candidate.model_validate_json(candidate.read_bytes()),
        [ValidationReport.model_validate_json(path.read_bytes()) for path in reports],
    )
    output.write_bytes(patch)


@app.command("matrix")
def matrix() -> None:
    """Emit the native validation matrix from the repository's system policy."""
    runners = {
        "aarch64-darwin": "macos-15",
        "aarch64-linux": "ubuntu-24.04-arm",
        "x86_64-linux": "ubuntu-24.04",
    }
    typer.echo(
        json.dumps({
            "include": [
                {"system": system, "runner": runners[system]}
                for system in supported_systems()
            ]
        })
    )
