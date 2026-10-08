"""Structural Darwin root warmup: realize the missing shared drv set.

Packages inventory (zed) is not the roots' rust/stdenv graph. Hydra's
cache.nixos.org still serves the pre-CVE Darwin coreutils, while this
nixpkgs revision patches coreutils (CVE-2026-56391/56392) in
``pkgs/by-name/co/coreutils/package.nix``. That is a nixpkgs source
patch, not a repo overlay: ``overlays/local-build-fixes.nix`` does not
touch coreutils, and ``modules/home/packages.nix`` only adds the
``coreutils-full`` leaf. ``lib/package-overlays.nix`` applies the same
overlay list to packages, darwinConfigurations, and homeConfigurations,
so the patched stdenv is consistent across those instantiations. Scoping
or dropping the CVE patches is a nixpkgs-pin decision, not a warmup
shortcut.

Until hydra publishes that stdenv, every downstream drv whose hash
depends on it misses cache.nixos.org. Warmup therefore realizes the
intersection of per-root Darwin output closures that are on neither
cache.nixos.org nor gkze, and the planner fails closed if a shard would
still compile more than a packages-scale remainder locally.
"""

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from lib.update.ci.coverage import (
    check_path_in_cachix,
    check_path_on_nixos,
)
from lib.update.ci.shard_plan import (
    ClosureShard,
    composed_root_name,
    darwin_roots,
)
from lib.update.derivation_validation import (
    DerivationValidationFailure,
    DerivationValidationRequest,
    RootClosureManifest,
    root_closure_installable,
    validate_derivation_requests,
)
from lib.update.io import atomic_write_text

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from pathlib import Path

    from lib.update.derivation_validation import ValidationProgress

WARMUP_PLAN_NAME = "warmup-plan.json"
_DARWIN_SYSTEM = "aarch64-darwin"
# Hosted packages on 37740898487 realized 132 local zed drvs. Home-george
# compiled 1534 rust_* because those hashes were not the packages graph.
# After intersection warmup, a shard should only compile host-unique leaves.
# 400 is packages-scale plus slack; 1534 rust_* fails this gate.
MAX_SHARD_LOCAL_BUILDS = 400
_WARMUP_REALIZE_CHUNK = 128
_SUBSTITUTER_WORKERS = 16
_NIXOS_CACHE = "https://cache.nixos.org"
_GKZE_CACHE = "https://gkze.cachix.org"


class WarmupError(ValueError):
    """The planner could not produce a safe Darwin warmup set."""


class RootWarmupStats(BaseModel):
    """Per-root Darwin output counts relative to substituters and warmup."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outputs: int
    missing: int
    warmup: int
    remaining: int


class ShardLocalBuildReport(BaseModel):
    """How many Darwin outputs a shard must still compile after warmup."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    shard: str
    roots: tuple[str, ...]
    remaining: int
    remaining_rust_crates: int


