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
from typing import TYPE_CHECKING, Literal

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

type _StoreRun = Callable[
    ..., subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]
]

WARMUP_PLAN_NAME = "warmup-plan.json"
WARMUP_DRVS_NAME = "warmup-drvs"
WARMUP_DRVS_CACHE_INFO = "nix-cache-info"
WARMUP_DRVS_ROOTS = "gcroots"
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
_STORE_COPY_CHUNK = 128
_SUBSTITUTER_WORKERS = 16
_NIXOS_CACHE = "https://cache.nixos.org"
_GKZE_CACHE = "https://gkze.cachix.org"
_STORE_PREFIX = "/nix/store/"
# Derivation-v4 JSON omits the store dir; older `nix derivation show`
# responses used absolute paths. Accept both so warmup does not collect
# zero Darwin outputs on Nix 2.35.
_STORE_BASENAME = re.compile(r"^[0-9a-z]{32}-.+$")
# rust_* family is a handful of crates. #1263 compiled 406 bootstrap
# drvs; fail the hosted job on the dry-run line instead of --keep-going.
WARMUP_FATAL_BUILD_LIMIT = 32
_WILL_BE_BUILT = re.compile(r"these (\d+) derivations? will be built", re.IGNORECASE)


class WarmupError(ValueError):
    """The planner could not produce a safe Darwin warmup set."""


class WarmupFatalError(WarmupError):
    """A streamed warmup/root line that must fail the hosted job immediately."""


def warmup_fatal_line(line: str) -> str | None:
    """Return why a live warmup/root log line must fail the job."""
    if "error[E04" in line:
        return "rustc SVH/crate mismatch"
    if "Cannot build" in line:
        return "cannot build"
    if "liveness" in line.lower():
        return "liveness"
    lowered = line.lower()
    if "cannot download" in lowered and "from any mirror" in lowered:
        return "cannot download from any mirror"
    match = _WILL_BE_BUILT.search(line)
    if match and int(match.group(1)) > WARMUP_FATAL_BUILD_LIMIT:
        return f"unexpectedly large will-be-built count ({match.group(1)})"
    return None


def raise_if_warmup_fatal(line: str) -> None:
    """Abort as soon as a fatal warmup/root pattern is streamed."""
    if reason := warmup_fatal_line(line):
        msg = f"{reason}: {line}"
        raise WarmupFatalError(msg)


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


def warmup_build_installable(path: str) -> str:
    """Return the installable that realizes warmup *outputs*, not the ``.drv`` file.

    ``nix build /nix/store/foo.drv`` only substitutes/realizes the derivation
    text. After a successful import that is a no-op and Cachix never sees
    rust_* outputs. ``37868270521`` then failed with ``don't know how to
    build these paths`` once auto-GC ate the unrooted import. ``foo.drv^*``
    is the output set; ``_batch_key`` already refuses to batch those nodes.
    """
    if path.endswith(".drv"):
        return f"{path}^*"
    return path


def _store_command_detail(
    result: subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes],
    *,
    fallback: str,
) -> str:
    """Return stderr/stdout from a nix process as text."""
    stderr = result.stderr
    stdout = result.stdout
    if isinstance(stderr, bytes):
        stderr = stderr.decode(errors="replace")
    if isinstance(stdout, bytes):
        stdout = stdout.decode(errors="replace")
    return stderr.strip() or stdout.strip() or fallback


def _file_store_uri(path: Path) -> str:
    """Return a ``file://`` URI for a local binary-cache directory."""
    return path.resolve().as_uri()


def _auto_gc_off_args() -> tuple[str, ...]:
    """Disable store auto-GC for copy/verify so unrooted imports are not eaten.

    Hosted ``update-runtime`` sets ``min-free = 32GiB``. macos-15 often has
    less free than that, so any store addition triggers GC. ``37868270521``
    ``nix-store --import`` exited 0, ``/nix`` stayed ~4.6Gi, and the first
    ``nix build`` could not see the just-imported ``.drv`` files.
    """
    return ("--option", "min-free", "0", "--option", "max-free", "0")


