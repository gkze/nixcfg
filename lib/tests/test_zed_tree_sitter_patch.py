"""Tests for the extracted Zed tree-sitter patch helper."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

from lib.import_utils import load_module_from_path
from lib.update.paths import REPO_ROOT


def _load_helper() -> ModuleType:
    return load_module_from_path(
        REPO_ROOT / "packages/zed-editor-nightly/patch_tree_sitter_build_rs.py",
        "_zed_tree_sitter_patch",
    )


def test_patch_file_expands_tree_sitter_include_iteration(tmp_path: Path) -> None:
    """The helper should replace the single include with split_whitespace iteration."""
    helper = _load_helper()
    build_rs = tmp_path / "build.rs"
    build_rs.write_text(helper._OLD, encoding="utf-8")

    helper.patch_file(build_rs)
    patched = build_rs.read_text(encoding="utf-8")

    assert '.define("static_assert(...)", "");' in patched
    assert 'if let Ok(include) = env::var("DEP_WASMTIME_C_API_INCLUDE")' in patched
    assert "for include in include.split_whitespace()" in patched
    assert "config.include(include);" in patched
