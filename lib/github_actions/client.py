"""GitHub Actions helpers built on GitHubKit's generated REST models.

GitHub does not currently ship an official Python SDK for Actions workflow
inspection, so this module uses GitHubKit's generated REST client and models for
all supported GitHub Actions API surfaces. The live log transport remains in
``lib.github_actions.tail`` because GitHub's web UI uses undocumented polling
endpoints there.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast
from urllib.parse import urlsplit

from githubkit import GitHub
from githubkit.versions.v2022_11_28.models import (
    Job,
    ReposOwnerRepoActionsRunsRunIdJobsGetResponse200,
    ReposOwnerRepoActionsWorkflowsGetResponse200,
    ReposOwnerRepoActionsWorkflowsWorkflowIdRunsGetResponse200,
    Workflow,
    WorkflowRun,
)

from lib import http_utils
from lib.update.paths import get_repo_root

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from pathlib import Path

_DEFAULT_SERVER_URL: Final[str] = "https://github.com"
_USER_AGENT: Final[str] = "nixcfg-github-actions/0.0.0"
_PAGE_SIZE: Final[int] = 100
_ACTIVE_RUN_STATUSES: Final[frozenset[str]] = frozenset({
    "in_progress",
    "queued",
    "requested",
    "waiting",
    "pending",
})
_ACTIVE_JOB_STATUSES: Final[frozenset[str]] = frozenset({
    "in_progress",
    "queued",
    "waiting",
    "pending",
})
_OWNER_REPO_PARTS: Final[int] = 2


@dataclass(frozen=True)
class RepositorySlug:
    """One GitHub repository in ``owner/name`` form."""

    owner: str
    name: str

    @property
    def full_name(self) -> str:
        """Return the canonical ``owner/name`` string."""
        return f"{self.owner}/{self.name}"

    @classmethod
    def parse(cls, value: str) -> RepositorySlug:
        """Parse ``owner/name`` into a validated repository slug."""
        parts = value.strip().split("/")
        if len(parts) != _OWNER_REPO_PARTS or any(part == "" for part in parts):
            msg = f"Expected repository in 'owner/name' form, got {value!r}"
            raise ValueError(msg)
        owner, name = parts
        return cls(owner=owner, name=name)


@dataclass(frozen=True)
class RepositoryContext:
    """Resolved repository slug plus the GitHub web base URL."""

    slug: RepositorySlug
    server_url: str


@dataclass(frozen=True)
class WorkflowListRow:
    """Workflow metadata plus the most recent run, if any."""

    workflow: Workflow
    latest_run: WorkflowRun | None


@dataclass(frozen=True)
class JobSummary:
    """Stable job metadata used by the local tailing workflow.

    The official jobs endpoint includes step payloads, but GitHubKit's generated
    step model currently rejects real-world statuses like ``pending``. We still
    validate the response with GitHubKit after stripping the unsupported step
    payloads, then convert the result into this narrower summary type.
    """

    id: int
    name: str
    status: str
    conclusion: str | None
    html_url: str
    started_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True)
class _ParsedRemote:
    """Structured parts extracted from a git remote URL."""

    host: str
    slug: RepositorySlug


class GitHubActionsClient:
    """Typed wrapper around GitHubKit's supported Actions REST endpoints."""

    def __init__(
        self,
        *,
        token: str,
        context: RepositoryContext,
        github: GitHub | None = None,
    ) -> None:
        """Create one client bound to a repository and GitHub token."""
        self.context = context
        self._github = github or build_github_client(token=token, context=context)
        self._actions = self._github.rest("2022-11-28").actions

    def list_workflows(self) -> tuple[Workflow, ...]:
        """Return all workflows configured for the repository."""
        return _collect_paginated(
            lambda *, page, per_page: tuple(
                self._actions.list_repo_workflows(
                    self.context.slug.owner,
                    self.context.slug.name,
                    per_page=per_page,
                    page=page,
                ).parsed_data.workflows
            )
        )

    def list_workflow_runs(
        self,
        workflow_id: int,
        *,
        limit: int = 20,
    ) -> tuple[WorkflowRun, ...]:
        """Return recent runs for one workflow, newest first."""
        return _collect_paginated(
            lambda *, page, per_page: tuple(
                self._actions.list_workflow_runs(
                    self.context.slug.owner,
                    self.context.slug.name,
                    workflow_id,
                    per_page=per_page,
                    page=page,
                ).parsed_data.workflow_runs
            ),
            limit=limit,
        )

    def get_workflow_run(self, run_id: int) -> WorkflowRun:
        """Return one workflow run by its numeric id."""
        response = self._actions.get_workflow_run(
            self.context.slug.owner,
            self.context.slug.name,
            run_id,
        )
        return response.parsed_data

    def list_run_jobs(self, run_id: int) -> tuple[JobSummary, ...]:
        """Return stable job summaries for one workflow run."""
        return _collect_paginated(
            lambda *, page, per_page: tuple(
                _job_to_summary(job)
                for job in _parse_job_list_response(
                    self._actions.list_jobs_for_workflow_run(
                        self.context.slug.owner,
                        self.context.slug.name,
                        run_id,
                        per_page=per_page,
                        page=page,
                    ).json()
                ).jobs
            )
        )