# ``nix-store --add-root`` is a modifier. Determinate Nix 3.22 on macos-15
# prints ``error: no operation specified`` unless an operation such as
# ``--realise`` is also present (``37879308953`` / #1256).
_NIX_STORE_OPERATIONS = frozenset({
    "--realise",
    "-r",
    "--query",
    "-q",
    "--add",
    "--delete",
    "--gc",
    "--dump",
    "--restore",
    "--export",
    "--import",
    "--verify",
    "--optimise",
    "--read-log",
    "-l",
    "--dump-db",
    "--load-db",
    "--print-env",
    "--serve",
})


def nix_store_argv_has_operation(args: Sequence[str]) -> bool:
    """Return whether a ``nix-store`` argv includes a real operation.

    The #1256 argv ``nix-store --add-root ROOT --indirect DRV`` does not.
    """
    return (
        bool(args)
        and args[0] == "nix-store"
        and bool(_NIX_STORE_OPERATIONS & set(args))
    )


def warmup_drv_root_args(root: Path, drv: str) -> list[str]:
    """Return argv that GC-roots an imported ``.drv`` file without building it.

    ``nix-store --realise --add-root`` on a ``.drv`` builds outputs. ``nix
    build --out-link`` realizes the already-copied ``.drv`` store path and
    registers the symlink as a GC root. That is the #1256-safe form.
    """
    return [
        "nix",
        "build",
        "--out-link",
        str(root),
        "--offline",
        *_auto_gc_off_args(),
        drv,
    ]


