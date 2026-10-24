"""Portable candidate behavior through real Git workspaces and updater phases."""

import json
import subprocess
import traceback
from collections.abc import Iterable
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
    DerivationValidationRequest,
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


@pytest.fixture(autouse=True)
def _noop_realized_root_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    """Closure tests mock realization; they must not call real ``nix path-info``."""
    monkeypatch.setattr(
        pipeline.jobs, "remember_realized_installable_paths", lambda *_a, **_k: ()
    )


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


def test_pin_then_parallel_native_matches_serial_darwin_then_linux(
    prepared_run,
) -> None:
    """38137325241: Linux hashes the pin, not HEAD; merge equals today's serial tree."""
    root, operations, state = prepared_run
    state["system"] = "aarch64-darwin"
    darwin = pipeline.prepare_candidate(("example",))[0]
    state["system"] = "aarch64-linux"
    arm = pipeline.prepare_candidate(("example",), previous=darwin)[0]
    state["system"] = "x86_64-linux"
    x86 = pipeline.prepare_candidate(("example",), previous=darwin)[0]
    serial = pipeline.merge_prepared_candidates(arm, x86, repo=root)
    operations.clear()
    state["system"] = "x86_64-linux"
    pin_out = root.parent / "pin.json"
    pinned = CliRunner().invoke(
        pipeline.app,
        ["prepare", "--output", str(pin_out), "--pins-only", "example"],
    )
    assert pinned.exit_code == 0, pinned.output
    pin = Candidate.model_validate_json(pin_out.read_bytes())
    assert pin.systems == ()
    assert pin.prepared
    assert operations == ["resolve"]
    with pytest.raises(ValueError, match="cannot extend"):
        pipeline.prepare_candidate(("example",), previous=pin, pins_only=True)
    natives: list[Candidate] = []
    for system in pipeline.supported_systems():
        state["system"] = system
        natives.append(pipeline.prepare_candidate(("example",), previous=pin)[0])
    parallel = pipeline.fold_prepared_candidates(*natives, repo=root)
    assert parallel.tree == serial.tree
    assert set(parallel.systems) == set(serial.systems)
    with IsolatedUpdateWorkspace(root) as workspace:
        parallel.apply(workspace.root)
        result = SourceEntry.model_validate_json(
            (workspace.root / "packages/example/sources.json").read_bytes()
        )
    assert result.version == "2"
    assert set(result.hashes.mapping) == set(pipeline.supported_systems())


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


@pytest.mark.parametrize("hosted_darwin", [True, False])
def test_hosted_darwin_validates_root_closures_without_streaming_build_logs(
    prepared_run,
    monkeypatch: pytest.MonkeyPatch,
    hosted_darwin: bool,
) -> None:
    """Hosted macos-15 validates root closures but must not stream their build logs.

    The closure is still built on a hosted runner: only the ``-L`` log streaming
    is suppressed, because Cachix's daemon cannot upload a realized path while
    the runner streams 4000+ derivation logs (lib/update/ci/candidate.py). Both
    halves are pinned so neither "skip the closure" nor "always stream" can
    regress unnoticed.
    """
    _, _, state = prepared_run
    candidate = None
    for system in pipeline.supported_systems():
        state["system"] = system
        candidate, _ = pipeline.prepare_candidate(("example",), previous=candidate)
    assert candidate is not None
    order: list[str] = []
    streamed: list[bool] = []
    monkeypatch.setattr(pipeline.jobs.sys, "platform", "darwin")
    if hosted_darwin:
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    else:
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        monkeypatch.delenv("RUNNER_ENVIRONMENT", raising=False)
    monkeypatch.setattr(
        pipeline.jobs, "reclaim_hosted_store", lambda: order.append("reclaim")
    )
    monkeypatch.setattr(
        pipeline.validation,
        "validate_derivations",
        lambda *_args, **_kwargs: (),
    )

    def validate_roots(*, print_build_logs, **_kwargs):
        streamed.append(print_build_logs)
        order.append("roots")
        return ()

    monkeypatch.setattr(
        pipeline.validation,
        "validate_root_closures",
        validate_roots,
    )
    state["system"] = "aarch64-darwin"
    report = pipeline.validate_candidate(candidate)
    assert report.failures == ()
    assert order == ["reclaim", "roots"]
    assert streamed == [not hosted_darwin]


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
    assert receipt["store_paths"] == []
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
    assert receipt["store_paths"] == []


