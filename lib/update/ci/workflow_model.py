"""Typed GitHub Actions workflow model with deterministic YAML rendering.

The repo's hand-written workflow YAML used to be policed by string-marker
validators. Instead, workflows are now described as typed Python models
(:mod:`lib.update.ci.workflow_defs`), validated structurally, and rendered to
committed YAML via :func:`Workflow.render`. Rendering proves its own
correctness: the emitted document is re-parsed with the GitHub Actions YAML
loader and compared against the model's plain data before it is returned.

Only the workflow syntax subset actually used by this repo is modeled.
"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, ConfigDict, Field

from lib.update.ci._workflow_analysis import WorkflowAnalysis
from lib.update.ci._workflow_yaml import GitHubActionsYamlLoader, workflow_job_map

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

type ScalarValue = str | int | bool
type RenderValue = (
    ScalarValue
    | None
    | dict[str, "RenderValue"]
    | list["RenderValue"]
    | tuple["RenderValue", ...]
)

MAX_LINE_WIDTH = 100

_UPLOAD_ACTION_PREFIX = "actions/upload-artifact@"
_DOWNLOAD_ACTION_PREFIX = "actions/download-artifact@"
_MATRIX_EXPR_RE = re.compile(r"\$\{\{\s*matrix\.([A-Za-z0-9_]+)\s*\}\}")
_NUMBER_LIKE_RE = re.compile(r"^[-+]?(?:\d+|\d*\.\d+|\d+\.\d*)(?:[eE][-+]?\d+)?$")
_SPECIAL_WORDS = frozenset({
    "",
    "~",
    "null",
    "none",
    "true",
    "false",
    "yes",
    "no",
    "on",
    "off",
    "nan",
    "inf",
    ".nan",
    ".inf",
})
_PLAIN_UNSAFE_FIRST_CHARS = frozenset("!&*?|>%@`\"'#,[]{}~:")
_FIRST_PRINTABLE_CODEPOINT = 32


class WorkflowRenderError(RuntimeError):
    """Raised when a workflow value cannot be rendered deterministically."""


class WorkflowValidationError(RuntimeError):
    """Raised when a typed workflow violates a structural invariant."""


# ---------------------------------------------------------------------------
# Scalar and mapping emission helpers
# ---------------------------------------------------------------------------


def _quote(value: str) -> str:
    """Return *value* as a YAML double-quoted scalar."""
    return json.dumps(value)


def _needs_quote(value: str) -> bool:
    """Return whether a single-line string requires double quoting."""
    if not value or value != value.strip():
        return True
    if value.lower() in _SPECIAL_WORDS or _NUMBER_LIKE_RE.match(value):
        return True
    first = value[0]
    if first in _PLAIN_UNSAFE_FIRST_CHARS:
        return True
    if first == "-" and (len(value) == 1 or value[1] == " "):
        return True
    if ": " in value or value.endswith(":") or " #" in value:
        return True
    return any(ord(char) < _FIRST_PRINTABLE_CODEPOINT for char in value)


def _literal_block_lines(prefix: str, value: str, *, indent: int) -> list[str]:
    """Emit one multi-line string as a literal block scalar."""
    body = value.split("\n")
    indicator = "|-"
    if body and not body[-1]:
        indicator = "|"
        body = body[:-1]
    content_pad = " " * (indent + 2)
    lines = [f"{prefix} {indicator}"]
    for line in body:
        if line != line.rstrip():
            msg = f"Literal block line has trailing whitespace: {line!r}"
            raise WorkflowRenderError(msg)
        lines.append(f"{content_pad}{line}" if line else "")
    if not body or body[0].startswith(" "):
        msg = f"Literal block content must start with an unindented line: {value!r}"
        raise WorkflowRenderError(msg)
    return lines


def _folded_lines(prefix: str, value: str, *, indent: int) -> list[str]:
    """Emit one long single-line string as a folded block scalar."""
    if "  " in value or _needs_quote(value):
        msg = f"Cannot fold long scalar deterministically: {value!r}"
        raise WorkflowRenderError(msg)
    content_pad = " " * (indent + 2)
    budget = MAX_LINE_WIDTH - len(content_pad)
    lines = [f"{prefix} >-"]
    current: list[str] = []
    current_len = 0
    wrapped: list[str] = []
    for word in value.split(" "):
        candidate_len = current_len + len(word) + (1 if current else 0)
        if current and candidate_len > budget:
            wrapped.append(" ".join(current))
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len = candidate_len
    wrapped.append(" ".join(current))
    if " ".join(wrapped) != value:  # pragma: no cover -- defensive round-trip guard
        msg = f"Folded scalar failed to round-trip: {value!r}"
        raise WorkflowRenderError(msg)
    lines.extend(f"{content_pad}{line}" for line in wrapped)
    return lines


def _scalar_entry_lines(
    key: str, value: ScalarValue | None, *, indent: int
) -> list[str]:
    """Emit one ``key: value`` mapping entry for a scalar value."""
    prefix = f"{' ' * indent}{key}:"
    if value is None:
        return [prefix]
    if isinstance(value, bool):
        return [f"{prefix} {'true' if value else 'false'}"]
    if isinstance(value, int):
        return [f"{prefix} {value}"]
    if "\n" in value:
        return _literal_block_lines(prefix, value, indent=indent)
    if _needs_quote(value):
        line = f"{prefix} {_quote(value)}"
        if len(line) > MAX_LINE_WIDTH:
            msg = f"Quoted scalar does not fit on one line: {value!r}"
            raise WorkflowRenderError(msg)
        return [line]
    if len(f"{prefix} {value}") <= MAX_LINE_WIDTH:
        return [f"{prefix} {value}"]
    return _folded_lines(prefix, value, indent=indent)


def _sequence_scalar_line(value: ScalarValue, *, indent: int) -> str:
    """Emit one scalar sequence item."""
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, int):
        text = str(value)
    elif "\n" in value or len(value) + indent + 2 > MAX_LINE_WIDTH:
        msg = f"Unsupported scalar sequence item: {value!r}"
        raise WorkflowRenderError(msg)
    else:
        text = _quote(value) if _needs_quote(value) else value
    return f"{' ' * indent}- {text}"


def _splice_item_marker(lines: list[str], *, indent: int) -> list[str]:
    """Rewrite the first mapping line into a ``- `` sequence item."""
    head = lines[0]
    lines[0] = f"{head[: indent - 2]}- {head[indent:]}"
    return lines


def _entry_lines(key: str, value: RenderValue, *, indent: int) -> list[str]:
    """Emit one mapping entry for scalar, mapping, or sequence values."""
    pad = " " * indent
    if isinstance(value, dict):
        if not value:
            msg = f"Refusing to render empty mapping for key {key!r}"
            raise WorkflowRenderError(msg)
        return [f"{pad}{key}:", *_mapping_lines(value, indent=indent + 2)]
    if isinstance(value, (list, tuple)):
        if not value:
            msg = f"Refusing to render empty sequence for key {key!r}"
            raise WorkflowRenderError(msg)
        lines = [f"{pad}{key}:"]
        for item in value:
            if isinstance(item, dict):
                lines.extend(
                    _splice_item_marker(
                        _mapping_lines(item, indent=indent + 4),
                        indent=indent + 4,
                    )
                )
            else:
                lines.append(
                    _sequence_scalar_line(_scalar_item(item), indent=indent + 2)
                )
        return lines
    return _scalar_entry_lines(key, value, indent=indent)


def _scalar_item(value: RenderValue) -> ScalarValue:
    """Require one sequence item to be a scalar."""
    if value is None or isinstance(value, (dict, list, tuple)):
        msg = f"Unsupported nested sequence item: {value!r}"
        raise WorkflowRenderError(msg)
    return value


def _mapping_lines(mapping: Mapping[str, RenderValue], *, indent: int) -> list[str]:
    """Emit one mapping in insertion order."""
    lines: list[str] = []
    for key, value in mapping.items():
        lines.extend(_entry_lines(key, value, indent=indent))
    return lines


def _comment_lines(comments: Sequence[str], *, indent: int) -> list[str]:
    """Emit comment lines at one indentation level."""
    pad = " " * indent
    return [f"{pad}# {comment}" if comment else f"{pad}#" for comment in comments]


# ---------------------------------------------------------------------------
# Typed workflow models
# ---------------------------------------------------------------------------


class WorkflowModelBase(BaseModel):
    """Shared configuration for all workflow model nodes."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Step(WorkflowModelBase):
    """One workflow job step."""

    comment_lines: tuple[str, ...] = ()
    trailing_comment_lines: tuple[str, ...] = ()
    uses_comment: str | None = None
    name: str | None = None
    id: str | None = None
    if_: str | None = None
    continue_on_error: bool | None = None
    uses: str | None = None
    with_: dict[str, ScalarValue] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    run: str | None = None
    shell: str | None = None
    working_directory: str | None = None

    def iter_items(self) -> Iterator[tuple[str, RenderValue]]:
        """Yield rendered step keys in canonical order."""
        entries: tuple[tuple[str, RenderValue], ...] = (
            ("name", self.name),
            ("id", self.id),
            ("if", self.if_),
            ("continue-on-error", self.continue_on_error),
            ("uses", self.uses),
            ("with", dict(self.with_)),
            ("env", dict(self.env)),
            ("run", self.run),
            ("shell", self.shell),
            ("working-directory", self.working_directory),
        )
        for key, value in entries:
            if value is None or (isinstance(value, dict) and not value):
                continue
            yield key, value

    def to_data(self) -> dict[str, RenderValue]:
        """Return this step as plain workflow data."""
        return dict(self.iter_items())


