"""Read ``.github/workflows`` into the job graph Buildkite follows.

Runners, ``needs``, matrices, timeouts, and secrets come from the workflow
files. A reusable ``workflow_call`` stays a component; callers bind its
``with:`` inputs.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from lib.buildkite.github_expr import Call, Name, expression_text, parse

if TYPE_CHECKING:
    from collections.abc import Mapping

_WORKFLOW_DIR = Path(".github/workflows")
_SECRET = re.compile(r"secrets\.([A-Z][A-Z0-9_]*)")
_QUEUES = {
    "ubuntu-24.04": "linux-x86",
    "ubuntu-24.04-arm": "linux-arm",
    "macos-15": "macos-arm",
}
# GitHub's Intel macOS labels. This repo does not use them; the queue exists
# only so a native x86_64-darwin runner cannot be silently dropped.
_INTEL_MACOS = frozenset({
    "macos-12",
    "macos-12-large",
    "macos-13",
    "macos-13-large",
    "macos-13-xlarge",
})
_NATIVE_WORKFLOW = "./.github/workflows/update-native.yml"


class WorkflowParseError(ValueError):
    """A workflow file is not in the shape this generator understands."""


@dataclass(frozen=True)
class Matrix:
    """One job's fan-out.

    ``dynamic_from`` is ``job.output`` when the axes are produced at runtime.
    """

    fail_fast: bool
    max_parallel: int | None
    axes: tuple[tuple[str, tuple[str, ...]], ...]
    dynamic_from: str | None

    @property
    def dynamic(self) -> bool:
        """Whether the cell list is a ``fromJSON(needs…)`` value."""
        return self.dynamic_from is not None


@dataclass(frozen=True)
class ActionStep:
    """One Actions step, still carrying the workflow's expressions."""

    step_id: str | None
    name: str
    run: str | None
    shell: str | None
    env: tuple[tuple[str, str], ...]
    if_expr: str | None
    continue_on_error: bool
    timeout_minutes: int | None
    uses: str | None
    with_args: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Job:
    """One Actions job, or one caller of a reusable workflow."""

    workflow: str
    job_id: str
    name: str
    needs: tuple[str, ...]
    runs_on: str
    queue: str | None
    timeout_minutes: int | None
    if_expr: str | None
    matrix: Matrix | None
    secrets: tuple[str, ...]
    outputs: tuple[tuple[str, str], ...]
    environment: str | None
    uses: str | None
    with_args: tuple[tuple[str, str], ...]
    steps: tuple[ActionStep, ...]


@dataclass(frozen=True)
class Concurrency:
    """The workflow ``concurrency`` block, with expressions still unevaluated."""

    group: str
    cancel_in_progress: str


@dataclass(frozen=True)
class Triggers:
    """Which builds should upload this workflow."""

    events: frozenset[str]
    push_branches: tuple[str, ...] | None
    push_paths: tuple[str, ...] | None
    pull_paths: tuple[str, ...] | None
    crons: tuple[str, ...]


@dataclass(frozen=True)
class Workflow:
    """One ``.github/workflows`` file."""

    filename: str
    name: str
    triggers: Triggers
    concurrency: Concurrency | None
    jobs: tuple[Job, ...]
    call_secrets: tuple[str, ...]

    def job(self, job_id: str) -> Job:
        """Return the job named *job_id*."""
        for job in self.jobs:
            if job.job_id == job_id:
                return job
        message = f"{self.filename} has no job {job_id}"
        raise WorkflowParseError(message)


def queue_for_runner(runner: str) -> str:
    """Map a GitHub-hosted runner label to a Buildkite agent queue."""
    if runner in _QUEUES:
        return _QUEUES[runner]
    if runner in _INTEL_MACOS or "x86_64-darwin" in runner:
        return "macos-x86"
    message = f"no Buildkite queue for runner {runner!r}"
    raise WorkflowParseError(message)


def load_action_steps(root: Path) -> tuple[ActionStep, ...]:
    """Return the steps of the shared update-runtime composite action."""
    runs = _mapping(_action_document(root).get("runs"), "update-runtime runs")
    return _steps(runs.get("steps"))


def load_action_contract(
    root: Path,
) -> tuple[dict[str, str], tuple[tuple[str, str], ...]]:
    """Return update-runtime input defaults and output expressions."""
    return contract_from_document(_action_document(root))


def contract_from_document(
    document: Mapping[str, object],
) -> tuple[dict[str, str], tuple[tuple[str, str], ...]]:
    """Read action input defaults and output expressions from one document."""
    defaults: dict[str, str] = {}
    inputs = document.get("inputs")
    if isinstance(inputs, dict):
        for name, body in inputs.items():
            if isinstance(body, dict) and isinstance(body.get("default"), str):
                defaults[str(name)] = body["default"]
    outputs: list[tuple[str, str]] = []
    declared = document.get("outputs")
    if isinstance(declared, dict):
        for name, body in declared.items():
            if isinstance(body, dict) and isinstance(body.get("value"), str):
                outputs.append((str(name), body["value"]))
    return defaults, tuple(outputs)


