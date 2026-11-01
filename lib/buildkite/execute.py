"""Run one Actions job on a Buildkite agent.

The command list still comes from the workflow file. This module evaluates
``if``, expands ``${{ }}``, and turns checkout, artifacts, Cachix, and
``jobs.py`` stages into local commands. It does not reimplement a stage.
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from lib.buildkite.admission import cancel_job, matching_job_ids
from lib.buildkite.artifacts import arrange_download, stage_upload
from lib.buildkite.emit import (
    actions_job_name,
    cell_token,
    cells_from_output,
    render_continuation,
    static_cells,
    step_key,
)
from lib.buildkite.github_expr import (
    Binary,
    Call,
    Expr,
    ExprContext,
    Unary,
    expression_text,
    interpolate,
    parse,
    stringify,
    truthy,
)
from lib.buildkite.graph import (
    ActionStep,
    Job,
    Workflow,
    WorkflowParseError,
    load_action_contract,
    load_action_steps,
    load_workflows,
)
from lib.buildkite.runtime import (
    combine_results,
    daemon_argv,
    github_process_env,
    hook_script,
    nix_user_conf,
    prefer_conclusion,
    prepare_build_slots,
    publish_allowed,
    runner_identity,
)
from lib.buildkite.siblings import fetch_build
from lib.buildkite.triggers import (
    BuildEvent,
    event_from_env,
    expr_context,
    workflow_inputs,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

_META_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_KINDS = (
    ("actions/checkout", "checkout"),
    ("actions/setup-python", "setup-python"),
    ("actions/download-artifact", "download-artifact"),
    ("actions/upload-artifact", "upload-artifact"),
    ("actions/cache", "cache"),
    ("DeterminateSystems/determinate-nix-action", "nix"),
    ("cachix/cachix-action", "cachix"),
    ("crazy-max/ghaction-import-gpg", "gpg"),
    ("./.github/actions/update-runtime", "update-runtime"),
    ("./.github/workflows/update-native.yml", "update-native"),
)
_HARD = frozenset({"failure", "timed_out", "startup_failure"})


class CommandResult:
    """One finished command. ``timed_out`` keeps a timeout distinct from exit 1."""

    def __init__(
        self, returncode: int, stdout: str = "", *, timed_out: bool = False
    ) -> None:
        """Record the exit code, captured stdout, and whether the command timed out."""
        self.returncode = returncode
        self.stdout = stdout
        self.timed_out = timed_out


class Daemon(Protocol):
    """A process the runner started and must stop."""

    def poll(self) -> int | None:
        """Return the exit code once the process has finished."""
        ...

    def terminate(self) -> None:
        """Ask the process to exit."""
        ...

    def kill(self) -> None:
        """Stop the process when terminate did not."""
        ...

    def wait(self, timeout: float | None = None) -> int:
        """Wait until the process exits and return its code."""
        ...


class Runner(Protocol):
    """Run a command and return its result. Tests pass a fake."""

    def __call__(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str],
        cwd: Path,
        timeout: float | None,
        stdin: str | None,
    ) -> CommandResult:
        """Run *argv* and return its exit code and stdout."""
        ...


class Spawner(Protocol):
    """Start a long-lived process such as the per-job Cachix daemon."""

    def __call__(
        self, argv: Sequence[str], *, env: Mapping[str, str], cwd: Path
    ) -> Daemon:
        """Start *argv* and return the process handle."""
        ...


class Agent(Protocol):
    """Buildkite meta-data, artifacts, and pipeline upload."""

    def get_meta(self, key: str) -> str | None:
        """Return one meta-data value, or ``None`` when it is unset."""
        ...

    def set_meta(self, key: str, value: str) -> None:
        """Store one meta-data value."""
        ...

    def upload(self, name: str, path: Path) -> int:
        """Upload *path* under the Actions artifact name. Return the exit code."""
        ...

    def download(self, name: str | None, dest: Path) -> int:
        """Download one artifact, or every artifact when *name* is ``None``."""
        ...

    def upload_pipeline(self, document: Mapping[str, object]) -> None:
        """Upload a continuation pipeline."""
        ...

    def cancel_labeled(self, labels: Sequence[str]) -> None:
        """Cancel matrix cells whose labels are in *labels*."""
        ...


@dataclass
class StepOutcome:
    """The conclusion of one Actions step."""

    returncode: int
    timed_out: bool
    outputs: Mapping[str, str]


@dataclass
class RunState:
    """Mutable per-job state shared by the step handlers."""

    root: Path
    agent: Agent
    runner: Runner
    spawn: Spawner
    env: dict[str, str]
    job_name: str
    runner_temp: Path
    secret_names: tuple[str, ...] = ()
    needs: dict[str, dict[str, object]] = field(default_factory=dict)
    daemons: list[Daemon] = field(default_factory=list)
    step_index: int = 0


def action_kind(uses: str | None) -> str:
    """Classify an Actions ``uses:`` value. Unknown actions fail closed."""
    if uses is None:
        return "run"
    for prefix, kind in _KINDS:
        if uses == prefix or uses.startswith(prefix + "@"):
            return kind
    message = f"unsupported action {uses}"
    raise WorkflowParseError(message)


def parse_github_output(text: str) -> dict[str, str]:
    """Parse a ``GITHUB_OUTPUT`` file. The last assignment of a name wins."""
    values: dict[str, str] = {}
    pending: str | None = None
    delimiter = ""
    chunks: list[str] = []
    for line in text.splitlines():
        if pending is not None:
            if line == delimiter:
                values[pending] = "\n".join(chunks)
                pending = None
                chunks = []
            else:
                chunks.append(line)
            continue
        if "<<" in line:
            pending, delimiter = line.split("<<", 1)
            continue
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name] = value
    return values


def subprocess_runner(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Path,
    timeout: float | None,
    stdin: str | None,
) -> CommandResult:
    """Run *argv* and translate a timeout into exit 124."""
    try:
        completed = subprocess.run(  # noqa: S603 -- argv boundaries, never a shell
            list(argv),
            cwd=cwd,
            env=dict(env),
            input=stdin,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        return CommandResult(124, stdout, timed_out=True)
    return CommandResult(completed.returncode, completed.stdout or "")


def subprocess_daemon(
    argv: Sequence[str], *, env: Mapping[str, str], cwd: Path
) -> Daemon:
    """Start *argv* in its own session so flush can stop it without the agent."""
    return cast(
        "Daemon",
        subprocess.Popen(  # noqa: S603 -- argv boundaries, never a shell
            list(argv),
            cwd=cwd,
            env=dict(env),
            start_new_session=True,
            text=True,
        ),
    )


class MemoryAgent:
    """In-memory Buildkite agent for tests and local renders."""

    def __init__(self) -> None:
        """Start with empty meta-data, artifacts, and pipelines."""
        self.meta: dict[str, str] = {}
        self.artifacts: dict[str, dict[str, bytes]] = {}
        self.pipelines: list[dict[str, object]] = []
        self.cancelled: list[tuple[str, ...]] = []

    def get_meta(self, key: str) -> str | None:
        """Return one stored meta-data value."""
        return self.meta.get(key)

    def set_meta(self, key: str, value: str) -> None:
        """Store one meta-data value."""
        _require_meta_key(key)
        self.meta[key] = value

    def upload(self, name: str, path: Path) -> int:
        """Remember *path* under *name* and return 0."""
        files: dict[str, bytes] = {}
        if path.is_file():
            files[path.name] = path.read_bytes()
        elif path.is_dir():
            files = {
                str(item.relative_to(path)): item.read_bytes()
                for item in path.rglob("*")
                if item.is_file()
            }
        self.artifacts[name] = files
        return 0

    def download(self, name: str | None, dest: Path) -> int:
        """Write a stored artifact into *dest*. Return 1 when *name* is missing."""
        dest.mkdir(parents=True, exist_ok=True)
        if name is None:
            for artifact_name, files in self.artifacts.items():
                _write_files(dest / artifact_name, files)
            return 0
        files = self.artifacts.get(name)
        if files is None:
            return 1
        _write_files(dest, files)
        return 0

    def upload_pipeline(self, document: Mapping[str, object]) -> None:
        """Record a continuation document."""
        self.pipelines.append(dict(document))

    def cancel_labeled(self, labels: Sequence[str]) -> None:
        """Record the labels a fail-fast cancel would target."""
        self.cancelled.append(tuple(labels))


class BuildkiteAgent:
    """Adapt ``buildkite-agent`` to :class:`Agent`."""

    def __init__(
        self,
        env: Mapping[str, str],
        runner: Runner,
        *,
        opener: Callable[..., object] | None = None,
    ) -> None:
        """Bind the agent environment and the command runner."""
        self._env = env
        self._runner = runner
        self._opener = opener

    def get_meta(self, key: str) -> str | None:
        """Return one meta-data value from ``buildkite-agent``."""
        result = self._agent(["meta-data", "get", key], stdin=None)
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    def set_meta(self, key: str, value: str) -> None:
        """Store one meta-data value through ``buildkite-agent``."""
        _require_meta_key(key)
        result = self._agent(["meta-data", "set", key, value], stdin=None)
        if result.returncode != 0:
            message = f"meta-data set failed for {key}"
            raise WorkflowParseError(message)

    def upload(self, name: str, path: Path) -> int:
        """Upload *path* under ``bk-artifacts/<name>/``."""
        staging = Path(tempfile.mkdtemp())
        try:
            if stage_upload(staging, name, path) == 0:
                return 0
            return self._agent(
                ["artifact", "upload", "bk-artifacts/**/*"],
                stdin=None,
                cwd=staging,
            ).returncode
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def download(self, name: str | None, dest: Path) -> int:
        """Download artifacts and arrange them into the Actions destination."""
        pattern = "bk-artifacts/**/*" if name is None else f"bk-artifacts/{name}/**/*"
        staging = dest / ".bk-staging"
        result = self._agent(
            ["artifact", "download", pattern, str(staging)],
            stdin=None,
        )
        if result.returncode != 0:
            return result.returncode
        try:
            arrange_download(staging, dest, name)
        except WorkflowParseError:
            return 1
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return 0

    def upload_pipeline(self, document: Mapping[str, object]) -> None:
        """Upload a continuation pipeline as JSON."""
        result = self._agent(["pipeline", "upload"], stdin=json.dumps(document))
        if result.returncode != 0:
            message = "pipeline upload failed"
            raise WorkflowParseError(message)

    def cancel_labeled(self, labels: Sequence[str]) -> None:
        """Cancel jobs in this build whose names match *labels*."""
        if not labels:
            return
        token, organization, pipeline, number = _api_coordinates(self._env)
        if token is None or organization is None or pipeline is None or number is None:
            return
        try:
            build = fetch_build(
                organization=organization,
                pipeline=pipeline,
                number=number,
                token=token,
                opener=self._opener,
            )
        except OSError, TypeError, json.JSONDecodeError, UnicodeError:
            return
        for job_id in matching_job_ids(build, labels):
            cancel_job(
                organization=organization,
                pipeline=pipeline,
                number=number,
                job_id=job_id,
                token=token,
                opener=self._opener,
            )

    def _agent(
        self,
        args: Sequence[str],
        *,
        stdin: str | None,
        cwd: Path | None = None,
    ) -> CommandResult:
        return self._runner(
            ["buildkite-agent", *args],
            env=self._env,
            cwd=self._env_path("BUILDKITE_BUILD_CHECKOUT_PATH") if cwd is None else cwd,
            timeout=None,
            stdin=stdin,
        )

    def _env_path(self, name: str) -> Path:
        return Path(self._env.get(name, Path.cwd()))


def run_job(
    root: Path,
    filename: str,
    job_id: str,
    *,
    agent: Agent,
    env: Mapping[str, str],
    runner: Runner | None = None,
    spawn: Spawner | None = None,
) -> int:
    """Run one concrete job or matrix cell. Return 0 when the job did not fail."""
    workflow = load_workflows(root)[filename]
    job = workflow.job(job_id)
    cell = cell_from_env(env)
    key = step_key(filename, job_id, cell or None)
    agent.set_meta(result_key(key), "failure")
    state = _state(root, job, cell, agent, env, runner, spawn)
    state.secret_names = job.secrets
    state.needs = needs_from_meta(workflow, agent)
    inputs = _job_inputs(state, job, cell)
    success, failure = _entry_status(state, job)
    context = _context(
        state, inputs=inputs, matrix=cell, steps={}, success=success, failure=failure
    )
    if not enabled(job.if_expr, context):
        agent.set_meta(result_key(key), "skipped")
        return 0
    conclusion = "success"
    hard = False
    outputs: dict[str, dict[str, object]] = {}
    try:
        for step in _job_steps(job):
            context = _context(
                state,
                inputs=inputs,
                matrix=cell,
                steps=outputs,
                success=not hard,
                failure=hard,
            )
            if not enabled(step.if_expr, context):
                continue
            outcome = run_step(state, step, context)
            if step.step_id is not None:
                outputs[step.step_id] = {"outputs": dict(outcome.outputs)}
            conclusion, hard = _fold(conclusion, step, outcome, hard=hard)
        final = _context(
            state,
            inputs=inputs,
            matrix=cell,
            steps=outputs,
            success=not hard,
            failure=hard,
        )
        _write_outputs(job, key, agent, final)
    finally:
        _stop_daemons(state.daemons)
    agent.set_meta(result_key(key), conclusion)
    if job.matrix is not None and job.matrix.fail_fast and conclusion in _HARD:
        agent.cancel_labeled(_fail_fast_labels(job_id, job, cell))
    if conclusion in _HARD:
        return 1
    return 0


def fanout(
    root: Path,
    filename: str,
    job_id: str,
    *,
    agent: Agent,
    env: Mapping[str, str],
) -> int:
    """Upload matrix cells once the producing job has written its output."""
    workflow = load_workflows(root)[filename]
    job = workflow.job(job_id)
    dynamic_from = job.matrix.dynamic_from if job.matrix is not None else None
    if dynamic_from is None:
        message = f"{job_id} is not a dynamic matrix"
        raise WorkflowParseError(message)
    producer, _, output = dynamic_from.partition(".")
    state = _state(root, job, {}, agent, env, None, None)
    state.secret_names = job.secrets
    state.needs = needs_from_meta(workflow, agent)
    inputs = workflow_inputs(state_event(env), env)
    success, failure = _entry_status(state, job)
    context = _context(
        state, inputs=inputs, matrix={}, steps={}, success=success, failure=failure
    )
    if not enabled(job.if_expr, context):
        agent.upload_pipeline(
            render_continuation(workflow, job_id, (), context=context)
        )
        return 0
    raw = agent.get_meta(output_key(step_key(filename, producer), output))
    cells = _cells(raw)
    if cells is None:
        return 1
    tokens = ",".join(cell_token(cell) for cell in cells)
    agent.set_meta(cells_key(step_key(filename, job_id)), tokens)
    agent.upload_pipeline(render_continuation(workflow, job_id, cells, context=context))
    return 0


def aggregate(
    root: Path,
    filename: str,
    job_id: str,
    *,
    agent: Agent,
    env: Mapping[str, str],
) -> int:
    """Write one matrix conclusion from the cell conclusions."""
    del root, env
    parent = step_key(filename, job_id)
    raw = agent.get_meta(cells_key(parent)) or ""
    tokens = [token for token in raw.split(",") if token]
    results = [
        agent.get_meta(result_key(f"{parent}--{token}")) or "skipped"
        for token in tokens
    ]
    agent.set_meta(result_key(parent), combine_results(results))
    return 0


def skip_job(
    root: Path,
    filename: str,
    job_id: str,
    *,
    agent: Agent,
    env: Mapping[str, str],
) -> int:
    """Record a matrix that produced no cells as skipped."""
    del root, env
    agent.set_meta(result_key(step_key(filename, job_id)), "skipped")
    return 0


def run_step(state: RunState, step: ActionStep, context: ExprContext) -> StepOutcome:
    """Run one Actions step."""
    kind = action_kind(step.uses)
    if kind == "update-runtime":
        return _run_runtime(state, step, context)
    return _HANDLERS[kind](state, step, context)


def needs_from_meta(workflow: Workflow, agent: Agent) -> dict[str, dict[str, object]]:
    """Rebuild the Actions ``needs`` context from Buildkite meta-data."""
    viewed: dict[str, dict[str, object]] = {}
    for job in workflow.jobs:
        key = step_key(workflow.filename, job.job_id)
        viewed[job.job_id] = {
            "result": _job_result(workflow.filename, job, agent),
            "outputs": _stored_outputs(job, key, agent),
        }
    return viewed


def enabled(if_expr: str | None, context: ExprContext) -> bool:
    """Evaluate an Actions ``if``.

    A missing condition means ``success()``. An expression that does not call
    ``success``, ``failure``, ``always``, or ``cancelled`` is prefixed with
    ``success() &&``, matching Actions.
    """
    if if_expr is None:
        return context.success
    body = expression_text(if_expr)
    parsed = parse(if_expr if body is None else body)
    result = truthy(context.eval(parsed))
    if _has_status(parsed):
        return result
    return context.success and result


def _entry_status(state: RunState, job: Job) -> tuple[bool, bool]:
    """Return ``(success, failure)`` from ``needs`` for a job that is starting."""
    if not job.needs:
        return True, False
    succeeded = all(state.needs[need]["result"] == "success" for need in job.needs)
    return succeeded, not succeeded


def _has_status(expr: Expr) -> bool:
    if isinstance(expr, Call) and expr.name in {
        "success",
        "failure",
        "always",
        "cancelled",
    }:
        return True
    if isinstance(expr, Call):
        return any(_has_status(arg) for arg in expr.args)
    if isinstance(expr, Unary):
        return _has_status(expr.item)
    if isinstance(expr, Binary):
        return _has_status(expr.left) or _has_status(expr.right)
    return False


def eval_text(text: str, context: ExprContext) -> str:
    """Evaluate one output expression or interpolate a mixed string."""
    if "${{" in text:
        return interpolate(text, context).strip()
    stripped = text.strip()
    if not stripped:
        return ""
    return stringify(context.eval(parse(stripped))).strip()


def cell_from_env(env: Mapping[str, str]) -> dict[str, str]:
    """Return the matrix cell a step was given, or an empty mapping."""
    raw = env.get("NIXCFG_BUILDKITE_CELL", "").strip()
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        message = "NIXCFG_BUILDKITE_CELL is not an object"
        raise WorkflowParseError(message)
    return {str(key): str(value) for key, value in parsed.items()}


def result_key(step: str) -> str:
    """Return the meta-data key that stores one step's Actions conclusion."""
    return f"result--{step}"


