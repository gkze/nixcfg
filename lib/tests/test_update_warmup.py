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
    RootWarmupStats,
    ShardLocalBuildReport,
    WarmupError,
    WarmupPlan,
    _ar_members,
    _drv_rustc_env,
    _language_models_rlibs,
    _printable_strings,
    _store_output_rest,
    assert_local_build_threshold,
    darwin_output_paths,
    default_cache_present,
    describe_rlib,
    diagnose_agent_ui_language_models,
    dump_agent_ui_build_log,
    eval_root_darwin_outputs,
    export_warmup_drvs,
    extra_filename_from_rlib_name,
    import_warmup_drvs,
    intersect_missing,
    is_crate2nix_rust_output,
    is_rust_agent_ui_store_path,
    is_rust_language_models_store_path,
    language_models_output_drvs,
    load_warmup_plan,
    nix_store_argv_has_operation,
    partition_agent_ui_drvs,
    plan_darwin_warmup,
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


def _gnu_ar(members: list[tuple[str, bytes]]) -> bytes:
    buf = bytearray(b"!<arch>\n")
    for name, payload in members:
        header_name = f"{name}/".encode()[:16].ljust(16)
        buf.extend(header_name)
        buf.extend(b"0".ljust(12))
        buf.extend(b"0".ljust(6))
        buf.extend(b"0".ljust(6))
        buf.extend(b"644".ljust(8))
        buf.extend(str(len(payload)).encode().ljust(10))
        buf.extend(b"`\n")
        buf.extend(payload)
        if len(payload) % 2:
            buf.append(0)
    return bytes(buf)


def _bsd_ar(name: str, payload: bytes) -> bytes:
    name_bytes = name.encode() + b"\0"
    body = name_bytes + payload
    header_name = f"#1/{len(name_bytes)}".encode().ljust(16)
    buf = bytearray(b"!<arch>\n")
    buf.extend(header_name)
    buf.extend(b"0".ljust(12))
    buf.extend(b"0".ljust(6))
    buf.extend(b"0".ljust(6))
    buf.extend(b"644".ljust(8))
    buf.extend(str(len(body)).encode().ljust(10))
    buf.extend(b"`\n")
    buf.extend(body)
    if len(body) % 2:
        buf.append(0)
    return bytes(buf)


def _plan(output_drvs: dict[str, str]) -> WarmupPlan:
    return WarmupPlan(
        schemaVersion=1,
        system="aarch64-darwin",
        substituters=("https://gkze.cachix.org",),
        warmupOutputs=tuple(output_drvs),
        rustLayers=(tuple(output_drvs),),
        outputDrvs=output_drvs,
        perRoot={
            "darwin-argus": RootWarmupStats(
                outputs=len(output_drvs),
                missing=len(output_drvs),
                warmup=len(output_drvs),
                remaining=0,
            )
        },
        shards=(
            ShardLocalBuildReport(
                shard="darwin-argus",
                roots=("darwin-argus",),
                remaining=0,
                remaining_rust_crates=0,
            ),
        ),
        notes="fixture",
    )


def test_rust_agent_ui_and_language_models_store_path_helpers() -> None:
    agent = "/nix/store/ks6dzvchaaaaaaaaaaaaaaaaaaaaaaaa-rust_agent_ui-0.1.0.drv"
    models = (
        "/nix/store/h3crq11aaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_language_models-0.1.0-lib"
    )
    assert is_rust_agent_ui_store_path(agent)
    assert not is_rust_agent_ui_store_path(models)
    assert is_rust_language_models_store_path(models)
    assert not is_rust_language_models_store_path(agent)
    assert _store_output_rest("not-a-store") == "a-store"
    assert extra_filename_from_rlib_name("liblanguage_models-f75b2474e2.rlib") == (
        "f75b2474e2"
    )
    assert extra_filename_from_rlib_name("liblanguage_models.rlib") is None
    assert partition_agent_ui_drvs((models + ".drv", agent, agent)) == (
        (models + ".drv",),
        (agent,),
    )
    assert language_models_output_drvs({models: models + ".drv", agent: agent}) == {
        models: models + ".drv"
    }
    assert language_models_output_drvs({
        "/nix/store/plain": "/nix/store/aaaa-rust_language_models-0.1.0.drv"
    }) == {"/nix/store/plain": "/nix/store/aaaa-rust_language_models-0.1.0.drv"}


