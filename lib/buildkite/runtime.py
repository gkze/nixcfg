"""Pure decisions the Buildkite step runner applies around ``jobs.py``.

Nothing here talks to Buildkite or Nix. The runner uses these results so the
publish gate, matrix conclusions, and Cachix hook stay testable without a job.
"""

import os
import platform
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_PUBLISH_STAGES = frozenset({"publish", "start-repair"})
_PREPARE_STAGES = frozenset({"prepare", "prepare-pins"})
_CONCLUSION_RANK = (
    "failure",
    "startup_failure",
    "timed_out",
    "cancelled",
)


def publish_allowed(stage: str, flag: str | None) -> bool:
    """Return whether a stage may push a commit, open a PR, or dispatch Update.

    Cachix publication is not gated. Pushing a store path twice is idempotent.
    Git publication is not.
    """
    if stage not in _PUBLISH_STAGES:
        return True
    return flag == "true"


def prefer_conclusion(current: str, incoming: str) -> str:
    """Keep the more severe job conclusion.

    A timeout stays ``timed_out`` when a later ``always()`` step also fails.
    Repair matches the literal conclusion, so a timeout must not become
    ``failure``.
    """
    if current == "success":
        return incoming
    if incoming in {"success", "skipped"}:
        return current
    if "timed_out" in {current, incoming}:
        return "timed_out"
    if "startup_failure" in {current, incoming}:
        return "startup_failure"
    if "cancelled" in {current, incoming}:
        return "cancelled"
    return "failure"


def combine_results(results: Sequence[str]) -> str:
    """Return one matrix conclusion from cell conclusions.

    ``failure``, ``timed_out``, and ``startup_failure`` stay distinct.
    Repair watches the literal conclusion ``failure``, matching Actions.
    """
    if not results:
        return "skipped"
    for conclusion in _CONCLUSION_RANK:
        if conclusion in results:
            return conclusion
    if all(result == "success" for result in results):
        return "success"
    if all(result == "skipped" for result in results):
        return "skipped"
    return "failure"


def runner_identity() -> tuple[str, str]:
    """Return GitHub's ``RUNNER_OS`` and ``RUNNER_ARCH`` names for this host."""
    system = platform.system()
    machine = platform.machine().lower()
    os_name = {"Linux": "Linux", "Darwin": "macOS"}.get(system, system)
    arch = "ARM64" if machine in {"arm64", "aarch64"} else "X64"
    return os_name, arch


def prepare_build_slots(stage: str, env: Mapping[str, str]) -> str | None:
    """Return ``UPDATE_MAX_NIX_BUILDS`` for a prepare stage that has not set one.

    Hosted Actions sets this to the runner's CPU count. Persistent agents use
    the same rule so prepare still overlaps one hash build per core.
    """
    if stage not in _PREPARE_STAGES or "UPDATE_MAX_NIX_BUILDS" in env:
        return None
    return str(os.cpu_count() or 1)


def daemon_argv(socket: str) -> list[str]:
    """Return the per-job Cachix daemon command.

    The socket is private to the job. Flush stops this daemon; it does not
    stop a system daemon on the shared store.
    """
    return ["cachix", "daemon", "run", "--socket", socket]


def hook_script(*, socket: str, token_file: str) -> str:
    """Return a Nix post-build hook that pushes to the per-job daemon.

    The token stays in a mode-600 file. The hook reads it when Nix runs the
    hook, which does not inherit the job environment.
    """
    return (
        "#!/bin/sh\n"
        "set -eu\n"
        f"export CACHIX_DAEMON_SOCKET={shlex.quote(socket)}\n"
        f'export CACHIX_AUTH_TOKEN="$(cat {shlex.quote(token_file)})"\n'
        "exec cachix push gkze $OUT_PATHS\n"
    )


def nix_user_conf(hook: str) -> str:
    """Return the per-job Nix config that installs *hook*."""
    return f"post-build-hook = {hook}\n"


def github_process_env(env: Mapping[str, str]) -> dict[str, str]:
    """Return the ``GITHUB_*`` variables ``jobs.py`` reads, without claiming to be Actions.

    ``GITHUB_ACTIONS`` stays unset. Image cleanup and hosted block-device
    detection therefore keep their Actions-only behavior.
    """
    os_name, arch = runner_identity()
    branch = env.get("BUILDKITE_BRANCH", "")
    repository = env.get("NIXCFG_REPOSITORY", "gkze/nixcfg")
    return {
        "GITHUB_REF": f"refs/heads/{branch}",
        "GITHUB_REF_NAME": branch,
        "GITHUB_SHA": env.get("BUILDKITE_COMMIT", ""),
        "GITHUB_REPOSITORY": repository,
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_RUN_ID": env.get("BUILDKITE_BUILD_NUMBER", ""),
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_WORKSPACE": env.get("BUILDKITE_BUILD_CHECKOUT_PATH", str(Path.cwd())),
        "RUNNER_OS": os_name,
        "RUNNER_ARCH": arch,
    }