def output_key(step: str, name: str) -> str:
    """Return the meta-data key that stores one job output."""
    return f"output--{step}--{name}"


def cells_key(step: str) -> str:
    """Return the meta-data key that stores a dynamic matrix's cell tokens."""
    return f"cells--{step}"


def _state(
    root: Path,
    job: Job,
    cell: Mapping[str, str],
    agent: Agent,
    env: Mapping[str, str],
    runner: Runner | None,
    spawn: Spawner | None,
) -> RunState:
    temp = Path(env.get("RUNNER_TEMP", "")) if env.get("RUNNER_TEMP") else None
    runner_temp = temp if temp is not None else Path(tempfile.mkdtemp())
    runner_temp.mkdir(parents=True, exist_ok=True)
    return RunState(
        root=root,
        agent=agent,
        runner=runner or subprocess_runner,
        spawn=spawn or subprocess_daemon,
        env=dict(env),
        job_name=actions_job_name(job.job_id, cell or None),
        runner_temp=runner_temp,
    )


def _job_inputs(
    state: RunState, job: Job, cell: Mapping[str, str]
) -> dict[str, object]:
    event_env = state.env
    inputs: dict[str, object] = dict(workflow_inputs(state_event(event_env), event_env))
    if job.uses is None:
        return inputs
    parent = _context(
        state, inputs=inputs, matrix=cell, steps={}, success=True, failure=False
    )
    return {key: interpolate(value, parent) for key, value in job.with_args}