def build_github_client(*, token: str, context: RepositoryContext) -> GitHub:
    """Create one configured GitHubKit client for this repository context."""
    return GitHub(
        token,
        base_url=github_api_base_url(context.server_url),
        user_agent=_USER_AGENT,
    )


def resolve_repository_context(
    *,
    repo: str | None,
    server_url: str | None,
    cwd: Path | None = None,
) -> RepositoryContext:
    """Resolve repository slug and GitHub host from args or the local git remote."""
    if repo is not None:
        return RepositoryContext(
            slug=RepositorySlug.parse(repo),
            server_url=normalize_server_url(server_url or _DEFAULT_SERVER_URL),
        )

    remote = parse_git_remote_origin(cwd=cwd)
    if server_url is not None:
        normalized_server_url = normalize_server_url(server_url)
        if urlsplit(normalized_server_url).netloc != remote.host:
            msg = (
                f"remote.origin.url points at {remote.host!r}, not "
                f"{urlsplit(normalized_server_url).netloc!r}; pass --repo to override"
            )
            raise ValueError(msg)
        return RepositoryContext(slug=remote.slug, server_url=normalized_server_url)

    return RepositoryContext(slug=remote.slug, server_url=f"https://{remote.host}")


def default_github_token() -> str:
    """Resolve the GitHub token from the usual local credential sources."""
    token = http_utils.resolve_github_token(allow_keyring=True, allow_netrc=True)
    if token is None:
        msg = (
            "Could not resolve a GitHub token from GITHUB_TOKEN/GH_TOKEN, the gh "
            "credential keyring, or ~/.netrc"
        )
        raise RuntimeError(msg)
    return token


def normalize_server_url(url: str) -> str:
    """Normalize one GitHub web base URL."""
    parsed = urlsplit(url.strip())
    if parsed.scheme != "https" or not parsed.netloc:
        msg = f"Expected an HTTPS GitHub server URL, got {url!r}"
        raise ValueError(msg)
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        msg = f"Expected a bare GitHub server origin, got {url!r}"
        raise ValueError(msg)
    return f"{parsed.scheme}://{parsed.netloc}"


def github_api_base_url(server_url: str) -> str:
    """Return the REST API base URL for one GitHub web origin."""
    normalized = normalize_server_url(server_url)
    if urlsplit(normalized).netloc == "github.com":
        return "https://api.github.com"
    return f"{normalized}/api/v3"