class Matrix(WorkflowModelBase):
    """One static ``strategy.matrix`` definition."""

    values: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    include: tuple[dict[str, ScalarValue], ...] = ()

    def to_data(self) -> dict[str, RenderValue]:
        """Return this matrix as plain workflow data."""
        data: dict[str, RenderValue] = {
            key: list(items) for key, items in self.values.items()
        }
        if self.include:
            data["include"] = [dict(entry) for entry in self.include]
        return data


class Strategy(WorkflowModelBase):
    """One job ``strategy`` block."""

    fail_fast: bool | None = None
    max_parallel: int | None = None
    matrix: str | Matrix

    def to_data(self) -> dict[str, RenderValue]:
        """Return this strategy as plain workflow data."""
        data: dict[str, RenderValue] = {}
        if self.fail_fast is not None:
            data["fail-fast"] = self.fail_fast
        if self.max_parallel is not None:
            data["max-parallel"] = self.max_parallel
        data["matrix"] = (
            self.matrix if isinstance(self.matrix, str) else self.matrix.to_data()
        )
        return data


class Job(WorkflowModelBase):
    """One workflow job."""

    comment_lines: tuple[str, ...] = ()
    name: str | None = None
    needs: tuple[str, ...] = ()
    if_: str | None = None
    runs_on: str
    timeout_minutes: int | None = None
    permissions: dict[str, str] = Field(default_factory=dict)
    outputs: dict[str, str] = Field(default_factory=dict)
    strategy: Strategy | None = None
    steps: tuple[Step, ...]

    def to_data(self) -> dict[str, RenderValue]:
        """Return this job as plain workflow data."""
        data: dict[str, RenderValue] = {}
        if self.name is not None:
            data["name"] = self.name
        if self.needs:
            data["needs"] = self.needs[0] if len(self.needs) == 1 else list(self.needs)
        if self.if_ is not None:
            data["if"] = self.if_
        data["runs-on"] = self.runs_on
        if self.timeout_minutes is not None:
            data["timeout-minutes"] = self.timeout_minutes
        if self.permissions:
            data["permissions"] = dict(self.permissions)
        if self.outputs:
            data["outputs"] = dict(self.outputs)
        if self.strategy is not None:
            data["strategy"] = self.strategy.to_data()
        data["steps"] = [step.to_data() for step in self.steps]
        return data


