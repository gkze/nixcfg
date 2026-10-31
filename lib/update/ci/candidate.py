"""Native candidate preparation and validation using the local updater core."""

import asyncio
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stdout
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, NamedTuple, cast

import typer
from pydantic import BaseModel, ConfigDict, ValidationError

from lib.diagnostics import redact_urls
from lib.nix.models.sources import HashCollection, HashEntryMergeKey, SourceEntry
from lib.system_policy import supported_systems
from lib.update import cli as update_cli
from lib.update import derivation_validation as validation
from lib.update.candidate import Candidate, Preparation, ResolvedVersion, git
from lib.update.ci import jobs
from lib.update.ci.coverage import (
    ROOT_OUT_PATHS_NAME,
    assert_cached_darwin_boundary,
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
    CRANE_DEP_CHUNK,
    SETTINGS_MEMBER_CRATES,
    TREE_SITTER_MEMBER_CRATES,
    WARMUP_DRVS_NAME,
    WARMUP_PLAN_NAME,
    WarmupError,
    WarmupFatalError,
    WarmupPlan,
    assert_force_local_dry_run,
    assert_zed_family_realize_set,
    canary_crate_warmup_paths,
    cascade_canary_paths,
    command_store_drv,
    compiler_input_drvs,
    darwin_fod_canary_installables,
    darwin_leaf_prebuild_installables,
    export_warmup_drvs,
    filter_cascade_realize_drvs,
    gitbutler_family_slot_from_package,
    import_warmup_drvs,
    is_compiler_must_substitute_store_path,
    is_crate2nix_rust_output,
    is_deferred_compiler_rust_drv,
    is_extension_host_family_store_path,
    is_fixed_output_hash_mismatch,
    is_linux_builder_image_command,
    is_rust_extension_host_store_path,
    is_rust_language_models_store_path,
    is_rust_zed_store_path,
    is_source_fetch_mirror_line,
    is_source_fetch_store_path,
    is_source_fetch_transport_error,
    is_svh_sensitive_store_path,
    is_zed_editor_nightly_store_path,
    language_models_input_drvs,
    language_models_warmup_outputs,
    linux_builder_substitute_miss_reason,
    load_warmup_plan,
    parse_canary_crates,
    parse_canary_slots,
    partition_agent_ui_drvs,
    partition_compiler_input_drvs,
    partition_compiler_substitute_probe_drvs,
    partition_crane_dep_drvs,
    partition_rust_warmup_others,
    partition_rust_zed_drvs,
    partition_settings_family_drvs,
    partition_svh_sensitive_drvs,
    partition_zed_editor_nightly_drvs,
    plan_darwin_warmup,
    publish_warmup_source_fetches,
    raise_if_warmup_fatal,
    realize_source_fetch_drvs,
    realize_warmup_outputs,
    rust_crate_input_drvs,
    rust_svh_input_drvs,
    rust_warmup_probe_paths,
    rust_zed_member_warmup_outputs,
    rustc_generation_ids,
    settings_family_warmup_outputs,
    skip_cached_warmup_paths,
    slot_cascade_paths,
    slot_warmup_paths,
    source_fetch_retry_drvs,
    unique_drvs_for_outputs,
    write_warmup_plan,
    zed_family_realize_policy,
    zed_family_warmup_outputs,
)
from lib.update.cli_options import RepairAgent, UpdateOptions
from lib.update.derivation_validation import root_closure_check_attr
from lib.update.io import atomic_write_text
from lib.update.nix import get_current_nix_platform
from lib.update.paths import get_repo_root
from lib.update.persistence import IsolatedUpdateWorkspace, planned_update_paths
from lib.update.planner import prepare_plan_prerequisites
from lib.update.prepare_shard import (
    PrepareShardCoverage,
    coverage_from_json,
    parse_prepare_coverage,
    require_exact_prepare_coverage,
)
from lib.update.repair import check_applied_repair, propose_repair
from lib.update.updaters import ensure_updaters_loaded

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

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


@app.command("check-repair")
def check_repair() -> None:
    """Re-check the staged repair patch; the publishing job trusts no artifact."""
    check_applied_repair(get_repo_root())


ValidationScope = Literal[
    "all",
    "packages",
    "closures",
    "closure-shard",
    "rust-warmup",
    "cascade-warmup",
    "cascade-canary",
    "fod-canary",
    "leaf-prebuild",
    "zed-warmup",
]
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
    store_paths: tuple[str, ...] = ()


PREPARE_SEED_NAME = "prepare-seed.json"


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
    pins_only: bool = False,
    prepare_shard: int | None = None,
    prepare_shard_count: int | None = None,
    coverage_path: Path | None = None,
) -> tuple[Candidate, int]:
    """Extend one pinned candidate on the current system without live writes.

    ``pins_only`` refreshes flake refs and source versions without native
    hashes and records no platform. Linux/Darwin hash jobs then extend that
    pin in parallel so they hash the updated lock, not HEAD.
    """
    system = get_current_nix_platform()
    if system not in supported_systems():
        msg = f"No configured builder policy for {system}"
        raise ValueError(msg)
    if pins_only and previous is not None:
        msg = "Pin refresh cannot extend a previous candidate"
        raise ValueError(msg)
    preparation = Preparation(
        system=system,
        targets=targets,
        previous=previous,
        validate_all_packages=validate_all_packages,
        pins_only=pins_only,
        prepare_shard=prepare_shard,
        prepare_shard_count=prepare_shard_count,
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
    _write_prepare_coverage(
        preparation, coverage_path, prepared=preparation.candidate.prepared
    )
    return preparation.candidate, status


def _write_prepare_coverage(
    preparation: Preparation,
    path: Path | None,
    *,
    prepared: bool,
) -> None:
    """Record the full plan and hashed slice beside a successful shard."""
    shard = preparation.prepare_shard
    if path is None or not prepared or shard is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        PrepareShardCoverage(
            plan=preparation.prepare_plan,
            hashed=preparation.prepare_hashed,
            shard=shard,
            count=cast("int", preparation.prepare_shard_count),
        ).to_json(),
    )


_FAIL_FAST_PROGRESS_SOURCES = frozenset({
    "rust-warmup",
    "cascade-warmup",
    "cascade-canary",
    "fod-canary",
    "leaf-prebuild",
    "root-deps",
    "root-closures",
})


