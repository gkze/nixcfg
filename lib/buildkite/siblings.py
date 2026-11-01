"""Expose Buildkite jobs in the shape ``sibling_failfast.parse_workflow_jobs`` reads.

``jobs.py`` polls this module while a native stage runs. The decision stays in
``sibling_stop``. This file only translates Buildkite job states. A missing
token exits non-zero so the poll error keeps the build running, which is the
same failure policy as an Actions API error.
"""

import json
import os
import sys
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol, Self, cast

_ACTIVE = frozenset({
    "scheduled",
    "running",
    "failing",
    "canceling",
    "waiting",
    "waiting_failed",
    "blocked",
    "blocked_failed",
    "assigned",
    "accepted",
    "unblocked",
    "timing_out",
    "pending",
})
_CONCLUSIONS = {
    "passed": "success",
    "finished": "success",
    "failed": "failure",
    "timed_out": "timed_out",
    "canceled": "cancelled",
    "skipped": "skipped",
    "broken": "startup_failure",
    "waiting_failed": "startup_failure",
    "blocked_failed": "startup_failure",
}


class _Response(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, *args: object) -> object: ...

    def read(self, length: int = -1) -> bytes: ...


def conclusion_for_state(state: str) -> tuple[str, str | None]:
    """Return ``(status, conclusion)`` for one Buildkite job state."""
    if state in _CONCLUSIONS:
        return "completed", _CONCLUSIONS[state]
    if state in _ACTIVE:
        return "in_progress", None
    return "in_progress", None


def jobs_payload(build: Mapping[str, object]) -> dict[str, object]:
    """Translate a Buildkite build into an Actions List-jobs document."""
    raw_jobs = build.get("jobs")
    if not isinstance(raw_jobs, list):
        return {"jobs": []}
    jobs: list[dict[str, object]] = []
    for item in raw_jobs:
        if not isinstance(item, Mapping):
            continue
        name = item.get("name") or item.get("label")
        state = item.get("state")
        if not isinstance(name, str) or not isinstance(state, str):
            continue
        status, conclusion = conclusion_for_state(state)
        finished = item.get("finished_at")
        jobs.append({
            "name": name,
            "status": status,
            "conclusion": conclusion,
            "completed_at": finished if isinstance(finished, str) else "",
            "steps": [],
        })
    return {"jobs": jobs}


def fetch_build(
    *,
    organization: str,
    pipeline: str,
    number: str,
    token: str,
    opener: Callable[..., object] | None = None,
) -> Mapping[str, object]:
    """Return one build document from the Buildkite REST API."""
    url = (
        "https://api.buildkite.com/v2/organizations/"
        f"{organization}/pipelines/{pipeline}/builds/{number}"
    )
    request = urllib.request.Request(  # noqa: S310 -- fixed https Buildkite API
        url,
        headers={"Authorization": f"Bearer {token}", "User-Agent": "nixcfg-buildkite"},
    )
    open_url = opener or urllib.request.urlopen  # noqa: S310 -- fixed https Buildkite API
    with cast("_Response", open_url(request, timeout=15)) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        message = "Buildkite build document is not an object"
        raise TypeError(message)
    return payload


def write_jobs(path: Path, payload: Mapping[str, object]) -> None:
    """Atomically replace *path* with the jobs document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(path)


def main(env: Mapping[str, str] | None = None) -> int:
    """Write the current build's jobs for the sibling poll."""
    values = os.environ if env is None else env
    destination = values.get("NIXCFG_SIBLING_JOBS_PATH", "").strip()
    token = values.get("BUILDKITE_API_TOKEN", "").strip()
    organization = values.get("BUILDKITE_ORGANIZATION_SLUG", "").strip()
    pipeline = values.get("BUILDKITE_PIPELINE_SLUG", "").strip()
    number = values.get("BUILDKITE_BUILD_NUMBER", "").strip()
    if not destination or not token or not organization or not pipeline or not number:
        return 2
    try:
        build = fetch_build(
            organization=organization,
            pipeline=pipeline,
            number=number,
            token=token,
        )
    except OSError, json.JSONDecodeError, TypeError, UnicodeError:
        return 3
    write_jobs(Path(destination), jobs_payload(build))
    return 0


if __name__ == "__main__":  # pragma: no cover -- delegate to the tested main
    sys.exit(main())
