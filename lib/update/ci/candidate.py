"""Native candidate preparation and validation using the local updater core."""

import asyncio
import json
import math
import os
import sys
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Literal, NamedTuple

import typer
from pydantic import BaseModel, ConfigDict, ValidationError

from lib.diagnostics import redact_urls
from lib.nix.models.sources import HashCollection, HashEntryMergeKey, SourceEntry
from lib.system_policy import supported_systems
from lib.update import cli as update_cli
from lib.update import derivation_validation as validation
from lib.update.candidate import Candidate, Preparation, git
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
    EXTENSION_HOST_MEMBER_CRATES,
    SETTINGS_MEMBER_CRATES,
    WARMUP_DRVS_NAME,
    WARMUP_PLAN_NAME,
    WarmupError,
    WarmupPlan,
    assert_force_local_dry_run,
    canary_crate_warmup_paths,
    compiler_input_drvs,
    export_warmup_drvs,
    extension_host_family_warmup_outputs,
    import_warmup_drvs,
    is_extension_host_family_store_path,
    is_rust_extension_host_store_path,
    is_rust_language_models_store_path,
    language_models_input_drvs,
    language_models_warmup_outputs,
    load_warmup_plan,
    parse_canary_crates,
    parse_canary_slots,
    partition_agent_ui_drvs,
    partition_compiler_input_drvs,
    partition_rust_warmup_others,
    partition_rust_zed_drvs,
    partition_settings_family_drvs,
    partition_zed_editor_nightly_drvs,
    plan_darwin_warmup,
    raise_if_warmup_fatal,
    realize_warmup_outputs,
    rust_crate_input_drvs,
    settings_family_warmup_outputs,
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
# Recaching the Rosetta VM after rust-warmup can rebuild from source if gkze
# LRU-evicted it. 36m succeeded on #1257; 45m discovery+build was too tight
# to share. Realization gets its own budget.
_ROOT_DEPS_BUILD_TIMEOUT_SECONDS = 3 * 60 * 60
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


_FAIL_FAST_PROGRESS_SOURCES = frozenset({
    "rust-warmup",
    "root-deps",
    "root-closures",
})


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
        if source in _FAIL_FAST_PROGRESS_SOURCES:
            raise_if_warmup_fatal(text)

    return emit


def cache_root_dependencies(candidate: Candidate) -> RootDependencyCacheReport:
    """Realize this platform's native deps of foreign roots for later substitutes.

    Recache after rust-warmup from the final three-system candidate so gkze
    LRU cannot drop the Rosetta VM image before Darwin roots substitute it.
    This is not a validation report: certification still requires the
    final-tree gates.
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
                build_timeout=_ROOT_DEPS_BUILD_TIMEOUT_SECONDS,
            )
        workspace.validate_changes(allowed)
    return RootDependencyCacheReport(
        tree=candidate.tree,
        system=system,
        failures=failures,
    )


def checkout_candidate(root: Path) -> Candidate:
    """Return an identity candidate for the current checkout.

    Dispatch canary realizes rust_* against the branch tree and the current
    warmup plan. It does not refresh flake inputs or certify.
    """
    tree = git(root, "rev-parse", "HEAD^{tree}").decode().strip()
    return Candidate(
        base_tree=tree,
        tree=tree,
        targets=(),
        sources=(),
        systems=supported_systems(),
        resolutions={},
        prepared=True,
        patch=b"",
    )


class _IndexBlob(NamedTuple):
    """One Git index entry: mode plus blob bytes (symlink target or file)."""

    mode: str
    content: bytes


def _index_files(root: Path) -> dict[str, _IndexBlob]:
    """Return mode-aware blobs of every path in the current Git index."""
    git(root, "add", "--all")
    files: dict[str, _IndexBlob] = {}
    for entry in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not entry:
            continue
        meta, path = entry.split(b"\t", 1)
        mode, _oid, _stage = meta.split(b" ", 2)
        name = path.decode()
        files[name] = _IndexBlob(mode.decode(), git(root, "show", f":{name}"))
    return files


def _candidate_index_files(candidate: Candidate, repo: Path) -> dict[str, _IndexBlob]:
    """Apply *candidate* in isolation and return its indexed files."""
    with IsolatedUpdateWorkspace(repo) as workspace:
        candidate.apply(workspace.root)
        return _index_files(workspace.root)


def _install_index_blob(dest: Path, blob: _IndexBlob) -> None:
    """Write *blob* without following an existing symlink at *dest*.

    ``Path.write_bytes`` follows ``CLAUDE.md`` → ``AGENTS.md`` and would
    replace the guide with the symlink target. Unlink first, then recreate
    the symlink or regular file using the index mode.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)
    if blob.mode == "120000":
        dest.symlink_to(os.fsdecode(blob.content))
        return
    dest.write_bytes(blob.content)
    if blob.mode == "100755":
        dest.chmod(0o755)


