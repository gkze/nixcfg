"""Tests for updater module discovery helper."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType, SimpleNamespace

from lib.update.paths import package_file_map
from lib.update.updaters import (
    _DISCOVERY_STATE,
    UPDATERS,
    _discover_updaters,
    _updater_module_paths,
    ensure_updaters_loaded,
)
from lib.update.updaters.module_manifest import UPDATER_MODULE_PATHS


def test_discover_updaters_handles_existing_and_invalid_specs(
    monkeypatch,
) -> None:
    """Skip already-imported modules and invalid import specs."""
    fake_file = Path("/tmp/updater.py")
    monkeypatch.setattr(
        "lib.update.updaters._updater_module_paths",
        lambda: {
            "exists": fake_file,
            "stale": fake_file,
            "spec-none": fake_file,
            "loader-none": fake_file,
            "ok": fake_file,
        },
    )

    import sys

    sys.modules["_updater_pkg.exists"] = ModuleType("_updater_pkg.exists")
    sys.modules["_updater_pkg.stale"] = ModuleType("_updater_pkg.stale")
    sys.modules.pop("_updater_pkg.spec-none", None)
    sys.modules.pop("_updater_pkg.loader-none", None)
    sys.modules.pop("_updater_pkg.ok", None)
    monkeypatch.setitem(UPDATERS, "exists", object)

    created_modules: list[str] = []
    executed_modules: list[object] = []

    class _Loader:
        def exec_module(self, mod: object) -> None:
            executed_modules.append(mod)

    def _spec_from_file_location(name: str, _path: Path) -> object | None:
        if name.endswith("spec-none"):
            return None
        if name.endswith("loader-none"):
            return SimpleNamespace(loader=None)
        return SimpleNamespace(loader=_Loader())

    def _module_from_spec(_spec: object) -> object:
        module = object()
        created_modules.append("created")
        return module

    monkeypatch.setattr(
        "lib.update.updaters.importlib.util.spec_from_file_location",
        _spec_from_file_location,
    )
    monkeypatch.setattr(
        "lib.update.updaters.importlib.util.module_from_spec",
        _module_from_spec,
    )

    _discover_updaters()

    assert len(created_modules) == 2
    assert len(executed_modules) == 2
    assert sys.modules["_updater_pkg.stale"] is not None


def test_updater_manifest_matches_repo_scan() -> None:
    """Keep the explicit updater manifest aligned with the repository layout."""
    expected = package_file_map("updater.py")
    manifest_paths = _updater_module_paths()

    assert set(UPDATER_MODULE_PATHS) == set(expected)
    assert manifest_paths == expected


def test_ensure_updaters_loaded_fast_path_skips_discovery(monkeypatch) -> None:
    """Return the existing registry immediately when discovery already ran."""
    complete = True
    monkeypatch.setitem(_DISCOVERY_STATE, "complete", complete)
    monkeypatch.setitem(UPDATERS, "demo", object)

    calls: list[str] = []
    monkeypatch.setattr(
        "lib.update.updaters._discover_updaters", lambda: calls.append("run")
    )

    assert ensure_updaters_loaded() is UPDATERS
    assert calls == []


def test_ensure_updaters_loaded_rediscovers_empty_registry(monkeypatch) -> None:
    """Re-run discovery when the loaded flag is set but the registry is empty."""
    original = dict(UPDATERS)
    complete = True
    monkeypatch.setitem(_DISCOVERY_STATE, "complete", complete)
    UPDATERS.clear()

    def _discover() -> None:
        UPDATERS["demo"] = object

    monkeypatch.setattr("lib.update.updaters._discover_updaters", _discover)

    assert ensure_updaters_loaded() == {"demo": object}
    UPDATERS.clear()
    UPDATERS.update(original)


def test_ensure_updaters_loaded_rechecks_state_inside_lock(monkeypatch) -> None:
    """Avoid redundant discovery when the registry becomes ready before lock entry."""

    class _Lock:
        def __enter__(self) -> None:
            _DISCOVERY_STATE["complete"] = True
            UPDATERS["demo"] = object

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    original = dict(UPDATERS)
    complete = False
    monkeypatch.setitem(_DISCOVERY_STATE, "complete", complete)
    UPDATERS.clear()
    monkeypatch.setattr("lib.update.updaters._DISCOVERY_LOCK", _Lock())

    calls: list[str] = []
    monkeypatch.setattr(
        "lib.update.updaters._discover_updaters", lambda: calls.append("run")
    )

    assert ensure_updaters_loaded() == {"demo": object}
    assert calls == []
    UPDATERS.clear()
    UPDATERS.update(original)