class WorkflowCallInput(WorkflowModelBase):
    """One ``workflow_call`` input declaration."""

    description: str
    required: bool
    default: str
    type: str

    def to_data(self) -> dict[str, RenderValue]:
        """Return this input as plain workflow data."""
        return {
            "description": self.description,
            "required": self.required,
            "default": self.default,
            "type": self.type,
        }


class WorkflowCallTrigger(WorkflowModelBase):
    """One ``workflow_call`` trigger definition."""

    inputs: dict[str, WorkflowCallInput] = Field(default_factory=dict)

    def to_data(self) -> dict[str, RenderValue] | None:
        """Return this trigger as plain workflow data."""
        if not self.inputs:
            return None
        return {
            "inputs": {name: spec.to_data() for name, spec in self.inputs.items()},
        }


class Triggers(WorkflowModelBase):
    """The workflow ``on`` block."""

    comment_lines: tuple[str, ...] = ()
    workflow_call: WorkflowCallTrigger = Field(default_factory=WorkflowCallTrigger)

    def to_data(self) -> dict[str, RenderValue]:
        """Return the trigger block as plain workflow data."""
        return {"workflow_call": self.workflow_call.to_data()}


class Concurrency(WorkflowModelBase):
    """The workflow ``concurrency`` block."""

    group: str
    cancel_in_progress: bool

    def to_data(self) -> dict[str, RenderValue]:
        """Return the concurrency block as plain workflow data."""
        return {"group": self.group, "cancel-in-progress": self.cancel_in_progress}


