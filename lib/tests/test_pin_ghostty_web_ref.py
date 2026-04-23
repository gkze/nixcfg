"""Tests for the ghostty-web lockfile pin helper."""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

import pytest

from lib.import_utils import load_module_from_path
from lib.update.paths import REPO_ROOT

_LOCK_ENTRY = (
    '"ghostty-web": ['
    '"ghostty-web@github:anomalyco/ghostty-web#4af877d", {}, '
    '"anomalyco-ghostty-web-4af877d", "sha512-demo"]\n'
)


@pytest.fixture(name="pin_module")
def _pin_module_fixture():
    """Load the pin helper from the overlay tools."""
    return load_module_from_path(
        REPO_ROOT / "overlays/opencode/pin_ghostty_web_ref.py",
        "pin_ghostty_web_ref_test",
    )


@pytest.fixture(name="workspace")
def _workspace_fixture(tmp_path: Path) -> Path:
    """Create a minimal fake opencode workspace."""
    (tmp_path / "packages/app").mkdir(parents=True)
    return tmp_path


def _write_package_json(path: Path, spec: str) -> None:
    path.write_text(
        json.dumps({"dependencies": {"ghostty-web": spec}}, indent=2) + "\n",
        encoding="utf-8",
    )


def test_pin_helper_rewrites_branch_spec_to_locked_commit(
    pin_module, workspace: Path
) -> None:
    """The helper should pin package.json to the bun.lock commit."""
    (workspace / "bun.lock").write_text(_LOCK_ENTRY, encoding="utf-8")
    package_json = workspace / "packages/app/package.json"
    _write_package_json(package_json, "github:anomalyco/ghostty-web#main")

    assert pin_module.main(["pin_ghostty_web_ref.py", str(workspace)]) == 0

    payload = json.loads(package_json.read_text(encoding="utf-8"))
    assert (
        payload["dependencies"]["ghostty-web"] == "github:anomalyco/ghostty-web#4af877d"
    )


def test_pin_helper_is_idempotent_when_already_pinned(
    pin_module, workspace: Path
) -> None:
    """Pinned refs should be left unchanged."""
    (workspace / "bun.lock").write_text(_LOCK_ENTRY, encoding="utf-8")
    package_json = workspace / "packages/app/package.json"
    pinned = "github:anomalyco/ghostty-web#4af877d"
    _write_package_json(package_json, pinned)

    assert pin_module.main(["pin_ghostty_web_ref.py", str(workspace)]) == 0

    payload = json.loads(package_json.read_text(encoding="utf-8"))
    assert payload["dependencies"]["ghostty-web"] == pinned


def test_pin_helper_requires_the_locked_ref(pin_module, workspace: Path) -> None:
    """Missing lockfile metadata should fail loudly."""
    (workspace / "bun.lock").write_text('"other": []\n', encoding="utf-8")
    _write_package_json(
        workspace / "packages/app/package.json",
        "github:anomalyco/ghostty-web#main",
    )

    with pytest.raises(RuntimeError, match="locked ghostty-web GitHub ref"):
        pin_module.main(["pin_ghostty_web_ref.py", str(workspace)])


def test_pin_helper_requires_dependencies_mapping(pin_module, workspace: Path) -> None:
    """package.json must expose dependencies as an object."""
    (workspace / "bun.lock").write_text(_LOCK_ENTRY, encoding="utf-8")
    (workspace / "packages/app/package.json").write_text(
        json.dumps({"dependencies": []}, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(TypeError, match="missing a dependencies mapping"):
        pin_module.main(["pin_ghostty_web_ref.py", str(workspace)])


def test_pin_helper_requires_ghostty_web_dependency(
    pin_module, workspace: Path
) -> None:
    """package.json must already declare the ghostty-web workspace dependency."""
    (workspace / "bun.lock").write_text(_LOCK_ENTRY, encoding="utf-8")
    (workspace / "packages/app/package.json").write_text(
        json.dumps({"dependencies": {"other": "1.0.0"}}, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(TypeError, match="missing the ghostty-web dependency"):
        pin_module.main(["pin_ghostty_web_ref.py", str(workspace)])


def test_pin_helper_script_entrypoint_exits_with_main_result(
    monkeypatch: pytest.MonkeyPatch,
    workspace: Path,
) -> None:
    """Running the helper as a script should exit with its main() return code."""
    (workspace / "bun.lock").write_text(_LOCK_ENTRY, encoding="utf-8")
    _write_package_json(
        workspace / "packages/app/package.json",
        "github:anomalyco/ghostty-web#main",
    )
    script_path = REPO_ROOT / "overlays/opencode/pin_ghostty_web_ref.py"

    monkeypatch.setattr(sys, "argv", [str(script_path), str(workspace)])

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(script_path), run_name="__main__")

    assert exc_info.value.code == 0
