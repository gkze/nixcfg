"""Contracts for Todoist native release selection."""

from lib.tests._updater_helpers import load_repo_module
from lib.update.updaters import VersionInfo


def test_todoist_selects_only_the_native_arm64_dmg() -> None:
    """Mixed-architecture feed entries must not choose a ZIP or Intel download."""
    module = load_repo_module(
        "packages/todoist-desktop/updater.py", "todoist_updater_test"
    )
    updater = module.TodoistDesktopUpdater()
    selector = updater.SELECTORS["aarch64-darwin"]
    root = "https://electron-dl.todoist.net/mac/"
    assert updater.FEED_URL == "https://electron-dl.todoist.net/mac/latest-mac.yml"
    assert selector("9.31.0", root + "Todoist-darwin-9.31.0-arm64-latest.dmg")
    assert not selector("9.31.0", root + "Todoist-darwin-9.31.0-x64-latest.dmg")
    assert not selector("9.31.0", root + "Todoist-darwin-9.31.0-arm64-latest.zip")
    assert not selector("9.31.0", root + "Todoist-darwin-9.28.0-arm64-latest.dmg")

    assert updater.get_download_url("aarch64-darwin", VersionInfo("9.31.0")) == (
        root + "Todoist-darwin-9.31.0-arm64-latest.dmg"
    )
