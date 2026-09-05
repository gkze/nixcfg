"""Tests for the T3 Code updater registrations."""

import asyncio
import json
import shlex
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.flake_lock import FlakeLockNode
from lib.nix.models.sources import SourceEntry, SourcesFile
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.tests._updater_helpers import collect_events as _collect
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.artifacts import GeneratedArtifact
from lib.update.config import resolve_config
from lib.update.electron_manifest import ElectronManifestMetadata
from lib.update.events import (
    CommandResult,
    UpdateEvent,
    UpdateEventKind,
    expect_artifact_updates,
)
from lib.update.generated_artifact_commands import stream_command_materialized_artifacts
from lib.update.nix import _build_package_path_attr_expr
from lib.update.persistence import persist_generated_artifacts
from lib.update.source_runner import (
    SourcesPhaseContext,
    SourceTaskContext,
    SourceTaskResult,
    run_sources_phase,
)
from lib.update.updaters import UpdateContext, VersionInfo

if TYPE_CHECKING:
    from lib.update.process import RunCommandOptions
    from lib.update.updaters.t3_runtime import T3RuntimeUpdater

HASH = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
NEW_HASH = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="


def _version_info(updater: object) -> VersionInfo:
    if getattr(updater, "name", None) != "t3code-desktop":
        return VersionInfo(version="main")
    return VersionInfo(
        version="main",
        metadata=ElectronManifestMetadata(
            node=FlakeLockNode(),
            commit="a" * 40,
            electron_version="41.5.0",
            manifest_path="apps/desktop/package.json",
            manifest_version="0.0.35",
        ),
    )


async def _unexpected_inner() -> AsyncIterator[UpdateEvent]:
    raise AssertionError("invalid generated artifact reached hashing")
    yield  # pragma: no cover -- makes this an async generator


def _current_entry() -> SourceEntry:
    return SourceEntry.model_validate({
        "input": "t3code",
        "version": "main",
        "drvHash": "drv",
        "hashes": [
            {
                "hashType": "nodeModulesHash",
                "hash": HASH,
                "platform": "aarch64-darwin",
            }
        ],
    })


def _source_overrides_from_package_expr(expr: str) -> dict[str, object]:
    parsed = parse_nix_expr(expr)
    contextual_import = expect_binding(parsed.scope, "flake").value
    assert isinstance(contextual_import, FunctionCall)
    import_arguments = contextual_import.argument
    assert isinstance(import_arguments, AttributeSet)
    evaluation_context = expect_binding(
        import_arguments.values,
        "evaluationContext",
    ).value
    assert isinstance(evaluation_context, AttributeSet)
    source_overrides = expect_binding(
        evaluation_context.values,
        "sourceOverrides",
    ).value
    if isinstance(source_overrides, AttributeSet):
        assert source_overrides.values == []
        return {}
    assert isinstance(source_overrides, FunctionCall)
    payload = source_overrides.argument
    assert isinstance(payload, StringPrimitive)
    decoded = json.loads(json.loads(f'"{payload.value}"'))
    assert isinstance(decoded, dict)
    return decoded


def test_t3code_updater_tracks_platform_specific_runtime_hashes() -> None:
    """The standalone package should compute its own Bun hash directly."""
    module = load_repo_module("packages/t3code/updater.py", "t3code_updater_test")

    assert module.T3CodeUpdater.hash_type == "nodeModulesHash"
    assert module.T3CodeUpdater.generated_artifact_files == (
        "bun.lock",
        "../t3code-desktop/bun.lock",
    )
    assert module.T3CodeUpdater.materialize_when_current is True
    assert module.T3CodeUpdater.shows_materialize_artifacts_phase is True
    assert module.T3CodeUpdater.platform_specific is True
    assert module.T3CodeUpdater.supported_platforms == ("aarch64-darwin",)
    assert module.T3CodeUpdater.input_name == "t3code"
    assert module.T3CodeUpdater.hash_attr_path == ".node_modules"


def test_t3code_desktop_updater_targets_the_main_t3code_input() -> None:
    """The desktop staged runtime hash should also follow the upstream input."""
    module = load_repo_module(
        "packages/t3code-desktop/updater.py", "t3code_desktop_updater_test"
    )

    assert module.T3CodeDesktopUpdater.hash_type == "nodeModulesHash"
    assert module.T3CodeDesktopUpdater.generated_artifact_files == (
        "../t3code/bun.lock",
        "bun.lock",
    )
    assert module.T3CodeDesktopUpdater.materialize_when_current is True
    assert module.T3CodeDesktopUpdater.shows_materialize_artifacts_phase is True
    assert module.T3CodeDesktopUpdater.platform_specific is True
    assert module.T3CodeDesktopUpdater.supported_platforms == ("aarch64-darwin",)
    assert module.T3CodeDesktopUpdater.input_name == "t3code"
    assert module.T3CodeDesktopUpdater.hash_attr_path == ".node_modules"
    assert module.T3CodeDesktopUpdater.compatibility_pins == {
        "electronBuilderVersion": "26.15.7",
    }
    assert module.T3CodeDesktopUpdater.compatibility_pin_rationale


