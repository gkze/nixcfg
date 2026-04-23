"""Tests for the extracted mux package.json patch helper."""

from __future__ import annotations

import json
import runpy
from pathlib import Path
from types import ModuleType

import pytest

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


def test_patch_package_json_preserves_existing_fields_and_trailing_newline(
    tmp_path: Path,
) -> None:
    """The helper should merge overrides without dropping unrelated package metadata."""
    helper = _load_helper()
    package_json = tmp_path / "package.json"
    package_json.write_text(
        json.dumps({
            "name": "mux",
            "build": {
                "appId": "dev.example.mux",
                "mac": {"category": "public.app-category.utilities"},
            },
        }),
        encoding="utf-8",
    )

    helper.patch_package_json(package_json, "/nix/store/other-electron-dist")
    text = package_json.read_text(encoding="utf-8")
    payload = json.loads(text)

    assert payload["name"] == "mux"
    assert payload["build"]["appId"] == "dev.example.mux"
    assert payload["build"]["mac"]["category"] == "public.app-category.utilities"
    assert payload["build"]["electronDist"] == "/nix/store/other-electron-dist"
    assert text.endswith("\n")


def test_main_patches_requested_package_json(tmp_path: Path) -> None:
    """The CLI wrapper should patch the requested package.json file in place."""
    helper = _load_helper()
    package_json = tmp_path / "package.json"
    package_json.write_text("{}\n", encoding="utf-8")

    assert helper.main([str(package_json), "/nix/store/electron-dist"]) == 0
    payload = json.loads(package_json.read_text(encoding="utf-8"))
    assert payload["build"]["electronDist"] == "/nix/store/electron-dist"


def test_main_rejects_invalid_argument_count() -> None:
    """The CLI wrapper should enforce its two-argument usage contract."""
    helper = _load_helper()

    with pytest.raises(SystemExit, match="usage: patch_package_json.py"):
        helper.main([])


def test_main_guard_exits_with_main_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Executing the helper as __main__ should raise SystemExit(main())."""
    package_json = tmp_path / "package.json"
    package_json.write_text("{}\n", encoding="utf-8")
    script_path = REPO_ROOT / "packages/mux/patch_package_json.py"
    monkeypatch.setattr(
        "sys.argv",
        [str(script_path), str(package_json), "/nix/store/electron-dist"],
    )

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(str(script_path), run_name="__main__")

    assert excinfo.value.code == 0
    payload = json.loads(package_json.read_text(encoding="utf-8"))
    assert payload["build"]["electronDist"] == "/nix/store/electron-dist"
