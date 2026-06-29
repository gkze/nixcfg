"""Import-audit tests for crate2nix compatibility shims."""

from __future__ import annotations

import ast
from pathlib import Path

from lib.update.paths import REPO_ROOT

_EXPECTED_COMPAT_IMPORTS = {
    "overlays/goose-cli/updater.py": {
        "patch_installed_crate2nix_missing_hashes",
        "patch_installed_crate2nix_target",
    },
    "packages/codex/updater.py": {"patch_installed_crate2nix_target"},
    "packages/gitbutler/updater.py": {"patch_installed_crate2nix_target"},
    "packages/zed-editor-nightly/updater.py": {"patch_installed_crate2nix_target"},
}


def _crate2nix_compat_imports(path: Path) -> set[str]:
    module = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(module):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != "lib.update.crate2nix_compat":
            continue
        imported.update(alias.name for alias in node.names)
    return imported


def test_crate2nix_compat_shims_remain_tied_to_active_updater_imports() -> None:
    """Compatibility helpers should only be removed after active imports are gone."""
    assert {
        rel_path: _crate2nix_compat_imports(REPO_ROOT / rel_path)
        for rel_path in _EXPECTED_COMPAT_IMPORTS
    } == _EXPECTED_COMPAT_IMPORTS
