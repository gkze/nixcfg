"""Fail-closed Update coverage: every planned root and package reached Cachix.

The root inventory is ``lib.rootClosureManifest``. The shard plan is
``lib.update.ci.shard_plan``. This module is the only place that decides
whether a run covered that inventory. A missing, skipped, unbuilt, or
unpushed root fails the run.

``plan-shards`` evaluates root ``outPath``s once and writes
``root-out-paths.json``. Darwin host Cachix queries use each shard's
``realized-root-paths.json`` (native eval + flush), not the Linux
planner hashes. Coverage never re-evaluates Darwin out paths.
"""

import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lib.update.ci.shard_plan import (
    ClosureShard,
    ClosureShardReceipt,
    ShardCosts,
    composed_root_name,
    darwin_roots,
    plan_darwin_closure_shards,
)
from lib.update.derivation_validation import (
    RootClosureManifest,
    root_closure_check_attr,
    root_closure_installable,
)
from lib.update.derivation_validation import (
    composed_root_name as compose_kind_name,
)
from lib.update.io import atomic_write_text
from lib.update.nix import get_current_nix_platform

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from pathlib import Path

_BINARY_CACHE_STORE = "https://gkze.cachix.org"
_NIXOS_CACHE_STORE = "https://cache.nixos.org"
_PATH_INFO_CHUNK = 128
_PATH_INFO_TIMEOUT_SECONDS = 30.0
_PATH_INFO_PER_PATH_TIMEOUT_SECONDS = 10.0
_PATH_INFO_BATCH_RETRIES = 1
_PATH_INFO_CHUNK_WORKERS = 8
_PATH_INFO_FALLBACK_WORKERS = 16
ROOT_OUT_PATHS_NAME = "root-out-paths.json"
CACHE_ROOT_DEPS_NAME = "cache-root-deps.json"
REALIZED_ROOT_PATHS_NAME = "realized-root-paths.json"
_LINUX_BUILDER_OUTPUT = "nixos-disk-image"
_REQUIRED_JOBS = (
    "plan-darwin-closures",
    "validate-arm",
    "validate-x86",
    "validate-darwin-warm-rust",
    "validate-darwin-cascade-warmup",
    "validate-darwin-packages",
    "validate-darwin-roots",
    "validate-darwin-closures",
)
_PACKAGE_REPORTS = (
    ("validate-aarch64-linux", "aarch64-linux"),
    ("validate-x86_64-linux", "x86_64-linux"),
    ("validate-aarch64-darwin-packages", "aarch64-darwin"),
)


class GateReport(BaseModel):
    """Certify-bound validation fields coverage inspects."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    tree: str
    system: str
    validate_all_packages: bool = False
    gates: tuple[str, ...]
    failures: tuple[object, ...]
    planned_installables: tuple[str, ...] = ()


class CoverageError(RuntimeError):
    """The run missed a required root, package, shard, or Cachix path."""


class RootOutPathCache(BaseModel):
    """Planner-evaluated root out paths reused by ``assert-coverage``."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    tree: str
    root_paths: dict[str, str] = Field(alias="rootPaths")
    manifest: RootClosureManifest

    @model_validator(mode="after")
    def require_store_paths(self) -> RootOutPathCache:
        """Refuse an empty or non-store cache so coverage cannot re-eval."""
        if not self.root_paths:
            msg = "root out-path cache has no paths"
            raise ValueError(msg)
        bad = [
            f"{name}={path}"
            for name, path in self.root_paths.items()
            if not path.startswith("/nix/store/")
        ]
        if bad:
            msg = f"root out-path cache contains a non-store path: {', '.join(bad)}"
            raise ValueError(msg)
        return self


def parse_job_results(raw: str) -> dict[str, str]:
    """Parse ``name=result`` lines from the coverage job environment."""
    results: dict[str, str] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name, separator, value = stripped.partition("=")
        if not separator or not name:
            msg = f"invalid job result line: {stripped}"
            raise CoverageError(msg)
        results[name] = value
    return results