def _hosted_validation_progress(
    source: str, *, keep_going: bool = False
) -> validation.ValidationProgress:
    """Stream Nix validation output to the live hosted job log."""
    substitute_only = False
    # root-deps exists to realize the linux-builder image. zed-warmup
    # compiles the workspace family. Both use the 400-drv ceiling.
    stream_keep_going = keep_going or source in {"zed-warmup", "root-deps"}
    current_drv = ""
    source_fetch_miss = False
    linux_builder_substitute = False
    # Darwin host closures realize remaining system/home after rust+cascade.
    # 38506525442 job 115804595478: 2-wide rocinante+zeus streamed
    # "these 470 derivations will be built" and the rust-warmup 32-limit
    # aborted before --keep-going could substitute/build. The linux-builder
    # image still uses the 400 cap; Darwin root-closure installables do not
    # treat will-be-built as a warmup abort.
    apply_wbb_limit = source != "root-closures"

    def emit(event: validation.ValidationProgressEvent) -> None:
        nonlocal substitute_only, stream_keep_going, current_drv
        nonlocal source_fetch_miss, apply_wbb_limit, linux_builder_substitute
        if isinstance(event, validation.ValidationCommandFinished):
            return
        if isinstance(event, validation.ValidationCommandStarted):
            text = f"$ {event.command}"
            substitute_only = "--max-jobs 0" in event.command
            current_drv = command_store_drv(event.command)
            source_fetch_miss = False
            linux_builder_substitute = (
                substitute_only and is_linux_builder_image_command(event.command)
            )
            if linux_builder_substitute:
                # 39006154683: Darwin cannot compile aarch64-linux
                # etc-fstab. 50 wbb under --max-jobs 0 is a cache miss.
                stream_keep_going = False
                apply_wbb_limit = True
            elif is_linux_builder_image_command(event.command):
                stream_keep_going = True
                apply_wbb_limit = True
            elif source == "root-closures":
                apply_wbb_limit = False
            jobs.record_runner_storage(f"before-{source}")
        elif isinstance(event, validation.ValidationCommandOutput):
            text = event.line.replace("\r", "")
        else:
            text = event.replace("\r", "")
        if not text:
            return
        sys.stderr.write(f"[{source}] {redact_urls(text)}\n")
        sys.stderr.flush()
        if source in _FAIL_FAST_PROGRESS_SOURCES or source == "zed-warmup":
            # 38231309175 slot 3: rust_xet-client --max-jobs 0 printed
            # 406 because sibling rust_* were only probed. That is the
            # cranelift-40 deferral, not rustc/bmake bootstrap. Those
            # toolchain 406s still fail closed.
            if linux_builder_substitute and (
                reason := linux_builder_substitute_miss_reason(text)
            ):
                raise WarmupFatalError(reason)
            rust_substitute_miss = (
                substitute_only
                and is_crate2nix_rust_output(current_drv)
                and "will be built" in text.lower()
            )
            # 39387963366 slot 0: rust_gpui-0.2.2 --keep-going --fallback
            # streamed 415 wbb (crate graph, not rustc/bmake bootstrap)
            # and the 400-cap aborted before Cachix could receive it.
            rust_keep_going_crate = (
                stream_keep_going
                and not substitute_only
                and is_crate2nix_rust_output(current_drv)
                and not is_compiler_must_substitute_store_path(current_drv)
                and "will be built" in text.lower()
            )
            drv = command_store_drv(text)
            if (
                is_source_fetch_mirror_line(text)
                or is_source_fetch_transport_error(text)
                or ("Cannot build" in text and drv and is_source_fetch_store_path(drv))
            ):
                source_fetch_miss = True
            if not rust_substitute_miss and not rust_keep_going_crate:
                raise_if_warmup_fatal(
                    text,
                    substitute_only=substitute_only,
                    keep_going=stream_keep_going,
                    source_fetch_miss=source_fetch_miss,
                    apply_wbb_limit=apply_wbb_limit,
                )

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
        installables: list[str] = []

        def capture_dependencies(found: tuple[str, ...]) -> None:
            installables.extend(found)

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
                on_dependency_installables=capture_dependencies,
            )
        workspace.validate_changes(allowed)
    store_paths = ()
    if not failures and installables:
        # 39006154683: substituting the VM from gkze does not fire the
        # post-build hook. Record outputs so flush `cachix push`es them
        # and refreshes LRU / pin even on a cache hit.
        store_paths = jobs.drv_output_store_paths(tuple(installables))
        jobs.remember_realized_store_paths(store_paths)
    return RootDependencyCacheReport(
        tree=candidate.tree,
        system=system,
        failures=failures,
        store_paths=store_paths,
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
    tree, patch = _materialize_merged_index(repo, base, merged_files)
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


def _materialize_merged_index(
    repo: Path,
    base: dict[str, _IndexBlob],
    merged_files: dict[str, _IndexBlob],
) -> tuple[str, bytes]:
    """Install a merged index in isolation and return its tree and patch."""
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
    return tree, patch


def _require_same_platform_partials(candidates: tuple[Candidate, ...]) -> None:
    """Reject a fold that is not one prepared system with one target set."""
    if not candidates:
        msg = "Same-platform prepare merge needs every shard"
        raise ValueError(msg)
    first = candidates[0]
    if len(first.systems) != 1:
        msg = "Same-platform prepare shards must record exactly one system"
        raise ValueError(msg)
    for candidate in candidates:
        if not candidate.prepared:
            msg = "A failed preparation cannot be merged"
            raise ValueError(msg)
        if candidate.base_tree != first.base_tree:
            msg = "Merged candidates must share a baseline tree"
            raise ValueError(msg)
        if candidate.targets != first.targets:
            msg = "Candidate target selection cannot change between platforms"
            raise ValueError(msg)
        if candidate.systems != first.systems:
            msg = "Same-platform prepare shards must share one system"
            raise ValueError(msg)


def _union_resolutions(
    groups: tuple[dict[str, ResolvedVersion], ...] | list[dict[str, ResolvedVersion]],
) -> dict[str, ResolvedVersion]:
    """Union resolutions, keeping equal overlap and failing on conflict."""
    merged: dict[str, ResolvedVersion] = {}
    for group in groups:
        for name, value in group.items():
            existing = merged.get(name)
            if existing is not None and existing != value:
                msg = f"Conflicting resolution for {name}"
                raise ValueError(msg)
            merged[name] = value
    return merged


def _prepare_coverage_edges(plan: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    """Return the prerequisite edges the fold must keep inside one shard."""
    return prepare_plan_prerequisites(plan, ensure_updaters_loaded())


def _load_prepare_coverage(text: str) -> PrepareShardCoverage:
    """Check a sidecar against the component assignment of its own plan."""
    return coverage_from_json(
        text,
        prerequisites=_prepare_coverage_edges(parse_prepare_coverage(text).plan),
    )


def merge_same_platform_partials(
    candidates: tuple[Candidate, ...],
    coverages: tuple[PrepareShardCoverage, ...],
    *,
    seed: Candidate | None,
    repo: Path,
) -> Candidate:
    """Fold same-system prepare shards after an exact hash-coverage proof.

    The requested target set stays the one every shard recorded. Coverage
    proves the hashed slices are the pure partition of one plan, so each plan
    name is hashed once. The result is still a one-system candidate; the
    existing three-system merge folds it with the other platforms.

    Every shard extends the same *seed* (the pins candidate), so the 3-way
    ancestor is the seed tree, not the baseline. 41157698790: the pin left
    superconductor ``hashes: {}``; shard 0 hashed it back to the baseline
    bytes, shards 1-4 kept the pin's blank. Against the baseline the blank
    looked like the only edit and won, so the fold lost the Darwin hash.
    ``None`` means the shards extended the baseline itself.
    """
    if len(candidates) != len(coverages):
        msg = "Each prepare shard needs its coverage sidecar"
        raise ValueError(msg)
    _require_same_platform_partials(candidates)
    require_exact_prepare_coverage(
        coverages,
        prerequisites=_prepare_coverage_edges(coverages[0].plan),
    )
    paired = sorted(
        zip(coverages, candidates, strict=True),
        key=lambda item: item[0].shard,
    )
    first = paired[0][1]
    if seed is not None and (not seed.prepared or seed.base_tree != first.base_tree):
        msg = "Prepare shards must extend one prepared seed on their baseline"
        raise ValueError(msg)
    with IsolatedUpdateWorkspace(repo) as workspace:
        base = _index_files(workspace.root)
    ancestor = base if seed is None else _candidate_index_files(seed, repo)
    merged_files = dict(ancestor)
    for _coverage, candidate in paired:
        merged_files = _merged_index_files(
            ancestor,
            merged_files,
            _candidate_index_files(candidate, repo),
        )
    tree, patch = _materialize_merged_index(repo, base, merged_files)
    return Candidate(
        base_tree=first.base_tree,
        tree=tree,
        targets=first.targets,
        sources=tuple(
            dict.fromkeys(
                source for candidate in candidates for source in candidate.sources
            )
        ),
        validate_all_packages=any(
            candidate.validate_all_packages for candidate in candidates
        ),
        systems=first.systems,
        resolutions=_union_resolutions([
            candidate.resolutions for _coverage, candidate in paired
        ]),
        prepared=True,
        patch=patch,
    )


def load_prepare_shard_dir(
    root: Path,
) -> tuple[tuple[Candidate, PrepareShardCoverage], ...]:
    """Read ``*/candidate.json`` plus the sibling coverage sidecar."""
    if not root.is_dir():
        msg = "Prepare shard directory is missing"
        raise ValueError(msg)
    loaded: list[tuple[Candidate, PrepareShardCoverage]] = []
    for candidate_path in sorted(root.glob("*/candidate.json")):
        coverage_path = candidate_path.with_name("prepare-coverage.json")
        if not coverage_path.is_file():
            msg = f"Prepare shard {candidate_path.parent.name} has no coverage"
            raise ValueError(msg)
        loaded.append((
            Candidate.model_validate_json(candidate_path.read_bytes()),
            _load_prepare_coverage(coverage_path.read_text(encoding="utf-8")),
        ))
    if not loaded:
        msg = "Prepare shard directory has no candidates"
        raise ValueError(msg)
    return tuple(loaded)


def load_prepare_shard_seed(root: Path) -> Candidate:
    """Return the one seed every shard recorded beside its candidate."""
    seeds: list[Candidate] = []
    for candidate_path in sorted(root.glob("*/candidate.json")):
        seed_path = candidate_path.with_name(PREPARE_SEED_NAME)
        if not seed_path.is_file():
            msg = f"Prepare shard {candidate_path.parent.name} has no seed"
            raise ValueError(msg)
        seeds.append(Candidate.model_validate_json(seed_path.read_bytes()))
    if not seeds:
        msg = "Prepare shard directory has no candidates"
        raise ValueError(msg)
    if any(seed != seeds[0] for seed in seeds[1:]):
        msg = "Prepare shards must extend the same seed"
        raise ValueError(msg)
    return seeds[0]


def fold_prepared_candidates(
    first: Candidate,
    *rest: Candidate,
    repo: Path,
) -> Candidate:
    """Fold same-baseline native extensions left-to-right."""
    merged = first
    for next_candidate in rest:
        merged = merge_prepared_candidates(merged, next_candidate, repo=repo)
    return merged


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
    if scope in {
        "closure-shard",
        "rust-warmup",
        "cascade-warmup",
        "cascade-canary",
        "fod-canary",
        "leaf-prebuild",
        "zed-warmup",
    }:
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


def _retry_substitute_only_helpers(
    failures: tuple[validation.DerivationValidationFailure, ...],
) -> tuple[str, ...]:
    """Retry ``--max-jobs 0`` Cannot-build helpers as 1-drv local compiles.

    #1270 canary: patchutils, then pbzx, then cpio each fetched their
    source and died on ``Cannot build`` under substitute-only. rustc
    without a rust-overlay target triple / stdenv / rust_* stay refused
    so the next Unix helper does not need another prefix + kick.
    38128207590: rustc-1.98.1-aarch64-apple-darwin fetched its tar.xz
    from zed.cachix.org, then --max-jobs 0 refused the unpack.
    """
    retry: list[str] = []
    for failure in failures:
        drv = failure.installable.removesuffix("^*")
        if is_compiler_must_substitute_store_path(drv):
            continue
        if is_svh_sensitive_store_path(drv):
            continue
        if is_crate2nix_rust_output(drv):
            continue
        if "Cannot build" not in failure.message:
            continue
        retry.append(drv)
    return tuple(dict.fromkeys(retry))


def _is_deferred_compiler_rust_miss(
    failure: validation.DerivationValidationFailure,
) -> bool:
    """Return whether a substitute-only rust_* miss should compile locally.

    38088756238 substituted rustc/stdenv, then --max-jobs 0'd 80 third-party
    rust_* (lsp-types, but-*, gix-*) that were not in gkze. 38231309175
    slot 3: rust_xet-client printed 406 will-be-built for that same
    uncompiled sibling graph. rustc / stdenv misses stay fatal.
    """
    return is_deferred_compiler_rust_drv(failure.installable.removesuffix("^*"))


def _deferred_compiler_rust_drvs(
    failures: tuple[validation.DerivationValidationFailure, ...],
) -> tuple[str, ...]:
    """Return substitute-only rust_* misses in first-seen probe order."""
    return tuple(
        dict.fromkeys(
            failure.installable.removesuffix("^*")
            for failure in failures
            if _is_deferred_compiler_rust_miss(failure)
        )
    )


def _realize_compiler_substitutes(
    substitute: tuple[str, ...],
    *,
    flake_root: Path,
    progress_source: str = "rust-warmup",
) -> tuple[validation.DerivationValidationFailure, ...]:
    """``--max-jobs 0`` toolchain; keep-going rust_* leaves; retry helpers.

    Keep rustc/stdenv failures. 38231309175 slot 3: rust_xet-client's
    406 will-be-built was the uncompiled sibling rust_* graph, not
    rustc bootstrap. 39387963366 mixed those rust_* into the same
    ``--max-jobs 0`` window as rustc; both bisection halves failed and
    every drv was probed one-by-one (1069 canary / 2441 slot 2) for
    ~119 compiles. Peel rust_* first. ``--keep-going --fallback`` still
    substitutes gkze/hydra hits, then compiles misses in crane chunks
    so the umbrella stays under the 400-cap before force-local
    extension_host. Do not overwrite a rustc miss with a helper retry.
    """
    probe, rust_leaves = partition_compiler_substitute_probe_drvs(substitute)
    failures = (
        realize_warmup_outputs(
            probe,
            flake_root=flake_root,
            progress=_hosted_validation_progress(progress_source),
            substitute_only=True,
        )
        if probe
        else ()
    )
    retry = _retry_substitute_only_helpers(failures)
    deferred = tuple(
        dict.fromkeys((*_deferred_compiler_rust_drvs(failures), *rust_leaves))
    )
    stubborn = tuple(
        failure
        for failure in failures
        if failure.installable.removesuffix("^*") not in retry
        and failure.installable.removesuffix("^*") not in deferred
    )
    if stubborn:
        return failures
    compiled: list[validation.DerivationValidationFailure] = []
    if retry:
        compiled.extend(
            realize_warmup_outputs(
                retry,
                flake_root=flake_root,
                progress=_hosted_validation_progress(progress_source),
            )
        )
    if deferred:
        compiled.extend(
            realize_warmup_outputs(
                deferred,
                flake_root=flake_root,
                progress=_hosted_validation_progress(progress_source, keep_going=True),
                print_build_logs=True,
                keep_going=True,
                chunk=CRANE_DEP_CHUNK,
            )
        )
    return tuple(compiled)


def _realize_svh_family(
    family: tuple[str, ...],
    *,
    flake_root: Path,
    keep_going: bool = False,
    progress_source: str = "rust-warmup",
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
        compiler_failures = _realize_compiler_substitutes(
            substitute, flake_root=flake_root, progress_source=progress_source
        )
        failures.extend(compiler_failures)
        if compiler_failures:
            return tuple(failures), False
    if fetches:
        helpers = tuple(drv for drv in fetches if not is_source_fetch_store_path(drv))
        source_fods = tuple(drv for drv in fetches if is_source_fetch_store_path(drv))
        fetch_progress = _hosted_validation_progress(progress_source)
        if helpers:
            helper_failures = realize_warmup_outputs(
                helpers,
                flake_root=flake_root,
                progress=fetch_progress,
            )
            failures.extend(helper_failures)
            if helper_failures:
                return tuple(failures), False
        if source_fods:
            fetch_failures = realize_source_fetch_drvs(
                source_fods,
                flake_root=flake_root,
                progress=fetch_progress,
                realize=realize_warmup_outputs,
            )
            failures.extend(fetch_failures)
            if fetch_failures:
                return tuple(failures), False
    assert_force_local_dry_run((*extension_host, *family_rest))
    family_progress = _hosted_validation_progress(
        progress_source, keep_going=keep_going
    )
    if extension_host:
        failures.extend(
            realize_warmup_outputs(
                extension_host,
                flake_root=flake_root,
                progress=family_progress,
                print_build_logs=True,
                force_local=True,
                keep_going=keep_going,
            )
        )
    if family_rest:
        failures.extend(
            realize_warmup_outputs(
                family_rest,
                flake_root=flake_root,
                progress=family_progress,
                print_build_logs=True,
                force_local=True,
                keep_going=keep_going,
            )
        )
    return tuple(failures), True


def _realize_logged_warmup(
    paths: tuple[str, ...],
    *,
    flake_root: Path,
    keep_going: bool = False,
    progress_source: str = "rust-warmup",
) -> tuple[validation.DerivationValidationFailure, ...]:
    if not paths:
        return ()
    return realize_warmup_outputs(
        paths,
        flake_root=flake_root,
        progress=_hosted_validation_progress(progress_source, keep_going=keep_going),
        print_build_logs=True,
        keep_going=keep_going,
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
            *rust_crate_input_drvs(settings, TREE_SITTER_MEMBER_CRATES),
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
    return slot_warmup_paths(
        plan.rust_layers,
        warmup_slot,
        pinned=plan.rusty_v8_family,
        output_drvs=plan.output_drvs,
    )


def _expand_svh_family_from_stripe(
    family: tuple[str, ...],
    settings: tuple[str, ...],
    stripe_svh: tuple[str, ...],
    others: tuple[str, ...],
    plan: WarmupPlan,
) -> tuple[str, ...]:
    """Merge settings, workspace SVH, and others' settings/tree-sitter intern."""
    family = _merge_settings_family(family, settings, plan)
    family = tuple(dict.fromkeys((*family, *stripe_svh)))
    rust_others = tuple(drv for drv in others if is_crate2nix_rust_output(drv))
    if not rust_others:
        return family
    family = _merge_settings_family(
        family,
        rust_crate_input_drvs(rust_others, SETTINGS_MEMBER_CRATES),
        plan,
    )
    return tuple(
        dict.fromkeys((
            *family,
            *rust_crate_input_drvs(rust_others, TREE_SITTER_MEMBER_CRATES),
        ))
    )


def _slot_paths_with_gitbutler_canary(
    plan: WarmupPlan,
    warmup_slot: int,
    requested_crates: tuple[str, ...],
    *,
    flake_root: Path,
) -> tuple[tuple[str, ...], dict[str, str]]:
    """Return this stripe plus a gitbutler package canary when rust peel is empty."""
    slot_paths = _canary_or_slot_paths(plan, warmup_slot, requested_crates)
    output_drvs = dict(plan.output_drvs)
    if plan.warmup_outputs or requested_crates:
        return slot_paths, output_drvs
    family_paths, family_drvs = gitbutler_family_slot_from_package(
        flake_root, warmup_slot
    )
    return tuple(dict.fromkeys((*slot_paths, *family_paths))), {
        **output_drvs,
        **family_drvs,
    }


def _realize_rust_warmup_others(
    others: tuple[str, ...],
    *,
    flake_root: Path,
    chunk: int | None = None,
) -> tuple[validation.DerivationValidationFailure, ...]:
    """Realize rust-warmup others; DNS-retry source FODs after a tainted hash.

    39217658040 slot 4: gitbutler others is family-scale (39 wbb) and
    must use the 400-cap. 39234076105 slot 4: rust_v8-goose keep-going
    hashed a 503-tainted rusty_v8 fetchgit tree. Retry the FOD, then the
    parent crate once the source is in the store. Crane-dep rust_* use
    the same retry; they are crates.io FODs too.
    """
    if not others:
        return ()
    progress = _hosted_validation_progress("rust-warmup", keep_going=True)
    if chunk is None:
        others_failures = realize_warmup_outputs(
            others,
            flake_root=flake_root,
            progress=progress,
            keep_going=True,
        )
    else:
        others_failures = realize_warmup_outputs(
            others,
            flake_root=flake_root,
            progress=progress,
            keep_going=True,
            chunk=chunk,
        )
    retry = source_fetch_retry_drvs(others_failures)
    if not retry:
        return others_failures
    fod_failures = realize_source_fetch_drvs(
        retry,
        flake_root=flake_root,
        progress=progress,
    )
    if fod_failures:
        return fod_failures
    return realize_warmup_outputs(
        others,
        flake_root=flake_root,
        progress=progress,
        keep_going=True,
    )


def _compile_drvs_after_gitbutler_probe(
    slot_paths: tuple[str, ...],
    output_drvs: dict[str, str],
    warmup_plan: Path,
    *,
    flake_root: Path,
) -> tuple[str, ...] | None:
    """Return rust-warmup .drv paths to compile, or None when the stripe is done.

    Cached GitButler-family NARs are probed with ``--max-jobs 0``. A hit
    skips compile. A miss (38867194218 path-info lie) joins the compile set.
    None means the slot was fully cached and must not import the drv cache.
    """
    uncached = skip_cached_warmup_paths(slot_paths, present=check_path_in_cachix)
    cached_family = rust_warmup_probe_paths(slot_paths, present=check_path_in_cachix)
    if not uncached and not cached_family:
        return None
    drvs = unique_drvs_for_outputs(uncached, output_drvs) if uncached else ()
    probe_drvs = (
        unique_drvs_for_outputs(cached_family, output_drvs) if cached_family else ()
    )
    if not probe_drvs:
        return drvs
    import_warmup_drvs(probe_drvs, warmup_plan.with_name(WARMUP_DRVS_NAME))
    probe_failures = realize_warmup_outputs(
        probe_drvs,
        flake_root=flake_root,
        progress=_hosted_validation_progress("rust-warmup", keep_going=True),
        substitute_only=True,
        keep_going=True,
    )
    still = tuple(
        dict.fromkeys(
            failure.installable.removesuffix("^*") for failure in probe_failures
        )
    )
    return tuple(dict.fromkeys((*drvs, *still)))


def _realize_rust_warmup(
    warmup_plan: Path,
    warmup_slot: int,
    *,
    flake_root: Path,
    expected_tree: str,
) -> tuple[validation.DerivationValidationFailure, ...]:
    """Realize one rust-warmup stripe, skipping paths already in gkze."""
    requested_slots, requested_crates = _canary_warmup_selection()
    if requested_slots and warmup_slot not in requested_slots:
        return ()
    plan = load_warmup_plan(warmup_plan, expected_tree=expected_tree)
    slot_paths, output_drvs = _slot_paths_with_gitbutler_canary(
        plan, warmup_slot, requested_crates, flake_root=flake_root
    )
    drvs = _compile_drvs_after_gitbutler_probe(
        slot_paths, output_drvs, warmup_plan, flake_root=flake_root
    )
    if not drvs:
        jobs.record_runner_storage("after-rust-warmup")
        return ()
    others, agent_ui, rust_zed, zed_nightly = _partition_rust_warmup_stripe(drvs)
    others, settings = partition_settings_family_drvs(others)
    others, stripe_svh = partition_svh_sensitive_drvs(others)
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
        planned_family = rust_zed_member_warmup_outputs(plan.rust_layers)
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
    planned_drvs = frozenset(plan.output_drvs.values())
    import_warmup_drvs(
        tuple(
            drv
            for drv in dict.fromkeys((
                *family,
                *drvs,
                *_settings_plan_drvs(settings, plan),
            ))
            if drv in planned_drvs
        ),
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
                *rust_svh_input_drvs(parents),
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
    family = _expand_svh_family_from_stripe(family, settings, stripe_svh, others, plan)
    return _realize_rust_warmup_compile(
        others,
        family,
        flake_root=flake_root,
        agent_ui=agent_ui,
        rust_zed=rust_zed,
        zed_nightly=zed_nightly,
        zed_slot=zed_slot,
    )


def _realize_rust_warmup_compile(
    others: tuple[str, ...],
    family: tuple[str, ...],
    *,
    flake_root: Path,
    agent_ui: tuple[str, ...],
    rust_zed: tuple[str, ...],
    zed_nightly: tuple[str, ...],
    zed_slot: bool,
) -> tuple[validation.DerivationValidationFailure, ...]:
    """Realize crane deps, SVH family, others, then compiled leaves."""
    failures: list[validation.DerivationValidationFailure] = []
    others, crane_deps = partition_crane_dep_drvs(others)
    # Crane-style: crates.io rust_* into the store first so force-local
    # SVH family substitutes them instead of rebuilding under
    # --no-substitute. Chunk 8 bounds macos-15 RSS.
    if crane_deps:
        failures.extend(
            _realize_rust_warmup_others(
                crane_deps, flake_root=flake_root, chunk=CRANE_DEP_CHUNK
            )
        )
    # Force-local SVH next. 38121661811 slots 1/2 built feature_flags /
    # git_hosting_providers before settings and fatal'd Cannot build
    # rust_settings after fetching Cachix content/json.
    family_failures, compiled = _realize_svh_family(
        family, flake_root=flake_root, keep_going=True
    )
    failures.extend(family_failures)
    if others:
        # 39217658040 slot 4: rust_but-agentlog --keep-going --fallback
        # streamed 39 wbb after helper retries compiled the uncached
        # overlay bash / nix-prefetch-git / fetch-cargo-vendor-util.
        # Default rust-warmup progress still 32-caps argv --keep-going
        # (406-bootstrap / goose-cli umbrellas). partition already
        # deferred those leaves. crate2nix rust_* keep_going (rust_gpui
        # 415 on 39387963366 slot 0) must compile; rustc/bmake 406
        # still fail closed.
        failures.extend(_realize_rust_warmup_others(others, flake_root=flake_root))
    if compiled:
        if agent_ui and not zed_slot:
            failures.extend(_realize_logged_warmup(agent_ui, flake_root=flake_root))
        if rust_zed:
            failures.extend(_realize_logged_warmup(rust_zed, flake_root=flake_root))
        if zed_nightly:
            failures.extend(_realize_logged_warmup(zed_nightly, flake_root=flake_root))
    jobs.record_runner_storage("after-rust-warmup")
    return tuple(failures)


def _realize_zed_warmup(
    warmup_plan: Path,
    *,
    flake_root: Path,
    expected_tree: str | None = None,
) -> tuple[validation.DerivationValidationFailure, ...]:
    """Build the whole Zed rust family on one runner, or substitute all of it.

    Mixed Cachix presence force-locals every family drv. Distinct rustc
    generations fail closed. --keep-going surfaces every broken crate.
    """
    if expected_tree is None:
        expected_tree = git(flake_root, "rev-parse", "HEAD^{tree}").decode().strip()
    plan = load_warmup_plan(warmup_plan, expected_tree=expected_tree)
    family_outputs = zed_family_warmup_outputs(plan.rust_layers)
    rust_zed_out = tuple(
        path for path in family_outputs if is_rust_zed_store_path(path)
    )
    nightly_out = tuple(
        path for path in family_outputs if is_zed_editor_nightly_store_path(path)
    )
    member_out = tuple(
        path
        for path in family_outputs
        if not is_rust_zed_store_path(path)
        and not is_zed_editor_nightly_store_path(path)
    )
    member_drvs = unique_drvs_for_outputs(member_out, plan.output_drvs)
    rust_zed = unique_drvs_for_outputs(rust_zed_out, plan.output_drvs)
    zed_nightly = unique_drvs_for_outputs(nightly_out, plan.output_drvs)
    import_warmup_drvs(
        tuple(dict.fromkeys((*member_drvs, *rust_zed, *zed_nightly))),
        warmup_plan.with_name(WARMUP_DRVS_NAME),
    )
    parents = rust_zed or rust_crate_input_drvs(zed_nightly, ("zed",))
    if parents:
        member_drvs = tuple(
            dict.fromkeys((
                *member_drvs,
                *rust_svh_input_drvs(parents),
            ))
        )
    family_drvs = tuple(dict.fromkeys((*member_drvs, *rust_zed, *zed_nightly)))
    assert_zed_family_realize_set(family_drvs, family_drvs)
    rustc_ids = rustc_generation_ids(family_drvs) if family_drvs else ()
    policy = zed_family_realize_policy(
        family_outputs or family_drvs,
        present=check_path_in_cachix,
        rustc_ids=rustc_ids,
    )
    if policy == "substitute":
        return realize_warmup_outputs(
            family_drvs,
            flake_root=flake_root,
            progress=_hosted_validation_progress("zed-warmup"),
            substitute_only=True,
        )
    family_failures, compiled = _realize_svh_family(
        member_drvs,
        flake_root=flake_root,
        keep_going=True,
        progress_source="zed-warmup",
    )
    failures = list(family_failures)
    if compiled:
        failures.extend(
            _realize_logged_warmup(
                rust_zed,
                flake_root=flake_root,
                keep_going=True,
                progress_source="zed-warmup",
            )
        )
        failures.extend(
            _realize_logged_warmup(
                zed_nightly,
                flake_root=flake_root,
                keep_going=True,
                progress_source="zed-warmup",
            )
        )
    jobs.record_runner_storage("after-zed-warmup")
    return tuple(failures)


def _realize_cascade_paths(
    warmup_plan: Path,
    paths: tuple[str, ...],
    *,
    flake_root: Path,
    label: str,
    expected_tree: str,
) -> tuple[validation.DerivationValidationFailure, ...]:
    """Realize planned cascade outputs one drv at a time, skip-if-in-gkze."""
    plan = load_warmup_plan(warmup_plan, expected_tree=expected_tree)
    missing = skip_cached_warmup_paths(
        paths,
        present=check_path_in_cachix,
    )
    if not missing:
        jobs.record_runner_storage(f"after-{label}")
        return ()
    drvs = unique_drvs_for_outputs(missing, plan.output_drvs)
    import_warmup_drvs(drvs, warmup_plan.with_name(WARMUP_DRVS_NAME))
    # 38191092497 slot 0: linear-cli-jsr-manifest.tsv.drv^* printed
    # 1297 will-be-built after ~75m of 1-3 drv FODs. Fatal'ing the
    # slot threw away the rest of the stripe. Root shards still own
    # umbrellas; cascade only realizes items under the 400 ceiling.
    drvs = filter_cascade_realize_drvs(drvs)
    if not drvs:
        jobs.record_runner_storage(f"after-{label}")
        return ()
    failures = realize_warmup_outputs(
        drvs,
        flake_root=flake_root,
        progress=_hosted_validation_progress(label, keep_going=True),
        keep_going=True,
        chunk=1,
    )
    jobs.record_runner_storage(f"after-{label}")
    return failures


def _realize_cascade_warmup(
    warmup_plan: Path,
    warmup_slot: int,
    *,
    flake_root: Path,
    expected_tree: str,
) -> tuple[validation.DerivationValidationFailure, ...]:
    """Realize one cascade-warmup stripe, one drv at a time, skip-if-in-gkze.

    38174166107 slots 1/2/3 ``nix build``'d output paths after importing
    the ``.drv`` cache and fatal'd ``don't know how to build these
    paths`` / no substituter (vimplugin queries, bun-cache-shard,
    rust crate FODs). ``foo.drv^*`` is the output set; rust-warmup
    already passes ``.drv`` paths.
    """
    plan = load_warmup_plan(warmup_plan, expected_tree=expected_tree)
    return _realize_cascade_paths(
        warmup_plan,
        slot_cascade_paths(plan.cascade_layers, warmup_slot),
        flake_root=flake_root,
        label="cascade-warmup",
        expected_tree=expected_tree,
    )


def _realize_cascade_canary(
    warmup_plan: Path,
    *,
    flake_root: Path,
    expected_tree: str,
) -> tuple[validation.DerivationValidationFailure, ...]:
    """Realize cascade-canary outputs once so stripe slots substitute them.

    38394692516 slot 4 compiled buzz-onnxruntime from source (not in gkze
    or cache.nixos.org) and APFS write-EIO'd ranlib. 38986213199 slot 1
    fail-closed HQ 0.11.13's arm64 automatic-update gate inventory after
    canary had already gone green. A dedicated canary runner publishes
    those NARs before the 5-wide stripe starts.
    """
    plan = load_warmup_plan(warmup_plan, expected_tree=expected_tree)
    return _realize_cascade_paths(
        warmup_plan,
        cascade_canary_paths(plan.cascade_layers),
        flake_root=flake_root,
        label="cascade-canary",
        expected_tree=expected_tree,
    )


def _realize_fod_canary(
    *,
    flake_root: Path,
) -> tuple[validation.DerivationValidationFailure, ...]:
    """Realize Darwin warmup-path FODs one attr at a time; abort on hash flip.

    38739788947: bb-pnpm-deps flip-flopped after a leftover-contaminated
    rematerialize. Two independent Darwin hosts must agree on the pin
    before rust-warmup occupies the macos-15 pool for hours.
    """
    failures: list[validation.DerivationValidationFailure] = []
    progress = _hosted_validation_progress("fod-canary")
    for installable in darwin_fod_canary_installables():
        window = validation.validate_derivation_requests(
            (
                validation.DerivationValidationRequest(
                    source="fod-canary",
                    installable=installable,
                    mode="build",
                ),
            ),
            flake_root=flake_root,
            progress=progress,
            print_build_logs=True,
        )
        failures.extend(window)
        if any(is_fixed_output_hash_mismatch(failure.message) for failure in window):
            return tuple(failures)
    return tuple(failures)


def _realize_leaf_prebuild(
    *,
    flake_root: Path,
) -> tuple[validation.DerivationValidationFailure, ...]:
    """Realize heavy Darwin leaves while plan-darwin-closures evaluates.

    The Cachix hook publishes them, so cascade stripes substitute instead
    of compiling the same leaf on two slots. Cascade and roots still
    realize and gate every path; this only moves when the leaf is built.
    """
    return validation.validate_derivation_requests(
        tuple(
            validation.DerivationValidationRequest(
                source="leaf-prebuild",
                installable=installable,
                mode="build",
            )
            for installable in darwin_leaf_prebuild_installables()
        ),
        flake_root=flake_root,
        progress=_hosted_validation_progress("leaf-prebuild"),
        print_build_logs=True,
    )


def _realize_requested_warmup(
    scope: str,
    warmup_plan: Path | None,
    warmup_slot: int | None,
    *,
    flake_root: Path,
    expected_tree: str,
) -> tuple[validation.DerivationValidationFailure, ...]:
    """Dispatch rust/cascade warmups, the canaries, leaf-prebuild, or zed-warmup."""
    if scope == "rust-warmup":
        if warmup_plan is None or warmup_slot is None:
            msg = "rust-warmup requires a warmup plan and slot"
            raise ValueError(msg)
        return _realize_rust_warmup(
            warmup_plan,
            warmup_slot,
            flake_root=flake_root,
            expected_tree=expected_tree,
        )
    if scope == "cascade-warmup":
        if warmup_plan is None or warmup_slot is None:
            msg = "cascade-warmup requires a warmup plan and slot"
            raise ValueError(msg)
        return _realize_cascade_warmup(
            warmup_plan,
            warmup_slot,
            flake_root=flake_root,
            expected_tree=expected_tree,
        )
    if scope == "cascade-canary":
        if warmup_plan is None:
            msg = "cascade-canary requires a warmup plan"
            raise ValueError(msg)
        return _realize_cascade_canary(
            warmup_plan,
            flake_root=flake_root,
            expected_tree=expected_tree,
        )
    if scope == "fod-canary":
        return _realize_fod_canary(flake_root=flake_root)
    if scope == "leaf-prebuild":
        return _realize_leaf_prebuild(flake_root=flake_root)
    if scope == "zed-warmup":
        if warmup_plan is None:
            msg = "zed-warmup requires a warmup plan"
            raise ValueError(msg)
        return _realize_zed_warmup(
            warmup_plan,
            flake_root=flake_root,
            expected_tree=expected_tree,
        )
    return ()


def _remember_successful_root_paths(
    *,
    flake_root: Path,
    system: str,
    closure_roots: tuple[str, ...] | None,
    failures: tuple[object, ...],
) -> None:
    """Record realized Darwin-root out paths so flush can ``cachix push`` them.

    38821223775 built ``darwin-system-26.11.57a3171`` wrappers, then
    ``cachix daemon stop`` returned 0 without publishing those tops.
    Linux jobs only realize those roots' native deps.
    """
    if failures or not system.endswith("-darwin"):
        # Linux validate-all realizes Darwin-root *deps*, not
        # checks.<linux>.root-closures. 38861101992 path-info'd that
        # missing attr and failed after a successful realize.
        return
    names = closure_roots if closure_roots is not None else (None,)
    jobs.remember_realized_installable_paths(
        tuple(
            f"path:{flake_root}#checks.{system}.{root_closure_check_attr(name)}"
            for name in names
        )
    )


def _realize_root_closures(
    *,
    flake_root: Path,
    system: str,
    scope: str,
    closure_roots: tuple[str, ...] | None,
    budget: float | None,
    prior: tuple[validation.DerivationValidationFailure, ...],
) -> tuple[validation.DerivationValidationFailure, ...]:
    """GC, realize root closures, and record tops for an explicit Cachix push."""
    jobs.record_runner_storage(f"before-gc-{scope}")
    jobs.reclaim_hosted_store()
    jobs.record_runner_storage(f"after-gc-{scope}")
    # Hosted macos-15 died mid-build when -L streamed 4000+ derivation
    # logs. Build without those logs so Cachix can upload every path.
    closure_progress = _hosted_validation_progress("root-closures")
    if budget is not None:
        closure_progress(
            f"Root-closure build budget is {budget:.0f}s; "
            "realized paths stay in the gkze cache"
        )
    failures = prior + validation.validate_root_closures(
        flake_root=flake_root,
        systems=(system,),
        root_names=closure_roots,
        include_dependencies=True,
        print_build_logs=not jobs.is_hosted_darwin_runner(),
        progress=closure_progress,
        timeout=_CLOSURE_DISCOVERY_TIMEOUT_SECONDS if budget is not None else None,
        build_timeout=budget,
    )
    _remember_successful_root_paths(
        flake_root=flake_root,
        system=system,
        closure_roots=closure_roots,
        failures=failures,
    )
    jobs.record_runner_storage(f"after-closures-{scope}")
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
    ``closures`` gate still proves ``root-closures``.     ``rust-warmup`` realizes
    one 5-wide stripe of the planner rust_* layers and is not certify
    evidence. ``fod-canary`` realizes Darwin warmup-path FODs
    (pnpm-deps, DMG src) on two hosts before rust-warmup. ``cascade-canary``
    realizes ``buzz-onnxruntime`` and versioned ``hq-*`` once so
    the 5-wide stripe can substitute them. ``leaf-prebuild`` realizes
    heavy rust_*-independent leaves (zeron) during the plan window. ``cascade-warmup`` realizes the
    shared non-rust intersection one drv at a time. Hosted Darwin shards
    GC before the root-closure fetch: a cache-miss rust_* subtree can
    fill macos-15 and ``min-free`` then GCs
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
    if scope == "cascade-warmup" and (warmup_plan is None or warmup_slot is None):
        msg = "cascade-warmup requires a warmup plan and slot"
        raise ValueError(msg)
    if scope in {"cascade-canary", "zed-warmup"} and warmup_plan is None:
        msg = f"{scope} requires a warmup plan"
        raise ValueError(msg)
    if scope not in {"rust-warmup", "cascade-warmup"} and warmup_slot is not None:
        msg = "warmup slots are only valid for rust-warmup or cascade-warmup"
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
            failures += _realize_requested_warmup(
                scope,
                warmup_plan,
                warmup_slot,
                flake_root=snapshot.root,
                expected_tree=candidate.tree,
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
                # package store. Root checks come from the independently
                # verified manifest.
                failures = _realize_root_closures(
                    flake_root=snapshot.root,
                    system=system,
                    scope=scope,
                    closure_roots=closure_roots,
                    budget=budget,
                    prior=failures,
                )
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
    extra: Annotated[
        Path | None,
        typer.Option(help="Optional third native candidate JSON."),
    ] = None,
) -> None:
    """Merge two or three same-baseline native prepare artifacts."""
    output = _output_path(output, get_repo_root())
    candidates = [
        Candidate.model_validate_json(left.read_bytes()),
        Candidate.model_validate_json(right.read_bytes()),
    ]
    if extra is not None:
        candidates.append(Candidate.model_validate_json(extra.read_bytes()))
    merged = fold_prepared_candidates(*candidates, repo=get_repo_root())
    atomic_write_text(output, merged.model_dump_json(indent=2) + "\n")


@app.command("merge-prepare-shards")
def merge_prepare_shards_cmd(
    shards: Annotated[
        Path, typer.Option(help="Directory of shard candidate artifacts.")
    ],
    output: Annotated[
        Path,
        typer.Option(help="Merged one-system candidate JSON outside the repository."),
    ],
) -> None:
    """Fold same-platform prepare shards after proving exact hash coverage."""
    output = _output_path(output, get_repo_root())
    loaded = load_prepare_shard_dir(shards)
    merged = merge_same_platform_partials(
        tuple(candidate for candidate, _coverage in loaded),
        tuple(coverage for _candidate, coverage in loaded),
        seed=load_prepare_shard_seed(shards),
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
    pins_only: Annotated[
        bool,
        typer.Option(
            help="Refresh flake refs and source versions without native hashes."
        ),
    ] = False,
    prepare_shard: Annotated[
        int | None,
        typer.Option(help="Hash only this slice of the unchanged target set."),
    ] = None,
    prepare_shard_count: Annotated[
        int | None,
        typer.Option(help="Number of same-platform prepare slices."),
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
    coverage = (
        None
        if prepare_shard is None and prepare_shard_count is None
        else output.with_name("prepare-coverage.json")
    )
    candidate, status = prepare_candidate(
        selected,
        previous=prior,
        validate_all_packages=validate_all_packages,
        pins_only=pins_only,
        prepare_shard=prepare_shard,
        prepare_shard_count=prepare_shard_count,
        coverage_path=coverage,
    )
    if coverage is not None and prior is not None:
        # The shard fold needs the tree every shard extended as its ancestor.
        atomic_write_text(
            output.with_name(PREPARE_SEED_NAME),
            prior.model_dump_json(indent=2) + "\n",
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
    if scope == "cascade-warmup" and (warmup_plan is None or warmup_slot is None):
        msg = "cascade-warmup requires --warmup-plan and --warmup-slot"
        raise ValueError(msg)
    if scope in {"cascade-canary", "zed-warmup"} and warmup_plan is None:
        msg = f"{scope} requires --warmup-plan"
        raise ValueError(msg)
    if scope not in {"rust-warmup", "cascade-warmup"} and warmup_slot is not None:
        msg = "--warmup-slot is only valid for rust-warmup or cascade-warmup"
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


@contextmanager
def _plan_phase(name: str) -> Generator[None]:
    """Log one plan-shards phase's wall time so the tail is attributable."""
    start = time.monotonic()
    try:
        yield
    finally:
        sys.stderr.write(
            f"plan-shards phase {name} secs={time.monotonic() - start:.1f}\n"
        )
        sys.stderr.flush()


def _timed_phase[T](name: str, work: Callable[[], T]) -> T:
    with _plan_phase(name):
        return work()


def _export_and_publish_warmup(warmup: WarmupPlan, drvs_cache: Path) -> None:
    """Export the warmup ``.drv`` cache while publishing source FODs.

    The export is local ``nix copy`` + xz; the publish is substituter
    network I/O. Neither reads the other's output, so they overlap.
    Both results are awaited, so either failure surfaces as before.
    """
    with ThreadPoolExecutor(max_workers=2) as pool:
        exported = pool.submit(
            _timed_phase,
            "export-drvs",
            lambda: export_warmup_drvs(tuple(warmup.output_drvs.values()), drvs_cache),
        )
        published = pool.submit(
            _timed_phase,
            "source-fods",
            lambda: publish_warmup_source_fetches(warmup, flake_root=get_repo_root()),
        )
        exported.result()
        published.result()


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
            root = snapshot.root
            costs = _timed_phase("costs", lambda: costs_for_tree(root))
            manifest = _timed_phase(
                "manifest", lambda: eval_root_closure_manifest(root)
            )
            shards = plan_darwin_closure_shards(manifest, costs=costs)
            paths = _timed_phase("root-paths", lambda: root_store_paths(root, manifest))
            warmup = _timed_phase(
                "warmup-plan",
                lambda: plan_darwin_warmup(
                    root,
                    tree=prepared.tree,
                    manifest=manifest,
                    shards=shards,
                ),
            )
    write_root_out_path_cache(
        output.with_name(ROOT_OUT_PATHS_NAME),
        tree=prepared.tree,
        root_paths=paths,
        manifest=manifest,
    )
    write_warmup_plan(output.with_name(WARMUP_PLAN_NAME), warmup)
    _export_and_publish_warmup(warmup, output.with_name(WARMUP_DRVS_NAME))
    matrix = github_actions_matrix(shards)
    atomic_write_text(output, json.dumps(matrix, indent=2) + "\n")
    target = github_output
    if target is None:
        raw = os.environ.get("GITHUB_OUTPUT")
        target = Path(raw) if raw else None
    if target is not None:
        write_github_actions_output(shards, target)


@app.command("assert-cached-boundary")
def assert_cached_boundary(
    candidate: Annotated[Path, typer.Option(help="Prepared candidate JSON.")],
    evidence: Annotated[Path, typer.Option(help="Downloaded Update artifacts.")],
) -> None:
    """Fail unless the linux-builder image and one Darwin root are in Cachix.

    39006154683: Darwin closures spent 5h after ``--max-jobs 0`` missed
    ``nixos-disk-image``. This path-info canary runs after linux-deps
    flush and one Darwin root shard, before the aggregate closures job.
    """
    prepared = Candidate.model_validate_json(candidate.read_bytes())
    require_complete_candidate(prepared)
    assert_cached_darwin_boundary(evidence, tree=prepared.tree)


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
    farm exits in seconds. Darwin Cachix queries use each shard's flushed
    ``realized-root-paths.json``, not Linux-eval'd planner hashes. This
    command never re-evaluates Darwin out paths.
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
