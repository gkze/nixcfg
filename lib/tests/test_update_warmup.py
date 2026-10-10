"""Structural Darwin warmup is the missing shared drv set, not a zed list."""

import json
import subprocess
from pathlib import Path

import pytest

from lib.update.ci.shard_plan import ClosureShard
from lib.update.ci.warmup import (
    MAX_SHARD_LOCAL_BUILDS,
    RUST_WARMUP_SLOTS,
    WARMUP_DRVS_CACHE_INFO,
    WARMUP_DRVS_NAME,
    WARMUP_DRVS_ROOTS,
    WARMUP_PLAN_NAME,
    ShardLocalBuildReport,
    WarmupError,
    _store_output_rest,
    assert_local_build_threshold,
    darwin_output_paths,
    default_cache_present,
    delete_local_store_paths,
    delete_warmup_drv_outputs,
    eval_root_darwin_outputs,
    export_warmup_drvs,
    extension_host_family_warmup_outputs,
    import_warmup_drvs,
    intersect_missing,
    is_crate2nix_rust_output,
    is_extension_host_family_store_path,
    is_named_rust_crate_store_path,
    is_rust_agent_ui_store_path,
    is_rust_extension_host_store_path,
    is_rust_language_models_store_path,
    is_rust_zed_store_path,
    language_models_input_drvs,
    language_models_warmup_outputs,
    load_warmup_plan,
    nix_store_argv_has_operation,
    partition_agent_ui_drvs,
    partition_rust_zed_drvs,
    plan_darwin_warmup,
    query_drv_outputs,
    realize_warmup_outputs,
    rust_warmup_layers,
    shard_remaining_outputs,
    skip_cached_warmup_paths,
    slot_warmup_paths,
    substitutable_paths,
    unique_drvs_for_outputs,
    warmup_build_installable,
    warmup_drv_root_args,
    warmup_output_drvs,
    write_warmup_plan,
)
from lib.update.derivation_validation import RootClosureManifest


def _manifest() -> RootClosureManifest:
    return RootClosureManifest.model_validate({
        "schemaVersion": 2,
        "requiredKinds": ["darwin", "home"],
        "requiredRoots": [],
        "roots": [
            {"kind": "darwin", "name": "argus", "system": "aarch64-darwin"},
            {"kind": "darwin", "name": "rocinante", "system": "aarch64-darwin"},
            {"kind": "home", "name": "george", "system": "aarch64-darwin"},
        ],
    })


def _graph(
    *darwin: str,
    linux: str | None = None,
    edges: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, object]:
    derivations: dict[str, object] = {}
    for path in darwin:
        name = path.rsplit("/", 1)[-1]
        input_drvs = {
            f"{dep.rsplit('/', 1)[-1]}.drv": ["out"]
            for dep in (edges or {}).get(path, ())
        }
        derivations[f"{name}.drv"] = {
            "version": 4,
            "system": "aarch64-darwin",
            "outputs": {"out": {"path": path}},
            "inputs": {"drvs": input_drvs},
        }
    if linux is not None:
        derivations["vm.drv"] = {
            "version": 4,
            "system": "aarch64-linux",
            "outputs": {"out": {"path": linux}},
            "inputs": {"drvs": {}},
        }
    return {"version": 4, "derivations": derivations}


def test_darwin_output_paths_keep_darwin_and_skip_linux_or_pathless() -> None:
    payload = _graph(
        "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-shared",
        "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_gpui-1",
        linux="/nix/store/cccccccccccccccccccccccccccccccc-vm",
    )
    derivations = payload["derivations"]
    assert isinstance(derivations, dict)
    derivations["float.drv"] = {
        "version": 4,
        "system": "aarch64-darwin",
        "outputs": {"out": {"method": "nar"}},
        "inputs": {"drvs": {}},
    }
    derivations["weird.drv"] = {
        "version": 4,
        "system": "aarch64-darwin",
        "outputs": "nope",
        "inputs": {"drvs": {}},
    }
    derivations["skip.drv"] = {
        "version": 4,
        "system": "aarch64-darwin",
        "outputs": {"out": "nope"},
        "inputs": {"drvs": {}},
    }
    derivations["not-a-drv"] = "nope"
    derivations["relative.drv"] = {
        "version": 4,
        "system": "aarch64-darwin",
        "outputs": {"out": {"path": "not-a-store-path"}},
        "inputs": {"drvs": {}},
    }
    derivations["v4-relative.drv"] = {
        "version": 4,
        "system": "aarch64-darwin",
        "outputs": {"out": {"path": "mklmiipy424axj6zgl1vnqg6a58mdz77-hello-2.12.3"}},
        "inputs": {"drvs": {}},
    }
    paths = darwin_output_paths(payload)
    assert "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-shared" in paths
    assert "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_gpui-1" in paths
    assert "/nix/store/mklmiipy424axj6zgl1vnqg6a58mdz77-hello-2.12.3" in paths
    assert "/nix/store/cccccccccccccccccccccccccccccccc-vm" not in paths
    assert "not-a-store-path" not in paths
    with pytest.raises(WarmupError, match="missing derivations"):
        darwin_output_paths({"version": 4})


