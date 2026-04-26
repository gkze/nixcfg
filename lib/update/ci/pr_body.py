"""Structured pull-request body models and Markdown rendering."""

from __future__ import annotations

import base64
import html
import re
import textwrap
import zlib
from datetime import datetime  # noqa: TC003
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from lib.update.ci._time import format_duration

if TYPE_CHECKING:
    from collections.abc import Callable

_COMMENT_MARKER = "nixcfg-pr-body-model"
_COMMENT_START_MARKER = f"{_COMMENT_MARKER}:start"
_COMMENT_END_MARKER = f"{_COMMENT_MARKER}:end"
_COMMENT_PAYLOAD_RE = re.compile(
    rf"<!--\s*{re.escape(_COMMENT_START_MARKER)}\r?\n"
    rf"(?P<payload>[A-Za-z0-9+/=\r\n]+)\r?\n"
    rf"{re.escape(_COMMENT_END_MARKER)}\s*-->",
    re.MULTILINE,
)
_COMMENT_WRAP_WIDTH = 76
_PAIR_ITEM_COUNT = 2


class PRBodyBaseModel(BaseModel):
    """Base model settings shared by all structured PR body models."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class LinkValue(PRBodyBaseModel):
    """One markdown link or plain text cell value."""

    label: str
    url: str | None = None


class FlakeInputUpdate(PRBodyBaseModel):
    """One updated flake input row in the PR body."""

    input_name: str
    source: LinkValue
    previous: LinkValue
    current: LinkValue
    diff: LinkValue


class FlakeInputSnapshot(PRBodyBaseModel):
    """One added or removed flake input row in the PR body."""

    input_name: str
    source: LinkValue
    revision: LinkValue


class SourceChange(PRBodyBaseModel):
    """One per-package sources.json diff section."""

    path: str
    url: str
    diff: str


class CertificationTarget(PRBodyBaseModel):
    """One specific installable cached during certification."""

    kind: Literal["target"] = "target"
    ref: str


class CertificationSharedClosure(PRBodyBaseModel):
    """One shared-closure summary derived from certification workflow targets."""

    kind: Literal["shared_closure"] = "shared_closure"
    refs: tuple[str, ...]
    excluded_heavy_closure_count: int


type CertificationClosure = Annotated[
    CertificationSharedClosure | CertificationTarget,
    Field(discriminator="kind"),
]


class CertificationSection(PRBodyBaseModel):
    """Certification workflow metadata rendered onto the PR body."""

    workflow_url: str
    updated_at: datetime
    elapsed_seconds: float
    cachix_name: str
    closures: tuple[CertificationClosure, ...]


class PRBodyModel(PRBodyBaseModel):
    """Declarative model for the entire generated update PR body."""

    schema_version: Literal[1] = 1
    workflow_run_url: str
    compare_url: str
    updated_flake_inputs: tuple[FlakeInputUpdate, ...] = ()
    added_flake_inputs: tuple[FlakeInputSnapshot, ...] = ()
    removed_flake_inputs: tuple[FlakeInputSnapshot, ...] = ()
    source_changes: tuple[SourceChange, ...] = ()
    certification: CertificationSection | None = None


def _markdown_link(label: str, url: str) -> str:
    return f"[{label}]({url})"


def _render_link_value(value: LinkValue) -> str:
    if value.url is None:
        return value.label
    return _markdown_link(value.label, value.url)


def _escape_table_cell(value: str) -> str:
    return value.replace("|", r"\|").replace("\n", "<br>")


def _render_table(rows: list[list[str]]) -> str:
    if not rows:
        msg = "Expected at least one table row"
        raise ValueError(msg)

    header, *body = rows
    rendered_rows = [header, *body]
    lines = [
        "| " + " | ".join(_escape_table_cell(cell) for cell in header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines.extend(
        "| " + " | ".join(_escape_table_cell(cell) for cell in row) + " |"
        for row in rendered_rows[1:]
    )
    return "\n".join(lines)


def _code_list_text(values: tuple[str, ...]) -> str:
    if not values:
        msg = "Expected at least one code-formatted value"
        raise ValueError(msg)

    parts: list[str] = []
    for index, value in enumerate(values):
        if index > 0:
            if index == len(values) - 1:
                separator = " and " if len(values) == _PAIR_ITEM_COUNT else ", and "
            else:
                separator = ", "
            parts.append(separator)
        parts.append(f"`{value}`")
    return "".join(parts)


def _format_timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M UTC")


def serialize_pr_body_model(model: PRBodyModel) -> str:
    """Return a compressed/base64 payload suitable for hidden comment storage."""
    payload = model.model_dump_json(exclude_none=True).encode("utf-8")
    compressed = zlib.compress(payload, level=9)
    return base64.b64encode(compressed).decode("ascii")


def deserialize_pr_body_model(payload: str) -> PRBodyModel:
    """Decode one hidden-comment payload back into a structured PR body model."""
    compressed = base64.b64decode(payload.encode("ascii"), validate=True)
    return PRBodyModel.model_validate_json(zlib.decompress(compressed))


def extract_pr_body_model(markdown: str) -> PRBodyModel:
    """Extract the hidden serialized PR body model from rendered markdown."""
    matches = list(_COMMENT_PAYLOAD_RE.finditer(markdown))
    if not matches:
        msg = "Rendered PR body does not contain serialized nixcfg PR body model"
        raise ValueError(msg)
    if len(matches) > 1:
        msg = "Rendered PR body contains multiple serialized nixcfg PR body models"
        raise ValueError(msg)
    payload = "".join(matches[0].group("payload").splitlines())
    return deserialize_pr_body_model(payload)


def _serialized_model_comment(model: PRBodyModel) -> str:
    payload = serialize_pr_body_model(model)
    wrapped = textwrap.fill(payload, width=_COMMENT_WRAP_WIDTH)
    return f"<!-- {_COMMENT_START_MARKER}\n{wrapped}\n{_COMMENT_END_MARKER} -->"


def _render_flake_input_section[T](
    title: str,
    header: list[str],
    changes: tuple[T, ...],
    row_builder: Callable[[T], list[str]],
) -> str | None:
    if not changes:
        return None

    rows = [header, *[row_builder(change) for change in changes]]
    return f"### {title}\n\n{_render_table(rows)}"


def _flake_input_sections(model: PRBodyModel) -> list[str]:
    sections = [
        section
        for section in (
            _render_flake_input_section(
                "Updated flake inputs",
                ["Input", "Source", "From", "To", "Diff"],
                model.updated_flake_inputs,
                lambda change: [
                    change.input_name,
                    _render_link_value(change.source),
                    _render_link_value(change.previous),
                    _render_link_value(change.current),
                    _render_link_value(change.diff),
                ],
            ),
            _render_flake_input_section(
                "Added flake inputs",
                ["Input", "Source", "Revision"],
                model.added_flake_inputs,
                lambda change: [
                    change.input_name,
                    _render_link_value(change.source),
                    _render_link_value(change.revision),
                ],
            ),
            _render_flake_input_section(
                "Removed flake inputs",
                ["Input", "Source", "Revision"],
                model.removed_flake_inputs,
                lambda change: [
                    change.input_name,
                    _render_link_value(change.source),
                    _render_link_value(change.revision),
                ],
            ),
        )
        if section is not None
    ]

    if not sections:
        sections.append("No flake.lock input changes detected.")
    return sections


def _render_source_changes(model: PRBodyModel) -> str | None:
    if not model.source_changes:
        return None

    blocks = ["### Per-package sources.json changes"]
    for change in model.source_changes:
        blocks.extend([
            "<details>",
            (
                "<summary>"
                f'<a href="{html.escape(change.url, quote=True)}">'
                f"<code>{html.escape(change.path)}</code>"
                "</a>"
                "</summary>"
            ),
            "",
            "```diff",
            change.diff.rstrip("\n"),
            "```",
            "</details>",
        ])
    return "\n".join(blocks)


def render_certification_section(certification: CertificationSection) -> str:
    """Render one certification section as Markdown."""
    lines = [
        "## Certification",
        f"Latest certification: {_markdown_link('workflow run', certification.workflow_url)}  ",
        f"Updated: `{_format_timestamp(certification.updated_at)}`  ",
        f"Elapsed: `{format_duration(certification.elapsed_seconds)}`",
        "",
        f"Closures pushed to Cachix (`{certification.cachix_name}`):",
    ]
    for closure in certification.closures:
        if isinstance(closure, CertificationTarget):
            lines.append(f"- `{closure.ref}`")
            continue
        suffix = "s" if closure.excluded_heavy_closure_count != 1 else ""
        lines.append(
            "- Shared Darwin closure for "
            f"{_code_list_text(closure.refs)} excluding "
            f"{closure.excluded_heavy_closure_count} heavy package closure{suffix}"
        )
    return "\n".join(lines)


def _render_certification(model: PRBodyModel) -> str | None:
    certification = model.certification
    if certification is None:
        return None
    return render_certification_section(certification)


def render_pr_body(model: PRBodyModel) -> str:
    """Render one PR body model to markdown without mutating Marko internals."""
    sections = [
        f"**{_markdown_link('Workflow run', model.workflow_run_url)}**",
        f"**{_markdown_link('Compare', model.compare_url)}**",
        *_flake_input_sections(model),
    ]
    if source_changes := _render_source_changes(model):
        sections.append(source_changes)
    if certification := _render_certification(model):
        sections.append(certification)
    sections.append(_serialized_model_comment(model))
    return "\n\n".join(sections).rstrip() + "\n"


def write_pr_body(*, output: str | Path, model: PRBodyModel) -> int:
    """Render and write one PR body markdown file from a structured model."""
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_pr_body(model), encoding="utf-8")
    return 0


__all__ = [
    "CertificationSection",
    "CertificationSharedClosure",
    "CertificationTarget",
    "FlakeInputSnapshot",
    "FlakeInputUpdate",
    "LinkValue",
    "PRBodyModel",
    "SourceChange",
    "deserialize_pr_body_model",
    "extract_pr_body_model",
    "render_certification_section",
    "render_pr_body",
    "serialize_pr_body_model",
    "write_pr_body",
]
