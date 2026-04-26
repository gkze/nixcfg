"""Workflow-step helpers for update PR certification rendering."""

from __future__ import annotations

import dataclasses
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from lib import json_utils
from lib.update.ci._workflow_analysis import WorkflowAnalysis, load_workflow_analysis
from lib.update.ci.pr_body import (
    CertificationSection,
    CertificationSharedClosure,
    CertificationTarget,
    PRBodyModel,
    render_certification_section,
)

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclasses.dataclass(frozen=True)
class CertificationPRBodyOptions:
    """Inputs used to render the certification section onto an update PR."""

    workflow_url: str
    started_at: str
    updated_at: str
    cachix_name: str
    workflow_path: Path = Path(".github/workflows/update-certify.yml")


_CERTIFICATION_HELPER_REF = ".#nixcfg"
_CERTIFICATION_SHARED_CLOSURE_MARKER = "nix run .#nixcfg -- ci cache closure"
_CERTIFICATION_EXCLUDE_REF_RE = re.compile(r"--exclude-ref\s+([^\s\\]+)")
_CERTIFICATION_FLAKE_REF_RE = re.compile(r"(\.[#][^\s\\]+)")
_CERTIFICATION_BUILD_REF_RE = re.compile(r"nix build\s+([^\s\\]+)")
_CERTIFICATION_DARWIN_HOST_RE = re.compile(r"build-darwin-config\s+([^\s\\]+)")
_CERTIFICATION_SECTION_START = "<!-- update-certification:start -->"
_CERTIFICATION_SECTION_END = "<!-- update-certification:end -->"
_MISSING_PR_BODY_MODEL_MESSAGE = (
    "Rendered PR body does not contain serialized nixcfg PR body model"
)


def load_json_file(*, input_path: Path, context: str) -> dict[str, object]:
    """Load one JSON file and require an object payload with string keys."""
    return json_utils.as_object_dict(
        json.loads(input_path.read_text(encoding="utf-8")),
        context=context,
    )


def required_string_field(
    payload: dict[str, object], *, field: str, context: str
) -> str:
    """Return one required non-empty string field from a JSON object."""
    value = json_utils.get_required_str(payload, field, context=context)
    if value.strip():
        return value
    msg = f"Expected non-empty string field {field!r} in {context}"
    raise TypeError(msg)