def test_intersection_and_shard_remainder_are_cache_aware() -> None:
    shared = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-shared"
    rust = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_gpui-1"
    unique = "/nix/store/dddddddddddddddddddddddddddddddd-argus-only"
    cached = "/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-cached"
    per_root = {
        "darwin-argus": frozenset({shared, rust, unique, cached}),
        "home-george": frozenset({shared, rust, cached}),
    }
    substitutable = frozenset({cached})
    warmup = intersect_missing(per_root, substitutable)
    assert warmup == frozenset({shared, rust})
    assert intersect_missing({}, substitutable) == frozenset()
    shard = ClosureShard(
        shard="darwin-argus",
        system="aarch64-darwin",
        roots=("darwin-argus",),
    )
    remaining = shard_remaining_outputs(
        shard, per_root, substitutable=substitutable, warmup=warmup
    )
    assert remaining == frozenset({unique})
    unknown = ClosureShard(
        shard="darwin-missing",
        system="aarch64-darwin",
        roots=("darwin-missing",),
    )
    assert (
        shard_remaining_outputs(
            unknown, per_root, substitutable=substitutable, warmup=warmup
        )
        == frozenset()
    )
    assert is_crate2nix_rust_output(rust)
    assert not is_crate2nix_rust_output(shared)
    assert not is_crate2nix_rust_output("/nix/store/hashonly")


def test_local_build_threshold_fails_closed_on_divergent_stdenv_scale() -> None:
    ok = ShardLocalBuildReport(
        shard="home-george",
        roots=("home-george",),
        remaining=132,
        remaining_rust_crates=0,
    )
    assert_local_build_threshold((ok,))
    huge = ShardLocalBuildReport(
        shard="home-george",
        roots=("home-george",),
        remaining=1534,
        remaining_rust_crates=1534,
    )
    with pytest.raises(WarmupError, match="1534"):
        assert_local_build_threshold((huge,))
    with pytest.raises(WarmupError, match="at least 1"):
        assert_local_build_threshold((ok,), threshold=0)
    assert MAX_SHARD_LOCAL_BUILDS == 400