class WarmupPlan(BaseModel):
    """Intersection of missing Darwin root outputs the packages job realizes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(alias="schemaVersion")
    system: str
    substituters: tuple[str, ...]
    warmup_outputs: tuple[str, ...] = Field(alias="warmupOutputs")
    per_root: dict[str, RootWarmupStats] = Field(alias="perRoot")
    shards: tuple[ShardLocalBuildReport, ...]
    notes: str


def is_crate2nix_rust_output(store_path: str) -> bool:
    """Return whether *store_path* is a crate2nix ``rust_*`` output."""
    name = store_path.rsplit("/", 1)[-1]
    _hash, separator, rest = name.partition("-")
    return bool(separator) and rest.startswith("rust_")


def darwin_output_paths(payload: Mapping[str, object]) -> frozenset[str]:
    """Collect aarch64-darwin output paths from ``nix derivation show`` JSON."""
    derivations = payload.get("derivations")
    if not isinstance(derivations, dict):
        msg = "derivation graph missing derivations"
        raise WarmupError(msg)
    paths: set[str] = set()
    for drv in derivations.values():
        if not isinstance(drv, dict) or drv.get("system") != _DARWIN_SYSTEM:
            continue
        outputs = drv.get("outputs")
        if not isinstance(outputs, dict):
            continue
        for output in outputs.values():
            if not isinstance(output, dict):
                continue
            path = output.get("path")
            if isinstance(path, str) and path.startswith("/nix/store/"):
                paths.add(path)
    return frozenset(paths)


def intersect_missing(
    per_root: Mapping[str, frozenset[str]],
    substitutable: frozenset[str],
) -> frozenset[str]:
    """Return outputs every root needs that neither substituter has."""
    if not per_root:
        return frozenset()
    shared = frozenset.intersection(*per_root.values())
    return shared - substitutable


def shard_remaining_outputs(
    shard: ClosureShard,
    per_root: Mapping[str, frozenset[str]],
    *,
    substitutable: frozenset[str],
    warmup: frozenset[str],
) -> frozenset[str]:
    """Return this shard's Darwin outputs that warmup and caches will not cover."""
    union: set[str] = set()
    for name in shard.roots:
        union.update(per_root.get(name, frozenset()))
    return frozenset(union - substitutable - warmup)


def assert_local_build_threshold(
    reports: Sequence[ShardLocalBuildReport],
    *,
    threshold: int = MAX_SHARD_LOCAL_BUILDS,
) -> None:
    """Fail closed if any shard would compile more than *threshold* locally."""
    if threshold < 1:
        msg = "local-build threshold must be at least 1"
        raise WarmupError(msg)
    offenders = [report for report in reports if report.remaining > threshold]
    if not offenders:
        return
    detail = ", ".join(
        f"{report.shard} remaining={report.remaining} "
        f"rust_*={report.remaining_rust_crates}"
        for report in offenders
    )
    msg = (
        "Darwin shard local-build budget exceeded before realization "
        f"(threshold={threshold}, packages-scale was 132 local drvs on "
        f"37740898487): {detail}"
    )
    raise WarmupError(msg)


