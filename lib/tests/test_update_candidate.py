"""Portable candidate behavior through real Git workspaces and updater phases."""

import json
import subprocess
import traceback
from dataclasses import dataclass
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lib.nix.models.flake_lock import FlakeLockNode
from lib.nix.models.sources import SourceEntry
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

    def warmup_realize(*_args: object, **_kwargs: object) -> tuple[()]:
        order.append("warmup")
        return ()

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", warmup_realize)
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
    packages = pipeline.validate_candidate(
        candidate, scope="packages", warmup_plan=warmup_plan
    )
    assert packages.gates == ("packages",)
    assert order == ["packages", "warmup"]
    closures = pipeline.validate_candidate(
        candidate,
        scope="closures",
        closure_budget_seconds=pipeline.HOSTED_DARWIN_CLOSURE_BUILD_BUDGET_SECONDS,
    )
    assert closures.gates == ("closures",)
    assert order == ["packages", "warmup", "reclaim", "roots"]
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
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(pipeline, "get_current_nix_platform", lambda: "aarch64-darwin")
    monkeypatch.setattr(pipeline.sys, "platform", "darwin")
    with pytest.raises(ValueError, match="warmup plan"):
        pipeline.validate_candidate(candidate, scope="packages")


def test_linux_ci_mocking_darwin_does_not_require_warmup_plan(
    prepared_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GITHUB_ACTIONS on Linux is not a hosted Darwin packages shard."""
    candidate = _candidate_for_scope(prepared_run)
    monkeypatch.setattr(
        pipeline.validation,
        "validate_derivations",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(pipeline, "get_current_nix_platform", lambda: "aarch64-darwin")
    monkeypatch.setattr(pipeline.sys, "platform", "linux")
    assert pipeline.validate_candidate(candidate, scope="packages").gates == (
        "packages",
    )


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
            "       > error[E0463]: can't find crate for `settings`"
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
