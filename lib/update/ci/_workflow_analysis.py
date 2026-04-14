"""Shared GitHub Actions workflow analysis helpers.

Built on top of the raw normalized YAML helpers in ``_workflow_yaml`` so
validators can share higher-level workflow semantics without giving up the raw
fallback loading path.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from graphlib import CycleError, TopologicalSorter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

from lib.update.ci._workflow_yaml import (
    WorkflowObject,
    load_workflow_jobs,
    workflow_job_map,
    workflow_job_needs,
    workflow_job_run_strings,
    workflow_job_steps,
    workflow_object,
)


@dataclass(frozen=True)
class WorkflowJobAnalysis:
    """Normalized view of one workflow job with cached semantic helpers."""

    job_id: str
    data: WorkflowObject
    invalid_needs_value_message: str = (
        "Job {job_id} defines unsupported needs value: {raw_needs!r}"
    )
    invalid_needs_item_message: str = "Job {job_id} contains non-string need: {need!r}"
    invalid_steps_message: str = "Job {job_id} does not define steps as a list"

    @cached_property
    def needs(self) -> tuple[str, ...]:
        """Return normalized ``needs`` values for this job."""
        return workflow_job_needs(
            self.data.get("needs"),
            job_id=self.job_id,
            invalid_value_message=self.invalid_needs_value_message,
            invalid_item_message=self.invalid_needs_item_message,
        )

    @cached_property
    def steps(self) -> tuple[WorkflowObject, ...]:
        """Return normalized dict-shaped workflow steps for this job."""
        return workflow_job_steps(
            self.data,
            job_id=self.job_id,
            invalid_steps_message=self.invalid_steps_message,
        )

    @cached_property
    def run_strings(self) -> tuple[str, ...]:
        """Return string-valued ``run`` steps for this job."""
        return workflow_job_run_strings(
            self.data,
            job_id=self.job_id,
            invalid_steps_message=self.invalid_steps_message,
        )

    def has_need(self, need: str) -> bool:
        """Return whether this job declares *need* directly."""
        return need in self.needs

    def require_need(
        self,
        need: str,
        *,
        missing_need_message: str = "{job_id} must depend on {need}",
    ) -> None:
        """Require one direct dependency and raise a stable error otherwise."""
        if not self.has_need(need):
            msg = missing_need_message.format(job_id=self.job_id, need=need)
            raise RuntimeError(msg)

    def forbid_need(
        self,
        need: str,
        *,
        forbidden_need_message: str = "{job_id} must not depend on {need}",
    ) -> None:
        """Reject one direct dependency with a stable validator-facing error."""
        if self.has_need(need):
            msg = forbidden_need_message.format(job_id=self.job_id, need=need)
            raise RuntimeError(msg)

    def has_run_marker(self, marker: str) -> bool:
        """Return whether any ``run`` step contains *marker*."""
        return any(marker in run for run in self.run_strings)

    def require_run_marker(
        self,
        marker: str,
        *,
        missing_run_message: str = (
            "Job {job_id} is missing required run step containing {marker!r}"
        ),
    ) -> None:
        """Require at least one ``run`` step containing *marker*."""
        if not self.has_run_marker(marker):
            msg = missing_run_message.format(job_id=self.job_id, marker=marker)
            raise RuntimeError(msg)

    def forbid_run_marker(
        self,
        marker: str,
        *,
        forbidden_run_message: str = (
            "Job {job_id} must not run step containing {marker!r}"
        ),
    ) -> None:
        """Reject any ``run`` step containing *marker*."""
        if self.has_run_marker(marker):
            msg = forbidden_run_message.format(job_id=self.job_id, marker=marker)
            raise RuntimeError(msg)

    def optional_matrix_include(
        self,
        *,
        invalid_entry_message: str = "Unsupported matrix include entry in job {job_id}: {entry!r}",
    ) -> tuple[WorkflowObject, ...]:
        """Return normalized ``strategy.matrix.include`` entries when present.

        Missing or non-list matrix definitions are treated as "not a matrix" to
        match GitHub Actions helpers that only expand concrete matrix jobs when a
        usable ``include`` list exists.
        """
        strategy = self.data.get("strategy")
        matrix = strategy.get("matrix") if isinstance(strategy, dict) else None
        include = matrix.get("include") if isinstance(matrix, dict) else None
        if not isinstance(include, list) or not include:
            return ()
        return self._normalize_matrix_include(
            include,
            invalid_entry_message=invalid_entry_message,
        )

    def require_matrix_include(
        self,
        *,
        missing_strategy_message: str = "{job_id} does not define a strategy mapping",
        missing_matrix_message: str = "{job_id} does not define a matrix mapping",
        invalid_include_message: str = "{job_id} matrix.include must be a non-empty list",
        invalid_entry_message: str = "Unsupported {job_id} matrix entry: {entry!r}",
    ) -> tuple[WorkflowObject, ...]:
        """Return normalized ``strategy.matrix.include`` entries or raise."""
        strategy = self.data.get("strategy")
        if not isinstance(strategy, dict):
            msg = missing_strategy_message.format(job_id=self.job_id)
            raise TypeError(msg)

        matrix = strategy.get("matrix")
        if not isinstance(matrix, dict):
            msg = missing_matrix_message.format(job_id=self.job_id)
            raise TypeError(msg)

        include = matrix.get("include")
        if not isinstance(include, list) or not include:
            msg = invalid_include_message.format(job_id=self.job_id)
            raise TypeError(msg)

        return self._normalize_matrix_include(
            include,
            invalid_entry_message=invalid_entry_message,
        )

    def _normalize_matrix_include(
        self,
        include: list[object],
        *,
        invalid_entry_message: str,
    ) -> tuple[WorkflowObject, ...]:
        entries: list[WorkflowObject] = []
        for index, entry in enumerate(include, start=1):
            if not isinstance(entry, dict):
                msg = invalid_entry_message.format(job_id=self.job_id, entry=entry)
                raise TypeError(msg)
            entries.append(
                workflow_object(
                    entry,
                    context=f"workflow job {self.job_id} matrix.include[{index}]",
                )
            )
        return tuple(entries)


@dataclass(frozen=True)
class WorkflowAnalysis:
    """Normalized workflow view shared by CI workflow validators."""

    workflow_path: Path | None
    jobs: dict[str, WorkflowJobAnalysis]

    @classmethod
    def from_jobs(
        cls,
        workflow_jobs: dict[str, WorkflowObject] | dict[str, WorkflowJobAnalysis],
        *,
        workflow_path: Path | None = None,
        invalid_needs_value_message: str = (
            "Job {job_id} defines unsupported needs value: {raw_needs!r}"
        ),
        invalid_needs_item_message: str = (
            "Job {job_id} contains non-string need: {need!r}"
        ),
        invalid_steps_message: str = "Job {job_id} does not define steps as a list",
    ) -> WorkflowAnalysis:
        """Coerce raw or analyzed workflow jobs into one shared analysis object."""
        return cls(
            workflow_path=workflow_path,
            jobs={
                job_id: (
                    job
                    if isinstance(job, WorkflowJobAnalysis)
                    else analyze_workflow_job(
                        job_id,
                        job,
                        invalid_needs_value_message=invalid_needs_value_message,
                        invalid_needs_item_message=invalid_needs_item_message,
                        invalid_steps_message=invalid_steps_message,
                    )
                )
                for job_id, job in workflow_jobs.items()
            },
        )

    def require_job(
        self,
        *,
        job_id: str,
        missing_job_message: str = "Workflow is missing required job {job_id!r}",
    ) -> WorkflowJobAnalysis:
        """Return one required job or raise a stable workflow-structure error."""
        try:
            return self.jobs[job_id]
        except KeyError as exc:
            msg = missing_job_message.format(job_id=job_id)
            raise RuntimeError(msg) from exc

    def require_jobs(
        self,
        *job_ids: str,
        missing_job_message: str = "Workflow is missing required job {job_id!r}",
    ) -> tuple[WorkflowJobAnalysis, ...]:
        """Return multiple required jobs in declaration order."""
        return tuple(
            self.require_job(
                job_id=job_id,
                missing_job_message=missing_job_message,
            )
            for job_id in job_ids
        )

    def has_any_job(self, job_ids: Iterable[str]) -> bool:
        """Return whether any listed workflow job is present."""
        return any(job_id in self.jobs for job_id in job_ids)

    def needs_graph(
        self,
        *,
        unknown_need_message: str = "Job `{job_id}` references unknown needs: {rendered}",
        cycle_message: str = "Workflow job needs contain a cycle: {rendered_cycle}",
    ) -> tuple[dict[str, frozenset[str]], dict[str, frozenset[str]]]:
        """Validate and return direct/transitive job dependency graphs."""
        direct_needs: dict[str, frozenset[str]] = {}
        errors: list[str] = []

        for job_id, job in self.jobs.items():
            needs = frozenset(job.needs)
            missing = sorted(need for need in needs if need not in self.jobs)
            if missing:
                rendered = ", ".join(f"`{need}`" for need in missing)
                errors.append(
                    unknown_need_message.format(
                        job_id=job_id,
                        missing=missing,
                        rendered=rendered,
                    )
                )
            direct_needs[job_id] = needs

        if errors:
            raise RuntimeError("\n".join(errors))

        dependency_graph = {
            job_id: set(needs) for job_id, needs in direct_needs.items()
        }
        try:
            topo_order = tuple(TopologicalSorter(dependency_graph).static_order())
        except CycleError as exc:
            cycle_nodes = exc.args[1] if len(exc.args) > 1 else ()
            rendered_cycle = " -> ".join(str(node) for node in cycle_nodes)
            msg = cycle_message.format(rendered_cycle=rendered_cycle)
            raise RuntimeError(msg) from exc

        transitive_needs: dict[str, frozenset[str]] = {}
        for job_id in topo_order:
            ancestors = set(direct_needs[job_id])
            for need in direct_needs[job_id]:
                ancestors.update(transitive_needs[need])
            transitive_needs[job_id] = frozenset(ancestors)

        return direct_needs, transitive_needs


def analyze_workflow_job(
    job_id: str,
    job_data: object,
    *,
    context: str | None = None,
    invalid_needs_value_message: str = (
        "Job {job_id} defines unsupported needs value: {raw_needs!r}"
    ),
    invalid_needs_item_message: str = "Job {job_id} contains non-string need: {need!r}",
    invalid_steps_message: str = "Job {job_id} does not define steps as a list",
) -> WorkflowJobAnalysis:
    """Return one workflow job analysis from a raw job mapping."""
    normalized_context = context or f"workflow job {job_id}"
    return WorkflowJobAnalysis(
        job_id=job_id,
        data=workflow_object(job_data, context=normalized_context),
        invalid_needs_value_message=invalid_needs_value_message,
        invalid_needs_item_message=invalid_needs_item_message,
        invalid_steps_message=invalid_steps_message,
    )


def analyze_workflow_jobs(
    value: object,
    *,
    context: str,
    invalid_job_message: str = "{context}.{job_id} must be a mapping",
    invalid_needs_value_message: str = (
        "Job {job_id} defines unsupported needs value: {raw_needs!r}"
    ),
    invalid_needs_item_message: str = "Job {job_id} contains non-string need: {need!r}",
    invalid_steps_message: str = "Job {job_id} does not define steps as a list",
) -> dict[str, WorkflowJobAnalysis]:
    """Return analyzed workflow jobs from a raw ``jobs`` mapping."""
    workflow_jobs = workflow_job_map(
        value,
        context=context,
        invalid_job_message=invalid_job_message,
    )
    return WorkflowAnalysis.from_jobs(
        workflow_jobs,
        invalid_needs_value_message=invalid_needs_value_message,
        invalid_needs_item_message=invalid_needs_item_message,
        invalid_steps_message=invalid_steps_message,
    ).jobs


def load_workflow_analysis(
    workflow_path: Path,
    *,
    context: str = "workflow jobs",
    missing_jobs_message: str = (
        "Workflow {workflow_path} is missing a top-level jobs mapping"
    ),
    invalid_job_message: str = "{context}.{job_id} must be a mapping",
    invalid_needs_value_message: str = (
        "Job {job_id} defines unsupported needs value: {raw_needs!r}"
    ),
    invalid_needs_item_message: str = "Job {job_id} contains non-string need: {need!r}",
    invalid_steps_message: str = "Job {job_id} does not define steps as a list",
) -> WorkflowAnalysis:
    """Load one workflow file into the shared analysis layer."""
    workflow_jobs = load_workflow_jobs(
        workflow_path,
        context=context,
        missing_jobs_message=missing_jobs_message,
        invalid_job_message=invalid_job_message,
    )
    return WorkflowAnalysis.from_jobs(
        workflow_jobs,
        workflow_path=workflow_path,
        invalid_needs_value_message=invalid_needs_value_message,
        invalid_needs_item_message=invalid_needs_item_message,
        invalid_steps_message=invalid_steps_message,
    )


__all__ = [
    "WorkflowAnalysis",
    "WorkflowJobAnalysis",
    "analyze_workflow_job",
    "analyze_workflow_jobs",
    "load_workflow_analysis",
]