def require_required_jobs(results: Mapping[str, str]) -> None:
    """Fail if a required validator was skipped, cancelled, or failed."""
    missing = [name for name in _REQUIRED_JOBS if name not in results]
    if missing:
        msg = f"coverage job results omitted required jobs: {', '.join(missing)}"
        raise CoverageError(msg)
    bad = [
        f"{name}={results[name]}"
        for name in _REQUIRED_JOBS
        if results[name] != "success"
    ]
    if bad:
        msg = f"required Update jobs did not succeed: {', '.join(bad)}"
        raise CoverageError(msg)


def load_validation_report(path: Path) -> GateReport:
    """Load one certify-bound validation report or fail closed."""
    try:
        return GateReport.model_validate_json(path.read_bytes())
    except (OSError, ValueError) as error:
        msg = f"invalid validation report: {path}"
        raise CoverageError(msg) from error


def load_shard_receipt(path: Path) -> ClosureShardReceipt:
    """Load one Darwin root-shard receipt or fail closed."""
    try:
        return ClosureShardReceipt.model_validate_json(path.read_bytes())
    except (OSError, ValueError) as error:
        msg = f"invalid closure shard receipt: {path}"
        raise CoverageError(msg) from error


def shard_artifact_name(shard: ClosureShard) -> str:
    """Return the Actions artifact directory for one root shard."""
    return f"validate-{shard.system}-closure-shard-{shard.shard}"


def require_validation_reports(
    evidence: Path,
    *,
    tree: str,
    validate_all_packages: bool,
) -> list[GateReport]:
    """Require Linux + Darwin package reports and the Darwin aggregate."""
    reports: list[GateReport] = []
    required = (
        *_PACKAGE_REPORTS,
        ("validate-aarch64-darwin-closures", "aarch64-darwin"),
    )
    for directory, system in required:
        path = evidence / directory / "validation.json"
        if not path.is_file():
            msg = f"missing validation report: {path}"
            raise CoverageError(msg)
        report = load_validation_report(path)
        if report.tree != tree or report.system != system or report.failures:
            msg = f"validation report is not publishable evidence: {path}"
            raise CoverageError(msg)
        if report.validate_all_packages != validate_all_packages:
            msg = f"validation report narrowed package inventory: {path}"
            raise CoverageError(msg)
        reports.append(report)
    packages = reports[0]
    linux_x86 = reports[1]
    darwin_packages = reports[2]
    closures = reports[3]
    if "packages" not in packages.gates or "closures" not in packages.gates:
        msg = "aarch64-linux validation omitted a required gate"
        raise CoverageError(msg)
    if "packages" not in linux_x86.gates or "closures" not in linux_x86.gates:
        msg = "x86_64-linux validation omitted a required gate"
        raise CoverageError(msg)
    if darwin_packages.gates != ("packages",):
        msg = "Darwin package validation must report only the packages gate"
        raise CoverageError(msg)
    if closures.gates != ("closures",):
        msg = "Darwin aggregate validation must report only the closures gate"
        raise CoverageError(msg)
    return reports


def require_package_inventory(
    reports: Sequence[GateReport],
    expected: Mapping[str, frozenset[str]],
) -> None:
    """Fail if a packages gate silently dropped declared installables."""
    for report in reports:
        if "packages" not in report.gates:
            continue
        planned = frozenset(report.planned_installables)
        wanted = expected.get(report.system)
        if wanted is None:
            msg = f"no declared package inventory for {report.system}"
            raise CoverageError(msg)
        if planned != wanted:
            missing = ", ".join(sorted(wanted - planned)) or "<none>"
            extra = ", ".join(sorted(planned - wanted)) or "<none>"
            msg = (
                f"{report.system} package inventory drifted "
                f"(missing={missing}; extra={extra})"
            )
            raise CoverageError(msg)
        if report.validate_all_packages and not planned:
            msg = f"{report.system} claimed all packages but planned none"
            raise CoverageError(msg)