def _is_package_sources_path(path: str) -> bool:
    """Return whether *path* is a per-package or flat ``sources.json``."""
    name = path.rsplit("/", 1)[-1]
    return name == "sources.json" or name.endswith(".sources.json")


def _parse_source_entry(data: bytes) -> SourceEntry | None:
    """Parse a bare per-package ``sources.json`` entry, or ``None`` if it is not one."""
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return SourceEntry.model_validate(payload)
    except TypeError, ValueError, ValidationError:
        return None


def _conflicting_optional_text(left: str | None, right: str | None) -> bool:
    """Return whether both sides set a scalar and disagree."""
    return left is not None and right is not None and left != right


def _conflicting_text_mapping(
    left: dict[str, str] | None,
    right: dict[str, str] | None,
) -> bool:
    """Return whether both sides set the same key to different values."""
    if left is None or right is None:
        return False
    return any(key in right and right[key] != value for key, value in left.items())


def _real_entry_hashes(collection: HashCollection) -> dict[HashEntryMergeKey, str]:
    """Return identity → hash for every non-placeholder structured hash."""
    return {
        entry.merge_key(): entry.hash
        for entry in (collection.entries or ())
        if not entry.hash.startswith(HashCollection.FAKE_HASH_PREFIX)
    }


def _real_mapping_hashes(collection: HashCollection) -> dict[str, str]:
    """Return platform → hash for every non-placeholder mapping hash."""
    return {
        platform: hash_val
        for platform, hash_val in (collection.mapping or {}).items()
        if not hash_val.startswith(HashCollection.FAKE_HASH_PREFIX)
    }


def _hashes_preserved(original: HashCollection, merged: HashCollection) -> bool:
    """Return whether every real hash in *original* survived *merged*."""
    merged_entries = _real_entry_hashes(merged)
    merged_mapping = _real_mapping_hashes(merged)
    return all(
        merged_entries.get(key) == hash_val
        for key, hash_val in _real_entry_hashes(original).items()
    ) and all(
        merged_mapping.get(platform) == hash_val
        for platform, hash_val in _real_mapping_hashes(original).items()
    )


def _hash_collections_compatible(left: HashCollection, right: HashCollection) -> bool:
    """Union platform hashes; reject silent last-wins overwrites."""
    try:
        merged = left.merge(right)
    except ValueError:
        return False
    return _hashes_preserved(left, merged) and _hashes_preserved(right, merged)


def _source_entries_compatible(left: SourceEntry, right: SourceEntry) -> bool:
    """Return whether two source entries may be unioned across native prepares.

    Serial prepare last-wins ``drvHash`` when version and artifact hashes agree.
    Parallel Linux prepares both start from Darwin and rewrite that fingerprint,
    so a drvHash-only (or complementary platform-hash) overlap is not a conflict.
    Version, input, commit, URLs, pins, and real artifact hashes still fail closed.
    """
    if any(
        _conflicting_optional_text(getattr(left, name), getattr(right, name))
        for name in ("version", "input", "commit", "electron_version")
    ):
        return False
    return (
        not _conflicting_text_mapping(left.urls, right.urls)
        and not _conflicting_text_mapping(left.pins, right.pins)
        and not _conflicting_text_mapping(
            left.platform_drv_hashes, right.platform_drv_hashes
        )
        and _hash_collections_compatible(left.hashes, right.hashes)
    )


def _merged_package_sources(path: str, first: bytes, second: bytes) -> bytes | None:
    """Semantically merge two ``sources.json`` blobs, or ``None`` to fail closed."""
    if not _is_package_sources_path(path):
        return None
    left = _parse_source_entry(first)
    right = _parse_source_entry(second)
    if left is None or right is None or not _source_entries_compatible(left, right):
        return None
    return (
        json.dumps(left.merge(right).to_dict(), indent=2, sort_keys=True) + "\n"
    ).encode()