def test_plan_darwin_warmup_writes_intersection_and_rejects_huge_remainder(
    tmp_path: Path,
) -> None:
    graphs = {
        "darwin-argus": _graph(
            "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-shared",
            "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_gpui-1",
            "/nix/store/dddddddddddddddddddddddddddddddd-argus-only",
            "/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-cached",
            linux="/nix/store/cccccccccccccccccccccccccccccccc-vm",
        ),
        "darwin-rocinante": _graph(
            "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-shared",
            "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_gpui-1",
            "/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-cached",
        ),
        "home-george": _graph(
            "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-shared",
            "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_gpui-1",
            "/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-cached",
        ),
    }

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        name = args[-1].rsplit("root-closure-", 1)[-1]
        return subprocess.CompletedProcess(
            args, 0, stdout=json.dumps(graphs[name]), stderr=""
        )

    shards = (
        ClosureShard(
            shard="darwin-argus",
            system="aarch64-darwin",
            roots=("darwin-argus",),
        ),
        ClosureShard(
            shard="home-george",
            system="aarch64-darwin",
            roots=("home-george",),
        ),
    )
    cached = {"/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-cached"}
    plan = plan_darwin_warmup(
        tmp_path,
        manifest=_manifest(),
        shards=shards,
        run=run,
        present=cached.__contains__,
    )
    assert plan.warmup_outputs == (
        "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-shared",
        "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_gpui-1",
    )
    assert plan.rust_layers == (
        ("/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-shared",),
        ("/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_gpui-1",),
    )
    assert plan.output_drvs == {
        "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-shared": (
            "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-shared.drv"
        ),
        "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_gpui-1": (
            "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_gpui-1.drv"
        ),
    }
    assert plan.per_root["darwin-argus"].remaining == 1
    assert plan.shards[0].remaining == 1
    assert plan.shards[1].remaining == 0
    path = tmp_path / WARMUP_PLAN_NAME
    write_warmup_plan(path, plan)
    loaded = load_warmup_plan(path)
    assert loaded.warmup_outputs == plan.warmup_outputs
    with pytest.raises(WarmupError, match="invalid warmup plan"):
        load_warmup_plan(tmp_path / "missing.json")
    huge_unique = [f"/nix/store/{index:032d}-unique-{index}" for index in range(401)]
    graphs["darwin-argus"] = _graph(
        "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-shared",
        *huge_unique,
    )
    with pytest.raises(WarmupError, match="local-build budget"):
        plan_darwin_warmup(
            tmp_path,
            manifest=_manifest(),
            shards=shards,
            run=run,
            present=lambda _path: False,
        )


