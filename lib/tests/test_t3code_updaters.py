"""Tests for the T3 Code updater registrations."""

from __future__ import annotations

from lib.import_utils import load_module_from_path
from lib.update.paths import REPO_ROOT


def test_t3code_updater_tracks_platform_specific_workspace_hashes() -> None:
    """The standalone package should use the shared Bun hash updater."""
    module = load_module_from_path(
        REPO_ROOT / "packages/t3code/updater.py",
        "t3code_updater_test",
    )

    assert module.T3CodeUpdater.hash_type == "nodeModulesHash"
    assert module.T3CodeUpdater.platform_specific is True
    assert module.T3CodeUpdater.input_name == "t3code"


def test_t3code_desktop_updater_targets_the_main_t3code_input() -> None:
    """The desktop staged runtime hash should also follow the upstream input."""
    module = load_module_from_path(
        REPO_ROOT / "packages/t3code-desktop/updater.py",
        "t3code_desktop_updater_test",
    )

    assert module.T3CodeDesktopUpdater.hash_type == "nodeModulesHash"
    assert module.T3CodeDesktopUpdater.platform_specific is True
    assert module.T3CodeDesktopUpdater.input_name == "t3code"
