"""Tests for the T3 Code updater registrations."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lib.nix.models.sources import SourceEntry
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._updater_helpers import collect_events as _collect
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.events import (
    CommandResult,
    UpdateEvent,
    UpdateEventKind,
    expect_artifact_updates,
)
from lib.update.generated_artifact_commands import stream_command_materialized_artifacts
from lib.update.nix import _build_overlay_attr_expr
from lib.update.updaters import UpdateContext, VersionInfo

if TYPE_CHECKING:
    from lib.update.process import RunCommandOptions

HASH = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
NEW_HASH = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="


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
    assert_nix_ast_equal(
        module.T3CodeUpdater._node_modules_expr(system="aarch64-darwin"),
        _build_overlay_attr_expr("t3code", ".node_modules", system="aarch64-darwin"),
    )


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
    assert_nix_ast_equal(
        module.T3CodeDesktopUpdater._node_modules_expr(system="aarch64-darwin"),
        _build_overlay_attr_expr(
            "t3code-desktop", ".node_modules", system="aarch64-darwin"
        ),
    )


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

    events = _run(
        _collect(
            updater._compute_hash_for_system(
                VersionInfo(version="main"), system="aarch64-darwin"
            )
        )
    )

    assert captured["source"] == package_name
    assert captured["env"] == {"FAKE_HASHES": "1"}
    assert_nix_ast_equal(
        str(captured["expr"]),
        _build_overlay_attr_expr(
            package_name, ".node_modules", system="aarch64-darwin"
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

    async def _fake_compute_drv_fingerprint(
        source: str,
        *,
        system: str | None = None,
        config: object | None = None,
    ) -> str:
        captured.update({
            "fingerprint_source": source,
            "fingerprint_system": system,
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
        "lib.update.nix.compute_drv_fingerprint",
        _fake_compute_drv_fingerprint,
    )
    monkeypatch.setattr(
        "lib.update.nix.compute_fixed_output_hash",
        _fake_compute_fixed_output_hash,
    )
    monkeypatch.setattr(
        module,
        "stream_command_materialized_artifacts",
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
                pinned_version=VersionInfo(version="main"),
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
    assert captured["materialize_source"] == package_name
    assert captured["materialize_artifact_paths"] == (
        "packages/t3code/bun.lock",
        "packages/t3code-desktop/bun.lock",
    )
    assert captured["materialize_detail"] == "T3 runtime Bun locks"
    assert captured["source"] == package_name
    assert captured["env"] == {"FAKE_HASHES": "1"}
    assert_nix_ast_equal(
        str(captured["expr"]),
        _build_overlay_attr_expr(
            package_name, ".node_modules", system="aarch64-darwin"
        ),
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
    """The runtime lock refresher should wrap the platform hash stream."""
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

    monkeypatch.setattr(
        module,
        "stream_command_materialized_artifacts",
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

    events = _run(
        _collect(
            updater.fetch_hashes(
                VersionInfo(version="main"),
                object(),
                context=UpdateContext(current=None, dry_run=True),
            )
        )
    )

    assert captured["source"] == package_name
    assert captured["args"] == module._runtime_lock_command()
    assert captured["artifact_paths"] == (
        "packages/t3code/bun.lock",
        "packages/t3code-desktop/bun.lock",
    )
    assert captured["dry_run"] is True
    assert captured["detail"] == "T3 runtime Bun locks"
    assert captured["hash_source"] == package_name
    assert captured["env"] == {"FAKE_HASHES": "1"}
    assert events[0] == UpdateEvent.status(package_name, "materialized")
    assert events[-1].kind is UpdateEventKind.VALUE


def test_command_materialized_artifacts_restore_files_after_hashing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Command-generated artifacts are visible to hashing and then restored."""
    first_lock = tmp_path / "packages/t3code/bun.lock"
    second_lock = tmp_path / "packages/t3code-desktop/bun.lock"
    first_lock.parent.mkdir(parents=True)
    second_lock.parent.mkdir(parents=True)
    first_lock.write_text("old standalone\n", encoding="utf-8")
    second_lock.write_text("old desktop\n", encoding="utf-8")
    seen_by_hash: list[tuple[str, str]] = []

    async def _fake_run_command(
        args: list[str],
        *,
        options: RunCommandOptions,
    ) -> AsyncIterator[UpdateEvent]:
        first_lock.write_text("new standalone\n", encoding="utf-8")
        second_lock.write_text("new desktop\n", encoding="utf-8")
        yield UpdateEvent.value(
            options.source,
            CommandResult(args=args, returncode=0, stdout="", stderr=""),
        )

    async def _inner_hash() -> AsyncIterator[UpdateEvent]:
        seen_by_hash.append((
            first_lock.read_text(encoding="utf-8"),
            second_lock.read_text(encoding="utf-8"),
        ))
        yield UpdateEvent.value("t3code", HASH)

    monkeypatch.setattr(
        "lib.update.generated_artifact_commands._run_command",
        _fake_run_command,
    )

    events = _run(
        _collect(
            stream_command_materialized_artifacts(
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
            )
        )
    )

    assert seen_by_hash == [("new standalone\n", "new desktop\n")]
    assert first_lock.read_text(encoding="utf-8") == "old standalone\n"
    assert second_lock.read_text(encoding="utf-8") == "old desktop\n"

    artifact_events = [
        event for event in events if event.kind is UpdateEventKind.ARTIFACT
    ]
    assert len(artifact_events) == 1
    artifacts = expect_artifact_updates(artifact_events[0].payload)
    assert [(artifact.path.as_posix(), artifact.content) for artifact in artifacts] == [
        ("packages/t3code/bun.lock", "new standalone\n"),
        ("packages/t3code-desktop/bun.lock", "new desktop\n"),
    ]


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
                    dry_run=True,
                    repo_root=tmp_path,
                )
            ),
            _collect(
                stream_command_materialized_artifacts(
                    "second",
                    args=["refresh-locks"],
                    artifact_paths=("packages/t3code/bun.lock",),
                    inner=_inner_hash("second"),
                    dry_run=True,
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