def test_shared_runtime_locks_use_one_candidate_view_during_desktop_pin_bump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both shared-lock producers must materialize the new desktop pin."""
    t3code_module = load_repo_module(
        "packages/t3code/updater.py",
        "t3code_shared_candidate_test",
    )
    desktop_module = load_repo_module(
        "packages/t3code-desktop/updater.py",
        "t3code_desktop_shared_candidate_test",
    )
    updater_classes = {
        "t3code": t3code_module.T3CodeUpdater,
        "t3code-desktop": desktop_module.T3CodeDesktopUpdater,
    }
    old_desktop = SourceEntry.model_validate({
        **_current_entry().to_dict(),
        "pins": {"electronBuilderVersion": "26.8.1"},
    })
    materialized: list[tuple[str, str | None, tuple[GeneratedArtifact, ...]]] = []

    async def _fetch_latest(_self: object, _session: object) -> VersionInfo:
        return _version_info(_self)

    async def _not_latest(
        _self: object,
        _context: object,
        _info: VersionInfo,
    ) -> bool:
        return False

    async def _fingerprint(
        self: T3RuntimeUpdater,
        _source_override: SourceEntry | None = None,
    ) -> str:
        return f"{self.name}-candidate-drv"

    async def _hash(
        source: str,
        _expr: str,
        *,
        env: dict[str, str] | None = None,
        config: object | None = None,
    ) -> AsyncIterator[UpdateEvent]:
        _ = (env, config)
        yield UpdateEvent.value(source, NEW_HASH)

    async def _materialize(
        source: str,
        *,
        args: list[str],
        artifact_paths: tuple[str, ...],
        inner: AsyncIterator[UpdateEvent],
        dry_run: bool,
        config: object | None = None,
        detail: str,
    ) -> AsyncIterator[UpdateEvent]:
        _ = (dry_run, config, detail)
        overrides = _source_overrides_from_package_expr(args[4])
        desktop = overrides.get("t3code-desktop")
        pins = desktop.get("pins") if isinstance(desktop, dict) else None
        electron_builder_version = (
            pins.get("electronBuilderVersion") if isinstance(pins, dict) else None
        )
        assert electron_builder_version is None or isinstance(
            electron_builder_version,
            str,
        )
        content = json.dumps(
            {"electronBuilderVersion": electron_builder_version},
            sort_keys=True,
        )
        artifacts = tuple(
            GeneratedArtifact.text(path, content, changed_from_snapshot=True)
            for path in artifact_paths
        )
        materialized.append((source, electron_builder_version, artifacts))
        yield UpdateEvent.artifact(source, list(artifacts))
        async for event in inner:
            yield event

    async def _run_queue_task(
        *,
        source: str,
        queue: asyncio.Queue[UpdateEvent | None],
        task: Callable[[], Awaitable[None]],
    ) -> None:
        _ = (source, queue)
        await task()

    for updater_class in updater_classes.values():
        monkeypatch.setattr(updater_class, "fetch_latest", _fetch_latest)
        monkeypatch.setattr(updater_class, "_is_latest", _not_latest)
        monkeypatch.setattr(updater_class, "_compute_drv_fingerprint", _fingerprint)
    monkeypatch.setattr(
        "lib.update.source_runner._get_updaters", lambda: updater_classes
    )
    monkeypatch.setattr(
        "lib.update.source_runner.update_process.run_queue_task",
        _run_queue_task,
    )
    monkeypatch.setattr(
        "lib.update.updaters.t3_runtime.stream_command_materialized_artifacts",
        _materialize,
    )
    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _hash)
    monkeypatch.setattr(
        "lib.update.nix.get_current_nix_platform",
        lambda: "aarch64-darwin",
    )

    result = _run(
        run_sources_phase(
            SourcesPhaseContext(
                source_names=["t3code", "t3code-desktop"],
                sources=SourcesFile(
                    entries={
                        "t3code": _current_entry(),
                        "t3code-desktop": old_desktop,
                    }
                ),
                queue=asyncio.Queue(),
                update_input=False,
                native_only=False,
                config=resolve_config(),
            )
        )
    )

    assert {pin for _source, pin, _artifacts in materialized} == {"26.15.7"}
    assert [source for source, _pin, _artifacts in materialized] == [
        "t3code-desktop",
        "t3code",
    ]
    assert {
        artifact.content
        for artifacts in result.artifact_updates.values()
        for artifact in artifacts
    } == {'{"electronBuilderVersion": "26.15.7"}'}


@pytest.mark.parametrize(
    ("module_path", "module_name", "class_name", "package_name"),
    [
        (
            "packages/t3code/updater.py",
            "t3code_updater_compute_test",
            "T3CodeUpdater",
            "t3code",
        ),
        (
            "packages/t3code-desktop/updater.py",
            "t3code_desktop_updater_compute_test",
            "T3CodeDesktopUpdater",
            "t3code-desktop",
        ),
    ],
)
def test_t3code_updaters_hash_only_their_node_modules_attr(
    monkeypatch: pytest.MonkeyPatch,
    module_path: str,
    module_name: str,
    class_name: str,
    package_name: str,
) -> None:
    """Hash probes should not build sibling workspace or Electron fixed outputs."""
    module = load_repo_module(module_path, module_name)
    updater = getattr(module, class_name)()
    captured: dict[str, object] = {}

    async def _fake_compute_fixed_output_hash(
        source: str,
        expr: str,
        *,
        env: dict[str, str] | None = None,
        config: object | None = None,
    ) -> AsyncIterator[UpdateEvent]:
        captured.update({"source": source, "expr": expr, "env": env, "config": config})
        yield UpdateEvent.value(source, HASH)

    monkeypatch.setattr(
        "lib.update.nix.compute_fixed_output_hash",
        _fake_compute_fixed_output_hash,
    )

    info = _version_info(updater)
    source_override = (
        updater.build_result(info, [])
        if updater.compatibility_pins is not None
        else None
    )
    events = _run(
        _collect(updater._compute_hash_for_system(info, system="aarch64-darwin"))
    )

    assert captured["source"] == package_name
    assert captured["env"] is None
    assert_nix_ast_equal(
        str(captured["expr"]),
        _build_package_path_attr_expr(
            package_name,
            ".node_modules",
            system="aarch64-darwin",
            source_overrides=(
                {package_name: source_override} if source_override is not None else None
            ),
            fake_hashes=True if source_override is not None else None,
        ),
    )
    assert events == [UpdateEvent.value(package_name, HASH)]


@pytest.mark.parametrize(
    ("module_path", "module_name", "class_name", "package_name"),
    [
        (
            "packages/t3code/updater.py",
            "t3code_updater_current_verify_test",
            "T3CodeUpdater",
            "t3code",
        ),
        (
            "packages/t3code-desktop/updater.py",
            "t3code_desktop_updater_current_verify_test",
            "T3CodeDesktopUpdater",
            "t3code-desktop",
        ),
    ],
)
def test_t3code_updaters_recheck_node_modules_when_drv_fingerprint_matches(
    monkeypatch: pytest.MonkeyPatch,
    module_path: str,
    module_name: str,
    class_name: str,
    package_name: str,
) -> None:
    """A matching drvHash must not hide stale runtime ``nodeModulesHash`` data."""
    module = load_repo_module(module_path, module_name)
    updater = getattr(module, class_name)()
    captured: dict[str, object] = {}

    async def _fetch_latest(_session: object) -> VersionInfo:
        return _version_info(updater)

    monkeypatch.setattr(updater, "fetch_latest", _fetch_latest)

    async def _fake_materialize_runtime_locks(
        source: str,
        *,
        args: list[str],
        artifact_paths: tuple[str, ...],
        inner: AsyncIterator[UpdateEvent],
        dry_run: bool,
        config: object | None = None,
        detail: str,
    ) -> AsyncIterator[UpdateEvent]:
        captured.update({
            "materialize_source": source,
            "materialize_args": args,
            "materialize_artifact_paths": artifact_paths,
            "materialize_dry_run": dry_run,
            "materialize_config": config,
            "materialize_detail": detail,
        })
        async for event in inner:
            yield event

    async def _fake_compute_expr_drv_fingerprint(
        source: str,
        expr: str,
        *,
        config: object | None = None,
    ) -> str:
        captured.update({
            "fingerprint_source": source,
            "fingerprint_expr": expr,
            "fingerprint_config": config,
        })
        return "drv"

    async def _fake_compute_fixed_output_hash(
        source: str,
        expr: str,
        *,
        env: dict[str, str] | None = None,
        config: object | None = None,
    ):
        captured.update({"source": source, "expr": expr, "env": env, "config": config})
        yield UpdateEvent.value(source, NEW_HASH)

    monkeypatch.setattr(
        "lib.update.nix.compute_expr_drv_fingerprint",
        _fake_compute_expr_drv_fingerprint,
    )
    monkeypatch.setattr(
        "lib.update.nix.compute_fixed_output_hash",
        _fake_compute_fixed_output_hash,
    )
    monkeypatch.setattr(
        "lib.update.updaters.t3_runtime.stream_command_materialized_artifacts",
        _fake_materialize_runtime_locks,
    )
    monkeypatch.setattr(
        "lib.update.nix.get_current_nix_platform",
        lambda: "aarch64-darwin",
    )

    events = _run(
        _collect(
            updater.update_stream(
                _current_entry(),
                object(),
            )
        )
    )

    result_payloads = [
        event.payload
        for event in events
        if event.kind is UpdateEventKind.RESULT and event.payload is not None
    ]
    assert len(result_payloads) == 1
    result = result_payloads[0]
    assert isinstance(result, SourceEntry)
    assert result.drv_hash == "drv"
    assert result.hashes.entries[0].hash == NEW_HASH
    assert captured["fingerprint_source"] == package_name
    fingerprint_override = (
        updater.build_result(_version_info(updater), [])
        if updater.compatibility_pins is not None
        else None
    )
    assert_nix_ast_equal(
        str(captured["fingerprint_expr"]),
        _build_package_path_attr_expr(
            package_name,
            ".node_modules",
            source_overrides=(
                {package_name: fingerprint_override}
                if fingerprint_override is not None
                else None
            ),
            fake_hashes=True if fingerprint_override is not None else None,
        ),
    )
    assert captured["materialize_source"] == package_name
    assert captured["materialize_artifact_paths"] == (
        "packages/t3code/bun.lock",
        "packages/t3code-desktop/bun.lock",
    )
    assert captured["materialize_detail"] == "T3 runtime Bun locks"
    assert captured["source"] == package_name
    assert captured["env"] is None
    assert_nix_ast_equal(
        str(captured["expr"]),
        _build_package_path_attr_expr(
            package_name,
            ".node_modules",
            system="aarch64-darwin",
            source_overrides=(
                {package_name: fingerprint_override}
                if fingerprint_override is not None
                else None
            ),
            fake_hashes=True if fingerprint_override is not None else None,
        ),
    )


def test_t3code_fingerprints_materialized_locks_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fingerprint the generated lock candidate before restoring checked-in files."""
    module = load_repo_module(
        "packages/t3code-desktop/updater.py",
        "t3code_desktop_fingerprint_lifecycle_test",
    )
    updater = module.T3CodeDesktopUpdater()
    checked_in_lock = "stale lock"
    candidate_lock = "candidate lock"
    fingerprint_states: list[str] = []

    async def _fetch_latest(_session: object) -> VersionInfo:
        return _version_info(updater)

    async def _fingerprint(_source_override: SourceEntry | None = None) -> str:
        fingerprint_states.append(checked_in_lock)
        return f"drv-{checked_in_lock}"

    async def _hash(
        source: str,
        _expr: str,
        *,
        env: dict[str, str] | None = None,
        config: object | None = None,
    ) -> AsyncIterator[UpdateEvent]:
        _ = (env, config)
        yield UpdateEvent.value(source, NEW_HASH)

    async def _materialize(
        source: str,
        *,
        args: list[str],
        artifact_paths: tuple[str, ...],
        inner: AsyncIterator[UpdateEvent],
        dry_run: bool,
        config: object | None = None,
        detail: str,
    ) -> AsyncIterator[UpdateEvent]:
        nonlocal checked_in_lock
        _ = (args, dry_run, config, detail)
        previous = checked_in_lock
        checked_in_lock = candidate_lock
        try:
            yield UpdateEvent.artifact(
                source,
                [
                    GeneratedArtifact.text(
                        path,
                        candidate_lock,
                        changed_from_snapshot=previous != candidate_lock,
                    )
                    for path in artifact_paths
                ],
            )
            async for event in inner:
                yield event
        finally:
            checked_in_lock = previous

    monkeypatch.setattr(updater, "fetch_latest", _fetch_latest)
    monkeypatch.setattr(updater, "_compute_drv_fingerprint", _fingerprint)
    monkeypatch.setattr(
        "lib.update.updaters.t3_runtime.stream_command_materialized_artifacts",
        _materialize,
    )
    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _hash)
    monkeypatch.setattr(
        "lib.update.nix.get_current_nix_platform",
        lambda: "aarch64-darwin",
    )

    old_desktop = SourceEntry.model_validate({
        **_current_entry().to_dict(),
        "pins": {"electronBuilderVersion": "26.8.1"},
    })
    first_events = _run(
        _collect(
            updater.update_stream(
                old_desktop,
                object(),
                context=UpdateContext(
                    current=old_desktop,
                    effective_sources={
                        "t3code": _current_entry(),
                        "t3code-desktop": old_desktop,
                    },
                ),
            )
        )
    )
    first_results = [
        event.payload
        for event in first_events
        if event.kind is UpdateEventKind.RESULT and event.payload is not None
    ]
    assert len(first_results) == 1
    first_result = first_results[0]
    assert isinstance(first_result, SourceEntry)

    checked_in_lock = candidate_lock
    second_events = _run(
        _collect(
            updater.update_stream(
                first_result,
                object(),
                context=UpdateContext(
                    current=first_result,
                    effective_sources={
                        "t3code": _current_entry(),
                        "t3code-desktop": first_result,
                    },
                ),
            )
        )
    )

    assert first_result.drv_hash == "drv-candidate lock"
    assert fingerprint_states
    assert set(fingerprint_states) == {candidate_lock}
    assert not any(
        event.kind is UpdateEventKind.RESULT and event.payload is not None
        for event in second_events
    )
    assert not any(
        artifact.changed_from_snapshot
        for event in second_events
        if event.kind is UpdateEventKind.ARTIFACT
        for artifact in expect_artifact_updates(event.payload)
    )