def _context(
    state: RunState,
    *,
    inputs: Mapping[str, object],
    matrix: Mapping[str, object],
    steps: Mapping[str, Mapping[str, object]],
    success: bool,
    failure: bool,
) -> ExprContext:
    event = state_event(state.env)
    context = expr_context(
        event,
        inputs=inputs,
        needs=state.needs,
        steps=steps,
        secrets={name: state.env.get(name, "") for name in state.secret_names},
        matrix=matrix,
        runner_temp=str(state.runner_temp),
        success=success,
        failure=failure,
        cancelled=state.env.get("BUILDKITE_BUILD_STATE") == "canceling",
    )
    os_name, arch = runner_identity()
    github = dict(context.github)
    github["token"] = state.env.get("GITHUB_TOKEN", "")
    github["job"] = state.job_name
    context.github = github
    context.runner = {"temp": str(state.runner_temp), "os": os_name, "arch": arch}
    return context


def state_event(env: Mapping[str, str]) -> BuildEvent:
    """Return the build event stored beside the process environment."""
    return event_from_env(env, None)


def _job_steps(job: Job) -> tuple[ActionStep, ...]:
    if job.uses is None:
        return job.steps
    if action_kind(job.uses) != "update-native":
        message = f"unsupported reusable workflow {job.uses}"
        raise WorkflowParseError(message)
    return job.steps