def test_describe_rlib_reads_svh_target_and_ar_members(tmp_path: Path) -> None:
    payload = b"".join([
        b"rustc 1.98.1 (48a229cea 2026-09-01)\0",
        b"aarch64-apple-darwin\0",
        b"language_models\0",
        b"trailing",
    ])
    gnu = tmp_path / "liblanguage_models-f75b2474e2.rlib"
    gnu.write_bytes(_gnu_ar([("lib.rmeta", payload), ("lm.0.o", b"obj")]))
    described = describe_rlib(gnu)
    assert described["extraFilename"] == "f75b2474e2"
    assert described["rustc"] == ["rustc 1.98.1 (48a229cea 2026-09-01)"]
    assert described["triples"] == ["aarch64-apple-darwin"]
    assert described["crateNames"] == ["language_models"]
    assert described["members"] == [
        {"name": "lib.rmeta", "size": len(payload)},
        {"name": "lm.0.o", "size": 3},
    ]
    bsd = tmp_path / "liblanguage_models-deadbeef01.rlib"
    bsd.write_bytes(_bsd_ar("lib.rmeta", payload))
    assert describe_rlib(bsd)["members"][0]["name"] == "lib.rmeta"
    empty = tmp_path / "libempty.rlib"
    empty.write_bytes(b"not-an-archive")
    assert describe_rlib(empty)["members"] == []
    bad_size = bytearray(b"!<arch>\n")
    bad_size.extend(b"lib.rmeta/".ljust(16))
    bad_size.extend(b"0".ljust(12))
    bad_size.extend(b"0".ljust(6))
    bad_size.extend(b"0".ljust(6))
    bad_size.extend(b"644".ljust(8))
    bad_size.extend(b"not-a-size".ljust(10))
    bad_size.extend(b"`\n")
    assert _ar_members(bytes(bad_size)) == ()
    bad_bsd = bytearray(b"!<arch>\n")
    bad_bsd.extend(b"#1/zz".ljust(16))
    bad_bsd.extend(b"0".ljust(12))
    bad_bsd.extend(b"0".ljust(6))
    bad_bsd.extend(b"0".ljust(6))
    bad_bsd.extend(b"644".ljust(8))
    bad_bsd.extend(b"4".ljust(10))
    bad_bsd.extend(b"`\nxxxx")
    assert _ar_members(bytes(bad_bsd)) == (("#1/zz", 4),)
    assert _printable_strings(b"short\0ok-string") == ("ok-string",)
    assert _language_models_rlibs(str(tmp_path / "missing-lib")) == ()


def test_drv_rustc_env_filters_metadata_and_target() -> None:
    assert _drv_rustc_env("nope") == {}
    assert _drv_rustc_env({"drv": "nope"}) == {}
    assert _drv_rustc_env({"drv": {"env": "nope"}}) == {}
    assert _drv_rustc_env({
        "drv": {
            "env": {
                "NIX_RUSTFLAGS": "-C metadata=f75b2474e2 --target aarch64-apple-darwin",
                "ignored": 1,
                "CARGO_CRATE_NAME": "language_models",
                "unrelated": "plain",
            }
        }
    }) == {
        "NIX_RUSTFLAGS": "-C metadata=f75b2474e2 --target aarch64-apple-darwin",
        "CARGO_CRATE_NAME": "language_models",
    }


def test_diagnose_agent_ui_language_models_is_slot_gated(
    tmp_path: Path,
) -> None:
    lines: list[str] = []
    calls: list[list[str]] = []

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    diagnose_agent_ui_language_models(
        _plan({}),
        slot_paths=("/nix/store/aaaa-rust_gpui-0.1.0",),
        flake_root=tmp_path,
        warmup_drvs=tmp_path / "warmup-drvs",
        realize_drvs=(),
        run=run,
        write=lines.append,
    )
    assert calls == []
    assert lines == []
    diagnose_agent_ui_language_models(
        _plan({}),
        slot_paths=("/nix/store/ks6dzvchaaaaaaaaaaaaaaaaaaaaaaaa-rust_agent_ui-0.1.0",),
        flake_root=tmp_path,
        warmup_drvs=tmp_path / "warmup-drvs",
        realize_drvs=(),
        run=run,
        write=lines.append,
    )
    assert "no rust_language_models outputs" in lines[-1]