@pytest.mark.parametrize(
    ("module_path", "module_name", "class_name", "package_name"),
    [
        (
            "packages/t3code/updater.py",
            "t3code_updater_materialize_test",
            "T3CodeUpdater",
            "t3code",
        ),
        (
            "packages/t3code-desktop/updater.py",
            "t3code_desktop_updater_materialize_test",
            "T3CodeDesktopUpdater",
            "t3code-desktop",
        ),
    ],
)
def test_t3code_updaters_refresh_runtime_locks_before_hashing(
    monkeypatch: pytest.MonkeyPatch,
    module_path: str,
    module_name: str,
    class_name: str,
    package_name: str,
) -> None:
    """The runtime lock refresher should wrap hashing and finalization."""
    module = load_repo_module(module_path, module_name)
    updater = getattr(module, class_name)()
    captured: dict[str, object] = {}

    async def _fake_materialize_runtime_locks(
        source: str,
        *,
        args: list[str],
        artifact_paths: tuple[str, ...],
        inner: AsyncIterator[UpdateEvent],
        dry_run: bool,
        config: object | None = None,
        detail: str,
    ) -> AsyncIterator[UpdateEvent]:
        captured.update({
            "source": source,
            "args": args,
            "artifact_paths": artifact_paths,
            "dry_run": dry_run,
            "config": config,
            "detail": detail,
        })
        yield UpdateEvent.status(source, "materialized")
        async for event in inner:
            yield event

    async def _fake_compute_fixed_output_hash(
        source: str,
        expr: str,
        *,
        env: dict[str, str] | None = None,
        config: object | None = None,
    ) -> AsyncIterator[UpdateEvent]:
        captured.update({"hash_source": source, "expr": expr, "env": env})
        yield UpdateEvent.value(source, NEW_HASH)

    async def _fetch_latest(_session: object) -> VersionInfo:
        return info

    async def _fingerprint(_source_override: SourceEntry | None = None) -> str:
        return "candidate-drv"

    monkeypatch.setattr(
        "lib.update.updaters.t3_runtime.stream_command_materialized_artifacts",
        _fake_materialize_runtime_locks,
    )
    monkeypatch.setattr(
        "lib.update.nix.compute_fixed_output_hash",
        _fake_compute_fixed_output_hash,
    )
    monkeypatch.setattr(
        "lib.update.nix.get_current_nix_platform",
        lambda: "aarch64-darwin",
    )

    info = _version_info(updater)
    monkeypatch.setattr(updater, "fetch_latest", _fetch_latest)
    monkeypatch.setattr(updater, "_compute_drv_fingerprint", _fingerprint)
    events = _run(
        _collect(
            updater.update_stream(
                None,
                object(),
                context=UpdateContext(current=None, dry_run=True),
            )
        )
    )

    assert captured["source"] == package_name
    assert captured["args"][:4] == ["nix", "run", "--impure", "--expr"]
    source_override = (
        updater.build_result(info, [])
        if updater.compatibility_pins is not None
        else None
    )
    assert_nix_ast_equal(
        captured["args"][4],
        _build_package_path_attr_expr(
            "t3code-desktop",
            ".passthru.updateRuntimeLocks",
            source_overrides=(
                {package_name: source_override} if source_override is not None else None
            ),
            fake_hashes=True if source_override is not None else None,
        ),
    )
    assert captured["artifact_paths"] == (
        "packages/t3code/bun.lock",
        "packages/t3code-desktop/bun.lock",
    )
    assert captured["dry_run"] is True
    assert captured["detail"] == "T3 runtime Bun locks"
    assert captured["hash_source"] == package_name
    assert captured["env"] is None
    assert UpdateEvent.status(package_name, "materialized") in events
    result = events[-1]
    assert result.kind is UpdateEventKind.RESULT
    assert isinstance(result.payload, SourceEntry)
    assert result.payload.hashes.entries[0].hash == NEW_HASH
    assert result.payload.drv_hash == "candidate-drv"


