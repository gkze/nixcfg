"""Shared test isolation for the update tooling."""

import pytest


@pytest.fixture(autouse=True)
def _no_persistent_run_log(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every test's update run out of the user's real state directory.

    Logging only activates when ``UPDATE_RUN_LOG`` is truthy AND
    ``UPDATE_RUN_LOG_DIR`` points at a directory, so tests that exercise the
    run log must set both (typically ``UPDATE_RUN_LOG=1`` plus a temporary
    ``UPDATE_RUN_LOG_DIR``). This fixture's ``UPDATE_RUN_LOG=0`` disables
    logging for every other test.
    """
    monkeypatch.setenv("UPDATE_RUN_LOG", "0")
