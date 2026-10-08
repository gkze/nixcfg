"""Native candidate preparation and validation using the local updater core."""

import asyncio
import json
import math
import os
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
from lib.update.ci.coverage import (
    ROOT_OUT_PATHS_NAME,
    assert_update_coverage,
    check_path_in_cachix,
    load_planned_root_out_paths,
    parse_job_results,
    require_required_jobs,
    root_store_paths,
    write_root_out_path_cache,
)
from lib.update.ci.shard_plan import (
    ClosureShardReceipt,
    costs_for_tree,
    eval_root_closure_manifest,
    github_actions_matrix,
    plan_darwin_closure_shards,
    write_github_actions_output,
)
from lib.update.ci.warmup import (
    WARMUP_DRVS_NAME,
    WARMUP_PLAN_NAME,
    export_warmup_drvs,
    import_warmup_drvs,
    load_warmup_plan,
    plan_darwin_warmup,
    realize_warmup_outputs,
    skip_cached_warmup_paths,
    slot_warmup_paths,
    unique_drvs_for_outputs,
    write_warmup_plan,
)
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


ValidationScope = Literal["all", "packages", "closures", "closure-shard", "rust-warmup"]
ValidationGate = Literal["packages", "closures"]
# Graph discovery is minutes, not the build. Keep it inside the job cap so a
# hung eval fails this shard instead of consuming the build budget.
_CLOSURE_DISCOVERY_TIMEOUT_SECONDS = 45 * 60
# Five hours of nix build, inside the 360-minute hosted job, leaves time for
# image cleanup, discovery, and the Cachix daemon flush before GitHub cancels.
HOSTED_DARWIN_CLOSURE_BUILD_BUDGET_SECONDS = 5 * 60 * 60


