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
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
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

    from lib.update.derivation_validation import ValidationProgress

WARMUP_PLAN_NAME = "warmup-plan.json"
WARMUP_DRVS_NAME = "warmup-drvs"
_DARWIN_SYSTEM = "aarch64-darwin"
# Hosted packages on 37740898487 realized 132 local zed drvs. Home-george
# compiled 1534 rust_* because those hashes were not the packages graph.
# After intersection warmup, a shard should only compile host-unique leaves.
# 400 is packages-scale plus slack; 1534 rust_* fails this gate.
MAX_SHARD_LOCAL_BUILDS = 400
# Public macos-15 cap is 5. rust_* warmup owns those slots, partitioned by
# crate2nix dependency layers so later crates can substitute earlier ones.
RUST_WARMUP_SLOTS = 5
_WARMUP_REALIZE_CHUNK = 128
_SUBSTITUTER_WORKERS = 16
_NIXOS_CACHE = "https://cache.nixos.org"
_GKZE_CACHE = "https://gkze.cachix.org"
_STORE_PREFIX = "/nix/store/"
# Derivation-v4 JSON omits the store dir; older `nix derivation show`
# responses used absolute paths. Accept both so warmup does not collect
# zero Darwin outputs on Nix 2.35.
_STORE_BASENAME = re.compile(r"^[0-9a-z]{32}-.+$")


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
    """Intersection of missing Darwin root outputs rust-warmup realizes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(alias="schemaVersion")
    system: str
    substituters: tuple[str, ...]
    warmup_outputs: tuple[str, ...] = Field(alias="warmupOutputs")
    rust_layers: tuple[tuple[str, ...], ...] = Field(alias="rustLayers", default=())
    output_drvs: dict[str, str] = Field(alias="outputDrvs", default_factory=dict)
    per_root: dict[str, RootWarmupStats] = Field(alias="perRoot")
    shards: tuple[ShardLocalBuildReport, ...]
    notes: str


def is_crate2nix_rust_output(store_path: str) -> bool:
    """Return whether *store_path* is a crate2nix ``rust_*`` output."""
    name = store_path.rsplit("/", 1)[-1]
    _hash, separator, rest = name.partition("-")
    return bool(separator) and rest.startswith("rust_")


def _absolute_store_path(path: str) -> str | None:
    """Return a full store path, or None if *path* is not a store output."""
    if path.startswith(_STORE_PREFIX) and path != _STORE_PREFIX:
        return path
    if _STORE_BASENAME.fullmatch(path):
        return f"{_STORE_PREFIX}{path}"
    return None


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
            raw = output.get("path")
            if not isinstance(raw, str):
                continue
            path = _absolute_store_path(raw)
            if path is not None:
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


def eval_root_darwin_graph(
    flake_root: Path,
    root_name: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, object]:
    """Instantiate one Darwin root and return the recursive derivation JSON."""
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
    return payload


def eval_root_darwin_outputs(
    flake_root: Path,
    root_name: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> frozenset[str]:
    """Instantiate one Darwin root's drv graph and return Darwin output paths."""
    return darwin_output_paths(eval_root_darwin_graph(flake_root, root_name, run=run))


def _drv_basename(name: str) -> str:
    """Return the store basename of a derivation path or key."""
    return name.rsplit("/", 1)[-1]


def _input_drv_keys(drv: Mapping[str, object]) -> frozenset[str]:
    """Return input derivation basenames from v3 ``inputDrvs`` or v4 ``inputs``."""
    inputs = drv.get("inputs")
    if isinstance(inputs, dict):
        drvs = inputs.get("drvs")
        if isinstance(drvs, dict):
            return frozenset(_drv_basename(key) for key in drvs)
    raw = drv.get("inputDrvs")
    if isinstance(raw, dict):
        return frozenset(_drv_basename(key) for key in raw)
    return frozenset()