def test_diagnose_agent_ui_language_models_checks_substituted_rlib(
    tmp_path: Path,
) -> None:
    lib_out = (
        tmp_path / "h3crq11aaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_language_models-0.1.0-lib"
    )
    (lib_out / "lib").mkdir(parents=True)
    rlib = lib_out / "lib" / "liblanguage_models-f75b2474e2.rlib"
    rlib.write_bytes(
        _gnu_ar([
            (
                "lib.rmeta",
                b"rustc 1.98.1 (48a229cea 2026-09-01)\0"
                b"aarch64-apple-darwin\0language_models\0",
            )
        ])
    )
    sibling = tmp_path / "7h1bjnkidcmsaaaaaaaaaaaaaaaaaaaaaa-rust_language_models-0.1.0"
    (sibling / "lib").mkdir(parents=True)
    (sibling / "lib" / "liblanguage_models-f75b2474e2.rlib").write_bytes(
        rlib.read_bytes()
    )
    drv = "/nix/store/lmdrvaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_language_models-0.1.0.drv"
    cache = tmp_path / "warmup-drvs"
    cache.mkdir()
    imported: list[tuple[object, object]] = []
    lines: list[str] = []
    calls: list[list[str]] = []

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        if args[:3] == ["nix", "derivation", "show"]:
            return subprocess.CompletedProcess(
                args,
                0,
                json.dumps({
                    drv: {
                        "env": {
                            "NIX_RUSTFLAGS": (
                                "-C metadata=f75b2474e2 --target aarch64-apple-darwin"
                            )
                        }
                    }
                }),
                "",
            )
        if "--check" in args:
            return subprocess.CompletedProcess(args, 1, "", "output differs")
        return subprocess.CompletedProcess(args, 0, "ok", "")

    diagnose_agent_ui_language_models(
        _plan({str(lib_out): drv, str(sibling): drv}),
        slot_paths=("/nix/store/ks6dzvchaaaaaaaaaaaaaaaaaaaaaaaa-rust_agent_ui-0.1.0",),
        flake_root=tmp_path,
        warmup_drvs=cache,
        realize_drvs=(),
        run=run,
        import_drvs=lambda paths, dest: imported.append((tuple(paths), dest)),
        write=lines.append,
    )
    assert imported == [((drv,), cache)]
    assert any(
        args[:3] == ["nix", "build", "--no-link"] and "--check" in args
        for args in calls
    )
    assert any("--max-jobs" in args and "0" in args for args in calls)
    assert any("output differs" in line for line in lines)
    assert any("f75b2474e2" in line for line in lines)
    assert any("aarch64-apple-darwin" in line for line in lines)
    assert any("differed from the substituted path" in line for line in lines)

    missing_lib = tmp_path / "missing-rust_language_models-0.1.0-lib"
    lines.clear()
    diagnose_agent_ui_language_models(
        _plan({str(missing_lib): drv}),
        slot_paths=("/nix/store/ks6dzvchaaaaaaaaaaaaaaaaaaaaaaaa-rust_agent_ui-0.1.0",),
        flake_root=tmp_path,
        warmup_drvs=tmp_path / "absent-cache",
        realize_drvs=(drv,),
        run=lambda args, **_kwargs: (
            subprocess.CompletedProcess(args, 0, "{", "not-json")
            if args[:3] == ["nix", "derivation", "show"]
            else subprocess.CompletedProcess(args, 0, "", "")
        ),
        write=lines.append,
    )
    assert any("no liblanguage_models*.rlib" in line for line in lines)
    assert any("derivation show JSON" in line for line in lines)
    assert any("matched the substituted path" in line for line in lines)


def test_dump_agent_ui_build_log_and_realize_print_logs(tmp_path: Path) -> None:
    lines: list[str] = []
    calls: list[list[str]] = []

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, "locator debug", "")

    dump_agent_ui_build_log(
        (
            "/nix/store/aaaa-rust_gpui-0.1.0.drv",
            "/nix/store/ks6dzvchaaaaaaaaaaaaaaaaaaaaaaaa-rust_agent_ui-0.1.0.drv",
            "/nix/store/ks6dzvchaaaaaaaaaaaaaaaaaaaaaaaa-rust_agent_ui-0.1.0.drv",
        ),
        run=run,
        write=lines.append,
    )
    assert calls == [
        [
            "nix",
            "log",
            "/nix/store/ks6dzvchaaaaaaaaaaaaaaaaaaaaaaaa-rust_agent_ui-0.1.0.drv",
        ]
    ]
    assert any("locator debug" in line for line in lines)

    realize_calls: list[list[str]] = []

    def realize_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        realize_calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    assert (
        realize_warmup_outputs(
            ("/nix/store/ks6dzvchaaaaaaaaaaaaaaaaaaaaaaaa-rust_agent_ui-0.1.0.drv",),
            flake_root=tmp_path,
            run=realize_run,
            print_build_logs=True,
        )
        == ()
    )
    assert "-L" in realize_calls[0]


def test_diagnose_defaults_to_print_and_subprocess(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """write=None and run=None are the hosted rust-warmup path."""
    runs: list[list[str]] = []

    def fake_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        runs.append(list(args))
        return subprocess.CompletedProcess(args, 1, "", "nix missing")

    monkeypatch.setattr("lib.update.ci.warmup.subprocess.run", fake_run)
    diagnose_agent_ui_language_models(
        _plan({
            "/nix/store/h3crq11aaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_language_models-0.1.0-lib": (
                "/nix/store/lmdrvaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_language_models-0.1.0.drv"
            )
        }),
        slot_paths=("/nix/store/ks6dzvchaaaaaaaaaaaaaaaaaaaaaaaa-rust_agent_ui-0.1.0",),
        flake_root=tmp_path,
        warmup_drvs=tmp_path / "absent",
        realize_drvs=(),
    )
    dump_agent_ui_build_log((
        "/nix/store/ks6dzvchaaaaaaaaaaaaaaaaaaaaaaaa-rust_agent_ui-0.1.0.drv",
    ))
    captured = capsys.readouterr()
    assert "TEMPORARY #221 rust_agent_ui / language_models diagnostic" in captured.out
    assert "nix missing" in captured.out
    assert any(args[:2] == ["nix", "build"] for args in runs)
    assert any(args[:2] == ["nix", "log"] for args in runs)
