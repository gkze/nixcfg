"""Tests for the T3 Code updater registrations."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from lib.nix.models.sources import SourceEntry
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._updater_helpers import collect_events as _collect
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.events import UpdateEvent, UpdateEventKind
from lib.update.nix import _build_overlay_attr_expr
from lib.update.updaters.base import VersionInfo

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
    assert module.T3CodeUpdater.materialize_when_current is True
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
    assert module.T3CodeDesktopUpdater.materialize_when_current is True
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
        "lib.update.updaters.base.compute_fixed_output_hash",
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
        "lib.update.updaters.base.compute_drv_fingerprint",
        _fake_compute_drv_fingerprint,
    )
    monkeypatch.setattr(
        "lib.update.updaters.base.compute_fixed_output_hash",
        _fake_compute_fixed_output_hash,
    )
    monkeypatch.setattr(
        "lib.update.updaters.base.get_current_nix_platform",
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
    assert captured["source"] == package_name
    assert captured["env"] == {"FAKE_HASHES": "1"}
    assert_nix_ast_equal(
        str(captured["expr"]),
        _build_overlay_attr_expr(
            package_name, ".node_modules", system="aarch64-darwin"
        ),
    )