class Workflow(WorkflowModelBase):
    """One GitHub Actions workflow document."""

    name: str
    triggers: Triggers
    permissions: dict[str, str]
    concurrency: Concurrency
    env: dict[str, str] = Field(default_factory=dict)
    jobs: dict[str, Job]

    def to_data(self) -> dict[str, RenderValue]:
        """Return the workflow as plain data matching the parsed YAML."""
        data: dict[str, RenderValue] = {
            "name": self.name,
            "on": self.triggers.to_data(),
            "permissions": dict(self.permissions),
            "concurrency": self.concurrency.to_data(),
        }
        if self.env:
            data["env"] = dict(self.env)
        data["jobs"] = {job_id: job.to_data() for job_id, job in self.jobs.items()}
        return data

    # -- validation ---------------------------------------------------------

    def validate_structure(self) -> None:
        """Check needs-graph and artifact contracts, raising on violations."""
        analysis = WorkflowAnalysis.from_jobs(
            workflow_job_map(
                {job_id: job.to_data() for job_id, job in self.jobs.items()},
                context=f"workflow {self.name} jobs",
            )
        )
        _, transitive_needs = analysis.needs_graph()
        errors = _validate_artifact_contracts(self.jobs, transitive_needs)
        if errors:
            raise WorkflowValidationError("\n".join(errors))

    # -- rendering ----------------------------------------------------------

    def render(self, *, header_comments: Sequence[str] = ()) -> str:
        """Render the workflow deterministically and prove the result."""
        self.validate_structure()
        lines = ["--- # !yamlfmt!:ignore"]
        lines.extend(_comment_lines(header_comments, indent=0))
        lines.extend(_scalar_entry_lines("name", self.name, indent=0))
        lines.append("")
        lines.append("on:")
        lines.extend(_comment_lines(self.triggers.comment_lines, indent=2))
        trigger_data = self.triggers.to_data()
        lines.extend(_mapping_lines(trigger_data, indent=2))
        lines.append("")
        lines.extend(_entry_lines("permissions", dict(self.permissions), indent=0))
        lines.append("")
        lines.extend(_entry_lines("concurrency", self.concurrency.to_data(), indent=0))
        if self.env:
            lines.append("")
            lines.extend(_entry_lines("env", dict(self.env), indent=0))
        lines.append("")
        lines.append("jobs:")
        for index, (job_id, job) in enumerate(self.jobs.items()):
            if index:
                lines.append("")
            lines.extend(_comment_lines(job.comment_lines, indent=2))
            lines.append(f"  {job_id}:")
            lines.extend(_job_body_lines(job, indent=4))
        rendered = "\n".join(lines) + "\n"
        _prove_render(rendered, self.to_data())
        return rendered


def _job_body_lines(job: Job, *, indent: int) -> list[str]:
    """Emit one job body, handling step comments explicitly."""
    lines: list[str] = []
    for key, value in job.to_data().items():
        if key == "steps":
            lines.append(f"{' ' * indent}steps:")
            lines.extend(_steps_lines(job.steps, indent=indent + 2))
            continue
        lines.extend(_entry_lines(key, value, indent=indent))
    return lines


def _steps_lines(steps: Sequence[Step], *, indent: int) -> list[str]:
    """Emit one job's steps with attached comments."""
    lines: list[str] = []
    for step in steps:
        lines.extend(_comment_lines(step.comment_lines, indent=indent))
        body: list[str] = []
        for key, value in step.iter_items():
            entry = _entry_lines(key, value, indent=indent + 2)
            if key == "uses" and step.uses_comment is not None:
                entry[0] = f"{entry[0]} # {step.uses_comment}"
                if len(entry[0]) > MAX_LINE_WIDTH:
                    msg = f"uses line with comment is too long: {entry[0]!r}"
                    raise WorkflowRenderError(msg)
            body.extend(entry)
        lines.extend(_splice_item_marker(body, indent=indent + 2))
        lines.extend(_comment_lines(step.trailing_comment_lines, indent=indent))
    return lines


