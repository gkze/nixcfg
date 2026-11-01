"""The suite never inherits the invoking repository's Git environment."""

import os
import shutil
import subprocess

import pytest

from lib.tests._git_isolation import GIT_LOCAL_ENV_VARS


def test_tests_run_without_repository_local_git_environment() -> None:
    """A pre-push hook in a linked worktree exports GIT_DIR (core.bare leak)."""
    leaked = sorted(name for name in GIT_LOCAL_ENV_VARS if name in os.environ)
    assert leaked == []


def test_scrubbed_names_cover_every_repository_local_git_variable() -> None:
    """Keep the scrub list in step with the Git that runs the suite."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is unavailable")
    reported = subprocess.run(  # noqa: S603 -- resolved git, fixed arguments
        [git, "rev-parse", "--local-env-vars"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert set(reported) <= set(GIT_LOCAL_ENV_VARS)