def test_command_materialized_artifacts_dry_run_skips_live_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dry-run hashes still run without commands touching checked-in artifacts."""
    first_lock = tmp_path / "packages/t3code/bun.lock"
    second_lock = tmp_path / "packages/t3code-desktop/bun.lock"
    first_lock.parent.mkdir(parents=True)
    second_lock.parent.mkdir(parents=True)
    first_lock.write_text("old standalone\n", encoding="utf-8")
    second_lock.write_text("old desktop\n", encoding="utf-8")
    before = {
        path: (path.stat().st_ino, path.stat().st_mtime_ns)
        for path in (first_lock, second_lock)
    }
    seen_by_hash: list[tuple[str, str]] = []

    async def _unexpected_run_command(
        _args: list[str],
        *,
        options: RunCommandOptions,
    ) -> AsyncIterator[UpdateEvent]:
        _ = options
        raise AssertionError("dry-run invoked materializer")
        yield  # pragma: no cover -- makes this an async generator

    async def _inner_hash() -> AsyncIterator[UpdateEvent]:
        seen_by_hash.append((
            first_lock.read_text(encoding="utf-8"),
            second_lock.read_text(encoding="utf-8"),
        ))
        yield UpdateEvent.value("t3code", HASH)

    monkeypatch.setattr(
        "lib.update.generated_artifact_commands._run_command",
        _unexpected_run_command,
    )

    async def _collect_with_change_detection() -> list[UpdateEvent]:
        events: list[UpdateEvent] = []
        async for event in stream_command_materialized_artifacts(
            "t3code",
            args=["refresh-locks"],
            artifact_paths=(
                "packages/t3code/bun.lock",
                "packages/t3code-desktop/bun.lock",
            ),
            inner=_inner_hash(),
            dry_run=True,
            detail="T3 runtime Bun locks",
            repo_root=tmp_path,
        ):
            events.append(event)
        return events

    events = _run(_collect_with_change_detection())

    assert seen_by_hash == [("old standalone\n", "old desktop\n")]
    assert first_lock.read_text(encoding="utf-8") == "old standalone\n"
    assert second_lock.read_text(encoding="utf-8") == "old desktop\n"
    assert all(event.kind is not UpdateEventKind.ARTIFACT for event in events)
    assert events[-1] == UpdateEvent.value("t3code", HASH)
    assert {
        path: (path.stat().st_ino, path.stat().st_mtime_ns)
        for path in (first_lock, second_lock)
    } == before


def test_command_materialized_artifacts_restore_when_hashing_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Command-backed probes restore checked-in files even on inner failures."""
    lock_file = tmp_path / "packages/t3code/bun.lock"
    lock_file.parent.mkdir(parents=True)
    lock_file.write_bytes(b"\xffbefore\n")
    lock_file.chmod(0o640)

    async def _refresh(
        args: list[str],
        *,
        options: RunCommandOptions,
    ) -> AsyncIterator[UpdateEvent]:
        lock_file.write_text("temporary\n", encoding="utf-8")
        lock_file.chmod(0o600)
        yield UpdateEvent.value(
            options.source,
            CommandResult(args=args, returncode=0, stdout="", stderr=""),
        )

    async def _failed_hash() -> AsyncIterator[UpdateEvent]:
        assert lock_file.read_text(encoding="utf-8") == "temporary\n"
        assert lock_file.stat().st_mode & 0o777 == 0o600
        raise RuntimeError("hash failed")
        yield  # pragma: no cover -- makes this an async generator

    monkeypatch.setattr("lib.update.generated_artifact_commands._run_command", _refresh)

    with pytest.raises(RuntimeError, match="hash failed"):
        _run(
            _collect(
                stream_command_materialized_artifacts(
                    "t3code",
                    args=["refresh-locks"],
                    artifact_paths=("packages/t3code/bun.lock",),
                    inner=_failed_hash(),
                    dry_run=False,
                    repo_root=tmp_path,
                )
            )
        )

    assert lock_file.read_bytes() == b"\xffbefore\n"
    assert lock_file.is_file()
    assert not lock_file.is_symlink()
    assert lock_file.stat().st_mode & 0o777 == 0o640


