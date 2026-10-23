"""Structural Darwin warmup is the missing shared drv set, not a zed list."""

import json
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path

import pytest
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import parse_nix_expr
from lib.update.ci.coverage import check_path_in_cachix
from lib.update.ci.shard_plan import ClosureShard
from lib.update.ci.warmup import (
    CASCADE_WARMUP_SLOTS,
    CRANE_DEP_CHUNK,
    DARWIN_GRAPH_EVAL_WORKERS,
    DARWIN_RUST_FAMILY_PACKAGES,
    MAX_SHARD_LOCAL_BUILDS,
    RUST_WARMUP_SLOTS,
    SETTINGS_MEMBER_CRATES,
    SHARED_MISSING_MIN_ROOTS,
    SOURCE_FETCH_DNS_HOSTS,
    SOURCE_FETCH_RETRY_BACKOFF_SECONDS,
    WARMUP_DRVS_CACHE_INFO,
    WARMUP_DRVS_NAME,
    WARMUP_DRVS_ROOTS,
    WARMUP_FATAL_BUILD_LIMIT,
    WARMUP_PLAN_NAME,
    ShardLocalBuildReport,
    WarmupError,
    WarmupFatalError,
    _eval_darwin_package_graphs,
    _eval_root_darwin_graphs,
    _store_output_rest,
    assert_force_local_dry_run,
    assert_local_build_threshold,
    assert_zed_family_realize_set,
    canary_crate_warmup_paths,
    cascade_canary_paths,
    cascade_path_weight,
    classify_slot_warmup_will_be_built,
    command_store_drv,
    compiler_input_drvs,
    darwin_fod_canary_installables,
    darwin_leaf_prebuild_installables,
    darwin_output_paths,
    darwin_package_installable,
    default_cache_present,
    default_cache_present_many,
    drv_input_references,
    dry_run_will_be_built_count,
    eval_darwin_package_graph,
    eval_root_darwin_outputs,
    export_warmup_drvs,
    extension_host_family_warmup_outputs,
    filter_cascade_realize_drvs,
    force_local_dry_run_builds,
    gitbutler_family_package_outputs,
    gitbutler_family_slot_from_package,
    gitbutler_family_warmup_outputs,
    import_warmup_drvs,
    intersect_missing,
    is_cascade_canary_store_path,
    is_compiler_local_helper_store_path,
    is_compiler_must_substitute_store_path,
    is_crane_dep_store_path,
    is_crate2nix_rust_output,
    is_deferred_compiler_rust_drv,
    is_extension_host_family_store_path,
    is_fixed_output_hash_mismatch,
    is_force_local_allowed_build,
    is_gitbutler_family_store_path,
    is_linux_builder_image_command,
    is_linux_builder_output_store_path,
    is_named_rust_crate_store_path,
    is_rust_agent_ui_store_path,
    is_rust_extension_host_store_path,
    is_rust_language_models_store_path,
    is_rust_overlay_binary_component,
    is_rust_zed_store_path,
    is_safe_rust_warmup_other,
    is_settings_family_store_path,
    is_source_fetch_mirror_line,
    is_source_fetch_store_path,
    is_source_fetch_transport_error,
    is_svh_sensitive_store_path,
    is_tree_sitter_family_store_path,
    is_zed_editor_nightly_store_path,
    is_zed_workspace_crate_store_path,
    language_models_input_drvs,
    language_models_warmup_outputs,
    linux_builder_substitute_miss_reason,
    load_warmup_plan,
    locally_fetchable_source_drvs,
    nix_store_argv_has_operation,
    parse_canary_crates,
    parse_canary_slots,
    parse_kick_canary_crates,
    parse_kick_canary_slots,
    partition_agent_ui_drvs,
    partition_compiler_input_drvs,
    partition_compiler_substitute_probe_drvs,
    partition_crane_dep_drvs,
    partition_rust_warmup_others,
    partition_rust_zed_drvs,
    partition_settings_family_drvs,
    partition_svh_sensitive_drvs,
    partition_zed_editor_nightly_drvs,
    plan_darwin_warmup,
    publish_warmup_source_fetches,
    query_drv_outputs,
    raise_if_warmup_fatal,
    realize_source_fetch_drvs,
    realize_warmup_outputs,
    rust_compile_input_drvs,
    rust_svh_input_drvs,
    rust_warmup_layers,
    rust_warmup_output_drvs,
    rust_warmup_probe_paths,
    rust_warmup_realize_paths,
    rust_zed_member_warmup_outputs,
    rustc_generation_ids,
    settings_family_warmup_outputs,
    shard_remaining_outputs,
    skip_cached_warmup_paths,
    slot_cascade_paths,
    slot_warmup_paths,
    source_fetch_drv_systems,
    source_fetch_failure_drv,
    source_fetch_input_drvs,
    source_fetch_leftover_drvs,
    source_fetch_network_ready,
    source_fetch_publish_leftover_line,
    source_fetch_retry_drvs,
    stripe_layer_paths,
    substitutable_paths,
    unique_drvs_for_outputs,
    unpublished_source_fetches,
    wait_for_source_fetch_network,
    warmup_build_installable,
    warmup_drv_root_args,
    warmup_fatal_line,
    warmup_output_drvs,
    write_warmup_plan,
    zed_family_cachix_presence,
    zed_family_realize_policy,
    zed_family_warmup_outputs,
)
from lib.update.derivation_validation import (
    DerivationValidationFailure,
    RootClosureManifest,
)
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


