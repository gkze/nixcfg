"""Tests for the extracted Zed tree-sitter patch helper."""

from __future__ import annotations

import runpy
from pathlib import Path
from types import ModuleType

import pytest

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

    assert patched == helper._NEW


def test_patch_file_errors_when_expected_snippet_is_missing(tmp_path: Path) -> None:
    """The helper should fail clearly when the patch target is absent."""
    helper = _load_helper()
    build_rs = tmp_path / "build.rs"
    build_rs.write_text("fn main() {}\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="tree-sitter patch target not found"):
        helper.patch_file(build_rs)


def test_main_patches_requested_file(tmp_path: Path) -> None:
    """The CLI wrapper should patch the requested build.rs file in place."""
    helper = _load_helper()
    build_rs = tmp_path / "build.rs"
    build_rs.write_text(helper._OLD, encoding="utf-8")

    assert helper.main([str(build_rs)]) == 0
    assert build_rs.read_text(encoding="utf-8") == helper._NEW


def test_main_rejects_invalid_argument_count() -> None:
    """The CLI wrapper should enforce its one-argument usage contract."""
    helper = _load_helper()

    with pytest.raises(SystemExit, match="usage: patch_tree_sitter_build_rs.py"):
        helper.main([])


def test_main_guard_exits_with_main_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Executing the helper as __main__ should raise SystemExit(main())."""
    build_rs = tmp_path / "build.rs"
    build_rs.write_text(_load_helper()._OLD, encoding="utf-8")
    script_path = (
        REPO_ROOT / "packages/zed-editor-nightly/patch_tree_sitter_build_rs.py"
    )
    monkeypatch.setattr("sys.argv", [str(script_path), str(build_rs)])

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(str(script_path), run_name="__main__")

    assert excinfo.value.code == 0
    assert build_rs.read_text(encoding="utf-8") == _load_helper()._NEW