class ValidationReport(BaseModel):
    """Evidence from one native validator, bound to the exact proposed Git tree."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tree: str
    system: str
    validate_all_packages: bool = False
    gates: tuple[ValidationGate, ...] = ("packages", "closures")
    failures: tuple[validation.DerivationValidationFailure, ...]
    planned_installables: tuple[str, ...] = ()


class RootDependencyCacheReport(BaseModel):
    """Cachix warmup for foreign-root native deps; not publication evidence."""

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
            jobs.record_runner_storage(f"before-{source}")
        elif isinstance(event, validation.ValidationCommandOutput):
            text = event.line.replace("\r", "")
        else:
            text = event.replace("\r", "")
        if not text:
            return
        sys.stderr.write(f"[{source}] {redact_urls(text)}\n")
        sys.stderr.flush()

    return emit


def cache_root_dependencies(candidate: Candidate) -> RootDependencyCacheReport:
    """Realize this platform's native deps of foreign roots for later substitutes.

    Flake references freeze after the first prepare. Later stages only add
    hashes, so the Linux VM image inside Darwin roots can be cached from the
    Darwin-prepare candidate while Linux prepare continues. This is not a
    validation report: certification still requires the final-tree gates.
    """
    if not candidate.prepared:
        msg = "A failed preparation cannot populate the binary cache"
        raise ValueError(msg)
    system = get_current_nix_platform()
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
            failures = validation.validate_root_closures(
                flake_root=snapshot.root,
                systems=(system,),
                include_dependencies=True,
                dependencies_only=True,
                print_build_logs=True,
                progress=_hosted_validation_progress("root-deps"),
                timeout=_CLOSURE_DISCOVERY_TIMEOUT_SECONDS,
            )
        workspace.validate_changes(allowed)
    return RootDependencyCacheReport(
        tree=candidate.tree,
        system=system,
        failures=failures,
    )


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
    if scope in {"closure-shard", "rust-warmup"}:
        return ()
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


def _realize_rust_warmup(
    warmup_plan: Path,
    warmup_slot: int,
    *,
    flake_root: Path,
) -> tuple[validation.DerivationValidationFailure, ...]:
    """Realize one rust-warmup stripe, skipping paths already in gkze."""
    plan = load_warmup_plan(warmup_plan)
    missing = skip_cached_warmup_paths(
        slot_warmup_paths(plan.rust_layers, warmup_slot),
        present=check_path_in_cachix,
    )
    drvs = unique_drvs_for_outputs(missing, plan.output_drvs)
    import_warmup_drvs(drvs, warmup_plan.with_name(WARMUP_DRVS_NAME))
    failures = realize_warmup_outputs(
        drvs,
        flake_root=flake_root,
        progress=_hosted_validation_progress("rust-warmup"),
    )
    jobs.record_runner_storage("after-rust-warmup")
    return failures


def validate_candidate(
    candidate: Candidate,
    *,
    scope: ValidationScope = "all",
    closure_budget_seconds: float | None = None,
    closure_roots: tuple[str, ...] | None = None,
    warmup_plan: Path | None = None,
    warmup_slot: int | None = None,
) -> ValidationReport:
    """Validate the assembled tree on this native builder, leaving the repo alone.

    ``packages`` and ``closures`` split one platform across runners. A combined
    ``all`` scope is what Linux validators and local updates use. ``closure-shard``
    realizes named Darwin roots and is not certify evidence; the aggregate
    ``closures`` gate still proves ``root-closures``. ``rust-warmup`` realizes
    one 5-wide stripe of the planner rust_* layers and is not certify
    evidence. Hosted Darwin shards GC before the root-closure fetch: a
    cache-miss rust_* subtree can fill macos-15 and ``min-free`` then GCs
    mid-rustc, which tears rlibs (E0786 / SIGBUS). The combined scope GCs
    after package outputs share the disk.
    """
    require_complete_candidate(candidate)
    if closure_roots is not None and scope != "closure-shard":
        msg = "Named closure roots require the closure-shard scope"
        raise ValueError(msg)
    if scope == "closure-shard" and not closure_roots:
        msg = "closure-shard requires a nonempty root list"
        raise ValueError(msg)
    if scope == "rust-warmup" and (warmup_plan is None or warmup_slot is None):
        msg = "rust-warmup requires a warmup plan and slot"
        raise ValueError(msg)
    if scope != "rust-warmup" and warmup_slot is not None:
        msg = "warmup slots are only valid for the rust-warmup scope"
        raise ValueError(msg)
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
        planned_installables: tuple[str, ...] = ()
        with workspace.validation_snapshot() as snapshot:
            if scope == "rust-warmup" and warmup_plan is not None:
                if warmup_slot is None:
                    msg = "rust-warmup requires a warmup plan and slot"
                    raise ValueError(msg)
                failures += _realize_rust_warmup(
                    warmup_plan, warmup_slot, flake_root=snapshot.root
                )
            if "packages" in gates:
                sources = None if candidate.validate_all_packages else candidate.sources
                planned_installables = tuple(
                    request.installable
                    for request in validation.resolve_derivation_validations(
                        updaters if sources is None else sources,
                        updaters=updaters,
                        all_declared_systems=True,
                        native_builds_only=True,
                    )
                )
                failures += validation.validate_derivations(
                    sources,
                    updaters=updaters,
                    flake_root=snapshot.root,
                    print_build_logs=True,
                    all_declared_systems=True,
                    native_builds_only=True,
                    progress=_hosted_validation_progress("derivations"),
                )
                jobs.record_runner_storage("after-packages")
            if "closures" in gates or scope == "closure-shard":
                # Hosted macos-15 root-closures can fetch tens of GiB.
                # GC first when free is below max-free+min-free so min-free
                # does not fire mid-rustc. Skip when image cleanup already
                # left that headroom; closure shards do not inherit a
                # package store.
                jobs.record_runner_storage(f"before-gc-{scope}")
                jobs.reclaim_hosted_store()
                jobs.record_runner_storage(f"after-gc-{scope}")
                # Root checks come from the independently verified manifest.
                # Hosted macos-15 died mid-build when -L streamed 4000+
                # derivation logs. Build without those logs so Cachix can
                # upload every realized path.
                closure_progress = _hosted_validation_progress("root-closures")
                if budget is not None:
                    closure_progress(
                        f"Root-closure build budget is {budget:.0f}s; "
                        "realized paths stay in the gkze cache"
                    )
                failures += validation.validate_root_closures(
                    flake_root=snapshot.root,
                    systems=(system,),
                    root_names=closure_roots,
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
                jobs.record_runner_storage(f"after-closures-{scope}")
        workspace.validate_changes(allowed)
    return ValidationReport(
        tree=candidate.tree,
        system=system,
        gates=gates,
        failures=failures,
        validate_all_packages=candidate.validate_all_packages,
        planned_installables=planned_installables,
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


@app.command("cache-root-deps")
def cache_root_deps(
    candidate: Annotated[Path, typer.Option(help="Prepared candidate JSON.")],
    output: Annotated[Path, typer.Option(help="Foreign-root dependency receipt.")],
) -> None:
    """Cache this platform's native deps of foreign roots; do not certify."""
    output = _output_path(output, get_repo_root())
    report = cache_root_dependencies(
        Candidate.model_validate_json(candidate.read_bytes())
    )
    atomic_write_text(output, report.model_dump_json(indent=2) + "\n")
    raise typer.Exit(bool(report.failures))