def _live_manifest() -> RootClosureManifest:
    """Four Darwin roots as packed on 38146388927."""
    return RootClosureManifest.model_validate({
        "schemaVersion": 2,
        "requiredKinds": ["darwin", "home"],
        "requiredRoots": [],
        "roots": [
            {"kind": "darwin", "name": "argus", "system": "aarch64-darwin"},
            {"kind": "darwin", "name": "rocinante", "system": "aarch64-darwin"},
            {"kind": "darwin", "name": "zeus", "system": "aarch64-darwin"},
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


def test_rust_warmup_layers_accept_v3_input_drvs_and_skip_malformed() -> None:
    """39113211468: Darwin graphs still emit v3 inputDrvs and junk entries."""
    rust_a = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_a-1"
    rust_b = "/nix/store/cccccccccccccccccccccccccccccccc-rust_b-1"
    payload = {
        "derivations": {
            1: {"system": "aarch64-darwin"},
            "not-a-drv": "nope",
            "linux.drv": {
                "system": "x86_64-linux",
                "outputs": {"out": {"path": rust_a}},
                "inputDrvs": {},
            },
            "no-outputs.drv": {
                "system": "aarch64-darwin",
                "outputs": "nope",
                "inputDrvs": {},
            },
            "no-output-map.drv": {
                "system": "aarch64-darwin",
                "outputs": {"out": "nope"},
                "inputDrvs": {},
            },
            "no-path.drv": {
                "system": "aarch64-darwin",
                "outputs": {"out": {"path": 1}},
                "inputDrvs": {},
            },
            "/nix/store/dddddddddddddddddddddddddddddddd-not-a-drv": {
                "system": "aarch64-darwin",
                "outputs": {
                    "out": {
                        "path": ("/nix/store/hhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhh-junk")
                    }
                },
                "inputDrvs": "nope",
                "inputs": {"drvs": "not-a-map"},
            },
            "/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-no-outputs.drv": {
                "system": "aarch64-darwin",
                "outputs": "nope",
                "inputDrvs": {},
            },
            "/nix/store/ffffffffffffffffffffffffffffffff-no-output-map.drv": {
                "system": "aarch64-darwin",
                "outputs": {"out": "nope"},
                "inputDrvs": {},
            },
            "/nix/store/gggggggggggggggggggggggggggggggg-no-path.drv": {
                "system": "aarch64-darwin",
                "outputs": {"out": {"path": 1}},
                "inputDrvs": {},
            },
            f"{rust_a.rsplit('/', 1)[-1]}.drv": {
                "system": "aarch64-darwin",
                "outputs": {"out": {"path": rust_a}},
                "inputs": {"drvs": "not-a-map"},
                "inputDrvs": {f"{rust_b.rsplit('/', 1)[-1]}.drv": ["out"]},
            },
            f"{rust_b.rsplit('/', 1)[-1]}.drv": {
                "system": "aarch64-darwin",
                "outputs": {"out": {"path": rust_b}},
                "inputDrvs": {},
            },
        }
    }
    layers = rust_warmup_layers(frozenset({rust_a, rust_b}), (payload,))
    assert layers[0] == (rust_b,)
    assert layers[1] == (rust_a,)
    mapping = warmup_output_drvs(frozenset({rust_a, rust_b}), (payload,))
    assert mapping[rust_a].endswith(".drv")
    with pytest.raises(WarmupError, match="missing derivations"):
        rust_warmup_layers(frozenset({rust_a}), ({"version": 4},))
    with pytest.raises(WarmupError, match="missing derivations"):
        warmup_output_drvs(frozenset({rust_a}), ({"version": 4},))


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
    assert SHARED_MISSING_MIN_ROOTS == 2
    assert warmup == frozenset({shared, rust})
    assert intersect_missing({}, substitutable) == frozenset()
    darwin_only = "/nix/store/ffffffffffffffffffffffffffffffff-darwin-only"
    four = {
        "darwin-argus": frozenset({shared, rust, unique, cached, darwin_only}),
        "darwin-rocinante": frozenset({shared, rust, cached, darwin_only}),
        "darwin-zeus": frozenset({shared, rust, cached, darwin_only}),
        "home-george": frozenset({shared, rust, cached}),
    }
    assert intersect_missing(four, substitutable) == frozenset({
        shared,
        rust,
        darwin_only,
    })
    assert intersect_missing(four, substitutable, min_roots=4) == frozenset({
        shared,
        rust,
    })
    assert (
        intersect_missing({"darwin-argus": frozenset({unique})}, substitutable)
        == frozenset()
    )
    with pytest.raises(WarmupError, match="at least 1"):
        intersect_missing(per_root, substitutable, min_roots=0)
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
        package_graphs={},
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
        "/nix/store/ffffffffffffffffffffffffffffffff-goose-cli-1.51.0": (
            "/nix/store/ffffffffffffffffffffffffffffffff-goose-cli-1.51.0.drv"
        ),
    }
    assert (
        "/nix/store/ffffffffffffffffffffffffffffffff-goose-cli-1.51.0"
        not in plan.warmup_outputs
    )
    assert plan.cascade_outputs == (
        "/nix/store/ffffffffffffffffffffffffffffffff-goose-cli-1.51.0",
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
        "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-cargo-package-sha2-0.10.9",
        *huge_unique,
    )
    with pytest.raises(
        WarmupError, match="local-build budget.*cascade=.*remaining_sample="
    ):
        plan_darwin_warmup(
            tmp_path,
            manifest=_manifest(),
            shards=shards,
            run=run,
            package_graphs={},
            present=lambda _path: False,
        )


def test_plan_darwin_warmup_peels_shared_non_rust_cascade_from_remaining(
    tmp_path: Path,
) -> None:
    """38130420607: rust_* peel left 1724/1733 shared non-rust in both shards.

    Zed a34a062→93d7456 plus lock moves shifted the shared Darwin cascade.
    The rust-only filter kept rust_*=0 remaining while both shards sat
    over the 400 gate. Peel that intersection into cascade-warmup; the
    400 gate still fails closed on host-unique remainders.
    """
    rust = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_gpui-1"
    unique = "/nix/store/dddddddddddddddddddddddddddddddd-argus-only"
    cascade = tuple(
        f"/nix/store/{index:032d}-coreutils-cascade-{index}" for index in range(401)
    )

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        name = args[-1].rsplit("root-closure-", 1)[-1]
        paths = (*cascade, rust)
        if name == "darwin-argus":
            paths = (*paths, unique)
        return subprocess.CompletedProcess(
            args, 0, stdout=json.dumps(_graph(*paths)), stderr=""
        )

    shards = (
        ClosureShard(
            shard="darwin-argus+home-george",
            system="aarch64-darwin",
            roots=("darwin-argus", "home-george"),
        ),
        ClosureShard(
            shard="darwin-rocinante",
            system="aarch64-darwin",
            roots=("darwin-rocinante",),
        ),
    )
    plan = plan_darwin_warmup(
        tmp_path,
        manifest=_manifest(),
        shards=shards,
        run=run,
        package_graphs={},
        present=lambda _path: False,
    )
    assert rust in plan.warmup_outputs
    assert plan.cascade_outputs == cascade
    assert unique not in plan.cascade_outputs
    assert plan.shards[0].remaining == 1
    assert plan.shards[0].remaining_rust_crates == 0
    assert plan.shards[1].remaining == 0
    assert slot_cascade_paths(plan.cascade_layers, 0)
    assert CASCADE_WARMUP_SLOTS == 5
    striped = {
        path
        for slot in range(CASCADE_WARMUP_SLOTS)
        for path in slot_cascade_paths(plan.cascade_layers, slot)
    }
    assert striped == set(cascade)


def test_plan_darwin_warmup_peels_two_plus_darwin_only_shared(
    tmp_path: Path,
) -> None:
    """38146388927: all-4 peel left remaining=829/836 (rust_*=0).

    darwin-argus∩rocinante∩zeus is shared by three roots but not
    home-george, so all-root intersection kept ~400 of those outputs in
    *both* 2-wide shards. Two-plus peels that set into cascade; one-root
    leaves still fail the 400 gate.
    """
    rust = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_gpui-1"
    all4 = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-notion-app-7.25.1"
    unique = "/nix/store/dddddddddddddddddddddddddddddddd-argus-only"
    darwin_only = tuple(
        f"/nix/store/{index:032d}-darwin-only-{index}" for index in range(401)
    )

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        name = args[-1].rsplit("root-closure-", 1)[-1]
        paths = (all4, rust)
        if name != "home-george":
            paths = (*paths, *darwin_only)
        if name == "darwin-argus":
            paths = (*paths, unique)
        return subprocess.CompletedProcess(
            args, 0, stdout=json.dumps(_graph(*paths)), stderr=""
        )

    shards = (
        ClosureShard(
            shard="darwin-argus+home-george",
            system="aarch64-darwin",
            roots=("darwin-argus", "home-george"),
        ),
        ClosureShard(
            shard="darwin-rocinante+darwin-zeus",
            system="aarch64-darwin",
            roots=("darwin-rocinante", "darwin-zeus"),
        ),
    )
    assert intersect_missing(
        {
            "darwin-argus": frozenset({all4, rust, unique, *darwin_only}),
            "darwin-rocinante": frozenset({all4, rust, *darwin_only}),
            "darwin-zeus": frozenset({all4, rust, *darwin_only}),
            "home-george": frozenset({all4, rust}),
        },
        frozenset(),
        min_roots=4,
    ) == frozenset({all4, rust})
    plan = plan_darwin_warmup(
        tmp_path,
        manifest=_live_manifest(),
        shards=shards,
        run=run,
        package_graphs={},
        present=lambda _path: False,
    )
    assert rust in plan.warmup_outputs
    assert all4 in plan.cascade_outputs
    assert set(darwin_only) <= set(plan.cascade_outputs)
    assert unique not in plan.cascade_outputs
    assert plan.shards[0].remaining == 1
    assert plan.shards[0].remaining_rust_crates == 0
    assert plan.shards[1].remaining == 0
    huge_unique = [f"/nix/store/{index:032d}-unique-{index}" for index in range(401)]

    def run_unique(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        name = args[-1].rsplit("root-closure-", 1)[-1]
        paths = (all4, rust, *darwin_only)
        if name == "darwin-argus":
            paths = (*paths, *huge_unique)
        return subprocess.CompletedProcess(
            args, 0, stdout=json.dumps(_graph(*paths)), stderr=""
        )

    with pytest.raises(WarmupError, match="remaining_sample=.*unique-0"):
        plan_darwin_warmup(
            tmp_path,
            manifest=_live_manifest(),
            shards=shards,
            run=run_unique,
            package_graphs={},
            present=lambda _path: False,
        )


def test_gitbutler_family_store_path_matches_workspace_crates() -> None:
    """Workspace crates are rust_<name>-<member>, not rust_<name>-<digit>."""
    assert is_gitbutler_family_store_path(
        "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_but-0.0.0"
    )
    assert is_gitbutler_family_store_path(
        "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_but-core-0.0.0.drv"
    )
    assert is_gitbutler_family_store_path(
        "/nix/store/cccccccccccccccccccccccccccccccc-rust_gitbutler-branch-actions-0.0.0"
    )
    assert is_gitbutler_family_store_path(
        "/nix/store/dddddddddddddddddddddddddddddddd-rust_sqlx-macros-0.9.0"
    )
    assert is_gitbutler_family_store_path(
        "/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-rust_gix-features-0.70.0"
    )
    assert not is_gitbutler_family_store_path(
        "/nix/store/ffffffffffffffffffffffffffffffff-rust_gpui-1"
    )
    assert not is_gitbutler_family_store_path(
        "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_butter-1.0.0"
    )
    assert darwin_package_installable("gitbutler") == (
        "path:.#packages.aarch64-darwin.gitbutler"
    )


def test_plan_darwin_warmup_peels_gitbutler_family_even_when_cached(
    tmp_path: Path,
) -> None:
    """38867194218: two-plus rust peel was empty; package graph still warms but."""
    but = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_but-core-0.0.0"
    sqlx = "/nix/store/cccccccccccccccccccccccccccccccc-rust_sqlx-0.9.0"
    tarball = "/nix/store/dddddddddddddddddddddddddddddddd-node-releases-2.0.56.tgz"
    gpui = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_gpui-1"

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, json.dumps(_graph(gpui)), "")

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
        present=lambda _path: True,
        package_graphs={"gitbutler": _graph(but, sqlx, tarball, gpui)},
    )
    assert but in plan.warmup_outputs
    assert sqlx in plan.warmup_outputs
    assert tarball not in plan.warmup_outputs
    assert gpui not in plan.warmup_outputs
    assert plan.output_drvs[but] == f"{but}.drv"
    assert "38867194218" in plan.notes


def test_rust_warmup_realize_paths_keeps_gitbutler_family() -> None:
    """path-info is not the realize oracle for rust_gitbutler / rust_but."""
    but = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_but-core-0.0.0"
    gpui = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_gpui-1"
    cached = rust_warmup_realize_paths((but, gpui), present=lambda _path: True)
    assert cached == (but,)
    missing = rust_warmup_realize_paths((but, gpui), present=lambda _path: False)
    assert missing == (but, gpui)
    assert rust_warmup_probe_paths((but, gpui), present=lambda _path: True) == (but,)
    assert rust_warmup_probe_paths((but, gpui), present=lambda _path: False) == ()
    assert rust_warmup_probe_paths((gpui,), present=lambda _path: True) == ()


def test_gitbutler_family_slot_from_package_stripes(tmp_path: Path) -> None:
    """Empty Linux plan still stripes the Darwin gitbutler package graph."""
    but = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_but-core-0.0.0"
    sqlx = "/nix/store/cccccccccccccccccccccccccccccccc-rust_sqlx-0.9.0"

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert "packages.aarch64-darwin.gitbutler" in args[-1]
        return subprocess.CompletedProcess(args, 0, json.dumps(_graph(but, sqlx)), "")

    assert gitbutler_family_package_outputs((_graph(but, sqlx),)) == frozenset({
        but,
        sqlx,
    })
    paths, drvs = gitbutler_family_slot_from_package(tmp_path, 0, run=run)
    striped = {
        path
        for slot in range(RUST_WARMUP_SLOTS)
        for path in gitbutler_family_slot_from_package(tmp_path, slot, run=run)[0]
    }
    assert striped == {but, sqlx}
    assert set(paths) <= {but, sqlx}
    assert drvs[but] == f"{but}.drv"
    payload = eval_darwin_package_graph(tmp_path, "gitbutler", run=run)
    assert but in darwin_output_paths(payload)
    assert gitbutler_family_warmup_outputs(((but,), (sqlx,))) == (but, sqlx)
    graphs = _eval_darwin_package_graphs(tmp_path, DARWIN_RUST_FAMILY_PACKAGES, run=run)
    assert "gitbutler" in graphs


def test_plan_darwin_warmup_evals_package_graphs_when_not_injected(
    tmp_path: Path,
) -> None:
    """39113211468: plan-shards instantiates gitbutler when package_graphs is omitted."""
    gpui = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_gpui-1"
    but = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_but-core-0.0.0"

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        expr = args[-1]
        if "packages.aarch64-darwin.gitbutler" in expr:
            return subprocess.CompletedProcess(args, 0, json.dumps(_graph(but)), "")
        return subprocess.CompletedProcess(args, 0, json.dumps(_graph(gpui)), "")

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
        present=lambda _path: True,
    )
    assert but in plan.warmup_outputs
    assert DARWIN_RUST_FAMILY_PACKAGES == ("gitbutler",)


