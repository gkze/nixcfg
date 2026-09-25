"""Native candidate preparation and validation using the local updater core."""

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel, ConfigDict

from lib.system_policy import supported_systems
from lib.update import cli as update_cli
from lib.update import derivation_validation as validation
from lib.update.candidate import Candidate, Preparation
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


class ValidationReport(BaseModel):
    """Evidence from one native validator, bound to the exact proposed Git tree."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tree: str
    system: str
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
    targets: tuple[str, ...], *, previous: Candidate | None = None
) -> tuple[Candidate, int]:
    """Extend one pinned candidate on the current system without live writes."""
    system = get_current_nix_platform()
    if system not in supported_systems():
        msg = f"No configured builder policy for {system}"
        raise ValueError(msg)
    preparation = Preparation(system=system, targets=targets, previous=previous)
    options = UpdateOptions(
        targets=targets,
        check=True,
        strict=True,
        no_refs=previous is not None,
        no_input=previous is not None,
        tty="off",
        json=True,
        timings=True,
    )
    result = asyncio.run(
        update_cli.collect_run_outcome(
            options, check_tools=True, preparation=preparation
        )
    )
    status = update_cli.emit_run_result(result, options)
    if preparation.candidate is None:
        msg = "Preparation failed before a candidate could be captured"
        raise RuntimeError(msg)
    return preparation.candidate, status


def require_complete_candidate(candidate: Candidate) -> None:
    """Reject failed or incomplete preparation before issuing validation evidence."""
    if not candidate.prepared or (
        len(candidate.systems) != len(set(candidate.systems))
        or set(candidate.systems) != set(supported_systems())
    ):
        msg = "Candidate must finish preparation on every configured system"
        raise ValueError(msg)


def validate_candidate(candidate: Candidate) -> ValidationReport:
    """Validate the assembled tree on this native builder, leaving the repo alone."""
    require_complete_candidate(candidate)
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
        with workspace.validation_snapshot() as snapshot:
            failures = validation.validate_derivations(
                candidate.sources,
                updaters=updaters,
                flake_root=snapshot.root,
                print_build_logs=True,
                all_declared_systems=True,
                native_builds_only=True,
            )
            # Root checks are computed from the same independently verified
            # manifest used by local updates; only native execution is sharded.
            failures += validation.validate_root_closures(
                flake_root=snapshot.root,
                systems=(system,),
                include_dependencies=True,
                print_build_logs=True,
            )
        workspace.validate_changes(allowed)
    return ValidationReport(tree=candidate.tree, system=system, failures=failures)


def certified_patch(candidate: Candidate, reports: list[ValidationReport]) -> bytes:
    """Publish only when each required builder validated this exact candidate."""
    require_complete_candidate(candidate)
    systems = [report.system for report in reports]
    if (
        len(systems) != len(set(systems))
        or set(systems) != set(candidate.systems)
        or any(report.tree != candidate.tree or report.failures for report in reports)
    ):
        msg = "Validation reports are missing, duplicated, failed, or for another tree"
        raise ValueError(msg)
    return candidate.patch


@app.command("prepare")
def prepare(
    targets: Annotated[list[str] | None, typer.Argument()] = None,
    *,
    output: Annotated[
        Path, typer.Option(help="Candidate JSON outside the repository.")
    ],
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
    candidate, status = prepare_candidate(selected, previous=prior)
    atomic_write_text(output, candidate.model_dump_json(indent=2) + "\n")
    raise typer.Exit(status)


@app.command("validate")
def validate(
    candidate: Annotated[Path, typer.Option(help="Final prepared candidate JSON.")],
    output: Annotated[Path, typer.Option(help="Native validation report.")],
) -> None:
    """Run the existing package and root gates on the exact assembled tree."""
    output = _output_path(output, get_repo_root())
    report = validate_candidate(Candidate.model_validate_json(candidate.read_bytes()))
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