def _fold(
    conclusion: str, step: ActionStep, outcome: StepOutcome, *, hard: bool
) -> tuple[str, bool]:
    if outcome.timed_out:
        return prefer_conclusion(conclusion, "timed_out"), True
    if outcome.returncode != 0 and not step.continue_on_error:
        return prefer_conclusion(conclusion, "failure"), True
    return conclusion, hard


def _write_outputs(job: Job, key: str, agent: Agent, context: ExprContext) -> None:
    for name, expression in job.outputs:
        agent.set_meta(output_key(key, name), eval_text(expression, context))


def _fail_fast_labels(
    job_id: str, job: Job, failed: Mapping[str, str]
) -> tuple[str, ...]:
    if job.matrix is None:
        return ()
    return tuple(
        actions_job_name(job_id, cell)
        for cell in static_cells(job.matrix)
        if cell != dict(failed)
    )


def _job_result(filename: str, job: Job, agent: Agent) -> str:
    if job.matrix is not None and job.matrix.axes and not job.matrix.dynamic:
        results = [
            agent.get_meta(result_key(step_key(filename, job.job_id, cell)))
            or "skipped"
            for cell in static_cells(job.matrix)
        ]
        return combine_results(results)
    stored = agent.get_meta(result_key(step_key(filename, job.job_id)))
    return stored or "skipped"