def _prove_render(rendered: str, expected: dict[str, RenderValue]) -> None:
    """Re-parse rendered YAML and require semantic equality with the model."""
    parsed = yaml.load(rendered, Loader=GitHubActionsYamlLoader)  # noqa: S506
    normalized = json.loads(json.dumps(expected))
    if parsed != normalized:  # pragma: no cover -- defensive round-trip guard
        msg = "Rendered workflow YAML does not round-trip to the typed model"
        raise WorkflowRenderError(msg)


# ---------------------------------------------------------------------------
# Ported artifact-contract invariants
# ---------------------------------------------------------------------------


def _substitute_matrix(value: str, matrix_values: Mapping[str, ScalarValue]) -> str:
    """Render simple ``${{ matrix.foo }}`` expressions in one string."""
    return _MATRIX_EXPR_RE.sub(
        lambda match: str(matrix_values.get(match.group(1), match.group(0))),
        value,
    )


def _substituted_step(step: Step, entry: Mapping[str, ScalarValue]) -> Step:
    """Copy one step with matrix expressions rendered from one include entry."""
    return step.model_copy(
        update={
            "if_": (None if step.if_ is None else _substitute_matrix(step.if_, entry)),
            "with_": {
                key: (
                    _substitute_matrix(value, entry)
                    if isinstance(value, str)
                    else value
                )
                for key, value in step.with_.items()
            },
        }
    )


def _job_step_instances(job: Job) -> list[tuple[Step, ...]]:
    """Expand static matrix-include jobs into concrete step tuples."""
    matrix = job.strategy.matrix if job.strategy is not None else None
    if not isinstance(matrix, Matrix) or not matrix.include:
        return [job.steps]
    return [
        tuple(_substituted_step(step, entry) for step in job.steps)
        for entry in matrix.include
    ]


def _string_with_value(step: Step, key: str) -> str:
    """Return one required string input from a step ``with`` mapping."""
    value = step.with_.get(key, "")
    return value.strip() if isinstance(value, str) else ""


def _validate_artifact_contracts(
    jobs: Mapping[str, Job],
    transitive_needs: Mapping[str, frozenset[str]],
) -> list[str]:
    """Check upload/download artifact-name and needs consistency."""
    errors: list[str] = []
    uploads: dict[str, str] = {}
    downloads: list[tuple[str, str, bool]] = []

    for job_id, job in jobs.items():
        for steps in _job_step_instances(job):
            for step in steps:
                uses = step.uses or ""
                if uses.startswith(_UPLOAD_ACTION_PREFIX):
                    artifact_name = _string_with_value(step, "name")
                    if not artifact_name:
                        errors.append(
                            f"Job `{job_id}` uploads an artifact without a name"
                        )
                        continue
                    producer = uploads.get(artifact_name)
                    if producer is not None and producer != job_id:
                        errors.append(
                            f"Artifact `{artifact_name}` is uploaded by both "
                            f"`{producer}` and `{job_id}`"
                        )
                        continue
                    uploads[artifact_name] = job_id
                elif uses.startswith(_DOWNLOAD_ACTION_PREFIX):
                    artifact_name = _string_with_value(step, "name")
                    pattern = _string_with_value(step, "pattern")
                    if artifact_name:
                        downloads.append((job_id, artifact_name, False))
                    elif pattern:
                        downloads.append((job_id, pattern, True))
                    else:
                        errors.append(
                            f"Job `{job_id}` downloads an artifact without a "
                            "name or pattern"
                        )

    for job_id, artifact_ref, is_pattern in downloads:
        producers = sorted({
            producer
            for name, producer in uploads.items()
            if producer != job_id
            and (
                PurePosixPath(name).match(artifact_ref)
                if is_pattern
                else name == artifact_ref
            )
        })
        if not producers:
            descriptor = "pattern" if is_pattern else "artifact"
            errors.append(
                f"Job `{job_id}` downloads unknown {descriptor} `{artifact_ref}`"
            )
            continue
        job_needs = transitive_needs[job_id]
        errors.extend(
            f"Job `{job_id}` downloads `{artifact_ref}` from `{producer}` "
            f"without depending on it"
            for producer in producers
            if producer not in job_needs
        )

    return errors


__all__ = [
    "MAX_LINE_WIDTH",
    "Concurrency",
    "Job",
    "Matrix",
    "Step",
    "Strategy",
    "Triggers",
    "Workflow",
    "WorkflowCallInput",
    "WorkflowCallTrigger",
    "WorkflowRenderError",
    "WorkflowValidationError",
]