def test_command_materializer_does_not_rewrite_an_unchanged_artifact(
    tmp_path: Path,
) -> None:
    """Keep the original inode when a materializer leaves its output unchanged."""
    lock_file = tmp_path / "packages/t3code/bun.lock"
    lock_file.parent.mkdir(parents=True)
    lock_file.write_text("unchanged\n", encoding="utf-8")
    before = lock_file.stat()

    async def _hash() -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.value("t3code", HASH)

    events = _run(
        _collect(
            stream_command_materialized_artifacts(
                "t3code",
                args=["sh", "-c", "true"],
                artifact_paths=("packages/t3code/bun.lock",),
                inner=_hash(),
                dry_run=False,
                repo_root=tmp_path,
            )
        )
    )

    artifact_event = next(
        event for event in events if event.kind is UpdateEventKind.ARTIFACT
    )
    assert not expect_artifact_updates(artifact_event.payload)[0].changed_from_snapshot
    after = lock_file.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


def test_command_materializer_removes_new_artifact_after_hashing(
    tmp_path: Path,
) -> None:
    """Treat a newly generated file as changed and remove its probe copy."""
    artifact = tmp_path / "packages/t3code/generated.lock"
    seen_by_hash: list[str] = []

    async def _hash() -> AsyncIterator[UpdateEvent]:
        seen_by_hash.append(artifact.read_text(encoding="utf-8"))
        yield UpdateEvent.value("t3code", HASH)

    events = _run(
        _collect(
            stream_command_materialized_artifacts(
                "t3code",
                args=[
                    "sh",
                    "-c",
                    f"mkdir -p {shlex.quote(str(artifact.parent))} && "
                    f"printf 'generated\\n' > {shlex.quote(str(artifact))}",
                ],
                artifact_paths=("packages/t3code/generated.lock",),
                inner=_hash(),
                dry_run=False,
                repo_root=tmp_path,
            )
        )
    )

    assert seen_by_hash == ["generated\n"]
    artifact_event = next(
        event for event in events if event.kind is UpdateEventKind.ARTIFACT
    )
    assert expect_artifact_updates(artifact_event.payload)[0].changed_from_snapshot
    assert not artifact.exists()