@app.command("validate")
def validate(
    candidate: Annotated[Path, typer.Option(help="Final prepared candidate JSON.")],
    output: Annotated[Path, typer.Option(help="Native validation report.")],
    scope: Annotated[
        ValidationScope,
        typer.Option(help="Package gate, closure gate, shard, or both."),
    ] = "all",
    closure_budget_seconds: Annotated[
        float | None,
        typer.Option(help="Stop the root-closure build after this many seconds."),
    ] = None,
    closure_roots: Annotated[
        str | None,
        typer.Option(help="Space-separated Darwin roots for one always-run shard."),
    ] = None,
    shard: Annotated[
        str | None,
        typer.Option(help="Shard identity recorded on a closure-shard receipt."),
    ] = None,
    warmup_plan: Annotated[
        Path | None,
        typer.Option(help="Planner warmup-plan.json for Darwin rust-warmup."),
    ] = None,
    warmup_slot: Annotated[
        int | None,
        typer.Option(help="rust-warmup matrix slot 0-4."),
    ] = None,
) -> None:
    """Run the existing package and root gates on the exact assembled tree."""
    if scope == "closure-shard" and (not closure_roots or not shard):
        msg = "closure-shard requires --closure-roots and --shard"
        raise ValueError(msg)
    if scope != "closure-shard" and (closure_roots or shard):
        msg = "--closure-roots and --shard are only valid for closure-shard"
        raise ValueError(msg)
    if scope == "rust-warmup" and (warmup_plan is None or warmup_slot is None):
        msg = "rust-warmup requires --warmup-plan and --warmup-slot"
        raise ValueError(msg)
    if scope != "rust-warmup" and warmup_slot is not None:
        msg = "--warmup-slot is only valid for rust-warmup"
        raise ValueError(msg)
    output = _output_path(output, get_repo_root())
    roots = None if closure_roots is None else tuple(closure_roots.split())
    report = validate_candidate(
        Candidate.model_validate_json(candidate.read_bytes()),
        scope=scope,
        closure_budget_seconds=closure_budget_seconds,
        closure_roots=roots,
        warmup_plan=warmup_plan,
        warmup_slot=warmup_slot,
    )
    if scope == "closure-shard":
        receipt = ClosureShardReceipt(
            tree=report.tree,
            system=report.system,
            shard=shard or "",
            roots=roots or (),
            failures=tuple(failure.message for failure in report.failures),
        )
        atomic_write_text(output, receipt.model_dump_json(indent=2) + "\n")
    else:
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