def require_shard_receipts(
    evidence: Path,
    shards: tuple[ClosureShard, ...],
    *,
    tree: str,
) -> list[ClosureShardReceipt]:
    """Require a successful receipt for every planned Darwin shard."""
    receipts: list[ClosureShardReceipt] = []
    for shard in shards:
        path = evidence / shard_artifact_name(shard) / "shard-receipt.json"
        if not path.is_file():
            msg = f"missing closure shard receipt: {path}"
            raise CoverageError(msg)
        receipt = load_shard_receipt(path)
        if (
            receipt.tree != tree
            or receipt.system != shard.system
            or receipt.shard != shard.shard
            or tuple(receipt.roots) != shard.roots
            or receipt.failures
        ):
            msg = f"closure shard receipt is not complete: {path}"
            raise CoverageError(msg)
        receipts.append(receipt)
    return receipts


def require_cachix_paths(
    paths: Mapping[str, str],
    *,
    present: Callable[[str], bool],
) -> None:
    """Fail if any required store path is absent from gkze.cachix.org."""
    missing = [f"{name}={path}" for name, path in paths.items() if not present(path)]
    if missing:
        msg = f"required paths missing from gkze.cachix.org: {', '.join(missing)}"
        raise CoverageError(msg)


def linux_builder_store_paths(evidence: Path, *, tree: str) -> dict[str, str]:
    """Return recorded linux-builder outputs from cache-root-deps receipts."""
    if not evidence.is_dir():
        msg = f"coverage evidence is not a directory: {evidence}"
        raise CoverageError(msg)
    receipts = sorted(
        path for path in evidence.rglob(CACHE_ROOT_DEPS_NAME) if path.is_file()
    )
    if not receipts:
        msg = f"no {CACHE_ROOT_DEPS_NAME} under {evidence}"
        raise CoverageError(msg)
    paths: dict[str, str] = {}
    for receipt in receipts:
        try:
            payload = json.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            msg = f"invalid {CACHE_ROOT_DEPS_NAME}: {receipt}"
            raise CoverageError(msg) from error
        if not isinstance(payload, dict):
            msg = f"invalid {CACHE_ROOT_DEPS_NAME}: {receipt}"
            raise CoverageError(msg)
        reported_tree = payload.get("tree")
        if reported_tree != tree:
            msg = f"{receipt} tree {reported_tree!r} does not match candidate {tree!r}"
            raise CoverageError(msg)
        system = payload.get("system")
        raw = payload.get("store_paths")
        if not isinstance(system, str) or not system:
            msg = f"{receipt} is missing a native system"
            raise CoverageError(msg)
        if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
            # 39006154683: receipts had no store_paths because substituting
            # the VM from gkze never fired the post-build hook.
            msg = (
                f"{receipt} did not record realized linux-builder outputs "
                "(substituted paths were not re-pushed)"
            )
            raise CoverageError(msg)
        images = [item for item in raw if _LINUX_BUILDER_OUTPUT in item]
        if len(images) > 1:
            msg = f"{receipt} recorded multiple linux-builder outputs: {images}"
            raise CoverageError(msg)
        if images:
            paths[f"linux-builder:{system}"] = images[0]
    if "linux-builder:aarch64-linux" not in paths:
        msg = (
            "cache-root-deps did not record the aarch64-linux linux-builder "
            "image output"
        )
        raise CoverageError(msg)
    return paths


def _realized_flush_store_paths(path: Path) -> tuple[str, ...]:
    """Return store paths flush recorded next to a Darwin shard receipt."""
    if not path.is_file():
        return ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        msg = f"invalid {REALIZED_ROOT_PATHS_NAME}: {path}"
        raise CoverageError(msg) from error
    raw = payload.get("storePaths") if isinstance(payload, dict) else None
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        msg = f"invalid {REALIZED_ROOT_PATHS_NAME}: {path}"
        raise CoverageError(msg)
    return tuple(item for item in raw if item.startswith("/nix/store/"))


