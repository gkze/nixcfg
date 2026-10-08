"""Fail-closed Update coverage over roots, shards, packages, and Cachix."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from lib.update.ci.coverage import (
    ClosureShardReceipt,
    CoverageError,
    assert_update_coverage,
    binary_cache_store,
    check_path_in_cachix,
    coverage_platform,
    dump_job_results,
    eval_check_out_path,
    load_shard_receipt,
    load_validation_report,
    parse_job_results,
    read_json,
    require_cachix_paths,
    require_package_inventory,
    require_required_jobs,
    require_shard_receipts,
    require_validation_reports,
    required_coverage_jobs,
    root_installable_for,
    root_store_paths,
    shard_artifact_name,
)
from lib.update.ci.shard_plan import ClosureShard, plan_darwin_closure_shards
from lib.update.derivation_validation import RootClosureManifest


def _manifest() -> RootClosureManifest:
    return RootClosureManifest.model_validate({
        "schemaVersion": 2,
        "requiredKinds": ["darwin", "home"],
        "requiredRoots": [],
        "roots": [
            {"kind": "darwin", "name": "argus", "system": "aarch64-darwin"},
            {"kind": "home", "name": "george", "system": "aarch64-darwin"},
        ],
    })


def _jobs() -> dict[str, str]:
    return dict.fromkeys(required_coverage_jobs(), "success")


def _report(
    *,
    system: str,
    gates: tuple[str, ...],
    installables: tuple[str, ...] = ("pkg",),
) -> dict[str, object]:
    return {
        "tree": "tree",
        "system": system,
        "validate_all_packages": True,
        "gates": list(gates),
        "failures": [],
        "planned_installables": list(installables),
    }


def _write_reports(evidence: Path) -> None:
    mapping = {
        "validate-aarch64-linux": _report(
            system="aarch64-linux", gates=("packages", "closures")
        ),
        "validate-x86_64-linux": _report(
            system="x86_64-linux", gates=("packages", "closures")
        ),
        "validate-aarch64-darwin-packages": _report(
            system="aarch64-darwin", gates=("packages",)
        ),
        "validate-aarch64-darwin-closures": _report(
            system="aarch64-darwin",
            gates=("closures",),
            installables=(),
        ),
    }
    for name, payload in mapping.items():
        path = evidence / name / "validation.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))


def _write_shards(evidence: Path, shards: tuple[ClosureShard, ...]) -> None:
    for shard in shards:
        path = evidence / shard_artifact_name(shard) / "shard-receipt.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            ClosureShardReceipt(
                tree="tree",
                system=shard.system,
                shard=shard.shard,
                roots=shard.roots,
            ).model_dump_json()
        )


def test_assert_update_coverage_accepts_a_complete_run(tmp_path: Path) -> None:
    manifest = _manifest()
    shards = plan_darwin_closure_shards(manifest)
    _write_reports(tmp_path)
    _write_shards(tmp_path, shards)
    assert_update_coverage(
        job_results=_jobs(),
        evidence=tmp_path,
        tree="tree",
        validate_all_packages=True,
        manifest=manifest,
        package_expected={
            "aarch64-linux": frozenset({"pkg"}),
            "x86_64-linux": frozenset({"pkg"}),
            "aarch64-darwin": frozenset({"pkg"}),
        },
        root_paths={
            "darwin-argus": "/nix/store/argus",
            "home-george": "/nix/store/home",
            "aggregate:aarch64-darwin": "/nix/store/farm",
        },
        cachix_present=lambda _path: True,
    )


def test_coverage_fails_closed_on_skips_and_missing_artifacts(
    tmp_path: Path,
) -> None:
    with pytest.raises(CoverageError, match="omitted"):
        require_required_jobs({})
    with pytest.raises(CoverageError, match="did not succeed"):
        require_required_jobs({**_jobs(), "validate-darwin-roots": "skipped"})
    with pytest.raises(CoverageError, match="invalid job result"):
        parse_job_results("=")
    assert parse_job_results("# comment\nvalidate-arm=success\n") == {
        "validate-arm": "success"
    }
    with pytest.raises(CoverageError, match="missing validation"):
        require_validation_reports(tmp_path, tree="tree", validate_all_packages=True)
    _write_reports(tmp_path)
    report_path = tmp_path / "validate-aarch64-linux" / "validation.json"
    report_path.write_text("{")
    with pytest.raises(CoverageError, match="invalid validation"):
        load_validation_report(report_path)
    report_path.write_text(
        json.dumps(_report(system="aarch64-linux", gates=("packages", "closures")))
    )
    (tmp_path / "validate-aarch64-linux" / "validation.json").write_text(
        json.dumps({
            **_report(system="aarch64-linux", gates=("packages", "closures")),
            "validate_all_packages": False,
        })
    )
    with pytest.raises(CoverageError, match="narrowed"):
        require_validation_reports(tmp_path, tree="tree", validate_all_packages=True)


def test_package_and_shard_inventory_fail_closed(tmp_path: Path) -> None:
    from lib.update.ci.candidate import ValidationReport

    report = ValidationReport(
        tree="tree",
        system="aarch64-darwin",
        gates=("packages",),
        failures=(),
        validate_all_packages=True,
        planned_installables=("kept",),
    )
    with pytest.raises(CoverageError, match="drifted"):
        require_package_inventory(
            [report], {"aarch64-darwin": frozenset({"kept", "missing"})}
        )
    with pytest.raises(CoverageError, match="no declared"):
        require_package_inventory([report], {})
    empty = ValidationReport(
        tree="tree",
        system="aarch64-darwin",
        gates=("packages",),
        failures=(),
        validate_all_packages=True,
    )
    with pytest.raises(CoverageError, match="planned none"):
        require_package_inventory([empty], {"aarch64-darwin": frozenset()})
    require_package_inventory(
        [
            ValidationReport(
                tree="tree",
                system="aarch64-darwin",
                gates=("closures",),
                failures=(),
            )
        ],
        {},
    )
    shards = plan_darwin_closure_shards(_manifest())
    with pytest.raises(CoverageError, match="missing closure shard"):
        require_shard_receipts(tmp_path, shards, tree="tree")
    _write_shards(tmp_path, shards)
    receipt = tmp_path / shard_artifact_name(shards[0]) / "shard-receipt.json"
    receipt.write_text("{")
    with pytest.raises(CoverageError, match="invalid closure shard"):
        load_shard_receipt(receipt)
    receipt.write_text(
        ClosureShardReceipt(
            tree="other",
            system="aarch64-darwin",
            shard=shards[0].shard,
            roots=shards[0].roots,
        ).model_dump_json()
    )
    with pytest.raises(CoverageError, match="not complete"):
        require_shard_receipts(tmp_path, shards, tree="tree")


def test_cachix_and_eval_helpers_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(CoverageError, match="missing from gkze"):
        require_cachix_paths({"root": "/nix/store/missing"}, present=lambda _p: False)
    require_cachix_paths({"root": "/nix/store/hit"}, present=lambda _p: True)

    def present(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert args[2] == "--store"
        assert args[3] == binary_cache_store()
        return subprocess.CompletedProcess(
            args, 0, stdout="/nix/store/hit\n", stderr=""
        )

    assert check_path_in_cachix("/nix/store/hit", run=present)
    assert not check_path_in_cachix(
        "/nix/store/miss",
        run=lambda args, **_k: subprocess.CompletedProcess(args, 1, "", "gone"),
    )
    with pytest.raises(CoverageError, match="could not evaluate"):
        eval_check_out_path(
            tmp_path,
            "aarch64-darwin",
            "root-closures",
            run=lambda args, **_k: subprocess.CompletedProcess(args, 1, "", "nope"),
        )
    path = eval_check_out_path(
        tmp_path,
        "aarch64-darwin",
        "root-closure-darwin-argus",
        run=lambda args, **_k: subprocess.CompletedProcess(
            args, 0, "/nix/store/argus\n", ""
        ),
    )
    assert path == "/nix/store/argus"
    paths = root_store_paths(
        tmp_path,
        _manifest(),
        run=lambda args, **_k: subprocess.CompletedProcess(
            args,
            0,
            "/nix/store/" + args[-1].rsplit(".", 2)[0].split(".")[-1] + "\n",
            "",
        ),
    )
    assert paths["darwin-argus"].startswith("/nix/store/")
    assert "aggregate:aarch64-darwin" in paths
    assert root_installable_for("aarch64-darwin") == (
        "path:.#checks.aarch64-darwin.root-closures"
    )
    assert coverage_platform()
    assert dump_job_results({"a": "success"}) == "a=success\n"
    payload = tmp_path / "doc.json"
    payload.write_text("[]")
    assert read_json(payload) == []


def test_assert_update_coverage_rejects_empty_darwin_or_missing_paths(
    tmp_path: Path,
) -> None:
    linux = RootClosureManifest.model_construct(
        schema_version=2,
        required_kinds=("darwin", "home"),
        required_roots=(),
        roots=(),
    )
    with pytest.raises(CoverageError, match="no Darwin"):
        assert_update_coverage(
            job_results=_jobs(),
            evidence=tmp_path,
            tree="tree",
            validate_all_packages=True,
            manifest=linux,
            package_expected={},
            root_paths={},
            cachix_present=lambda _p: True,
        )
    manifest = _manifest()
    shards = plan_darwin_closure_shards(manifest)
    _write_reports(tmp_path)
    _write_shards(tmp_path, shards)
    with pytest.raises(CoverageError, match="missing out paths"):
        assert_update_coverage(
            job_results=_jobs(),
            evidence=tmp_path,
            tree="tree",
            validate_all_packages=True,
            manifest=manifest,
            package_expected={
                "aarch64-linux": frozenset({"pkg"}),
                "x86_64-linux": frozenset({"pkg"}),
                "aarch64-darwin": frozenset({"pkg"}),
            },
            root_paths={"darwin-argus": "/nix/store/argus"},
            cachix_present=lambda _p: True,
        )


def test_gate_shape_failures(tmp_path: Path) -> None:
    _write_reports(tmp_path)
    (tmp_path / "validate-aarch64-darwin-packages" / "validation.json").write_text(
        json.dumps(_report(system="aarch64-darwin", gates=("packages", "closures")))
    )
    with pytest.raises(CoverageError, match="only the packages gate"):
        require_validation_reports(tmp_path, tree="tree", validate_all_packages=True)
    _write_reports(tmp_path)
    (tmp_path / "validate-aarch64-darwin-closures" / "validation.json").write_text(
        json.dumps(_report(system="aarch64-darwin", gates=("packages", "closures")))
    )
    with pytest.raises(CoverageError, match="only the closures gate"):
        require_validation_reports(tmp_path, tree="tree", validate_all_packages=True)
    _write_reports(tmp_path)
    (tmp_path / "validate-aarch64-linux" / "validation.json").write_text(
        json.dumps(_report(system="aarch64-linux", gates=("packages",)))
    )
    with pytest.raises(CoverageError, match="aarch64-linux"):
        require_validation_reports(tmp_path, tree="tree", validate_all_packages=True)
    _write_reports(tmp_path)
    (tmp_path / "validate-x86_64-linux" / "validation.json").write_text(
        json.dumps(_report(system="x86_64-linux", gates=("packages",)))
    )
    with pytest.raises(CoverageError, match="x86_64-linux"):
        require_validation_reports(tmp_path, tree="tree", validate_all_packages=True)
    _write_reports(tmp_path)
    (tmp_path / "validate-aarch64-linux" / "validation.json").write_text(
        json.dumps({
            **_report(system="aarch64-linux", gates=("packages", "closures")),
            "failures": [{"source": "x", "installable": "y", "message": "z"}],
        })
    )
    with pytest.raises(CoverageError, match="not publishable"):
        require_validation_reports(tmp_path, tree="tree", validate_all_packages=True)