def parse_github_timestamp(value: str) -> datetime:
    """Parse one GitHub API timestamp into UTC."""
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        msg = f"Invalid GitHub timestamp {value!r}"
        raise ValueError(msg) from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _ordered_unique(items: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return tuple(ordered)


def _certification_matrix_targets(
    workflow: WorkflowAnalysis, *, job_id: str
) -> tuple[str, ...]:
    include = workflow.require_job(job_id=job_id).require_matrix_include()
    targets: list[str] = []
    for entry in include:
        target = entry.get("target")
        if not isinstance(target, str) or not target.strip():
            msg = f"{job_id} matrix entry must define a non-empty string target"
            raise TypeError(msg)
        targets.append(target)
    return _ordered_unique(targets)


def _certification_shared_closure_refs(
    workflow: WorkflowAnalysis,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    run_strings = workflow.require_job(job_id="darwin-shared").run_strings
    shared_runs = [
        run for run in run_strings if _CERTIFICATION_SHARED_CLOSURE_MARKER in run
    ]
    if len(shared_runs) != 1:
        msg = "darwin-shared must define exactly one shared-closure step"
        raise RuntimeError(msg)

    shared_run = shared_runs[0]
    excluded = _ordered_unique([
        match.strip("\"'")
        for match in _CERTIFICATION_EXCLUDE_REF_RE.findall(shared_run)
    ])
    if not excluded:
        msg = "darwin-shared shared-closure step must define --exclude-ref values"
        raise RuntimeError(msg)

    included = tuple(
        ref
        for ref in _ordered_unique([
            match.strip("\"'")
            for match in _CERTIFICATION_FLAKE_REF_RE.findall(shared_run)
        ])
        if ref not in excluded and ref != _CERTIFICATION_HELPER_REF
    )
    if not included:
        msg = "darwin-shared shared-closure step must include at least one flake ref"
        raise RuntimeError(msg)

    return included, excluded


def _certification_darwin_host_targets(workflow: WorkflowAnalysis) -> tuple[str, ...]:
    targets: list[str] = []
    for job_id in ("darwin-argus", "darwin-rocinante"):
        hosts = _ordered_unique([
            match.strip("\"'")
            for run in workflow.require_job(job_id=job_id).run_strings
            for match in _CERTIFICATION_DARWIN_HOST_RE.findall(run)
        ])
        if len(hosts) != 1:
            msg = f"{job_id} must build exactly one darwin host"
            raise RuntimeError(msg)
        targets.append(f".#darwinConfigurations.{hosts[0]}.system")
    return tuple(targets)


def _certification_linux_targets(workflow: WorkflowAnalysis) -> tuple[str, ...]:
    targets = _ordered_unique([
        match.strip("\"'")
        for run in workflow.require_job(job_id="linux-x86_64").run_strings
        for match in _CERTIFICATION_BUILD_REF_RE.findall(run)
    ])
    if not targets:
        msg = "linux-x86_64 must define at least one nix build target"
        raise RuntimeError(msg)
    return targets


def _certification_closures(
    workflow: WorkflowAnalysis,
) -> tuple[CertificationTarget | CertificationSharedClosure, ...]:
    darwin_heavy_targets = [
        *_certification_matrix_targets(
            workflow,
            job_id="darwin-priority-heavy",
        ),
        *_certification_matrix_targets(
            workflow,
            job_id="darwin-extra-heavy",
        ),
    ]
    shared_refs, shared_excludes = _certification_shared_closure_refs(workflow)
    darwin_host_targets = _certification_darwin_host_targets(workflow)
    linux_targets = _certification_linux_targets(workflow)

    return (
        *(
            CertificationTarget(ref=target)
            for target in _ordered_unique(darwin_heavy_targets)
        ),
        CertificationSharedClosure(
            refs=shared_refs,
            excluded_heavy_closure_count=len(shared_excludes),
        ),
        *(CertificationTarget(ref=target) for target in darwin_host_targets),
        *(CertificationTarget(ref=target) for target in linux_targets),
    )


def _certification_section(
    options: CertificationPRBodyOptions,
    workflow: WorkflowAnalysis,
) -> CertificationSection:
    """Build certification metadata from a workflow run and workflow file."""
    started_at = parse_github_timestamp(options.started_at)
    updated_at = parse_github_timestamp(options.updated_at)
    return CertificationSection(
        workflow_url=options.workflow_url,
        updated_at=updated_at,
        elapsed_seconds=max(0.0, (updated_at - started_at).total_seconds()),
        cachix_name=options.cachix_name,
        closures=_certification_closures(workflow),
    )


def _replace_legacy_certification_section(*, body: str, section: str) -> str:
    """Insert or replace the visible certification block used by old PR bodies."""
    start_count = body.count(_CERTIFICATION_SECTION_START)
    end_count = body.count(_CERTIFICATION_SECTION_END)
    if start_count != end_count:
        msg = "PR body contains unbalanced certification section markers"
        raise ValueError(msg)
    if start_count > 1:
        msg = "PR body contains multiple certification sections"
        raise ValueError(msg)

    block = (
        f"{_CERTIFICATION_SECTION_START}\n"
        f"{section.rstrip()}\n"
        f"{_CERTIFICATION_SECTION_END}"
    )
    stripped_body = body.strip()
    if not stripped_body:
        return block + "\n"
    if start_count == 0:
        return stripped_body + "\n\n" + block + "\n"

    prefix, _, remainder = body.partition(_CERTIFICATION_SECTION_START)
    _, _, suffix = remainder.partition(_CERTIFICATION_SECTION_END)
    parts = [prefix.rstrip(), block, suffix.lstrip()]
    return "\n\n".join(part for part in parts if part).rstrip() + "\n"


def render_certification_pr_body(
    *,
    existing_body: str | Path,
    output: str | Path,
    options: CertificationPRBodyOptions,
    extract_pr_body_model: Callable[[str], PRBodyModel],
    write_pr_body: Callable[..., int],
) -> int:
    """Update the serialized PR body model with certification metadata."""
    current_body = Path(existing_body).read_text(encoding="utf-8")
    workflow = load_workflow_analysis(options.workflow_path)
    certification = _certification_section(options, workflow)
    try:
        model = extract_pr_body_model(current_body)
    except ValueError as exc:
        if _MISSING_PR_BODY_MODEL_MESSAGE not in str(exc):
            raise
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            _replace_legacy_certification_section(
                body=current_body,
                section=render_certification_section(certification),
            ),
            encoding="utf-8",
        )
        return 0

    updated_model = model.model_copy(update={"certification": certification})
    return write_pr_body(output=output, model=updated_model)