def parse_git_remote_origin(*, cwd: Path | None = None) -> _ParsedRemote:
    """Parse ``remote.origin.url`` from the local git checkout."""
    repo_root = (cwd or get_repo_root()).resolve()
    git_executable = shutil.which("git")
    if git_executable is None:
        msg = "Could not find `git` on PATH; pass --repo explicitly"
        raise RuntimeError(msg)
    result = subprocess.run(  # noqa: S603
        [git_executable, "-C", str(repo_root), "config", "--get", "remote.origin.url"],
        check=False,
        capture_output=True,
        text=True,
    )
    remote_url = result.stdout.strip()
    if result.returncode != 0 or not remote_url:
        msg = "Could not read remote.origin.url; pass --repo explicitly"
        raise RuntimeError(msg)
    return parse_git_remote_url(remote_url)


def parse_git_remote_url(remote_url: str) -> _ParsedRemote:
    """Parse a git remote URL into host and ``owner/name`` components."""
    value = remote_url.strip()
    if value.startswith("git@"):
        prefix, separator, path = value.partition(":")
        host = prefix.removeprefix("git@")
        if not separator:
            msg = f"Unsupported git remote URL: {remote_url!r}"
            raise ValueError(msg)
        return _ParsedRemote(host=host, slug=_parse_remote_path(path))

    parsed = urlsplit(value)
    if parsed.scheme in {"http", "https", "ssh"} and parsed.netloc:
        host = parsed.netloc.rsplit("@", maxsplit=1)[-1]
        return _ParsedRemote(host=host, slug=_parse_remote_path(parsed.path))

    msg = f"Unsupported git remote URL: {remote_url!r}"
    raise ValueError(msg)


def select_named_workflow(
    workflows: tuple[Workflow, ...],
    requested_name: str,
) -> Workflow:
    """Resolve one workflow by exact or unique fuzzy name match."""
    return _select_named(
        workflows,
        requested_name=requested_name,
        label="workflow",
        value_getter=lambda workflow: workflow.name,
    )


def select_named_job(
    jobs: tuple[JobSummary, ...],
    requested_name: str,
) -> JobSummary:
    """Resolve one workflow job by exact or unique fuzzy name match."""
    return _select_named(
        jobs,
        requested_name=requested_name,
        label="job",
        value_getter=lambda job: job.name,
    )


def choose_live_run(runs: tuple[WorkflowRun, ...]) -> WorkflowRun | None:
    """Return the most recent non-completed run, if any."""
    for run in runs:
        if run.status in _ACTIVE_RUN_STATUSES:
            return run
    return None


def choose_next_live_job(
    jobs: tuple[JobSummary, ...],
) -> JobSummary | None:
    """Return the next actively running job worth tailing, if any."""
    active = [job for job in jobs if job.status == "in_progress"]
    if active:
        return active[0]
    return None


def _collect_paginated[T](
    fetch_page: Callable[..., tuple[T, ...]],
    *,
    limit: int | None = None,
) -> tuple[T, ...]:
    """Collect paginated GitHub list responses until exhausted or limited."""
    if limit is not None and limit < 1:
        return ()

    page_size = _PAGE_SIZE if limit is None else min(limit, _PAGE_SIZE)
    collected: list[T] = []
    page = 1

    while True:
        page_items = fetch_page(page=page, per_page=page_size)
        if not page_items:
            break
        collected.extend(page_items)
        if limit is not None and len(collected) >= limit:
            return tuple(collected[:limit])
        if len(page_items) < page_size:
            break
        page += 1

    return tuple(collected)


