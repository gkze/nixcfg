"""Admit a build before the generated graph is uploaded.

Actions concurrency is a property of the workflow run, not of one matrix cell.
The bootstrap step lists active builds on the same branch and either waits or
cancels older ones. One Buildkite build can contain every workflow that
matched the event, so a cancel stops all of them together.
"""

import json
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol, Self, cast

from lib.buildkite.concurrency import Admission, BuildRef, admit
from lib.buildkite.emit import concurrency_expression
from lib.buildkite.github_expr import ExprContext, parse, truthy
from lib.buildkite.graph import Workflow, WorkflowParseError

_API = "https://api.buildkite.com/v2/organizations"


class _Response(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, *args: object) -> object: ...

    def read(self, length: int = -1) -> bytes: ...


def concurrency_cancels(
    workflows: Sequence[Workflow], context: ExprContext
) -> bool | None:
    """Return whether older builds should be cancelled.

    ``None`` means none of the selected workflows declare concurrency, so
    admission does nothing. If any selected workflow cancels in progress, the
    build cancels. A default-branch Update does not.
    """
    flags: list[bool] = []
    for workflow in workflows:
        if workflow.concurrency is None:
            continue
        text = concurrency_expression(workflow.concurrency.cancel_in_progress)
        flags.append(truthy(context.eval(parse(text))))
    if not flags:
        return None
    return any(flags)


def apply_admission(
    workflows: Sequence[Workflow],
    context: ExprContext,
    builds: Sequence[BuildRef],
    *,
    current_id: str,
) -> Admission:
    """Return the admission decision for *builds*."""
    cancel = concurrency_cancels(workflows, context)
    if cancel is None:
        return Admission((), wait=False)
    return admit(builds, current_id=current_id, cancel=cancel)


def parse_build_list(payload: object) -> tuple[BuildRef, ...]:
    """Parse a Buildkite list-builds response into admission records."""
    if not isinstance(payload, list):
        message = "build list is not a list"
        raise WorkflowParseError(message)
    builds: list[BuildRef] = []
    for item in payload:
        parsed = _build_ref(item)
        if parsed is not None:
            builds.append(parsed)
    return tuple(builds)


def matching_job_ids(
    build: Mapping[str, object], labels: Sequence[str]
) -> tuple[str, ...]:
    """Return Buildkite job ids whose name or label is in *labels*."""
    raw_jobs = build.get("jobs")
    if not isinstance(raw_jobs, list):
        return ()
    wanted = set(labels)
    found: list[str] = []
    for item in raw_jobs:
        if not isinstance(item, Mapping):
            continue
        name = item.get("name") or item.get("label")
        job_id = item.get("id")
        if isinstance(name, str) and isinstance(job_id, str) and name in wanted:
            found.append(job_id)
    return tuple(found)


def fetch_branch_builds(
    *,
    organization: str,
    pipeline: str,
    branch: str,
    token: str,
    opener: Callable[..., object] | None = None,
) -> tuple[BuildRef, ...]:
    """Return the newest page of builds on *branch*."""
    query = urllib.parse.urlencode({"branch": branch, "per_page": "100"})
    url = f"{_API}/{organization}/pipelines/{pipeline}/builds?{query}"
    return parse_build_list(_read_json(url, token, opener, method="GET", body=None))


def cancel_build(
    *,
    organization: str,
    pipeline: str,
    build_id: str,
    token: str,
    opener: Callable[..., object] | None = None,
) -> None:
    """Cancel one build. The id may be a number or a UUID."""
    url = f"{_API}/{organization}/pipelines/{pipeline}/builds/{build_id}/cancel"
    _read_json(url, token, opener, method="PUT", body=b"{}")


def cancel_job(
    *,
    organization: str,
    pipeline: str,
    number: str,
    job_id: str,
    token: str,
    opener: Callable[..., object] | None = None,
) -> None:
    """Cancel one job inside a build."""
    url = f"{_API}/{organization}/pipelines/{pipeline}/builds/{number}/jobs/{job_id}/cancel"
    _read_json(url, token, opener, method="PUT", body=b"{}")


def _build_ref(item: object) -> BuildRef | None:
    if not isinstance(item, dict):
        return None
    build_id = item.get("id")
    state = item.get("state")
    number = item.get("number")
    if not isinstance(build_id, str) or not isinstance(state, str):
        return None
    if isinstance(number, int) and not isinstance(number, bool):
        return BuildRef(build_id, number, state)
    if isinstance(number, str) and number.isdigit():
        return BuildRef(build_id, int(number), state)
    return None


def _read_json(
    url: str,
    token: str,
    opener: Callable[..., object] | None,
    *,
    method: str,
    body: bytes | None,
) -> object:
    request = urllib.request.Request(  # noqa: S310 -- fixed https Buildkite API
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "nixcfg-buildkite",
            "Content-Type": "application/json",
        },
    )
    open_url = opener or urllib.request.urlopen  # noqa: S310 -- fixed https Buildkite API
    with cast("_Response", open_url(request, timeout=15)) as response:
        return json.load(response)