def _stored_outputs(job: Job, key: str, agent: Agent) -> dict[str, str]:
    outputs: dict[str, str] = {}
    for name, _expression in job.outputs:
        value = agent.get_meta(output_key(key, name))
        if value is not None:
            outputs[name] = value
    return outputs


def _cells(raw: str | None) -> tuple[dict[str, str], ...] | None:
    if raw is None or raw == "":
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    cells = cells_from_output(payload)
    if not cells:
        return None
    return cells


def _run_script(state: RunState, step: ActionStep, context: ExprContext) -> StepOutcome:
    if step.run is None:
        message = f"{step.name} has no command"
        raise WorkflowParseError(message)
    step_env = _script_env(state, step, context)
    stage = step_env.get("NIXCFG_CI_STAGE", "")
    if stage == "clean-image":
        return StepOutcome(0, timed_out=False, outputs={})
    if not publish_allowed(stage, step_env.get("NIXCFG_BUILDKITE_PUBLISH")):
        return StepOutcome(0, timed_out=False, outputs={})
    state.step_index += 1
    output = state.runner_temp / f"github-output-{state.step_index}"
    output.write_text("", encoding="utf-8")
    step_env["GITHUB_OUTPUT"] = str(output)
    script = state.runner_temp / f"step-{state.step_index}.py"
    script.write_text(step.run, encoding="utf-8")
    result = state.runner(
        _script_argv(step.shell, script),
        env=step_env,
        cwd=state.root,
        timeout=_timeout(step),
        stdin=None,
    )
    text = output.read_text(encoding="utf-8") if output.is_file() else ""
    return StepOutcome(result.returncode, result.timed_out, parse_github_output(text))