def rust_warmup_layers(
    warmup: frozenset[str],
    payloads: Sequence[Mapping[str, object]],
) -> tuple[tuple[str, ...], ...]:
    """Layer rust_* warmup outputs by crate2nix ``inputDrvs``; prelude is the rest."""
    rust_warmup = frozenset(path for path in warmup if is_crate2nix_rust_output(path))
    prelude = tuple(sorted(path for path in warmup if path not in rust_warmup))
    if not rust_warmup:
        return (prelude,) if prelude else ()
    path_to_drv: dict[str, str] = {}
    drv_outputs: dict[str, set[str]] = {}
    drv_inputs: dict[str, set[str]] = {}
    for payload in payloads:
        derivations = payload.get("derivations")
        if not isinstance(derivations, dict):
            msg = "derivation graph missing derivations"
            raise WarmupError(msg)
        for raw_name, drv in derivations.items():
            if not isinstance(raw_name, str) or not isinstance(drv, dict):
                continue
            if drv.get("system") != _DARWIN_SYSTEM:
                continue
            outputs = drv.get("outputs")
            if not isinstance(outputs, dict):
                continue
            key = _drv_basename(raw_name)
            for output in outputs.values():
                if not isinstance(output, dict):
                    continue
                raw = output.get("path")
                if not isinstance(raw, str):
                    continue
                path = _absolute_store_path(raw)
                if path is None or path not in rust_warmup:
                    continue
                path_to_drv[path] = key
                drv_outputs.setdefault(key, set()).add(path)
            drv_inputs.setdefault(key, set()).update(_input_drv_keys(drv))
    missing = rust_warmup - frozenset(path_to_drv)
    if missing:
        sample = ", ".join(sorted(missing)[:8])
        msg = f"rust_* warmup paths missing from Darwin graphs: {sample}"
        raise WarmupError(msg)
    rust_drvs = frozenset(drv_outputs)
    deps = {
        drv: frozenset(dep for dep in drv_inputs.get(drv, set()) if dep in rust_drvs)
        for drv in rust_drvs
    }
    remaining = set(rust_drvs)
    layers: list[tuple[str, ...]] = []
    while remaining:
        ready = sorted(drv for drv in remaining if not (deps[drv] & remaining))
        if not ready:
            sample = ", ".join(sorted(remaining)[:8])
            msg = f"rust_* warmup dependency cycle: {sample}"
            raise WarmupError(msg)
        layer_paths = tuple(path for drv in ready for path in sorted(drv_outputs[drv]))
        layers.append(layer_paths)
        remaining.difference_update(ready)
    if prelude:
        return (prelude, *layers)
    return tuple(layers)


def warmup_output_drvs(
    warmup: frozenset[str],
    payloads: Sequence[Mapping[str, object]],
) -> dict[str, str]:
    """Map each warmup output to the Darwin ``.drv`` that produces it."""
    mapping: dict[str, str] = {}
    for payload in payloads:
        derivations = payload.get("derivations")
        if not isinstance(derivations, dict):
            msg = "derivation graph missing derivations"
            raise WarmupError(msg)
        for raw_name, drv in derivations.items():
            if not isinstance(raw_name, str) or not isinstance(drv, dict):
                continue
            if drv.get("system") != _DARWIN_SYSTEM:
                continue
            drv_path = _absolute_store_path(raw_name)
            if drv_path is None or not drv_path.endswith(".drv"):
                continue
            outputs = drv.get("outputs")
            if not isinstance(outputs, dict):
                continue
            for output in outputs.values():
                if not isinstance(output, dict):
                    continue
                raw = output.get("path")
                if not isinstance(raw, str):
                    continue
                path = _absolute_store_path(raw)
                if path is None or path not in warmup:
                    continue
                mapping[path] = drv_path
    missing = warmup - frozenset(mapping)
    if missing:
        sample = ", ".join(sorted(missing)[:8])
        msg = f"warmup outputs missing Darwin .drv mapping: {sample}"
        raise WarmupError(msg)
    return mapping