def test_command_materializer_rejects_preexisting_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a declared artifact directory before invoking its generator."""
    artifact = tmp_path / "packages/t3code/bun.lock"
    artifact.mkdir(parents=True)

    async def _unexpected_command(
        _args: list[str],
        *,
        options: RunCommandOptions,
    ) -> AsyncIterator[UpdateEvent]:
        _ = options
        raise AssertionError("generator ran for an invalid artifact path")
        yield  # pragma: no cover -- makes this an async generator

    monkeypatch.setattr(
        "lib.update.generated_artifact_commands._run_command",
        _unexpected_command,
    )

    with pytest.raises(RuntimeError, match="not a regular file"):
        _run(
            _collect(
                stream_command_materialized_artifacts(
                    "t3code",
                    args=["refresh-locks"],
                    artifact_paths=("packages/t3code/bun.lock",),
                    inner=_unexpected_inner(),
                    dry_run=False,
                    repo_root=tmp_path,
                )
            )
        )


def test_command_materializer_restores_file_replaced_by_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restore the original even when a broken generator changes its type."""
    artifact = tmp_path / "packages/t3code/bun.lock"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("before\n", encoding="utf-8")

    async def _replace_with_directory(
        args: list[str],
        *,
        options: RunCommandOptions,
    ) -> AsyncIterator[UpdateEvent]:
        artifact.unlink()
        artifact.mkdir()
        yield UpdateEvent.value(
            options.source,
            CommandResult(args=args, returncode=0, stdout="", stderr=""),
        )

    monkeypatch.setattr(
        "lib.update.generated_artifact_commands._run_command",
        _replace_with_directory,
    )

    with pytest.raises(RuntimeError, match="was not produced"):
        _run(
            _collect(
                stream_command_materialized_artifacts(
                    "t3code",
                    args=["refresh-locks"],
                    artifact_paths=("packages/t3code/bun.lock",),
                    inner=_unexpected_inner(),
                    dry_run=False,
                    repo_root=tmp_path,
                )
            )
        )

    assert artifact.read_text(encoding="utf-8") == "before\n"