def test_eval_root_darwin_graphs_is_serial_on_hosted_16g(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """38142145131: 4-wide derivation-show SIGTERM'd ubuntu-24.04 at ~800s."""
    assert DARWIN_GRAPH_EVAL_WORKERS == 1
    seen: list[int] = []
    calls: list[str] = []

    class _Pool:
        def __init__(self, *, max_workers: int) -> None:
            seen.append(max_workers)

        def __enter__(self) -> _Pool:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

        def map(
            self,
            fn: Callable[[str], tuple[str, dict[str, object], frozenset[str]]],
            names: Iterable[str],
        ) -> list[tuple[str, dict[str, object], frozenset[str]]]:
            return [fn(name) for name in names]

    monkeypatch.setattr("lib.update.ci.warmup.ThreadPoolExecutor", _Pool)

    def fake_eval(
        _flake_root: Path, name: str, *, run: object | None = None
    ) -> dict[str, object]:
        del run
        calls.append(name)
        return {"derivations": {}}

    monkeypatch.setattr("lib.update.ci.warmup.eval_root_darwin_graph", fake_eval)
    graphs, per_root = _eval_root_darwin_graphs(tmp_path, _manifest().roots)
    assert seen == [1]
    assert calls == ["darwin-argus", "darwin-rocinante", "home-george"]
    assert set(graphs) == set(calls)
    assert set(per_root) == set(calls)


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
    assert skip_cached_warmup_paths(()) == ()

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
        package_graphs={},
        present=lambda path: path.endswith("cached"),
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
        package_graphs={},
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
    per_drv = realize_warmup_outputs(
        [
            "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-ok",
            "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-fail",
        ],
        flake_root=tmp_path,
        run=run,
        chunk=1,
    )
    assert len(per_drv) == 1
    with pytest.raises(WarmupError, match="chunk must be at least 1"):
        realize_warmup_outputs(
            ["/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-ok"],
            flake_root=tmp_path,
            run=run,
            chunk=0,
        )


def test_realize_warmup_outputs_stops_on_fixed_output_hash_mismatch(
    tmp_path: Path,
) -> None:
    """38739788947: do not keep-going the cascade stripe after a FOD flip."""
    calls: list[str] = []
    mismatch = (
        "error: hash mismatch in fixed-output derivation "
        "'/nix/store/qbvbj2w3qxhn5c0x43f929k28plr1r8s-bb-pnpm-deps.drv':\n"
        "         specified: sha256-Wap+NC0xMgKHslKdl896QG6wje31HqZRFozQ1R4tAAQ=\n"
        "            got:    sha256-wb2dZ6Ki2+gC4XnHYOUUVcYfK1t3G8+dFeOlmRb2o84="
    )

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        target = next(arg for arg in args if arg.startswith(("/nix/store/", "path:")))
        calls.append(target)
        if "bb-pnpm-deps" in target:
            return subprocess.CompletedProcess(args, 1, "", mismatch)
        return subprocess.CompletedProcess(args, 0, "", "")

    failures = realize_warmup_outputs(
        [
            "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bb-pnpm-deps",
            "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-later",
        ],
        flake_root=tmp_path,
        run=run,
        keep_going=True,
        chunk=1,
    )
    assert [failure.installable for failure in failures] == [
        "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bb-pnpm-deps"
    ]
    assert is_fixed_output_hash_mismatch(failures[0].message)
    assert calls == ["/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bb-pnpm-deps"]


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


def test_command_store_drv_reads_quoted_drv_star() -> None:
    """Hosted rust-warmup logs quote foo.drv^*; the .drv identity is enough."""
    drv = "/nix/store/i60xdbbjfssbgmc8vv9pi2zrv21mk5l7-rust_xet-client-1.6.0.drv"
    assert command_store_drv(f"nix build --no-link --max-jobs 0 '{drv}^*'") == drv
    assert command_store_drv("nix build --max-jobs 0 /nix/store/cranelift.drv") == ""
    assert (
        command_store_drv("nix build path:.#checks.aarch64-darwin.root-closures") == ""
    )


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
    with pytest.raises(WarmupError, match="cascade warmup slot"):
        slot_cascade_paths(layers, 5)
    with pytest.raises(WarmupError, match="cascade warmup width"):
        slot_cascade_paths(layers, 0, width=0)
    with pytest.raises(WarmupError, match="warmup width must be"):
        stripe_layer_paths(layers, 0, width=0)
    with pytest.raises(WarmupError, match="warmup slot must be"):
        stripe_layer_paths(layers, 5, width=5)
    cycle = _graph(rust_a, rust_b, edges={rust_a: (rust_b,), rust_b: (rust_a,)})
    with pytest.raises(WarmupError, match="dependency cycle"):
        rust_warmup_layers(frozenset({rust_a, rust_b}), (cycle,))
    with pytest.raises(WarmupError, match="missing from Darwin graphs"):
        rust_warmup_layers(frozenset({rust_a}), (_graph(shared),))
    leaf = "/nix/store/hhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhh-goose-cli-1.51.0"
    assert rust_warmup_layers(frozenset({shared}), (_graph(shared),)) == ((shared,),)
    assert rust_warmup_layers(frozenset({leaf}), (_graph(leaf),)) == ()
    assert rust_warmup_layers(frozenset(), (_graph(shared),)) == ()


def test_skip_cached_warmup_paths_batches_gkze_path_info() -> None:
    """Stripe skip-if-in-gkze is one path-info --json, not one process per path."""
    hit = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_a-1"
    miss = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_b-1"
    calls: list[list[str]] = []

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        assert args[:4] == ["nix", "path-info", "--json", "--store"]
        assert "gkze" in args[4]
        payload = {path: ({} if path == hit else None) for path in args[5:]}
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    assert skip_cached_warmup_paths(
        (hit, miss, hit), present=check_path_in_cachix, run=run
    ) == (miss,)
    assert skip_cached_warmup_paths((hit, miss), run=run) == (miss,)
    assert len(calls) == 2


def test_substitutable_paths_query_gkze_before_nixos() -> None:
    """Planner path-info hits gkze first so rust_* skip a hydra miss each."""
    gkze = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_settings-1"
    hydra = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-coreutils-9.5"
    miss = "/nix/store/cccccccccccccccccccccccccccccccc-local-1"
    stores: list[str] = []
    queried: list[tuple[str, ...]] = []

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        store = args[args.index("--store") + 1]
        stores.append(store)
        queried.append(tuple(args[args.index("--store") + 2 :]))
        payload = {gkze: {}} if "gkze" in store else {hydra: {}}
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    found = substitutable_paths((gkze, hydra, miss), run=run)
    assert found == frozenset({gkze, hydra})
    assert len(stores) == 2
    assert "gkze" in stores[0]
    assert stores[1] == "https://cache.nixos.org"
    assert queried[0] == (gkze, hydra, miss)
    assert queried[1] == (hydra, miss)
    stores.clear()
    queried.clear()
    assert substitutable_paths((gkze,), run=run) == frozenset({gkze})
    assert len(stores) == 1
    assert "gkze" in stores[0]
    assert substitutable_paths(()) == frozenset()
    assert default_cache_present_many(()) == frozenset()


def test_unit_weight_stripe_matches_round_robin_in_one_layer() -> None:
    """A fresh layer with unit weights is round-robin; rust slots stay per-layer."""
    paths = tuple(
        f"/nix/store/{index:032x}-rust_serde-1.0.{index}" for index in range(10)
    )
    layers = (paths,)
    for slot in range(5):
        assert slot_warmup_paths(layers, slot, width=5) == paths[slot::5]
        assert stripe_layer_paths(layers, slot, width=5) == paths[slot::5]
        assert (
            stripe_layer_paths(layers, slot, width=5, weight=lambda _path: 0)
            == paths[slot::5]
        )


def test_cascade_stripes_spread_heavy_paths_instead_of_round_robin() -> None:
    """39166099302: round-robin stacked llvm/clang onto slot 0 (46m vs 11m)."""
    llvm = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-llvm-20.1.0"
    clang = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-clang-20.1.0"
    lights = tuple(f"/nix/store/{index:032x}-coreutils-9.{index}" for index in range(4))
    layers = ((llvm, *lights, clang),)
    assert cascade_path_weight(llvm) > cascade_path_weight(lights[0])
    assert cascade_path_weight(clang) > 1
    assert cascade_path_weight(lights[0]) == 1
    # Round-robin would put llvm and clang on slot 0 (indices 0 and 5).
    assert llvm in slot_cascade_paths(layers, 0)
    assert clang not in slot_cascade_paths(layers, 0)
    striped = [slot_cascade_paths(layers, slot) for slot in range(CASCADE_WARMUP_SLOTS)]
    assert {llvm, clang, *lights} == {path for slot in striped for path in slot}
    assert sum(1 for slot in striped if llvm in slot or clang in slot) == 2


def test_cascade_canary_paths_select_buzz_onnxruntime() -> None:
    """Cascade canary owns buzz-onnxruntime, not src or sibling cascade paths."""
    onnx = "/nix/store/4jjnjbsw4a0ysny10x9p57r057k25nhz-buzz-onnxruntime-1.27.0"
    onnx_drv = f"{onnx}.drv"
    other = "/nix/store/dddddddddddddddddddddddddddddddd-libiconv-1"
    assert is_cascade_canary_store_path(onnx)
    assert is_cascade_canary_store_path(onnx_drv)
    assert not is_cascade_canary_store_path(
        "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-buzz-onnxruntime-src"
    )
    assert not is_cascade_canary_store_path(other)
    assert cascade_canary_paths(((other, onnx), (onnx_drv,))) == (onnx, onnx_drv)
    assert cascade_canary_paths(((other,),)) == ()


def test_cascade_canary_paths_select_versioned_hq() -> None:
    """38986213199: HQ patcher inventory belongs in canary, not the stripe."""
    hq = "/nix/store/qmlzzifp0qqpzgm5lmldpd1jsf4ra2cw-hq-0.11.13"
    hq_drv = f"{hq}.drv"
    helper = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-hq-tray-helper"
    other = "/nix/store/dddddddddddddddddddddddddddddddd-libiconv-1"
    assert is_cascade_canary_store_path(hq)
    assert is_cascade_canary_store_path(hq_drv)
    assert not is_cascade_canary_store_path(helper)
    assert not is_cascade_canary_store_path(other)
    assert cascade_canary_paths(((other, hq), (hq_drv, helper))) == (hq, hq_drv)
    assert cascade_canary_paths(((other, helper),)) == ()


def test_cascade_canary_packages_are_not_rust_warmup_outputs() -> None:
    """Cascade canary may occupy a freed rust slot; packages must not need rust_*."""
    rust = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_zed-0.1.0"
    crate = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_regex-1.0.0"
    assert not is_cascade_canary_store_path(rust)
    assert not is_cascade_canary_store_path(crate)
    assert is_crate2nix_rust_output(rust)
    root = Path(__file__).resolve().parents[2]
    rust_inputs = frozenset({
        "cargo",
        "crane",
        "crate2nix",
        "fenix",
        "rustPlatform",
        "rustc",
    })
    for relative in (
        "packages/buzz/native/onnxruntime.nix",
        "packages/hq/default.nix",
    ):
        package = expect_instance(
            parse_nix_expr((root / relative).read_text(encoding="utf-8")),
            FunctionDefinition,
        )
        names = {
            argument.name
            for argument in package.argument_set
            if isinstance(argument, Identifier)
        }
        assert names.isdisjoint(rust_inputs), relative


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
    git_ui_core = (
        "/nix/store/vr7hj25vd3073rylxr7p82jl02i9gpaq-rust_git_ui_core-0.1.0.drv"
    )
    serde = "/nix/store/serdeaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_serde-1.0.229.drv"
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
            "\n".join((
                rustc,
                models,
                nightly,
                bmake,
                src,
                tree_sitter,
                git_ui_core,
                serde,
            )),
            "",
        )

    assert compiler_input_drvs((zed,), run=query_run) == (rustc, bmake, src, serde)
    assert rust_compile_input_drvs((zed,), run=query_run) == (
        models,
        tree_sitter,
        git_ui_core,
        serde,
    )
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