def _parse_job_list_response(
    payload: object,
) -> ReposOwnerRepoActionsRunsRunIdJobsGetResponse200:
    """Validate workflow jobs while ignoring unsupported step payload drift.

    GitHubKit's generated ``JobPropStepsItems`` model currently rejects live step
    statuses like ``pending`` that GitHub's jobs endpoint may emit in practice.
    We only need job-level metadata from this endpoint, so strip ``steps`` before
    validating the rest of the response into the generated job model.
    """
    if not isinstance(payload, dict):
        msg = "Expected workflow jobs response object"
        raise TypeError(msg)
    payload_obj = cast("dict[str, object]", payload)
    jobs = payload_obj.get("jobs")
    if not isinstance(jobs, list):
        msg = "Expected workflow jobs response to contain a jobs list"
        raise TypeError(msg)

    sanitized_payload = dict(payload_obj)
    sanitized_jobs: list[dict[str, object]] = []
    for job in jobs:
        if not isinstance(job, dict):
            msg = "Expected each workflow job to be an object"
            raise TypeError(msg)
        sanitized_job = dict(job)
        sanitized_job.pop("steps", None)
        sanitized_jobs.append(sanitized_job)
    sanitized_payload["jobs"] = sanitized_jobs
    return ReposOwnerRepoActionsRunsRunIdJobsGetResponse200.model_validate(
        sanitized_payload
    )


def _job_to_summary(job: Job) -> JobSummary:
    """Convert one validated GitHubKit job model into the local summary type."""
    html_url = job.html_url
    if html_url is None:
        msg = f"Expected workflow job {job.id} to include an html_url"
        raise TypeError(msg)
    return JobSummary(
        id=job.id,
        name=job.name,
        status=job.status,
        conclusion=job.conclusion,
        html_url=html_url,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


def _parse_remote_path(path: str) -> RepositorySlug:
    stripped = path.strip().removeprefix("/").removesuffix("/").removesuffix(".git")
    parts = [part for part in stripped.split("/") if part]
    if len(parts) != _OWNER_REPO_PARTS:
        msg = f"Expected remote path in owner/name form, got {path!r}"
        raise ValueError(msg)
    return RepositorySlug(owner=parts[0], name=parts[1])


def _select_named[T](
    items: tuple[T, ...],
    *,
    requested_name: str,
    label: str,
    value_getter: Callable[[T], str],
) -> T:
    requested = requested_name.strip()
    if not requested:
        msg = f"Expected a non-empty {label} name"
        raise ValueError(msg)

    exact = [item for item in items if value_getter(item) == requested]
    if len(exact) == 1:
        return exact[0]

    folded_requested = requested.casefold()
    casefold_matches = [
        item for item in items if value_getter(item).casefold() == folded_requested
    ]
    if len(casefold_matches) == 1:
        return casefold_matches[0]
    if len(casefold_matches) > 1:
        msg = _ambiguous_name_message(
            casefold_matches,
            label=label,
            value_getter=value_getter,
        )
        raise ValueError(msg)

    fuzzy_matches = [
        item for item in items if folded_requested in value_getter(item).casefold()
    ]
    if len(fuzzy_matches) == 1:
        return fuzzy_matches[0]
    if len(fuzzy_matches) > 1:
        msg = _ambiguous_name_message(
            fuzzy_matches,
            label=label,
            value_getter=value_getter,
        )
        raise ValueError(msg)

    available = ", ".join(value_getter(item) for item in items) or "<none>"
    msg = f"Unknown {label} {requested_name!r}. Available {label}s: {available}"
    raise ValueError(msg)


def _ambiguous_name_message[T](
    items: list[T],
    *,
    label: str,
    value_getter: Callable[[T], str],
) -> str:
    matches = ", ".join(value_getter(item) for item in items)
    return f"Ambiguous {label} name; matches: {matches}"


__all__ = [
    "GitHubActionsClient",
    "JobSummary",
    "ReposOwnerRepoActionsRunsRunIdJobsGetResponse200",
    "ReposOwnerRepoActionsWorkflowsGetResponse200",
    "ReposOwnerRepoActionsWorkflowsWorkflowIdRunsGetResponse200",
    "RepositoryContext",
    "RepositorySlug",
    "Workflow",
    "WorkflowListRow",
    "WorkflowRun",
    "build_github_client",
    "choose_live_run",
    "choose_next_live_job",
    "default_github_token",
    "github_api_base_url",
    "normalize_server_url",
    "parse_git_remote_origin",
    "parse_git_remote_url",
    "resolve_repository_context",
    "select_named_job",
    "select_named_workflow",
]
