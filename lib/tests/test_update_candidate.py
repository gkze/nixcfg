"""Portable candidate behavior through real Git workspaces and updater phases."""

import json
import subprocess
import traceback
from dataclasses import dataclass
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lib.nix.models.flake_lock import FlakeLockNode
from lib.nix.models.sources import HashCollection, SourceEntry
from lib.tests._run_updates_helpers import drain_events, make_run_plan
from lib.tests._update_workspace_helpers import init_update_workspace_repo
from lib.tests._updater_helpers import load_repo_module_for_test
from lib.update import cli, source_runner
from lib.update.candidate import Candidate, Preparation, ResolvedVersion, git
from lib.update.ci import candidate as pipeline
from lib.update.ci.coverage import (
    ROOT_OUT_PATHS_NAME,
    CoverageError,
    dump_job_results,
    required_coverage_jobs,
    write_root_out_path_cache,
)
from lib.update.ci.warmup import WarmupFatalError
from lib.update.derivation_validation import (
    DerivationValidation,
    DerivationValidationFailure,
    ValidationCommandFinished,
    ValidationCommandOutput,
    ValidationCommandStarted,
    ValidationIncompleteError,
)
from lib.update.persistence import IsolatedUpdateWorkspace
from lib.update.ui_consumer import consume_events
from lib.update.updaters import Updater, VersionInfo
from lib.update.updaters.metadata import GitHubReleaseMetadata, MappingMetadata

_HASH = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="


@dataclass(frozen=True)
class ExampleMetadata(MappingMetadata):
    """Representative nested updater metadata, including non-mapping state."""

    hashes: tuple[str, ...]
    source: SourceEntry


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {"commit": "abc", "nested": [1, True]},
        GitHubReleaseMetadata(tag="v2"),
        ExampleMetadata(hashes=(_HASH,), source=SourceEntry(version="2", hashes={})),
    ],
)
def test_resolution_survives_json_without_losing_metadata(metadata) -> None:
    """Typed/custom metadata remains usable by the original updater."""
    info = VersionInfo(version="2", metadata=metadata)
    saved = ResolvedVersion.capture(info)
    loaded = ResolvedVersion.model_validate_json(saved.model_dump_json())
    assert loaded.restore() == info


def test_resolution_rejects_executable_or_unknown_metadata() -> None:
    """Artifact metadata cannot request arbitrary Python deserialization."""
    with pytest.raises(ValueError, match="invalid-json-value"):
        ResolvedVersion.capture(VersionInfo(version="2", metadata=object()))
    with pytest.raises(ValueError, match="Unknown updater metadata"):
        ResolvedVersion(version="2", metadata_type="os.system").restore()


@pytest.mark.parametrize(
    ("module_path", "class_name", "extra"),
    [
        (
            "packages/emdash/updater.py",
            "EmdashSourceMetadata",
            {
                "shell_env_capture_path": "src/env.ts",
                "toolchain": {
                    "node_engine": ">=24.0.0",
                    "nodejs_attr": "nodejs_24",
                    "nodejs_version": "24.20.0",
                    "package_manager": "pnpm@10.28.2",
                    "pnpm_engine": ">=10.28.0",
                    "pnpm_attr": "pnpm_10",
                    "pnpm_version": "10.34.5",
                },
            },
        ),
        ("packages/mux/updater.py", "MuxSourceMetadata", {"bun_version": "1.3.0"}),
        (
            "lib/update/electron_manifest.py",
            "ElectronManifestMetadata",
            {"manifest_path": "package.json", "manifest_version": "1.2.3"},
        ),
    ],
)
def test_package_metadata_survives_native_candidate_handoff(
    module_path, class_name, extra
) -> None:
    """Real dynamic metadata must resolve nested model types at runtime."""
    module = load_repo_module_for_test(module_path, prefix="candidate_metadata")
    saved = ResolvedVersion(
        version="1.2.3",
        metadata_type=f"{module.__name__}.{class_name}",
        metadata={
            "node": {"locked": {"type": "github", "rev": "a" * 40, "narHash": _HASH}},
            "commit": "a" * 40,
            "electron_version": "40.10.2",
            **extra,
        },
    )
    restored = saved.restore()
    captured = ResolvedVersion.capture(restored)
    assert ResolvedVersion.model_validate_json(
        captured.model_dump_json()
    ).restore() == (restored)
    assert isinstance(restored.metadata, MappingMetadata)
    node = restored.metadata["node"]
    assert isinstance(node, FlakeLockNode)
    assert node.locked is not None
    assert node.locked.rev == "a" * 40


@pytest.fixture
def prepared_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Path, list[str], dict[str, str | bool]]:
    """Keep Git, candidate isolation, scheduling and persistence real."""
    root = tmp_path / "live"
    initial = SourceEntry(version="1", hashes={}).model_dump_json(by_alias=True)
    init_update_workspace_repo(
        root,
        tracked_files={
            "packages/example/sources.json": initial,
            "packages/example/updater.py": "# fixture\n",
        },
    )
    operations: list[str] = []
    state = {"system": "aarch64-darwin", "fail": False}

    class Example(Updater):
        name = "example"

        async def fetch_latest(self, session, *, context):
            _ = session, context
            operations.append("resolve")
            return VersionInfo(version="2", metadata=GitHubReleaseMetadata(tag="v2"))

        async def fetch_hashes(self, info, session, *, context, emit):
            _ = session, emit
            context.hashes_fully_computed = False
            assert info.metadata == GitHubReleaseMetadata(tag="v2")
            operations.append(f"hash:{state['system']}")
            if state.get("darwin_only") and state["system"] != "aarch64-darwin":
                return {"aarch64-darwin": _HASH}
            if state["fail"]:
                msg = "upstream packaging changed"
                raise RuntimeError(msg)
            return {str(state["system"]): _HASH}

    registry = {"example": Example}

    def plan(_opts):
        plan = make_run_plan(source_names=("example",), dry_run=True)
        plan.sources.entries["example"] = SourceEntry.model_validate_json(
            Path("packages/example/sources.json").read_bytes()
        )
        return plan

    monkeypatch.setattr(cli, "get_repo_root", lambda: root)
    monkeypatch.setattr(cli, "_build_run_plan", plan)
    monkeypatch.setattr(cli, "_get_updaters", lambda: registry)
    monkeypatch.setattr(source_runner, "_get_updaters", lambda: registry)
    monkeypatch.setattr(cli, "consume_events", drain_events)
    monkeypatch.setattr(cli, "_handle_required_tool_check", lambda _opts: None)
    monkeypatch.setattr(pipeline, "get_current_nix_platform", lambda: state["system"])
    monkeypatch.setattr(pipeline, "get_repo_root", lambda: root)
    monkeypatch.setattr(pipeline, "ensure_updaters_loaded", lambda: registry)
    return root, operations, state


@pytest.mark.parametrize("darwin_only", [False, True])
def test_native_stages_pin_versions_preserve_hashes_and_never_promote(
    prepared_run,
    darwin_only: bool,
) -> None:
    """Each runner reconstructs the candidate from the untouched original tree."""
    root, operations, state = prepared_run
    state["darwin_only"] = darwin_only
    previous = None
    for system in pipeline.supported_systems():
        state["system"] = system
        candidate, status = pipeline.prepare_candidate(("example",), previous=previous)
        assert status == 0
        assert candidate.prepared
        previous = Candidate.model_validate_json(candidate.model_dump_json())
        assert not git(root, "status", "--porcelain")
    assert operations.count("resolve") == 1
    assert len(operations) == 4
    assert previous is not None
    with IsolatedUpdateWorkspace(root) as workspace:
        previous.apply(workspace.root)
        result = SourceEntry.model_validate_json(
            (workspace.root / "packages/example/sources.json").read_bytes()
        )
        assert result.version == "2"
        assert set(result.hashes.mapping) == (
            {"aarch64-darwin"} if darwin_only else set(pipeline.supported_systems())
        )


def test_preparation_streams_progress_separately_from_json(
    prepared_run, monkeypatch, capsys
) -> None:
    """The CI consumer gets useful progress and a parseable result separately."""
    monkeypatch.setattr(cli, "consume_events", consume_events)
    _, status = pipeline.prepare_candidate(("example",))
    captured = capsys.readouterr()
    assert status == 0
    assert json.loads(captured.out)["success"] is True
    assert "Phase" in captured.err
    assert "example" in captured.err


def test_failed_preparation_retains_resolution_and_is_not_a_completed_platform(
    prepared_run,
) -> None:
    """Repair evidence survives a domain failure, without certifying or promoting it."""
    root, operations, state = prepared_run
    state["fail"] = True
    candidate, status = pipeline.prepare_candidate(("example",))
    assert status == 1
    assert not candidate.prepared
    assert candidate.systems == ()
    assert candidate.resolutions["example"].version == "2"
    assert operations == ["resolve", "hash:aarch64-darwin"]
    assert not git(root, "status", "--porcelain")
    with pytest.raises(ValueError, match="failed preparation"):
        Preparation(system="x86_64-linux", targets=("example",), previous=candidate)


def test_candidate_identity_and_stage_admission(prepared_run) -> None:
    """Reject corruption, different baselines, duplicate stages and retargeting."""
    root, _, _ = prepared_run
    candidate, _ = pipeline.prepare_candidate(("example",))
    for updates, message in [
        ({"base_tree": "0" * 40}, "baseline"),
        ({"tree": "0" * 40}, "recorded tree"),
    ]:
        with (
            IsolatedUpdateWorkspace(root) as workspace,
            pytest.raises(ValueError, match=message),
        ):
            candidate.model_copy(update=updates).apply(workspace.root)
    with pytest.raises(ValueError, match="already prepared"):
        Preparation(system="aarch64-darwin", targets=("example",), previous=candidate)
    with pytest.raises(ValueError, match="target selection"):
        Preparation(system="x86_64-linux", targets=("other",), previous=candidate)


@pytest.mark.parametrize("validate_all_packages", [False, True])
@pytest.mark.parametrize("no_change", [False, True])
def test_repair_inventory_blocks_unselected_package_failure(
    prepared_run, monkeypatch, validate_all_packages, no_change
) -> None:
    """Repair admission includes held declarations even without update changes."""
    _, operations, state = prepared_run
    registry = pipeline.ensure_updaters_loaded()

    class Held(Updater):
        name = "held"
        bulk_update_hold = "Keep the pinned release"
        derivation_validations = (
            DerivationValidation(
                installable=".#pkgs.{system}.held",
                systems=pipeline.supported_systems(),
                mode="build",
            ),
        )

    registry["held"] = Held
    candidate = None
    for system in pipeline.supported_systems():
        state["system"] = system
        candidate, status = pipeline.prepare_candidate(
            ("example",),
            previous=candidate,
            validate_all_packages=validate_all_packages if candidate is None else False,
        )
        assert status == 0
        candidate = Candidate.model_validate_json(candidate.model_dump_json())
        assert candidate.sources == ("example",)
        assert candidate.targets == ("example",)
        assert candidate.validate_all_packages == validate_all_packages
    assert candidate is not None
    assert operations.count("resolve") == 1
    if no_change:
        candidate = candidate.model_copy(
            update={
                "tree": candidate.base_tree,
                "patch": b"",
                "sources": (),
            }
        )
    checked = []

    def validate_requests(requests, **_kwargs):
        checked.extend(requests)
        return tuple(
            DerivationValidationFailure(
                request.source, request.installable, "broken repair"
            )
            for request in requests
        )

    monkeypatch.setattr(
        pipeline.validation, "get_current_nix_platform", lambda: state["system"]
    )
    monkeypatch.setattr(
        pipeline.validation, "validate_derivation_requests", validate_requests
    )
    monkeypatch.setattr(
        pipeline.validation, "validate_root_closures", lambda **_kwargs: ()
    )
    reports = []
    for system in pipeline.supported_systems():
        state["system"] = system
        report = pipeline.validate_candidate(candidate)
        reports.append(report)
        assert report.validate_all_packages == validate_all_packages
        assert bool(report.failures) == validate_all_packages
    if validate_all_packages:
        assert [request.installable for request in checked] == [
            f".#pkgs.{system}.held" for system in pipeline.supported_systems()
        ]
        with pytest.raises(ValueError, match="Validation reports"):
            pipeline.certified_patch(candidate, reports)
        targeted_reports = [
            report.model_copy(
                update={
                    "failures": (),
                    "validate_all_packages": False,
                }
            )
            for report in reports
        ]
        with pytest.raises(ValueError, match="Validation reports"):
            pipeline.certified_patch(candidate, targeted_reports)
    else:
        assert checked == []
        assert pipeline.certified_patch(candidate, reports) == candidate.patch


def test_dependent_resolution_is_recomputed() -> None:
    """A companion consumes current prerequisite outputs instead of old metadata."""
    preparation = Preparation(system="aarch64-darwin", targets=())
    preparation.dependent_sources.add("companion")
    preparation.record("companion", VersionInfo(version="2"))
    preparation.record("absent", None)
    assert preparation.resolved("companion") is None
    assert preparation.resolved("absent") is None
    assert preparation.resolutions == {}


