"""Fail-closed Update coverage: every planned root and package reached Cachix.

The root inventory is ``lib.rootClosureManifest``. The shard plan is
``lib.update.ci.shard_plan``. This module is the only place that decides
whether a run covered that inventory. A missing, skipped, unbuilt, or
unpushed root fails the run.
"""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

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
from lib.update.nix import get_current_nix_platform

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

_BINARY_CACHE_STORE = "https://gkze.cachix.org"
_REQUIRED_JOBS = (
    "plan-darwin-closures",
    "validate-arm",
    "validate-x86",
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


def check_path_in_cachix(
    store_path: str,
    *,
    run: Callable[..., object] | None = None,
) -> bool:
    """Return whether *store_path* is present in the gkze binary cache."""
    runner = subprocess.run if run is None else run
    result = runner(
        [
            "nix",
            "path-info",
            "--store",
            _BINARY_CACHE_STORE,
            store_path,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return getattr(result, "returncode", 1) == 0


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
    require_cachix_paths(root_paths, present=cachix_present)


def binary_cache_store() -> str:
    """Return the Cachix HTTP store URL coverage queries."""
    return _BINARY_CACHE_STORE


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