def test_cache_root_deps_records_substituted_linux_builder_outputs(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """39006154683: substituting the VM from gkze did not fire the push hook.

    Darwin closures then ``--max-jobs 0``'d the same drv and streamed 50 wbb.
    Recache must path-info the output and leave it for flush to push/pin.
    """
    candidate = _candidate_for_scope(prepared_run)
    drv = "/nix/store/6xczfl5hgl4yb7p9p997l6hmlwgpfhdj-nixos-disk-image.drv^*"
    output = "/nix/store/wy15yizd3i7z6bmg3llr43a2pflg9yyb-nixos-disk-image"
    remembered: list[tuple[str, ...]] = []

    def roots(**kwargs: object) -> tuple[()]:
        capture = kwargs.get("on_dependency_installables")
        assert callable(capture)
        capture((drv,))
        return ()

    monkeypatch.setattr(pipeline.validation, "validate_root_closures", roots)
    monkeypatch.setattr(
        pipeline.jobs, "drv_output_store_paths", lambda installables: (output,)
    )
    monkeypatch.setattr(
        pipeline.jobs,
        "remember_realized_store_paths",
        lambda paths: remembered.append(paths) or paths,
    )
    report = pipeline.cache_root_dependencies(candidate)
    assert report.failures == ()
    assert report.store_paths == (output,)
    assert remembered == [(output,)]
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(candidate.model_dump_json())
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
    assert receipt["store_paths"] == [output]


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
            tree=candidate.tree,
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


def test_rust_warmup_realizes_gitbutler_family_even_when_path_info_says_cached(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """38867194218: path-info said rust_but-* was in gkze; roots compiled it.

    39217658040: that others compile is keep_going so family-sized 39
    will-be-built does not abort at the default 32-drv bootstrap guard.
    """
    candidate = _candidate_for_scope(prepared_run)
    monkeypatch.setattr(
        pipeline.validation,
        "validate_derivations",
        lambda *_args, **_kwargs: (),
    )
    realized: list[tuple[object, object, object]] = []
    progress_calls: list[tuple[str, bool]] = []
    real_progress = pipeline._hosted_validation_progress
    but = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_but-core-0.0.0"
    but_drv = f"{but}.drv"

    def warmup_realize(paths: object, *_args: object, **kwargs: object) -> object:
        realized.append((
            paths,
            kwargs.get("substitute_only"),
            kwargs.get("keep_going"),
        ))
        if kwargs.get("substitute_only"):
            return (
                DerivationValidationFailure(
                    source="root-warmup",
                    installable=f"{but_drv}^*",
                    message="error: Cannot build gitbutler; NAR missing",
                ),
            )
        return ()

    def spy_progress(source: str, *, keep_going: bool = False) -> object:
        progress_calls.append((source, keep_going))
        return real_progress(source, keep_going=keep_going)

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", warmup_realize)
    monkeypatch.setattr(pipeline, "_hosted_validation_progress", spy_progress)
    monkeypatch.setattr(pipeline, "import_warmup_drvs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda _path: True)
    monkeypatch.setattr(pipeline, "rust_crate_input_drvs", lambda *_a, **_k: ())
    from lib.update.ci.warmup import (
        RootWarmupStats,
        ShardLocalBuildReport,
        WarmupPlan,
        write_warmup_plan,
    )

    gpui = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_gpui-1"
    gpui_drv = f"{gpui}.drv"
    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            tree=candidate.tree,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(but, gpui),
            rustLayers=((but, gpui),),
            outputDrvs={but: but_drv, gpui: gpui_drv},
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
    result = pipeline.validate_candidate(
        candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=0
    )
    assert result.failures == ()
    assert realized == [
        ((but_drv,), True, True),
        ((but_drv,), None, True),
    ]
    assert ("rust-warmup", True) in progress_calls


def test_rust_warmup_realizes_crane_deps_before_svh_family(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """crates.io rust_* populate the store before force-local rust_settings."""
    candidate = _candidate_for_scope(prepared_run)
    monkeypatch.setattr(
        pipeline.validation,
        "validate_derivations",
        lambda *_args, **_kwargs: (),
    )
    realized: list[tuple[object, object, object]] = []

    def warmup_realize(paths: object, *_args: object, **kwargs: object) -> tuple[()]:
        realized.append((
            paths,
            kwargs.get("chunk"),
            kwargs.get("force_local"),
        ))
        return ()

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", warmup_realize)
    monkeypatch.setattr(pipeline, "import_warmup_drvs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda _path: False)
    monkeypatch.setattr(pipeline, "assert_force_local_dry_run", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "compiler_input_drvs", lambda *_a, **_k: ())
    monkeypatch.setattr(pipeline, "rust_crate_input_drvs", lambda *_a, **_k: ())
    from lib.update.ci.warmup import (
        CRANE_DEP_CHUNK,
        RootWarmupStats,
        ShardLocalBuildReport,
        WarmupPlan,
        write_warmup_plan,
    )

    serde = "/nix/store/serdeaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_serde-1.0.229"
    settings = "/nix/store/2y7vj1wq5nz030asgn7rhipbcx5aya89-rust_settings-0.1.0"
    serde_drv = f"{serde}.drv"
    settings_drv = f"{settings}.drv"
    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            tree=candidate.tree,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(serde, settings),
            rustLayers=((serde,), (settings,)),
            outputDrvs={serde: serde_drv, settings: settings_drv},
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
    result = pipeline.validate_candidate(
        candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=0
    )
    assert result.failures == ()
    assert realized[0] == ((serde_drv,), CRANE_DEP_CHUNK, None)
    assert realized[-1] == ((settings_drv,), None, True)


def test_rust_warmup_skips_cached_stripe_without_gitbutler(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fully cached non-gitbutler stripe must not import or compile."""
    candidate = _candidate_for_scope(prepared_run)
    monkeypatch.setattr(
        pipeline.validation,
        "validate_derivations",
        lambda *_args, **_kwargs: (),
    )
    realized: list[object] = []
    imported: list[object] = []
    monkeypatch.setattr(
        pipeline,
        "realize_warmup_outputs",
        lambda paths, *_a, **_k: realized.append(paths) or (),
    )
    monkeypatch.setattr(
        pipeline,
        "import_warmup_drvs",
        lambda paths, *_a, **_k: imported.append(paths),
    )
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda _path: True)
    from lib.update.ci.warmup import (
        RootWarmupStats,
        ShardLocalBuildReport,
        WarmupPlan,
        write_warmup_plan,
    )

    serde = "/nix/store/serdeaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_serde-1.0.229"
    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            tree=candidate.tree,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(serde,),
            rustLayers=((serde,),),
            outputDrvs={serde: f"{serde}.drv"},
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=1, missing=0, warmup=1, remaining=0
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
    result = pipeline.validate_candidate(
        candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=0
    )
    assert result.failures == ()
    assert imported == []
    assert realized == []


def test_rust_warmup_empty_plan_canaries_gitbutler_package_graph(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """38867194218 rustLayers=[] still instantiates packages.gitbutler."""
    candidate = _candidate_for_scope(prepared_run)
    monkeypatch.setattr(
        pipeline.validation,
        "validate_derivations",
        lambda *_args, **_kwargs: (),
    )
    realized: list[tuple[object, object]] = []
    but = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_but-core-0.0.0"
    but_drv = f"{but}.drv"

    def warmup_realize(paths: object, *_args: object, **kwargs: object) -> object:
        realized.append((paths, kwargs.get("substitute_only")))
        if kwargs.get("substitute_only"):
            return (
                DerivationValidationFailure(
                    source="root-warmup",
                    installable=f"{but_drv}^*",
                    message="error: Cannot build gitbutler; NAR missing",
                ),
            )
        return ()

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", warmup_realize)
    monkeypatch.setattr(pipeline, "import_warmup_drvs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda _path: True)
    monkeypatch.setattr(pipeline, "rust_crate_input_drvs", lambda *_a, **_k: ())
    monkeypatch.setattr(
        pipeline,
        "gitbutler_family_slot_from_package",
        lambda _root, _slot: ((but,), {but: but_drv}),
    )
    from lib.update.ci.warmup import (
        RootWarmupStats,
        ShardLocalBuildReport,
        WarmupPlan,
        write_warmup_plan,
    )

    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            tree=candidate.tree,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(),
            rustLayers=(),
            outputDrvs={},
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=1, missing=0, warmup=0, remaining=0
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
    result = pipeline.validate_candidate(
        candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=0
    )
    assert result.failures == ()
    assert realized == [((but_drv,), True), ((but_drv,), None)]


def test_rust_warmup_skips_gitbutler_compile_when_substitute_hits(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real NAR in gkze must not recompile the GitButler family."""
    candidate = _candidate_for_scope(prepared_run)
    monkeypatch.setattr(
        pipeline.validation,
        "validate_derivations",
        lambda *_args, **_kwargs: (),
    )
    realized: list[tuple[object, object]] = []

    def warmup_realize(paths: object, *_args: object, **kwargs: object) -> tuple[()]:
        realized.append((paths, kwargs.get("substitute_only")))
        return ()

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", warmup_realize)
    monkeypatch.setattr(pipeline, "import_warmup_drvs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda _path: True)
    but = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_but-core-0.0.0"
    but_drv = f"{but}.drv"
    from lib.update.ci.warmup import (
        RootWarmupStats,
        ShardLocalBuildReport,
        WarmupPlan,
        write_warmup_plan,
    )

    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            tree=candidate.tree,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(but,),
            rustLayers=((but,),),
            outputDrvs={but: but_drv},
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=1, missing=0, warmup=1, remaining=0
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
    result = pipeline.validate_candidate(
        candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=0
    )
    assert result.failures == ()
    assert realized == [((but_drv,), True)]


def test_cascade_warmup_realizes_uncached_stripe_per_derivation(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """38130420607: shared non-rust cascade is one drv at a time, skip-if-in-gkze."""
    candidate = _candidate_for_scope(prepared_run)
    monkeypatch.setattr(
        pipeline.validation,
        "validate_derivations",
        lambda *_args, **_kwargs: (),
    )
    realized: list[tuple[object, object]] = []

    def warmup_realize(paths: object, *_args: object, **kwargs: object) -> tuple[()]:
        realized.append((paths, kwargs.get("chunk"), kwargs.get("keep_going")))
        return ()

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", warmup_realize)
    monkeypatch.setattr(pipeline, "import_warmup_drvs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        pipeline, "filter_cascade_realize_drvs", lambda drvs, **_kwargs: drvs
    )
    cached = "/nix/store/cccccccccccccccccccccccccccccccc-coreutils-9.10"
    missing = "/nix/store/dddddddddddddddddddddddddddddddd-libiconv-1"
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda path: path == cached)
    from lib.update.ci.warmup import (
        RootWarmupStats,
        ShardLocalBuildReport,
        WarmupPlan,
        write_warmup_plan,
    )

    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            tree=candidate.tree,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(),
            rustLayers=(),
            cascadeOutputs=(cached, missing),
            cascadeLayers=(
                (cached,),
                (missing,),
            ),
            outputDrvs={cached: f"{cached}.drv", missing: f"{missing}.drv"},
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=2, missing=2, warmup=0, remaining=0
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
        candidate, scope="cascade-warmup", warmup_plan=warmup_plan, warmup_slot=0
    )
    assert report.gates == ()
    # Min-load (#243): unit-weight cached path takes slot 0 and is skipped
    # in gkze; the missing path goes to slot 1 instead of stacking on 0.
    assert realized == []
    assigned = pipeline.validate_candidate(
        candidate, scope="cascade-warmup", warmup_plan=warmup_plan, warmup_slot=1
    )
    assert assigned.failures == ()
    assert realized == [((f"{missing}.drv",), 1, True)]
    with pytest.raises(ValueError, match="cascade-warmup requires a warmup plan"):
        pipeline.validate_candidate(candidate, scope="cascade-warmup")
    with pytest.raises(ValueError, match="cascade-warmup requires a warmup plan"):
        pipeline._realize_requested_warmup(
            "cascade-warmup", None, None, flake_root=tmp_path, expected_tree="a" * 40
        )
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(candidate.model_dump_json())
    missing_cli = CliRunner().invoke(
        pipeline.app,
        [
            "validate",
            "--candidate",
            str(candidate_path),
            "--output",
            str(tmp_path / "validation.json"),
            "--scope",
            "cascade-warmup",
        ],
    )
    assert missing_cli.exit_code != 0
    assert "cascade-warmup requires --warmup-plan" in str(missing_cli.exception)
    slot_on_packages = CliRunner().invoke(
        pipeline.app,
        [
            "validate",
            "--candidate",
            str(candidate_path),
            "--output",
            str(tmp_path / "validation.json"),
            "--scope",
            "packages",
            "--warmup-slot",
            "0",
        ],
    )
    assert slot_on_packages.exit_code != 0
    assert "--warmup-slot is only valid" in str(slot_on_packages.exception)


def test_cascade_canary_realizes_buzz_onnxruntime_once(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """38394692516: canary publishes buzz-onnxruntime; stripe slots skip it."""
    candidate = _candidate_for_scope(prepared_run)
    monkeypatch.setattr(
        pipeline.validation,
        "validate_derivations",
        lambda *_args, **_kwargs: (),
    )
    realized: list[tuple[object, object]] = []

    def warmup_realize(paths: object, *_args: object, **kwargs: object) -> tuple[()]:
        realized.append((paths, kwargs.get("chunk"), kwargs.get("keep_going")))
        return ()

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", warmup_realize)
    monkeypatch.setattr(pipeline, "import_warmup_drvs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        pipeline, "filter_cascade_realize_drvs", lambda drvs, **_kwargs: drvs
    )
    onnx = "/nix/store/4jjnjbsw4a0ysny10x9p57r057k25nhz-buzz-onnxruntime-1.27.0"
    other = "/nix/store/dddddddddddddddddddddddddddddddd-libiconv-1"
    cached = "/nix/store/cccccccccccccccccccccccccccccccc-coreutils-9.10"
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda path: path == cached)
    from lib.update.ci.warmup import (
        RootWarmupStats,
        ShardLocalBuildReport,
        WarmupPlan,
        write_warmup_plan,
    )

    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            tree=candidate.tree,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(),
            rustLayers=(),
            cascadeOutputs=(cached, onnx, other),
            cascadeLayers=((cached, onnx), (other,)),
            outputDrvs={
                cached: f"{cached}.drv",
                onnx: f"{onnx}.drv",
                other: f"{other}.drv",
            },
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=3, missing=3, warmup=0, remaining=0
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
        candidate, scope="cascade-canary", warmup_plan=warmup_plan
    )
    assert report.gates == ()
    assert realized == [((f"{onnx}.drv",), 1, True)]
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda path: path == onnx)
    skipped = pipeline.validate_candidate(
        candidate, scope="cascade-canary", warmup_plan=warmup_plan
    )
    assert skipped.failures == ()
    assert realized == [((f"{onnx}.drv",), 1, True)]
    with pytest.raises(ValueError, match="cascade-canary requires a warmup plan"):
        pipeline.validate_candidate(candidate, scope="cascade-canary")
    with pytest.raises(ValueError, match="cascade-canary requires a warmup plan"):
        pipeline._realize_requested_warmup(
            "cascade-canary", None, None, flake_root=tmp_path, expected_tree="a" * 40
        )
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(candidate.model_dump_json())
    missing_cli = CliRunner().invoke(
        pipeline.app,
        [
            "validate",
            "--candidate",
            str(candidate_path),
            "--output",
            str(tmp_path / "validation.json"),
            "--scope",
            "cascade-canary",
        ],
    )
    assert missing_cli.exit_code != 0
    assert "cascade-canary requires --warmup-plan" in str(missing_cli.exception)


def test_fod_canary_realizes_darwin_fods_and_stops_on_hash_mismatch(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """38739788947: two Darwin hosts realize pnpm-deps/DMGs before rust-warmup."""
    candidate = _candidate_for_scope(prepared_run)
    monkeypatch.setattr(
        pipeline.validation,
        "validate_derivations",
        lambda *_args, **_kwargs: (),
    )
    seen: list[str] = []
    mismatch = DerivationValidationFailure(
        source="fod-canary",
        installable="path:.#pkgs.aarch64-darwin.bb.pnpmDeps",
        message=(
            "error: hash mismatch in fixed-output derivation "
            "'/nix/store/qbvbj2w3qxhn5c0x43f929k28plr1r8s-bb-pnpm-deps.drv'"
        ),
    )

    def realize(
        requests: Iterable[DerivationValidationRequest],
        *_args: object,
        **_kwargs: object,
    ) -> tuple[object, ...]:
        installable = next(iter(requests)).installable
        seen.append(installable)
        if "bb.pnpmDeps" in installable:
            return (mismatch,)
        return ()

    monkeypatch.setattr(pipeline.validation, "validate_derivation_requests", realize)
    report = pipeline.validate_candidate(candidate, scope="fod-canary")
    assert report.gates == ()
    assert report.failures == (mismatch,)
    assert seen == ["path:.#pkgs.aarch64-darwin.bb.pnpmDeps"]
    seen.clear()

    def succeed(
        requests: Iterable[DerivationValidationRequest],
        *_args: object,
        **_kwargs: object,
    ) -> tuple[()]:
        seen.append(next(iter(requests)).installable)
        return ()

    monkeypatch.setattr(pipeline.validation, "validate_derivation_requests", succeed)
    ok = pipeline.validate_candidate(candidate, scope="fod-canary")
    assert ok.failures == ()
    assert seen == [
        "path:.#pkgs.aarch64-darwin.bb.pnpmDeps",
        "path:.#pkgs.aarch64-darwin.emdash.pnpmDeps",
        "path:.#pkgs.aarch64-darwin.comet.src",
    ]


def test_leaf_prebuild_realizes_heavy_darwin_leaves_without_gates(
    prepared_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    """39166099302: cascade slots 1 and 2 both compiled zeron; build it once early."""
    candidate = _candidate_for_scope(prepared_run)
    monkeypatch.setattr(
        pipeline.validation,
        "validate_derivations",
        lambda *_args, **_kwargs: pytest.fail("leaf-prebuild is not a package gate"),
    )
    failure = DerivationValidationFailure(
        source="leaf-prebuild",
        installable="path:.#pkgs.aarch64-darwin.zeron",
        message="builder for zeron failed",
    )
    seen: list[tuple[str, str, str]] = []

    def realize(
        requests: Iterable[DerivationValidationRequest],
        *_args: object,
        **_kwargs: object,
    ) -> tuple[DerivationValidationFailure, ...]:
        seen.extend(
            (request.source, request.installable, request.mode) for request in requests
        )
        return (failure,)

    monkeypatch.setattr(pipeline.validation, "validate_derivation_requests", realize)
    report = pipeline.validate_candidate(candidate, scope="leaf-prebuild")
    assert report.gates == ()
    assert report.failures == (failure,)
    assert seen == [("leaf-prebuild", "path:.#pkgs.aarch64-darwin.zeron", "build")]


def test_cascade_warmup_skips_umbrella_over_shard_ceiling(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """38191092497: linear-cli-jsr-manifest.tsv 1297 will-be-built must not abort the stripe."""
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
    leaf = "/nix/store/dddddddddddddddddddddddddddddddd-libiconv-1"
    umbrella = "/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-linear-cli-jsr-manifest.tsv"
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda _path: False)
    from lib.update.ci import warmup as warmup_mod
    from lib.update.ci.warmup import (
        RootWarmupStats,
        ShardLocalBuildReport,
        WarmupPlan,
        write_warmup_plan,
    )

    def fake_count(drv: str, **_kwargs: object) -> int:
        return 1297 if "linear-cli-jsr-manifest" in drv else 2

    monkeypatch.setattr(warmup_mod, "dry_run_will_be_built_count", fake_count)
    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            tree=candidate.tree,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(),
            rustLayers=(),
            cascadeOutputs=(leaf, umbrella),
            cascadeLayers=((leaf, umbrella),),
            outputDrvs={leaf: f"{leaf}.drv", umbrella: f"{umbrella}.drv"},
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=2, missing=2, warmup=0, remaining=0
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
        candidate, scope="cascade-warmup", warmup_plan=warmup_plan, warmup_slot=0
    )
    assert report.gates == ()
    assert realized == [(f"{leaf}.drv",)]


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
            tree=candidate.tree,
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


def test_realize_rust_warmup_others_is_noop_without_drvs(tmp_path: Path) -> None:
    assert pipeline._realize_rust_warmup_others((), flake_root=tmp_path) == ()


def test_rust_warmup_retries_rusty_v8_after_others_hash_mismatch(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """39234076105 slot 4: retry rusty_v8 FOD, then re-realize rust_v8-goose."""
    candidate = _candidate_for_scope(prepared_run)
    monkeypatch.setattr(
        pipeline.validation,
        "validate_derivations",
        lambda *_args, **_kwargs: (),
    )
    rusty = "/nix/store/az1hmrm8app3kdcn79cxlad1z9zg7s6g-rusty_v8-dbb64c2.drv"
    goose = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_v8-goose-145.0.0"
    goose_drv = f"{goose}.drv"
    mismatch = DerivationValidationFailure(
        source="root-warmup",
        installable=f"{goose_drv}^*",
        message=f"error: hash mismatch in fixed-output derivation '{rusty}':",
    )
    realized: list[tuple[str, object]] = []
    warmup_calls = {"n": 0}

    def warmup_realize(
        paths: object, *_args: object, **_kwargs: object
    ) -> tuple[object, ...]:
        realized.append(("warmup", paths))
        warmup_calls["n"] += 1
        if warmup_calls["n"] == 1:
            return (mismatch,)
        return ()

    def fod_realize(paths: object, *_args: object, **_kwargs: object) -> tuple[()]:
        realized.append(("source", paths))
        return ()

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", warmup_realize)
    monkeypatch.setattr(pipeline, "realize_source_fetch_drvs", fod_realize)
    monkeypatch.setattr(pipeline, "import_warmup_drvs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda _path: False)
    monkeypatch.setattr(pipeline, "rust_crate_input_drvs", lambda *_args, **_kwargs: ())
    from lib.update.ci.warmup import (
        RootWarmupStats,
        ShardLocalBuildReport,
        WarmupPlan,
        write_warmup_plan,
    )

    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            tree=candidate.tree,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(goose,),
            rustLayers=((goose,),),
            outputDrvs={goose: goose_drv},
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
    assert report.failures == ()
    assert realized == [
        ("warmup", (goose_drv,)),
        ("source", (rusty,)),
        ("warmup", (goose_drv,)),
    ]


def test_rust_warmup_realizes_rusty_v8_family_on_exactly_one_slot(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """39912925016: two slots each compiled rust_pctx_code_execution_runtime.

    Slot 0's local copy had a different SVH than the one Cachix
    rust_pctx_executor was built against, and rust_goose E0463'd.
    """
    candidate = _candidate_for_scope(prepared_run)
    monkeypatch.setattr(
        pipeline.validation, "validate_derivations", lambda *_args, **_kwargs: ()
    )
    native = "/nix/store/11111111111111111111111111111111-goose-cli-v8-native-dbb64c2"
    v8 = "/nix/store/22222222222222222222222222222222-rust_v8-goose-145.0.0"
    runtime = (
        "/nix/store/44444444444444444444444444444444-"
        "rust_pctx_code_execution_runtime-0.2.2"
    )
    executor = "/nix/store/55555555555555555555555555555555-rust_pctx_executor-0.2.2"
    goose = "/nix/store/66666666666666666666666666666666-rust_goose-1.55.0"
    family = (native, v8, runtime, executor, goose)
    filler = tuple(f"/nix/store/{index:032x}-rust_filler-{index}" for index in range(8))
    realized: list[str] = []

    def warmup_realize(paths: object, *_args: object, **_kwargs: object) -> tuple[()]:
        assert isinstance(paths, tuple)
        realized.extend(paths)
        return ()

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", warmup_realize)
    monkeypatch.setattr(pipeline, "import_warmup_drvs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda _path: False)
    monkeypatch.setattr(pipeline, "rust_crate_input_drvs", lambda *_args, **_kwargs: ())
    from lib.update.ci.warmup import (
        RootWarmupStats,
        ShardLocalBuildReport,
        WarmupPlan,
        write_warmup_plan,
    )

    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            tree=candidate.tree,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=tuple(sorted((*family, *filler))),
            # Plain round-robin would split the family over slots 0, 1, and 2.
            rustLayers=(
                (native, *filler[:5]),
                (filler[5], v8),
                (runtime,),
                (filler[6], filler[7], executor),
                (goose,),
            ),
            rustyV8Family=tuple(sorted(family)),
            outputDrvs={path: f"{path}.drv" for path in (*family, *filler)},
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=13, missing=13, warmup=13, remaining=0
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
    per_slot: list[frozenset[str]] = []
    for slot in range(5):
        realized.clear()
        report = pipeline.validate_candidate(
            candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=slot
        )
        assert report.failures == ()
        per_slot.append(frozenset(realized))
    family_drvs = frozenset(f"{path}.drv" for path in family)
    owners = [slot for slot, drvs in enumerate(per_slot) if drvs & family_drvs]
    assert len(owners) == 1
    assert family_drvs <= per_slot[owners[0]]


def test_rust_warmup_keeps_rusty_v8_failure_when_fod_retry_misses(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second tainted rusty_v8 hash must not proceed into rust_v8-goose."""
    candidate = _candidate_for_scope(prepared_run)
    monkeypatch.setattr(
        pipeline.validation,
        "validate_derivations",
        lambda *_args, **_kwargs: (),
    )
    rusty = "/nix/store/az1hmrm8app3kdcn79cxlad1z9zg7s6g-rusty_v8-dbb64c2.drv"
    goose = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_v8-goose-145.0.0"
    goose_drv = f"{goose}.drv"
    mismatch = DerivationValidationFailure(
        source="root-warmup",
        installable=f"{goose_drv}^*",
        message=f"error: hash mismatch in fixed-output derivation '{rusty}':",
    )
    realized: list[tuple[str, object]] = []

    def warmup_realize(
        paths: object, *_args: object, **_kwargs: object
    ) -> tuple[DerivationValidationFailure, ...]:
        realized.append(("warmup", paths))
        return (mismatch,)

    def fod_realize(
        paths: object, *_args: object, **_kwargs: object
    ) -> tuple[DerivationValidationFailure, ...]:
        realized.append(("source", paths))
        return (mismatch,)

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", warmup_realize)
    monkeypatch.setattr(pipeline, "realize_source_fetch_drvs", fod_realize)
    monkeypatch.setattr(pipeline, "import_warmup_drvs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda _path: False)
    monkeypatch.setattr(pipeline, "rust_crate_input_drvs", lambda *_args, **_kwargs: ())
    from lib.update.ci.warmup import (
        RootWarmupStats,
        ShardLocalBuildReport,
        WarmupPlan,
        write_warmup_plan,
    )

    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            tree=candidate.tree,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(goose,),
            rustLayers=((goose,),),
            outputDrvs={goose: goose_drv},
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
    assert report.failures == (mismatch,)
    assert realized == [("warmup", (goose_drv,)), ("source", (rusty,))]


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
    other = "/nix/store/serdeaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_serde-1.0.229"
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
            tree=candidate.tree,
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
    assert [force for _paths, _logs, force, _sub in realized] == [False, True]


def test_rust_warmup_peels_workspace_svh_before_others(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """38121661811 slots 1/2: do not compile feature_flags before settings."""
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

    settings = "/nix/store/hwlj8la2ply5al3dhkz0d84z4rj3j61m-rust_settings-0.1.0"
    flags = "/nix/store/0anpp4kml0bzvpjm2i4ykqgdbl00rn23-rust_feature_flags-0.1.0"
    serde = "/nix/store/serdeaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_serde-1.0.229"
    settings_drv = f"{settings}.drv"
    flags_drv = f"{flags}.drv"
    serde_drv = f"{serde}.drv"
    monkeypatch.setattr(
        pipeline,
        "rust_crate_input_drvs",
        lambda _parents, _crates: (settings_drv,),
    )
    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            tree=candidate.tree,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(serde, flags, settings),
            rustLayers=((serde,), (flags,), (settings,)),
            outputDrvs={
                serde: serde_drv,
                flags: flags_drv,
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
    assert force_local == [(settings_drv, flags_drv)]
    others = [paths for paths, _logs, force, _sub in realized if not force]
    assert others == [(serde_drv,)]
    assert [force for _paths, _logs, force, _sub in realized] == [False, True]


def test_rust_warmup_settings_json_force_locals_tree_sitter(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1270 canary: do not compile rust_settings_json against Cachix tree_sitter."""
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

    settings_json = (
        "/nix/store/jsonaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_settings_json-0.1.0"
    )
    tree_sitter = "/nix/store/1zsfiw8m72a6ql2wx3f5bpq3mn7vnw77-rust_tree-sitter-0.27.0"
    settings_json_drv = f"{settings_json}.drv"
    tree_sitter_drv = f"{tree_sitter}.drv"

    def crate_inputs(_parents: object, crates: object, **_k: object) -> tuple[str, ...]:
        names = tuple(crates) if isinstance(crates, tuple) else ()
        if "tree-sitter" in names:
            return (tree_sitter_drv,)
        return ()

    monkeypatch.setattr(pipeline, "rust_crate_input_drvs", crate_inputs)
    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            tree=candidate.tree,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(settings_json,),
            rustLayers=((settings_json,),),
            outputDrvs={settings_json: settings_json_drv},
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
    assert report.failures == ()
    force_local = [paths for paths, _logs, force, _sub in realized if force]
    assert force_local == [(settings_json_drv, tree_sitter_drv)]


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
            tree=candidate.tree,
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
    rustc_darwin = "/nix/store/l2nj43cyy37grnkh3m8h4jzhpsx8yp8b-rustc-1.98.1-aarch64-apple-darwin.drv"
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
            installable=f"{rustc_darwin}^*",
            message="error: Cannot build '/nix/store/...-rustc-1.98.1-aarch64-apple-darwin.drv'.",
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
    )) == (cpio, unknown, rustc_darwin)


def test_deferred_compiler_rust_miss_skips_toolchain() -> None:
    """38088756238: lsp-types / but-* --max-jobs 0 misses are not rustc."""
    lsp_types = "/nix/store/v11pzry7z3p0d8pvkhqiclf5h2q0knqb-rust_lsp-types-0.95.1.drv"
    but_core = "/nix/store/akcscbhmgl5vwzznyazlglrjswbs27dk-rust_but-core-0.0.0.drv"
    rustc = "/nix/store/cccccccccccccccccccccccccccccccc-rustc-1.98.1.drv"
    stdenv = "/nix/store/1gx3hvygaaaaaaaaaaaaaaaaaaaaaaaa-stdenv-darwin.drv"
    rust_miss = DerivationValidationFailure(
        source="root-warmup",
        installable=f"{lsp_types}^*",
        message="these 1 derivations will be built:",
    )
    but_miss = DerivationValidationFailure(
        source="root-warmup",
        installable=f"{but_core}^*",
        message="error: Cannot build '/nix/store/...-rust_but-core-0.0.0.drv'.",
    )
    rustc_miss = DerivationValidationFailure(
        source="root-warmup",
        installable=f"{rustc}^*",
        message="error: Cannot build '/nix/store/...-rustc-1.98.1.drv'.",
    )
    stdenv_miss = DerivationValidationFailure(
        source="root-warmup",
        installable=f"{stdenv}^*",
        message="error: Cannot build '/nix/store/...-stdenv-darwin.drv'.",
    )
    assert pipeline._is_deferred_compiler_rust_miss(rust_miss)
    assert pipeline._is_deferred_compiler_rust_miss(but_miss)
    assert not pipeline._is_deferred_compiler_rust_miss(rustc_miss)
    assert not pipeline._is_deferred_compiler_rust_miss(stdenv_miss)


def test_deferred_compiler_rust_drvs_keep_probe_order() -> None:
    """38231309175: compile rust_xet-runtime before rust_xet-client."""
    runtime = "/nix/store/r9pr51ll4a8b9m5yavl6njzanbvbilmq-rust_xet-runtime-1.6.0.drv"
    client = "/nix/store/i60xdbbjfssbgmc8vv9pi2zrv21mk5l7-rust_xet-client-1.6.0.drv"
    rustc = "/nix/store/cccccccccccccccccccccccccccccccc-rustc-1.98.1.drv"
    assert pipeline._deferred_compiler_rust_drvs((
        DerivationValidationFailure(
            source="root-warmup",
            installable=f"{runtime}^*",
            message="error: Cannot build rust_xet-runtime.",
        ),
        DerivationValidationFailure(
            source="root-warmup",
            installable=f"{rustc}^*",
            message="these 406 derivations will be built:",
        ),
        DerivationValidationFailure(
            source="root-warmup",
            installable=f"{client}^*",
            message="these 406 derivations will be built:",
        ),
        DerivationValidationFailure(
            source="root-warmup",
            installable=f"{runtime}^*",
            message="duplicate probe",
        ),
    )) == (runtime, client)


def test_realize_compiler_substitutes_compiles_deferred_rust_leaves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """39387963366: probe rustc only; keep-going rust_* leaves in crane chunks."""
    monkeypatch.setattr(pipeline.jobs, "record_runner_storage", lambda *_a, **_k: {})
    runtime = "/nix/store/r9pr51ll4a8b9m5yavl6njzanbvbilmq-rust_xet-runtime-1.6.0.drv"
    client = "/nix/store/i60xdbbjfssbgmc8vv9pi2zrv21mk5l7-rust_xet-client-1.6.0.drv"
    rustc = "/nix/store/xbq69m0caaaaaaaaaaaaaaaaaaaaaaaa-rustc-1.98.1.drv"
    calls: list[tuple[object, bool, bool, object]] = []

    def warmup_realize(paths: object, *_args: object, **kwargs: object) -> object:
        calls.append((
            paths,
            bool(kwargs.get("substitute_only")),
            bool(kwargs.get("keep_going")),
            kwargs.get("chunk"),
        ))
        if kwargs.get("substitute_only"):
            return ()
        return ()

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", warmup_realize)
    assert (
        pipeline._realize_compiler_substitutes(
            (rustc, runtime, client), flake_root=tmp_path
        )
        == ()
    )
    assert calls[0] == ((rustc,), True, False, None)
    assert calls[1] == ((runtime, client), False, True, pipeline.CRANE_DEP_CHUNK)


def test_realize_compiler_substitutes_keep_going_only_rust_leaves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No rustc in the substitute set means skip --max-jobs 0 entirely."""
    monkeypatch.setattr(pipeline.jobs, "record_runner_storage", lambda *_a, **_k: {})
    serde = "/nix/store/serdeaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_serde-1.0.229.drv"
    calls: list[tuple[object, bool]] = []

    def warmup_realize(paths: object, *_args: object, **kwargs: object) -> object:
        calls.append((paths, bool(kwargs.get("substitute_only"))))
        return ()

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", warmup_realize)
    assert pipeline._realize_compiler_substitutes((serde,), flake_root=tmp_path) == ()
    assert calls == [((serde,), False)]


def test_realize_compiler_substitutes_keeps_rustc_miss_without_compiling_leaves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rustc --max-jobs 0 miss must not keep-going cranelift leaves."""
    monkeypatch.setattr(pipeline.jobs, "record_runner_storage", lambda *_a, **_k: {})
    rustc = "/nix/store/xbq69m0caaaaaaaaaaaaaaaaaaaaaaaa-rustc-1.98.1.drv"
    cranelift = (
        "/nix/store/2nlfdbacyjxij6mbskq39pp97z528q33-"
        "rust_cranelift-codegen-shared-0.135.1.drv"
    )
    rustc_fail = DerivationValidationFailure(
        source="root-warmup",
        installable=f"{rustc}^*",
        message="error: Cannot build '/nix/store/...-rustc-1.98.1.drv'.",
    )
    calls: list[object] = []

    def warmup_realize(paths: object, *_args: object, **kwargs: object) -> object:
        calls.append((paths, bool(kwargs.get("substitute_only"))))
        if kwargs.get("substitute_only"):
            return (rustc_fail,)
        msg = "must not compile rust leaves after a rustc miss"
        raise AssertionError(msg)

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", warmup_realize)
    assert pipeline._realize_compiler_substitutes(
        (rustc, cranelift), flake_root=tmp_path
    ) == (rustc_fail,)
    assert calls == [((rustc,), True)]


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
            tree=candidate.tree,
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
            tree=candidate.tree,
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


def test_compiler_rust_cache_miss_still_force_locals_family(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """38088756238: rust_lsp-types --max-jobs 0 miss compiles, then force-local."""
    candidate = _candidate_for_scope(prepared_run)
    monkeypatch.setattr(
        pipeline.validation,
        "validate_derivations",
        lambda *_args, **_kwargs: (),
    )
    realized: list[tuple[object, bool, bool, bool]] = []
    rustc_drv = "/nix/store/xbq69m0caaaaaaaaaaaaaaaaaaaaaaaa-rustc-1.98.1.drv"
    lsp_drv = "/nix/store/v11pzry7z3p0d8pvkhqiclf5h2q0knqb-rust_lsp-types-0.95.1.drv"
    lsp_fail = DerivationValidationFailure(
        source="root-warmup",
        installable=f"{lsp_drv}^*",
        message="these 1 derivations will be built:",
    )

    def warmup_realize(paths: object, *_args: object, **kwargs: object) -> object:
        realized.append((
            paths,
            bool(kwargs.get("print_build_logs")),
            bool(kwargs.get("force_local")),
            bool(kwargs.get("substitute_only")),
        ))
        if kwargs.get("substitute_only"):
            return (lsp_fail,)
        return ()

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", warmup_realize)
    monkeypatch.setattr(pipeline, "import_warmup_drvs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda _path: False)
    monkeypatch.setattr(pipeline, "assert_force_local_dry_run", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "rust_crate_input_drvs", lambda *_a, **_k: ())
    monkeypatch.setattr(
        pipeline,
        "compiler_input_drvs",
        lambda *_args, **_kwargs: (rustc_drv, lsp_drv),
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
            tree=candidate.tree,
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
    assert report.failures == ()
    assert realized == [
        ((rustc_drv,), False, False, True),
        ((lsp_drv,), True, False, False),
        ((settings_drv,), True, True, False),
    ]


def test_zed_warmup_force_locals_whole_family_when_cachix_is_mixed(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zed-only cold/mixed path builds every family drv; never a Cachix subset."""
    candidate = _candidate_for_scope(prepared_run)
    monkeypatch.setattr(
        pipeline.validation,
        "validate_derivations",
        lambda *_args, **_kwargs: (),
    )
    realized: list[tuple[object, bool, bool, bool, bool]] = []

    def warmup_realize(paths: object, *_args: object, **kwargs: object) -> tuple[()]:
        realized.append((
            paths,
            bool(kwargs.get("print_build_logs")),
            bool(kwargs.get("force_local")),
            bool(kwargs.get("substitute_only")),
            bool(kwargs.get("keep_going")),
        ))
        return ()

    imported: list[object] = []

    def capture_import(paths: object, *_args: object, **_kwargs: object) -> None:
        imported.append(paths)

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", warmup_realize)
    monkeypatch.setattr(pipeline, "import_warmup_drvs", capture_import)
    monkeypatch.setattr(
        pipeline, "check_path_in_cachix", lambda path: "settings_content" in path
    )
    monkeypatch.setattr(pipeline, "assert_force_local_dry_run", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "compiler_input_drvs", lambda *_a, **_k: ())
    monkeypatch.setattr(pipeline, "rust_crate_input_drvs", lambda *_a, **_k: ())
    monkeypatch.setattr(pipeline, "rust_svh_input_drvs", lambda *_a, **_k: ())
    monkeypatch.setattr(pipeline, "rustc_generation_ids", lambda *_a, **_k: ())
    from lib.update.ci.warmup import (
        RootWarmupStats,
        ShardLocalBuildReport,
        WarmupPlan,
        write_warmup_plan,
    )

    settings = "/nix/store/2y7vj1wq5nz030asgn7rhipbcx5aya89-rust_settings-0.1.0"
    content = "/nix/store/ma14flyg1v5b4vhinb2l0klw1xmdg9nz-rust_settings_content-0.1.0"
    rust_zed = "/nix/store/av7xckfpaaaaaaaaaaaaaaaaaaaaaaaaa-rust_zed-1.25.0"
    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            tree=candidate.tree,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(content, settings, rust_zed),
            rustLayers=((content,), (settings,), (rust_zed,)),
            outputDrvs={
                content: f"{content}.drv",
                settings: f"{settings}.drv",
                rust_zed: f"{rust_zed}.drv",
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
        candidate, scope="zed-warmup", warmup_plan=warmup_plan
    )
    assert report.failures == ()
    assert imported
    assert all(str(path).endswith(".drv") for path in imported[0])
    force_local = [row for row in realized if row[2]]
    assert force_local
    assert all(row[4] for row in force_local)
    leaves = [row for row in realized if row[0] == (f"{rust_zed}.drv",)]
    assert leaves == [((f"{rust_zed}.drv",), True, False, False, True)]


def test_zed_warmup_substitutes_whole_family_from_one_generation(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cache-hit path substitutes rust_zed + family together, never a mix."""
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
    monkeypatch.setattr(pipeline, "import_warmup_drvs", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda _path: True)
    monkeypatch.setattr(pipeline, "compiler_input_drvs", lambda *_a, **_k: ())
    monkeypatch.setattr(pipeline, "rust_crate_input_drvs", lambda *_a, **_k: ())
    monkeypatch.setattr(pipeline, "rust_svh_input_drvs", lambda *_a, **_k: ())
    monkeypatch.setattr(
        pipeline,
        "rustc_generation_ids",
        lambda *_a, **_k: (
            "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rustc-1.98.1.drv",
        ),
    )
    from lib.update.ci.warmup import (
        RootWarmupStats,
        ShardLocalBuildReport,
        WarmupPlan,
        write_warmup_plan,
    )

    settings = "/nix/store/2y7vj1wq5nz030asgn7rhipbcx5aya89-rust_settings-0.1.0"
    rust_zed = "/nix/store/av7xckfpaaaaaaaaaaaaaaaaaaaaaaaaa-rust_zed-1.25.0"
    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            tree=candidate.tree,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(settings, rust_zed),
            rustLayers=((settings,), (rust_zed,)),
            outputDrvs={settings: f"{settings}.drv", rust_zed: f"{rust_zed}.drv"},
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
        candidate, scope="zed-warmup", warmup_plan=warmup_plan
    )
    assert report.failures == ()
    assert realized == [
        ((f"{settings}.drv", f"{rust_zed}.drv"), False, False, True),
    ]


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
            tree=candidate.tree,
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
            tree=candidate.tree,
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
        ((language_models_drv, other_drv), True, True, False),
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
            tree=candidate.tree,
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
        ((language_models_drv, other_drv), True, True, False),
        ((agent_ui_drv,), True, False, False),
    ]
    realized.clear()
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            tree=candidate.tree,
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
    assert realized == [((language_models_drv,), True, True, False)]
    monkeypatch.setattr(pipeline, "language_models_input_drvs", lambda _drvs: ())
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            tree=candidate.tree,
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
    monkeypatch.setattr(pipeline, "rust_svh_input_drvs", lambda *_args, **_kwargs: ())
    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            tree=candidate.tree,
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
            tree=candidate.tree,
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
        "rust_svh_input_drvs",
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
    monkeypatch.setattr(pipeline, "rust_svh_input_drvs", lambda *_args, **_kwargs: ())
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            tree=candidate.tree,
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


def test_rust_warmup_zed_slot_force_locals_cached_collab_ui_from_layers(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """38151215158: skip_cached collab_ui still force-locals from rust_layers."""
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
    monkeypatch.setattr(
        pipeline,
        "check_path_in_cachix",
        lambda path: "collab_ui" in path or "extension_host" in path,
    )
    monkeypatch.setattr(pipeline, "compiler_input_drvs", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(pipeline, "assert_force_local_dry_run", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "rust_svh_input_drvs", lambda *_args, **_kwargs: ())
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
    collab_ui = "/nix/store/in05ddj652lyxf7fddyphh6npd2qd52c-rust_collab_ui-0.1.0-lib"
    collab_ui_drv = (
        "/nix/store/in05ddj652lyxf7fddyphh6npd2qd52c-rust_collab_ui-0.1.0.drv"
    )
    rust_zed = "/nix/store/dya03z7ahr923gj36783368fimlqvlqd-rust_zed-1.25.0"
    rust_zed_drv = "/nix/store/dya03z7ahr923gj36783368fimlqvlqd-rust_zed-1.25.0.drv"
    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            tree=candidate.tree,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(extension_host, collab_ui, rust_zed),
            rustLayers=((extension_host, collab_ui), (rust_zed,)),
            outputDrvs={
                extension_host: extension_host_drv,
                collab_ui: collab_ui_drv,
                rust_zed: rust_zed_drv,
            },
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=3, missing=1, warmup=3, remaining=0
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
        ((collab_ui_drv,), True, True, False),
        ((rust_zed_drv,), True, False, False),
    ]


def test_rust_warmup_zed_slot_force_locals_collab_ui_from_input_drvs(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """38151215158: cached collab_ui absent from rust_layers still force-locals."""
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
    monkeypatch.setattr(pipeline, "compiler_input_drvs", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(pipeline, "assert_force_local_dry_run", lambda *_a, **_k: None)
    extension_host_drv = (
        "/nix/store/exthostaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_extension_host-0.1.0.drv"
    )
    collab_ui_drv = (
        "/nix/store/in05ddj652lyxf7fddyphh6npd2qd52c-rust_collab_ui-0.1.0.drv"
    )
    rust_zed = "/nix/store/dya03z7ahr923gj36783368fimlqvlqd-rust_zed-1.25.0"
    rust_zed_drv = "/nix/store/dya03z7ahr923gj36783368fimlqvlqd-rust_zed-1.25.0.drv"
    monkeypatch.setattr(
        pipeline,
        "rust_svh_input_drvs",
        lambda *_args, **_kwargs: (extension_host_drv, collab_ui_drv),
    )
    from lib.update.ci.warmup import (
        RootWarmupStats,
        ShardLocalBuildReport,
        WarmupPlan,
        write_warmup_plan,
    )

    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            tree=candidate.tree,
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
    rust = pipeline.validate_candidate(
        candidate, scope="rust-warmup", warmup_plan=warmup_plan, warmup_slot=0
    )
    assert rust.gates == ()
    assert realized == [
        ((extension_host_drv,), True, True, False),
        ((collab_ui_drv,), True, True, False),
        ((rust_zed_drv,), True, False, False),
    ]


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
        "rust_svh_input_drvs",
        lambda *_args, **_kwargs: (extension_host_drv, settings_drv),
    )
    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            tree=candidate.tree,
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
            tree=candidate.tree,
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
        return ()

    monkeypatch.setattr(pipeline, "rust_crate_input_drvs", crate_inputs)
    monkeypatch.setattr(
        pipeline,
        "rust_svh_input_drvs",
        lambda *_args, **_kwargs: (extension_host_drv, settings_drv),
    )
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
            tree=candidate.tree,
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
        "rust_svh_input_drvs",
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
    monkeypatch.setattr(pipeline, "rust_svh_input_drvs", lambda *_args, **_kwargs: ())
    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            tree=candidate.tree,
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


def test_successful_root_closures_record_realized_paths_for_flush(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """38821223775: flush must know the locally built darwin-system wrapper."""
    candidate = _candidate_for_scope(prepared_run)
    remembered: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        pipeline.validation, "validate_derivations", lambda *_args, **_kwargs: ()
    )
    monkeypatch.setattr(pipeline.validation, "validate_root_closures", lambda **_k: ())
    monkeypatch.setattr(pipeline.jobs, "reclaim_hosted_store", lambda: None)
    monkeypatch.setattr(
        pipeline.jobs,
        "remember_realized_installable_paths",
        lambda installables, **_k: remembered.append(installables) or installables,
    )
    shard = pipeline.validate_candidate(
        candidate,
        scope="closure-shard",
        closure_roots=("darwin-argus",),
    )
    assert len(remembered) == 1
    assert remembered[0][0].endswith(
        f"#checks.{shard.system}.root-closure-darwin-argus"
    )
    remembered.clear()
    closures = pipeline.validate_candidate(
        candidate,
        scope="closures",
        closure_budget_seconds=pipeline.HOSTED_DARWIN_CLOSURE_BUILD_BUDGET_SECONDS,
    )
    assert remembered[0][0].endswith(f"#checks.{closures.system}.root-closures")


def test_linux_closures_do_not_record_realized_root_paths(
    prepared_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Linux validate builds Darwin-root deps; it never realizes the Darwin check."""
    candidate = _candidate_for_scope(prepared_run)
    _root, _operations, state = prepared_run
    state["system"] = "x86_64-linux"
    remembered: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        pipeline.validation, "validate_derivations", lambda *_args, **_kwargs: ()
    )
    monkeypatch.setattr(pipeline.validation, "validate_root_closures", lambda **_k: ())
    monkeypatch.setattr(pipeline.jobs, "reclaim_hosted_store", lambda: None)
    monkeypatch.setattr(
        pipeline.jobs,
        "remember_realized_installable_paths",
        lambda installables, **_k: remembered.append(installables) or installables,
    )
    pipeline.validate_candidate(candidate, scope="closures")
    assert remembered == []


def test_failed_root_closures_do_not_record_realized_paths(
    prepared_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed shard must not publish a root the runner never realized."""
    candidate = _candidate_for_scope(prepared_run)
    remembered: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        pipeline.validation, "validate_derivations", lambda *_args, **_kwargs: ()
    )
    monkeypatch.setattr(
        pipeline.validation,
        "validate_root_closures",
        lambda **_k: (
            DerivationValidationFailure(
                "darwin-argus",
                "path:.#checks.aarch64-darwin.root-closure-darwin-argus",
                "build failed",
            ),
        ),
    )
    monkeypatch.setattr(pipeline.jobs, "reclaim_hosted_store", lambda: None)
    monkeypatch.setattr(
        pipeline.jobs,
        "remember_realized_installable_paths",
        lambda installables, **_k: remembered.append(installables) or installables,
    )
    report = pipeline.validate_candidate(
        candidate,
        scope="closure-shard",
        closure_roots=("darwin-argus",),
    )
    assert report.failures
    assert remembered == []


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
            tree=_kwargs["tree"],
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
    published: list[object] = []

    def fake_publish(plan: object, *, flake_root: Path, **_kwargs: object) -> None:
        published.append((plan, flake_root))

    monkeypatch.setattr(pipeline, "publish_warmup_source_fetches", fake_publish)
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
    assert published
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
    assert warmup["tree"] == candidate.tree
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


def test_hosted_zed_warmup_progress_keeps_going_past_workspace_family(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """38126388469: --max-jobs 0 cranelift 40 must defer, not abort at 32.

    Default rust-warmup progress still 32-caps argv --keep-going so an
    accidental flag cannot lift the ceiling. The others realize path
    (39217658040 rust_but-agentlog 39) must pass keep_going=True; that
    is family-scale, not #1263's 406-drv bootstrap.     Family keep_going
    and zed-warmup use the 400-drv ceiling so the 37-crate workspace
    compiles. Bootstrap-scale 406 still aborts on substitute-only
    rustc/bmake and on keep_going umbrellas. rust_* 406
    (38231309175 rust_xet-client) defers like cranelift 40; keep_going
    rust_gpui 415 (39387963366 slot 0) must compile.
    """
    monkeypatch.setattr(pipeline.jobs, "record_runner_storage", lambda *_a, **_k: {})
    rust = pipeline._hosted_validation_progress("rust-warmup")
    rust(ValidationCommandStarted("nix build --max-jobs 0 /nix/store/cranelift.drv"))
    rust(
        ValidationCommandOutput(
            "nix build --max-jobs 0 /nix/store/cranelift.drv",
            "these 40 derivations will be built:",
        )
    )
    with pytest.raises(WarmupFatalError, match="will-be-built"):
        rust(
            ValidationCommandOutput(
                "nix build --max-jobs 0 /nix/store/cranelift.drv",
                "these 406 derivations will be built:",
            )
        )
    xet = (
        "nix build --no-link --max-jobs 0 "
        "'/nix/store/i60xdbbjfssbgmc8vv9pi2zrv21mk5l7-rust_xet-client-1.6.0.drv^*'"
    )
    rust(ValidationCommandStarted(xet))
    rust(ValidationCommandOutput(xet, "these 406 derivations will be built:"))
    rust(
        ValidationCommandOutput(
            xet,
            "error: Cannot build '/nix/store/i60xdbbjfssbgmc8vv9pi2zrv21mk5l7-rust_xet-client-1.6.0.drv'.",
        )
    )
    rustc = (
        "nix build --no-link --max-jobs 0 "
        "'/nix/store/xbq69m0caaaaaaaaaaaaaaaaaaaaaaaa-rustc-1.98.1.drv^*'"
    )
    rust(ValidationCommandStarted(rustc))
    with pytest.raises(WarmupFatalError, match="will-be-built"):
        rust(ValidationCommandOutput(rustc, "these 406 derivations will be built:"))
    others = pipeline._hosted_validation_progress("rust-warmup")
    others(ValidationCommandStarted("nix build --keep-going /nix/store/other.drv"))
    with pytest.raises(WarmupFatalError, match="will-be-built"):
        others(
            ValidationCommandOutput(
                "nix build --keep-going /nix/store/other.drv",
                "these 37 derivations will be built:",
            )
        )
    family = pipeline._hosted_validation_progress("rust-warmup", keep_going=True)
    family(
        ValidationCommandStarted(
            "nix build --keep-going --no-substitute /nix/store/settings.drv"
        )
    )
    cascade = pipeline._hosted_validation_progress("cascade-warmup", keep_going=True)
    cascade(ValidationCommandStarted("nix build --keep-going /nix/store/bb.drv"))
    with pytest.raises(WarmupFatalError, match="fixed-output hash mismatch"):
        cascade(
            ValidationCommandOutput(
                "nix build --keep-going /nix/store/bb.drv",
                "error: hash mismatch in fixed-output derivation "
                "'/nix/store/qbvbj2w3qxhn5c0x43f929k28plr1r8s-bb-pnpm-deps.drv'",
            )
        )
    family(
        ValidationCommandStarted(
            "nix build --keep-going --no-substitute /nix/store/settings.drv"
        )
    )
    family(
        ValidationCommandOutput(
            "nix build --keep-going --no-substitute /nix/store/settings.drv",
            "these 37 derivations will be built:",
        )
    )
    agentlog = (
        "nix build --no-link --keep-going --fallback "
        "'/nix/store/9i3im1cyff56yk1zkliqg7vhg53k845k-rust_but-agentlog-0.0.0.drv^*'"
    )
    others_keep = pipeline._hosted_validation_progress("rust-warmup", keep_going=True)
    others_keep(ValidationCommandStarted(agentlog))
    others_keep(
        ValidationCommandOutput(agentlog, "these 39 derivations will be built:")
    )
    others_keep(
        ValidationCommandOutput(agentlog, "these 406 derivations will be built:")
    )
    gpui = (
        "nix build --no-link --keep-going --fallback "
        "'/nix/store/031nw7v3w2gxx42wm290ivmg1ip6b16z-rust_gpui-0.2.2.drv^*'"
    )
    others_keep(ValidationCommandStarted(gpui))
    others_keep(ValidationCommandOutput(gpui, "these 415 derivations will be built:"))
    umbrella = (
        "nix build --no-link --keep-going --fallback "
        "'/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-goose-cli-1.51.0.drv^*'"
    )
    others_keep(ValidationCommandStarted(umbrella))
    with pytest.raises(WarmupFatalError, match="will-be-built"):
        others_keep(
            ValidationCommandOutput(umbrella, "these 406 derivations will be built:")
        )
    zed = pipeline._hosted_validation_progress("zed-warmup")
    zed(ValidationCommandStarted("nix build --max-jobs 0 /nix/store/git_ui.drv"))
    zed(
        ValidationCommandOutput(
            "nix build --max-jobs 0 /nix/store/git_ui.drv",
            "these 37 derivations will be built:",
        )
    )
    assert "these 37 derivations will be built" in capsys.readouterr().err


def test_realize_svh_family_keeps_going_on_hosted_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Family --keep-going must lift rust-warmup's 32-drv streamed ceiling."""
    seen: list[tuple[str, bool]] = []
    real = pipeline._hosted_validation_progress

    def spy(source: str, *, keep_going: bool = False) -> object:
        seen.append((source, keep_going))
        return real(source, keep_going=keep_going)

    monkeypatch.setattr(pipeline, "_hosted_validation_progress", spy)
    monkeypatch.setattr(pipeline, "compiler_input_drvs", lambda *_a, **_k: ())
    monkeypatch.setattr(pipeline, "assert_force_local_dry_run", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "realize_warmup_outputs", lambda *_a, **_k: ())
    settings = "/nix/store/2y7vj1wq5nz030asgn7rhipbcx5aya89-rust_settings-0.1.0.drv"
    pipeline._realize_svh_family((settings,), flake_root=tmp_path, keep_going=True)
    assert ("rust-warmup", True) in seen


def test_realize_logged_warmup_forwards_keep_going_to_hosted_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Streamed ceiling must match the realize keep_going flag, not argv alone."""
    seen: list[tuple[str, bool]] = []
    real = pipeline._hosted_validation_progress

    def spy(source: str, *, keep_going: bool = False) -> object:
        seen.append((source, keep_going))
        return real(source, keep_going=keep_going)

    monkeypatch.setattr(pipeline, "_hosted_validation_progress", spy)
    monkeypatch.setattr(pipeline, "realize_warmup_outputs", lambda *_a, **_k: ())
    but = "/nix/store/9i3im1cyff56yk1zkliqg7vhg53k845k-rust_but-agentlog-0.0.0.drv"
    pipeline._realize_logged_warmup((but,), flake_root=tmp_path, keep_going=True)
    assert seen == [("rust-warmup", True)]


def test_realize_svh_family_retries_source_fods_not_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Helpers compile immediately; source FODs use the DNS-gated retry."""
    settings = "/nix/store/2y7vj1wq5nz030asgn7rhipbcx5aya89-rust_settings-0.1.0.drv"
    rustc = "/nix/store/xbq69m0caaaaaaaaaaaaaaaaaaaaaaaa-rustc-1.98.1.drv"
    cpio = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-cpio-2.15.drv"
    source = "/nix/store/1hm4jss2dwiz4sii2z00qgwwasflxghy-source.drv"
    realized: list[tuple[object, str]] = []

    def warmup_realize(paths: object, *_args: object, **_kwargs: object) -> tuple[()]:
        realized.append((paths, "warmup"))
        return ()

    def fod_realize(paths: object, *_args: object, **_kwargs: object) -> tuple[()]:
        realized.append((paths, "source"))
        return ()

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", warmup_realize)
    monkeypatch.setattr(pipeline, "realize_source_fetch_drvs", fod_realize)
    monkeypatch.setattr(
        pipeline, "compiler_input_drvs", lambda *_a, **_k: (rustc, cpio, source)
    )
    monkeypatch.setattr(pipeline, "assert_force_local_dry_run", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "_realize_compiler_substitutes", lambda *_a, **_k: ())
    failures, compiled = pipeline._realize_svh_family((settings,), flake_root=tmp_path)
    assert failures == ()
    assert compiled is True
    assert realized == [
        ((cpio,), "warmup"),
        ((source,), "source"),
        ((settings,), "warmup"),
    ]


def test_realize_svh_family_stops_when_compiler_helpers_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """39113211468: helper compile failure must not continue into source FODs."""
    settings = "/nix/store/2y7vj1wq5nz030asgn7rhipbcx5aya89-rust_settings-0.1.0.drv"
    cpio = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-cpio-2.15.drv"
    source = "/nix/store/1hm4jss2dwiz4sii2z00qgwwasflxghy-source.drv"
    helper_fail = DerivationValidationFailure(
        source="rust-warmup",
        installable=f"{cpio}^*",
        message="builder failed",
    )
    seen: list[object] = []

    def warmup_realize(
        paths: object, *_args: object, **_kwargs: object
    ) -> tuple[object, ...]:
        seen.append(paths)
        return (helper_fail,)

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", warmup_realize)
    monkeypatch.setattr(
        pipeline,
        "realize_source_fetch_drvs",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("source FODs must not run after helper failure")
        ),
    )
    monkeypatch.setattr(
        pipeline, "compiler_input_drvs", lambda *_a, **_k: (cpio, source)
    )
    monkeypatch.setattr(pipeline, "assert_force_local_dry_run", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "_realize_compiler_substitutes", lambda *_a, **_k: ())
    failures, compiled = pipeline._realize_svh_family((settings,), flake_root=tmp_path)
    assert failures == (helper_fail,)
    assert compiled is False
    assert seen == [(cpio,)]


def test_install_index_blob_chmod_executable(tmp_path: Path) -> None:
    dest = tmp_path / "bin" / "tool"
    pipeline._install_index_blob(
        dest, pipeline._IndexBlob(mode="100755", content=b"#!/bin/sh\necho hi\n")
    )
    assert dest.is_file()
    assert dest.stat().st_mode & 0o111


def test_realize_requested_warmup_fail_closed_without_plan(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="rust-warmup requires a warmup plan"):
        pipeline._realize_requested_warmup(
            "rust-warmup", None, None, flake_root=tmp_path, expected_tree="a" * 40
        )
    with pytest.raises(ValueError, match="zed-warmup requires a warmup plan"):
        pipeline._realize_requested_warmup(
            "zed-warmup", None, None, flake_root=tmp_path, expected_tree="a" * 40
        )
    assert (
        pipeline._realize_requested_warmup(
            "packages", None, None, flake_root=tmp_path, expected_tree="a" * 40
        )
        == ()
    )


def test_realize_zed_warmup_substitutes_or_compiles_the_family(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """39113211468: zed-warmup substitute vs compile branches had no tests."""
    from lib.update.ci.warmup import (
        RootWarmupStats,
        ShardLocalBuildReport,
        WarmupPlan,
        write_warmup_plan,
    )

    settings = "/nix/store/2y7vj1wq5nz030asgn7rhipbcx5aya89-rust_settings-0.1.0"
    rust_zed = "/nix/store/av7xckfpaaaaaaaaaaaaaaaaaaaaaaaaa-rust_zed-1.25.0"
    nightly = (
        "/nix/store/rlcwld41aaaaaaaaaaaaaaaaaaaaaaaaa-"
        "zed-editor-nightly-unstable-f16f965"
    )
    tree = "a" * 40
    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            tree=tree,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(settings, rust_zed, nightly),
            rustLayers=((settings, rust_zed, nightly),),
            outputDrvs={
                settings: f"{settings}.drv",
                rust_zed: f"{rust_zed}.drv",
                nightly: f"{nightly}.drv",
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
    seen: list[tuple[object, object]] = []

    def warmup_realize(paths: object, *_args: object, **kwargs: object) -> tuple[()]:
        seen.append((paths, kwargs.get("substitute_only")))
        return ()

    monkeypatch.setattr(pipeline, "realize_warmup_outputs", warmup_realize)
    monkeypatch.setattr(pipeline, "import_warmup_drvs", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda _path: True)
    monkeypatch.setattr(pipeline, "rust_svh_input_drvs", lambda *_a, **_k: ())
    monkeypatch.setattr(pipeline, "rust_crate_input_drvs", lambda *_a, **_k: ())
    monkeypatch.setattr(pipeline, "rustc_generation_ids", lambda *_a, **_k: ())
    monkeypatch.setattr(pipeline.jobs, "record_runner_storage", lambda *_a, **_k: None)
    assert (
        pipeline._realize_zed_warmup(
            warmup_plan, flake_root=tmp_path, expected_tree=tree
        )
        == ()
    )
    assert seen
    assert seen[0][1] is True
    seen.clear()
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda _path: False)
    logged: list[object] = []

    def family(
        drvs: object, *_args: object, **_kwargs: object
    ) -> tuple[tuple[()], bool]:
        logged.append(("family", drvs))
        return ((), True)

    monkeypatch.setattr(pipeline, "_realize_svh_family", family)
    monkeypatch.setattr(
        pipeline,
        "_realize_logged_warmup",
        lambda drvs, **_k: logged.append(("logged", drvs)) or (),
    )
    assert (
        pipeline._realize_zed_warmup(
            warmup_plan, flake_root=tmp_path, expected_tree=tree
        )
        == ()
    )
    assert ("family", (f"{settings}.drv",)) in logged
    assert ("logged", (f"{rust_zed}.drv",)) in logged
    assert ("logged", (f"{nightly}.drv",)) in logged
    helper_fail = DerivationValidationFailure(
        source="zed-warmup",
        installable=f"{settings}.drv^*",
        message="builder failed",
    )

    def family_fail(
        drvs: object, *_args: object, **_kwargs: object
    ) -> tuple[tuple[object, ...], bool]:
        logged.append(("family-fail", drvs))
        return ((helper_fail,), False)

    logged.clear()
    monkeypatch.setattr(pipeline, "_realize_svh_family", family_fail)
    assert pipeline._realize_zed_warmup(
        warmup_plan, flake_root=tmp_path, expected_tree=tree
    ) == (helper_fail,)
    assert logged == [("family-fail", (f"{settings}.drv",))]
    nightly_only = tmp_path / "nightly-plan.json"
    write_warmup_plan(
        nightly_only,
        WarmupPlan(
            schemaVersion=1,
            tree=tree,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(nightly,),
            rustLayers=((nightly,),),
            outputDrvs={nightly: f"{nightly}.drv"},
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
    parents: list[object] = []
    monkeypatch.setattr(
        pipeline,
        "rust_crate_input_drvs",
        lambda drvs, *_a, **_k: parents.append(drvs) or (),
    )
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda _path: True)
    seen.clear()
    assert (
        pipeline._realize_zed_warmup(
            nightly_only, flake_root=tmp_path, expected_tree=tree
        )
        == ()
    )
    assert parents == [(f"{nightly}.drv",)]


def test_realize_zed_warmup_rejects_stale_source_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A restored Zed plan cannot realize outputs from another Git tree."""
    from lib.update.ci.warmup import (
        RootWarmupStats,
        ShardLocalBuildReport,
        WarmupError,
        WarmupPlan,
        write_warmup_plan,
    )

    path = tmp_path / "stale-plan.json"
    write_warmup_plan(
        path,
        WarmupPlan(
            schemaVersion=1,
            tree="b" * 40,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(),
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=0, missing=0, warmup=0, remaining=0
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
            notes="stale fixture",
        ),
    )
    with pytest.raises(WarmupError, match="source tree mismatch"):
        pipeline._realize_zed_warmup(path, flake_root=tmp_path, expected_tree="a" * 40)
    monkeypatch.setattr(pipeline, "git", lambda *_args: b"a" * 40)
    with pytest.raises(WarmupError, match="source tree mismatch"):
        pipeline._realize_zed_warmup(path, flake_root=tmp_path)


def test_realize_cascade_paths_noops_when_filter_drops_every_drv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lib.update.ci.warmup import (
        RootWarmupStats,
        ShardLocalBuildReport,
        WarmupPlan,
        write_warmup_plan,
    )

    path = "/nix/store/dddddddddddddddddddddddddddddddd-libiconv-1"
    warmup_plan = tmp_path / "warmup-plan.json"
    write_warmup_plan(
        warmup_plan,
        WarmupPlan(
            schemaVersion=1,
            tree="a" * 40,
            system="aarch64-darwin",
            substituters=("https://cache.nixos.org", "https://gkze.cachix.org"),
            warmupOutputs=(),
            rustLayers=(),
            cascadeOutputs=(path,),
            cascadeLayers=((path,),),
            outputDrvs={path: f"{path}.drv"},
            perRoot={
                "darwin-argus": RootWarmupStats(
                    outputs=1, missing=1, warmup=0, remaining=1
                )
            },
            shards=(
                ShardLocalBuildReport(
                    shard="darwin-argus",
                    roots=("darwin-argus",),
                    remaining=1,
                    remaining_rust_crates=0,
                ),
            ),
            notes="fixture",
        ),
    )
    monkeypatch.setattr(pipeline, "check_path_in_cachix", lambda _path: False)
    monkeypatch.setattr(pipeline, "import_warmup_drvs", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "filter_cascade_realize_drvs", lambda *_a, **_k: ())
    monkeypatch.setattr(pipeline.jobs, "record_runner_storage", lambda *_a, **_k: None)
    assert (
        pipeline._realize_cascade_paths(
            warmup_plan,
            (path,),
            flake_root=tmp_path,
            label="cascade-warmup",
            expected_tree="a" * 40,
        )
        == ()
    )


def test_validate_cli_requires_rust_warmup_plan(prepared_run, tmp_path: Path) -> None:
    candidate = _candidate_for_scope(prepared_run)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(candidate.model_dump_json())
    missing = CliRunner().invoke(
        pipeline.app,
        [
            "validate",
            "--candidate",
            str(candidate_path),
            "--output",
            str(tmp_path / "validation.json"),
            "--scope",
            "rust-warmup",
        ],
    )
    assert missing.exit_code != 0
    assert "rust-warmup requires --warmup-plan" in str(missing.exception)


def test_assert_cached_boundary_cli_queries_flushed_evidence(
    prepared_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate_for_scope(prepared_run)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(candidate.model_dump_json())
    seen: list[tuple[Path, str]] = []
    monkeypatch.setattr(
        pipeline,
        "assert_cached_darwin_boundary",
        lambda evidence, tree: seen.append((evidence, tree)),
    )
    result = CliRunner().invoke(
        pipeline.app,
        [
            "assert-cached-boundary",
            "--candidate",
            str(candidate_path),
            "--evidence",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert seen == [(tmp_path, candidate.tree)]


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
    roots(
        ValidationCommandOutput(
            "nix build path:.#checks.aarch64-darwin.root-closure-darwin-rocinante",
            "these 470 derivations will be built:",
        )
    )
    packages = pipeline._hosted_validation_progress("derivations")
    packages(
        ValidationCommandOutput(
            "nix build",
            "error[E0460]: packages inventory is not a warmup abort",
        )
    )
    cascade = pipeline._hosted_validation_progress("cascade-warmup", keep_going=True)
    with pytest.raises(WarmupFatalError, match="fixed-output hash mismatch"):
        cascade(
            ValidationCommandOutput(
                "nix build --keep-going",
                "error: hash mismatch in fixed-output derivation "
                "'/nix/store/qbvbj2w3qxhn5c0x43f929k28plr1r8s-bb-pnpm-deps.drv'",
            )
        )


_ARM_DISK_IMAGE_BUILD = (
    "nix build --no-link --keep-going --fallback -L "
    "'/nix/store/6xczfl5hgl4yb7p9p997l6hmlwgpfhdj-nixos-disk-image.drv^*'"
)


_DARWIN_DISK_IMAGE_SUBSTITUTE = (
    "nix build --no-link --max-jobs 0 "
    "'/nix/store/6xczfl5hgl4yb7p9p997l6hmlwgpfhdj-nixos-disk-image.drv^*'"
)


def test_hosted_darwin_linux_builder_substitute_miss_is_fatal(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """39006154683: Darwin --max-jobs 0 streamed 50 wbb then spent 5h.

    Linux cache-root-deps had already substituted the same drv from gkze.
    Cannot-build etc-fstab is platform mismatch, not a recoverable helper.
    """
    monkeypatch.setattr(pipeline.jobs, "record_runner_storage", lambda *_a, **_k: {})
    roots = pipeline._hosted_validation_progress("root-closures")
    roots(ValidationCommandStarted(_DARWIN_DISK_IMAGE_SUBSTITUTE))
    with pytest.raises(WarmupFatalError, match="linux-builder image missing"):
        roots(
            ValidationCommandOutput(
                _DARWIN_DISK_IMAGE_SUBSTITUTE,
                "these 50 derivations will be built:",
            )
        )
    err = capsys.readouterr().err
    assert "[root-closures] these 50 derivations will be built:" in err
    roots = pipeline._hosted_validation_progress("root-closures")
    roots(ValidationCommandStarted(_DARWIN_DISK_IMAGE_SUBSTITUTE))
    with pytest.raises(WarmupFatalError, match="linux-builder image missing"):
        roots(
            ValidationCommandOutput(
                _DARWIN_DISK_IMAGE_SUBSTITUTE,
                "error: Cannot build "
                "'/nix/store/wgyazfmqvg0z06ydkhgpnrkgyrzbqawj-etc-fstab.drv'.",
            )
        )


def test_hosted_linux_builder_image_50_wbb_is_not_fatal(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """38137325241: arm root-closures died at 50 wbb on nixos-disk-image.

    That rebuild is the linux-builder VM, expected when hydra has not
    published the CVE-patched stdenv. The 32-limit stays for Darwin
    roots and rust-warmup; the image uses the 400 cap.
    """
    monkeypatch.setattr(pipeline.jobs, "record_runner_storage", lambda *_a, **_k: {})
    roots = pipeline._hosted_validation_progress("root-closures")
    roots(ValidationCommandStarted(_ARM_DISK_IMAGE_BUILD))
    roots(
        ValidationCommandOutput(
            _ARM_DISK_IMAGE_BUILD,
            "these 50 derivations will be built:",
        )
    )
    with pytest.raises(WarmupFatalError, match="will-be-built"):
        roots(
            ValidationCommandOutput(
                _ARM_DISK_IMAGE_BUILD,
                "these 406 derivations will be built:",
            )
        )
    err = capsys.readouterr().err
    assert "[root-closures] these 50 derivations will be built:" in err


def test_hosted_root_deps_50_wbb_is_not_fatal(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """cache-root-deps exists to realize the VM; 50 wbb must not abort it."""
    monkeypatch.setattr(pipeline.jobs, "record_runner_storage", lambda *_a, **_k: {})
    deps = pipeline._hosted_validation_progress("root-deps")
    deps(ValidationCommandStarted("nix build --keep-going /nix/store/vm.drv^*"))
    deps(
        ValidationCommandOutput(
            "nix build --keep-going /nix/store/vm.drv^*",
            "these 50 derivations will be built:",
        )
    )
    assert "these 50 derivations will be built" in capsys.readouterr().err


def test_hosted_root_closures_host_wbb_is_not_a_warmup_abort(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """38506525442: Darwin root-closures streamed 470 wbb after rust+cascade.

    That is the host closure, not rust-warmup bootstrap. The 32-limit
    aborted rocinante+zeus before --keep-going could finish. Cannot-build
    and rust-warmup 32 stay fatal; the linux-builder image keeps 400.
    """
    monkeypatch.setattr(pipeline.jobs, "record_runner_storage", lambda *_a, **_k: {})
    roots = pipeline._hosted_validation_progress("root-closures")
    roots(
        ValidationCommandStarted(
            "nix build --keep-going path:.#checks.aarch64-darwin.root-closure-darwin-rocinante"
        )
    )
    roots(
        ValidationCommandOutput(
            "nix build --keep-going path:.#checks.aarch64-darwin.root-closure-darwin-rocinante",
            "these 470 derivations will be built:",
        )
    )
    rust = pipeline._hosted_validation_progress("rust-warmup")
    with pytest.raises(WarmupFatalError, match="will-be-built"):
        rust(
            ValidationCommandOutput(
                "nix build --keep-going /nix/store/other.drv",
                "these 50 derivations will be built:",
            )
        )
    assert "these 470 derivations will be built" in capsys.readouterr().err


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
    progress(
        ValidationCommandStarted(
            "nix build --keep-going --fallback "
            "'/nix/store/1hm4jss2dwiz4sii2z00qgwwasflxghy-source.drv^*'"
        )
    )
    progress(
        ValidationCommandOutput(
            "nix build --keep-going --fallback "
            "'/nix/store/1hm4jss2dwiz4sii2z00qgwwasflxghy-source.drv^*'",
            "error: Cannot build "
            "'/nix/store/1hm4jss2dwiz4sii2z00qgwwasflxghy-source.drv'.",
        )
    )
    progress(
        ValidationCommandOutput(
            "nix build --keep-going --fallback "
            "'/nix/store/d64pffww4zyr1ggfcihjh7hb5njx0342-convert_case-0.11.0.tar.gz.drv^*'",
            "error: Cannot build "
            "'/nix/store/d64pffww4zyr1ggfcihjh7hb5njx0342-convert_case-0.11.0.tar.gz.drv'.",
        )
    )


def test_hosted_warmup_skips_rust_cannot_build_after_source_fod_miss(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """38359636482 slot 2: rust_markup5ever follows markup5ever.tar.gz DNS miss."""
    monkeypatch.setattr(pipeline.jobs, "record_runner_storage", lambda *_a, **_k: {})
    progress = pipeline._hosted_validation_progress("rust-warmup")
    command = (
        "nix build --no-link --keep-going --fallback "
        "'/nix/store/fa0ic3pgpb37rrh8akp5f54wmrml4bn6-rust_markup5ever-0.38.0.drv^*'"
    )
    progress(ValidationCommandStarted(command))
    progress(
        ValidationCommandOutput(
            command,
            "error: Cannot build "
            "'/nix/store/5j7k6mw5vgchgibgvasm60nzm0cnvcqz-markup5ever-0.38.0.tar.gz.drv'.",
        )
    )
    progress(
        ValidationCommandOutput(
            command,
            "       > error: cannot download markup5ever-0.38.0.tar.gz from any mirror",
        )
    )
    progress(
        ValidationCommandOutput(
            command,
            "error: Cannot build "
            "'/nix/store/fa0ic3pgpb37rrh8akp5f54wmrml4bn6-rust_markup5ever-0.38.0.drv'.",
        )
    )
    assert "markup5ever" in capsys.readouterr().err
    progress(ValidationCommandStarted("nix build --no-link /nix/store/settings.drv"))
    with pytest.raises(WarmupFatalError, match="cannot build"):
        progress(
            ValidationCommandOutput(
                "nix build --no-link /nix/store/settings.drv",
                "Cannot build "
                "'/nix/store/2y7vj1wq5nz030asgn7rhipbcx5aya89-rust_settings-0.1.0.drv'",
            )
        )


def test_hosted_warmup_skips_rusty_v8_hash_mismatch_after_http_503(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """39234076105 slot 4: Chromium 503 then tainted rusty_v8 hash is retried."""
    monkeypatch.setattr(pipeline.jobs, "record_runner_storage", lambda *_a, **_k: {})
    rusty = "/nix/store/az1hmrm8app3kdcn79cxlad1z9zg7s6g-rusty_v8-dbb64c2.drv"
    goose = (
        "nix build --no-link --keep-going --fallback "
        "'/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-rust_v8-goose-145.0.0.drv^*'"
    )
    progress = pipeline._hosted_validation_progress("rust-warmup", keep_going=True)
    progress(ValidationCommandStarted(goose))
    progress(
        ValidationCommandOutput(
            goose,
            "error: RPC failed; HTTP 503 curl 22 The requested URL returned error: 503",
        )
    )
    progress(
        ValidationCommandOutput(
            goose,
            f"error: hash mismatch in fixed-output derivation '{rusty}':",
        )
    )
    assert "rusty_v8" in capsys.readouterr().err
    cascade = pipeline._hosted_validation_progress("cascade-warmup", keep_going=True)
    cascade(ValidationCommandStarted("nix build --keep-going /nix/store/bb.drv"))
    with pytest.raises(WarmupFatalError, match="fixed-output hash mismatch"):
        cascade(
            ValidationCommandOutput(
                "nix build --keep-going /nix/store/bb.drv",
                "error: hash mismatch in fixed-output derivation "
                "'/nix/store/qbvbj2w3qxhn5c0x43f929k28plr1r8s-bb-pnpm-deps.drv'",
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
            tree=pipeline.checkout_candidate(prepared_run[0]).tree,
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
    darwin = _candidate_with_file(
        root,
        "darwin.txt",
        "darwin\n",
        systems=("aarch64-darwin",),
    )
    arm_only = _candidate_with_file(
        root,
        "arm-only.txt",
        "arm\n",
        systems=("aarch64-linux",),
    )
    x86_only = _candidate_with_file(
        root,
        "x86-only.txt",
        "x86\n",
        systems=("x86_64-linux",),
    )
    extra_path = tmp_path / "extra.json"
    left.write_text(darwin.model_dump_json(), encoding="utf-8")
    right.write_text(arm_only.model_dump_json(), encoding="utf-8")
    extra_path.write_text(x86_only.model_dump_json(), encoding="utf-8")
    folded = tmp_path / "artifacts" / "folded.json"
    extra = CliRunner().invoke(
        pipeline.app,
        [
            "merge-candidates",
            "--left",
            str(left),
            "--right",
            str(right),
            "--extra",
            str(extra_path),
            "--output",
            str(folded),
        ],
    )
    assert extra.exit_code == 0, extra.output
    assert set(Candidate.model_validate_json(folded.read_bytes()).systems) == {
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


def test_export_and_publish_warmup_overlaps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """39538011102 ran ~10 min export, then ~31 min FOD publish, serially.

    The export blocks until the publish has started; a serial plan-shards
    would time out here. Both phases log wall time, and an export failure
    still fails the plan.
    """
    import threading

    from lib.update.ci.warmup import WarmupError, WarmupPlan

    plan = WarmupPlan.model_validate({
        "schemaVersion": 1,
        "system": "aarch64-darwin",
        "substituters": [],
        "warmupOutputs": [],
        "outputDrvs": {"/nix/store/out": "/nix/store/out.drv"},
        "perRoot": {},
        "shards": [],
        "notes": "fixture",
    })
    publishing = threading.Event()
    exported: list[tuple[tuple[str, ...], Path]] = []

    def export(drvs: tuple[str, ...], dest: Path) -> None:
        assert publishing.wait(timeout=10), "export did not overlap publish"
        exported.append((drvs, dest))

    def publish(warmup: object, *, flake_root: Path) -> tuple[()]:
        assert warmup is plan
        publishing.set()
        return ()

    monkeypatch.setattr(pipeline, "export_warmup_drvs", export)
    monkeypatch.setattr(pipeline, "publish_warmup_source_fetches", publish)
    pipeline._export_and_publish_warmup(plan, tmp_path / "drvs")
    assert exported == [(("/nix/store/out.drv",), tmp_path / "drvs")]
    err = capsys.readouterr().err
    assert "plan-shards phase export-drvs secs=" in err
    assert "plan-shards phase source-fods secs=" in err

    def fail_export(_drvs: object, _dest: object) -> None:
        msg = "planner is missing warmup .drv"
        raise WarmupError(msg)

    monkeypatch.setattr(pipeline, "export_warmup_drvs", fail_export)
    with pytest.raises(WarmupError, match="missing warmup"):
        pipeline._export_and_publish_warmup(plan, tmp_path / "drvs")