def test_native_validation_and_certification(
    prepared_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only matching successful evidence from every builder authorizes a patch."""
    root, _, state = prepared_run
    candidate = None
    for system in pipeline.supported_systems():
        state["system"] = system
        candidate, _ = pipeline.prepare_candidate(("example",), previous=candidate)
    assert candidate is not None
    roots: list[tuple[str, ...]] = []

    def validate_sources(names, *, flake_root, **_kwargs):
        assert names == ("example",)
        assert (
            json.loads((flake_root / "packages/example/sources.json").read_text())[
                "version"
            ]
            == "2"
        )
        return ()

    def validate_roots(*, systems, include_dependencies, print_build_logs, **_kwargs):
        assert include_dependencies
        # Hosted macos-15 died when -L streamed 4000+ derivation logs.
        # Dedicated Darwin builders still want the logs.
        assert print_build_logs is (not pipeline.jobs.is_hosted_darwin_runner())
        order.append("roots")
        roots.append(systems)
        return ()

    order: list[str] = []
    monkeypatch.setattr(
        pipeline.jobs, "reclaim_hosted_store", lambda: order.append("reclaim")
    )
    monkeypatch.setattr(pipeline.validation, "validate_derivations", validate_sources)
    monkeypatch.setattr(pipeline.validation, "validate_root_closures", validate_roots)
    reports = []
    for system in pipeline.supported_systems():
        state["system"] = system
        reports.append(pipeline.validate_candidate(candidate))
    assert roots == [(system,) for system in pipeline.supported_systems()]
    assert order == ["reclaim", "roots"] * len(pipeline.supported_systems())
    assert pipeline.certified_patch(candidate, reports) == candidate.patch
    assert not git(root, "status", "--porcelain")
    for bad in (
        reports[:-1],
        [*reports, reports[0]],
        [reports[0].model_copy(update={"tree": "wrong"}), *reports[1:]],
        [
            reports[0].model_copy(
                update={
                    "failures": (
                        DerivationValidationFailure(
                            "example", ".#example", "build failed"
                        ),
                    )
                }
            ),
            *reports[1:],
        ],
    ):
        with pytest.raises(ValueError, match="Validation reports"):
            pipeline.certified_patch(candidate, bad)
    with pytest.raises(ValueError, match="every configured system"):
        pipeline.certified_patch(candidate.model_copy(update={"systems": ()}), reports)


def _candidate_for_scope(prepared_run) -> Candidate:
    root, _, state = prepared_run
    tree = git(root, "rev-parse", "HEAD^{tree}").decode().strip()
    state["system"] = "aarch64-darwin"
    return Candidate(
        base_tree=tree,
        tree=tree,
        targets=(),
        sources=(),
        systems=pipeline.supported_systems(),
        resolutions={},
        prepared=True,
        patch=b"",
    )


def test_cache_root_dependencies_skips_complete_candidate_and_own_roots(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """VM warmup may use the Darwin-prepare candidate and must not certify."""
    root, _, _state = prepared_run
    tree = git(root, "rev-parse", "HEAD^{tree}").decode().strip()
    incomplete = Candidate(
        base_tree=tree,
        tree=tree,
        targets=(),
        sources=(),
        systems=("aarch64-darwin",),
        resolutions={},
        prepared=True,
        patch=b"",
    )
    seen: list[dict[str, object]] = []

    def roots(**kwargs: object) -> tuple[()]:
        seen.append(kwargs)
        return ()

    monkeypatch.setattr(pipeline.validation, "validate_root_closures", roots)
    report = pipeline.cache_root_dependencies(incomplete)
    assert report.tree == tree
    assert report.failures == ()
    assert seen[0]["include_dependencies"] is True
    assert seen[0]["dependencies_only"] is True
    assert seen[0]["timeout"] == pipeline._CLOSURE_DISCOVERY_TIMEOUT_SECONDS
    assert seen[0]["build_timeout"] == pipeline._ROOT_DEPS_BUILD_TIMEOUT_SECONDS
    failed = incomplete.model_copy(update={"prepared": False})
    with pytest.raises(ValueError, match="failed preparation"):
        pipeline.cache_root_dependencies(failed)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(incomplete.model_dump_json())
    result = CliRunner().invoke(
        pipeline.app,
        [
            "cache-root-deps",
            "--candidate",
            str(candidate_path),
            "--output",
            str(tmp_path / "cache-root-deps.json"),
        ],
    )
    assert result.exit_code == 0
    receipt = json.loads((tmp_path / "cache-root-deps.json").read_text())
    assert receipt["tree"] == tree
    assert "gates" not in receipt


def test_cache_root_deps_cli_propagates_dependency_failures(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed VM cache must fail the job without becoming a validation report."""
    candidate = _candidate_for_scope(prepared_run)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(candidate.model_dump_json())
    monkeypatch.setattr(
        pipeline.validation,
        "validate_root_closures",
        lambda **_kwargs: (
            DerivationValidationFailure(
                source="root-closures",
                installable="/nix/store/vm.drv^*",
                message="VM build failed",
            ),
        ),
    )
    result = CliRunner().invoke(
        pipeline.app,
        [
            "cache-root-deps",
            "--candidate",
            str(candidate_path),
            "--output",
            str(tmp_path / "cache-root-deps.json"),
        ],
    )
    assert result.exit_code == 1
    receipt = json.loads((tmp_path / "cache-root-deps.json").read_text())
    assert receipt["failures"][0]["message"] == "VM build failed"


def test_validation_scopes_split_packages_from_closures(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Package shards do not GC; closure jobs GC before the root-closure fetch."""
    candidate = _candidate_for_scope(prepared_run)
    order: list[str] = []
    seen: list[dict[str, object]] = []
    monkeypatch.setattr(
        pipeline.jobs, "reclaim_hosted_store", lambda: order.append("reclaim")
    )
    monkeypatch.setattr(
        pipeline.validation,
        "validate_derivations",
        lambda *_args, **_kwargs: order.append("packages") or (),
    )

    def roots(**kwargs):
        order.append("roots")
        seen.append(kwargs)
        return ()

    monkeypatch.setattr(pipeline.validation, "validate_root_closures", roots)
    packages = pipeline.validate_candidate(candidate, scope="packages")
    assert packages.gates == ("packages",)
    assert order == ["packages"]
    closures = pipeline.validate_candidate(
        candidate,
        scope="closures",
        closure_budget_seconds=pipeline.HOSTED_DARWIN_CLOSURE_BUILD_BUDGET_SECONDS,
    )
    assert closures.gates == ("closures",)
    assert order == ["packages", "reclaim", "roots"]
    assert seen[0]["build_timeout"] == (
        pipeline.HOSTED_DARWIN_CLOSURE_BUILD_BUDGET_SECONDS
    )
    assert seen[0]["timeout"] == pipeline._CLOSURE_DISCOVERY_TIMEOUT_SECONDS
    others = [
        pipeline.ValidationReport(
            tree=candidate.tree,
            system=system,
            failures=(),
        )
        for system in pipeline.supported_systems()
        if system != packages.system
    ]
    assert (
        pipeline.certified_patch(candidate, [packages, closures, *others])
        == candidate.patch
    )
    with pytest.raises(ValueError, match="Validation reports"):
        pipeline.certified_patch(candidate, [packages, *others])
    with pytest.raises(ValueError, match="Validation reports"):
        pipeline.certified_patch(candidate, [packages, packages, closures, *others])
    with pytest.raises(ValueError, match="Unknown validation scope"):
        pipeline.validate_candidate(candidate, scope="neither")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="positive"):
        pipeline.validate_candidate(candidate, closure_budget_seconds=0)
    with pytest.raises(ValueError, match="positive"):
        pipeline.validate_candidate(candidate, closure_budget_seconds=float("nan"))
    with pytest.raises(TypeError, match="positive"):
        pipeline._require_closure_budget("18000")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="positive"):
        pipeline._require_closure_budget(seconds=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Named closure roots"):
        pipeline.validate_candidate(
            candidate, scope="packages", closure_roots=("darwin-argus",)
        )
    with pytest.raises(ValueError, match="nonempty root list"):
        pipeline.validate_candidate(candidate, scope="closure-shard", closure_roots=())


def test_rust_warmup_scope_realizes_slot_and_rejects_bad_args(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rust-warmup is not certify evidence; packages no longer serialize it."""
    candidate = _candidate_for_scope(prepared_run)
    order: list[str] = []
    monkeypatch.setattr(
        pipeline.validation,
        "validate_derivations",
        lambda *_args, **_kwargs: (),
    )

    realized: list[object] = []

    def warmup_realize(paths: object, *_args: object, **_kwargs: object) -> tuple[()]:
        order.append("warmup")
        realized.append(paths)
        return ()

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", warmup_realize)
    monkeypatch.setattr(pipeline, "import_warmup_drvs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda _path: False)
    from lib.update.ci.warmup import (
        RootWarmupStats,
        ShardLocalBuildReport,
        WarmupPlan,
        write_warmup_plan,
    )

    cargo = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-cargo-package-sha2-0.10.9"
    cargo_drv = f"{cargo}.drv"
    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(cargo,),
            rustLayers=((cargo,),),
            outputDrvs={cargo: cargo_drv},
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=1, missing=1, warmup=1, remaining=0
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
        ),
    )
    rust = pipeline.validate_candidate(
        candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=0
    )
    assert rust.gates == ()
    assert order == ["warmup"]
    assert realized == [(cargo_drv,)]
    monkeypatch.setenv("NIXCFG_CANARY", "true")
    default_skip = pipeline.validate_candidate(
        candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=1
    )
    assert default_skip.failures == ()
    assert order == ["warmup"]
    monkeypatch.setenv("NIXCFG_CANARY_SLOTS", "1")
    skipped = pipeline.validate_candidate(
        candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=0
    )
    assert skipped.failures == ()
    assert order == ["warmup"]
    monkeypatch.delenv("NIXCFG_CANARY_SLOTS")
    monkeypatch.setenv("NIXCFG_CANARY_CRATES", "extension_host")
    filtered = pipeline.validate_candidate(
        candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=0
    )
    assert filtered.failures == ()
    assert order == ["warmup"]
    monkeypatch.delenv("NIXCFG_CANARY")
    monkeypatch.delenv("NIXCFG_CANARY_CRATES")
    with pytest.raises(ValueError, match="warmup plan and slot"):
        pipeline.validate_candidate(candidate, scope="rust-warmup")
    with pytest.raises(ValueError, match="warmup slots are only valid"):
        pipeline.validate_candidate(candidate, scope="packages", warmup_slot=0)


def test_rust_warmup_defers_leaf_packages_from_others(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1268 slots 3/4: do not nix-build goose-cli / vendor-cargo-deps in others."""
    candidate = _candidate_for_scope(prepared_run)
    monkeypatch.setattr(
        pipeline.validation,
        "validate_derivations",
        lambda *_args, **_kwargs: (),
    )
    realized: list[object] = []

    def warmup_realize(paths: object, *_args: object, **_kwargs: object) -> tuple[()]:
        realized.append(paths)
        return ()

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", warmup_realize)
    monkeypatch.setattr(pipeline, "import_warmup_drvs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda _path: False)
    from lib.update.ci.warmup import (
        RootWarmupStats,
        ShardLocalBuildReport,
        WarmupPlan,
        write_warmup_plan,
    )

    cargo = "/nix/store/mynvanc4j9sgag5gdd8ir1s3p0pq3rgg-cargo-package-sha2-0.10.9"
    goose = "/nix/store/rhak437lr7f3zwfgf2qrb40ryw7mx98i-goose-cli-1.51.0"
    vendor = "/nix/store/0jmdym99ibn8rpv9696zvyrkr0mjyvjv-vendor-cargo-deps"
    cargo_drv = f"{cargo}.drv"
    goose_drv = f"{goose}.drv"
    vendor_drv = f"{vendor}.drv"
    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(cargo, goose, vendor),
            rustLayers=((cargo, goose, vendor),),
            outputDrvs={cargo: cargo_drv, goose: goose_drv, vendor: vendor_drv},
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=3, missing=3, warmup=3, remaining=0
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
        ),
    )
    report = pipeline.validate_candidate(
        candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=0
    )
    assert report.failures == ()
    assert realized == [(cargo_drv,)]
    assert goose_drv not in realized[0]
    assert vendor_drv not in realized[0]


def test_rust_warmup_settings_family_force_locals_content_cluster(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1269 slot 3: do not compile rust_settings against Cachix settings_content."""
    candidate = _candidate_for_scope(prepared_run)
    monkeypatch.setattr(
        pipeline.validation,
        "validate_derivations",
        lambda *_args, **_kwargs: (),
    )
    realized: list[tuple[object, bool, bool, bool]] = []

    def warmup_realize(paths: object, *_args: object, **kwargs: object) -> tuple[()]:
        realized.append((
            paths,
            bool(kwargs.get("print_build_logs")),
            bool(kwargs.get("force_local")),
            bool(kwargs.get("substitute_only")),
        ))
        return ()

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", warmup_realize)
    monkeypatch.setattr(pipeline, "import_warmup_drvs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda _path: False)
    monkeypatch.setattr(pipeline, "compiler_input_drvs", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(pipeline, "assert_force_local_dry_run", lambda *_a, **_k: None)
    from lib.update.ci.warmup import (
        RootWarmupStats,
        ShardLocalBuildReport,
        WarmupPlan,
        write_warmup_plan,
    )

    settings = "/nix/store/2y7vj1wq5nz030asgn7rhipbcx5aya89-rust_settings-0.1.0"
    content = "/nix/store/ma14flyg1v5b4vhinb2l0klw1xmdg9nz-rust_settings_content-0.1.0"
    other = "/nix/store/otheraaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_gpui-0.1.0"
    settings_drv = f"{settings}.drv"
    content_drv = f"{content}.drv"
    other_drv = f"{other}.drv"
    monkeypatch.setattr(
        pipeline,
        "rust_crate_input_drvs",
        lambda _parents, _crates: (content_drv,),
    )
    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(other, content, settings),
            rustLayers=((other,), (content,), (settings,)),
            outputDrvs={
                other: other_drv,
                content: content_drv,
                settings: settings_drv,
            },
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=3, missing=3, warmup=3, remaining=0
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
        ),
    )
    report = pipeline.validate_candidate(
        candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=0
    )
    assert report.failures == ()
    force_local = [paths for paths, _logs, force, _sub in realized if force]
    assert force_local == [(content_drv, settings_drv)]
    others = [paths for paths, _logs, force, _sub in realized if not force]
    assert others == [(other_drv,)]


def test_rust_warmup_settings_family_builds_patchutils_helper(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1270 canary: do not --max-jobs 0 the 1-drv patchutils helper."""
    candidate = _candidate_for_scope(prepared_run)
    monkeypatch.setattr(
        pipeline.validation,
        "validate_derivations",
        lambda *_args, **_kwargs: (),
    )
    realized: list[tuple[object, bool, bool, bool]] = []

    def warmup_realize(paths: object, *_args: object, **kwargs: object) -> tuple[()]:
        realized.append((
            paths,
            bool(kwargs.get("print_build_logs")),
            bool(kwargs.get("force_local")),
            bool(kwargs.get("substitute_only")),
        ))
        return ()

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", warmup_realize)
    monkeypatch.setattr(pipeline, "import_warmup_drvs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda _path: False)
    monkeypatch.setattr(pipeline, "assert_force_local_dry_run", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "rust_crate_input_drvs", lambda *_a, **_k: ())
    rustc_drv = "/nix/store/xbq69m0caaaaaaaaaaaaaaaaaaaaaaaa-rustc-1.98.1.drv"
    patchutils_drv = "/nix/store/m6399k05aaqiz3mx4cdpkdqr4hp05kmj-patchutils-0.3.3.drv"
    monkeypatch.setattr(
        pipeline,
        "compiler_input_drvs",
        lambda *_args, **_kwargs: (rustc_drv, patchutils_drv),
    )
    from lib.update.ci.warmup import (
        RootWarmupStats,
        ShardLocalBuildReport,
        WarmupPlan,
        write_warmup_plan,
    )

    settings = "/nix/store/2y7vj1wq5nz030asgn7rhipbcx5aya89-rust_settings-0.1.0"
    content = "/nix/store/ma14flyg1v5b4vhinb2l0klw1xmdg9nz-rust_settings_content-0.1.0"
    settings_drv = f"{settings}.drv"
    content_drv = f"{content}.drv"
    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(content, settings),
            rustLayers=((content,), (settings,)),
            outputDrvs={content: content_drv, settings: settings_drv},
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=2, missing=2, warmup=2, remaining=0
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
        ),
    )
    report = pipeline.validate_candidate(
        candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=0
    )
    assert report.failures == ()
    assert realized == [
        ((rustc_drv,), False, False, True),
        ((patchutils_drv,), False, False, False),
        ((content_drv, settings_drv), True, True, False),
    ]


def test_retry_substitute_only_helpers_skips_toolchain_and_rust() -> None:
    """#1270: retry unknown Unix helpers; refuse rustc and rust_*."""
    cpio = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-cpio-2.15.drv"
    unknown = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-diffutils-3.12.drv"
    rustc = "/nix/store/cccccccccccccccccccccccccccccccc-rustc-1.98.1.drv"
    settings = "/nix/store/dddddddddddddddddddddddddddddddd-rust_settings-0.1.0.drv"
    num_cpus = "/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-rust_num_cpus-1.16.0.drv"
    assert pipeline._retry_substitute_only_helpers((
        DerivationValidationFailure(
            source="root-warmup",
            installable=f"{cpio}^*",
            message="error: Cannot build '/nix/store/...-cpio-2.15.drv'.",
        ),
        DerivationValidationFailure(
            source="root-warmup",
            installable=f"{unknown}^*",
            message="error: Cannot build '/nix/store/...-diffutils-3.12.drv'.",
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
            installable=f"{num_cpus}^*",
            message="error: Cannot build '/nix/store/...-rust_num_cpus-1.16.0.drv'.",
        ),
        DerivationValidationFailure(
            source="root-warmup",
            installable=f"{unknown}^*",
            message="error: hash mismatch in fixed-output derivation",
        ),
    )) == (cpio, unknown)


def test_rust_warmup_retries_unknown_1drv_helper_after_max_jobs_zero(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown helper cache miss must retry locally; rustc miss must not."""
    candidate = _candidate_for_scope(prepared_run)
    monkeypatch.setattr(
        pipeline.validation,
        "validate_derivations",
        lambda *_args, **_kwargs: (),
    )
    realized: list[tuple[object, bool, bool, bool]] = []
    unknown_drv = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-diffutils-3.12.drv"
    rustc_drv = "/nix/store/xbq69m0caaaaaaaaaaaaaaaaaaaaaaaa-rustc-1.98.1.drv"
    unknown_fail = DerivationValidationFailure(
        source="root-warmup",
        installable=f"{unknown_drv}^*",
        message="error: Cannot build '/nix/store/...-diffutils-3.12.drv'.",
    )

    def warmup_realize(paths: object, *_args: object, **kwargs: object) -> object:
        realized.append((
            paths,
            bool(kwargs.get("print_build_logs")),
            bool(kwargs.get("force_local")),
            bool(kwargs.get("substitute_only")),
        ))
        if kwargs.get("substitute_only"):
            return (unknown_fail,)
        return ()

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", warmup_realize)
    monkeypatch.setattr(pipeline, "import_warmup_drvs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda _path: False)
    monkeypatch.setattr(pipeline, "assert_force_local_dry_run", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "rust_crate_input_drvs", lambda *_a, **_k: ())
    monkeypatch.setattr(
        pipeline,
        "compiler_input_drvs",
        lambda *_args, **_kwargs: (rustc_drv, unknown_drv),
    )
    from lib.update.ci.warmup import (
        RootWarmupStats,
        ShardLocalBuildReport,
        WarmupPlan,
        write_warmup_plan,
    )

    settings = "/nix/store/2y7vj1wq5nz030asgn7rhipbcx5aya89-rust_settings-0.1.0"
    content = "/nix/store/ma14flyg1v5b4vhinb2l0klw1xmdg9nz-rust_settings_content-0.1.0"
    settings_drv = f"{settings}.drv"
    content_drv = f"{content}.drv"
    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(content, settings),
            rustLayers=((content,), (settings,)),
            outputDrvs={content: content_drv, settings: settings_drv},
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=2, missing=2, warmup=2, remaining=0
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
        ),
    )
    report = pipeline.validate_candidate(
        candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=0
    )
    assert report.failures == ()
    assert realized == [
        ((rustc_drv, unknown_drv), False, False, True),
        ((unknown_drv,), False, False, False),
        ((content_drv, settings_drv), True, True, False),
    ]


def test_rust_warmup_refuses_force_local_after_rustc_substitute_miss(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rustc --max-jobs 0 miss must keep the failure and skip force-local."""
    candidate = _candidate_for_scope(prepared_run)
    monkeypatch.setattr(
        pipeline.validation,
        "validate_derivations",
        lambda *_args, **_kwargs: (),
    )
    realized: list[tuple[object, bool, bool, bool]] = []
    rustc_drv = "/nix/store/xbq69m0caaaaaaaaaaaaaaaaaaaaaaaa-rustc-1.98.1.drv"
    unknown_drv = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-diffutils-3.12.drv"
    rustc_fail = DerivationValidationFailure(
        source="root-warmup",
        installable=f"{rustc_drv}^*",
        message="error: Cannot build '/nix/store/...-rustc-1.98.1.drv'.",
    )
    unknown_fail = DerivationValidationFailure(
        source="root-warmup",
        installable=f"{unknown_drv}^*",
        message="error: Cannot build '/nix/store/...-diffutils-3.12.drv'.",
    )

    def warmup_realize(paths: object, *_args: object, **kwargs: object) -> object:
        realized.append((
            paths,
            bool(kwargs.get("print_build_logs")),
            bool(kwargs.get("force_local")),
            bool(kwargs.get("substitute_only")),
        ))
        if kwargs.get("substitute_only"):
            return (rustc_fail, unknown_fail)
        return ()

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", warmup_realize)
    monkeypatch.setattr(pipeline, "import_warmup_drvs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda _path: False)
    monkeypatch.setattr(pipeline, "assert_force_local_dry_run", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "rust_crate_input_drvs", lambda *_a, **_k: ())
    monkeypatch.setattr(
        pipeline,
        "compiler_input_drvs",
        lambda *_args, **_kwargs: (rustc_drv, unknown_drv),
    )
    from lib.update.ci.warmup import (
        RootWarmupStats,
        ShardLocalBuildReport,
        WarmupPlan,
        write_warmup_plan,
    )

    settings = "/nix/store/2y7vj1wq5nz030asgn7rhipbcx5aya89-rust_settings-0.1.0"
    settings_drv = f"{settings}.drv"
    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(settings,),
            rustLayers=((settings,),),
            outputDrvs={settings: settings_drv},
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=1, missing=1, warmup=1, remaining=0
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
        ),
    )
    report = pipeline.validate_candidate(
        candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=0
    )
    assert report.failures == (rustc_fail, unknown_fail)
    assert realized == [((rustc_drv, unknown_drv), False, False, True)]


def test_rust_warmup_canary_crates_realize_settings_off_slot_stripe(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1269: canary-crates must realize rust_settings from every rust layer."""
    candidate = _candidate_for_scope(prepared_run)
    monkeypatch.setattr(
        pipeline.validation,
        "validate_derivations",
        lambda *_args, **_kwargs: (),
    )
    realized: list[tuple[object, bool, bool, bool]] = []

    def warmup_realize(paths: object, *_args: object, **kwargs: object) -> tuple[()]:
        realized.append((
            paths,
            bool(kwargs.get("print_build_logs")),
            bool(kwargs.get("force_local")),
            bool(kwargs.get("substitute_only")),
        ))
        return ()

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", warmup_realize)
    monkeypatch.setattr(pipeline, "import_warmup_drvs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda _path: False)
    monkeypatch.setattr(pipeline, "compiler_input_drvs", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(pipeline, "assert_force_local_dry_run", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "rust_crate_input_drvs", lambda *_a, **_k: ())
    from lib.update.ci.warmup import (
        RootWarmupStats,
        ShardLocalBuildReport,
        WarmupPlan,
        write_warmup_plan,
    )

    filler = tuple(
        f"/nix/store/fill{index}aaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_util-{index}.0"
        for index in range(4)
    )
    settings = "/nix/store/2y7vj1wq5nz030asgn7rhipbcx5aya89-rust_settings-0.1.0"
    content = "/nix/store/ma14flyg1v5b4vhinb2l0klw1xmdg9nz-rust_settings_content-0.1.0"
    settings_drv = f"{settings}.drv"
    content_drv = f"{content}.drv"
    filler_drvs = {path: f"{path}.drv" for path in filler}
    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(*filler, content, settings),
            rustLayers=((*filler, content), (*filler, settings)),
            outputDrvs={**filler_drvs, content: content_drv, settings: settings_drv},
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=6, missing=6, warmup=6, remaining=0
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
        ),
    )
    monkeypatch.setenv("NIXCFG_CANARY", "true")
    monkeypatch.setenv(
        "NIXCFG_CANARY_CRATES",
        "settings settings_content settings_json settings_macros",
    )
    report = pipeline.validate_candidate(
        candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=0
    )
    assert report.failures == ()
    force_local = [paths for paths, _logs, force, _sub in realized if force]
    assert force_local == [(content_drv, settings_drv)]
    skipped = pipeline.validate_candidate(
        candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=3
    )
    assert skipped.failures == ()
    assert [paths for paths, _logs, force, _sub in realized if force] == [
        (content_drv, settings_drv)
    ]
    monkeypatch.delenv("NIXCFG_CANARY")
    monkeypatch.delenv("NIXCFG_CANARY_CRATES")


def test_rust_warmup_agent_ui_slot_realizes_agent_ui_last(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """agent_ui stripes force-local language_models, then realize the leaf."""
    candidate = _candidate_for_scope(prepared_run)
    monkeypatch.setattr(
        pipeline.validation,
        "validate_derivations",
        lambda *_args, **_kwargs: (),
    )
    realized: list[tuple[object, bool, bool, bool]] = []

    def warmup_realize(paths: object, *_args: object, **kwargs: object) -> tuple[()]:
        realized.append((
            paths,
            bool(kwargs.get("print_build_logs")),
            bool(kwargs.get("force_local")),
            bool(kwargs.get("substitute_only")),
        ))
        return ()

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", warmup_realize)
    monkeypatch.setattr(pipeline, "import_warmup_drvs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda _path: False)
    monkeypatch.setattr(pipeline, "compiler_input_drvs", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(pipeline, "assert_force_local_dry_run", lambda *_a, **_k: None)
    from lib.update.ci.warmup import (
        RootWarmupStats,
        ShardLocalBuildReport,
        WarmupPlan,
        write_warmup_plan,
    )

    language_models = (
        "/nix/store/h3crq11aaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_language_models-0.1.0-lib"
    )
    language_models_drv = (
        "/nix/store/lmdrvaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_language_models-0.1.0.drv"
    )
    monkeypatch.setattr(
        pipeline, "language_models_input_drvs", lambda _drvs: (language_models_drv,)
    )
    agent_ui = "/nix/store/ks6dzvchaaaaaaaaaaaaaaaaaaaaaaaa-rust_agent_ui-0.1.0"
    agent_ui_drv = "/nix/store/ks6dzvchaaaaaaaaaaaaaaaaaaaaaaaa-rust_agent_ui-0.1.0.drv"
    other = "/nix/store/otheraaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_gpui-0.1.0"
    other_drv = "/nix/store/otheraaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_gpui-0.1.0.drv"
    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(language_models, other, agent_ui),
            rustLayers=((language_models,), (other,), (agent_ui,)),
            outputDrvs={
                language_models: language_models_drv,
                other: other_drv,
                agent_ui: agent_ui_drv,
            },
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=3, missing=3, warmup=3, remaining=0
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
        ),
    )
    rust = pipeline.validate_candidate(
        candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=0
    )
    assert rust.gates == ()
    assert realized == [
        ((other_drv,), False, False, False),
        ((language_models_drv,), True, True, False),
        ((agent_ui_drv,), True, False, False),
    ]
    realized.clear()
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda path: path != agent_ui)
    rust = pipeline.validate_candidate(
        candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=0
    )
    assert rust.gates == ()
    assert realized == [
        ((language_models_drv,), True, True, False),
        ((agent_ui_drv,), True, False, False),
    ]
    realized.clear()
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(other, agent_ui),
            rustLayers=((other,), (agent_ui,)),
            outputDrvs={
                other: other_drv,
                agent_ui: agent_ui_drv,
            },
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=2, missing=2, warmup=2, remaining=0
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
        ),
    )
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda _path: False)
    monkeypatch.setattr(
        pipeline,
        "language_models_input_drvs",
        lambda _drvs: (language_models_drv,),
    )
    rust = pipeline.validate_candidate(
        candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=0
    )
    assert rust.gates == ()
    assert realized == [
        ((other_drv,), False, False, False),
        ((language_models_drv,), True, True, False),
        ((agent_ui_drv,), True, False, False),
    ]
    realized.clear()
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(language_models, agent_ui),
            rustLayers=((language_models, agent_ui),),
            outputDrvs={
                language_models: language_models_drv,
                agent_ui: agent_ui_drv,
            },
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=2, missing=2, warmup=2, remaining=0
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
        ),
    )
    rust = pipeline.validate_candidate(
        candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=0
    )
    assert rust.gates == ()
    assert realized == [((language_models_drv,), False, False, False)]
    monkeypatch.setattr(pipeline, "language_models_input_drvs", lambda _drvs: ())
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(other, agent_ui),
            rustLayers=((other,), (agent_ui,)),
            outputDrvs={
                other: other_drv,
                agent_ui: agent_ui_drv,
            },
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=2, missing=2, warmup=2, remaining=0
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
        ),
    )
    with pytest.raises(pipeline.WarmupError, match="no rust_language_models input"):
        pipeline.validate_candidate(
            candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=0
        )


def test_rust_warmup_zed_slot_force_locals_extension_host_family(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rust_zed stripe force-locals extension_host then dependents, then rust_zed."""
    candidate = _candidate_for_scope(prepared_run)
    monkeypatch.setattr(
        pipeline.validation,
        "validate_derivations",
        lambda *_args, **_kwargs: (),
    )
    realized: list[tuple[object, bool, bool, bool]] = []

    def warmup_realize(paths: object, *_args: object, **kwargs: object) -> tuple[()]:
        realized.append((
            paths,
            bool(kwargs.get("print_build_logs")),
            bool(kwargs.get("force_local")),
            bool(kwargs.get("substitute_only")),
        ))
        return ()

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", warmup_realize)
    monkeypatch.setattr(pipeline, "import_warmup_drvs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda _path: False)
    monkeypatch.setattr(pipeline, "compiler_input_drvs", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(pipeline, "assert_force_local_dry_run", lambda *_a, **_k: None)
    from lib.update.ci.warmup import (
        RootWarmupStats,
        ShardLocalBuildReport,
        WarmupPlan,
        write_warmup_plan,
    )

    extension_host = (
        "/nix/store/5crb9axiaaaaaaaaaaaaaaaaaaaaaaaaa-rust_extension_host-0.1.0-lib"
    )
    extension_host_drv = (
        "/nix/store/exthostaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_extension_host-0.1.0.drv"
    )
    activity = (
        "/nix/store/8xk2b1cbaaaaaaaaaaaaaaaaaaaaaaaaa-rust_activity_indicator-0.1.0-lib"
    )
    activity_drv = "/nix/store/actindaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_activity_indicator-0.1.0.drv"
    rust_zed = "/nix/store/av7xckfpaaaaaaaaaaaaaaaaaaaaaaaaa-rust_zed-1.25.0"
    rust_zed_drv = "/nix/store/av7xckfpaaaaaaaaaaaaaaaaaaaaaaaaa-rust_zed-1.25.0.drv"
    settings_drv = (
        "/nix/store/setuiaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_settings_ui-0.1.0.drv"
    )
    monkeypatch.setattr(
        pipeline,
        "rust_crate_input_drvs",
        lambda *_args, **_kwargs: (extension_host_drv, activity_drv),
    )
    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(extension_host, activity, rust_zed),
            rustLayers=((extension_host,), (activity,), (rust_zed,)),
            outputDrvs={
                extension_host: extension_host_drv,
                activity: activity_drv,
                rust_zed: rust_zed_drv,
            },
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=3, missing=3, warmup=3, remaining=0
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
        ),
    )
    rust = pipeline.validate_candidate(
        candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=0
    )
    assert rust.gates == ()
    assert realized == [
        ((extension_host_drv,), True, True, False),
        ((activity_drv,), True, True, False),
        ((rust_zed_drv,), True, False, False),
    ]
    realized.clear()
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(extension_host, rust_zed),
            rustLayers=((extension_host,), (rust_zed,)),
            outputDrvs={
                extension_host: extension_host_drv,
                rust_zed: rust_zed_drv,
            },
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=2, missing=2, warmup=2, remaining=0
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
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "rust_crate_input_drvs",
        lambda *_args, **_kwargs: (extension_host_drv, activity_drv, settings_drv),
    )
    rust = pipeline.validate_candidate(
        candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=0
    )
    assert rust.gates == ()
    assert realized == [
        ((extension_host_drv,), True, True, False),
        ((activity_drv, settings_drv), True, True, False),
        ((rust_zed_drv,), True, False, False),
    ]
    monkeypatch.setattr(pipeline, "rust_crate_input_drvs", lambda *_args, **_kwargs: ())
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(rust_zed,),
            rustLayers=((rust_zed,),),
            outputDrvs={rust_zed: rust_zed_drv},
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=1, missing=1, warmup=1, remaining=0
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
        ),
    )
    with pytest.raises(pipeline.WarmupError, match="no rust_extension_host input"):
        pipeline.validate_candidate(
            candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=0
        )


def test_rust_warmup_zed_nightly_waits_for_family(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1262: zed-editor-nightly must not compile rust_zed before the family."""
    candidate = _candidate_for_scope(prepared_run)
    monkeypatch.setattr(
        pipeline.validation, "validate_derivations", lambda *_args, **_kwargs: ()
    )
    realized: list[tuple[object, bool, bool, bool]] = []

    def warmup_realize(paths: object, *_args: object, **kwargs: object) -> tuple[()]:
        realized.append((
            paths,
            bool(kwargs.get("print_build_logs")),
            bool(kwargs.get("force_local")),
            bool(kwargs.get("substitute_only")),
        ))
        return ()

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", warmup_realize)
    monkeypatch.setattr(pipeline, "import_warmup_drvs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda _path: False)
    monkeypatch.setattr(pipeline, "assert_force_local_dry_run", lambda *_a, **_k: None)
    from lib.update.ci.warmup import (
        RootWarmupStats,
        ShardLocalBuildReport,
        WarmupPlan,
        write_warmup_plan,
    )

    extension_host = (
        "/nix/store/5crb9axiaaaaaaaaaaaaaaaaaaaaaaaaa-rust_extension_host-0.1.0-lib"
    )
    extension_host_drv = (
        "/nix/store/exthostaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_extension_host-0.1.0.drv"
    )
    settings_drv = (
        "/nix/store/setuiaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_settings_ui-0.1.0.drv"
    )
    rust_zed = "/nix/store/av7xckfpaaaaaaaaaaaaaaaaaaaaaaaaa-rust_zed-1.25.0"
    rust_zed_drv = "/nix/store/av7xckfpaaaaaaaaaaaaaaaaaaaaaaaaa-rust_zed-1.25.0.drv"
    nightly = "/nix/store/rlcwld41aaaaaaaaaaaaaaaaaaaaaaaaa-zed-editor-nightly-unstable-f16f965"
    nightly_drv = (
        "/nix/store/rlcwld41aaaaaaaaaaaaaaaaaaaaaaaaa-"
        "zed-editor-nightly-unstable-f16f965.drv"
    )
    rustc_drv = "/nix/store/xbq69m0caaaaaaaaaaaaaaaaaaaaaaaa-rustc-1.98.1.drv"
    monkeypatch.setattr(
        pipeline, "compiler_input_drvs", lambda *_args, **_kwargs: (rustc_drv,)
    )
    monkeypatch.setattr(
        pipeline,
        "rust_crate_input_drvs",
        lambda *_args, **_kwargs: (extension_host_drv, settings_drv),
    )
    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(extension_host, rust_zed, nightly),
            rustLayers=((extension_host,), (rust_zed,), (nightly,)),
            outputDrvs={
                extension_host: extension_host_drv,
                rust_zed: rust_zed_drv,
                nightly: nightly_drv,
            },
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=3, missing=3, warmup=3, remaining=0
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
        ),
    )
    rust = pipeline.validate_candidate(
        candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=0
    )
    assert rust.gates == ()
    assert realized == [
        ((rustc_drv,), False, False, True),
        ((extension_host_drv,), True, True, False),
        ((settings_drv,), True, True, False),
        ((rust_zed_drv,), True, False, False),
        ((nightly_drv,), True, False, False),
    ]
    realized.clear()
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(nightly,),
            rustLayers=((nightly,),),
            outputDrvs={nightly: nightly_drv},
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=1, missing=1, warmup=1, remaining=0
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
        ),
    )

    def crate_inputs(
        _parents: object, crates: object, **_kwargs: object
    ) -> tuple[str, ...]:
        if tuple(crates) == ("zed",):
            return (rust_zed_drv,)
        return (extension_host_drv, settings_drv)

    monkeypatch.setattr(pipeline, "rust_crate_input_drvs", crate_inputs)
    rust = pipeline.validate_candidate(
        candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=0
    )
    assert rust.gates == ()
    assert realized == [
        ((rustc_drv,), False, False, True),
        ((extension_host_drv,), True, True, False),
        ((settings_drv,), True, True, False),
        ((nightly_drv,), True, False, False),
    ]
    realized.clear()
    from lib.update.derivation_validation import DerivationValidationFailure

    def failing_compiler(
        paths: object, *_args: object, **kwargs: object
    ) -> tuple[DerivationValidationFailure, ...]:
        realized.append((
            paths,
            bool(kwargs.get("print_build_logs")),
            bool(kwargs.get("force_local")),
            bool(kwargs.get("substitute_only")),
        ))
        if kwargs.get("substitute_only"):
            return (
                DerivationValidationFailure("root-warmup", "rustc.drv^*", "cache miss"),
            )
        return ()

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", failing_compiler)
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(extension_host, rust_zed),
            rustLayers=((extension_host,), (rust_zed,)),
            outputDrvs={
                extension_host: extension_host_drv,
                rust_zed: rust_zed_drv,
            },
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=2, missing=2, warmup=2, remaining=0
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
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "rust_crate_input_drvs",
        lambda *_args, **_kwargs: (extension_host_drv, settings_drv),
    )
    rust = pipeline.validate_candidate(
        candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=0
    )
    assert rust.failures[0].message == "cache miss"
    assert realized == [((rustc_drv,), False, False, True)]


def test_rust_warmup_downloads_source_fods_before_force_local(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1264: crate tarball FODs download; rustc stays --max-jobs 0."""
    candidate = _candidate_for_scope(prepared_run)
    monkeypatch.setattr(
        pipeline.validation, "validate_derivations", lambda *_args, **_kwargs: ()
    )
    realized: list[tuple[object, bool, bool, bool]] = []

    def warmup_realize(paths: object, *_args: object, **kwargs: object) -> tuple[()]:
        realized.append((
            paths,
            bool(kwargs.get("print_build_logs")),
            bool(kwargs.get("force_local")),
            bool(kwargs.get("substitute_only")),
        ))
        return ()

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", warmup_realize)
    monkeypatch.setattr(pipeline, "import_warmup_drvs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda _path: False)
    monkeypatch.setattr(pipeline, "assert_force_local_dry_run", lambda *_a, **_k: None)
    from lib.update.ci.warmup import (
        RootWarmupStats,
        ShardLocalBuildReport,
        WarmupPlan,
        write_warmup_plan,
    )

    extension_host = (
        "/nix/store/5crb9axiaaaaaaaaaaaaaaaaaaaaaaaaa-rust_extension_host-0.1.0-lib"
    )
    extension_host_drv = (
        "/nix/store/exthostaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_extension_host-0.1.0.drv"
    )
    rust_zed = "/nix/store/av7xckfpaaaaaaaaaaaaaaaaaaaaaaaaa-rust_zed-1.25.0"
    rust_zed_drv = "/nix/store/av7xckfpaaaaaaaaaaaaaaaaaaaaaaaaa-rust_zed-1.25.0.drv"
    rustc_drv = "/nix/store/xbq69m0caaaaaaaaaaaaaaaaaaaaaaaa-rustc-1.98.1.drv"
    crate_src = (
        "/nix/store/195q1crx0p9g5na7l7aw96bqvs3y2ab0-coreaudio-rs-0.14.2.tar.gz.drv"
    )
    monkeypatch.setattr(
        pipeline,
        "compiler_input_drvs",
        lambda *_args, **_kwargs: (rustc_drv, crate_src),
    )
    monkeypatch.setattr(
        pipeline,
        "rust_crate_input_drvs",
        lambda *_args, **_kwargs: (extension_host_drv,),
    )
    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(extension_host, rust_zed),
            rustLayers=((extension_host,), (rust_zed,)),
            outputDrvs={
                extension_host: extension_host_drv,
                rust_zed: rust_zed_drv,
            },
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=2, missing=2, warmup=2, remaining=0
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
        ),
    )
    rust = pipeline.validate_candidate(
        candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=0
    )
    assert rust.gates == ()
    assert realized == [
        ((rustc_drv,), False, False, True),
        ((crate_src,), False, False, False),
        ((extension_host_drv,), True, True, False),
        ((rust_zed_drv,), True, False, False),
    ]
    realized.clear()

    def failing_fetch(
        paths: object, *_args: object, **kwargs: object
    ) -> tuple[DerivationValidationFailure, ...]:
        realized.append((
            paths,
            bool(kwargs.get("print_build_logs")),
            bool(kwargs.get("force_local")),
            bool(kwargs.get("substitute_only")),
        ))
        if not kwargs.get("substitute_only") and not kwargs.get("force_local"):
            return (
                DerivationValidationFailure(
                    "root-warmup", "coreaudio.drv^*", "cannot download"
                ),
            )
        return ()

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", failing_fetch)
    rust = pipeline.validate_candidate(
        candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=0
    )
    assert rust.failures[0].message == "cannot download"
    assert realized == [
        ((rustc_drv,), False, False, True),
        ((crate_src,), False, False, False),
    ]


def test_closure_budget_timeout_fails_closed(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A timed-out or interrupted closure build writes no success report."""
    candidate = _candidate_for_scope(prepared_run)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(candidate.model_dump_json())
    output = tmp_path / "validation.json"
    monkeypatch.setattr(
        pipeline.validation, "validate_derivations", lambda *_args, **_kwargs: ()
    )
    args = [
        "validate",
        "--candidate",
        str(candidate_path),
        "--output",
        str(output),
        "--scope",
        "closures",
        "--closure-budget-seconds",
        "18000",
    ]

    def timed_out(**_kwargs: object) -> None:
        raise ValidationIncompleteError(
            "Validation incomplete: nix build path:.#checks.aarch64-darwin.root-closures: "
            "Command timed out after 18000 seconds"
        )

    monkeypatch.setattr(pipeline.validation, "validate_root_closures", timed_out)
    timed = CliRunner().invoke(pipeline.app, args)
    assert timed.exit_code not in {0, None}
    assert not output.exists()

    def eval_timed_out(**_kwargs: object) -> None:
        raise ValidationIncompleteError(
            "Validation incomplete: nix eval path:.#lib.rootClosureManifest: "
            "Command timed out after 2700 seconds"
        )

    monkeypatch.setattr(pipeline.validation, "validate_root_closures", eval_timed_out)
    stalled = CliRunner().invoke(pipeline.app, args)
    assert stalled.exit_code not in {0, None}
    assert not output.exists()

    def signaled(**_kwargs: object) -> None:
        raise ValidationIncompleteError(
            "Validation incomplete: nix build: terminated by signal 15"
        )

    monkeypatch.setattr(pipeline.validation, "validate_root_closures", signaled)
    killed = CliRunner().invoke(pipeline.app, args)
    assert killed.exit_code not in {0, None}

    def store_bus(**_kwargs: object) -> None:
        raise ValidationIncompleteError(
            "Validation incomplete: nix build path:.#checks.aarch64-darwin.root-closures: "
            "terminated by signal 10"
        )

    monkeypatch.setattr(pipeline.validation, "validate_root_closures", store_bus)
    bus = CliRunner().invoke(pipeline.app, args)
    assert bus.exit_code not in {0, None}
    assert not output.exists()
    missing_roots = CliRunner().invoke(
        pipeline.app,
        [
            *args,
            "--scope",
            "closure-shard",
        ],
    )
    assert missing_roots.exit_code != 0
    package_roots = CliRunner().invoke(
        pipeline.app,
        [
            "validate",
            "--candidate",
            str(candidate_path),
            "--output",
            str(output),
            "--scope",
            "packages",
            "--closure-roots",
            "darwin-argus",
        ],
    )
    assert package_roots.exit_code != 0


@pytest.mark.parametrize(
    "message",
    [
        'error: cannot unlink "/nix/store/abc-replay-10.67.0.tgz": Illegal byte sequence',
        (
            'error: clearing flags of path "/nix/store/s6-bun-cache/share/'
            'bun-packages/lie@3.3.0": Illegal byte sequence\n'
            "error: Cannot build '/nix/store/a7-superset-1.30.2.drv'.\n"
            "       Reason: 1 dependency failed.\n"
        ),
        (
            'error: opening file "/nix/store/67b3dw9p5i6qynv9mf3fhsa21cmibk57-'
            'unsloth-desktop-0.1.813-beta.drv": No such file or directory'
        ),
        (
            "error: Cannot build '/nix/store/wzrs1hpgczfxgv6q7yvp7iy39plhakw7-"
            "granola-7.626.3.drv'.\n"
            "       Reason: builder failed with exit code 1.\n"
            "       > build input /nix/store/fyaryjvghbkpfnsyw97hb3lyb37s1pd6-"
            "move-lib64.sh does not exist"
        ),
        (
            "error: cannot open connection to remote store 'daemon': "
            "Nix daemon disconnected unexpectedly (maybe it crashed?)"
        ),
        (
            "error: Cannot build '/nix/store/1vhn1bsiqchjp101n2sj5fjjk6fiw596-"
            "rust_agent_settings-0.1.0.drv'.\n"
            "       Reason: builder failed with exit code 1.\n"
            "       > rustc --extern settings=/nix/store/"
            "l0sqrxbm7jiz24hjci8bpkl2mh9wwsvw-rust_settings-0.1.0-lib/lib/"
            "libsettings-7be7f1170a.rlib\n"
            "       > error[E0463]: can't find crate for `settings`\n"
            "       > note: extern location for settings does not exist: "
            "/nix/store/l0sqrxbm7jiz24hjci8bpkl2mh9wwsvw-rust_settings-0.1.0-lib/"
            "lib/libsettings-7be7f1170a.rlib"
        ),
    ],
)
def test_closure_store_faults_fail_closed(
    prepared_run,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    message: str,
) -> None:
    """Store faults no longer yield a later shard; the job fails closed."""
    candidate = _candidate_for_scope(prepared_run)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(candidate.model_dump_json())
    output = tmp_path / "validation.json"
    monkeypatch.setattr(
        pipeline.validation, "validate_derivations", lambda *_args, **_kwargs: ()
    )

    def only_store(**_kwargs: object) -> tuple[DerivationValidationFailure, ...]:
        return (
            DerivationValidationFailure(
                source="root-closures",
                installable="path:.#checks.aarch64-darwin.root-closures",
                message=message,
            ),
        )

    monkeypatch.setattr(pipeline.validation, "validate_root_closures", only_store)
    result = CliRunner().invoke(
        pipeline.app,
        [
            "validate",
            "--candidate",
            str(candidate_path),
            "--output",
            str(output),
            "--scope",
            "closures",
            "--closure-budget-seconds",
            "18000",
        ],
    )
    assert result.exit_code == 1
    written = json.loads(output.read_text())
    assert written["failures"][0]["message"] == message


def test_closure_shard_writes_receipt_instead_of_validation_report(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per-root shards are not certify evidence."""
    candidate = _candidate_for_scope(prepared_run)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(candidate.model_dump_json())
    output = tmp_path / "shard-receipt.json"
    monkeypatch.setattr(
        pipeline.validation, "validate_derivations", lambda *_args, **_kwargs: ()
    )
    seen: list[object] = []
    order: list[str] = []

    def roots(**kwargs: object) -> tuple[()]:
        order.append("roots")
        seen.append(kwargs.get("root_names"))
        return ()

    monkeypatch.setattr(
        pipeline.jobs, "reclaim_hosted_store", lambda: order.append("reclaim")
    )
    monkeypatch.setattr(pipeline.validation, "validate_root_closures", roots)
    result = CliRunner().invoke(
        pipeline.app,
        [
            "validate",
            "--candidate",
            str(candidate_path),
            "--output",
            str(output),
            "--scope",
            "closure-shard",
            "--closure-roots",
            "darwin-argus",
            "--shard",
            "darwin-argus",
        ],
    )
    assert result.exit_code == 0, result.output
    receipt = json.loads(output.read_text())
    assert receipt["shard"] == "darwin-argus"
    assert receipt["roots"] == ["darwin-argus"]
    assert receipt["failures"] == []
    assert seen == [("darwin-argus",)]
    assert order == ["reclaim", "roots"]


def test_plan_shards_writes_generated_matrix(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate_for_scope(prepared_run)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(candidate.model_dump_json())
    output = tmp_path / "matrix.json"
    github = tmp_path / "github-output"
    manifest = {
        "schemaVersion": 2,
        "requiredKinds": ["darwin", "home"],
        "requiredRoots": [],
        "roots": [
            {"kind": "darwin", "name": "argus", "system": "aarch64-darwin"},
            {"kind": "home", "name": "george", "system": "aarch64-darwin"},
        ],
    }

    def fake_eval(flake_root: Path) -> object:
        from lib.update.derivation_validation import RootClosureManifest

        assert flake_root.is_dir()
        return RootClosureManifest.model_validate(manifest)

    def fake_paths(
        flake_root: Path, evaluated: object, **_kwargs: object
    ) -> dict[str, str]:
        assert flake_root.is_dir()
        assert evaluated is not None
        return {
            "darwin-argus": "/nix/store/argus",
            "home-george": "/nix/store/home",
            "aggregate:aarch64-darwin": "/nix/store/farm",
        }

    monkeypatch.setattr(pipeline, "eval_root_closure_manifest", fake_eval)
    monkeypatch.setattr(pipeline, "root_store_paths", fake_paths)

    def fake_warmup(*_args: object, **_kwargs: object) -> object:
        from lib.update.ci.warmup import (
            RootWarmupStats,
            ShardLocalBuildReport,
            WarmupPlan,
        )

        return WarmupPlan(
            schemaVersion=1,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=("/nix/store/shared",),
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=2, missing=1, warmup=1, remaining=0
                ),
                "home-george": RootWarmupStats(
                    outputs=2, missing=1, warmup=1, remaining=0
                ),
            },
            shards=(
                ShardLocalBuildReport(
                    shard="darwin-argus",
                    roots=("darwin-argus",),
                    remaining=0,
                    remaining_rust_crates=0,
                ),
                ShardLocalBuildReport(
                    shard="home-george",
                    roots=("home-george",),
                    remaining=0,
                    remaining_rust_crates=0,
                ),
            ),
            notes="fixture",
        )

    monkeypatch.setattr(pipeline, "plan_darwin_warmup", fake_warmup)
    result = CliRunner().invoke(
        pipeline.app,
        [
            "plan-shards",
            "--candidate",
            str(candidate_path),
            "--output",
            str(output),
            "--github-output",
            str(github),
        ],
    )
    assert result.exit_code == 0, result.output
    matrix = json.loads(output.read_text())
    assert {row["shard"] for row in matrix["include"]} == {
        "darwin-argus",
        "home-george",
    }
    cache = json.loads(output.with_name(ROOT_OUT_PATHS_NAME).read_text())
    assert cache["tree"] == candidate.tree
    assert cache["rootPaths"]["darwin-argus"] == "/nix/store/argus"
    assert cache["manifest"]["roots"]
    warmup = json.loads(output.with_name("warmup-plan.json").read_text())
    assert warmup["warmupOutputs"] == ["/nix/store/shared"]
    assert "rustLayers" in warmup
    assert "darwin_closure_shards=" in github.read_text()
    env_output = tmp_path / "github-output-env"
    monkeypatch.setenv("GITHUB_OUTPUT", str(env_output))
    env_matrix = tmp_path / "matrix-env.json"
    via_env = CliRunner().invoke(
        pipeline.app,
        [
            "plan-shards",
            "--candidate",
            str(candidate_path),
            "--output",
            str(env_matrix),
        ],
    )
    assert via_env.exit_code == 0, via_env.output
    assert "darwin_closure_shards=" in env_output.read_text()
    assert (env_matrix.with_name(ROOT_OUT_PATHS_NAME)).is_file()
    monkeypatch.delenv("GITHUB_OUTPUT")
    no_output = tmp_path / "matrix-no-github.json"
    without = CliRunner().invoke(
        pipeline.app,
        [
            "plan-shards",
            "--candidate",
            str(candidate_path),
            "--output",
            str(no_output),
        ],
    )
    assert without.exit_code == 0, without.output
    assert json.loads(no_output.read_text())["include"]
    assert json.loads(no_output.with_name(ROOT_OUT_PATHS_NAME).read_text())["tree"] == (
        candidate.tree
    )


def _coverage_manifest() -> object:
    from lib.update.derivation_validation import RootClosureManifest

    return RootClosureManifest.model_validate({
        "schemaVersion": 2,
        "requiredKinds": ["darwin", "home"],
        "requiredRoots": [],
        "roots": [
            {"kind": "darwin", "name": "argus", "system": "aarch64-darwin"},
            {"kind": "home", "name": "george", "system": "aarch64-darwin"},
        ],
    })


def _write_coverage_cache(evidence: Path, *, tree: str) -> None:
    cache = evidence / "plan-shards-x86_64-linux" / ROOT_OUT_PATHS_NAME
    cache.parent.mkdir(parents=True)
    write_root_out_path_cache(
        cache,
        tree=tree,
        root_paths={
            "darwin-argus": "/nix/store/argus",
            "home-george": "/nix/store/home",
            "aggregate:aarch64-darwin": "/nix/store/farm",
        },
        manifest=_coverage_manifest(),
    )


def test_assert_coverage_cli_reuses_planner_out_paths(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate_for_scope(prepared_run)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(candidate.model_dump_json())
    evidence = tmp_path / "evidence"
    _write_coverage_cache(evidence, tree=candidate.tree)
    seen: dict[str, object] = {}

    def fake_assert(**kwargs: object) -> None:
        seen.update(kwargs)

    def refuse_eval(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("assert-coverage must not re-evaluate root out paths")

    monkeypatch.setattr(pipeline, "eval_root_closure_manifest", refuse_eval)
    monkeypatch.setattr(pipeline, "root_store_paths", refuse_eval)
    monkeypatch.setattr(pipeline, "assert_update_coverage", fake_assert)
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda _path: True)
    result = CliRunner().invoke(
        pipeline.app,
        [
            "assert-coverage",
            "--candidate",
            str(candidate_path),
            "--evidence",
            str(evidence),
            "--job-results",
            dump_job_results(dict.fromkeys(required_coverage_jobs(), "success")),
        ],
    )
    assert result.exit_code == 0, result.output
    assert seen["tree"] == candidate.tree
    assert seen["evidence"] == evidence
    assert seen["root_paths"]["darwin-argus"] == "/nix/store/argus"
    assert seen["manifest"] == _coverage_manifest()


def test_assert_coverage_fails_closed_before_workspace_when_jobs_failed(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate_for_scope(prepared_run)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(candidate.model_dump_json())
    evidence = tmp_path / "evidence"
    evidence.mkdir()

    def refuse_workspace(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("failed jobs must not enter IsolatedUpdateWorkspace")

    def refuse_cache(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("failed jobs must not load the out-path cache")

    monkeypatch.setattr(pipeline, "IsolatedUpdateWorkspace", refuse_workspace)
    monkeypatch.setattr(pipeline, "load_planned_root_out_paths", refuse_cache)
    failed = dict.fromkeys(required_coverage_jobs(), "success")
    failed["validate-darwin-roots"] = "failure"
    result = CliRunner().invoke(
        pipeline.app,
        [
            "assert-coverage",
            "--candidate",
            str(candidate_path),
            "--evidence",
            str(evidence),
            "--job-results",
            dump_job_results(failed),
        ],
    )
    assert result.exit_code != 0
    assert isinstance(result.exception, CoverageError)
    assert "did not succeed" in str(result.exception)


def test_assert_coverage_fails_closed_when_planner_cache_missing(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate_for_scope(prepared_run)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(candidate.model_dump_json())
    evidence = tmp_path / "evidence"
    evidence.mkdir()

    def refuse_eval(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("missing cache must not fall back to root_store_paths")

    monkeypatch.setattr(pipeline, "root_store_paths", refuse_eval)
    monkeypatch.setattr(pipeline, "IsolatedUpdateWorkspace", refuse_eval)
    result = CliRunner().invoke(
        pipeline.app,
        [
            "assert-coverage",
            "--candidate",
            str(candidate_path),
            "--evidence",
            str(evidence),
            "--job-results",
            dump_job_results(dict.fromkeys(required_coverage_jobs(), "success")),
        ],
    )
    assert result.exit_code != 0
    assert isinstance(result.exception, CoverageError)
    assert ROOT_OUT_PATHS_NAME in str(result.exception)


def test_prepare_command_exports_failure_evidence_outside_checkout(
    prepared_run, tmp_path: Path
) -> None:
    """The CLI emits machine-readable diagnostics and saves a failed candidate."""
    root, _, state = prepared_run
    state["fail"] = True
    output = tmp_path / "artifacts" / "candidate.json"
    result = CliRunner().invoke(
        pipeline.app, ["prepare", "--output", str(output), "example"]
    )
    assert result.exit_code == 1, result.output
    assert not Candidate.model_validate_json(output.read_bytes()).prepared
    assert json.loads(result.stdout)["errors"] == ["example"]
    rejected = CliRunner().invoke(
        pipeline.app, ["prepare", "--output", str(root / "candidate.json")]
    )
    assert rejected.exit_code != 0
    assert not (root / "candidate.json").exists()


@pytest.mark.parametrize("validate_all_packages", [False, True])
def test_candidate_commands_transfer_the_pinned_selection_and_native_reports(
    validate_all_packages,
    prepared_run,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Exercise the actual CLI files passed between independent native jobs."""
    _, _, state = prepared_run
    runner = CliRunner()
    previous = None
    reports = []
    for index, system in enumerate(pipeline.supported_systems()):
        state["system"] = system
        output = tmp_path / f"candidate-{index}.json"
        args = ["prepare", "--output", str(output)]
        if previous is None:
            if validate_all_packages:
                args.append("--validate-all-packages")
            args.append("example")
        else:
            args += ["--previous", str(previous)]
        result = runner.invoke(pipeline.app, args)
        assert result.exit_code == 0, result.output
        assert Candidate.model_validate_json(output.read_bytes()).targets == (
            "example",
        )
        previous = output
        assert (
            Candidate.model_validate_json(output.read_bytes()).validate_all_packages
            == validate_all_packages
        )
    assert previous is not None
    monkeypatch.setattr(
        pipeline.validation, "validate_derivations", lambda *_args, **_kwargs: ()
    )
    monkeypatch.setattr(
        pipeline.validation, "validate_root_closures", lambda **_kwargs: ()
    )
    for system in pipeline.supported_systems():
        state["system"] = system
        report = tmp_path / f"{system}.json"
        result = runner.invoke(
            pipeline.app,
            ["validate", "--candidate", str(previous), "--output", str(report)],
        )
        assert result.exit_code == 0, result.exception
        reports.append(report)
    patch = tmp_path / "update.patch"
    args = ["certify", "--candidate", str(previous), "--output", str(patch)]
    for report in reports:
        args += ["--report", str(report)]
    result = runner.invoke(pipeline.app, args)
    assert result.exit_code == 0, result.exception
    assert (
        patch.read_bytes() == Candidate.model_validate_json(previous.read_bytes()).patch
    )
    reports[0].unlink()
    assert runner.invoke(pipeline.app, args).exit_code != 0


def test_hosted_validation_streams_nix_logs_to_stderr(
    prepared_run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Hosted validate must print Nix output on the live job log, not only artifacts."""
    root, _, _state = prepared_run
    tree = git(root, "rev-parse", "HEAD^{tree}").decode().strip()
    candidate = Candidate(
        base_tree=tree,
        tree=tree,
        targets=(),
        sources=(),
        systems=pipeline.supported_systems(),
        resolutions={},
        prepared=True,
        patch=b"",
    )

    def validate_derivations(*_args, progress, **_kwargs):
        progress(
            ValidationCommandStarted("nix build path:.#pkgs.aarch64-darwin.example")
        )
        progress(
            ValidationCommandOutput(
                "nix build path:.#pkgs.aarch64-darwin.example",
                "building '/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-example.drv'",
            )
        )
        progress(
            ValidationCommandOutput(
                "nix build path:.#pkgs.aarch64-darwin.example",
                "fetching https://example.com/file?token=secret",
            )
        )
        progress(
            ValidationCommandOutput(
                "nix build path:.#pkgs.aarch64-darwin.example", "\r"
            )
        )
        progress("")
        progress("\r")
        progress("Batch validation did not succeed; isolating failing targets")
        progress(
            ValidationCommandFinished(
                "nix build path:.#pkgs.aarch64-darwin.example", succeeded=True
            )
        )
        return ()

    def validate_roots(*_args, progress, **_kwargs):
        progress(
            ValidationCommandStarted(
                "nix build path:.#checks.aarch64-darwin.root-closures"
            )
        )
        progress(
            ValidationCommandOutput(
                "nix build path:.#checks.aarch64-darwin.root-closures",
                "building '/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-root-closures.drv'",
            )
        )
        progress(
            ValidationCommandFinished(
                "nix build path:.#checks.aarch64-darwin.root-closures",
                succeeded=True,
            )
        )
        return ()

    monkeypatch.setattr(
        pipeline.validation, "validate_derivations", validate_derivations
    )
    monkeypatch.setattr(pipeline.validation, "validate_root_closures", validate_roots)
    assert pipeline.validate_candidate(candidate).failures == ()
    err = capsys.readouterr().err
    assert "[derivations] $ nix build path:.#pkgs.aarch64-darwin.example\n" in err
    assert (
        "[derivations] building '/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-example.drv'\n"
        in err
    )
    assert "token=secret" not in err
    assert "[derivations] fetching https://example.com/file?REDACTED\n" in err
    assert (
        "[derivations] Batch validation did not succeed; isolating failing targets\n"
        in err
    )
    assert (
        "[root-closures] $ nix build path:.#checks.aarch64-darwin.root-closures\n"
        in err
    )
    assert (
        "[root-closures] building '/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-root-closures.drv'\n"
        in err
    )
    assert "[derivations] \n" not in err


def test_hosted_warmup_progress_fails_fast_on_fatal_patterns(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Warm-rust and roots must abort on the first streamed fatal line."""
    progress = pipeline._hosted_validation_progress("rust-warmup")
    with pytest.raises(WarmupFatalError, match="SVH"):
        progress(
            ValidationCommandOutput(
                "nix build --no-substitute",
                "error[E0460]: found possibly newer version of crate `settings_ui`",
            )
        )
    assert "[rust-warmup] error[E0460]" in capsys.readouterr().err
    roots = pipeline._hosted_validation_progress("root-closures")
    with pytest.raises(WarmupFatalError, match="will-be-built"):
        roots(
            ValidationCommandOutput(
                "nix build",
                "these 406 derivations will be built:",
            )
        )
    packages = pipeline._hosted_validation_progress("derivations")
    packages(
        ValidationCommandOutput(
            "nix build",
            "error[E0460]: packages inventory is not a warmup abort",
        )
    )


def test_hosted_warmup_progress_skips_cannot_build_during_max_jobs_zero(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1270: streamed Cannot-build under --max-jobs 0 is retried, not fatal."""
    monkeypatch.setattr(pipeline.jobs, "record_runner_storage", lambda *_a, **_k: {})
    progress = pipeline._hosted_validation_progress("rust-warmup")
    progress(ValidationCommandStarted("nix build --max-jobs 0 /nix/store/cpio.drv"))
    progress(
        ValidationCommandOutput(
            "nix build --max-jobs 0 /nix/store/cpio.drv",
            "error: Cannot build '/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-cpio-2.15.drv'.",
        )
    )
    assert "Cannot build" in capsys.readouterr().err
    with pytest.raises(WarmupFatalError, match="SVH"):
        progress(
            ValidationCommandOutput(
                "nix build --max-jobs 0 /nix/store/cpio.drv",
                "error[E0463]: can't find crate for `settings_content`",
            )
        )
    progress(
        ValidationCommandStarted("nix build --no-substitute /nix/store/settings.drv")
    )
    with pytest.raises(WarmupFatalError, match="cannot build"):
        progress(
            ValidationCommandOutput(
                "nix build --no-substitute /nix/store/settings.drv",
                "Cannot build '/nix/store/2y7vj1wq5nz030asgn7rhipbcx5aya89-rust_settings-0.1.0.drv'",
            )
        )


def test_noop_candidate_still_validates_repaired_baseline_roots(
    prepared_run,
    monkeypatch,
) -> None:
    """A repaired baseline can be broken even when preparation emits no patch."""
    root, _, state = prepared_run
    tree = git(root, "rev-parse", "HEAD^{tree}").decode().strip()
    candidate = Candidate(
        base_tree=tree,
        tree=tree,
        targets=(),
        sources=(),
        systems=pipeline.supported_systems(),
        resolutions={},
        prepared=True,
        patch=b"",
    )
    checked = []
    monkeypatch.setattr(
        pipeline.validation, "validate_derivations", lambda *_args, **_kwargs: ()
    )

    def roots(**kwargs):
        checked.append(kwargs["systems"])
        return ()

    monkeypatch.setattr(pipeline.validation, "validate_root_closures", roots)
    assert pipeline.validate_candidate(candidate).failures == ()
    assert checked == [(state["system"],)]


def test_hosted_darwin_builds_root_closures_without_derivation_logs(
    prepared_run, monkeypatch
) -> None:
    """The Mac runner must realize the closure; -L logs previously killed the job."""
    root, _, _state = prepared_run
    tree = git(root, "rev-parse", "HEAD^{tree}").decode().strip()
    candidate = Candidate(
        base_tree=tree,
        tree=tree,
        targets=(),
        sources=(),
        systems=pipeline.supported_systems(),
        resolutions={},
        prepared=True,
        patch=b"",
    )
    seen: list[bool] = []
    monkeypatch.setattr(pipeline.jobs, "is_hosted_darwin_runner", lambda: True)
    monkeypatch.setattr(pipeline.jobs, "reclaim_hosted_store", lambda: None)
    monkeypatch.setattr(
        pipeline.validation, "validate_derivations", lambda *_args, **_kwargs: ()
    )

    def roots(**kwargs):
        seen.append(kwargs["print_build_logs"])
        return ()

    monkeypatch.setattr(pipeline.validation, "validate_root_closures", roots)
    assert pipeline.validate_candidate(candidate).failures == ()
    assert seen == [False]


def test_non_hosted_builders_print_root_closure_derivation_logs(
    prepared_run, monkeypatch
) -> None:
    """Linux and dedicated Darwin builders still stream -L for closure triage."""
    root, _, _state = prepared_run
    tree = git(root, "rev-parse", "HEAD^{tree}").decode().strip()
    candidate = Candidate(
        base_tree=tree,
        tree=tree,
        targets=(),
        sources=(),
        systems=pipeline.supported_systems(),
        resolutions={},
        prepared=True,
        patch=b"",
    )
    seen: list[bool] = []
    monkeypatch.setattr(pipeline.jobs, "is_hosted_darwin_runner", lambda: False)
    monkeypatch.setattr(pipeline.jobs, "reclaim_hosted_store", lambda: None)
    monkeypatch.setattr(
        pipeline.validation, "validate_derivations", lambda *_args, **_kwargs: ()
    )

    def roots(**kwargs):
        seen.append(kwargs["print_build_logs"])
        return ()

    monkeypatch.setattr(pipeline.validation, "validate_root_closures", roots)
    assert pipeline.validate_candidate(candidate).failures == ()
    assert seen == [True]


def _candidate_with_file(
    root: Path,
    relative: str,
    content: str,
    *,
    systems: tuple[str, ...],
) -> Candidate:
    """Build a prepared candidate whose patch adds or replaces one file."""
    base = git(root, "rev-parse", "HEAD^{tree}").decode().strip()
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    git(root, "add", "--", relative)
    tree = git(root, "write-tree").decode().strip()
    patch = git(
        root,
        "diff",
        "--cached",
        "--binary",
        "--full-index",
        "--no-ext-diff",
        "--no-textconv",
        "HEAD",
        "--",
    )
    git(root, "reset", "--hard", "HEAD")
    return Candidate(
        base_tree=base,
        tree=tree,
        targets=("example",),
        sources=("example",),
        systems=systems,
        resolutions={},
        prepared=True,
        patch=patch,
    )


def test_merge_prepared_candidates_preserves_guide_symlink(tmp_path: Path) -> None:
    """Materializing the merge must not follow CLAUDE.md into AGENTS.md (#1267)."""
    root = tmp_path / "repo"
    init_update_workspace_repo(
        root,
        tracked_files={
            "AGENTS.md": "# Agent Guide\nkeep this body\n",
            "keep.txt": "keep\n",
        },
    )
    (root / "CLAUDE.md").symlink_to("AGENTS.md")
    git(root, "add", "--", "CLAUDE.md")
    git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "commit.gpgSign=false",
        "commit",
        "-m",
        "symlink",
    )
    arm = _candidate_with_file(
        root,
        "arm.txt",
        "arm\n",
        systems=("aarch64-darwin", "aarch64-linux"),
    )
    x86 = _candidate_with_file(
        root,
        "x86.txt",
        "x86\n",
        systems=("aarch64-darwin", "x86_64-linux"),
    )
    merged = pipeline.merge_prepared_candidates(arm, x86, repo=root)
    merged.apply(root)
    assert (root / "AGENTS.md").read_text(encoding="utf-8") == (
        "# Agent Guide\nkeep this body\n"
    )
    assert (root / "CLAUDE.md").is_symlink()
    assert (root / "CLAUDE.md").readlink() == Path("AGENTS.md")
    assert b"AGENTS.md" not in merged.patch


def test_merge_prepared_candidates_keeps_disjoint_linux_edits(tmp_path: Path) -> None:
    """Arm and x86 may extend Darwin in parallel when they do not clash."""
    root = tmp_path / "repo"
    init_update_workspace_repo(
        root,
        tracked_files={"shared.txt": "base\n", "keep.txt": "keep\n"},
    )
    arm = _candidate_with_file(
        root,
        "arm.txt",
        "arm\n",
        systems=("aarch64-darwin", "aarch64-linux"),
    )
    x86 = _candidate_with_file(
        root,
        "x86.txt",
        "x86\n",
        systems=("aarch64-darwin", "x86_64-linux"),
    )
    x86 = Candidate(
        base_tree=x86.base_tree,
        tree=x86.tree,
        targets=x86.targets,
        sources=x86.sources,
        systems=x86.systems,
        resolutions={"linux": ResolvedVersion(version="1")},
        prepared=True,
        patch=x86.patch,
    )
    merged = pipeline.merge_prepared_candidates(arm, x86, repo=root)
    assert merged.resolutions["linux"].version == "1"
    assert set(merged.systems) == {
        "aarch64-darwin",
        "aarch64-linux",
        "x86_64-linux",
    }
    assert merged.prepared
    merged.apply(root)
    assert (root / "arm.txt").read_text(encoding="utf-8") == "arm\n"
    assert (root / "x86.txt").read_text(encoding="utf-8") == "x86\n"
    assert (root / "keep.txt").read_text(encoding="utf-8") == "keep\n"


_OPENAI_VENDOR = "sha256-V7ZBn8uZ+oMF9HOT8Upao2rUDFpb7+bk77w7ODBdiO4="
_OPENAI_OLD_VENDOR = "sha256-h06DRGoNo7T6HMNQKg8WgyyxCbrWMVM8LJvfPkVHXPs="
_OTHER_VENDOR = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
_THIRD_VENDOR = "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC="
_OPENAI_ARM_DRV = "924w8bq2jgn13ygzxkjz4hss80xk75k2"
_OPENAI_X86_DRV = "j7ikqjdxpm48apk76ph7alvh8xrdac1r"


def _source_json(
    *,
    version: str = "v1.38.0",
    drv_hash: str = _OPENAI_ARM_DRV,
    vendor: str = _OPENAI_VENDOR,
    input_name: str = "openai-cli",
    **extra: object,
) -> str:
    """Persist-shaped per-package ``sources.json`` used by merge tests."""
    payload: dict[str, object] = {
        "drvHash": drv_hash,
        "hashes": [{"hash": vendor, "hashType": "vendorHash"}],
        "input": input_name,
        "version": version,
        **extra,
    }
    return (
        json.dumps(
            SourceEntry.model_validate(payload).to_dict(),
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def test_merge_prepared_candidates_rejects_file_conflicts(tmp_path: Path) -> None:
    """Shared generated files with different contents fail closed."""
    root = tmp_path / "repo"
    init_update_workspace_repo(root, tracked_files={"shared.txt": "base\n"})
    arm = _candidate_with_file(
        root,
        "shared.txt",
        "arm\n",
        systems=("aarch64-darwin", "aarch64-linux"),
    )
    x86 = _candidate_with_file(
        root,
        "shared.txt",
        "x86\n",
        systems=("aarch64-darwin", "x86_64-linux"),
    )
    with pytest.raises(ValueError, match="Conflicting candidate edits"):
        pipeline.merge_prepared_candidates(arm, x86, repo=root)
    with pytest.raises(ValueError, match="share a baseline"):
        pipeline.merge_prepared_candidates(
            arm,
            Candidate(
                base_tree="b" * 40,
                tree="b" * 40,
                targets=("example",),
                sources=(),
                systems=("aarch64-darwin", "x86_64-linux"),
                resolutions={},
                prepared=True,
                patch=b"",
            ),
            repo=root,
        )


def test_merge_prepared_candidates_unions_drv_hash_only_sources_json(
    tmp_path: Path,
) -> None:
    """#1266: parallel Linux prepares rewrote openai-cli drvHash only."""
    root = tmp_path / "repo"
    init_update_workspace_repo(
        root,
        tracked_files={
            "packages/openai-cli/sources.json": _source_json(
                version="v1.37.0",
                drv_hash="s4dcpz4xz6z89q4a8kjml53mqrvxbp11",
                vendor=_OPENAI_OLD_VENDOR,
            )
        },
    )
    arm = _candidate_with_file(
        root,
        "packages/openai-cli/sources.json",
        _source_json(drv_hash=_OPENAI_ARM_DRV),
        systems=("aarch64-darwin", "aarch64-linux"),
    )
    x86 = _candidate_with_file(
        root,
        "packages/openai-cli/sources.json",
        _source_json(drv_hash=_OPENAI_X86_DRV),
        systems=("aarch64-darwin", "x86_64-linux"),
    )
    merged = pipeline.merge_prepared_candidates(arm, x86, repo=root)
    merged.apply(root)
    entry = SourceEntry.model_validate_json(
        (root / "packages/openai-cli/sources.json").read_bytes()
    )
    assert entry.version == "v1.38.0"
    assert entry.input == "openai-cli"
    assert entry.drv_hash == _OPENAI_X86_DRV
    assert entry.hashes.primary_hash() == _OPENAI_VENDOR


def test_merge_prepared_candidates_unions_complementary_platform_hashes(
    tmp_path: Path,
) -> None:
    """Arm and x86 may each add their native hash to the same sources.json."""
    root = tmp_path / "repo"
    darwin = {
        "hashes": {"aarch64-darwin": _OPENAI_VENDOR},
        "version": "1.0.0",
    }
    init_update_workspace_repo(
        root,
        tracked_files={
            "overlays/demo.sources.json": json.dumps(darwin, indent=2) + "\n"
        },
    )
    arm_entry = SourceEntry.model_validate({
        "hashes": {
            **darwin["hashes"],
            "aarch64-linux": _OTHER_VENDOR,
        },
        "version": "1.0.0",
    })
    x86_entry = SourceEntry.model_validate({
        "hashes": {
            **darwin["hashes"],
            "x86_64-linux": _THIRD_VENDOR,
        },
        "version": "1.0.0",
    })
    arm = _candidate_with_file(
        root,
        "overlays/demo.sources.json",
        json.dumps(arm_entry.to_dict(), indent=2, sort_keys=True) + "\n",
        systems=("aarch64-darwin", "aarch64-linux"),
    )
    x86 = _candidate_with_file(
        root,
        "overlays/demo.sources.json",
        json.dumps(x86_entry.to_dict(), indent=2, sort_keys=True) + "\n",
        systems=("aarch64-darwin", "x86_64-linux"),
    )
    merged = pipeline.merge_prepared_candidates(arm, x86, repo=root)
    merged.apply(root)
    entry = SourceEntry.model_validate_json(
        (root / "overlays/demo.sources.json").read_bytes()
    )
    assert entry.hashes.mapping == {
        "aarch64-darwin": _OPENAI_VENDOR,
        "aarch64-linux": _OTHER_VENDOR,
        "x86_64-linux": _THIRD_VENDOR,
    }


def test_merge_prepared_candidates_rejects_incompatible_sources_json(
    tmp_path: Path,
) -> None:
    """Version or artifact-hash disagreements still fail closed."""
    root = tmp_path / "repo"
    init_update_workspace_repo(
        root,
        tracked_files={
            "packages/openai-cli/sources.json": _source_json(version="v1.37.0")
        },
    )
    arm = _candidate_with_file(
        root,
        "packages/openai-cli/sources.json",
        _source_json(version="v1.38.0"),
        systems=("aarch64-darwin", "aarch64-linux"),
    )
    x86 = _candidate_with_file(
        root,
        "packages/openai-cli/sources.json",
        _source_json(version="v1.39.0"),
        systems=("aarch64-darwin", "x86_64-linux"),
    )
    with pytest.raises(ValueError, match="Conflicting candidate edits"):
        pipeline.merge_prepared_candidates(arm, x86, repo=root)
    git(root, "reset", "--hard", "HEAD")
    hashed = _candidate_with_file(
        root,
        "packages/openai-cli/sources.json",
        _source_json(vendor=_OTHER_VENDOR),
        systems=("aarch64-darwin", "x86_64-linux"),
    )
    with pytest.raises(ValueError, match="Conflicting candidate edits"):
        pipeline.merge_prepared_candidates(arm, hashed, repo=root)


def test_merge_prepared_candidates_rejects_non_entry_json_conflicts(
    tmp_path: Path,
) -> None:
    """crate-sources and unparseable JSON keep the whole-file fail-closed path."""
    root = tmp_path / "repo"
    init_update_workspace_repo(
        root,
        tracked_files={
            "packages/demo/crate-sources.json": '{"crate":{"name":"old"}}\n'
        },
    )
    arm = _candidate_with_file(
        root,
        "packages/demo/crate-sources.json",
        '{"crate":{"name":"arm"}}\n',
        systems=("aarch64-darwin", "aarch64-linux"),
    )
    x86 = _candidate_with_file(
        root,
        "packages/demo/crate-sources.json",
        '{"crate":{"name":"x86"}}\n',
        systems=("aarch64-darwin", "x86_64-linux"),
    )
    with pytest.raises(ValueError, match="Conflicting candidate edits"):
        pipeline.merge_prepared_candidates(arm, x86, repo=root)


def test_merge_prepared_candidates_rejects_sources_delete_versus_edit(
    tmp_path: Path,
) -> None:
    """A delete on one platform and an edit on the other is still a conflict."""
    root = tmp_path / "repo"
    init_update_workspace_repo(
        root,
        tracked_files={
            "packages/openai-cli/sources.json": _source_json(version="v1.37.0")
        },
    )
    git(root, "rm", "--", "packages/openai-cli/sources.json")
    deleted = Candidate(
        base_tree=git(root, "rev-parse", "HEAD^{tree}").decode().strip(),
        tree=git(root, "write-tree").decode().strip(),
        targets=("example",),
        sources=("example",),
        systems=("aarch64-darwin", "aarch64-linux"),
        resolutions={},
        prepared=True,
        patch=git(
            root,
            "diff",
            "--cached",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            "--no-textconv",
            "HEAD",
            "--",
        ),
    )
    git(root, "reset", "--hard", "HEAD")
    x86 = _candidate_with_file(
        root,
        "packages/openai-cli/sources.json",
        _source_json(),
        systems=("aarch64-darwin", "x86_64-linux"),
    )
    with pytest.raises(ValueError, match="Conflicting candidate edits"):
        pipeline.merge_prepared_candidates(deleted, x86, repo=root)


@pytest.mark.parametrize(
    ("left_fields", "right_fields"),
    [
        ({"input": "openai-cli"}, {"input": "other-cli"}),
        ({"commit": "a" * 40}, {"commit": "b" * 40}),
        ({"electronVersion": "40.0.0"}, {"electronVersion": "41.0.0"}),
        (
            {"urls": {"upstream": "https://example.invalid/a"}},
            {"urls": {"upstream": "https://example.invalid/b"}},
        ),
        (
            {"pins": {"electronVersion": "40.0.0"}},
            {"pins": {"electronVersion": "41.0.0"}},
        ),
        (
            {"platformDrvHashes": {"aarch64-linux": "armdrv"}},
            {"platformDrvHashes": {"aarch64-linux": "x86drv"}},
        ),
    ],
)
def test_source_entries_compatible_rejects_scalar_and_mapping_conflicts(
    left_fields: dict[str, object],
    right_fields: dict[str, object],
) -> None:
    """Parallel prepares may not silently last-win identity or pin fields."""
    base = json.loads(_source_json())
    left = SourceEntry.model_validate({**base, **left_fields})
    right = SourceEntry.model_validate({**base, **right_fields})
    assert not pipeline._source_entries_compatible(left, right)


def test_source_entry_helpers_cover_parse_and_hash_edges() -> None:
    """Parse failures and hash-representation mismatches stay fail-closed."""
    assert pipeline._parse_source_entry(b"{not json") is None
    assert (
        pipeline._parse_source_entry(
            b'["sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="]'
        )
        is None
    )
    assert pipeline._parse_source_entry(b'{"hashes": "nope"}') is None
    assert pipeline._merged_package_sources("README.md", b"a", b"b") is None
    fake = HashCollection.FAKE_HASH_PREFIX
    left = SourceEntry.model_validate({
        "hashes": [{"hash": fake, "hashType": "vendorHash"}],
        "version": "1",
    })
    right = SourceEntry.model_validate({
        "hashes": [{"hash": _OPENAI_VENDOR, "hashType": "vendorHash"}],
        "version": "1",
    })
    assert pipeline._source_entries_compatible(left, right)
    mapping = SourceEntry.model_validate({
        "hashes": {"aarch64-linux": _OPENAI_VENDOR},
        "version": "1",
    })
    assert not pipeline._hash_collections_compatible(left.hashes, mapping.hashes)
    other_mapping = HashCollection.model_validate({"aarch64-linux": _OTHER_VENDOR})
    assert not pipeline._hash_collections_compatible(mapping.hashes, other_mapping)
    fake_mapping = HashCollection.model_validate({"aarch64-linux": fake})
    assert pipeline._hash_collections_compatible(fake_mapping, mapping.hashes)
    empty = HashCollection()
    assert pipeline._hashes_preserved(empty, empty)
    plain = SourceEntry.model_validate_json(_source_json().encode())
    with_url = plain.model_copy(
        update={"urls": {"upstream": "https://example.invalid/a"}}
    )
    assert pipeline._source_entries_compatible(plain, with_url)


def test_checkout_candidate_is_identity_of_head(tmp_path: Path) -> None:
    """Canary uses the branch tree; it does not invent a prepare patch."""
    root = tmp_path / "repo"
    init_update_workspace_repo(root, tracked_files={"keep.txt": "keep\n"})
    candidate = pipeline.checkout_candidate(root)
    tree = git(root, "rev-parse", "HEAD^{tree}").decode().strip()
    assert candidate.tree == tree
    assert candidate.base_tree == tree
    assert candidate.patch == b""
    assert candidate.prepared
    assert set(candidate.systems) == {
        "aarch64-darwin",
        "aarch64-linux",
        "x86_64-linux",
    }


def test_validate_command_canary_uses_checkout_without_previous(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dispatch canary validates the branch tree plus the current plan."""
    output = tmp_path / "artifacts" / "validation.json"
    warmup_plan = tmp_path / "warmup-plan.json"
    from lib.update.ci.warmup import (
        RootWarmupStats,
        ShardLocalBuildReport,
        WarmupPlan,
        write_warmup_plan,
    )

    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=("/nix/store/shared",),
            rustLayers=(("/nix/store/shared",),),
            outputDrvs={
                "/nix/store/shared": "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-shared.drv"
            },
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=1, missing=1, warmup=1, remaining=0
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
        ),
    )
    args = [
        "validate",
        "--scope",
        "rust-warmup",
        "--warmup-plan",
        str(warmup_plan),
        "--warmup-slot",
        "0",
        "--output",
        str(output),
    ]
    rejected = CliRunner().invoke(pipeline.app, args)
    assert rejected.exit_code != 0
    assert "previous candidate" in str(rejected.exception)
    monkeypatch.setenv("NIXCFG_CANARY", "true")
    monkeypatch.setattr(pipeline, "realize_warmup_outputs", lambda *_a, **_k: ())
    monkeypatch.setattr(pipeline, "import_warmup_drvs", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda _path: True)
    accepted = CliRunner().invoke(pipeline.app, args)
    assert accepted.exit_code == 0, accepted.output
    report = pipeline.ValidationReport.model_validate_json(output.read_bytes())
    assert report.gates == ()
    assert report.tree == pipeline.checkout_candidate(prepared_run[0]).tree


def test_merge_prepared_candidates_keeps_identical_shared_edits(
    tmp_path: Path,
) -> None:
    """Same-content overlap is not a conflict; one-sided deletes apply."""
    root = tmp_path / "repo"
    init_update_workspace_repo(
        root,
        tracked_files={"shared.txt": "base\n", "gone.txt": "gone\n"},
    )
    arm = _candidate_with_file(
        root,
        "shared.txt",
        "same\n",
        systems=("aarch64-darwin", "aarch64-linux"),
    )
    x86 = _candidate_with_file(
        root,
        "shared.txt",
        "same\n",
        systems=("aarch64-darwin", "x86_64-linux"),
    )
    merged = pipeline.merge_prepared_candidates(arm, x86, repo=root)
    merged.apply(root)
    assert (root / "shared.txt").read_text(encoding="utf-8") == "same\n"
    git(root, "reset", "--hard", "HEAD")
    git(root, "rm", "--", "gone.txt")
    deleted = Candidate(
        base_tree=arm.base_tree,
        tree=git(root, "write-tree").decode().strip(),
        targets=arm.targets,
        sources=arm.sources,
        systems=arm.systems,
        resolutions={"pkg": ResolvedVersion(version="1")},
        prepared=True,
        patch=git(
            root,
            "diff",
            "--cached",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            "--no-textconv",
            "HEAD",
            "--",
        ),
    )
    git(root, "reset", "--hard", "HEAD")
    gone = pipeline.merge_prepared_candidates(deleted, x86, repo=root)
    gone.apply(root)
    assert not (root / "gone.txt").exists()
    assert (root / "shared.txt").read_text(encoding="utf-8") == "same\n"


def test_merge_candidates_command_writes_outside_the_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    init_update_workspace_repo(root, tracked_files={"keep.txt": "keep\n"})
    arm = _candidate_with_file(
        root,
        "arm.txt",
        "arm\n",
        systems=("aarch64-darwin", "aarch64-linux"),
    )
    x86 = _candidate_with_file(
        root,
        "x86.txt",
        "x86\n",
        systems=("aarch64-darwin", "x86_64-linux"),
    )
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    output = tmp_path / "artifacts" / "merged.json"
    left.write_text(arm.model_dump_json(), encoding="utf-8")
    right.write_text(x86.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(pipeline, "get_repo_root", lambda: root)
    result = CliRunner().invoke(
        pipeline.app,
        [
            "merge-candidates",
            "--left",
            str(left),
            "--right",
            str(right),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    merged = Candidate.model_validate_json(output.read_bytes())
    assert set(merged.systems) == {
        "aarch64-darwin",
        "aarch64-linux",
        "x86_64-linux",
    }


def test_merge_prepared_candidates_rejects_incompatible_extensions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    init_update_workspace_repo(root, tracked_files={"keep.txt": "keep\n"})
    arm = _candidate_with_file(
        root,
        "arm.txt",
        "arm\n",
        systems=("aarch64-darwin", "aarch64-linux"),
    )
    x86_systems = ("aarch64-darwin", "x86_64-linux")
    with pytest.raises(ValueError, match="target selection"):
        pipeline.merge_prepared_candidates(
            arm,
            Candidate(
                base_tree=arm.base_tree,
                tree=arm.tree,
                targets=("other",),
                sources=arm.sources,
                systems=x86_systems,
                resolutions={},
                prepared=True,
                patch=arm.patch,
            ),
            repo=root,
        )
    with pytest.raises(ValueError, match="cannot be merged"):
        pipeline.merge_prepared_candidates(
            Candidate(
                base_tree=arm.base_tree,
                tree=arm.tree,
                targets=arm.targets,
                sources=arm.sources,
                systems=arm.systems,
                resolutions={},
                prepared=False,
                patch=arm.patch,
            ),
            Candidate(
                base_tree=arm.base_tree,
                tree=arm.tree,
                targets=arm.targets,
                sources=arm.sources,
                systems=x86_systems,
                resolutions={},
                prepared=True,
                patch=arm.patch,
            ),
            repo=root,
        )
    with pytest.raises(ValueError, match="disjoint platform"):
        pipeline.merge_prepared_candidates(
            arm,
            Candidate(
                base_tree=arm.base_tree,
                tree=arm.tree,
                targets=arm.targets,
                sources=arm.sources,
                systems=arm.systems,
                resolutions={},
                prepared=True,
                patch=arm.patch,
            ),
            repo=root,
        )
    with pytest.raises(ValueError, match="Conflicting resolution"):
        pipeline.merge_prepared_candidates(
            Candidate(
                base_tree=arm.base_tree,
                tree=arm.tree,
                targets=arm.targets,
                sources=arm.sources,
                systems=arm.systems,
                resolutions={"pkg": ResolvedVersion(version="1")},
                prepared=True,
                patch=arm.patch,
            ),
            Candidate(
                base_tree=arm.base_tree,
                tree=arm.tree,
                targets=arm.targets,
                sources=arm.sources,
                systems=x86_systems,
                resolutions={"pkg": ResolvedVersion(version="2")},
                prepared=True,
                patch=b"",
            ),
            repo=root,
        )


@pytest.mark.parametrize(
    ("systems", "prepared"),
    [((), True), (("aarch64-darwin", "aarch64-darwin"), True), ((), False)],
)
def test_incomplete_candidates_never_reach_validation(systems, prepared) -> None:
    candidate = Candidate(
        base_tree="a" * 40,
        tree="a" * 40,
        targets=(),
        sources=(),
        systems=systems,
        resolutions={},
        prepared=prepared,
        patch=b"",
    )
    with pytest.raises(ValueError, match="every configured system"):
        pipeline.validate_candidate(candidate)


def test_unsupported_builder_and_early_preparation_failure_are_explicit(
    prepared_run, monkeypatch
) -> None:
    _, _, state = prepared_run
    state["system"] = "unsupported"
    with pytest.raises(ValueError, match="No configured builder policy"):
        pipeline.prepare_candidate(())
    candidate = Candidate(
        base_tree="a" * 40,
        tree="a" * 40,
        targets=(),
        sources=(),
        systems=pipeline.supported_systems(),
        resolutions={},
        prepared=True,
        patch=b"",
    )
    with pytest.raises(ValueError, match="Unexpected validation platform"):
        pipeline.validate_candidate(candidate)
    state["system"] = "aarch64-darwin"

    async def fail(*_args, **_kwargs):
        return 1

    monkeypatch.setattr(pipeline.update_cli, "collect_run_outcome", fail)
    with pytest.raises(RuntimeError, match="before a candidate could be captured"):
        pipeline.prepare_candidate(())


@pytest.mark.parametrize("phase", ["packages", "roots"])
def test_incomplete_native_validation_cannot_issue_report(
    prepared_run, monkeypatch, tmp_path, phase
) -> None:
    """The CI command fails before writing certification evidence on incompleteness."""
    root, _, _state = prepared_run
    tree = git(root, "rev-parse", "HEAD^{tree}").decode().strip()
    candidate = Candidate(
        base_tree=tree,
        tree=tree,
        targets=(),
        sources=(),
        systems=pipeline.supported_systems(),
        resolutions={},
        prepared=True,
        patch=b"",
    )

    def incomplete(*_args, **_kwargs):
        def run(args, **_kwargs):
            raise subprocess.TimeoutExpired(
                args, 1, stderr="https://example.test/?token=synthetic-ci-secret"
            )

        return pipeline.validation._run_validation_command(
            ["nix", "build", ".#demo"],
            cwd=root,
            timeout=1,
            run=run,
            sleep=lambda _: pytest.fail("incomplete execution must not retry"),
        )

    monkeypatch.setattr(
        pipeline.validation,
        "validate_derivations",
        incomplete if phase == "packages" else lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(pipeline.validation, "validate_root_closures", incomplete)
    source = tmp_path / "candidate.json"
    source.write_text(candidate.model_dump_json())
    report = tmp_path / "report.json"
    result = CliRunner().invoke(
        pipeline.app,
        [
            "validate",
            "--candidate",
            str(source),
            "--output",
            str(report),
        ],
    )
    assert result.exit_code != 0
    assert isinstance(result.exception, pipeline.validation.ValidationIncompleteError)
    assert "synthetic-ci-secret" not in "".join(
        traceback.format_exception(result.exception)
    )
    assert not report.exists()
    with pytest.raises(ValueError, match="Validation reports"):
        pipeline.certified_patch(candidate, [])