def test_partition_compiler_substitute_probe_peels_rust_leaves() -> None:
    """39387963366: rustc stays --max-jobs 0; cranelift/serde keep-going."""
    rustc = "/nix/store/xbq69m0caaaaaaaaaaaaaaaaaaaaaaaa-rustc-1.98.1.drv"
    stdenv = "/nix/store/1gx3hvygaaaaaaaaaaaaaaaaaaaaaaaa-stdenv-darwin.drv"
    cranelift = (
        "/nix/store/2nlfdbacyjxij6mbskq39pp97z528q33-"
        "rust_cranelift-codegen-shared-0.135.1.drv"
    )
    serde = "/nix/store/serdeaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_serde-1.0.229.drv"
    assert is_deferred_compiler_rust_drv(cranelift)
    assert is_deferred_compiler_rust_drv(serde)
    assert not is_deferred_compiler_rust_drv(rustc)
    assert not is_deferred_compiler_rust_drv(stdenv)
    assert partition_compiler_substitute_probe_drvs((
        rustc,
        cranelift,
        serde,
        rustc,
        stdenv,
    )) == ((rustc, stdenv), (cranelift, serde))
    assert partition_compiler_substitute_probe_drvs(()) == ((), ())
    assert partition_compiler_substitute_probe_drvs((rustc,)) == ((rustc,), ())
    assert partition_compiler_substitute_probe_drvs((serde,)) == ((), (serde,))


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
    wasmtime_c_api = "/nix/store/28lrlw473ahihpdrzxawclg7r6hvbj25-rust_wasmtime-c-api-impl-48.0.1.drv"
    wasmtime = "/nix/store/2v5460cwzb51v43iqz8ihvqb36d4a18a-rust_wasmtime-48.0.1.drv"
    wasmtime_wasi = (
        "/nix/store/bnsdl4aygysiwj06qbl0v0gg0qaqxpaf-rust_wasmtime-wasi-48.0.1.drv"
    )
    wasmtime_wasi_io = (
        "/nix/store/wasiioaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_wasmtime-wasi-io-48.0.1.drv"
    )
    assert is_crate2nix_rust_output(tree_sitter)
    assert not is_settings_family_store_path(tree_sitter)
    assert is_tree_sitter_family_store_path(tree_sitter)
    assert is_svh_sensitive_store_path(tree_sitter)
    assert is_force_local_allowed_build(tree_sitter)
    assert is_tree_sitter_family_store_path(wasmtime_c_api)
    assert is_tree_sitter_family_store_path(wasmtime)
    assert is_tree_sitter_family_store_path(wasmtime_wasi)
    assert is_tree_sitter_family_store_path(wasmtime_wasi_io)
    assert is_svh_sensitive_store_path(wasmtime_c_api)
    assert is_svh_sensitive_store_path(wasmtime_wasi)
    assert is_force_local_allowed_build(wasmtime_c_api)
    assert is_force_local_allowed_build(wasmtime_wasi)
    assert not is_named_rust_crate_store_path(wasmtime_wasi, "wasmtime")
    assert not is_named_rust_crate_store_path(wasmtime_wasi_io, "wasmtime-wasi")
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
        (
            "/nix/store/az1hmrm8app3kdcn79cxlad1z9zg7s6g-rusty_v8-dbb64c2.drv",
            True,
        ),
        (
            "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_v8-goose-145.0.0.drv",
            False,
        ),
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


