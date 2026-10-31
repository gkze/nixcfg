"""Stop a hosted Update job after a required sibling has already failed.

``jobs.py`` imports this module while bootstrapping the runner, before the
packaged CLI exists. It stays on the standard library for that reason. The
aiohttp GitHub client in ``lib.update.net`` is the updater's client; this poll
is the Actions job list for the run that is already executing.

GitHub ``strategy.fail-fast`` cancels sibling matrix jobs. Cancellation skips
``if: always()`` steps, including the Cachix flush, so a runner death would
drop store paths the next attempt should substitute. This policy waits until a
sibling job has **completed** with ``failure``, ``timed_out``, or
``startup_failure``, then the still-running job exits on its own. Flush still
runs. ``cancelled`` and ``skipped`` are not that signal: a device-faulted
Darwin prepare completes successfully and the fresh VM retries it.

A green run still builds every closure. The stop only abandons work that can
no longer reach publish.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, Self, cast


class _JobPage(Protocol):
    """The slice of ``urlopen`` this poll reads."""

    def __enter__(self) -> Self: ...

    def __exit__(self, *args: object) -> object: ...

    def read(self, length: int = -1) -> bytes: ...


SIBLING_STOP_FILENAME = "sibling-stop.json"

_REAL_FAILURES = frozenset({"failure", "timed_out", "startup_failure"})
_PREPARE_SYSTEMS = frozenset({"aarch64-darwin", "aarch64-linux", "x86_64-linux"})
_LINUX_SYSTEMS = frozenset({"aarch64-linux", "x86_64-linux"})
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
# Public-pool Darwin prepare uses explicit jobs, not a matrix. The retry
# alternative is first so prepare-darwin-retry-0 is not prepare-darwin.
_DARWIN_PREPARE_SHARD = re.compile(r"^(prepare-darwin(?:-retry)?)-\d+$")
_RUN_ID = re.compile(r"^[0-9]{1,20}$")
_PAGE_SIZE = 100
_MAX_PAGES = 10
_HTTP_TIMEOUT_SECONDS = 15.0
_API_VERSION = "2022-11-28"

# Names are the caller job id, without the reusable-workflow `` / native``
# suffix and without a matrix ``(value)`` suffix. Membership is the whole
# policy: a group stops when any watched name has completed in failure.
# ``linux-validate`` watches plan only, so a validate failure does not stop
# plan (38137325241 was a runner SIGTERM, and plan is the shard matrix).
# ``canary-rust`` is not ``rust``: a canary failure must not fail-close the
# 5-wide stripe. Prebuild and cascade-canary are absent on purpose.
_GROUPS: dict[str, frozenset[str]] = {
    "prepare": frozenset({
        "prepare-darwin",
        "prepare-arm",
        "prepare-x86",
        "prepare-darwin-retry",
    }),
    "linux-deps": frozenset({
        "cache-darwin-linux-deps-arm",
        "cache-darwin-linux-deps-x86",
    }),
    "linux-validate": frozenset({"plan-darwin-closures"}),
    "rust": frozenset({"validate-darwin-warm-rust"}),
    "canary-rust": frozenset({"canary-warm-rust"}),
    "cascade": frozenset({"validate-darwin-cascade-warmup"}),
    "roots": frozenset({"validate-darwin-roots"}),
}


class SiblingPollError(Exception):
    """The job list could not be read. The build keeps going."""


@dataclass(frozen=True)
class WorkflowJob:
    """One Actions job row from the current run attempt."""

    name: str
    status: str
    conclusion: str | None
    completed_at: str
    failed_step: str


@dataclass(frozen=True)
class SiblingStop:
    """A completed sibling failure that already blocks publish."""

    group: str
    job: str
    conclusion: str
    failed_step: str


def failfast_group(*, stage: str, system: str, scope: str, canary: bool) -> str:
    """Return the sibling-stop group for one hosted stage, or ``""`` to watch nothing."""
    if stage == "prepare" and system in _PREPARE_SYSTEMS:
        return "prepare"
    if stage == "cache-root-deps" and system in _LINUX_SYSTEMS:
        return "linux-deps"
    if stage == "validate" and scope in {"", "all"} and system in _LINUX_SYSTEMS:
        return "linux-validate"
    if stage == "validate" and scope == "rust-warmup":
        return "canary-rust" if canary else "rust"
    if stage == "validate" and scope == "cascade-warmup":
        return "cascade"
    if stage == "validate" and scope == "closure-shard":
        return "roots"
    return ""


def stop_message(stop: SiblingStop) -> str:
    """Return the annotation text for a sibling stop.

    The sibling is the failure to repair. This job did not discover a second
    root cause by exiting early.
    """
    step = f" step={stop.failed_step}" if stop.failed_step else ""
    return (
        "NIXCFG_SIBLING_STOP "
        f"group={stop.group} sibling={stop.job} conclusion={stop.conclusion}{step}. "
        "Repair the sibling. This job is stopping because that failure "
        "already blocks publish. A green run still builds every closure."
    )


def sibling_stop(group: str, jobs: Sequence[WorkflowJob]) -> SiblingStop | None:
    """Return the earliest completed real failure in *group*, if any."""
    if not group:
        return None
    try:
        watched = _GROUPS[group]
    except KeyError:
        msg = f"unknown sibling-stop group: {group}"
        raise ValueError(msg) from None
    found: list[WorkflowJob] = [
        job
        for job in jobs
        if job.status == "completed"
        and job.conclusion in _REAL_FAILURES
        and _job_matches(job.name, watched)
    ]
    if not found:
        return None
    found.sort(key=lambda job: (job.completed_at, job.name))
    earliest = found[0]
    return SiblingStop(
        group=group,
        job=earliest.name,
        conclusion=earliest.conclusion or "",
        failed_step=earliest.failed_step,
    )


def _raw_job_count(payload: object) -> int:
    """Return the unparsed job-row count, so one bad row cannot hide later pages."""
    if not isinstance(payload, dict):
        return 0
    raw_jobs = payload.get("jobs")
    if not isinstance(raw_jobs, list):
        return 0
    return len(raw_jobs)


def parse_workflow_jobs(payload: object) -> tuple[WorkflowJob, ...]:
    """Parse a List-jobs-for-a-workflow-run payload, skipping malformed rows."""
    if not isinstance(payload, dict):
        msg = "jobs payload is not an object"
        raise SiblingPollError(msg)
    raw_jobs = payload.get("jobs")
    if not isinstance(raw_jobs, list):
        msg = "jobs payload is missing jobs"
        raise SiblingPollError(msg)
    parsed: list[WorkflowJob] = []
    for item in raw_jobs:
        job = _parse_job(item)
        if job is not None:
            parsed.append(job)
    return tuple(parsed)


def fetch_workflow_jobs(
    *,
    repository: str,
    run_id: str,
    token: str,
    opener: Callable[..., _JobPage] | None = None,
) -> tuple[WorkflowJob, ...]:
    """Return jobs for the latest attempt of one Actions run.

    *opener* matches ``urllib.request.urlopen`` and exists so tests can supply
    a response without a network call. A poll error raises
    :class:`SiblingPollError` whose message is a status or exception type, not
    the token or the response body.
    """
    _require_coordinates(repository, run_id, token)
    open_url = opener or cast(
        "Callable[..., _JobPage]",
        urllib.request.urlopen,  # noqa: S310 -- fixed https://api.github.com jobs list
    )
    collected: list[WorkflowJob] = []
    for page in range(1, _MAX_PAGES + 1):
        payload = _read_job_page(
            open_url,
            repository=repository,
            run_id=run_id,
            token=token,
            page=page,
        )
        collected.extend(parse_workflow_jobs(payload))
        if _raw_job_count(payload) < _PAGE_SIZE:
            break
    return tuple(collected)


def _job_matches(api_name: str, watched: frozenset[str]) -> bool:
    """Match a caller job id, including a matrix suffix, but not a longer id.

    ``prepare-darwin-<digits>`` is the public-pool shard of ``prepare-darwin``.
    ``prepare-darwin-retry-<digits>`` is that shard's device-fault retry.
    ``prepare-darwin-merge`` stays outside the group.
    """
    caller = api_name.split(" / ", 1)[0]
    shard = _DARWIN_PREPARE_SHARD.fullmatch(caller)
    aliases = (caller,) if shard is None else (caller, shard.group(1))
    return any(
        alias == name or caller.startswith(f"{name} (")
        for name in watched
        for alias in aliases
    )


def _parse_job(item: object) -> WorkflowJob | None:
    if not isinstance(item, dict):
        return None
    name = item.get("name")
    status = item.get("status")
    if not isinstance(name, str) or not isinstance(status, str):
        return None
    conclusion = item.get("conclusion")
    completed = item.get("completed_at")
    return WorkflowJob(
        name=name,
        status=status,
        conclusion=conclusion if isinstance(conclusion, str) else None,
        completed_at=completed if isinstance(completed, str) else "",
        failed_step=_failed_step(item.get("steps")),
    )


def _failed_step(steps: object) -> str:
    if not isinstance(steps, list):
        return ""
    for step in steps:
        if not isinstance(step, Mapping):
            continue
        if step.get("conclusion") not in _REAL_FAILURES:
            continue
        name = step.get("name")
        if isinstance(name, str) and name:
            return name
    return ""


def _require_coordinates(repository: str, run_id: str, token: str) -> None:
    if _REPOSITORY.fullmatch(repository) is None:
        msg = "repository"
        raise SiblingPollError(msg)
    if _RUN_ID.fullmatch(run_id) is None:
        msg = "run_id"
        raise SiblingPollError(msg)
    if not token or any(character.isspace() for character in token):
        msg = "token"
        raise SiblingPollError(msg)


def _read_job_page(
    opener: Callable[..., _JobPage],
    *,
    repository: str,
    run_id: str,
    token: str,
    page: int,
) -> object:
    query = urllib.parse.urlencode({
        "filter": "latest",
        "page": page,
        "per_page": _PAGE_SIZE,
    })
    url = (
        f"https://api.github.com/repos/{repository}/actions/runs/{run_id}/jobs?{query}"
    )
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "nixcfg-update-sibling-stop",
            "X-GitHub-Api-Version": _API_VERSION,
        },
    )
    try:
        with opener(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        msg = f"HTTPError:{error.code}"
        raise SiblingPollError(msg) from error
    except json.JSONDecodeError as error:
        msg = type(error).__name__
        raise SiblingPollError(msg) from error
    except OSError as error:
        # URLError and TimeoutError are OSError subclasses. One handler keeps
        # the poll failure typed without a multi-exception tuple.
        msg = type(error).__name__
        raise SiblingPollError(msg) from error