def test_shared_materialized_artifact_keeps_each_successful_source_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful source must retain a shared artifact after its peer fails."""
    lock_file = tmp_path / "packages/t3code/bun.lock"
    lock_file.parent.mkdir(parents=True)
    lock_file.write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(
        GeneratedArtifact,
        "resolved_path",
        lambda self, *, repo_root=tmp_path: tmp_path / self.path,
    )
    monkeypatch.setattr(
        GeneratedArtifact,
        "repo_relative_path",
        lambda self, *, repo_root=tmp_path: self.path,
    )
    source_names = ["t3code", "t3code-desktop"]
    artifact = GeneratedArtifact.text(
        "packages/t3code/bun.lock",
        "new\n",
        changed_from_snapshot=True,
    )

    async def _update_source(
        name: str, *, context: SourceTaskContext
    ) -> SourceTaskResult:
        _ = context
        return SourceTaskResult(
            completed=name == "t3code-desktop",
            artifacts=(artifact,),
        )

    monkeypatch.setattr("lib.update.source_runner.update_source_task", _update_source)
    monkeypatch.setattr(
        "lib.update.source_runner.update_planner.source_update_waves",
        lambda *_args: [source_names],
    )
    monkeypatch.setattr(
        "lib.update.source_runner._get_updaters",
        lambda: dict.fromkeys(source_names, object),
    )
    result = _run(
        run_sources_phase(
            SourcesPhaseContext(
                source_names=source_names,
                sources=SourcesFile(
                    entries={name: _current_entry() for name in source_names}
                ),
                queue=asyncio.Queue(),
                update_input=False,
                native_only=False,
                config=resolve_config(),
            )
        )
    )
    assert result.details == {"t3code": "error", "t3code-desktop": "updated"}
    assert result.artifact_updates == {"t3code-desktop": (artifact,)}
    persist_generated_artifacts(
        do_sources=True,
        source_names=source_names,
        dry_run=False,
        artifact_updates=result.artifact_updates,
        details=result.details,
    )
    assert lock_file.read_text(encoding="utf-8") == "new\n"


def test_shared_materialized_artifact_rejects_conflicting_successful_snapshots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful producers must not silently disagree on one artifact."""
    lock_file = tmp_path / "packages/t3code/bun.lock"
    lock_file.parent.mkdir(parents=True)
    lock_file.write_text("baseline\n", encoding="utf-8")
    monkeypatch.setattr(
        GeneratedArtifact,
        "resolved_path",
        lambda self, *, repo_root=tmp_path: tmp_path / self.path,
    )
    monkeypatch.setattr(
        GeneratedArtifact,
        "repo_relative_path",
        lambda self, *, repo_root=tmp_path: self.path,
    )
    source_names = ["t3code", "t3code-desktop"]
    queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
    baseline = GeneratedArtifact.text(
        "packages/t3code/bun.lock",
        "baseline\n",
        changed_from_snapshot=False,
    )
    changed = GeneratedArtifact.text(
        "packages/t3code/bun.lock",
        "different\n",
        changed_from_snapshot=True,
    )

    async def _update_source(
        name: str, *, context: SourceTaskContext
    ) -> SourceTaskResult:
        _ = context
        artifact = baseline if name == "t3code" else changed
        return SourceTaskResult(completed=True, artifacts=(artifact,))

    monkeypatch.setattr("lib.update.source_runner.update_source_task", _update_source)
    monkeypatch.setattr(
        "lib.update.source_runner.update_planner.source_update_waves",
        lambda *_args: [source_names],
    )
    monkeypatch.setattr(
        "lib.update.source_runner._get_updaters",
        lambda: dict.fromkeys(source_names, object),
    )
    result = _run(
        run_sources_phase(
            SourcesPhaseContext(
                source_names=source_names,
                sources=SourcesFile(
                    entries={name: _current_entry() for name in source_names}
                ),
                queue=queue,
                update_input=False,
                native_only=False,
                config=resolve_config(),
            )
        )
    )

    assert result.artifact_updates == {
        "t3code": (baseline,),
        "t3code-desktop": (changed,),
    }
    assert result.details == {
        "t3code": "no_change",
        "t3code-desktop": "updated",
    }
    with pytest.raises(
        RuntimeError,
        match="Conflicting generated artifact updates for packages/t3code/bun.lock",
    ):
        persist_generated_artifacts(
            do_sources=True,
            source_names=source_names,
            dry_run=False,
            artifact_updates=result.artifact_updates,
            details=result.details,
        )
    assert lock_file.read_text(encoding="utf-8") == "baseline\n"