def _merged_index_files(
    base: dict[str, _IndexBlob],
    left: dict[str, _IndexBlob],
    right: dict[str, _IndexBlob],
) -> dict[str, _IndexBlob]:
    """3-way merge file maps; fail closed on content conflicts.

    Per-package ``sources.json`` overlaps use :class:`SourceEntry` merge when the
    only disagreements are derivation fingerprints or complementary native
    hashes. Other overlapping edits still fail closed.
    """
    merged = dict(base)
    for path in set(base) | set(left) | set(right):
        ancestor = base.get(path)
        first = left.get(path)
        second = right.get(path)
        if first == second:
            chosen = first
        elif first == ancestor:
            chosen = second
        elif second == ancestor:
            chosen = first
        elif first is None or second is None:
            msg = f"Conflicting candidate edits for {path}"
            raise ValueError(msg)
        else:
            content = _merged_package_sources(path, first.content, second.content)
            if content is None or first.mode != second.mode:
                msg = f"Conflicting candidate edits for {path}"
                raise ValueError(msg)
            chosen = _IndexBlob(first.mode, content)
        if chosen is None:
            merged.pop(path, None)
        else:
            merged[path] = chosen
    return merged


def merge_prepared_candidates(
    left: Candidate,
    right: Candidate,
    *,
    repo: Path,
) -> Candidate:
    """Merge two same-baseline native extensions into one candidate.

    Darwin pins refs and shared generated files. Linux prepare only adds
    native hashes, so arm and x86 can extend Darwin in parallel. Overlapping
    file edits with different contents fail closed, except per-package
    ``sources.json`` files whose only disagreements are ``drvHash`` or
    complementary native hashes.
    """
    if left.base_tree != right.base_tree:
        msg = "Merged candidates must share a baseline tree"
        raise ValueError(msg)
    if left.targets != right.targets:
        msg = "Candidate target selection cannot change between platforms"
        raise ValueError(msg)
    if not left.prepared or not right.prepared:
        msg = "A failed preparation cannot be merged"
        raise ValueError(msg)
    left_extra = set(left.systems) - set(right.systems)
    right_extra = set(right.systems) - set(left.systems)
    if not left_extra or not right_extra:
        msg = "Merge needs disjoint platform extensions"
        raise ValueError(msg)
    for name, value in right.resolutions.items():
        existing = left.resolutions.get(name)
        if existing is not None and existing != value:
            msg = f"Conflicting resolution for {name}"
            raise ValueError(msg)
    with IsolatedUpdateWorkspace(repo) as workspace:
        base = _index_files(workspace.root)
    merged_files = _merged_index_files(
        base,
        _candidate_index_files(left, repo),
        _candidate_index_files(right, repo),
    )
    with IsolatedUpdateWorkspace(repo) as workspace:
        root = workspace.root
        for path in set(base) - set(merged_files):
            (root / path).unlink()
        for path, blob in merged_files.items():
            _install_index_blob(root / path, blob)
        git(root, "add", "--all")
        tree = git(root, "write-tree").decode().strip()
        patch = git(
            root,
            "diff",
            "--cached",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            "--no-textconv",
            "HEAD",
            "--",
        )
    return Candidate(
        base_tree=left.base_tree,
        tree=tree,
        targets=left.targets,
        sources=tuple(dict.fromkeys((*left.sources, *right.sources))),
        validate_all_packages=left.validate_all_packages or right.validate_all_packages,
        systems=tuple(
            system
            for system in supported_systems()
            if system in set(left.systems) | set(right.systems)
        ),
        resolutions={**left.resolutions, **right.resolutions},
        prepared=True,
        patch=patch,
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


def _realize_svh_family(
    family: tuple[str, ...],
    *,
    flake_root: Path,
) -> tuple[tuple[validation.DerivationValidationFailure, ...], bool]:
    """Substitute the non-family closure, then compile SVH-sensitive rust_*.

    The bool is whether rust_zed / nightly may proceed. False means the
    compiler populate missed cache and force-local must not run.
    """
    if not family:
        return (), True
    extension_host = tuple(
        drv for drv in family if is_rust_extension_host_store_path(drv)
    )
    family_rest = tuple(
        drv for drv in family if not is_rust_extension_host_store_path(drv)
    )
    compiler = compiler_input_drvs(family)
    substitute, fetches = partition_compiler_input_drvs(compiler)
    failures: list[validation.DerivationValidationFailure] = []
    if substitute:
        compiler_failures = realize_warmup_outputs(
            substitute,
            flake_root=flake_root,
            progress=_hosted_validation_progress("rust-warmup"),
            substitute_only=True,
        )
        failures.extend(compiler_failures)
        if compiler_failures:
            return tuple(failures), False
    if fetches:
        fetch_failures = realize_warmup_outputs(
            fetches,
            flake_root=flake_root,
            progress=_hosted_validation_progress("rust-warmup"),
        )
        failures.extend(fetch_failures)
        if fetch_failures:
            return tuple(failures), False
    assert_force_local_dry_run((*extension_host, *family_rest))
    if extension_host:
        failures.extend(
            realize_warmup_outputs(
                extension_host,
                flake_root=flake_root,
                progress=_hosted_validation_progress("rust-warmup"),
                print_build_logs=True,
                force_local=True,
            )
        )
    if family_rest:
        failures.extend(
            realize_warmup_outputs(
                family_rest,
                flake_root=flake_root,
                progress=_hosted_validation_progress("rust-warmup"),
                print_build_logs=True,
                force_local=True,
            )
        )
    return tuple(failures), True


def _realize_logged_warmup(
    paths: tuple[str, ...],
    *,
    flake_root: Path,
) -> tuple[validation.DerivationValidationFailure, ...]:
    if not paths:
        return ()
    return realize_warmup_outputs(
        paths,
        flake_root=flake_root,
        progress=_hosted_validation_progress("rust-warmup"),
        print_build_logs=True,
    )


def _partition_rust_warmup_stripe(
    drvs: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Peel SVH leaves, then drop #1268 leaf/vendor umbrellas from others."""
    others, agent_ui = partition_agent_ui_drvs(drvs)
    others, rust_zed = partition_rust_zed_drvs(others)
    others, zed_nightly = partition_zed_editor_nightly_drvs(others)
    others = partition_rust_warmup_others(others)[0]
    return others, agent_ui, rust_zed, zed_nightly


def _settings_plan_drvs(settings: tuple[str, ...], plan: WarmupPlan) -> tuple[str, ...]:
    """Map rust_layers rust_settings* onto .drv paths, including Cachix hits."""
    if not settings:
        return ()
    planned = settings_family_warmup_outputs(plan.rust_layers)
    return unique_drvs_for_outputs(planned, plan.output_drvs) if planned else ()


def _merge_settings_family(
    family: tuple[str, ...],
    settings: tuple[str, ...],
    plan: WarmupPlan,
) -> tuple[str, ...]:
    """Add rust_settings* from the plan and imported inputDrvs to *family*."""
    if not settings:
        return family
    return tuple(
        dict.fromkeys((
            *family,
            *_settings_plan_drvs(settings, plan),
            *settings,
            *rust_crate_input_drvs(settings, SETTINGS_MEMBER_CRATES),
        ))
    )


def _canary_warmup_selection() -> tuple[tuple[int, ...], tuple[str, ...]]:
    """Read canary slot/crate env; unnamed canary defaults to slot 0 only."""
    slots = parse_canary_slots(os.environ.get("NIXCFG_CANARY_SLOTS", ""))
    crates = parse_canary_crates(os.environ.get("NIXCFG_CANARY_CRATES", ""))
    if os.environ.get("NIXCFG_CANARY") == "true" and not slots:
        slots = (0,)
    return slots, crates


def _canary_or_slot_paths(
    plan: WarmupPlan,
    warmup_slot: int,
    requested_crates: tuple[str, ...],
) -> tuple[str, ...]:
    """Named crates come from every rust layer; otherwise use the slot stripe."""
    if requested_crates:
        return canary_crate_warmup_paths(plan.rust_layers, requested_crates)
    return slot_warmup_paths(plan.rust_layers, warmup_slot)


def _realize_rust_warmup(
    warmup_plan: Path,
    warmup_slot: int,
    *,
    flake_root: Path,
) -> tuple[validation.DerivationValidationFailure, ...]:
    """Realize one rust-warmup stripe, skipping paths already in gkze."""
    requested_slots, requested_crates = _canary_warmup_selection()
    if requested_slots and warmup_slot not in requested_slots:
        return ()
    plan = load_warmup_plan(warmup_plan)
    slot_paths = _canary_or_slot_paths(plan, warmup_slot, requested_crates)
    missing = skip_cached_warmup_paths(
        slot_paths,
        present=check_path_in_cachix,
    )
    drvs = unique_drvs_for_outputs(missing, plan.output_drvs)
    others, agent_ui, rust_zed, zed_nightly = _partition_rust_warmup_stripe(drvs)
    others, settings = partition_settings_family_drvs(others)
    # Update #1258/#1263: Darwin rustc intern of a Cachix
    # language_models rlib is bare E0463 (nixpkgs#482646). rust_zed
    # then E0460s when target/deps/libextension_host is a newer SVH
    # than substituted settings_ui. #1269 slot 3: rust_settings
    # E0463'd against Cachix rust_settings_content. #1263
    # `--no-substitute` on extension_host rebuilt 406 bootstrap
    # drvs (bmake 404) because only direct refs were `--fallback`'d.
    # Substitute the full non-family `--requisites`` with
    # `--max-jobs 0`, dry-run gate the will-be-built set, then
    # `--no-substitute` rust_* only.
    zed_slot = bool(rust_zed or zed_nightly)
    family: tuple[str, ...] = ()
    if zed_slot:
        others = tuple(
            drv for drv in others if not is_extension_host_family_store_path(drv)
        )
        planned_family = extension_host_family_warmup_outputs(plan.rust_layers)
        family = (
            unique_drvs_for_outputs(planned_family, plan.output_drvs)
            if planned_family
            else ()
        )
    elif agent_ui:
        others = tuple(
            drv for drv in others if not is_rust_language_models_store_path(drv)
        )
        planned_models = language_models_warmup_outputs(plan.rust_layers)
        family = (
            unique_drvs_for_outputs(planned_models, plan.output_drvs)
            if planned_models
            else ()
        )
    import_warmup_drvs(
        tuple(dict.fromkeys((*family, *drvs, *_settings_plan_drvs(settings, plan)))),
        warmup_plan.with_name(WARMUP_DRVS_NAME),
    )
    # Planned family is only the still-missing warmup stripe. Cached
    # dependents (the #1261 E0460 mix, including settings_ui) are
    # absent from rust_layers, so always merge inputDrvs after import.
    if zed_slot:
        parents = rust_zed or rust_crate_input_drvs(zed_nightly, ("zed",))
        family = tuple(
            dict.fromkeys((
                *family,
                *rust_crate_input_drvs(parents, EXTENSION_HOST_MEMBER_CRATES),
            ))
        )
        if not any(is_rust_extension_host_store_path(drv) for drv in family):
            msg = "rust_zed drv has no rust_extension_host input"
            raise WarmupError(msg)
    elif agent_ui:
        family = tuple(dict.fromkeys((*family, *language_models_input_drvs(agent_ui))))
        if not family:
            msg = "agent_ui drv has no rust_language_models input"
            raise WarmupError(msg)
    family = _merge_settings_family(family, settings, plan)
    failures: list[validation.DerivationValidationFailure] = []
    if others:
        failures.extend(
            realize_warmup_outputs(
                others,
                flake_root=flake_root,
                progress=_hosted_validation_progress("rust-warmup"),
            )
        )
    family_failures, compiled = _realize_svh_family(family, flake_root=flake_root)
    failures.extend(family_failures)
    if compiled:
        if agent_ui and not zed_slot:
            failures.extend(_realize_logged_warmup(agent_ui, flake_root=flake_root))
        if rust_zed:
            failures.extend(_realize_logged_warmup(rust_zed, flake_root=flake_root))
        if zed_nightly:
            failures.extend(_realize_logged_warmup(zed_nightly, flake_root=flake_root))
    jobs.record_runner_storage("after-rust-warmup")
    return tuple(failures)


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


@app.command("merge-candidates")
def merge_candidates_cmd(
    left: Annotated[Path, typer.Option(help="First native candidate JSON.")],
    right: Annotated[Path, typer.Option(help="Second native candidate JSON.")],
    output: Annotated[
        Path, typer.Option(help="Merged candidate JSON outside the repository.")
    ],
) -> None:
    """Merge two same-baseline native prepare artifacts into one candidate."""
    output = _output_path(output, get_repo_root())
    merged = merge_prepared_candidates(
        Candidate.model_validate_json(left.read_bytes()),
        Candidate.model_validate_json(right.read_bytes()),
        repo=get_repo_root(),
    )
    atomic_write_text(output, merged.model_dump_json(indent=2) + "\n")


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
    candidate: Annotated[
        Path | None, typer.Option(help="Final prepared candidate JSON.")
    ] = None,
    output: Annotated[Path, typer.Option(help="Native validation report.")] = Path(
        "validation.json"
    ),
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
    if candidate is None:
        if os.environ.get("NIXCFG_CANARY") != "true":
            msg = "Validation requires a previous candidate"
            raise ValueError(msg)
        prepared = checkout_candidate(get_repo_root())
    else:
        prepared = Candidate.model_validate_json(candidate.read_bytes())
    report = validate_candidate(
        prepared,
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