def test_eval_root_darwin_outputs_and_substituters_fail_closed(
    tmp_path: Path,
) -> None:
    def boom(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="no graph")

    with pytest.raises(WarmupError, match="no graph"):
        eval_root_darwin_outputs(tmp_path, "darwin-argus", run=boom)
    with pytest.raises(WarmupError, match="nix derivation show failed"):
        eval_root_darwin_outputs(
            tmp_path,
            "darwin-argus",
            run=lambda args, **_k: subprocess.CompletedProcess(args, 1, "", ""),
        )
    with pytest.raises(WarmupError, match="not JSON"):
        eval_root_darwin_outputs(
            tmp_path,
            "darwin-argus",
            run=lambda args, **_k: subprocess.CompletedProcess(args, 0, "nope", ""),
        )
    with pytest.raises(WarmupError, match="not an object"):
        eval_root_darwin_outputs(
            tmp_path,
            "darwin-argus",
            run=lambda args, **_k: subprocess.CompletedProcess(args, 0, "[1]", ""),
        )
    shown = eval_root_darwin_outputs(
        tmp_path,
        "darwin-argus",
        run=lambda args, **_k: subprocess.CompletedProcess(
            args,
            0,
            json.dumps(_graph("/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-shared")),
            "",
        ),
    )
    assert shown == frozenset({"/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-shared"})
    assert substitutable_paths((), present=lambda _p: True) == frozenset()
    assert substitutable_paths(
        ["/nix/store/hit", "/nix/store/miss", "/nix/store/hit"],
        present=lambda path: path.endswith("hit"),
    ) == frozenset({"/nix/store/hit"})

    def nixos(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        store = args[args.index("--store") + 1]
        path = args[-1]
        ok = store.endswith("cache.nixos.org") and path.endswith("nixos")
        return subprocess.CompletedProcess(args, 0 if ok else 1, "", "")

    assert default_cache_present("/nix/store/nixos", run=nixos)
    assert not default_cache_present("/nix/store/miss", run=nixos)

    def gkze(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        store = args[args.index("--store") + 1]
        path = args[-1]
        ok = "gkze" in store and path.endswith("gkze")
        return subprocess.CompletedProcess(args, 0 if ok else 1, "", "")

    assert default_cache_present("/nix/store/gkze", run=gkze)


def test_plan_defaults_to_subprocess_and_cache_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _graph(
        "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-shared",
        "/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-cached",
    )

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, json.dumps(graph), "")

    monkeypatch.setattr("lib.update.ci.warmup.subprocess.run", run)
    shown = eval_root_darwin_outputs(tmp_path, "darwin-argus")
    assert shown == frozenset({
        "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-shared",
        "/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-cached",
    })
    monkeypatch.setattr(
        "lib.update.ci.warmup.default_cache_present",
        lambda path: path.endswith("cached"),
    )
    shards = (
        ClosureShard(
            shard="darwin-argus",
            system="aarch64-darwin",
            roots=("darwin-argus",),
        ),
    )
    plan = plan_darwin_warmup(
        tmp_path,
        manifest=_manifest(),
        shards=shards,
        run=run,
    )
    assert plan.warmup_outputs == (
        "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-shared",
    )
    linux_only = RootClosureManifest.model_validate({
        "schemaVersion": 2,
        "requiredKinds": ["darwin", "home"],
        "requiredRoots": [],
        "roots": [
            {"kind": "darwin", "name": "argus", "system": "x86_64-linux"},
            {"kind": "home", "name": "george", "system": "x86_64-linux"},
        ],
    })
    empty = plan_darwin_warmup(
        tmp_path,
        manifest=linux_only,
        shards=(),
        run=run,
        present=lambda _path: False,
    )
    assert empty.warmup_outputs == ()
    assert empty.rust_layers == ()
    assert empty.per_root == {}


def test_realize_warmup_outputs_batches_store_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[-1].endswith("fail"):
            return subprocess.CompletedProcess(args, 1, "", "boom")
        return subprocess.CompletedProcess(args, 0, "", "")

    empty = realize_warmup_outputs((), flake_root=tmp_path, run=run)
    assert empty == ()
    assert calls == []
    failures = realize_warmup_outputs(
        [
            "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-ok",
            "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-fail",
        ],
        flake_root=tmp_path,
        run=run,
    )
    assert calls[0][1] == "build"
    assert "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-ok" in calls[0]
    assert [failure.installable for failure in failures] == [
        "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-fail",
    ]
    assert failures[0].source == "root-warmup"
    assert "boom" in failures[0].message


def test_realize_warmup_outputs_builds_drv_outputs(tmp_path: Path) -> None:
    """Nix build foo.drv realizes the .drv file; outputs need foo.drv^* (#1255)."""
    drv = "/nix/store/cccccccccccccccccccccccccccccccc-rust_a-1.drv"
    drv_calls: list[list[str]] = []

    def drv_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        drv_calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    assert warmup_build_installable(drv) == f"{drv}^*"
    assert (
        warmup_build_installable("/nix/store/dddd-shared") == "/nix/store/dddd-shared"
    )
    assert realize_warmup_outputs((drv,), flake_root=tmp_path, run=drv_run) == ()
    assert f"{drv}^*" in drv_calls[0]
    assert drv not in drv_calls[0]


def test_warmup_drvs_map_outputs_and_import_register_files(tmp_path: Path) -> None:
    """Darwin rebuilds from mapped .drv paths; output store paths are not enough."""
    shared = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-shared"
    rust_out = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_zed-1"
    rust_lib = "/nix/store/cccccccccccccccccccccccccccccccc-rust_zed-1-lib"
    payload = _graph(shared, rust_out)
    derivations = payload["derivations"]
    assert isinstance(derivations, dict)
    rust_drv = derivations["bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_zed-1.drv"]
    assert isinstance(rust_drv, dict)
    outputs = rust_drv["outputs"]
    assert isinstance(outputs, dict)
    outputs["lib"] = {"path": rust_lib}
    mapping = warmup_output_drvs(frozenset({shared, rust_out, rust_lib}), (payload,))
    assert mapping[shared].endswith("-shared.drv")
    assert mapping[rust_out] == mapping[rust_lib]
    assert unique_drvs_for_outputs((rust_out, rust_lib, shared), mapping) == (
        mapping[rust_out],
        mapping[shared],
    )
    with pytest.raises(WarmupError, match="missing Darwin .drv mapping"):
        unique_drvs_for_outputs(("/nix/store/missing",), mapping)
    with pytest.raises(WarmupError, match="missing Darwin .drv mapping"):
        warmup_output_drvs(frozenset({shared, "/nix/store/missing"}), (payload,))


def _planner_drv(tmp_path: Path, name: str = "rust_a-1") -> tuple[Path, str]:
    """Return a fake planner .drv file and its original store path."""
    drv_name = f"dddddddddddddddddddddddddddddddd-{name}.drv"
    drv_path = f"/nix/store/{drv_name}"
    planner_drv = tmp_path / "planner" / drv_name
    planner_drv.parent.mkdir(exist_ok=True)
    planner_drv.write_text("drv")
    return planner_drv, drv_path


def _store_ok(
    args: list[str], stdout: str | bytes = "", stderr: str | bytes = ""
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    """Return a successful nix process result."""
    return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr=stderr)


def _store_fail(
    args: list[str], stdout: str | bytes = "", stderr: str | bytes = ""
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    """Return a failed nix process result."""
    return subprocess.CompletedProcess(args, 1, stdout=stdout, stderr=stderr)


def _write_file_cache(cache: Path) -> Path:
    """Create a nix copy --to file:// cache marker."""
    cache.mkdir(parents=True, exist_ok=True)
    (cache / WARMUP_DRVS_CACHE_INFO).write_text(
        "StoreDir: /nix/store\nWantMassQuery: 1\nPriority: 40\n"
    )
    return cache


def _copy_paths(args: list[str]) -> tuple[str, ...]:
    """Return store paths after the file:// URI in a nix copy argv."""
    uri = next(part for part in args if part.startswith("file://"))
    return tuple(args[args.index(uri) + 1 :])


def test_warmup_drv_closure_exports_original_store_paths(tmp_path: Path) -> None:
    """macos-15 rejected nix-store --add of copied .drv files (37851246740)."""
    planner_drv, drv_path = _planner_drv(tmp_path)
    cache = tmp_path / WARMUP_DRVS_NAME
    export_calls: list[list[str]] = []

    def export_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        export_calls.append(args)
        assert args[:3] == ["nix", "copy", "--derivation"]
        return _store_ok(args)

    export_warmup_drvs((), cache)
    assert cache.is_dir()
    assert not (cache / WARMUP_DRVS_CACHE_INFO).exists()
    export_warmup_drvs((str(planner_drv),), cache, run=export_run)
    assert export_calls[0][:3] == ["nix", "copy", "--derivation"]
    assert "--to" in export_calls[0]
    assert (
        export_calls[0][export_calls[0].index("--to") + 1] == cache.resolve().as_uri()
    )
    assert str(planner_drv) in export_calls[0]
    assert "--add" not in {part for call in export_calls for part in call}
    assert "--export" not in {part for call in export_calls for part in call}
    with pytest.raises(WarmupError, match="planner is missing"):
        export_warmup_drvs((drv_path,), tmp_path / "missing-src")


def test_warmup_drv_closure_export_failures_fail_closed(tmp_path: Path) -> None:
    """A failed file:// copy must not look like a usable Darwin cache."""
    planner_drv, _drv_path = _planner_drv(tmp_path)

    def copy_fail(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return _store_fail(args, stderr="copy failed")

    with pytest.raises(WarmupError, match="failed to export"):
        export_warmup_drvs((str(planner_drv),), tmp_path / "export-fail", run=copy_fail)

    def copy_empty_streams(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        return _store_fail(args, stdout=b"", stderr=b"")

    with pytest.raises(WarmupError, match="nix copy --derivation failed"):
        export_warmup_drvs(
            (str(planner_drv),), tmp_path / "empty-streams", run=copy_empty_streams
        )


def test_warmup_drv_closure_imports_file_store(tmp_path: Path) -> None:
    """Darwin copies, GC-roots, and path-info-checks the original .drv paths."""
    _planner_file, drv_path = _planner_drv(tmp_path)
    cache = _write_file_cache(tmp_path / WARMUP_DRVS_NAME)
    imported: list[list[str]] = []

    def import_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        imported.append(args)
        return _store_ok(args, stdout=drv_path)

    import_warmup_drvs((), tmp_path / "no-cache")
    with pytest.raises(WarmupError, match="warmup drv cache missing"):
        import_warmup_drvs((drv_path,), tmp_path / "no-cache", run=import_run)
    empty_cache = tmp_path / "empty-cache"
    empty_cache.mkdir()
    with pytest.raises(WarmupError, match="warmup drv cache missing"):
        import_warmup_drvs((drv_path,), empty_cache, run=import_run)
    import_warmup_drvs((drv_path,), cache, run=import_run)
    assert imported[0][:3] == ["nix", "copy", "--derivation"]
    assert "--from" in imported[0]
    assert "--no-check-sigs" in imported[0]
    assert "min-free" in imported[0]
    assert imported[0][imported[0].index("min-free") + 1] == "0"
    assert imported[0][imported[0].index("--from") + 1] == cache.resolve().as_uri()
    assert imported[1][:3] == ["nix", "build", "--out-link"]
    assert "--offline" in imported[1]
    assert drv_path in imported[1]
    assert imported[1][3] == str(cache / WARMUP_DRVS_ROOTS / Path(drv_path).name)
    assert imported[2][:2] == ["nix", "path-info"]
    assert drv_path in imported[2]
    with pytest.raises(WarmupError, match="failed to import"):
        import_warmup_drvs(
            (drv_path,),
            cache,
            run=lambda args, **_kwargs: _store_fail(args, stderr="import failed"),
        )
    with pytest.raises(WarmupError, match="nix copy --derivation failed"):
        import_warmup_drvs(
            (drv_path,),
            cache,
            run=lambda args, **_kwargs: _store_fail(args, stdout=b"", stderr=b""),
        )
    assert _planner_file.is_file()


def test_warmup_gc_root_argv_rejects_add_root_without_operation() -> None:
    """Exact #1256 argv: nix-store --add-root is a modifier, not an operation."""
    drv = "/nix/store/byzk43f67r0kgqvpwmx6cwynxg8qjf5g-patchutils-0.3.3.drv"
    root = Path(
        "/tmp/warmup-drvs/gcroots/byzk43f67r0kgqvpwmx6cwynxg8qjf5g-patchutils-0.3.3.drv"
    )
    malformed = ["nix-store", "--add-root", str(root), "--indirect", drv]
    assert not nix_store_argv_has_operation(())
    assert not nix_store_argv_has_operation(malformed)
    assert nix_store_argv_has_operation([
        "nix-store",
        "--realise",
        "--add-root",
        str(root),
        drv,
    ])
    assert not nix_store_argv_has_operation([
        "nix",
        "build",
        "--out-link",
        str(root),
        drv,
    ])
    args = warmup_drv_root_args(root, drv)
    assert args[:3] == ["nix", "build", "--out-link"]
    assert args[3] == str(root)
    assert "--offline" in args
    assert drv in args
    assert "--add-root" not in args
    assert "--indirect" not in args
    assert args != malformed


def test_warmup_drv_closure_import_roots_and_path_info_fail_closed(
    tmp_path: Path,
) -> None:
    """#1255 would have died at path-info instead of nix build don't-know-how."""
    _planner_file, drv_path = _planner_drv(tmp_path)
    cache = _write_file_cache(tmp_path / WARMUP_DRVS_NAME)

    def root_fail(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["nix", "copy"]:
            return _store_ok(args)
        return _store_fail(args, stderr="add-root failed")

    with pytest.raises(WarmupError, match="failed to GC-root"):
        import_warmup_drvs((drv_path,), cache, run=root_fail)

    def path_info_fail(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["nix", "path-info"]:
            return _store_fail(args, stderr="don't know how to build")
        return _store_ok(args)

    with pytest.raises(WarmupError, match="imported warmup .drv missing"):
        import_warmup_drvs((drv_path,), cache, run=path_info_fail)


def test_warmup_drv_closure_copy_batches_paths(tmp_path: Path) -> None:
    """ARG_MAX-safe copy keeps every planner .drv in the file:// cache."""
    planner_dir = tmp_path / "planner"
    planner_dir.mkdir()
    drvs = []
    for index in range(129):
        path = planner_dir / f"{index:032d}-x.drv"
        path.write_text("drv")
        drvs.append(str(path))
    export_batches: list[tuple[str, ...]] = []

    def batched_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        assert args[:3] == ["nix", "copy", "--derivation"]
        export_batches.append(_copy_paths(args))
        return _store_ok(args)

    export_warmup_drvs(tuple(drvs), tmp_path / "batched", run=batched_run)
    assert [len(batch) for batch in export_batches] == [128, 1]


def test_rust_warmup_layers_and_slots_keep_dependency_order() -> None:
    shared = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-shared"
    rust_a = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_a-1"
    rust_b = "/nix/store/cccccccccccccccccccccccccccccccc-rust_b-1"
    rust_c = "/nix/store/dddddddddddddddddddddddddddddddd-rust_c-1"
    rust_d = "/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-rust_d-1"
    rust_e = "/nix/store/ffffffffffffffffffffffffffffffff-rust_e-1"
    payload = _graph(
        shared,
        rust_a,
        rust_b,
        rust_c,
        rust_d,
        rust_e,
        edges={rust_b: (rust_a,), rust_c: (rust_b,)},
    )
    warmup = frozenset({shared, rust_a, rust_b, rust_c, rust_d, rust_e})
    layers = rust_warmup_layers(warmup, (payload,))
    assert layers[0] == (shared,)
    assert rust_a in layers[1]
    assert rust_d in layers[1]
    assert rust_e in layers[1]
    assert layers[2] == (rust_b,)
    assert layers[3] == (rust_c,)
    assert RUST_WARMUP_SLOTS == 5
    slot0 = slot_warmup_paths(layers, 0)
    assert slot0[0] == shared
    assert rust_b in slot0
    assert rust_c in slot0
    assert rust_a not in slot_warmup_paths(layers, 1)
    assert skip_cached_warmup_paths(
        (rust_a, rust_b, rust_a), present=lambda path: path.endswith("rust_a-1")
    ) == (rust_b,)
    with pytest.raises(WarmupError, match="slot must be"):
        slot_warmup_paths(layers, 5)
    with pytest.raises(WarmupError, match="width must be"):
        slot_warmup_paths(layers, 0, width=0)
    cycle = _graph(rust_a, rust_b, edges={rust_a: (rust_b,), rust_b: (rust_a,)})
    with pytest.raises(WarmupError, match="dependency cycle"):
        rust_warmup_layers(frozenset({rust_a, rust_b}), (cycle,))
    with pytest.raises(WarmupError, match="missing from Darwin graphs"):
        rust_warmup_layers(frozenset({rust_a}), (_graph(shared),))
    assert rust_warmup_layers(frozenset({shared}), (_graph(shared),)) == ((shared,),)
    assert rust_warmup_layers(frozenset(), (_graph(shared),)) == ()


def test_rust_agent_ui_partition_and_realize_print_logs(tmp_path: Path) -> None:
    """agent_ui drvs realize last with ``-L`` so rustc logs are not truncated."""
    agent = "/nix/store/ks6dzvchaaaaaaaaaaaaaaaaaaaaaaaa-rust_agent_ui-0.1.0.drv"
    models = (
        "/nix/store/h3crq11aaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_language_models-0.1.0.drv"
    )
    cloud = (
        "/nix/store/cloudaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_language_models_cloud-0.1.0"
    )
    assert is_rust_agent_ui_store_path(agent)
    assert not is_rust_agent_ui_store_path(models)
    assert is_rust_language_models_store_path(models)
    assert not is_rust_language_models_store_path(cloud)
    zed = "/nix/store/av7xckfpaaaaaaaaaaaaaaaaaaaaaaaaa-rust_zed-1.25.0.drv"
    zed_font = (
        "/nix/store/fontkitaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_zed-font-kit-0.14.1-zed"
    )
    assert is_rust_zed_store_path(zed)
    assert not is_rust_zed_store_path(zed_font)
    assert is_named_rust_crate_store_path(zed, "zed")
    assert is_extension_host_family_store_path(models)
    assert not is_extension_host_family_store_path(zed)
    assert is_rust_extension_host_store_path(
        "/nix/store/5crb9axiaaaaaaaaaaaaaaaaaaaaaaaaa-rust_extension_host-0.1.0-lib"
    )
    assert not is_rust_extension_host_store_path(models)
    assert _store_output_rest("not-a-store") == "a-store"
    assert partition_agent_ui_drvs((models, agent, agent)) == ((models,), (agent,))
    assert partition_rust_zed_drvs((models, zed, zed)) == ((models,), (zed,))
    host_lib = (
        "/nix/store/5crb9axiaaaaaaaaaaaaaaaaaaaaaaaaa-rust_extension_host-0.1.0-lib"
    )
    assert extension_host_family_warmup_outputs(((host_lib, zed), (models,))) == (
        host_lib,
        models,
    )
    models_lib = (
        "/nix/store/h3crq11aaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_language_models-0.1.0-lib"
    )
    assert language_models_warmup_outputs(((cloud, models_lib), (agent,))) == (
        models_lib,
    )
    refs = language_models_input_drvs(
        (agent,),
        run=lambda args, **_kwargs: subprocess.CompletedProcess(
            args, 0, "\n".join((models, cloud + ".drv", agent)), ""
        ),
    )
    assert refs == (models,)
    byte_refs = language_models_input_drvs(
        (agent,),
        run=lambda args, **_kwargs: subprocess.CompletedProcess(
            args, 0, b"\n".join((models.encode(), cloud.encode() + b".drv")), b""
        ),
    )
    assert byte_refs == (models,)
    with pytest.raises(WarmupError, match="failed to query rust crate inputs"):
        language_models_input_drvs(
            (agent,),
            run=lambda args, **_kwargs: subprocess.CompletedProcess(
                args, 1, "", "nix-store: dead"
            ),
        )

    realize_calls: list[list[str]] = []

    def realize_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        realize_calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    assert (
        realize_warmup_outputs(
            (agent,),
            flake_root=tmp_path,
            run=realize_run,
            print_build_logs=True,
        )
        == ()
    )
    assert "-L" in realize_calls[0]
    realize_calls.clear()
    assert (
        realize_warmup_outputs(
            (models,),
            flake_root=tmp_path,
            run=realize_run,
            print_build_logs=True,
            force_local=True,
        )
        == ()
    )
    assert any("--query" in args and "--outputs" in args for args in realize_calls)
    builds = [args for args in realize_calls if args[:2] == ["nix", "build"]]
    assert any("--max-jobs" in args and "0" in args for args in builds)
    local = next(args for args in builds if "--no-substitute" in args)
    assert "--rebuild" not in local
    assert "-L" in local


def test_language_models_input_drvs_defaults_to_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Planner-absent language_models is discovered with the default store runner."""
    agent = "/nix/store/ks6dzvchaaaaaaaaaaaaaaaaaaaaaaaa-rust_agent_ui-0.1.0.drv"
    models = (
        "/nix/store/h3crq11aaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_language_models-0.1.0.drv"
    )

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, f"{models}\n", "")

    monkeypatch.setattr("lib.update.ci.warmup.subprocess.run", run)
    assert language_models_input_drvs((agent,)) == (models,)


def test_delete_local_store_paths_removes_existing_and_fails_closed(
    tmp_path: Path,
) -> None:
    """Force-local delete is this store only; leftover paths fail closed."""
    target = tmp_path / "rust_language_models-0.1.0-lib"
    target.write_text("nar", encoding="utf-8")

    def remove(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        for path in args[args.index("--ignore-liveness") + 1 :]:
            Path(path).unlink()
        return subprocess.CompletedProcess(args, 0, "", "")

    delete_local_store_paths((str(target),), run=remove)
    assert not target.exists()
    delete_local_store_paths((str(tmp_path / "missing-lib"),), run=remove)
    target.write_text("nar", encoding="utf-8")
    with pytest.raises(WarmupError, match="failed to delete local outputs"):
        delete_local_store_paths(
            (str(target),),
            run=lambda args, **_kwargs: subprocess.CompletedProcess(
                args, 1, "", "busy"
            ),
        )


def test_query_and_delete_warmup_drv_outputs_use_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Force-local queries outputs then deletes only paths that exist."""
    drv = "/nix/store/lmdrvaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_language_models-0.1.0.drv"
    output = tmp_path / "rust_language_models-0.1.0-lib"
    output.write_text("nar", encoding="utf-8")
    seen: list[list[str]] = []

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(args)
        if "--outputs" in args:
            return subprocess.CompletedProcess(args, 0, f"{output}\n", "")
        if "--delete" in args:
            Path(args[-1]).unlink()
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 1, "", "unexpected")

    monkeypatch.setattr("lib.update.ci.warmup.subprocess.run", run)
    delete_warmup_drv_outputs((drv,))
    assert not output.exists()
    assert any("--outputs" in args for args in seen)
    assert any("--delete" in args and "--ignore-liveness" in args for args in seen)
    with pytest.raises(WarmupError, match="failed to query outputs"):
        delete_warmup_drv_outputs(
            (drv,),
            run=lambda args, **_kwargs: subprocess.CompletedProcess(
                args, 1, "", "no drv"
            ),
        )
    assert query_drv_outputs(
        drv,
        run=lambda args, **_kwargs: subprocess.CompletedProcess(
            args, 0, b"/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-out\n", b""
        ),
    ) == ("/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-out",)