def _script_env(
    state: RunState, step: ActionStep, context: ExprContext
) -> dict[str, str]:
    step_env = dict(state.env)
    step_env.update(github_process_env(step_env))
    step_env["RUNNER_TEMP"] = str(state.runner_temp)
    for key, value in step.env:
        step_env[key] = interpolate(value, context)
    slots = prepare_build_slots(step_env.get("NIXCFG_CI_STAGE", ""), step_env)
    if slots is not None:
        step_env["UPDATE_MAX_NIX_BUILDS"] = slots
    step_env["NIXCFG_SIBLING_JOBS_PATH"] = str(state.runner_temp / "sibling-jobs.json")
    step_env["NIXCFG_SIBLING_REFRESH_ARGV"] = json.dumps([
        sys.executable,
        "-m",
        "lib.buildkite.siblings",
    ])
    return step_env


def _script_argv(shell: str | None, script: Path) -> list[str]:
    if shell is None or shell == "python" or shell.startswith("python"):
        return [sys.executable, str(script)]
    if shell in {"bash", "sh"}:
        return [shell, str(script)]
    message = f"unsupported shell {shell}"
    raise WorkflowParseError(message)


def _run_checkout(
    state: RunState, step: ActionStep, context: ExprContext
) -> StepOutcome:
    args = _with_args(step, context)
    if args.get("fetch-depth") == "0":
        shallow = state.runner(
            ["git", "rev-parse", "--is-shallow-repository"],
            env=state.env,
            cwd=state.root,
            timeout=_timeout(step),
            stdin=None,
        )
        if shallow.stdout.strip() == "true":
            fetched = state.runner(
                ["git", "fetch", "--unshallow", "origin"],
                env=state.env,
                cwd=state.root,
                timeout=_timeout(step),
                stdin=None,
            )
            if fetched.returncode != 0:
                return StepOutcome(fetched.returncode, fetched.timed_out, {})
    ref = args.get("ref") or state.env.get("BUILDKITE_COMMIT") or "HEAD"
    result = state.runner(
        ["git", "checkout", "--detach", ref],
        env=state.env,
        cwd=state.root,
        timeout=_timeout(step),
        stdin=None,
    )
    return StepOutcome(result.returncode, result.timed_out, {})


