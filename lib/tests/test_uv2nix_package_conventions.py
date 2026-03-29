"""Enforce repository conventions for ``mkUv2nixPackage`` packages."""

from __future__ import annotations

import json
from pathlib import Path

from lib.update.paths import REPO_ROOT
from lib.update.updaters import UPDATERS, UvLockUpdater, ensure_updaters_loaded


def _uv2nix_package_dirs() -> list[Path]:
    packages_root = REPO_ROOT / "packages"
    result: list[Path] = []
    for package_dir in sorted(
        path for path in packages_root.iterdir() if path.is_dir()
    ):
        default_nix = package_dir / "default.nix"
        if not default_nix.is_file():
            continue
        if "mkUv2nixPackage" in default_nix.read_text(encoding="utf-8"):
            result.append(package_dir)
    return result


def test_mk_uv2nix_packages_use_checked_in_uv_lock_and_updater() -> None:
    """Every ``mkUv2nixPackage`` package should use the checked-in uv workflow."""
    ensure_updaters_loaded()

    package_dirs = _uv2nix_package_dirs()
    assert package_dirs

    for package_dir in package_dirs:
        name = package_dir.name
        uv_lock = package_dir / "uv.lock"
        updater_py = package_dir / "updater.py"
        sources_json = package_dir / "sources.json"

        assert uv_lock.is_file(), f"{name} is missing checked-in uv.lock"
        assert updater_py.is_file(), f"{name} is missing updater.py"
        assert sources_json.is_file(), f"{name} is missing sources.json"

        updater_cls = UPDATERS.get(name)
        assert updater_cls is not None, f"{name} is missing updater registration"
        assert issubclass(updater_cls, UvLockUpdater), (
            f"{name} must register a UvLockUpdater"
        )

        sources = json.loads(sources_json.read_text(encoding="utf-8"))
        assert sources.get("hashes") == [], f"{name} should not store uvLockHash"