def _copy_derivation_closure(
    drv_paths: Sequence[str],
    store: Path,
    *,
    to_store: bool,
    run: _StoreRun | None = None,
) -> None:
    """Copy a derivation FS closure through a daemon-aware ``file://`` cache."""
    runner = subprocess.run if run is None else run
    uri = _file_store_uri(store)
    flag = "--to" if to_store else "--from"
    extra = () if to_store else ("--no-check-sigs",)
    ordered = tuple(dict.fromkeys(drv_paths))
    verb = "export" if to_store else "import"
    for start in range(0, len(ordered), _STORE_COPY_CHUNK):
        batch = ordered[start : start + _STORE_COPY_CHUNK]
        result = runner(
            [
                "nix",
                "copy",
                "--derivation",
                *extra,
                *_auto_gc_off_args(),
                flag,
                uri,
                *batch,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            detail = _store_command_detail(
                result, fallback="nix copy --derivation failed"
            )
            msg = f"failed to {verb} warmup .drv closure: {detail}"
            raise WarmupError(msg)


def _register_warmup_drv_roots(
    drv_paths: Sequence[str],
    roots_dir: Path,
    *,
    run: _StoreRun | None = None,
) -> None:
    """Pin imported ``.drv`` files so later ``min-free`` GC cannot drop them."""
    runner = subprocess.run if run is None else run
    roots_dir.mkdir(parents=True, exist_ok=True)
    for drv in dict.fromkeys(drv_paths):
        root = roots_dir / Path(drv).name
        result = runner(
            warmup_drv_root_args(root, drv),
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            detail = _store_command_detail(
                result, fallback="nix build --out-link failed"
            )
            msg = f"failed to GC-root imported warmup .drv {drv}: {detail}"
            raise WarmupError(msg)


def _require_store_paths(
    paths: Sequence[str],
    *,
    run: _StoreRun | None = None,
) -> None:
    """Fail closed unless every imported ``.drv`` is a live store object."""
    runner = subprocess.run if run is None else run
    ordered = tuple(dict.fromkeys(paths))
    for start in range(0, len(ordered), _STORE_COPY_CHUNK):
        batch = ordered[start : start + _STORE_COPY_CHUNK]
        result = runner(
            ["nix", "path-info", *_auto_gc_off_args(), *batch],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            detail = _store_command_detail(result, fallback="nix path-info failed")
            msg = f"imported warmup .drv missing from store: {detail}"
            raise WarmupError(msg)


def export_warmup_drvs(
    drv_paths: Sequence[str],
    dest: Path,
    *,
    run: _StoreRun | None = None,
) -> None:
    """Copy the planner's warmup derivation closure into a ``file://`` cache.

    ``nix-store --add`` of a copied ``.drv`` content-addresses a new path
    (``37851246740``). A concatenated ``nix-store --export`` NAR imported via
    Python ``input=`` returned 0 on macos-15 but did not leave buildable store
    objects (``37868270521``): auto-GC under ``min-free = 32GiB`` collected
    the unrooted dump, and ``nix build foo.drv`` would only have realized the
    ``.drv`` file anyway. ``nix copy --derivation`` talks to the daemon, keeps
    original store paths, and includes the eval-closure references.
    """
    dest.mkdir(parents=True, exist_ok=True)
    ordered = tuple(dict.fromkeys(drv_paths))
    if not ordered:
        return
    for drv in ordered:
        if not Path(drv).is_file():
            msg = f"planner is missing warmup .drv {drv}"
            raise WarmupError(msg)
    _copy_derivation_closure(ordered, dest, to_store=True, run=run)


def import_warmup_drvs(
    drv_paths: Sequence[str],
    cache: Path,
    *,
    run: _StoreRun | None = None,
) -> None:
    """Register the exported derivation closure and GC-root each ``.drv``."""
    if not drv_paths:
        return
    if not cache.is_dir():
        msg = f"warmup drv cache missing: {cache}"
        raise WarmupError(msg)
    info = cache / WARMUP_DRVS_CACHE_INFO
    if not info.is_file():
        msg = f"warmup drv cache missing {WARMUP_DRVS_CACHE_INFO}"
        raise WarmupError(msg)
    ordered = tuple(dict.fromkeys(drv_paths))
    _copy_derivation_closure(ordered, cache, to_store=False, run=run)
    _register_warmup_drv_roots(ordered, cache / WARMUP_DRVS_ROOTS, run=run)
    _require_store_paths(ordered, run=run)


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


def _store_output_rest(path: str) -> str:
    """Return the store basename after the 32-char hash, or the basename."""
    name = path.rstrip("/").rsplit("/", 1)[-1]
    _digest, separator, rest = name.partition("-")
    return rest if separator else name


def is_named_rust_crate_store_path(path: str, crate: str) -> bool:
    """Return whether *path* is crate2nix ``rust_<crate>-<version>``, not a sibling.

    ``rust_language_models-`` does not match ``rust_language_models_cloud-``.
    ``rust_zed-1.`` does not match ``rust_zed-font-kit`` or ``rust_zed_actions``.
    """
    rest = _store_output_rest(path)
    prefix = f"rust_{crate}-"
    if not rest.startswith(prefix):
        return False
    after = rest[len(prefix) :]
    return bool(after) and after[0].isdigit()


def is_rust_agent_ui_store_path(path: str) -> bool:
    """Return whether *path* is a crate2nix ``rust_agent_ui`` output or drv."""
    return is_named_rust_crate_store_path(path, "agent_ui")


def is_rust_language_models_store_path(path: str) -> bool:
    """Return whether *path* is crate2nix ``rust_language_models``, not ``_cloud``."""
    return is_named_rust_crate_store_path(path, "language_models")


def is_rust_zed_store_path(path: str) -> bool:
    """Return whether *path* is crate2nix ``rust_zed-<version>``, not ``zed_*``."""
    return is_named_rust_crate_store_path(path, "zed")


def is_zed_editor_nightly_store_path(path: str) -> bool:
    """Return whether *path* is the Zed nightly package, not a crate ``-src``."""
    rest = _store_output_rest(path)
    return rest.startswith("zed-editor-nightly-") and "-src" not in rest


# Cargo.nix crates that depend on ``extension_host`` plus ``title_bar``
# (depends on ``recent_projects``; rust_zed --externs it). rust_zed is the
# leaf and is realized after this set is compiled on the same runner.
EXTENSION_HOST_MEMBER_CRATES = (
    "activity_indicator",
    "agent_ui",
    "extension_host",
    "extensions_ui",
    "feedback",
    "language_models",
    "recent_projects",
    "remote_server",
    "settings_ui",
    "title_bar",
)


def is_extension_host_family_store_path(path: str) -> bool:
    """Return whether *path* is an ``extension_host`` SVH-family rust_* crate."""
    return any(
        is_named_rust_crate_store_path(path, crate)
        for crate in EXTENSION_HOST_MEMBER_CRATES
    )


def is_rust_extension_host_store_path(path: str) -> bool:
    """Return whether *path* is crate2nix ``rust_extension_host``."""
    return is_named_rust_crate_store_path(path, "extension_host")


def partition_agent_ui_drvs(
    drvs: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split warmup drvs so ``rust_agent_ui`` can realize last with ``-L``."""
    others: list[str] = []
    agent_ui: list[str] = []
    for drv in dict.fromkeys(drvs):
        if is_rust_agent_ui_store_path(drv):
            agent_ui.append(drv)
        else:
            others.append(drv)
    return tuple(others), tuple(agent_ui)


def partition_rust_zed_drvs(
    drvs: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split warmup drvs so ``rust_zed`` realizes after its extension_host family."""
    others: list[str] = []
    rust_zed: list[str] = []
    for drv in dict.fromkeys(drvs):
        if is_rust_zed_store_path(drv):
            rust_zed.append(drv)
        else:
            others.append(drv)
    return tuple(others), tuple(rust_zed)


def partition_zed_editor_nightly_drvs(
    drvs: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split warmup drvs so the nightly package cannot build rust_zed early."""
    others: list[str] = []
    nightly: list[str] = []
    for drv in dict.fromkeys(drvs):
        if is_zed_editor_nightly_store_path(drv):
            nightly.append(drv)
        else:
            others.append(drv)
    return tuple(others), tuple(nightly)


def is_svh_sensitive_store_path(path: str) -> bool:
    """Return whether substituting *path* can mix extension_host-family SVHs."""
    return (
        is_extension_host_family_store_path(path)
        or is_rust_zed_store_path(path)
        or is_zed_editor_nightly_store_path(path)
        or is_rust_agent_ui_store_path(path)
    )


def _warmup_outputs_matching(
    layers: Sequence[Sequence[str]],
    predicate: Callable[[str], bool],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(path for layer in layers for path in layer if predicate(path))
    )


def language_models_warmup_outputs(layers: Sequence[Sequence[str]]) -> tuple[str, ...]:
    """Return rust_language_models outputs from every warmup layer, first-seen."""
    return _warmup_outputs_matching(layers, is_rust_language_models_store_path)


def extension_host_family_warmup_outputs(
    layers: Sequence[Sequence[str]],
) -> tuple[str, ...]:
    """Return extension_host-family outputs from every warmup layer, first-seen."""
    return _warmup_outputs_matching(layers, is_extension_host_family_store_path)


def _query_drv_graph(
    parent_drvs: Sequence[str],
    operation: Literal["references", "requisites"],
    *,
    run: _StoreRun | None = None,
) -> tuple[str, ...]:
    """Return ``.drv`` ``--references`` or ``--requisites`` of *parent_drvs*."""
    runner = subprocess.run if run is None else run
    found: dict[str, None] = {}
    for drv in dict.fromkeys(parent_drvs):
        result = runner(
            ["nix-store", "--query", f"--{operation}", drv],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            detail = _store_command_detail(
                result, fallback=f"nix-store --query --{operation} failed"
            )
            msg = f"failed to query rust crate inputs of {drv}: {detail}"
            raise WarmupError(msg)
        stdout = result.stdout
        text = stdout.decode() if isinstance(stdout, bytes) else stdout
        for line in text.splitlines():
            path = line.strip()
            if path.endswith(".drv"):
                found.setdefault(path, None)
    return tuple(found)


def drv_input_references(
    parent_drvs: Sequence[str],
    *,
    run: _StoreRun | None = None,
) -> tuple[str, ...]:
    """Return direct ``.drv`` references of *parent_drvs* after import."""
    return _query_drv_graph(parent_drvs, "references", run=run)


def drv_input_requisites(
    parent_drvs: Sequence[str],
    *,
    run: _StoreRun | None = None,
) -> tuple[str, ...]:
    """Return the full ``.drv`` closure of *parent_drvs* after import."""
    return _query_drv_graph(parent_drvs, "requisites", run=run)


def rust_crate_input_drvs(
    parent_drvs: Sequence[str],
    crates: Sequence[str],
    *,
    run: _StoreRun | None = None,
) -> tuple[str, ...]:
    """Return named crate2nix ``.drv`` inputs of *parent_drvs* after import."""
    wanted = tuple(crates)
    return tuple(
        path
        for path in drv_input_references(parent_drvs, run=run)
        if any(is_named_rust_crate_store_path(path, crate) for crate in wanted)
    )


def compiler_input_drvs(
    parent_drvs: Sequence[str],
    *,
    run: _StoreRun | None = None,
) -> tuple[str, ...]:
    """Return the non-family ``.drv`` closure that must be substituted first.

    ``#1263`` used direct ``--references`` plus ``--fallback``, then
    ``--no-substitute`` on ``extension_host`` rebuilt 406 bootstrap
    drvs (bmake 404). Query ``--requisites`` and realize them with
    ``--max-jobs 0`` so only the SVH-sensitive rust_* compile.
    """
    return tuple(
        path
        for path in drv_input_requisites(parent_drvs, run=run)
        if not is_svh_sensitive_store_path(path)
    )


_SOURCE_FETCH_ARCHIVES = (
    ".tar.gz",
    ".tar.bz2",
    ".tar.xz",
    ".tar.zst",
    ".tgz",
    ".zip",
    ".crate",
)


def is_source_fetch_store_path(path: str) -> bool:
    """Return whether *path* is a fetchurl/FOD archive, not a compile.

    ``#1264`` ``--max-jobs 0`` refused ``coreaudio-rs-0.14.2.tar.gz.drv``
    because a cache miss on a crate tarball is a download, not bootstrap.
    """
    name = _store_output_rest(path).removesuffix(".drv")
    if name.endswith(_SOURCE_FETCH_ARCHIVES):
        return True
    return name == "source" or name.endswith(("-src", "-source"))


def partition_compiler_input_drvs(
    drvs: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split compiler requisites into substitute-only vs source-fetch FODs."""
    substitute: list[str] = []
    fetches: list[str] = []
    for drv in dict.fromkeys(drvs):
        if is_source_fetch_store_path(drv):
            fetches.append(drv)
        else:
            substitute.append(drv)
    return tuple(substitute), tuple(fetches)


_DRV_PATH = re.compile(r"/nix/store/[0-9a-z]{32}-[^/\s]+\.drv")


def is_force_local_allowed_build(path: str) -> bool:
    """Return whether a ``--dry-run --no-substitute`` build may compile *path*."""
    if is_svh_sensitive_store_path(path):
        return True
    rest = _store_output_rest(path)
    if "-src" not in rest:
        return False
    return (
        "zed-editor-nightly" in rest
        or "extension_host" in rest
        or any(crate in rest for crate in EXTENSION_HOST_MEMBER_CRATES)
    )


def force_local_dry_run_builds(
    drvs: Sequence[str],
    *,
    run: _StoreRun | None = None,
) -> tuple[str, ...]:
    """Return ``.drv``s ``nix build --dry-run --no-substitute`` would compile."""
    runner = subprocess.run if run is None else run
    found: dict[str, None] = {}
    for drv in dict.fromkeys(drvs):
        result = runner(
            [
                "nix",
                "build",
                "--dry-run",
                "--no-link",
                "--no-substitute",
                warmup_build_installable(drv),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            detail = _store_command_detail(
                result, fallback="nix build --dry-run --no-substitute failed"
            )
            msg = f"failed to dry-run force-local {drv}: {detail}"
            raise WarmupError(msg)
        stdout = result.stdout
        stderr = result.stderr
        out = stdout.decode() if isinstance(stdout, bytes) else stdout
        err = stderr.decode() if isinstance(stderr, bytes) else stderr
        in_section = False
        for line in f"{out}\n{err}".splitlines():
            if "will be built:" in line:
                in_section = True
                continue
            if not in_section:
                continue
            match = _DRV_PATH.search(line)
            if match:
                found.setdefault(match.group(0), None)
                continue
            if line.strip() == "" or not line.lstrip().startswith("/nix/store/"):
                in_section = False
    return tuple(found)


def assert_force_local_dry_run(
    drvs: Sequence[str],
    *,
    run: _StoreRun | None = None,
) -> None:
    """Fail closed if force-local would compile stdenv or other non-family drvs."""
    extra = tuple(
        path
        for path in force_local_dry_run_builds(drvs, run=run)
        if not is_force_local_allowed_build(path)
    )
    if extra:
        sample = ", ".join(extra[:8])
        msg = f"force-local would compile non-family: {sample}"
        raise WarmupError(msg)


def language_models_input_drvs(
    agent_ui_drvs: Sequence[str],
    *,
    run: _StoreRun | None = None,
) -> tuple[str, ...]:
    """Return ``rust_language_models`` ``.drv`` inputs of *agent_ui_drvs*."""
    return rust_crate_input_drvs(agent_ui_drvs, ("language_models",), run=run)


def query_drv_outputs(
    drv: str,
    *,
    run: _StoreRun | None = None,
) -> tuple[str, ...]:
    """Return store output paths of one ``.drv``."""
    runner = subprocess.run if run is None else run
    result = runner(
        ["nix-store", "--query", "--outputs", drv],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = _store_command_detail(
            result, fallback="nix-store --query --outputs failed"
        )
        msg = f"failed to query outputs of {drv}: {detail}"
        raise WarmupError(msg)
    stdout = result.stdout
    text = stdout.decode() if isinstance(stdout, bytes) else stdout
    return tuple(line.strip() for line in text.splitlines() if line.strip())


def realize_warmup_outputs(
    paths: Sequence[str],
    *,
    flake_root: Path,
    run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    progress: ValidationProgress | None = None,
    timeout: float | None = None,
    print_build_logs: bool = False,
    force_local: bool = False,
    substitute_only: bool = False,
) -> tuple[DerivationValidationFailure, ...]:
    """Build the warmup outputs so Cachix's post-build-hook pushes each path.

    ``substitute_only`` is ``--max-jobs 0`` for the non-family closure.
    ``force_local`` is ``--no-substitute`` on SVH-sensitive rust_* only.
    Do not combine them: #1263 ``--no-substitute`` on ``extension_host``
    rebuilt 406 bootstrap drvs after a partial ``--fallback``.
    """
    if force_local and substitute_only:
        msg = "realize_warmup_outputs cannot be force_local and substitute_only"
        raise WarmupError(msg)
    failures: list[DerivationValidationFailure] = []
    ordered = tuple(dict.fromkeys(paths))
    for start in range(0, len(ordered), _WARMUP_REALIZE_CHUNK):
        chunk = ordered[start : start + _WARMUP_REALIZE_CHUNK]
        requests = tuple(
            DerivationValidationRequest(
                source="root-warmup",
                installable=warmup_build_installable(path),
                mode="build",
                no_substitute=force_local,
                substitute_only=substitute_only,
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
                print_build_logs=print_build_logs,
            )
        )
    return tuple(failures)