def _run_setup_python(
    state: RunState, step: ActionStep, context: ExprContext
) -> StepOutcome:
    version = _with_args(step, context).get("python-version", "3.14")
    if version.startswith("3.14") and sys.version_info >= (3, 14):
        return StepOutcome(0, timed_out=False, outputs={})
    result = state.runner(
        ["python3.14", "--version"],
        env=state.env,
        cwd=state.root,
        timeout=_timeout(step),
        stdin=None,
    )
    return StepOutcome(result.returncode, result.timed_out, {})


def _run_download(
    state: RunState, step: ActionStep, context: ExprContext
) -> StepOutcome:
    args = _with_args(step, context)
    name = args.get("name", "").strip() or None
    dest = Path(args.get("path") or state.runner_temp)
    return StepOutcome(state.agent.download(name, dest), timed_out=False, outputs={})


def _run_upload(state: RunState, step: ActionStep, context: ExprContext) -> StepOutcome:
    args = _with_args(step, context)
    name = args.get("name", "").strip()
    raw_path = args.get("path", "")
    if not name or not raw_path:
        return StepOutcome(1, timed_out=False, outputs={})
    path = Path(raw_path)
    if _file_count(path) == 0:
        return StepOutcome(0, timed_out=False, outputs={})
    return StepOutcome(state.agent.upload(name, path), timed_out=False, outputs={})


def _run_cache(state: RunState, step: ActionStep, context: ExprContext) -> StepOutcome:
    del state, step, context
    return StepOutcome(0, timed_out=False, outputs={})


def _run_nix(state: RunState, step: ActionStep, context: ExprContext) -> StepOutcome:
    del context
    result = state.runner(
        ["nix", "--version"],
        env=state.env,
        cwd=state.root,
        timeout=_timeout(step),
        stdin=None,
    )
    return StepOutcome(result.returncode, result.timed_out, {})


def _run_cachix(state: RunState, step: ActionStep, context: ExprContext) -> StepOutcome:
    token = _with_args(step, context).get("authToken", "")
    if not token:
        return StepOutcome(0, timed_out=False, outputs={})
    daemon_dir = state.runner_temp / "cachix-daemon"
    daemon_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    token_file = daemon_dir / "token"
    token_file.write_text(token, encoding="utf-8")
    token_file.chmod(0o600)
    socket = str(daemon_dir / "daemon.sock")
    hook = daemon_dir / "post-build-hook.sh"
    hook.write_text(
        hook_script(socket=socket, token_file=str(token_file)), encoding="utf-8"
    )
    hook.chmod(0o700)
    conf = daemon_dir / "nix.conf"
    conf.write_text(nix_user_conf(str(hook)), encoding="utf-8")
    state.env["CACHIX_AUTH_TOKEN"] = token
    state.env["CACHIX_DAEMON_SOCKET"] = socket
    state.env["CACHIX_DAEMON_DIR"] = str(daemon_dir)
    state.env["NIX_USER_CONF_FILES"] = str(conf)
    daemon = state.spawn(daemon_argv(socket), env=state.env, cwd=state.root)
    state.daemons.append(daemon)
    if daemon.poll() is not None:
        return StepOutcome(1, timed_out=False, outputs={})
    return StepOutcome(0, timed_out=False, outputs={})


