"""Tests for the extracted Codex Cargo.lock patch helper."""

from __future__ import annotations

import runpy
from pathlib import Path
from types import ModuleType

import pytest
import tomlkit

from lib.import_utils import load_module_from_path
from lib.update.paths import REPO_ROOT


def _load_helper() -> ModuleType:
    return load_module_from_path(
        REPO_ROOT / "packages/codex/patch_cargo_lock_version.py",
        "_codex_patch_cargo_lock",
    )


def _load_allocator_helper() -> ModuleType:
    return load_module_from_path(
        REPO_ROOT / "packages/codex/patch_allocator_weak_linkage.py",
        "_codex_patch_allocator_weak_linkage",
    )


def test_patch_lockfile_updates_only_local_placeholder_versions(tmp_path: Path) -> None:
    """Only source-less 0.0.0 packages should be rewritten."""
    helper = _load_helper()
    lock_file = tmp_path / "Cargo.lock"
    lock_file.write_text(
        """
[[package]]
name = "codex-cli"
version = "0.0.0"

[[package]]
name = "remote"
version = "0.0.0"
source = "registry+https://example.invalid"

[[package]]
name = "stable"
version = "1.2.3"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    helper.patch_lockfile(lock_file, "9.9.9")
    patched = lock_file.read_text(encoding="utf-8")

    assert 'name = "codex-cli"' in patched
    assert 'version = "9.9.9"' in patched
    assert 'name = "remote"' in patched
    assert 'source = "registry+https://example.invalid"' in patched
    assert patched.count('version = "9.9.9"') == 1
    assert 'version = "1.2.3"' in patched


def test_patch_lockfile_preserves_packages_without_package_table(
    tmp_path: Path,
) -> None:
    """The helper should tolerate lockfiles without any package entries."""
    helper = _load_helper()
    lock_file = tmp_path / "Cargo.lock"
    lock_file.write_text("version = 3\n", encoding="utf-8")

    helper.patch_lockfile(lock_file, "1.2.3")

    assert tomlkit.parse(lock_file.read_text(encoding="utf-8"))["version"] == 3


def test_main_patches_requested_lockfile(tmp_path: Path) -> None:
    """The CLI wrapper should patch the requested lockfile in place."""
    helper = _load_helper()
    lock_file = tmp_path / "Cargo.lock"
    lock_file.write_text(
        '[[package]]\nname = "codex-cli"\nversion = "0.0.0"\n',
        encoding="utf-8",
    )

    assert helper.main([str(lock_file), "2.3.4"]) == 0
    assert 'version = "2.3.4"' in lock_file.read_text(encoding="utf-8")


def test_main_rejects_invalid_argument_count() -> None:
    """The CLI wrapper should enforce its two-argument usage contract."""
    helper = _load_helper()

    with pytest.raises(SystemExit, match="usage: patch_cargo_lock_version.py"):
        helper.main([])


def test_main_guard_exits_with_main_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Executing the helper as __main__ should raise SystemExit(main())."""
    lock_file = tmp_path / "Cargo.lock"
    lock_file.write_text(
        '[[package]]\nname = "codex-cli"\nversion = "0.0.0"\n',
        encoding="utf-8",
    )
    script_path = REPO_ROOT / "packages/codex/patch_cargo_lock_version.py"
    monkeypatch.setattr("sys.argv", [str(script_path), str(lock_file), "3.4.5"])

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(str(script_path), run_name="__main__")

    assert excinfo.value.code == 0
    assert 'version = "3.4.5"' in lock_file.read_text(encoding="utf-8")


def test_patch_allocator_removes_weak_linkage_attrs(tmp_path: Path) -> None:
    """The allocator helper should remove weak linkage attributes in place."""
    helper = _load_allocator_helper()
    allocator = tmp_path / "lib.rs"
    allocator.write_text(
        '#[linkage = "weak"]\n'
        'pub extern "C" fn __rust_alloc() {}\n'
        '#[linkage = "weak"]\n'
        'pub extern "C" fn __rust_dealloc() {}\n',
        encoding="utf-8",
    )

    helper.patch_allocator(allocator)

    patched = allocator.read_text(encoding="utf-8")
    assert '#[linkage = "weak"]' not in patched
    assert "__rust_alloc" in patched
    assert "__rust_dealloc" in patched


def test_patch_allocator_main_rejects_invalid_argument_count() -> None:
    """The allocator CLI should require exactly one path argument."""
    helper = _load_allocator_helper()

    with pytest.raises(SystemExit, match="usage: patch_allocator_weak_linkage.py"):
        helper.main([])


def test_patch_allocator_main_guard_exits_with_main_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Executing the allocator helper as __main__ should raise SystemExit(main())."""
    allocator = tmp_path / "lib.rs"
    allocator.write_text('#[linkage = "weak"]\nfn shim() {}\n', encoding="utf-8")
    script_path = REPO_ROOT / "packages/codex/patch_allocator_weak_linkage.py"
    monkeypatch.setattr("sys.argv", [str(script_path), str(allocator)])

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(str(script_path), run_name="__main__")

    assert excinfo.value.code == 0
    assert '#[linkage = "weak"]' not in allocator.read_text(encoding="utf-8")