def eval_root_darwin_outputs(
    flake_root: Path,
    root_name: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> frozenset[str]:
    """Instantiate one Darwin root's drv graph and return Darwin output paths."""
    runner = subprocess.run if run is None else run
    installable = root_closure_installable(_DARWIN_SYSTEM, root_name).replace(
        "path:.#", f"path:{flake_root}#"
    )
    result = runner(
        [
            "nix",
            "derivation",
            "show",
            "--recursive",
            "--no-update-lock-file",
            "--option",
            "allow-import-from-derivation",
            "false",
            installable,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = (
            result.stderr.strip()
            or result.stdout.strip()
            or "nix derivation show failed"
        )
        msg = f"warmup graph for {root_name} failed: {detail}"
        raise WarmupError(msg)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        msg = f"warmup graph for {root_name} is not JSON"
        raise WarmupError(msg) from error
    if not isinstance(payload, dict):
        msg = f"warmup graph for {root_name} is not an object"
        raise WarmupError(msg)
    return darwin_output_paths(payload)


def substitutable_paths(
    paths: Iterable[str],
    *,
    present: Callable[[str], bool],
) -> frozenset[str]:
    """Return the subset of *paths* a substituter already has."""
    unique = tuple(dict.fromkeys(paths))
    if not unique:
        return frozenset()
    found: set[str] = set()
    with ThreadPoolExecutor(max_workers=_SUBSTITUTER_WORKERS) as pool:
        for path, ok in zip(unique, pool.map(present, unique), strict=True):
            if ok:
                found.add(path)
    return frozenset(found)


def default_cache_present(
    store_path: str,
    *,
    run: Callable[..., object] | None = None,
) -> bool:
    """Return whether *store_path* is on cache.nixos.org or gkze.cachix.org."""
    return check_path_on_nixos(store_path, run=run) or check_path_in_cachix(
        store_path, run=run
    )


def plan_darwin_warmup(
    flake_root: Path,
    *,
    manifest: RootClosureManifest,
    shards: tuple[ClosureShard, ...],
    run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    present: Callable[[str], bool] | None = None,
    threshold: int = MAX_SHARD_LOCAL_BUILDS,
) -> WarmupPlan:
    """Plan the missing shared Darwin outputs and fail closed on huge remainders."""
    roots = darwin_roots(manifest)
    per_root = {
        composed_root_name(root): eval_root_darwin_outputs(
            flake_root, composed_root_name(root), run=run
        )
        for root in roots
    }
    all_outputs = frozenset().union(*per_root.values()) if per_root else frozenset()
    checker = present if present is not None else default_cache_present
    substitutable = substitutable_paths(all_outputs, present=checker)
    warmup = intersect_missing(per_root, substitutable)
    reports: list[ShardLocalBuildReport] = []
    stats: dict[str, RootWarmupStats] = {}
    for name, outputs in per_root.items():
        missing = outputs - substitutable
        remaining = missing - warmup
        stats[name] = RootWarmupStats(
            outputs=len(outputs),
            missing=len(missing),
            warmup=len(missing & warmup),
            remaining=len(remaining),
        )
    for shard in shards:
        remaining = shard_remaining_outputs(
            shard, per_root, substitutable=substitutable, warmup=warmup
        )
        reports.append(
            ShardLocalBuildReport(
                shard=shard.shard,
                roots=shard.roots,
                remaining=len(remaining),
                remaining_rust_crates=sum(
                    1 for path in remaining if is_crate2nix_rust_output(path)
                ),
            )
        )
    assert_local_build_threshold(reports, threshold=threshold)
    ordered = tuple(sorted(warmup))
    return WarmupPlan(
        schemaVersion=1,
        system=_DARWIN_SYSTEM,
        substituters=(_NIXOS_CACHE, _GKZE_CACHE),
        warmupOutputs=ordered,
        perRoot=stats,
        shards=tuple(reports),
        notes=(
            "Intersection of per-root aarch64-darwin outputs absent from "
            "cache.nixos.org and gkze.cachix.org. Packages realizes this set "
            "so shards substitute the shared stdenv/rust graph. "
            f"warmup={len(ordered)} threshold={threshold} "
            "(provisional 2-wide shards; revisit 4-wide vs 2-wide by bytes "
            "written and update-runtime after this warmup lands)."
        ),
    )


def write_warmup_plan(path: Path, plan: WarmupPlan) -> None:
    """Write the packages-job warmup list next to the shard matrix."""
    atomic_write_text(
        path,
        plan.model_dump_json(by_alias=True, indent=2) + "\n",
        mkdir=True,
    )


def load_warmup_plan(path: Path) -> WarmupPlan:
    """Load a warmup plan or fail closed."""
    try:
        return WarmupPlan.model_validate_json(path.read_bytes())
    except (OSError, ValueError) as error:
        msg = f"invalid warmup plan: {path}"
        raise WarmupError(msg) from error


def realize_warmup_outputs(
    paths: Sequence[str],
    *,
    flake_root: Path,
    run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    progress: ValidationProgress | None = None,
    timeout: float | None = None,
) -> tuple[DerivationValidationFailure, ...]:
    """Build the warmup outputs so Cachix's post-build-hook pushes each path."""
    failures: list[DerivationValidationFailure] = []
    ordered = tuple(dict.fromkeys(paths))
    for start in range(0, len(ordered), _WARMUP_REALIZE_CHUNK):
        chunk = ordered[start : start + _WARMUP_REALIZE_CHUNK]
        requests = tuple(
            DerivationValidationRequest(
                source="root-warmup",
                installable=path,
                mode="build",
            )
            for path in chunk
        )
        failures.extend(
            validate_derivation_requests(
                requests,
                flake_root=flake_root,
                run=run,
                progress=progress,
                timeout=timeout,
                print_build_logs=False,
            )
        )
    return tuple(failures)