def _run_gpg(state: RunState, step: ActionStep, context: ExprContext) -> StepOutcome:
    del context
    if not publish_allowed("publish", state.env.get("NIXCFG_BUILDKITE_PUBLISH")):
        return StepOutcome(0, timed_out=False, outputs={})
    result = state.runner(
        ["gpg", "--batch", "--yes", "--import"],
        env=state.env,
        cwd=state.root,
        timeout=_timeout(step),
        stdin=state.env.get("GPG_PRIVATE_KEY", ""),
    )
    return StepOutcome(result.returncode, result.timed_out, {})


def _run_runtime(
    state: RunState, step: ActionStep, context: ExprContext
) -> StepOutcome:
    defaults, declared = load_action_contract(state.root)
    inputs = {**defaults, **_with_args(step, context)}
    recorded: dict[str, dict[str, object]] = {}
    code = 0
    timed_out = False
    hard = False
    for inner in load_action_steps(state.root):
        inner_context = _context(
            state,
            inputs=inputs,
            matrix=context.matrix,
            steps=recorded,
            success=not hard,
            failure=hard,
        )
        if not enabled(inner.if_expr, inner_context):
            continue
        outcome = run_step(state, inner, inner_context)
        if inner.step_id is not None:
            recorded[inner.step_id] = {"outputs": dict(outcome.outputs)}
        if outcome.timed_out:
            timed_out = True
            hard = True
            code = outcome.returncode or 124
        elif outcome.returncode != 0 and not inner.continue_on_error:
            hard = True
            code = outcome.returncode
    final = _context(
        state,
        inputs=inputs,
        matrix=context.matrix,
        steps=recorded,
        success=not hard,
        failure=hard,
    )
    mapped = {name: eval_text(expression, final) for name, expression in declared}
    return StepOutcome(code, timed_out, mapped)


def _run_native(state: RunState, step: ActionStep, context: ExprContext) -> StepOutcome:
    del state, context
    message = f"reusable workflow {step.uses} is a job, not a step"
    raise WorkflowParseError(message)


def _with_args(step: ActionStep, context: ExprContext) -> dict[str, str]:
    return {key: interpolate(value, context) for key, value in step.with_args}


def _timeout(step: ActionStep) -> float | None:
    if step.timeout_minutes is None:
        return None
    return float(step.timeout_minutes * 60)


def _file_count(path: Path) -> int:
    if path.is_file():
        return 1
    if not path.is_dir():
        return 0
    return sum(1 for item in path.rglob("*") if item.is_file())


def _stop_daemons(daemons: Sequence[Daemon]) -> None:
    for daemon in daemons:
        _stop_one(daemon)


def _stop_one(daemon: Daemon) -> None:
    if daemon.poll() is not None:
        return
    try:
        daemon.terminate()
        daemon.wait(timeout=5)
    except OSError, subprocess.TimeoutExpired:
        try:
            daemon.kill()
        except OSError:
            return


def _require_meta_key(key: str) -> None:
    if _META_KEY.fullmatch(key) is None:
        message = f"invalid meta-data key {key}"
        raise WorkflowParseError(message)


def _api_coordinates(
    env: Mapping[str, str],
) -> tuple[str | None, str | None, str | None, str | None]:
    values = [
        env.get("BUILDKITE_API_TOKEN", "").strip(),
        env.get("BUILDKITE_ORGANIZATION_SLUG", "").strip(),
        env.get("BUILDKITE_PIPELINE_SLUG", "").strip(),
        env.get("BUILDKITE_BUILD_NUMBER", "").strip(),
    ]
    if not all(values):
        return None, None, None, None
    return values[0], values[1], values[2], values[3]


def _write_files(dest: Path, files: Mapping[str, bytes]) -> None:
    for relative, data in files.items():
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


_HANDLERS: dict[str, Callable[[RunState, ActionStep, ExprContext], StepOutcome]] = {
    "run": _run_script,
    "checkout": _run_checkout,
    "setup-python": _run_setup_python,
    "download-artifact": _run_download,
    "upload-artifact": _run_upload,
    "cache": _run_cache,
    "nix": _run_nix,
    "cachix": _run_cachix,
    "gpg": _run_gpg,
    "update-native": _run_native,
}