def _action_document(root: Path) -> dict[str, object]:
    path = root / ".github/actions/update-runtime/action.yml"
    return _load_yaml(path)


def load_workflows(root: Path) -> dict[str, Workflow]:
    """Parse every workflow file under *root*."""
    directory = root / _WORKFLOW_DIR
    parsed = {
        path.name: _workflow(path.name, _load_yaml(path))
        for path in sorted(directory.glob("*.yml"))
    }
    return {
        name: _with_caller_secrets(workflow, parsed)
        for name, workflow in parsed.items()
    }


def _load_yaml(path: Path) -> dict[str, object]:
    loaded = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    if not isinstance(loaded, dict):
        message = f"{path} did not parse as a mapping"
        raise WorkflowParseError(message)
    return loaded


def _workflow(filename: str, document: Mapping[str, object]) -> Workflow:
    triggers = _triggers(_mapping(document.get("on"), f"{filename} on"))
    jobs_raw = _mapping(document.get("jobs"), f"{filename} jobs")
    jobs = tuple(
        _job(filename, job_id, _mapping(body, job_id))
        for job_id, body in jobs_raw.items()
    )
    return Workflow(
        filename=filename,
        name=_string(document.get("name"), filename),
        triggers=triggers,
        concurrency=_concurrency(document.get("concurrency")),
        jobs=jobs,
        call_secrets=_call_secrets(document),
    )


def _with_caller_secrets(
    workflow: Workflow, workflows: Mapping[str, Workflow]
) -> Workflow:
    jobs = tuple(_attach_called_secrets(job, workflows) for job in workflow.jobs)
    return Workflow(
        filename=workflow.filename,
        name=workflow.name,
        triggers=workflow.triggers,
        concurrency=workflow.concurrency,
        jobs=jobs,
        call_secrets=workflow.call_secrets,
    )


def _attach_called_secrets(job: Job, workflows: Mapping[str, Workflow]) -> Job:
    if job.uses != _NATIVE_WORKFLOW:
        return job
    callee = workflows["update-native.yml"]
    names = set(job.secrets)
    names.update(callee.call_secrets)
    for called in callee.jobs:
        names.update(called.secrets)
    return Job(
        workflow=job.workflow,
        job_id=job.job_id,
        name=job.name,
        needs=job.needs,
        runs_on=job.runs_on,
        queue=job.queue,
        timeout_minutes=job.timeout_minutes or callee.jobs[0].timeout_minutes,
        if_expr=job.if_expr,
        matrix=job.matrix,
        secrets=tuple(sorted(names)),
        outputs=job.outputs or callee.jobs[0].outputs,
        environment=job.environment,
        uses=job.uses,
        with_args=job.with_args,
        steps=callee.jobs[0].steps,
    )


def _job(filename: str, job_id: str, body: Mapping[str, object]) -> Job:
    uses = body.get("uses")
    uses_text = uses if isinstance(uses, str) else None
    runs_on = _runs_on(body)
    timeout = _optional_int(body.get("timeout-minutes"))
    with_args = _pairs(body.get("with"))
    if not runs_on:
        runs_on = _literal_with(with_args, "runner")
    queue = (
        queue_for_runner(runs_on) if runs_on and not runs_on.startswith("${{") else None
    )
    return Job(
        workflow=filename,
        job_id=job_id,
        name=_optional_string(body.get("name")) or job_id,
        needs=_needs(body.get("needs")),
        runs_on=runs_on,
        queue=queue,
        timeout_minutes=timeout,
        if_expr=_optional_string(body.get("if")),
        matrix=_matrix(body.get("strategy")),
        secrets=_secrets_in(body),
        outputs=_pairs(body.get("outputs")),
        environment=_optional_string(body.get("environment")),
        uses=uses_text,
        with_args=with_args,
        steps=_steps(body.get("steps")),
    )


def _runs_on(body: Mapping[str, object]) -> str:
    value = body.get("runs-on")
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    message = f"runs-on must be a string, found {value!r}"
    raise WorkflowParseError(message)


def _literal_with(pairs: tuple[tuple[str, str], ...], key: str) -> str:
    for name, value in pairs:
        if name == key and not value.strip().startswith("${{"):
            return value
    return ""


def _matrix(strategy: object) -> Matrix | None:
    if strategy is None:
        return None
    body = _mapping(strategy, "strategy")
    matrix = body.get("matrix")
    if matrix is None:
        return None
    fail_fast = _as_bool(body.get("fail-fast"), default=True)
    max_parallel = _optional_int(body.get("max-parallel"))
    if isinstance(matrix, str):
        return Matrix(fail_fast, max_parallel, (), _dynamic_matrix(matrix))
    axes = _mapping(matrix, "matrix")
    parsed = tuple(
        (key, tuple(_string(item, key) for item in _sequence(value, key)))
        for key, value in axes.items()
        if key not in {"include", "exclude"}
    )
    return Matrix(fail_fast, max_parallel, parsed, None)


