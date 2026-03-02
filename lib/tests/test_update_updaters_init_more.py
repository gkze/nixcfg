"""Tests for updater module discovery helper."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from lib.tests._assertions import check
from lib.update.updaters import _discover_updaters


def test_discover_updaters_handles_existing_and_invalid_specs(
    monkeypatch,
) -> None:
    """Skip already-imported modules and invalid import specs."""
    fake_file = Path("/tmp/updater.py")
    monkeypatch.setattr(
        "lib.update.updaters.package_file_map",
        lambda _name: {
            "exists": fake_file,
            "spec-none": fake_file,
            "loader-none": fake_file,
            "ok": fake_file,
        },
    )

    import sys

    sys.modules["_updater_pkg.exists"] = object()

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

    check(len(created_modules) == 1)
    check(len(executed_modules) == 1)
