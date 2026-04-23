"""Pure-Python tests for Superset's binding.gyp patch helper."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import ModuleType

import pytest

from lib.import_utils import load_module_from_path
from lib.update.paths import REPO_ROOT


def _load_module() -> ModuleType:
    return load_module_from_path(
        REPO_ROOT / "packages/superset/patch_node_addon_api_binding_gyp.py",
        "superset_patch_node_addon_api_binding_gyp_dedicated_test",
    )


def test_main_patches_matching_binding_gyp_files_and_reports_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Rewrite vendored binding.gyp include lookups and print patched paths."""
    module = _load_module()
    root = tmp_path / "node_modules"
    package_dir = root / "pkg"
    package_dir.mkdir(parents=True)
    matching = package_dir / "binding.gyp"
    untouched = root / "other" / "binding.gyp"
    untouched.parent.mkdir(parents=True)
    matching.write_text(f"prefix {module.OLD} suffix\n", encoding="utf-8")
    untouched.write_text("no replacement here\n", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["patch-helper", str(root)])

    assert module.main() == 0
    assert matching.read_text(encoding="utf-8") == f"prefix {module.NEW} suffix\n"
    assert untouched.read_text(encoding="utf-8") == "no replacement here\n"
    assert capsys.readouterr().out == (
        f"patched node-addon-api include paths in:\n  {matching}\n"
    )


def test_main_uses_default_root_and_reports_when_nothing_changed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Default to apps/desktop/node_modules and print the no-op message."""
    module = _load_module()
    default_root = tmp_path / "apps" / "desktop" / "node_modules"
    default_root.mkdir(parents=True)
    (default_root / "pkg").mkdir()
    (default_root / "pkg" / "binding.gyp").write_text(
        "already patched\n", encoding="utf-8"
    )

    monkeypatch.setattr(Path, "cwd", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(sys, "argv", ["patch-helper"])

    assert module.main() == 0
    assert capsys.readouterr().out == (
        "no binding.gyp files needed node-addon-api include patching\n"
    )


def test_stdout_helper_appends_newline(capsys: pytest.CaptureFixture[str]) -> None:
    """Use stdout writes consistently for status lines."""
    module = _load_module()

    module._stdout("patched")

    assert capsys.readouterr().out == "patched\n"


def test_script_main_guard_exits_with_main_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Execute the file as a script so the __main__ guard runs."""
    root = tmp_path / "node_modules"
    root.mkdir()
    monkeypatch.setattr(sys, "argv", ["patch-helper", str(root)])

    with pytest.raises(SystemExit, match="0") as excinfo:
        runpy.run_path(
            str(REPO_ROOT / "packages/superset/patch_node_addon_api_binding_gyp.py"),
            run_name="__main__",
        )

    assert excinfo.value.code == 0
    assert capsys.readouterr().out == (
        "no binding.gyp files needed node-addon-api include patching\n"
    )