def _dynamic_matrix(expression: str) -> str:
    body = expression_text(expression)
    if body is None:
        message = f"matrix expression is not ${{{{ }}}}: {expression!r}"
        raise WorkflowParseError(message)
    parsed = parse(body)
    if (
        not isinstance(parsed, Call)
        or parsed.name != "fromJSON"
        or len(parsed.args) != 1
    ):
        message = f"dynamic matrix must be fromJSON(needs…): {expression!r}"
        raise WorkflowParseError(message)
    name = parsed.args[0]
    if (
        not isinstance(name, Name)
        or name.path[:1] != ("needs",)
        or "outputs" not in name.path
    ):
        message = f"dynamic matrix does not read a job output: {expression!r}"
        raise WorkflowParseError(message)
    job_id = name.path[1]
    output = name.path[-1]
    return f"{job_id}.{output}"


def _steps(value: object) -> tuple[ActionStep, ...]:
    if value is None:
        return ()
    return tuple(_step(_mapping(item, "step")) for item in _sequence(value, "steps"))


def _step(body: Mapping[str, object]) -> ActionStep:
    uses = body.get("uses")
    fallback = uses if isinstance(uses, str) else "step"
    return ActionStep(
        step_id=_optional_string(body.get("id")),
        name=_optional_string(body.get("name")) or fallback,
        run=_optional_string(body.get("run")),
        shell=_optional_string(body.get("shell")),
        env=_pairs(body.get("env")),
        if_expr=_optional_string(body.get("if")),
        continue_on_error=_as_bool(body.get("continue-on-error"), default=False),
        timeout_minutes=_optional_int(body.get("timeout-minutes")),
        uses=uses if isinstance(uses, str) else None,
        with_args=_pairs(body.get("with")),
    )


def _triggers(body: Mapping[str, object]) -> Triggers:
    events: set[str] = set()
    push_branches: tuple[str, ...] | None = None
    push_paths: tuple[str, ...] | None = None
    pull_paths: tuple[str, ...] | None = None
    crons: tuple[str, ...] = ()
    if "workflow_dispatch" in body:
        events.add("workflow_dispatch")
    if "workflow_call" in body:
        events.add("workflow_call")
    if "pull_request" in body:
        events.add("pull_request")
        pull = body["pull_request"]
        if isinstance(pull, dict):
            pull_paths = _optional_strings(pull.get("paths"))
    if "push" in body:
        events.add("push")
        push = body["push"]
        if isinstance(push, dict):
            push_branches = _optional_strings(push.get("branches"))
            push_paths = _optional_strings(push.get("paths"))
    if "schedule" in body:
        events.add("schedule")
        crons = tuple(
            _string(_mapping(item, "schedule").get("cron"), "cron")
            for item in _sequence(body["schedule"], "schedule")
        )
    return Triggers(
        events=frozenset(events),
        push_branches=push_branches,
        push_paths=push_paths,
        pull_paths=pull_paths,
        crons=crons,
    )


def _concurrency(value: object) -> Concurrency | None:
    if value is None:
        return None
    body = _mapping(value, "concurrency")
    cancel = body.get("cancel-in-progress", "false")
    return Concurrency(
        group=_string(body.get("group"), "concurrency.group"),
        cancel_in_progress=_string(cancel, "cancel-in-progress"),
    )


def _call_secrets(document: Mapping[str, object]) -> tuple[str, ...]:
    on = document.get("on")
    if not isinstance(on, dict):
        return ()
    call = on.get("workflow_call")
    if not isinstance(call, dict):
        return ()
    secrets = call.get("secrets")
    if not isinstance(secrets, dict):
        return ()
    return tuple(sorted(str(name) for name in secrets))


def _secrets_in(body: Mapping[str, object]) -> tuple[str, ...]:
    text = _flatten(body)
    names = set(_SECRET.findall(text))
    if "github.token" in text:
        names.add("GITHUB_TOKEN")
    return tuple(sorted(names))


def _flatten(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(f"{key}\n{_flatten(item)}" for key, item in value.items())
    if isinstance(value, list):
        return "\n".join(_flatten(item) for item in value)
    return ""


def _needs(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(_string(item, "needs") for item in _sequence(value, "needs"))


def _pairs(value: object) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    body = _mapping(value, "mapping")
    return tuple((str(key), _as_text(item)) for key, item in body.items())


def _as_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    message = f"expected text, found {value!r}"
    raise WorkflowParseError(message)


def _optional_strings(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    return tuple(_string(item, "filter") for item in _sequence(value, "filter"))


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return _string(value, "text").strip()


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, str) and value.isdigit():
        return int(value)
    message = f"expected an integer, found {value!r}"
    raise WorkflowParseError(message)


def _as_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if value == "true":
        return True
    if value == "false":
        return False
    message = f"expected a boolean, found {value!r}"
    raise WorkflowParseError(message)


def _string(value: object, label: str) -> str:
    if isinstance(value, str):
        return value
    message = f"{label} must be a string"
    raise WorkflowParseError(message)


def _mapping(value: object, label: str) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    message = f"{label} must be a mapping"
    raise WorkflowParseError(message)


def _sequence(value: object, label: str) -> list[object]:
    if isinstance(value, list):
        return value
    message = f"{label} must be a sequence"
    raise WorkflowParseError(message)
