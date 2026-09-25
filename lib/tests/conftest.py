"""Shared test isolation for the update tooling."""

import pytest


@pytest.fixture(autouse=True)
def _no_persistent_run_log(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Keep every test's update run out of the user's real state directory.

    Detailed diagnostics are opt-in in tests. Durable execution always persists
    its SQLite state, so the default run directory must also be temporary.
    """
    monkeypatch.setenv("UPDATE_RUN_LOG", "0")
    monkeypatch.setenv("UPDATE_RUN_LOG_DIR", str(tmp_path / "update-runs"))
