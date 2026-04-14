"""Tests for the extracted Codex Cargo.lock patch helper."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

from lib.import_utils import load_module_from_path
from lib.update.paths import REPO_ROOT


def _load_helper() -> ModuleType:
    return load_module_from_path(
        REPO_ROOT / "packages/codex/patch_cargo_lock_version.py",
        "_codex_patch_cargo_lock",
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
