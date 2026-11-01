"""Explicit authorization for bounded Nix semantic tests."""

import os
import shutil
from typing import TYPE_CHECKING

import pytest

from lib.tests._git_isolation import GIT_LOCAL_ENV_VARS
from lib.tests._nix_eval import allow_nix_evaluation

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _no_hosted_github_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests from writing a hosted job's live GITHUB_ENV (39113211468).

    Pytest in the Darwin certify job inherited the publish job's GITHUB_ENV;
    a flush-cachix test then emptied CACHIX_DAEMON_* in that live file.
    """
    monkeypatch.delenv("GITHUB_ENV", raising=False)


def pytest_configure(config: pytest.Config) -> None:
    """Keep fixture Git commands off the repository running the suite.

    The pre-push hook runs pytest with Git's hook environment; from a linked
    worktree that includes ``GIT_DIR``, so a fixture's ``git clone --bare``
    set ``core.bare=true`` and its ``git add`` emptied the real index. Scrub
    the process environment once: a per-test ``monkeypatch.delenv`` is undone
    by any test that calls ``monkeypatch.undo()``.
    """
    del config
    for name in GIT_LOCAL_ENV_VARS:
        os.environ.pop(name, None)


@pytest.fixture(autouse=True)
def _nix_evaluation_scope(request: pytest.FixtureRequest) -> Iterator[None]:
    marker = request.node.get_closest_marker("nix_eval")
    if marker is None:
        yield
        return
    if shutil.which("nix") is None and shutil.which("nix-instantiate") is None:
        if os.environ.get("NIXCFG_REQUIRE_NIX_EVAL") == "true":
            pytest.fail(
                "Nix is required for marked semantic tests in this quality gate"
            )
        pytest.skip("Nix is unavailable; marked semantic tests are optional locally")
    with allow_nix_evaluation(marker.kwargs.get("reason")):
        yield
