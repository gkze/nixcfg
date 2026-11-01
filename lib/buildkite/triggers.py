"""Decide which Actions workflows a Buildkite build should upload.

Path and branch filters use GitHub's rules: ``*`` stays inside one path
segment and ``**`` crosses segments. An explicit ``NIXCFG_BUILDKITE_WORKFLOW``
selects one file. A manual build with no selection uploads nothing, so the
Buildkite "New Build" button does not start Update by accident.
"""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lib.buildkite.github_expr import ExprContext
from lib.buildkite.graph import Workflow, WorkflowParseError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


@dataclass(frozen=True)
class BuildEvent:
    """The parts of a Buildkite build that correspond to a GitHub event."""

    source: str
    branch: str
    default_branch: str
    changed_paths: tuple[str, ...] | None
    workflow: str | None
    head_repository: str
    repository: str
    sha: str
    pull_request: str
    build_number: str
    build_id: str


def event_source(env: Mapping[str, str]) -> str:
    """Map Buildkite's build source to an Actions event name."""
    if env.get("BUILDKITE_PULL_REQUEST", "false") not in {"", "false"}:
        return "pull_request"
    source = env.get("BUILDKITE_SOURCE", "")
    if source == "schedule":
        return "schedule"
    if source in {"ui", "api", "trigger"}:
        return "workflow_dispatch"
    return "push"


def event_from_env(
    env: Mapping[str, str], changed_paths: tuple[str, ...] | None
) -> BuildEvent:
    """Build a :class:`BuildEvent` from the agent environment."""
    repository = env.get("NIXCFG_REPOSITORY", "gkze/nixcfg")
    return BuildEvent(
        source=event_source(env),
        branch=env.get("BUILDKITE_BRANCH", ""),
        default_branch=env.get("NIXCFG_DEFAULT_BRANCH", "main"),
        changed_paths=changed_paths,
        workflow=_optional(env.get("NIXCFG_BUILDKITE_WORKFLOW")),
        head_repository=env.get("NIXCFG_HEAD_REPOSITORY", repository),
        repository=repository,
        sha=env.get("BUILDKITE_COMMIT", ""),
        pull_request=env.get("BUILDKITE_PULL_REQUEST", "false"),
        build_number=env.get("BUILDKITE_BUILD_NUMBER", ""),
        build_id=env.get("BUILDKITE_BUILD_ID", ""),
    )


def select_workflows(
    workflows: Sequence[Workflow], event: BuildEvent
) -> tuple[Workflow, ...]:
    """Return the workflows whose triggers match *event*, in file order."""
    if event.workflow:
        match = [
            workflow
            for workflow in workflows
            if _names_workflow(workflow, event.workflow)
        ]
        if not match:
            message = f"unknown workflow {event.workflow}"
            raise WorkflowParseError(message)
        return tuple(match)
    if event.source == "workflow_dispatch":
        return ()
    return tuple(workflow for workflow in workflows if _matches(workflow, event))


def path_matches(pattern: str, path: str) -> bool:
    """Return whether a GitHub path filter matches *path*."""
    escaped = re.escape(pattern).replace(r"\*\*", "\x00").replace(r"\*", "[^/]*")
    escaped = escaped.replace("\x00", ".*")
    return re.fullmatch(escaped, path) is not None


def expr_context(
    event: BuildEvent,
    *,
    inputs: Mapping[str, object] | None = None,
    needs: Mapping[str, Mapping[str, object]] | None = None,
    steps: Mapping[str, Mapping[str, object]] | None = None,
    secrets: Mapping[str, object] | None = None,
    matrix: Mapping[str, object] | None = None,
    runner_temp: str = "",
    success: bool = True,
    failure: bool = False,
    cancelled: bool = False,
) -> ExprContext:
    """Return an expression context for *event*."""
    return ExprContext(
        inputs={} if inputs is None else inputs,
        needs={} if needs is None else needs,
        steps={} if steps is None else steps,
        github=_github(event),
        runner={"temp": runner_temp, "os": "", "arch": ""},
        secrets={} if secrets is None else secrets,
        matrix={} if matrix is None else matrix,
        success=success,
        failure=failure,
        cancelled=cancelled,
    )


def workflow_inputs(event: BuildEvent, env: Mapping[str, str]) -> dict[str, object]:
    """Return Actions ``inputs`` for this build.

    Push and schedule events do not define inputs. Dispatch inputs stay null
    when the matching environment variable is unset, because ``gh workflow run``
    does not apply the YAML defaults either.
    """
    if event.source != "workflow_dispatch":
        return {}
    values: dict[str, object] = {}
    for name, variable in _INPUTS.items():
        if variable not in env:
            values[name] = None
            continue
        raw = env[variable]
        if name in _BOOLEAN_INPUTS:
            values[name] = _bool_input(raw)
            continue
        values[name] = raw
    return values


def _matches(workflow: Workflow, event: BuildEvent) -> bool:
    triggers = workflow.triggers
    if event.source not in triggers.events:
        return False
    if event.source == "schedule":
        return True
    if event.source == "pull_request":
        return _paths_match(triggers.pull_paths, event.changed_paths)
    if event.source == "push":
        return _branches_match(triggers.push_branches, event.branch) and _paths_match(
            triggers.push_paths, event.changed_paths
        )
    return False


def _paths_match(
    patterns: tuple[str, ...] | None, changed: tuple[str, ...] | None
) -> bool:
    if patterns is None:
        return True
    if changed is None:
        return True
    return any(path_matches(pattern, path) for pattern in patterns for path in changed)


def _branches_match(patterns: tuple[str, ...] | None, branch: str) -> bool:
    if patterns is None:
        return True
    return any(path_matches(pattern, branch) for pattern in patterns)


def _names_workflow(workflow: Workflow, selection: str) -> bool:
    stem = workflow.filename.removesuffix(".yml")
    return selection in {workflow.filename, stem, workflow.name}


def _github(event: BuildEvent) -> dict[str, object]:
    number = None if event.pull_request in {"", "false"} else event.pull_request
    return {
        "event_name": event.source,
        "ref": f"refs/heads/{event.branch}",
        "ref_name": event.branch,
        "sha": event.sha,
        "repository": event.repository,
        "head_ref": event.branch,
        "run_id": event.build_number,
        "run_attempt": "1",
        "token": "",
        "event": {
            "inputs": {},
            "repository": {"default_branch": event.default_branch},
            "pull_request": {
                "number": number or "",
                "base": {"sha": ""},
                "head": {
                    "sha": event.sha,
                    "repo": {"full_name": event.head_repository},
                },
            },
        },
    }


def _optional(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _bool_input(value: str) -> object:
    if value == "true":
        return True
    if value == "false":
        return False
    if value == "":
        return None
    return value


_BOOLEAN_INPUTS = frozenset({"validate_all_packages", "repair", "canary_warm_rust"})
_INPUTS = {
    "targets": "NIXCFG_UPDATE_TARGETS",
    "validate_all_packages": "NIXCFG_VALIDATE_ALL_PACKAGES",
    "repair": "NIXCFG_REPAIR",
    "repair_commit": "NIXCFG_REPAIR_COMMIT",
    "canary_warm_rust": "NIXCFG_CANARY_WARM_RUST",
    "canary_slots": "NIXCFG_CANARY_SLOTS",
    "canary_crates": "NIXCFG_CANARY_CRATES",
}
