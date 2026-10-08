"""Structural Darwin warmup is the missing shared drv set, not a zed list."""

import json
import subprocess
from pathlib import Path

import pytest

from lib.update.ci.shard_plan import ClosureShard
from lib.update.ci.warmup import (
    MAX_SHARD_LOCAL_BUILDS,
    RUST_WARMUP_SLOTS,
    WARMUP_PLAN_NAME,
    ShardLocalBuildReport,
    WarmupError,
    assert_local_build_threshold,
    darwin_output_paths,
    default_cache_present,
    eval_root_darwin_outputs,
    intersect_missing,
    is_crate2nix_rust_output,
    load_warmup_plan,
    plan_darwin_warmup,
    realize_warmup_outputs,
    rust_warmup_layers,
    shard_remaining_outputs,
    skip_cached_warmup_paths,
    slot_warmup_paths,
    substitutable_paths,
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