def test_command_materialized_artifacts_serializes_overlapping_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent materializers sharing an artifact path must not overlap."""
    lock_file = tmp_path / "packages/t3code/bun.lock"
    lock_file.parent.mkdir(parents=True)
    lock_file.write_text("old\n", encoding="utf-8")
    active_hashes = 0
    max_active_hashes = 0
    seen_by_hash: list[tuple[str, str]] = []

    async def _fake_run_command(
        args: list[str],
        *,
        options: RunCommandOptions,
    ) -> AsyncIterator[UpdateEvent]:
        _ = args
        lock_file.write_text(f"{options.source}\n", encoding="utf-8")
        yield UpdateEvent.value(
            options.source,
            CommandResult(args=[], returncode=0, stdout="", stderr=""),
        )

    async def _inner_hash(source: str) -> AsyncIterator[UpdateEvent]:
        nonlocal active_hashes, max_active_hashes
        active_hashes += 1
        max_active_hashes = max(max_active_hashes, active_hashes)
        await asyncio.sleep(0)
        seen_by_hash.append((source, lock_file.read_text(encoding="utf-8")))
        active_hashes -= 1
        yield UpdateEvent.value(source, HASH)

    async def _run_both() -> None:
        await asyncio.gather(
            _collect(
                stream_command_materialized_artifacts(
                    "first",
                    args=["refresh-locks"],
                    artifact_paths=("packages/t3code/bun.lock",),
                    inner=_inner_hash("first"),
                    dry_run=False,
                    repo_root=tmp_path,
                )
            ),
            _collect(
                stream_command_materialized_artifacts(
                    "second",
                    args=["refresh-locks"],
                    artifact_paths=("packages/t3code/bun.lock",),
                    inner=_inner_hash("second"),
                    dry_run=False,
                    repo_root=tmp_path,
                )
            ),
        )

    monkeypatch.setattr(
        "lib.update.generated_artifact_commands._run_command",
        _fake_run_command,
    )

    _run(_run_both())

    assert max_active_hashes == 1
    assert sorted(seen_by_hash) == [("first", "first\n"), ("second", "second\n")]
    assert lock_file.read_text(encoding="utf-8") == "old\n"