def realized_darwin_root_store_paths(evidence: Path) -> dict[str, str]:
    """Return Darwin-flushed root out paths keyed by shard root name.

    39041243758: ``plan-shards`` on x86_64-linux eval'd
    ``qd427pyva2w…-darwin-system`` while macos-15 realized
    ``3jid4n7w…-darwin-system``. Flush published the Darwin path; a
    Linux-eval phantom 404'd the canary and skipped closures.
    """
    if not evidence.is_dir():
        msg = f"coverage evidence is not a directory: {evidence}"
        raise CoverageError(msg)
    paths: dict[str, str] = {}
    for receipt_path in sorted(
        path for path in evidence.rglob("shard-receipt.json") if path.is_file()
    ):
        receipt = load_shard_receipt(receipt_path)
        if not receipt.system.endswith("-darwin") or receipt.failures:
            continue
        realized = _realized_flush_store_paths(
            receipt_path.parent / REALIZED_ROOT_PATHS_NAME
        )
        if len(receipt.roots) == 1 and len(realized) == 1:
            paths[receipt.roots[0]] = realized[0]
            continue
        if len(receipt.roots) == 1 and not realized:
            continue
        msg = (
            f"{receipt_path} Darwin shard realized {len(realized)} store paths "
            f"for roots {list(receipt.roots)}"
        )
        raise CoverageError(msg)
    return paths


def one_darwin_root_store_path(evidence: Path) -> dict[str, str]:
    """Return one Darwin-flushed root outPath for the pre-closures canary."""
    realized = realized_darwin_root_store_paths(evidence)
    if "darwin-argus" in realized:
        return {"darwin-argus": realized["darwin-argus"]}
    if not realized:
        msg = "no realized Darwin root store path under evidence"
        raise CoverageError(msg)
    name = min(realized)
    return {name: realized[name]}


def cachix_root_paths(
    *,
    planned: Mapping[str, str],
    evidence: Path,
    darwin_root_names: set[str],
) -> dict[str, str]:
    """Prefer Darwin-flushed out paths over Linux-eval'd planner hashes."""
    flushed = realized_darwin_root_store_paths(evidence)
    missing = darwin_root_names - set(flushed)
    if missing:
        msg = "Darwin shards did not record realized store paths for: " + ", ".join(
            sorted(missing)
        )
        raise CoverageError(msg)
    paths = {
        name: path
        for name, path in planned.items()
        if not name.startswith("aggregate:")
    }
    paths.update(flushed)
    return paths


def assert_cached_darwin_boundary(
    evidence: Path,
    *,
    tree: str,
    cachix_present: Callable[[str], bool] | None = None,
) -> None:
    """Fail unless the linux-builder image and one Darwin root are in Cachix."""
    present = check_path_in_cachix if cachix_present is None else cachix_present
    load_planned_root_out_paths(evidence, tree=tree)
    required = linux_builder_store_paths(evidence, tree=tree)
    required.update(one_darwin_root_store_path(evidence))
    require_cachix_paths(required, present=present)


