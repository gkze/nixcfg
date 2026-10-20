"""Fail-closed Update coverage over roots, shards, packages, and Cachix."""

import json
import subprocess
import threading
from pathlib import Path

import pytest

from lib.update.ci.coverage import (
    CACHE_ROOT_DEPS_NAME,
    REALIZED_ROOT_PATHS_NAME,
    ROOT_OUT_PATHS_NAME,
    ClosureShardReceipt,
    CoverageError,
    RootOutPathCache,
    assert_cached_darwin_boundary,
    assert_update_coverage,
    binary_cache_store,
    check_path_in_cachix,
    check_path_on_nixos,
    coverage_platform,
    dump_job_results,
    eval_check_out_path,
    find_root_out_path_cache,
    linux_builder_store_paths,
    load_planned_root_out_paths,
    load_root_out_path_cache,
    load_shard_receipt,
    load_validation_report,
    nixos_cache_store,
    one_darwin_root_store_path,
    parse_job_results,
    paths_on_store,
    read_json,
    realized_darwin_root_store_paths,
    require_cachix_paths,
    require_package_inventory,
    require_required_jobs,
    require_shard_receipts,
    require_validation_reports,
    required_coverage_jobs,
    root_installable_for,
    root_store_paths,
    shard_artifact_name,
    write_root_out_path_cache,
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
        parent = evidence / shard_artifact_name(shard)
        parent.mkdir(parents=True, exist_ok=True)
        (parent / "shard-receipt.json").write_text(
            ClosureShardReceipt(
                tree="tree",
                system=shard.system,
                shard=shard.shard,
                roots=shard.roots,
            ).model_dump_json()
        )
        (parent / REALIZED_ROOT_PATHS_NAME).write_text(
            json.dumps({"storePaths": [f"/nix/store/flushed-{shard.shard}"]}) + "\n"
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
        assert nixos_cache_store() == "https://cache.nixos.org"
        return subprocess.CompletedProcess(
            args, 0, stdout="/nix/store/hit\n", stderr=""
        )

    assert check_path_in_cachix("/nix/store/hit", run=present)
    assert not check_path_in_cachix(
        "/nix/store/miss",
        run=lambda args, **_k: subprocess.CompletedProcess(args, 1, "", "gone"),
    )

    def nixos(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert args[3] == nixos_cache_store()
        return subprocess.CompletedProcess(
            args, 0, stdout="/nix/store/hit\n", stderr=""
        )

    assert check_path_on_nixos("/nix/store/hit", run=nixos)
    assert not check_path_on_nixos(
        "/nix/store/miss",
        run=lambda args, **_k: subprocess.CompletedProcess(args, 1, "", "gone"),
    )

    hit = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-hit"
    miss = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-miss"
    calls: list[list[str]] = []

    def batched(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        assert args[:4] == ["nix", "path-info", "--json", "--store"]
        payload = {path: {"narSize": 1} for path in args[5:] if path == hit}
        return subprocess.CompletedProcess(args, 1, json.dumps(payload), "missing")

    assert paths_on_store((hit, miss, hit), binary_cache_store(), run=batched) == (
        frozenset({hit})
    )
    assert len(calls) == 1
    assert paths_on_store((), binary_cache_store(), run=batched) == frozenset()

    def determinate_nulls(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        payload = {path: {"narSize": 1} if path == hit else None for path in args[5:]}
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    assert paths_on_store((hit, miss), binary_cache_store(), run=determinate_nulls) == (
        frozenset({hit})
    )

    def garbage(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if "--json" in args:
            return subprocess.CompletedProcess(args, 1, "not-json", "")
        return subprocess.CompletedProcess(args, 0 if args[-1] == hit else 1, "", "")

    assert paths_on_store((hit, miss), binary_cache_store(), run=garbage) == (
        frozenset({hit})
    )

    def empty(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if "--json" in args:
            return subprocess.CompletedProcess(args, 1, "", "")
        return subprocess.CompletedProcess(args, 0 if args[-1] == hit else 1, "", "")

    assert paths_on_store((hit, miss), binary_cache_store(), run=empty) == (
        frozenset({hit})
    )

    def listed(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        payload = [{"path": hit}, {"narSize": 1}, "nope"]
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    assert paths_on_store((hit, miss), binary_cache_store(), run=listed) == (
        frozenset({hit})
    )
    assert (
        paths_on_store(
            (hit,),
            binary_cache_store(),
            run=lambda args, **_k: subprocess.CompletedProcess(args, 0, "1", ""),
        )
        == frozenset()
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


def test_paths_on_store_retries_then_treats_timeout_as_miss() -> None:
    """A hung 128-wide path-info is not a presence claim and is not sequential."""
    hit = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-hit"
    miss = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-miss"
    calls: list[list[str]] = []

    def hung(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        assert kwargs.get("timeout") == 0.2
        raise subprocess.TimeoutExpired(args, 0.2)

    assert (
        paths_on_store(
            (hit, miss),
            binary_cache_store(),
            run=hung,
            timeout=0.2,
            retries=1,
        )
        == frozenset()
    )
    assert len(calls) == 2
    assert all("--json" in args for args in calls)


def test_paths_on_store_retries_hung_batch_then_uses_json() -> None:
    """One timeout is retried; a later JSON batch still reports only real hits."""
    hit = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-hit"
    miss = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-miss"
    calls: list[list[str]] = []

    def flaky(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        assert kwargs.get("timeout") == 0.2
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(args, 0.2)
        payload = {path: {"narSize": 1} for path in args[5:] if path == hit}
        return subprocess.CompletedProcess(args, 1, json.dumps(payload), "")

    assert paths_on_store(
        (hit, miss),
        binary_cache_store(),
        run=flaky,
        timeout=0.2,
        retries=1,
    ) == frozenset({hit})
    assert len(calls) == 2
    assert all("--json" in args for args in calls)


def test_paths_on_store_fallback_is_bounded_parallel() -> None:
    """Empty JSON retries the batch, then probes paths 16-wide, not one-by-one."""
    hit = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-hit"
    miss = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-miss"
    json_calls = 0
    active = 0
    max_active = 0
    lock = threading.Lock()
    gate = threading.Barrier(2)

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal json_calls, active, max_active
        if "--json" in args:
            json_calls += 1
            assert kwargs.get("timeout") == 0.2
            return subprocess.CompletedProcess(args, 1, "", "")
        assert kwargs.get("timeout") == 0.1
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            gate.wait(timeout=2)
        finally:
            with lock:
                active -= 1
        return subprocess.CompletedProcess(args, 0 if args[-1] == hit else 1, "", "")

    assert paths_on_store(
        (hit, miss),
        binary_cache_store(),
        run=run,
        timeout=0.2,
        path_timeout=0.1,
        retries=1,
        workers=2,
    ) == frozenset({hit})
    assert json_calls == 2
    assert max_active == 2


def test_path_on_store_timeout_is_a_miss() -> None:
    """Single-path coverage queries fail closed when path-info hangs."""

    def hung(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs.get("timeout") == 0.1
        raise subprocess.TimeoutExpired(args, 0.1)

    assert not check_path_in_cachix("/nix/store/hit", run=hung, timeout=0.1)
    assert not check_path_on_nixos("/nix/store/hit", run=hung, timeout=0.1)


def test_paths_on_store_oserror_is_a_miss() -> None:
    """An unreachable store is not a presence claim."""

    def dead(_args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("cachix down")

    assert (
        paths_on_store(
            ("/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-hit",),
            binary_cache_store(),
            run=dead,
            retries=0,
        )
        == frozenset()
    )
    assert not check_path_in_cachix("/nix/store/hit", run=dead)


def test_planned_root_out_path_cache_is_fail_closed(tmp_path: Path) -> None:
    manifest = _manifest()
    paths = {
        "darwin-argus": "/nix/store/argus",
        "home-george": "/nix/store/home",
        "aggregate:aarch64-darwin": "/nix/store/farm",
    }
    cache_path = tmp_path / "plan-shards-x86_64-linux" / ROOT_OUT_PATHS_NAME
    cache_path.parent.mkdir()
    write_root_out_path_cache(
        cache_path, tree="tree", root_paths=paths, manifest=manifest
    )
    loaded = load_planned_root_out_paths(tmp_path, tree="tree")
    assert loaded.tree == "tree"
    assert loaded.root_paths == paths
    assert loaded.manifest == manifest
    assert find_root_out_path_cache(tmp_path) == cache_path
    with pytest.raises(CoverageError, match="does not match candidate"):
        load_planned_root_out_paths(tmp_path, tree="other")
    cache_path.write_text("{")
    with pytest.raises(CoverageError, match="invalid root out-path cache"):
        load_root_out_path_cache(cache_path)
    cache_path.unlink()
    with pytest.raises(CoverageError, match="exactly one"):
        find_root_out_path_cache(tmp_path)
    (tmp_path / "a" / ROOT_OUT_PATHS_NAME).parent.mkdir()
    (tmp_path / "b" / ROOT_OUT_PATHS_NAME).parent.mkdir()
    (tmp_path / "a" / ROOT_OUT_PATHS_NAME).write_text("{}")
    (tmp_path / "b" / ROOT_OUT_PATHS_NAME).write_text("{}")
    with pytest.raises(CoverageError, match="found 2"):
        find_root_out_path_cache(tmp_path)
    with pytest.raises(CoverageError, match="not a directory"):
        find_root_out_path_cache(tmp_path / "missing")
    with pytest.raises(ValueError, match="no paths"):
        RootOutPathCache(tree="tree", root_paths={}, manifest=manifest)
    with pytest.raises(ValueError, match="non-store"):
        RootOutPathCache(
            tree="tree",
            root_paths={"darwin-argus": "not-a-store"},
            manifest=manifest,
        )


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


def _write_linux_builder_receipt(
    evidence: Path,
    *,
    system: str,
    store_paths: list[str] | None,
    tree: str = "tree",
) -> None:
    receipt = evidence / f"cache-root-deps-{system}" / CACHE_ROOT_DEPS_NAME
    receipt.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "tree": tree,
        "system": system,
        "failures": [],
    }
    if store_paths is not None:
        payload["store_paths"] = store_paths
    receipt.write_text(json.dumps(payload) + "\n")


def test_linux_builder_store_paths_fail_closed_on_receipt_shape(
    tmp_path: Path,
) -> None:
    """39113211468: canary/coverage loaded linux-builder receipts without tests."""
    image = "/nix/store/wy15yizd3i7z6bmg3llr43a2pflg9yyb-nixos-disk-image"
    with pytest.raises(CoverageError, match="not a directory"):
        linux_builder_store_paths(tmp_path / "missing", tree="tree")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(CoverageError, match=CACHE_ROOT_DEPS_NAME):
        linux_builder_store_paths(empty, tree="tree")
    _write_linux_builder_receipt(tmp_path, system="aarch64-linux", store_paths=[image])
    receipt = tmp_path / "cache-root-deps-aarch64-linux" / CACHE_ROOT_DEPS_NAME
    receipt.write_text("{")
    with pytest.raises(CoverageError, match=f"invalid {CACHE_ROOT_DEPS_NAME}"):
        linux_builder_store_paths(tmp_path, tree="tree")
    receipt.write_text("[]\n")
    with pytest.raises(CoverageError, match=f"invalid {CACHE_ROOT_DEPS_NAME}"):
        linux_builder_store_paths(tmp_path, tree="tree")
    receipt.write_text(json.dumps({"tree": "other", "system": "aarch64-linux"}) + "\n")
    with pytest.raises(CoverageError, match="does not match candidate"):
        linux_builder_store_paths(tmp_path, tree="tree")
    receipt.write_text(json.dumps({"tree": "tree", "store_paths": [image]}) + "\n")
    with pytest.raises(CoverageError, match="missing a native system"):
        linux_builder_store_paths(tmp_path, tree="tree")
    receipt.write_text(
        json.dumps({
            "tree": "tree",
            "system": "",
            "store_paths": [image],
        })
        + "\n"
    )
    with pytest.raises(CoverageError, match="missing a native system"):
        linux_builder_store_paths(tmp_path, tree="tree")
    _write_linux_builder_receipt(
        tmp_path, system="aarch64-linux", store_paths=[image, f"{image}-copy"]
    )
    with pytest.raises(CoverageError, match="multiple linux-builder outputs"):
        linux_builder_store_paths(tmp_path, tree="tree")
    _write_linux_builder_receipt(tmp_path, system="x86_64-linux", store_paths=[image])
    (tmp_path / "cache-root-deps-aarch64-linux" / CACHE_ROOT_DEPS_NAME).unlink()
    with pytest.raises(CoverageError, match="aarch64-linux linux-builder"):
        linux_builder_store_paths(tmp_path, tree="tree")


def test_assert_cached_boundary_fails_when_linux_deps_omitted_store_paths(
    tmp_path: Path,
) -> None:
    """39006154683: cache-root-deps substituted the VM and wrote no outputs.

    Darwin closures then missed ``nixos-disk-image`` under ``--max-jobs 0``.
    The canary must fail closed on the old receipt shape.
    """
    manifest = _manifest()
    write_root_out_path_cache(
        tmp_path / "plan-shards-x86_64-linux" / ROOT_OUT_PATHS_NAME,
        tree="tree",
        root_paths={
            "darwin-argus": "/nix/store/argus",
            "home-george": "/nix/store/home",
            "aggregate:aarch64-darwin": "/nix/store/farm",
        },
        manifest=manifest,
    )
    _write_linux_builder_receipt(tmp_path, system="aarch64-linux", store_paths=None)
    with pytest.raises(CoverageError, match="substituted paths were not re-pushed"):
        assert_cached_darwin_boundary(
            tmp_path, tree="tree", cachix_present=lambda _: True
        )


def test_assert_cached_boundary_requires_image_and_one_darwin_root(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    image = "/nix/store/wy15yizd3i7z6bmg3llr43a2pflg9yyb-nixos-disk-image"
    linux_eval = "/nix/store/qd427pyva2w1ck6blh3kd5ra50g9rych-darwin-system"
    flushed = "/nix/store/3jid4n7w542fwisq3265agwvs02icj8s-darwin-system"
    write_root_out_path_cache(
        tmp_path / "plan-shards-x86_64-linux" / ROOT_OUT_PATHS_NAME,
        tree="tree",
        root_paths={
            "darwin-argus": linux_eval,
            "home-george": "/nix/store/home",
            "aggregate:aarch64-darwin": "/nix/store/farm",
        },
        manifest=manifest,
    )
    _write_linux_builder_receipt(tmp_path, system="aarch64-linux", store_paths=[image])
    _write_linux_builder_receipt(tmp_path, system="x86_64-linux", store_paths=[])
    shards = plan_darwin_closure_shards(manifest)
    _write_shards(tmp_path, shards)
    argus = next(shard for shard in shards if shard.shard == "darwin-argus")
    (tmp_path / shard_artifact_name(argus) / REALIZED_ROOT_PATHS_NAME).write_text(
        json.dumps({"storePaths": [flushed]}) + "\n"
    )
    seen: list[str] = []

    def present(path: str) -> bool:
        seen.append(path)
        return True

    assert_cached_darwin_boundary(tmp_path, tree="tree", cachix_present=present)
    assert seen == [image, flushed]
    assert linux_eval not in seen
    with pytest.raises(CoverageError, match="linux-builder:aarch64-linux"):
        assert_cached_darwin_boundary(
            tmp_path, tree="tree", cachix_present=lambda path: path != image
        )
    with pytest.raises(CoverageError, match="darwin-argus"):
        assert_cached_darwin_boundary(
            tmp_path, tree="tree", cachix_present=lambda path: path != flushed
        )


def test_assert_update_coverage_queries_flushed_darwin_paths(
    tmp_path: Path,
) -> None:
    """39041243758: Linux-eval'd darwin-system hashes were never pushed."""
    manifest = _manifest()
    shards = plan_darwin_closure_shards(manifest)
    _write_reports(tmp_path)
    _write_shards(tmp_path, shards)
    seen: list[str] = []

    def present(path: str) -> bool:
        seen.append(path)
        return True

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
            "darwin-argus": "/nix/store/qd427pyva2w1ck6blh3kd5ra50g9rych-darwin-system",
            "home-george": "/nix/store/home",
            "aggregate:aarch64-darwin": "/nix/store/farm",
        },
        cachix_present=present,
    )
    assert "/nix/store/flushed-darwin-argus" in seen
    assert "/nix/store/flushed-home-george" in seen
    assert "/nix/store/qd427pyva2w1ck6blh3kd5ra50g9rych-darwin-system" not in seen
    assert "/nix/store/farm" not in seen


def test_assert_update_coverage_fails_without_realized_darwin_paths(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    shards = plan_darwin_closure_shards(manifest)
    _write_reports(tmp_path)
    _write_shards(tmp_path, shards)
    argus = next(shard for shard in shards if shard.shard == "darwin-argus")
    (tmp_path / shard_artifact_name(argus) / REALIZED_ROOT_PATHS_NAME).unlink()
    with pytest.raises(CoverageError, match="did not record realized store paths"):
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


def test_realized_darwin_root_paths_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(CoverageError, match="not a directory"):
        realized_darwin_root_store_paths(tmp_path / "missing")
    linux_dir = tmp_path / "validate-x86_64-linux-closure-shard-linux"
    linux_dir.mkdir()
    (linux_dir / "shard-receipt.json").write_text(
        ClosureShardReceipt(
            tree="tree",
            system="x86_64-linux",
            shard="linux",
            roots=("nixos-host",),
        ).model_dump_json()
    )
    failed_dir = tmp_path / "validate-aarch64-darwin-closure-shard-failed"
    failed_dir.mkdir()
    (failed_dir / "shard-receipt.json").write_text(
        ClosureShardReceipt(
            tree="tree",
            system="aarch64-darwin",
            shard="failed",
            roots=("darwin-failed",),
            failures=("boom",),
        ).model_dump_json()
    )
    (failed_dir / REALIZED_ROOT_PATHS_NAME).write_text(
        json.dumps({"storePaths": ["/nix/store/failed"]}) + "\n"
    )
    manifest = _manifest()
    shards = plan_darwin_closure_shards(manifest)
    _write_shards(tmp_path, shards)
    home = next(shard for shard in shards if shard.shard == "home-george")
    argus = next(shard for shard in shards if shard.shard == "darwin-argus")
    (tmp_path / shard_artifact_name(argus) / REALIZED_ROOT_PATHS_NAME).unlink()
    assert one_darwin_root_store_path(tmp_path) == {
        "home-george": "/nix/store/flushed-home-george"
    }
    bad = tmp_path / shard_artifact_name(home) / REALIZED_ROOT_PATHS_NAME
    bad.write_text("{")
    with pytest.raises(CoverageError, match="invalid realized-root-paths"):
        realized_darwin_root_store_paths(tmp_path)
    bad.write_text(json.dumps({"storePaths": [1]}) + "\n")
    with pytest.raises(CoverageError, match="invalid realized-root-paths"):
        realized_darwin_root_store_paths(tmp_path)
    bad.write_text("[]\n")
    with pytest.raises(CoverageError, match="invalid realized-root-paths"):
        realized_darwin_root_store_paths(tmp_path)
    receipt = tmp_path / shard_artifact_name(home) / "shard-receipt.json"
    receipt.write_text(
        ClosureShardReceipt(
            tree="tree",
            system="aarch64-darwin",
            shard="home-george",
            roots=("home-george", "darwin-argus"),
        ).model_dump_json()
    )
    bad.write_text(json.dumps({"storePaths": ["/nix/store/a", "/nix/store/b"]}) + "\n")
    with pytest.raises(CoverageError, match="realized 2 store paths"):
        realized_darwin_root_store_paths(tmp_path)
    receipt.write_text(
        ClosureShardReceipt(
            tree="tree",
            system="aarch64-darwin",
            shard="home-george",
            roots=("home-george",),
        ).model_dump_json()
    )
    bad.write_text(json.dumps({"storePaths": ["relative"]}) + "\n")
    assert realized_darwin_root_store_paths(tmp_path) == {}


def test_assert_cached_boundary_fails_without_realized_darwin(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    image = "/nix/store/wy15yizd3i7z6bmg3llr43a2pflg9yyb-nixos-disk-image"
    write_root_out_path_cache(
        tmp_path / "plan-shards-x86_64-linux" / ROOT_OUT_PATHS_NAME,
        tree="tree",
        root_paths={
            "darwin-argus": "/nix/store/argus",
            "home-george": "/nix/store/home",
            "aggregate:aarch64-darwin": "/nix/store/farm",
        },
        manifest=manifest,
    )
    _write_linux_builder_receipt(tmp_path, system="aarch64-linux", store_paths=[image])
    with pytest.raises(CoverageError, match="no realized Darwin root"):
        assert_cached_darwin_boundary(
            tmp_path, tree="tree", cachix_present=lambda _path: True
        )