def test_source_fetch_input_drvs_drops_helpers_and_toolchain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Planner publish is rust_* crate/nixpkgs FODs, not cpio or rustc."""
    rustc = "/nix/store/cccccccccccccccccccccccccccccccc-rustc-1.98.1.drv"
    cpio = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-cpio-2.15.drv"
    source = "/nix/store/1hm4jss2dwiz4sii2z00qgwwasflxghy-source.drv"
    convert_case = (
        "/nix/store/d64pffww4zyr1ggfcihjh7hb5njx0342-convert_case-0.11.0.tar.gz.drv"
    )
    parent = "/nix/store/av7xckfpaaaaaaaaaaaaaaaaaaaaaaaaa-rust_zed-1.25.0.drv"
    monkeypatch.setattr(
        "lib.update.ci.warmup.compiler_input_drvs",
        lambda *_a, **_k: (rustc, cpio, source, convert_case),
    )
    assert source_fetch_input_drvs((parent,)) == (source, convert_case)


def test_is_source_fetch_mirror_line_skips_crate_fod() -> None:
    """38307411499 slots 2/4: crate tarball mirror lines retry; bmake stays fatal."""
    pin_project = (
        "       > error: cannot download pin-project-1.1.13.tar.gz from any mirror"
    )
    libloading = (
        "       > error: cannot download libloading-0.8.9.tar.gz from any mirror"
    )
    source = "error: cannot download source from any mirror"
    convert_case = "error: cannot download convert_case-0.11.0.tar.gz from any mirror"
    bmake = "error: cannot download bmake-20260313.tar.gz from any mirror"
    rustc = "error: cannot download rustc-1.98.1.tar.gz from any mirror"
    clang_sys = "       > error: cannot download clang-sys-1.9.1.tar.gz from any mirror"
    llvm_sys = "error: cannot download llvm-sys-180.1.0.tar.gz from any mirror"
    rustc_hash = "error: cannot download rustc-hash-2.1.1.tar.gz from any mirror"
    clang_src = "error: cannot download clang-19.1.7.src.tar.xz from any mirror"
    assert is_source_fetch_mirror_line(pin_project)
    assert is_source_fetch_mirror_line(libloading)
    assert is_source_fetch_mirror_line(clang_sys)
    assert is_source_fetch_mirror_line(llvm_sys)
    assert is_source_fetch_mirror_line(rustc_hash)
    assert is_source_fetch_mirror_line(source)
    assert is_source_fetch_mirror_line(convert_case)
    assert is_source_fetch_mirror_line(
        "error: cannot download crate-src from any mirror"
    )
    assert is_source_fetch_mirror_line(
        "error: cannot download vendor-source from any mirror"
    )
    assert not is_source_fetch_mirror_line(bmake)
    assert not is_source_fetch_mirror_line(rustc)
    assert not is_source_fetch_mirror_line(clang_src)
    assert not is_source_fetch_mirror_line("error: cannot download foo from any mirror")
    assert not is_source_fetch_mirror_line("unable to download 'https://example'")
    assert warmup_fatal_line(pin_project) is None
    assert warmup_fatal_line(libloading) is None
    assert warmup_fatal_line(clang_sys) is None
    assert (
        warmup_fatal_line(
            "error: Cannot build '/nix/store/mfrnbrs6xyj0fz76nz31a3r028ip420p-clang-sys-1.9.1.tar.gz.drv'."
        )
        is None
    )
    raise_if_warmup_fatal(pin_project)
    raise_if_warmup_fatal(libloading)
    raise_if_warmup_fatal(clang_sys)
    assert warmup_fatal_line(bmake) == "cannot download from any mirror"
    with pytest.raises(WarmupFatalError, match="cannot download from any mirror"):
        raise_if_warmup_fatal(bmake)
    assert warmup_fatal_line(rustc) == "cannot download from any mirror"
    assert warmup_fatal_line(clang_src) == "cannot download from any mirror"


def test_rust_cannot_build_after_source_fod_miss_is_not_fatal() -> None:
    """38359636482 slot 2: rust_markup5ever Cannot-build follows the tarball miss.

    markup5ever-0.38.0.tar.gz curl (6)'d static.crates.io. The source
    FOD line stream-skipped; the parent rust_* Cannot-build then aborted
    before DNS retry. A lone rust_* Cannot-build stays fatal.
    """
    rust = (
        "error: Cannot build "
        "'/nix/store/fa0ic3pgpb37rrh8akp5f54wmrml4bn6-rust_markup5ever-0.38.0.drv'."
    )
    rustc = (
        "error: Cannot build "
        "'/nix/store/xbq69m0caaaaaaaaaaaaaaaaaaaaaaaa-rustc-1.98.1.drv'."
    )
    assert warmup_fatal_line(rust) == "cannot build"
    assert warmup_fatal_line(rust, source_fetch_miss=True) is None
    raise_if_warmup_fatal(rust, source_fetch_miss=True)
    with pytest.raises(WarmupFatalError, match="cannot build"):
        raise_if_warmup_fatal(rust)
    assert warmup_fatal_line(rustc, source_fetch_miss=True) == "cannot build"


def test_source_fetch_retry_drvs_refuse_compiler_and_rust() -> None:
    """38258066093: retry source.drv / convert_case tarball, not rustc."""
    source = "/nix/store/1hm4jss2dwiz4sii2z00qgwwasflxghy-source.drv"
    convert_case = (
        "/nix/store/d64pffww4zyr1ggfcihjh7hb5njx0342-convert_case-0.11.0.tar.gz.drv"
    )
    rustc = "/nix/store/cccccccccccccccccccccccccccccccc-rustc-1.98.1.drv"
    settings = "/nix/store/dddddddddddddddddddddddddddddddd-rust_settings-0.1.0.drv"
    pin_project = (
        "/nix/store/72ws9414aaaaaaaaaaaaaaaaaaaaaaaa-pin-project-1.1.13.tar.gz.drv"
    )
    libloading = (
        "/nix/store/clbp5wbnz9r88lngchd67lpxkk7y6my9-libloading-0.8.9.tar.gz.drv"
    )
    clang_sys = "/nix/store/mfrnbrs6xyj0fz76nz31a3r028ip420p-clang-sys-1.9.1.tar.gz.drv"
    rusty_v8 = "/nix/store/az1hmrm8app3kdcn79cxlad1z9zg7s6g-rusty_v8-dbb64c2.drv"
    goose = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_v8-goose-145.0.0.drv"
    pnpm = "/nix/store/qbvbj2w3qxhn5c0x43f929k28plr1r8s-bb-pnpm-deps.drv"
    vendor_source = "/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-vendor-source.drv"
    assert source_fetch_retry_drvs((
        DerivationValidationFailure(
            source="root-warmup",
            installable=f"{source}^*",
            message="error: Cannot build '/nix/store/...-source.drv'.",
        ),
        DerivationValidationFailure(
            source="root-warmup",
            installable=f"{convert_case}^*",
            message="error: Cannot build '/nix/store/...-convert_case-0.11.0.tar.gz.drv'.",
        ),
        DerivationValidationFailure(
            source="root-warmup",
            installable=f"{rustc}^*",
            message="error: Cannot build '/nix/store/...-rustc-1.98.1.drv'.",
        ),
        DerivationValidationFailure(
            source="root-warmup",
            installable=f"{settings}^*",
            message="error: Cannot build '/nix/store/...-rust_settings-0.1.0.drv'.",
        ),
        DerivationValidationFailure(
            source="root-warmup",
            installable=f"{source}^*",
            message="error: hash mismatch in fixed-output derivation",
        ),
        DerivationValidationFailure(
            source="root-warmup",
            installable=f"{pin_project}^*",
            message=(
                "error: cannot download pin-project-1.1.13.tar.gz from any mirror"
            ),
        ),
        DerivationValidationFailure(
            source="root-warmup",
            installable=f"{libloading}^*",
            message="error: cannot download libloading-0.8.9.tar.gz from any mirror",
        ),
        DerivationValidationFailure(
            source="root-warmup",
            installable=f"{clang_sys}^*",
            message="error: cannot download clang-sys-1.9.1.tar.gz from any mirror",
        ),
        DerivationValidationFailure(
            source="root-warmup",
            installable=f"{source}^*",
            message="error: Cannot build '/nix/store/...-source.drv'.",
        ),
        DerivationValidationFailure(
            source="root-warmup",
            installable=f"{goose}^*",
            message=(f"error: hash mismatch in fixed-output derivation '{rusty_v8}':"),
        ),
        DerivationValidationFailure(
            source="root-warmup",
            installable=f"{pnpm}^*",
            message=(f"error: hash mismatch in fixed-output derivation '{pnpm}':"),
        ),
        DerivationValidationFailure(
            source="root-warmup",
            installable=f"{vendor_source}^*",
            message="builder failed",
        ),
    )) == (source, convert_case, pin_project, libloading, clang_sys, rusty_v8)


def test_source_fetch_failure_drv_prefers_message_fod() -> None:
    """39234076105: parent rust_v8-goose named rusty_v8 in the hash line."""
    rusty_v8 = "/nix/store/az1hmrm8app3kdcn79cxlad1z9zg7s6g-rusty_v8-dbb64c2.drv"
    goose = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_v8-goose-145.0.0.drv"
    settings = "/nix/store/dddddddddddddddddddddddddddddddd-rust_settings-0.1.0.drv"
    wrapped = DerivationValidationFailure(
        source="root-warmup",
        installable=f"{goose}^*",
        message=f"error: hash mismatch in fixed-output derivation '{rusty_v8}':",
    )
    compile_miss = DerivationValidationFailure(
        source="root-warmup",
        installable=f"{settings}^*",
        message="error: Cannot build '/nix/store/...-rust_settings-0.1.0.drv'.",
    )
    assert source_fetch_failure_drv(wrapped) == rusty_v8
    assert source_fetch_failure_drv(compile_miss) == ""
    assert source_fetch_leftover_drvs((wrapped, compile_miss)) == (rusty_v8,)


def test_is_source_fetch_transport_error_matches_chromium_503() -> None:
    """39234076105 slot 4 streamed HTTP 503 / RPC failed from googlesource."""
    assert is_source_fetch_transport_error("error: RPC failed; result=22")
    assert is_source_fetch_transport_error("remote: HTTP 503 Service Unavailable")
    assert is_source_fetch_transport_error(
        "fatal: unable to access 'https://chromium.googlesource.com/libunwind.git/': "
        "The requested URL returned error: 503"
    )
    assert not is_source_fetch_transport_error(
        "error: Cannot build '/nix/store/x.drv'."
    )
    assert not is_source_fetch_transport_error("HTTP 404 Not Found")
    assert is_source_fetch_transport_error("err(default): BadHttpStatus")
    assert is_source_fetch_transport_error(
        "curl: (22) The requested URL returned error: 502"
    )


def test_source_fetch_network_ready_requires_both_hosts() -> None:
    """Prove github.com and crates.io before retrying a FOD fetch."""
    seen: list[str] = []

    def resolve(host: str, port: int) -> object:
        assert port == 443
        seen.append(host)
        return [("AF_INET", host)]

    assert SOURCE_FETCH_DNS_HOSTS == ("github.com", "static.crates.io")
    assert source_fetch_network_ready(resolve=resolve)
    assert seen == list(SOURCE_FETCH_DNS_HOSTS)

    def dead_crates(host: str, port: int) -> object:
        if host == "static.crates.io":
            raise OSError("Could not resolve host")
        return [("AF_INET", host)]

    assert source_fetch_network_ready(resolve=dead_crates) is False


def test_wait_for_source_fetch_network_backs_off_before_probe() -> None:
    """Immediate retry would hit the same dead DNS as 38258066093 18:53Z."""
    sleeps: list[float] = []

    def resolve(host: str, port: int) -> object:
        assert sleeps == [SOURCE_FETCH_RETRY_BACKOFF_SECONDS]
        return [("AF_INET", host)]

    assert wait_for_source_fetch_network(resolve=resolve, sleep=sleeps.append)
    assert sleeps == [SOURCE_FETCH_RETRY_BACKOFF_SECONDS]
    assert (
        wait_for_source_fetch_network(
            resolve=lambda *_a: (_ for _ in ()).throw(OSError("dns")),
            sleep=lambda _seconds: None,
            backoff=3.0,
        )
        is False
    )


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
    rustc_darwin = "/nix/store/l2nj43cyy37grnkh3m8h4jzhpsx8yp8b-rustc-1.98.1-aarch64-apple-darwin.drv"
    stdenv = "/nix/store/1gx3hvygaaaaaaaaaaaaaaaaaaaaaaaa-stdenv-darwin.drv"
    clang_sys = "/nix/store/mfrnbrs6xyj0fz76nz31a3r028ip420p-clang-sys-1.9.1.tar.gz.drv"
    clang_src = (
        "/nix/store/cccccccccccccccccccccccccccccccc-clang-19.1.7.src.tar.xz.drv"
    )
    assert is_compiler_local_helper_store_path(patchutils)
    assert is_compiler_local_helper_store_path(pbzx)
    assert is_compiler_local_helper_store_path(cpio)
    assert not is_compiler_local_helper_store_path(rustc)
    assert not is_compiler_local_helper_store_path(stdenv)
    assert is_compiler_must_substitute_store_path(rustc)
    assert is_compiler_must_substitute_store_path(stdenv)
    assert is_compiler_must_substitute_store_path(clang_src)
    assert not is_compiler_must_substitute_store_path(cpio)
    assert not is_compiler_must_substitute_store_path(clang_sys)
    assert is_source_fetch_store_path(clang_sys)
    assert is_rust_overlay_binary_component(rustc_darwin)
    assert not is_rust_overlay_binary_component(rustc)
    assert not is_compiler_must_substitute_store_path(rustc_darwin)
    assert partition_compiler_input_drvs((rustc, stdenv, patchutils, pbzx, cpio)) == (
        (rustc, stdenv),
        (patchutils, pbzx, cpio),
    )


def test_partition_svh_sensitive_peels_workspace_before_serde() -> None:
    """38121661811 slots 1/2 built feature_flags before rust_settings."""
    feature_flags = (
        "/nix/store/0anpp4kml0bzvpjm2i4ykqgdbl00rn23-rust_feature_flags-0.1.0.drv"
    )
    git_hosting = "/nix/store/2h87371ygf9p3v3xc5kvgv5ci769dhz5-rust_git_hosting_providers-0.1.0.drv"
    settings = "/nix/store/hwlj8la2ply5al3dhkz0d84z4rj3j61m-rust_settings-0.1.0.drv"
    serde = "/nix/store/serdeaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_serde-1.0.229.drv"
    assert is_zed_workspace_crate_store_path(feature_flags)
    assert is_svh_sensitive_store_path(feature_flags)
    assert is_svh_sensitive_store_path(git_hosting)
    assert not is_svh_sensitive_store_path(serde)
    assert partition_svh_sensitive_drvs((
        serde,
        feature_flags,
        settings,
        git_hosting,
        serde,
    )) == (
        (serde,),
        (feature_flags, settings, git_hosting),
    )


def test_partition_crane_deps_peels_crates_io_before_workspace() -> None:
    """Crane vendors crates.io rust_*; workspace 0.1.0 stays in the SVH family."""
    serde = "/nix/store/serdeaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_serde-1.0.229.drv"
    settings = "/nix/store/hwlj8la2ply5al3dhkz0d84z4rj3j61m-rust_settings-0.1.0.drv"
    cargo = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-cargo-package-sha2-0.10.9"
    assert is_crane_dep_store_path(serde)
    assert not is_crane_dep_store_path(settings)
    assert not is_crane_dep_store_path(cargo)
    assert CRANE_DEP_CHUNK == 8
    assert partition_crane_dep_drvs((cargo, serde, settings, serde)) == (
        (cargo, settings),
        (serde,),
    )
    assert partition_crane_dep_drvs((cargo,)) == ((cargo,), ())
    assert partition_crane_dep_drvs(()) == ((), ())


def test_tree_sitter_wasmtime_c_api_is_svh_not_compiler_input() -> None:
    """38096149800 / 38103781962: local crates E0463'd Cachix wasmtime*.

    The rlib was already on the rustc --extern line. Cargo.nix already
    lists wasmtime-c-api-impl and wasmtime-wasi. Force-local the
    wasmtime intern with tree-sitter; do not --max-jobs 0 a Cachix rlib.
    """
    wasmtime_c_api = "/nix/store/28lrlw473ahihpdrzxawclg7r6hvbj25-rust_wasmtime-c-api-impl-48.0.1.drv"
    wasmtime = "/nix/store/2v5460cwzb51v43iqz8ihvqb36d4a18a-rust_wasmtime-48.0.1.drv"
    wasmtime_wasi = (
        "/nix/store/bnsdl4aygysiwj06qbl0v0gg0qaqxpaf-rust_wasmtime-wasi-48.0.1.drv"
    )
    wasmtime_wasi_io = (
        "/nix/store/wasiioaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_wasmtime-wasi-io-48.0.1.drv"
    )
    serde = "/nix/store/serdeaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_serde-1.0.229.drv"
    assert is_named_rust_crate_store_path(wasmtime_c_api, "wasmtime-c-api-impl")
    assert is_named_rust_crate_store_path(wasmtime, "wasmtime")
    assert is_named_rust_crate_store_path(wasmtime_wasi, "wasmtime-wasi")
    assert is_named_rust_crate_store_path(wasmtime_wasi_io, "wasmtime-wasi-io")
    assert not is_named_rust_crate_store_path(wasmtime_c_api, "wasmtime")
    assert not is_named_rust_crate_store_path(wasmtime_wasi, "wasmtime")
    assert not is_named_rust_crate_store_path(wasmtime_wasi_io, "wasmtime-wasi")
    assert is_tree_sitter_family_store_path(wasmtime_c_api)
    assert is_tree_sitter_family_store_path(wasmtime_wasi)
    assert is_tree_sitter_family_store_path(wasmtime_wasi_io)
    assert is_svh_sensitive_store_path(wasmtime_c_api)
    assert is_svh_sensitive_store_path(wasmtime_wasi)
    assert not is_svh_sensitive_store_path(serde)
    rustc = "/nix/store/xbq69m0caaaaaaaaaaaaaaaaaaaaaaaa-rustc-1.98.1.drv"

    def query_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args, 0, "\n".join((rustc, wasmtime_c_api, wasmtime_wasi, serde)), ""
        )

    assert compiler_input_drvs(("/nix/store/zed.drv",), run=query_run) == (
        rustc,
        serde,
    )


def test_zed_workspace_0_1_0_crates_are_svh_not_compiler_inputs() -> None:
    """38086720924: rust_git_ui_core --max-jobs 0 built the 37 workspace.

    Workspace rust_*-0.1.0 force-local with the family. crates.io
    rust_serde-1.0.229 / rust_lsp-types-0.95.1 stay compiler substitutes.
    rust_foo-10.1.0 must not match the 0.1.0 marker.
    """
    git_ui_core = (
        "/nix/store/vr7hj25vd3073rylxr7p82jl02i9gpaq-rust_git_ui_core-0.1.0.drv"
    )
    git_ui_lib = (
        "/nix/store/vr7hj25vd3073rylxr7p82jl02i9gpaq-rust_git_ui_core-0.1.0-lib"
    )
    gpui = "/nix/store/otheraaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_gpui-0.1.0"
    serde = "/nix/store/serdeaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_serde-1.0.229.drv"
    lsp_types = (
        "/nix/store/lspaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_lsp-types-0.95.1.drv"
    )
    ten = "/nix/store/tenaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_foo-10.1.0.drv"
    tree_sitter = (
        "/nix/store/1zsfiw8m72a6ql2wx3f5bpq3mn7vnw77-rust_tree-sitter-0.27.0.drv"
    )
    rust_zed = "/nix/store/av7xckfpaaaaaaaaaaaaaaaaaaaaaaaaa-rust_zed-1.25.0.drv"
    assert is_zed_workspace_crate_store_path(git_ui_core)
    assert is_zed_workspace_crate_store_path(git_ui_lib)
    assert is_zed_workspace_crate_store_path(gpui)
    assert is_svh_sensitive_store_path(git_ui_core)
    assert is_force_local_allowed_build(git_ui_core)
    assert not is_zed_workspace_crate_store_path(serde)
    assert not is_zed_workspace_crate_store_path(lsp_types)
    assert not is_zed_workspace_crate_store_path(ten)
    assert not is_svh_sensitive_store_path(serde)
    assert not is_zed_workspace_crate_store_path(tree_sitter)
    assert is_svh_sensitive_store_path(tree_sitter)
    assert not is_zed_workspace_crate_store_path(rust_zed)
    assert is_svh_sensitive_store_path(rust_zed)


def test_rust_svh_input_drvs_keeps_workspace_and_drops_zed_leaf() -> None:
    """38151215158: rust_zed inputs include cached rust_collab_ui-0.1.0."""
    rust_zed = "/nix/store/dya03z7ahr923gj36783368fimlqvlqd-rust_zed-1.25.0.drv"
    nightly = (
        "/nix/store/rlcwld41aaaaaaaaaaaaaaaaaaaaaaaaa-"
        "zed-editor-nightly-unstable-f16f965.drv"
    )
    collab_ui = "/nix/store/in05ddj652lyxf7fddyphh6npd2qd52c-rust_collab_ui-0.1.0.drv"
    extension_host = (
        "/nix/store/exthostaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_extension_host-0.1.0.drv"
    )
    rustc = "/nix/store/xbq69m0caaaaaaaaaaaaaaaaaaaaaaaa-rustc-1.98.1.drv"
    serde = "/nix/store/serdeaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_serde-1.0.229.drv"
    collab_lib = "/nix/store/in05ddj652lyxf7fddyphh6npd2qd52c-rust_collab_ui-0.1.0-lib"
    assert is_zed_workspace_crate_store_path(collab_ui)
    assert is_zed_workspace_crate_store_path(collab_lib)
    assert is_svh_sensitive_store_path(collab_ui)
    assert rust_zed_member_warmup_outputs(((collab_lib, rust_zed, nightly),)) == (
        collab_lib,
    )

    def query_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args,
            0,
            "\n".join((rustc, collab_ui, extension_host, rust_zed, nightly, serde)),
            "",
        )

    assert rust_svh_input_drvs((rust_zed,), run=query_run) == (
        collab_ui,
        extension_host,
    )


def test_zed_family_policy_is_atomic_all_or_force_local() -> None:
    """Mixed Cachix presence must not substitute a subset of the family."""
    settings = "/nix/store/2y7vj1wq5nz030asgn7rhipbcx5aya89-rust_settings-0.1.0"
    content = "/nix/store/ma14flyg1v5b4vhinb2l0klw1xmdg9nz-rust_settings_content-0.1.0"
    rustc_a = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rustc-1.98.1.drv"
    rustc_b = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rustc-1.98.1.drv"
    family = (content, settings)
    assert zed_family_cachix_presence((), present=lambda _p: True) == "none"
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
    git_ui_core = "/nix/store/vr7hj25vd3073rylxr7p82jl02i9gpaq-rust_git_ui_core-0.1.0"
    layers = ((content,), (tree_sitter,), (settings,), (git_ui_core,))
    assert zed_family_warmup_outputs(layers) == (
        content,
        tree_sitter,
        settings,
        git_ui_core,
    )
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
    """#1270: --max-jobs 0 Cannot-build is retried; 406-drv rustc counts stay fatal."""
    cannot = "error: Cannot build '/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-cpio-2.15.drv'."
    assert warmup_fatal_line(cannot, substitute_only=True) is None
    raise_if_warmup_fatal(cannot, substitute_only=True)
    assert warmup_fatal_line(cannot) == "cannot build"
    with pytest.raises(WarmupFatalError, match="cannot build"):
        raise_if_warmup_fatal(cannot)
    source = (
        "error: Cannot build '/nix/store/1hm4jss2dwiz4sii2z00qgwwasflxghy-source.drv'."
    )
    convert_case = (
        "error: Cannot build "
        "'/nix/store/d64pffww4zyr1ggfcihjh7hb5njx0342-"
        "convert_case-0.11.0.tar.gz.drv'."
    )
    settings = (
        "error: Cannot build "
        "'/nix/store/2y7vj1wq5nz030asgn7rhipbcx5aya89-rust_settings-0.1.0.drv'."
    )
    assert warmup_fatal_line(source) is None
    assert warmup_fatal_line(convert_case) is None
    raise_if_warmup_fatal(source)
    raise_if_warmup_fatal(convert_case)
    assert warmup_fatal_line(settings) == "cannot build"
    with pytest.raises(WarmupFatalError, match="cannot build"):
        raise_if_warmup_fatal(settings)
    svh = "error[E0463]: can't find crate for `settings_content`"
    assert warmup_fatal_line(svh, substitute_only=True) is not None
    with pytest.raises(WarmupFatalError, match="SVH"):
        raise_if_warmup_fatal(svh, substitute_only=True)
    huge = "these 406 derivations will be built:"
    assert warmup_fatal_line(huge, substitute_only=True) is not None
    with pytest.raises(WarmupFatalError, match="will-be-built"):
        raise_if_warmup_fatal(huge, substitute_only=True)
    cranelift = "these 40 derivations will be built:"
    assert warmup_fatal_line(cranelift, substitute_only=True) is None
    raise_if_warmup_fatal(cranelift, substitute_only=True)
    svh_keep = "error[E0463]: can't find crate for `settings_content`"
    assert warmup_fatal_line(svh_keep, keep_going=True) is None
    raise_if_warmup_fatal(svh_keep, keep_going=True)
    family = "these 37 derivations will be built:"
    assert warmup_fatal_line(family) is not None
    assert warmup_fatal_line(family, keep_going=True) is None
    raise_if_warmup_fatal(family, keep_going=True)
    # 39217658040: gitbutler others 39 is family-scale, not 406 bootstrap.
    agentlog = "these 39 derivations will be built:"
    assert warmup_fatal_line(agentlog) is not None
    assert warmup_fatal_line(agentlog, keep_going=True) is None
    raise_if_warmup_fatal(agentlog, keep_going=True)
    assert warmup_fatal_line(huge, keep_going=True) is not None
    # 38506525442: Darwin root-closures streamed 470 wbb; that line is
    # not a rust-warmup abort when the caller turns the ceiling off.
    host = "these 470 derivations will be built:"
    assert warmup_fatal_line(host) is not None
    assert warmup_fatal_line(host, apply_wbb_limit=False) is None
    raise_if_warmup_fatal(host, apply_wbb_limit=False)
    mismatch = (
        "error: hash mismatch in fixed-output derivation "
        "'/nix/store/qbvbj2w3qxhn5c0x43f929k28plr1r8s-bb-pnpm-deps.drv':"
    )
    assert warmup_fatal_line(mismatch, keep_going=True) == "fixed-output hash mismatch"
    with pytest.raises(WarmupFatalError, match="fixed-output hash mismatch"):
        raise_if_warmup_fatal(mismatch, keep_going=True)
    assert darwin_fod_canary_installables() == (
        "path:.#pkgs.aarch64-darwin.bb.pnpmDeps",
        "path:.#pkgs.aarch64-darwin.emdash.pnpmDeps",
        "path:.#pkgs.aarch64-darwin.comet.src",
    )
    assert darwin_leaf_prebuild_installables() == ("path:.#pkgs.aarch64-darwin.zeron",)