@app.command("plan-shards")
def plan_shards(
    candidate: Annotated[Path, typer.Option(help="Final prepared candidate JSON.")],
    output: Annotated[Path, typer.Option(help="Generated Darwin shard matrix JSON.")],
    github_output: Annotated[
        Path | None,
        typer.Option(help="Optional Actions GITHUB_OUTPUT path."),
    ] = None,
) -> None:
    """Plan always-run Darwin closure shards and cache evaluated root out paths."""
    prepared = Candidate.model_validate_json(candidate.read_bytes())
    require_complete_candidate(prepared)
    updaters = ensure_updaters_loaded()
    output = _output_path(output, get_repo_root())
    with IsolatedUpdateWorkspace(get_repo_root()) as workspace:
        prepared.apply(workspace.root)
        allowed = (
            *(
                path.relative_to(workspace.root)
                for path in planned_update_paths(list(prepared.sources), updaters)
            ),
            Path("flake.nix"),
            Path("flake.lock"),
        )
        workspace.validate_changes(allowed)
        with workspace.validation_snapshot() as snapshot:
            costs = costs_for_tree(snapshot.root)
            manifest = eval_root_closure_manifest(snapshot.root)
            shards = plan_darwin_closure_shards(manifest, costs=costs)
            paths = root_store_paths(snapshot.root, manifest)
            warmup = plan_darwin_warmup(snapshot.root, manifest=manifest, shards=shards)
    write_root_out_path_cache(
        output.with_name(ROOT_OUT_PATHS_NAME),
        tree=prepared.tree,
        root_paths=paths,
        manifest=manifest,
    )
    write_warmup_plan(output.with_name(WARMUP_PLAN_NAME), warmup)
    export_warmup_drvs(
        tuple(warmup.output_drvs.values()), output.with_name(WARMUP_DRVS_NAME)
    )
    matrix = github_actions_matrix(shards)
    atomic_write_text(output, json.dumps(matrix, indent=2) + "\n")
    target = github_output
    if target is None:
        raw = os.environ.get("GITHUB_OUTPUT")
        target = Path(raw) if raw else None
    if target is not None:
        write_github_actions_output(shards, target)


@app.command("assert-coverage")
def assert_coverage(
    candidate: Annotated[Path, typer.Option(help="Final prepared candidate JSON.")],
    evidence: Annotated[Path, typer.Option(help="Downloaded Update artifacts.")],
    job_results: Annotated[
        str,
        typer.Option(help="name=result lines for every required job."),
    ],
) -> None:
    """Fail unless every planned root and package is present in Cachix.

    Required-job results are checked first so a failed or cancelled shard
    farm exits in seconds. Root out paths come from the planner cache;
    this command never re-evaluates them.
    """
    prepared = Candidate.model_validate_json(candidate.read_bytes())
    require_complete_candidate(prepared)
    results = parse_job_results(job_results)
    require_required_jobs(results)
    cache = load_planned_root_out_paths(evidence, tree=prepared.tree)
    updaters = ensure_updaters_loaded()
    with IsolatedUpdateWorkspace(get_repo_root()) as workspace:
        prepared.apply(workspace.root)
        allowed = (
            *(
                path.relative_to(workspace.root)
                for path in planned_update_paths(list(prepared.sources), updaters)
            ),
            Path("flake.nix"),
            Path("flake.lock"),
        )
        workspace.validate_changes(allowed)
        with workspace.validation_snapshot() as snapshot:
            costs = costs_for_tree(snapshot.root)
            package_expected = {
                system: frozenset(
                    request.installable
                    for request in validation.resolve_derivation_validations(
                        updaters
                        if prepared.validate_all_packages
                        else prepared.sources,
                        updaters=updaters,
                        all_declared_systems=True,
                        native_builds_only=True,
                        native_system=system,
                    )
                )
                for system in prepared.systems
            }
    assert_update_coverage(
        job_results=results,
        evidence=evidence,
        tree=prepared.tree,
        validate_all_packages=prepared.validate_all_packages,
        manifest=cache.manifest,
        package_expected=package_expected,
        root_paths=cache.root_paths,
        cachix_present=check_path_in_cachix,
        costs=costs,
    )
