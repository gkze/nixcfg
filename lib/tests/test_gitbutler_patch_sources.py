"""Tests for GitButler source patching."""

from __future__ import annotations

import json
import runpy
from pathlib import Path
from types import ModuleType

import pytest

from lib.import_utils import load_module_from_path
from lib.update.paths import REPO_ROOT


def _load_patch_module() -> ModuleType:
    module_path = REPO_ROOT / "packages" / "gitbutler" / "patch_sources.py"
    return load_module_from_path(module_path, "_gitbutler_patch_sources")


def _write_tauri_source(root: Path, build_rs: str) -> Path:
    tauri_root = root / "crates" / "gitbutler-tauri"
    tauri_root.mkdir(parents=True)
    (tauri_root / "build.rs").write_text(build_rs, encoding="utf-8")
    (tauri_root / "tauri.conf.json").write_text(
        json.dumps({
            "build": {"beforeBuildCommand": "pnpm build", "frontendDist": "../app"},
            "bundle": {"active": True, "icon": []},
            "identifier": "dev.gitbutler.app",
            "plugins": {
                "deep-link": {"desktop": {"schemes": []}},
                "updater": {"endpoints": []},
            },
            "productName": "GitButler Dev",
        }),
        encoding="utf-8",
    )
    return tauri_root


def test_patch_sources_rewrites_tauri_config_and_build_script(tmp_path: Path) -> None:
    """Patched sources should match the Nix-packaged app layout."""
    module = _load_patch_module()
    build_rs = """fn main() {
    let manifest_dir = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    assert_eq!(manifest_dir.file_name().unwrap(), "gitbutler-tauri");
    let build_dir = manifest_dir
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .join("apps")
        .join("desktop")
        .join("build");
}
"""
    tauri_root = _write_tauri_source(tmp_path, build_rs)

    module._patch_tauri_config(tmp_path)
    module._patch_build_rs(tmp_path)

    config = json.loads((tauri_root / "tauri.conf.json").read_text(encoding="utf-8"))
    assert config["build"]["frontendDist"] == "frontend-dist"
    assert config["bundle"]["active"] is False
    assert config["identifier"] == "com.gitbutler.app"

    assert (
        (tauri_root / "build.rs").read_text(encoding="utf-8")
        == """fn main() {
    let manifest_dir = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    assert_eq!(manifest_dir.file_name().unwrap(), "gitbutler-tauri");
    let build_dir = manifest_dir.join("frontend-dist");
}
"""
    )


def test_patch_build_rs_errors_when_frontend_dist_snippet_is_missing(
    tmp_path: Path,
) -> None:
    """Unexpected upstream build.rs shape should fail loudly."""
    module = _load_patch_module()
    _write_tauri_source(tmp_path, "fn main() {}\n")

    with pytest.raises(SystemExit, match="frontendDist snippet not found"):
        module._patch_build_rs(tmp_path)


def test_main_validates_argv_and_patches_sources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """CLI entrypoint should validate arguments and patch the provided tree."""
    module = _load_patch_module()
    _write_tauri_source(
        tmp_path,
        """fn main() {
    let manifest_dir = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let build_dir = manifest_dir
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .join("apps")
        .join("desktop")
        .join("build");
}
""",
    )

    monkeypatch.setattr(module.sys, "argv", ["patch_sources.py"])
    with pytest.raises(SystemExit, match="usage: patch_sources.py"):
        module.main()

    monkeypatch.setattr(module.sys, "argv", ["patch_sources.py", str(tmp_path)])
    assert module.main() == 0


def test_main_guard_exits_with_main_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Executing the helper as __main__ should raise SystemExit(main())."""
    _write_tauri_source(
        tmp_path,
        """fn main() {
    let manifest_dir = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let build_dir = manifest_dir
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .join("apps")
        .join("desktop")
        .join("build");
}
""",
    )
    script_path = REPO_ROOT / "packages/gitbutler/patch_sources.py"

    monkeypatch.setattr("sys.argv", [str(script_path), str(tmp_path)])

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(str(script_path), run_name="__main__")

    assert excinfo.value.code == 0