def test_warmup_fatal_skips_tainted_source_fod_hash_mismatch() -> None:
    """39234076105: rusty_v8 after 503 retries; bb-pnpm-deps stays a pin error."""
    rusty = (
        "error: hash mismatch in fixed-output derivation "
        "'/nix/store/az1hmrm8app3kdcn79cxlad1z9zg7s6g-rusty_v8-dbb64c2.drv':"
    )
    pnpm = (
        "error: hash mismatch in fixed-output derivation "
        "'/nix/store/qbvbj2w3qxhn5c0x43f929k28plr1r8s-bb-pnpm-deps.drv':"
    )
    assert warmup_fatal_line(rusty) == "fixed-output hash mismatch"
    assert warmup_fatal_line(rusty, source_fetch_miss=True) is None
    raise_if_warmup_fatal(rusty, source_fetch_miss=True)
    assert warmup_fatal_line(pnpm, source_fetch_miss=True) == (
        "fixed-output hash mismatch"
    )
    bare = "error: hash mismatch in fixed-output derivation"
    assert warmup_fatal_line(bare, source_fetch_miss=True) == (
        "fixed-output hash mismatch"
    )


def test_realize_source_fetch_drvs_is_noop_without_drvs(tmp_path: Path) -> None:
    assert realize_source_fetch_drvs((), flake_root=tmp_path) == ()


