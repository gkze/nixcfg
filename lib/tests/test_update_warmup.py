"""Structural Darwin warmup is the missing shared drv set, not a zed list."""

import json
import subprocess
from pathlib import Path

import pytest

from lib.update.ci.shard_plan import ClosureShard
from lib.update.ci.warmup import (
    MAX_SHARD_LOCAL_BUILDS,
    RUST_WARMUP_SLOTS,
    SETTINGS_MEMBER_CRATES,
    WARMUP_DRVS_CACHE_INFO,
    WARMUP_DRVS_NAME,
    WARMUP_DRVS_ROOTS,
    WARMUP_FATAL_BUILD_LIMIT,
    WARMUP_PLAN_NAME,
    ShardLocalBuildReport,
    WarmupError,
    WarmupFatalError,
    _store_output_rest,
    assert_force_local_dry_run,
    assert_local_build_threshold,
    assert_zed_family_realize_set,
    canary_crate_warmup_paths,
    classify_slot_warmup_will_be_built,
    compiler_input_drvs,
    darwin_output_paths,
    default_cache_present,
    eval_root_darwin_outputs,
    export_warmup_drvs,
    extension_host_family_warmup_outputs,
    force_local_dry_run_builds,
    import_warmup_drvs,
    intersect_missing,
    is_compiler_local_helper_store_path,
    is_compiler_must_substitute_store_path,
    is_crate2nix_rust_output,
    is_extension_host_family_store_path,
    is_force_local_allowed_build,
    is_named_rust_crate_store_path,
    is_rust_agent_ui_store_path,
    is_rust_extension_host_store_path,
    is_rust_language_models_store_path,
    is_rust_zed_store_path,
    is_safe_rust_warmup_other,
    is_settings_family_store_path,
    is_source_fetch_store_path,
    is_svh_sensitive_store_path,
    is_tree_sitter_family_store_path,
    is_zed_editor_nightly_store_path,
    language_models_input_drvs,
    language_models_warmup_outputs,
    load_warmup_plan,
    nix_store_argv_has_operation,
    parse_canary_crates,
    parse_canary_slots,
    parse_kick_canary_crates,
    parse_kick_canary_slots,
    partition_agent_ui_drvs,
    partition_compiler_input_drvs,
    partition_rust_warmup_others,
    partition_rust_zed_drvs,
    partition_settings_family_drvs,
    partition_zed_editor_nightly_drvs,
    plan_darwin_warmup,
    query_drv_outputs,
    raise_if_warmup_fatal,
    realize_warmup_outputs,
    rust_compile_input_drvs,
    rust_warmup_layers,
    rustc_generation_ids,
    settings_family_warmup_outputs,
    shard_remaining_outputs,
    skip_cached_warmup_paths,
    slot_warmup_paths,
    substitutable_paths,
    unique_drvs_for_outputs,
    warmup_build_installable,
    warmup_drv_root_args,
    warmup_fatal_line,
    warmup_output_drvs,
    write_warmup_plan,
    zed_family_cachix_presence,
    zed_family_realize_policy,
    zed_family_warmup_outputs,
)
from lib.update.derivation_validation import RootClosureManifest
from lib.update.paths import REPO_ROOT


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
            "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-cargo-package-sha2-0.10.9",
            "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_gpui-1",
            "/nix/store/dddddddddddddddddddddddddddddddd-argus-only",
            "/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-cached",
            "/nix/store/ffffffffffffffffffffffffffffffff-goose-cli-1.51.0",
            linux="/nix/store/cccccccccccccccccccccccccccccccc-vm",
        ),
        "darwin-rocinante": _graph(
            "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-cargo-package-sha2-0.10.9",
            "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_gpui-1",
            "/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-cached",
            "/nix/store/ffffffffffffffffffffffffffffffff-goose-cli-1.51.0",
        ),
        "home-george": _graph(
            "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-cargo-package-sha2-0.10.9",
            "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_gpui-1",
            "/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-cached",
            "/nix/store/ffffffffffffffffffffffffffffffff-goose-cli-1.51.0",
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
        "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-cargo-package-sha2-0.10.9",
        "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_gpui-1",
    )
    assert plan.rust_layers == (
        ("/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-cargo-package-sha2-0.10.9",),
        ("/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_gpui-1",),
    )
    assert plan.output_drvs == {
        "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-cargo-package-sha2-0.10.9": (
            "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-cargo-package-sha2-0.10.9.drv"
        ),
        "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_gpui-1": (
            "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_gpui-1.drv"
        ),
    }
    assert (
        "/nix/store/ffffffffffffffffffffffffffffffff-goose-cli-1.51.0"
        not in plan.warmup_outputs
    )
    assert plan.per_root["darwin-argus"].remaining == 2
    assert plan.shards[0].remaining == 2
    assert plan.shards[1].remaining == 1
    path = tmp_path / WARMUP_PLAN_NAME
    write_warmup_plan(path, plan)
    loaded = load_warmup_plan(path)
    assert loaded.warmup_outputs == plan.warmup_outputs
    with pytest.raises(WarmupError, match="invalid warmup plan"):
        load_warmup_plan(tmp_path / "missing.json")
    huge_unique = [f"/nix/store/{index:032d}-unique-{index}" for index in range(401)]
    graphs["darwin-argus"] = _graph(
        "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-cargo-package-sha2-0.10.9",
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
        "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-cargo-package-sha2-0.10.9",
        "/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-cached",
    )

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, json.dumps(graph), "")

    monkeypatch.setattr("lib.update.ci.warmup.subprocess.run", run)
    shown = eval_root_darwin_outputs(tmp_path, "darwin-argus")
    assert shown == frozenset({
        "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-cargo-package-sha2-0.10.9",
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
        "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-cargo-package-sha2-0.10.9",
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
    shared = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-cargo-package-sha2-0.10.9"
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
    leaf = "/nix/store/hhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhh-goose-cli-1.51.0"
    assert rust_warmup_layers(frozenset({shared}), (_graph(shared),)) == ((shared,),)
    assert rust_warmup_layers(frozenset({leaf}), (_graph(leaf),)) == ()
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
    settings = "/nix/store/2y7vj1wq5nz030asgn7rhipbcx5aya89-rust_settings-0.1.0.drv"
    content = "/nix/store/ma14flyg1v5b4vhinb2l0klw1xmdg9nz-rust_settings_content-0.1.0"
    settings_ui = "/nix/store/setuiaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_settings_ui-0.1.0"
    assert is_settings_family_store_path(settings)
    assert is_settings_family_store_path(content)
    assert not is_settings_family_store_path(settings_ui)
    assert is_svh_sensitive_store_path(settings)
    assert partition_settings_family_drvs((zed, settings, content, settings)) == (
        (zed,),
        (settings, content),
    )
    assert settings_family_warmup_outputs(((content,), (settings, zed))) == (
        content,
        settings,
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
    builds = [args for args in realize_calls if args[:2] == ["nix", "build"]]
    assert builds
    assert all("--max-jobs" not in args for args in builds)
    assert all("--delete" not in args for args in realize_calls)
    local = next(args for args in builds if "--no-substitute" in args)
    assert "--rebuild" not in local
    assert "--keep-going" not in local
    assert "-L" in local


def test_compiler_input_drvs_uses_requisites_and_keeps_src() -> None:
    """#1263: substitute the non-rust closure, including crate -src.

    rust_* requisites (tree-sitter, language_models) stay out so
    force-local cannot intern a Cachix rlib (#1270 canary E0463).
    """
    agent = "/nix/store/ks6dzvchaaaaaaaaaaaaaaaaaaaaaaaa-rust_agent_ui-0.1.0.drv"
    models = (
        "/nix/store/h3crq11aaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_language_models-0.1.0.drv"
    )
    cloud = (
        "/nix/store/cloudaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_language_models_cloud-0.1.0"
    )
    zed = "/nix/store/av7xckfpaaaaaaaaaaaaaaaaaaaaaaaaa-rust_zed-1.25.0.drv"
    nightly = "/nix/store/rlcwld41aaaaaaaaaaaaaaaaaaaaaaaaa-zed-editor-nightly-unstable-f16f965.drv"
    rustc = "/nix/store/xbq69m0caaaaaaaaaaaaaaaaaaaaaaaa-rustc-1.98.1.drv"
    bmake = "/nix/store/fcy73hrwaaaaaaaaaaaaaaaaaaaaaaaa-bmake-20260313.tar.gz.drv"
    src = (
        "/nix/store/n2g2dzs3aaaaaaaaaaaaaaaaaaaaaaaaa-"
        "zed-editor-nightly-extension_host-src.drv"
    )
    tree_sitter = (
        "/nix/store/1zsfiw8m72a6ql2wx3f5bpq3mn7vnw77-rust_tree-sitter-0.27.0.drv"
    )
    assert is_zed_editor_nightly_store_path(nightly)
    assert not is_zed_editor_nightly_store_path(src)
    assert partition_zed_editor_nightly_drvs((models, nightly, nightly)) == (
        (models,),
        (nightly,),
    )
    seen_query: list[list[str]] = []

    def query_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        seen_query.append(args)
        return subprocess.CompletedProcess(
            args,
            0,
            "\n".join((rustc, models, nightly, bmake, src, tree_sitter)),
            "",
        )

    assert compiler_input_drvs((zed,), run=query_run) == (rustc, bmake, src)
    assert rust_compile_input_drvs((zed,), run=query_run) == (models, tree_sitter)
    assert partition_compiler_input_drvs((rustc, bmake, src, bmake)) == (
        (rustc,),
        (bmake, src),
    )
    patchutils = "/nix/store/m6399k05aaqiz3mx4cdpkdqr4hp05kmj-patchutils-0.3.3.drv"
    patchutils_tar = (
        "/nix/store/r59fr714v93cagij26k3082icsksrsv1-patchutils-0.3.3.tar.xz"
    )
    assert is_compiler_local_helper_store_path(patchutils)
    assert not is_compiler_local_helper_store_path(patchutils_tar)
    assert not is_compiler_local_helper_store_path(rustc)
    assert partition_compiler_input_drvs((rustc, patchutils, patchutils_tar)) == (
        (rustc,),
        (patchutils, patchutils_tar),
    )
    assert any("--requisites" in args for args in seen_query)
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


def test_parse_canary_slots_and_crates() -> None:
    """Dispatch canary accepts comma/space slots and unique crate names."""
    assert parse_canary_slots("") == ()
    assert parse_canary_slots("0, 2") == (0, 2)
    assert parse_canary_slots("1 1 3") == (1, 3)
    with pytest.raises(WarmupError, match="not an integer"):
        parse_canary_slots("x")
    with pytest.raises(WarmupError, match="0..4"):
        parse_canary_slots("5")
    assert parse_canary_crates("extension_host, zed zed") == ("extension_host", "zed")
    assert parse_canary_crates("") == ()
    assert parse_kick_canary_slots("# comment\n2026-10-10T15:00:00Z kick\n") == ()
    assert parse_kick_canary_slots(
        "# Touch this file\ncanary-slots: 3,4\n2026-10-10T16:00:00Z\n"
    ) == (3, 4)
    assert parse_kick_canary_slots("canary-slots: 4 4 3\n") == (4, 3)
    with pytest.raises(WarmupError, match="0..4"):
        parse_kick_canary_slots("canary-slots: 5\n")
    assert parse_kick_canary_crates("# comment\ncanary-slots: 3,4\n") == ()
    assert parse_kick_canary_crates(
        "canary-crates: settings settings_content settings\n"
        "canary-slots: 0\n"
        "2026-10-10T18:40:00Z\n"
    ) == ("settings", "settings_content")
    assert parse_kick_canary_crates(
        "canary-crates: settings_json, settings_macros\n"
    ) == (
        "settings_json",
        "settings_macros",
    )
    kick = (REPO_ROOT / ".github" / "update-kick").read_text(encoding="utf-8")
    assert parse_kick_canary_crates(kick) == SETTINGS_MEMBER_CRATES
    assert parse_kick_canary_slots(kick) == (0,)


def test_canary_crate_warmup_paths_reads_every_layer() -> None:
    """Named-crate canary must not filter to the slot stripe.

    rust_settings lives on slot 3 of its layer. A slot-0 stripe would
    miss it; #1269's slot-3/4 canary still never reached it.
    """
    other = "/nix/store/otheraaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_gpui-0.1.0"
    content = "/nix/store/ma14flyg1v5b4vhinb2l0klw1xmdg9nz-rust_settings_content-0.1.0"
    settings = "/nix/store/2y7vj1wq5nz030asgn7rhipbcx5aya89-rust_settings-0.1.0"
    filler = (
        "/nix/store/fill0aaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_collections-0.1.0",
        "/nix/store/fill1aaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_util-0.1.0",
        "/nix/store/fill2aaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_fs-0.1.0",
        "/nix/store/fill3aaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_paths-0.1.0",
    )
    layers = (
        (other, filler[0], filler[1], filler[2], filler[3]),
        (filler[0], filler[1], filler[2], content, filler[3]),
        (filler[0], filler[1], filler[2], settings, filler[3]),
    )
    assert slot_warmup_paths(layers, 0) == (other, filler[0], filler[0])
    assert settings not in slot_warmup_paths(layers, 0)
    assert settings in slot_warmup_paths(layers, 3)
    assert canary_crate_warmup_paths(layers, ("settings", "settings_content")) == (
        content,
        settings,
    )
    assert canary_crate_warmup_paths(layers, ()) == ()


def test_1269_slot3_settings_svh_mix_under_fatal_limit() -> None:
    """#1269 slot 3: rust_settings was 6 local + Cachix content cluster.

    Hosted job 114266601834: ``these 6 derivations will be built``
    (num_cpus, trash, paths, seahash, ec4rs, settings) and 225 fetched
    including rust_settings_content/_json/_macros from gkze, then local
    rust_settings E0463/E0432. Missing crate2nix deps would not fetch
    those rlibs. This workspace cannot ``nix build`` aarch64-darwin
    rust_settings; the hosted dry-run plus force-local classification
    is the proof.
    """
    settings = "/nix/store/2y7vj1wq5nz030asgn7rhipbcx5aya89-rust_settings-0.1.0.drv"
    content = (
        "/nix/store/ma14flyg1v5b4vhinb2l0klw1xmdg9nz-rust_settings_content-0.1.0.drv"
    )
    settings_json = (
        "/nix/store/jsonaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_settings_json-0.1.0.drv"
    )
    macros = (
        "/nix/store/macroaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_settings_macros-0.1.0.drv"
    )
    assert is_settings_family_store_path(settings)
    assert is_settings_family_store_path(content)
    assert is_settings_family_store_path(settings_json)
    assert is_settings_family_store_path(macros)
    assert is_svh_sensitive_store_path(settings)
    assert is_force_local_allowed_build(settings)
    tree_sitter = (
        "/nix/store/1zsfiw8m72a6ql2wx3f5bpq3mn7vnw77-rust_tree-sitter-0.27.0.drv"
    )
    assert is_crate2nix_rust_output(tree_sitter)
    assert not is_settings_family_store_path(tree_sitter)
    assert is_tree_sitter_family_store_path(tree_sitter)
    assert is_svh_sensitive_store_path(tree_sitter)
    assert is_force_local_allowed_build(tree_sitter)
    assert WARMUP_FATAL_BUILD_LIMIT >= 6


def test_1268_slot_others_defer_leaf_packages_with_dry_run_counts() -> None:
    """#1268 slots 3/4 exploded on leaf umbrellas; rustc itself substituted.

    Counts are the hosted Darwin ``these N derivations will be built``
    lines. This workspace cannot ``nix build`` aarch64-darwin; the
    classifier plus those observed counts is the dry-run proof.
    """
    cargo_sha2 = (
        "/nix/store/mynvanc4j9sgag5gdd8ir1s3p0pq3rgg-cargo-package-sha2-0.10.9.drv"
    )
    crane = "/nix/store/mad9jrvgkwimr9dx0nsabjbd6c7ii1lj-crane-utils-0.0.1.drv"
    v8_native = (
        "/nix/store/gh047rbs0widc6a8wy8x21szlyg0kjbz-"
        "goose-cli-v8-native-dbb64c20b9062b358b101e4592abb3ca8f646c2b.drv"
    )
    goose = "/nix/store/rhak437lr7f3zwfgf2qrb40ryw7mx98i-goose-cli-1.51.0.drv"
    vendor = "/nix/store/0jmdym99ibn8rpv9696zvyrkr0mjyvjv-vendor-cargo-deps.drv"
    but = "/nix/store/x29aw11rvynwmi40dkl4wbwk4d44pgam-but.drv"
    rustc = "/nix/store/arriw6r1qhajd9q60xi8dzw0mw9aixd1-rustc-1.98.1"
    assert is_safe_rust_warmup_other(cargo_sha2)
    assert is_safe_rust_warmup_other(crane)
    assert is_safe_rust_warmup_other(v8_native)
    assert not is_safe_rust_warmup_other(goose)
    assert not is_safe_rust_warmup_other(vendor)
    assert not is_safe_rust_warmup_other(but)
    assert not is_safe_rust_warmup_other(rustc)
    slot4 = (cargo_sha2, crane, goose)
    slot3 = (but, vendor)
    slot0 = (v8_native,)
    counts = {
        cargo_sha2: 1,
        crane: 1,
        v8_native: 1,
        goose: 1759,
        vendor: 521,
        but: 1,
    }
    safe4, deferred4 = classify_slot_warmup_will_be_built(slot4, will_be_built=counts)
    safe3, deferred3 = classify_slot_warmup_will_be_built(slot3, will_be_built=counts)
    safe0, deferred0 = classify_slot_warmup_will_be_built(slot0, will_be_built=counts)
    assert safe4 == ((cargo_sha2, 1), (crane, 1))
    assert deferred4 == ((goose, 1759),)
    assert max(count for _path, count in safe4) <= WARMUP_FATAL_BUILD_LIMIT
    assert deferred4[0][1] > WARMUP_FATAL_BUILD_LIMIT
    assert safe3 == ()
    assert deferred3 == ((but, 1), (vendor, 521))
    assert safe0 == ((v8_native, 1),)
    assert deferred0 == ()
    assert partition_rust_warmup_others((goose, cargo_sha2, goose)) == (
        (cargo_sha2,),
        (goose,),
    )


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (
            "/nix/store/195q1crx0p9g5na7l7aw96bqvs3y2ab0-coreaudio-rs-0.14.2.tar.gz.drv",
            True,
        ),
        ("/nix/store/fcy73hrwaaaaaaaaaaaaaaaaaaaaaaaa-bmake-20260313.tar.gz.drv", True),
        ("/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-source.drv", True),
        (
            "/nix/store/n2g2dzs3aaaaaaaaaaaaaaaaaaaaaaaaa-"
            "zed-editor-nightly-extension_host-src.drv",
            True,
        ),
        ("/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-vendor-source", True),
        ("/nix/store/cccccccccccccccccccccccccccccccc-crate.crate.drv", True),
        ("/nix/store/xbq69m0caaaaaaaaaaaaaaaaaaaaaaaa-rustc-1.98.1.drv", False),
        ("/nix/store/1gx3hvygaaaaaaaaaaaaaaaaaaaaaaaa-stdenv-darwin.drv", False),
        ("/nix/store/dddddddddddddddddddddddddddddddd-bmake-20260313.drv", False),
    ],
)
def test_is_source_fetch_store_path_classifies_archives(
    path: str, expected: bool
) -> None:
    """#1264: crate tarball FODs download; compiler/stdenv still substitute-only."""
    assert is_source_fetch_store_path(path) is expected


def test_1270_canary_patchutils_is_local_compiler_helper() -> None:
    """#1270 canary: patchutils, pbzx, then cpio were 1-drv helpers after FODs.

    Jobs 114284201109 / 114290145820 / 114291903959: ``this derivation
    will be built`` plus one fetched source, then ``--max-jobs 0``
    fatal'd Cannot build. rustc/stdenv stay substitute-only.
    """
    patchutils = "/nix/store/m6399k05aaqiz3mx4cdpkdqr4hp05kmj-patchutils-0.3.3.drv"
    pbzx = "/nix/store/9wq7n6729mdhisgxjyahy0cpjm4aq85p-pbzx-1.0.2.drv"
    cpio = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-cpio-2.15.drv"
    rustc = "/nix/store/xbq69m0caaaaaaaaaaaaaaaaaaaaaaaa-rustc-1.98.1.drv"
    stdenv = "/nix/store/1gx3hvygaaaaaaaaaaaaaaaaaaaaaaaa-stdenv-darwin.drv"
    assert is_compiler_local_helper_store_path(patchutils)
    assert is_compiler_local_helper_store_path(pbzx)
    assert is_compiler_local_helper_store_path(cpio)
    assert not is_compiler_local_helper_store_path(rustc)
    assert not is_compiler_local_helper_store_path(stdenv)
    assert is_compiler_must_substitute_store_path(rustc)
    assert is_compiler_must_substitute_store_path(stdenv)
    assert not is_compiler_must_substitute_store_path(cpio)
    assert partition_compiler_input_drvs((rustc, stdenv, patchutils, pbzx, cpio)) == (
        (rustc, stdenv),
        (patchutils, pbzx, cpio),
    )


def test_zed_family_policy_is_atomic_all_or_force_local() -> None:
    """Mixed Cachix presence must not substitute a subset of the family."""
    settings = "/nix/store/2y7vj1wq5nz030asgn7rhipbcx5aya89-rust_settings-0.1.0"
    content = "/nix/store/ma14flyg1v5b4vhinb2l0klw1xmdg9nz-rust_settings_content-0.1.0"
    rustc_a = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rustc-1.98.1.drv"
    rustc_b = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rustc-1.98.1.drv"
    family = (content, settings)
    assert zed_family_cachix_presence(family, present=lambda _p: False) == "none"
    assert zed_family_cachix_presence(family, present=lambda _p: True) == "all"
    assert (
        zed_family_cachix_presence(family, present=lambda path: path == content)
        == "mixed"
    )
    assert zed_family_realize_policy(family, present=lambda _p: False) == "force-local"
    assert zed_family_realize_policy(family, present=lambda _p: True) == "substitute"
    assert (
        zed_family_realize_policy(family, present=lambda path: path == content)
        == "force-local"
    )
    with pytest.raises(WarmupError, match="rustc generations"):
        zed_family_realize_policy(
            family, present=lambda _p: True, rustc_ids=(rustc_a, rustc_b)
        )
    assert_zed_family_realize_set(family, family)
    assert_zed_family_realize_set(family, ())
    with pytest.raises(WarmupError, match="mixes generations"):
        assert_zed_family_realize_set(family, (settings,))
    tree_sitter = "/nix/store/1zsfiw8m72a6ql2wx3f5bpq3mn7vnw77-rust_tree-sitter-0.27.0"
    layers = ((content,), (tree_sitter,), (settings,))
    assert zed_family_warmup_outputs(layers) == (content, tree_sitter, settings)
    rustc = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rustc-1.98.1.drv"
    rust_default = "/nix/store/dq17vrzvx74ismp4s6xicxirkmlshw1b-rust-default-1.98.1.drv"
    clang = "/nix/store/cccccccccccccccccccccccccccccccc-clang-21.1.8.drv"

    def query(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, f"{rustc}\n{clang}\n", "")

    assert rustc_generation_ids((f"{settings}.drv",), run=query) == (rustc,)

    def query_default(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, f"{rust_default}\n{clang}\n", "")

    assert rustc_generation_ids((f"{settings}.drv",), run=query_default) == (
        rust_default,
    )
    wrapper = "/nix/store/sqbzbgwqqn62fcwayrm0yk3778fdhmyj-rustc-wrapper-1.98.1.drv"

    def query_hosted(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args, 0, f"{wrapper}\n{rust_default}\n{clang}\n", ""
        )

    assert rustc_generation_ids((f"{settings}.drv",), run=query_hosted) == (wrapper,)
    assert (
        zed_family_realize_policy(family, present=lambda _p: True, rustc_ids=(wrapper,))
        == "substitute"
    )


def test_warmup_fatal_skips_cannot_build_during_substitute_only() -> None:
    """#1270: --max-jobs 0 Cannot-build is retried; SVH and rustc counts stay fatal."""
    cannot = "error: Cannot build '/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-cpio-2.15.drv'."
    assert warmup_fatal_line(cannot, substitute_only=True) is None
    raise_if_warmup_fatal(cannot, substitute_only=True)
    assert warmup_fatal_line(cannot) == "cannot build"
    with pytest.raises(WarmupFatalError, match="cannot build"):
        raise_if_warmup_fatal(cannot)
    svh = "error[E0463]: can't find crate for `settings_content`"
    assert warmup_fatal_line(svh, substitute_only=True) is not None
    with pytest.raises(WarmupFatalError, match="SVH"):
        raise_if_warmup_fatal(svh, substitute_only=True)
    huge = "these 406 derivations will be built:"
    assert warmup_fatal_line(huge, substitute_only=True) is not None
    with pytest.raises(WarmupFatalError, match="will-be-built"):
        raise_if_warmup_fatal(huge, substitute_only=True)
    svh_keep = "error[E0463]: can't find crate for `settings_content`"
    assert warmup_fatal_line(svh_keep, keep_going=True) is None
    raise_if_warmup_fatal(svh_keep, keep_going=True)
    family = "these 37 derivations will be built:"
    assert warmup_fatal_line(family) is not None
    assert warmup_fatal_line(family, keep_going=True) is None
    raise_if_warmup_fatal(family, keep_going=True)
    assert warmup_fatal_line(huge, keep_going=True) is not None


def test_realize_warmup_substitute_only_is_max_jobs_zero(tmp_path: Path) -> None:
    """Non-family inputs must substitute; they cannot share force-local."""
    rustc = "/nix/store/xbq69m0caaaaaaaaaaaaaaaaaaaaaaaa-rustc-1.98.1.drv"
    calls: list[list[str]] = []

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    assert (
        realize_warmup_outputs(
            (rustc,), flake_root=tmp_path, run=run, substitute_only=True
        )
        == ()
    )
    sub = next(args for args in calls if args[:2] == ["nix", "build"])
    assert "--max-jobs" in sub
    assert "0" in sub
    assert "--no-substitute" not in sub
    assert "--keep-going" not in sub
    with pytest.raises(WarmupError, match="cannot be force_local and substitute_only"):
        realize_warmup_outputs(
            (rustc,),
            flake_root=tmp_path,
            run=run,
            force_local=True,
            substitute_only=True,
        )


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


def test_query_drv_outputs_accepts_bytes_stdout() -> None:
    """Output query keeps the default store runner and bytes stdout."""
    drv = "/nix/store/lmdrvaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_language_models-0.1.0.drv"
    assert query_drv_outputs(
        drv,
        run=lambda args, **_kwargs: subprocess.CompletedProcess(
            args, 0, b"/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-out\n", b""
        ),
    ) == ("/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-out",)
    with pytest.raises(WarmupError, match="failed to query outputs"):
        query_drv_outputs(
            drv,
            run=lambda args, **_kwargs: subprocess.CompletedProcess(
                args, 1, "", "no drv"
            ),
        )


def test_force_local_dry_run_rejects_bootstrap_builds() -> None:
    """#1263: --no-substitute must not compile stdenv/bmake after inputs exist."""
    host = "/nix/store/jwk3kr03aaaaaaaaaaaaaaaaaaaaaaaa-rust_extension_host-0.1.0.drv"
    src = (
        "/nix/store/n2g2dzs3aaaaaaaaaaaaaaaaaaaaaaaa-"
        "zed-editor-nightly-extension_host-src.drv"
    )
    bmake = "/nix/store/fcy73hrwaaaaaaaaaaaaaaaaaaaaaaaa-bmake-20260313.tar.gz.drv"
    stdenv = "/nix/store/1gx3hvygaaaaaaaaaaaaaaaaaaaaaaaa-stdenv-darwin.drv"
    assert is_force_local_allowed_build(host)
    assert is_force_local_allowed_build(src)
    assert not is_force_local_allowed_build(bmake)
    assert not is_force_local_allowed_build(stdenv)
    stderr = (
        "these 3 derivations will be built:\n"
        f"  {host}\n"
        f"  {src}\n"
        f"  {bmake}\n"
        "these 2 paths will be fetched:\n"
        "  /nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rustc\n"
    )
    assert force_local_dry_run_builds(
        (host,),
        run=lambda args, **_kwargs: subprocess.CompletedProcess(args, 0, "", stderr),
    ) == (host, src, bmake)
    seen: list[list[str]] = []

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, "", stderr)

    with pytest.raises(WarmupError, match="would compile non-family"):
        assert_force_local_dry_run((host,), run=run)
    assert any("--dry-run" in args and "--no-substitute" in args for args in seen)
    ok = f"these 2 derivations will be built:\n  {host}\n  {src}\n"
    assert_force_local_dry_run(
        (host,),
        run=lambda args, **_kwargs: subprocess.CompletedProcess(args, 0, "", ok),
    )
    with pytest.raises(WarmupError, match="failed to dry-run force-local"):
        assert_force_local_dry_run(
            (host,),
            run=lambda args, **_kwargs: subprocess.CompletedProcess(
                args, 1, "", "no drv"
            ),
        )


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("error[E0460]: found possibly newer version of crate `settings_ui`", "SVH"),
        ("error[E0463]: can't find crate for `gpui`", "SVH"),
        (
            "Cannot build '/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_zed.drv'",
            "cannot build",
        ),
        ("error: you are not allowed to ignore liveness of /nix/store/x", "liveness"),
        (
            "error: cannot download bmake-20260313.tar.gz from any mirror",
            "cannot download",
        ),
        ("these 406 derivations will be built:", "will-be-built"),
    ],
)
def test_warmup_fatal_line_matches_hosted_abort_patterns(
    line: str, reason: str
) -> None:
    """#1263/#1262: the job must go red on the first fatal streamed line."""
    match = warmup_fatal_line(line)
    assert match is not None
    assert reason.lower() in match.lower() or reason == "SVH"
    with pytest.raises(WarmupFatalError, match=reason if reason != "SVH" else "SVH"):
        raise_if_warmup_fatal(line)


@pytest.mark.parametrize(
    "line",
    [
        "these 406 paths will be fetched",
        "these 2 derivations will be built:",
        f"these {WARMUP_FATAL_BUILD_LIMIT} derivations will be built:",
        "error: failed to fetch git+https://github.com/example/flake",
        "unable to download 'https://registry.npmjs.org/foo'",
        "error[E0308]: mismatched types",
        "building '/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_zed.drv'",
    ],
)
def test_warmup_fatal_line_ignores_nonfatal_noise(line: str) -> None:
    """Fetched-only closures, small rust families, and flake fetch noise stay live."""
    assert warmup_fatal_line(line) is None
    raise_if_warmup_fatal(line)