def unique_drvs_for_outputs(
    outputs: Sequence[str],
    output_drvs: Mapping[str, str],
) -> tuple[str, ...]:
    """Return first-seen ``.drv`` paths for *outputs*, failing closed on gaps."""
    missing = [path for path in outputs if path not in output_drvs]
    if missing:
        sample = ", ".join(missing[:8])
        msg = f"warmup outputs missing Darwin .drv mapping: {sample}"
        raise WarmupError(msg)
    seen: dict[str, None] = {}
    for path in outputs:
        seen.setdefault(output_drvs[path], None)
    return tuple(seen)


def export_warmup_drvs(drv_paths: Sequence[str], dest: Path) -> None:
    """Copy planner-instantiated ``.drv`` files next to warmup-plan.json."""
    dest.mkdir(parents=True, exist_ok=True)
    for drv in dict.fromkeys(drv_paths):
        source = Path(drv)
        if not source.is_file():
            msg = f"planner is missing warmup .drv {drv}"
            raise WarmupError(msg)
        target = dest / source.name
        target.write_bytes(source.read_bytes())


def import_warmup_drvs(
    drv_paths: Sequence[str],
    cache: Path,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> None:
    """Register copied ``.drv`` files so Darwin can ``nix build`` them."""
    if not drv_paths:
        return
    if not cache.is_dir():
        msg = f"warmup drv cache missing: {cache}"
        raise WarmupError(msg)
    runner = subprocess.run if run is None else run
    for drv in dict.fromkeys(drv_paths):
        source = cache / Path(drv).name
        if not source.is_file():
            msg = f"warmup drv cache missing {Path(drv).name}"
            raise WarmupError(msg)
        result = runner(
            ["nix-store", "--add", str(source)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip() or "nix-store --add failed"
            msg = f"failed to import warmup .drv {source.name}: {detail}"
            raise WarmupError(msg)


def slot_warmup_paths(
    layers: Sequence[Sequence[str]],
    slot: int,
    *,
    width: int = RUST_WARMUP_SLOTS,
) -> tuple[str, ...]:
    """Return this matrix slot's stripe of each warmup layer, in layer order."""
    if width < 1:
        msg = "rust warmup width must be at least 1"
        raise WarmupError(msg)
    if not 0 <= slot < width:
        msg = f"rust warmup slot must be in 0..{width - 1}, got {slot}"
        raise WarmupError(msg)
    return tuple(path for layer in layers for path in tuple(layer)[slot::width])


def skip_cached_warmup_paths(
    paths: Sequence[str],
    *,
    present: Callable[[str], bool],
) -> tuple[str, ...]:
    """Drop paths already in gkze so a later run resumes the same stripe."""
    return tuple(path for path in dict.fromkeys(paths) if not present(path))


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
    graphs: dict[str, dict[str, object]] = {}
    per_root: dict[str, frozenset[str]] = {}
    for root in roots:
        name = composed_root_name(root)
        payload = eval_root_darwin_graph(flake_root, name, run=run)
        graphs[name] = payload
        per_root[name] = darwin_output_paths(payload)
    all_outputs = frozenset().union(*per_root.values()) if per_root else frozenset()
    checker = present if present is not None else default_cache_present
    substitutable = substitutable_paths(all_outputs, present=checker)
    warmup = intersect_missing(per_root, substitutable)
    rust_layers = rust_warmup_layers(warmup, tuple(graphs.values()))
    output_drvs = warmup_output_drvs(warmup, tuple(graphs.values()))
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
        rustLayers=rust_layers,
        outputDrvs=output_drvs,
        perRoot=stats,
        shards=tuple(reports),
        notes=(
            "Intersection of per-root aarch64-darwin outputs absent from "
            "cache.nixos.org and gkze.cachix.org. Five macos-15 rust-warmup "
            "slots realize rust_* by dependency layer (skip-if-in-gkze); "
            "packages inventory stays certify evidence and does not serialize "
            "this set. "
            f"warmup={len(ordered)} rustLayers={len(rust_layers)} "
            f"slots={RUST_WARMUP_SLOTS} threshold={threshold} "
            "(provisional 2-wide root shards; revisit 4-wide vs 2-wide by "
            "bytes written and update-runtime after this warmup lands)."
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