def test_drv_input_references_ignore_non_drv_query_lines() -> None:
    parent = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_zed-1.drv"
    child = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_settings-0.1.0.drv"
    output = "/nix/store/cccccccccccccccccccccccccccccccc-rust_settings-0.1.0"

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args, 0, f"{child}\n{output}\nnot-a-store\n", ""
        )

    assert drv_input_references((parent,), run=run) == (child,)


def test_realize_source_fetch_drvs_retries_after_dns_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backoff, prove DNS, retry once; leftover FOD still fails the slot."""
    source = "/nix/store/1hm4jss2dwiz4sii2z00qgwwasflxghy-source.drv"
    convert_case = (
        "/nix/store/d64pffww4zyr1ggfcihjh7hb5njx0342-convert_case-0.11.0.tar.gz.drv"
    )
    cannot = DerivationValidationFailure(
        source="root-warmup",
        installable=f"{source}^*",
        message="error: Cannot build '/nix/store/...-source.drv'.",
    )
    calls: list[tuple[str, ...]] = []
    sleeps: list[float] = []

    def realize(
        paths: object, *_args: object, **_kwargs: object
    ) -> tuple[DerivationValidationFailure, ...]:
        ordered = tuple(paths)
        calls.append(ordered)
        if len(calls) == 1:
            return (cannot,)
        return ()

    monkeypatch.setattr("lib.update.ci.warmup.realize_warmup_outputs", realize)
    assert (
        realize_source_fetch_drvs(
            (source, convert_case, source),
            flake_root=tmp_path,
            resolve=lambda *_a: [("AF_INET", "ok")],
            sleep=sleeps.append,
        )
        == ()
    )
    assert calls == [(source, convert_case), (source,)]
    assert sleeps == [SOURCE_FETCH_RETRY_BACKOFF_SECONDS]


def test_realize_source_fetch_drvs_fails_closed_when_dns_stays_dead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Do not retry a FOD while github.com / crates.io still fail DNS."""
    source = "/nix/store/1hm4jss2dwiz4sii2z00qgwwasflxghy-source.drv"
    cannot = DerivationValidationFailure(
        source="root-warmup",
        installable=f"{source}^*",
        message="error: Cannot build '/nix/store/...-source.drv'.",
    )

    def realize(
        paths: object, *_args: object, **_kwargs: object
    ) -> tuple[DerivationValidationFailure, ...]:
        return (cannot,)

    monkeypatch.setattr("lib.update.ci.warmup.realize_warmup_outputs", realize)
    assert realize_source_fetch_drvs(
        (source,),
        flake_root=tmp_path,
        resolve=lambda *_a: (_ for _ in ()).throw(OSError("dns")),
        sleep=lambda _seconds: None,
    ) == (cannot,)


def test_realize_source_fetch_drvs_retries_tainted_hash_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """39234076105: 503-tainted rusty_v8 hash mismatch retries after DNS."""
    rusty_v8 = "/nix/store/az1hmrm8app3kdcn79cxlad1z9zg7s6g-rusty_v8-dbb64c2.drv"
    mismatch = DerivationValidationFailure(
        source="root-warmup",
        installable=f"{rusty_v8}^*",
        message=f"error: hash mismatch in fixed-output derivation '{rusty_v8}':",
    )
    calls: list[tuple[str, ...]] = []

    def realize(
        paths: object, *_args: object, **_kwargs: object
    ) -> tuple[DerivationValidationFailure, ...]:
        ordered = tuple(paths)
        calls.append(ordered)
        if len(calls) == 1:
            return (mismatch,)
        return ()

    monkeypatch.setattr("lib.update.ci.warmup.realize_warmup_outputs", realize)
    assert (
        realize_source_fetch_drvs(
            (rusty_v8,),
            flake_root=tmp_path,
            resolve=lambda *_a: [("AF_INET", "ok")],
            sleep=lambda _seconds: None,
        )
        == ()
    )
    assert calls == [(rusty_v8,), (rusty_v8,)]


