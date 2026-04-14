"""Tests for the extracted mux package.json patch helper."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

from lib.import_utils import load_module_from_path
from lib.update.paths import REPO_ROOT


def _load_helper() -> ModuleType:
    return load_module_from_path(
        REPO_ROOT / "packages/mux/patch_package_json.py",
        "_mux_patch_package_json",
    )


def test_patch_package_json_sets_hermetic_darwin_build_options(tmp_path: Path) -> None:
    """Mux package.json should gain the Darwin packaging overrides."""
    helper = _load_helper()
    package_json = tmp_path / "package.json"
    package_json.write_text("{}\n", encoding="utf-8")

    helper.patch_package_json(package_json, "/nix/store/demo-electron-dist")
    payload = json.loads(package_json.read_text(encoding="utf-8"))

    assert payload["build"]["electronDist"] == "/nix/store/demo-electron-dist"
    assert payload["build"]["mac"]["target"] == "dir"
    assert payload["build"]["mac"]["hardenedRuntime"] is False
    assert payload["build"]["mac"]["notarize"] is False