def _path_on_store(
    store_path: str,
    store: str,
    *,
    run: Callable[..., object] | None = None,
    timeout: float = _PATH_INFO_PER_PATH_TIMEOUT_SECONDS,
) -> bool:
    """Return whether *store_path* is present on one Nix HTTP binary cache.

    Timeout or an unreachable store is a miss. Coverage and skip-cached
    warmup must not treat a hung query as present.
    """
    runner = subprocess.run if run is None else run
    try:
        result = runner(
            [
                "nix",
                "path-info",
                "--store",
                store,
                store_path,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except OSError, subprocess.TimeoutExpired:
        return False
    return getattr(result, "returncode", 1) == 0


def _present_paths_from_path_info_json(payload: object) -> frozenset[str]:
    """Return store paths reported present in ``nix path-info --json`` output.

    Determinate Nix 3.22.5 V1 JSON still includes missing paths as keys
    whose value is ``null`` (``InvalidPath`` in ``path-info.cc``). Only a
    JSON object is a hit. Treating every key as present would skip rust
    and cascade warmup and let Darwin roots compile those outputs.
    """
    if isinstance(payload, dict):
        return frozenset(
            str(key) for key, info in payload.items() if isinstance(info, dict)
        )
    if not isinstance(payload, list):
        return frozenset()
    found: set[str] = set()
    for item in payload:
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            found.add(item["path"])
    return frozenset(found)


def paths_on_store(
    store_paths: Iterable[str],
    store: str,
    *,
    run: Callable[..., object] | None = None,
    chunk: int = _PATH_INFO_CHUNK,
    timeout: float = _PATH_INFO_TIMEOUT_SECONDS,
    path_timeout: float = _PATH_INFO_PER_PATH_TIMEOUT_SECONDS,
    retries: int = _PATH_INFO_BATCH_RETRIES,
    workers: int = _PATH_INFO_FALLBACK_WORKERS,
    chunk_workers: int = _PATH_INFO_CHUNK_WORKERS,
) -> frozenset[str]:
    """Return the subset of *store_paths* one HTTP cache already has.

    ``nix path-info --json --store`` is one process per chunk. Missing paths
    are ``null`` values (Determinate) or omitted keys, never a presence
    claim. A hung or empty batch is retried once. Timeout after that retry
    is a miss for the whole chunk. Non-JSON still falls back, but per-path
    and bounded-parallel, never a silent sequential scan of thousands of
    rust_* paths. Chunks themselves run *chunk_workers* wide so a full
    Darwin output set cannot serialize for an hour on successful 10s
    queries.
    """
    ordered = tuple(dict.fromkeys(store_paths))
    if not ordered:
        return frozenset()
    batches = tuple(
        ordered[start : start + chunk] for start in range(0, len(ordered), chunk)
    )
    worker_count = min(max(1, chunk_workers), len(batches))
    found: set[str] = set()
    done = 0
    lock = threading.Lock()

    def one(batch: Sequence[str]) -> frozenset[str]:
        nonlocal done
        hits = _paths_on_store_batch(
            batch,
            store,
            run=run,
            timeout=timeout,
            path_timeout=path_timeout,
            retries=retries,
            workers=workers,
        )
        with lock:
            done += 1
            found.update(hits)
            sys.stderr.write(
                f"path-info store={store} chunk={done}/{len(batches)} "
                f"batch={len(batch)} hits={len(hits)} total={len(found)}\n"
            )
            sys.stderr.flush()
        return hits

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        list(pool.map(one, batches))
    return frozenset(found)


def _try_paths_on_store_batch(
    batch: Sequence[str],
    store: str,
    *,
    run: Callable[..., object] | None,
    timeout: float,
) -> tuple[str, frozenset[str]]:
    """Return ``ok`` plus hits, or ``timeout`` / ``invalid`` plus no hits."""
    runner = subprocess.run if run is None else run
    try:
        result = runner(
            ["nix", "path-info", "--json", "--store", store, *batch],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except OSError, subprocess.TimeoutExpired:
        return "timeout", frozenset()
    stdout = str(getattr(result, "stdout", "") or "").strip()
    if not stdout:
        return "invalid", frozenset()
    try:
        reported = _present_paths_from_path_info_json(json.loads(stdout))
    except json.JSONDecodeError:
        return "invalid", frozenset()
    return "ok", frozenset(path for path in batch if path in reported)


def _paths_on_store_parallel(
    batch: Sequence[str],
    store: str,
    *,
    run: Callable[..., object] | None,
    timeout: float,
    workers: int,
) -> frozenset[str]:
    """Query one path per process, at most *workers* at a time."""
    worker_count = min(max(1, workers), len(batch))

    def present(path: str) -> str | None:
        if _path_on_store(path, store, run=run, timeout=timeout):
            return path
        return None

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        return frozenset(path for path in pool.map(present, batch) if path is not None)


def _paths_on_store_batch(
    batch: Sequence[str],
    store: str,
    *,
    run: Callable[..., object] | None = None,
    timeout: float = _PATH_INFO_TIMEOUT_SECONDS,
    path_timeout: float = _PATH_INFO_PER_PATH_TIMEOUT_SECONDS,
    retries: int = _PATH_INFO_BATCH_RETRIES,
    workers: int = _PATH_INFO_FALLBACK_WORKERS,
) -> frozenset[str]:
    """Query one ``path-info --json`` chunk; retry, then parallel per path."""
    last_kind = "invalid"
    for _ in range(1 + max(0, retries)):
        kind, found = _try_paths_on_store_batch(batch, store, run=run, timeout=timeout)
        if kind == "ok":
            return found
        last_kind = kind
    if last_kind == "timeout":
        return frozenset()
    return _paths_on_store_parallel(
        batch, store, run=run, timeout=path_timeout, workers=workers
    )


def check_path_in_cachix(
    store_path: str,
    *,
    run: Callable[..., object] | None = None,
    timeout: float = _PATH_INFO_PER_PATH_TIMEOUT_SECONDS,
) -> bool:
    """Return whether *store_path* is present in the gkze binary cache."""
    return _path_on_store(store_path, _BINARY_CACHE_STORE, run=run, timeout=timeout)


def check_path_on_nixos(
    store_path: str,
    *,
    run: Callable[..., object] | None = None,
    timeout: float = _PATH_INFO_PER_PATH_TIMEOUT_SECONDS,
) -> bool:
    """Return whether *store_path* is present on cache.nixos.org."""
    return _path_on_store(store_path, _NIXOS_CACHE_STORE, run=run, timeout=timeout)


def eval_check_out_path(
    flake_root: Path,
    system: str,
    attr: str,
    *,
    run: Callable[..., object] | None = None,
) -> str:
    """Return the store path of one flake check without realizing it."""
    runner = subprocess.run if run is None else run
    result = runner(
        [
            "nix",
            "eval",
            "--raw",
            "--no-write-lock-file",
            f"path:{flake_root}#checks.{system}.{attr}.outPath",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    path = getattr(result, "stdout", "").strip()
    if getattr(result, "returncode", 1) or not path.startswith("/nix/store/"):
        detail = getattr(result, "stderr", "").strip() or "nix eval failed"
        msg = f"could not evaluate {system} {attr} out path: {detail}"
        raise CoverageError(msg)
    return path


def write_root_out_path_cache(
    path: Path,
    *,
    tree: str,
    root_paths: Mapping[str, str],
    manifest: RootClosureManifest,
) -> None:
    """Write the planner's evaluated root out paths for coverage reuse."""
    cache = RootOutPathCache(tree=tree, root_paths=dict(root_paths), manifest=manifest)
    atomic_write_text(
        path,
        cache.model_dump_json(by_alias=True, indent=2) + "\n",
        mkdir=True,
    )


def load_root_out_path_cache(path: Path) -> RootOutPathCache:
    """Load one planner out-path cache or fail closed."""
    try:
        return RootOutPathCache.model_validate_json(path.read_bytes())
    except (OSError, ValueError) as error:
        msg = f"invalid root out-path cache: {path}"
        raise CoverageError(msg) from error


def find_root_out_path_cache(evidence: Path) -> Path:
    """Require exactly one planner out-path cache under *evidence*."""
    if not evidence.is_dir():
        msg = f"coverage evidence is not a directory: {evidence}"
        raise CoverageError(msg)
    matches = sorted(
        path for path in evidence.rglob(ROOT_OUT_PATHS_NAME) if path.is_file()
    )
    if len(matches) != 1:
        found = ", ".join(str(path) for path in matches) or "<none>"
        msg = (
            f"coverage needs exactly one {ROOT_OUT_PATHS_NAME} under {evidence}, "
            f"found {len(matches)}: {found}"
        )
        raise CoverageError(msg)
    return matches[0]


def load_planned_root_out_paths(evidence: Path, *, tree: str) -> RootOutPathCache:
    """Reuse the planner cache for *tree*; never re-evaluate out paths."""
    cache = load_root_out_path_cache(find_root_out_path_cache(evidence))
    if cache.tree != tree:
        msg = (
            f"root out-path cache tree {cache.tree!r} does not match candidate {tree!r}"
        )
        raise CoverageError(msg)
    return cache


def root_store_paths(
    flake_root: Path,
    manifest: RootClosureManifest,
    *,
    run: Callable[..., object] | None = None,
) -> dict[str, str]:
    """Evaluate every root's out path plus the per-system aggregates."""
    paths: dict[str, str] = {}
    systems = tuple(dict.fromkeys(root.system for root in manifest.roots))
    for root in manifest.roots:
        name = composed_root_name(root)
        paths[name] = eval_check_out_path(
            flake_root,
            root.system,
            root_closure_check_attr(name),
            run=run,
        )
    for system in systems:
        paths[f"aggregate:{system}"] = eval_check_out_path(
            flake_root,
            system,
            root_closure_check_attr(),
            run=run,
        )
    return paths


def assert_update_coverage(
    *,
    job_results: Mapping[str, str],
    evidence: Path,
    tree: str,
    validate_all_packages: bool,
    manifest: RootClosureManifest,
    package_expected: Mapping[str, frozenset[str]],
    root_paths: Mapping[str, str],
    cachix_present: Callable[[str], bool],
    costs: ShardCosts | None = None,
) -> None:
    """Fail closed unless every planned root, shard, and package is present."""
    require_required_jobs(job_results)
    if not darwin_roots(manifest):
        msg = "root manifest has no Darwin roots"
        raise CoverageError(msg)
    shards = plan_darwin_closure_shards(manifest, costs=costs)
    reports = require_validation_reports(
        evidence,
        tree=tree,
        validate_all_packages=validate_all_packages,
    )
    require_package_inventory(reports, package_expected)
    require_shard_receipts(evidence, shards, tree=tree)
    planned_roots = {compose_kind_name(root.kind, root.name) for root in manifest.roots}
    missing_paths = planned_roots - set(root_paths)
    if missing_paths:
        msg = f"coverage is missing out paths for: {', '.join(sorted(missing_paths))}"
        raise CoverageError(msg)
    require_cachix_paths(
        cachix_root_paths(
            planned=root_paths,
            evidence=evidence,
            darwin_root_names={
                compose_kind_name(root.kind, root.name)
                for root in darwin_roots(manifest)
            },
        ),
        present=cachix_present,
    )


def binary_cache_store() -> str:
    """Return the Cachix HTTP store URL coverage queries."""
    return _BINARY_CACHE_STORE


def nixos_cache_store() -> str:
    """Return the cache.nixos.org HTTP store URL planner queries."""
    return _NIXOS_CACHE_STORE


def required_coverage_jobs() -> tuple[str, ...]:
    """Return the job names coverage treats as mandatory."""
    return _REQUIRED_JOBS


def coverage_platform() -> str:
    """Return the platform the coverage job itself is running on."""
    return get_current_nix_platform()


def root_installable_for(system: str, name: str | None = None) -> str:
    """Return the flake installable coverage evaluates for one root."""
    return root_closure_installable(system, name)


def dump_job_results(results: Mapping[str, str]) -> str:
    """Render job results for the coverage job environment."""
    return "".join(f"{name}={value}\n" for name, value in results.items())


def read_json(path: Path) -> object:
    """Read a JSON document used as coverage fixture input."""
    return json.loads(path.read_text())