def test_realize_source_fetch_drvs_fails_closed_after_retry_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second Cannot-build must not let the parent rust_* proceed."""
    source = "/nix/store/1hm4jss2dwiz4sii2z00qgwwasflxghy-source.drv"
    cannot = DerivationValidationFailure(
        source="root-warmup",
        installable=f"{source}^*",
        message="error: Cannot build '/nix/store/...-source.drv'.",
    )

    def realize(
        paths: object, *_args: object, **_kwargs: object
    ) -> tuple[DerivationValidationFailure, ...]:
        return (cannot,)

    monkeypatch.setattr("lib.update.ci.warmup.realize_warmup_outputs", realize)
    assert realize_source_fetch_drvs(
        (source,),
        flake_root=tmp_path,
        resolve=lambda *_a: [("AF_INET", "ok")],
        sleep=lambda _seconds: None,
    ) == (cannot,)


def test_publish_warmup_source_fetches_realizes_rust_fods(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """plan-shards substitutes gkze-missing hits, then fetches local FODs.

    38296208698 fail-closed plan-shards on 562 leftover crate2nix FODs
    after ~50 min of Linux fetch and skipped every Darwin job. Never
    fail-close the plan; Darwin still fail-closes a needed miss.
    39538011102: aarch64-darwin crate FODs always fail ``platform
    mismatch`` on x86_64-linux, so only host/builtin leftovers fetch.
    """
    from lib.update.ci.warmup import RootWarmupStats, WarmupPlan

    rust = "/nix/store/av7xckfpaaaaaaaaaaaaaaaaaaaaaaaaa-rust_zed-1.25.0"
    rust_drv = "/nix/store/av7xckfpaaaaaaaaaaaaaaaaaaaaaaaaa-rust_zed-1.25.0.drv"
    source = "/nix/store/1hm4jss2dwiz4sii2z00qgwwasflxghy-source.drv"
    convert_case = (
        "/nix/store/d64pffww4zyr1ggfcihjh7hb5njx0342-convert_case-0.11.0.tar.gz.drv"
    )
    pin_project = (
        "/nix/store/72ws9414aaaaaaaaaaaaaaaaaaaaaaaa-pin-project-1.1.13.tar.gz.drv"
    )
    libloading = (
        "/nix/store/clbp5wbnz9r88lngchd67lpxkk7y6my9-libloading-0.8.9.tar.gz.drv"
    )
    clang_sys = "/nix/store/mfrnbrs6xyj0fz76nz31a3r028ip420p-clang-sys-1.9.1.tar.gz.drv"
    plan = WarmupPlan(
        schemaVersion=1,
        system="aarch64-darwin",
        substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
        warmupOutputs=(rust,),
        outputDrvs={rust: rust_drv},
        perRoot={
            "darwin-argus": RootWarmupStats(outputs=1, missing=1, warmup=1, remaining=0)
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
    assert rust_warmup_output_drvs(plan) == (rust_drv,)

    def refuse_realize(*_args: object, **_kwargs: object) -> tuple[()]:
        msg = "empty FOD set must not realize"
        raise AssertionError(msg)

    monkeypatch.setattr(
        "lib.update.ci.warmup.source_fetch_input_drvs", lambda *_a, **_k: ()
    )
    monkeypatch.setattr(
        "lib.update.ci.warmup.unpublished_source_fetches",
        lambda fods, **_k: tuple(fods),
    )
    assert (
        publish_warmup_source_fetches(plan, flake_root=tmp_path, realize=refuse_realize)
        == ()
    )
    monkeypatch.setattr(
        "lib.update.ci.warmup.source_fetch_input_drvs",
        lambda *_a, **_k: (source, convert_case, pin_project, libloading, clang_sys),
    )
    seen: list[tuple[tuple[str, ...], bool, bool]] = []
    leftover = DerivationValidationFailure(
        source="root-warmup",
        installable=f"{source}^*",
        message="error: Cannot build '/nix/store/...-source.drv'.",
    )
    pin_miss = DerivationValidationFailure(
        source="root-warmup",
        installable=f"{pin_project}^*",
        message="error: cannot download pin-project-1.1.13.tar.gz from any mirror",
    )
    lib_miss = DerivationValidationFailure(
        source="root-warmup",
        installable=f"{libloading}^*",
        message="error: cannot download libloading-0.8.9.tar.gz from any mirror",
    )
    clang_miss = DerivationValidationFailure(
        source="root-warmup",
        installable=f"{clang_sys}^*",
        message="error: cannot download clang-sys-1.9.1.tar.gz from any mirror",
    )
    rust_miss = DerivationValidationFailure(
        source="root-warmup",
        installable="/nix/store/dddddddddddddddddddddddddddddddd-rust_settings-0.1.0.drv^*",
        message="error: Cannot build '/nix/store/...-rust_settings-0.1.0.drv'.",
    )
    substitute_misses = (leftover, pin_miss, lib_miss, clang_miss)
    assert source_fetch_leftover_drvs((*substitute_misses, rust_miss)) == (
        source,
        pin_project,
        libloading,
        clang_sys,
    )

    def realize(
        paths: object, *_args: object, **kwargs: object
    ) -> tuple[DerivationValidationFailure, ...]:
        seen.append((
            tuple(paths),
            bool(kwargs.get("substitute_only")),
            bool(kwargs.get("keep_going")),
        ))
        if kwargs.get("substitute_only"):
            return substitute_misses
        return ()

    monkeypatch.setattr(
        "lib.update.ci.warmup.source_fetch_drv_systems",
        lambda drvs, **_k: {
            source: "x86_64-linux",
            pin_project: "aarch64-darwin",
            libloading: "aarch64-darwin",
            clang_sys: "builtin",
        },
    )
    published = publish_warmup_source_fetches(
        plan, flake_root=tmp_path, realize=realize, system="x86_64-linux"
    )
    assert seen == [
        ((source, convert_case, pin_project, libloading, clang_sys), True, True),
        ((source, clang_sys), False, True),
    ]
    assert [failure.installable for failure in published] == [
        f"{pin_project}^*",
        f"{libloading}^*",
    ]
    assert all("cannot build here" in failure.message for failure in published)

    def realize_still_missing(
        paths: object, *_args: object, **kwargs: object
    ) -> tuple[DerivationValidationFailure, ...]:
        if kwargs.get("substitute_only"):
            return substitute_misses
        return (pin_miss, lib_miss, clang_miss)

    monkeypatch.setattr(
        "lib.update.ci.warmup.source_fetch_drv_systems",
        lambda drvs, **_k: dict.fromkeys(drvs, "x86_64-linux"),
    )
    stubborn = publish_warmup_source_fetches(
        plan, flake_root=tmp_path, realize=realize_still_missing, system="x86_64-linux"
    )
    assert stubborn == (pin_miss, lib_miss, clang_miss)
    assert source_fetch_publish_leftover_line(stubborn) in capsys.readouterr().err
    assert (
        publish_warmup_source_fetches(
            plan, flake_root=tmp_path, realize=lambda *_a, **_k: ()
        )
        == ()
    )
    monkeypatch.setattr(
        "lib.update.ci.warmup.source_fetch_drv_systems",
        lambda drvs, **_k: dict.fromkeys(drvs, "aarch64-darwin"),
    )
    monkeypatch.setattr(
        "lib.update.ci.warmup.get_current_nix_platform", lambda: "x86_64-linux"
    )
    seen.clear()
    foreign = publish_warmup_source_fetches(plan, flake_root=tmp_path, realize=realize)
    assert seen == [
        ((source, convert_case, pin_project, libloading, clang_sys), True, True)
    ]
    assert len(foreign) == 4
    monkeypatch.setattr(
        "lib.update.ci.warmup.unpublished_source_fetches", lambda *_a, **_k: ()
    )
    assert (
        publish_warmup_source_fetches(plan, flake_root=tmp_path, realize=refuse_realize)
        == ()
    )


def test_unpublished_source_fetches_skips_gkze_hits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """39538011102 substituted 2284 FODs gkze already had; skip those."""
    hit = "/nix/store/jpbxlpf8c33hg7aa0jzlrqdq5ia13zq7-brotli-8.0.4.tar.gz.drv"
    miss = (
        "/nix/store/rys8l14n4riv0mxbl85858x3ng06qhcd-alloc-no-stdlib-2.0.4.tar.gz.drv"
    )
    odd = "/nix/store/xqqq8im19pic597cz80gshipmpghab98-alloc-stdlib-0.2.4.tar.gz.drv"
    outputs = {
        hit: ("/nix/store/j53pjz0hky6ykj41gam5jsm4rym07151-brotli-8.0.4.tar.gz",),
        miss: ("/nix/store/vmp7cly0rgdsj95hbwwczsriqbaba5sf-alloc-no-stdlib.tar.gz",),
        odd: (),
    }
    stores: list[tuple[tuple[str, ...], str]] = []

    def on_store(paths: object, store: str, **_kwargs: object) -> frozenset[str]:
        stores.append((tuple(paths), store))
        return frozenset(outputs[hit])

    monkeypatch.setattr(
        "lib.update.ci.warmup.query_drv_outputs", lambda drv, **_k: outputs[drv]
    )
    monkeypatch.setattr("lib.update.ci.warmup.paths_on_store", on_store)
    assert unpublished_source_fetches((hit, miss, odd, hit)) == (miss, odd)
    assert stores == [((*outputs[hit], *outputs[miss]), "https://gkze.cachix.org")]
    assert unpublished_source_fetches(()) == ()


def test_locally_fetchable_source_drvs_reads_derivation_system(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Only ``builtin`` and host-system FODs can be fetched on the planner."""
    darwin = "/nix/store/jpbxlpf8c33hg7aa0jzlrqdq5ia13zq7-brotli-8.0.4.tar.gz.drv"
    builtin = "/nix/store/26q4hp11s3rbgj9xnk1xhv7b56xzlhan-bootstrap-tools.tar.xz.drv"
    linux = "/nix/store/1hm4jss2dwiz4sii2z00qgwwasflxghy-source.drv"
    payload = {
        "version": 4,
        "derivations": {
            darwin.removeprefix("/nix/store/"): {"system": "aarch64-darwin"},
            builtin: {"system": "builtin"},
            linux.removeprefix("/nix/store/"): {"system": "x86_64-linux"},
            "not-a-store-path": {"system": "x86_64-linux"},
            "/nix/store/ffffffffffffffffffffffffffffffff-x.drv": "bad",
        },
    }
    calls: list[list[str]] = []

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    drvs = (darwin, builtin, linux)
    assert locally_fetchable_source_drvs(drvs, system="x86_64-linux", run=run) == (
        builtin,
        linux,
    )
    assert calls == [["nix", "derivation", "show", *drvs]]
    assert source_fetch_drv_systems((), run=run) == {}

    def run_bytes(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args, 0, json.dumps(payload).encode(), b"")

    assert (
        source_fetch_drv_systems((darwin,), run=run_bytes)[darwin] == "aarch64-darwin"
    )
    for stdout, code in (("boom", 1), ("not json", 0), ("[]", 0), ("{}", 0)):

        def broken(
            args: list[str], *, _out: str = stdout, _code: int = code, **_kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args, _code, _out, "")

        with pytest.raises(WarmupError):
            source_fetch_drv_systems((darwin,), run=broken)
        assert locally_fetchable_source_drvs(
            (darwin,), system="x86_64-linux", run=broken
        ) == (darwin,)
    assert "source FOD systems unknown" in capsys.readouterr().err


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
    lsp_types = "/nix/store/v11pzry7z3p0d8pvkhqiclf5h2q0knqb-rust_lsp-types-0.95.1.drv"
    merman = "/nix/store/s39xn12r5kn7jp29m7fnzcrqgx3qnhlb-rust_merman-0.8.0-alpha.5.drv"
    bmake = "/nix/store/fcy73hrwaaaaaaaaaaaaaaaaaaaaaaaa-bmake-20260313.tar.gz.drv"
    stdenv = "/nix/store/1gx3hvygaaaaaaaaaaaaaaaaaaaaaaaa-stdenv-darwin.drv"
    assert is_force_local_allowed_build(host)
    assert is_force_local_allowed_build(src)
    assert is_force_local_allowed_build(lsp_types)
    assert is_force_local_allowed_build(merman)
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
    trailing = (
        "these 1 derivations will be built:\n"
        "  /nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rustc\n"
        "  not-a-store-drv\n"
        "\n"
        "these 2 paths will be fetched:\n"
        "  /nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rustc\n"
    )
    assert (
        force_local_dry_run_builds(
            (host,),
            run=lambda args, **_kwargs: subprocess.CompletedProcess(
                args, 0, "", trailing
            ),
        )
        == ()
    )
    assert_force_local_dry_run(
        (host,),
        run=lambda args, **_kwargs: subprocess.CompletedProcess(args, 0, "", ok),
    )
    third_party = (
        "these 4 derivations will be built:\n"
        f"  {host}\n"
        f"  {src}\n"
        f"  {lsp_types}\n"
        f"  {merman}\n"
    )
    assert_force_local_dry_run(
        (host,),
        run=lambda args, **_kwargs: subprocess.CompletedProcess(
            args, 0, "", third_party
        ),
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


def test_linux_builder_image_command_matches_arm_validate_drv() -> None:
    """38137325241 streamed this exact installable before the 50-wbb abort."""
    command = (
        "nix build --no-link --keep-going --fallback -L "
        "'/nix/store/6xczfl5hgl4yb7p9p997l6hmlwgpfhdj-nixos-disk-image.drv^*'"
    )
    assert is_linux_builder_image_command(command)
    assert is_linux_builder_image_command(
        "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-nixos-disk-image.drv^*"
    )
    assert not is_linux_builder_image_command(
        "nix build path:.#checks.aarch64-darwin.root-closures"
    )
    assert not is_linux_builder_image_command(
        "nix build /nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_settings-0.1.0.drv"
    )
    fifty = "these 50 derivations will be built:"
    assert warmup_fatal_line(fifty) is not None
    assert warmup_fatal_line(fifty, keep_going=True) is None
    raise_if_warmup_fatal(fifty, keep_going=True)
    output = "/nix/store/wy15yizd3i7z6bmg3llr43a2pflg9yyb-nixos-disk-image"
    assert is_linux_builder_output_store_path(output)
    assert not is_linux_builder_output_store_path(
        "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-darwin-system-26.11.57a3171"
    )
    assert linux_builder_substitute_miss_reason(fifty) == (
        "linux-builder image missing from binary cache"
    )
    assert (
        linux_builder_substitute_miss_reason(
            "error: Cannot build '/nix/store/wgyazfmqvg0z06ydkhgpnrkgyrzbqawj-etc-fstab.drv'."
        )
        == "linux-builder image missing from binary cache"
    )
    assert (
        linux_builder_substitute_miss_reason("these 50 paths will be fetched:") is None
    )


def test_dry_run_will_be_built_count_parses_nix_dry_run_lines() -> None:
    """Hosted Darwin logs are the count oracle; this workspace cannot nix-build them."""
    drv = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-linear-cli-jsr-manifest.tsv.drv"

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        text = args[-1]
        if "linear-cli" in text:
            return subprocess.CompletedProcess(
                args, 0, "", "these 1297 derivations will be built:\n"
            )
        if "libiconv" in text:
            return subprocess.CompletedProcess(
                args, 0, "", "this derivation will be built:\n"
            )
        return subprocess.CompletedProcess(
            args, 0, "", "these 13 paths will be fetched:\n"
        )

    assert dry_run_will_be_built_count(drv, run=run) == 1297
    assert (
        dry_run_will_be_built_count(
            "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-libiconv-1.drv", run=run
        )
        == 1
    )
    assert (
        dry_run_will_be_built_count(
            "/nix/store/cccccccccccccccccccccccccccccccc-already-cached.drv", run=run
        )
        == 0
    )
    with pytest.raises(WarmupError, match="failed to dry-run cascade"):
        dry_run_will_be_built_count(
            drv,
            run=lambda args, **_kwargs: subprocess.CompletedProcess(
                args, 1, "", "no drv"
            ),
        )


def test_filter_cascade_realize_drvs_drops_umbrellas_over_ceiling() -> None:
    """38191092497: skip linear-cli-jsr-manifest.tsv, keep the 1-3 drv FODs."""
    leaf = "/nix/store/dddddddddddddddddddddddddddddddd-libiconv-1.drv"
    umbrella = (
        "/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-linear-cli-jsr-manifest.tsv.drv"
    )

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        text = args[-1]
        if "linear-cli" in text:
            return subprocess.CompletedProcess(
                args, 0, "", "these 1297 derivations will be built:\n"
            )
        return subprocess.CompletedProcess(
            args, 0, "", "these 2 derivations will be built:\n"
        )

    assert filter_cascade_realize_drvs((leaf, umbrella, leaf), run=run) == (leaf,)
    with pytest.raises(WarmupError, match="at least 1"):
        filter_cascade_realize_drvs((leaf,), run=run, limit=0)
