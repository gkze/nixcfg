"""Validate GitHub Actions artifact path contracts in workflows."""

from __future__ import annotations

import os
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from lib.update.ci._workflow_analysis import (
    WorkflowAnalysis,
    WorkflowJobAnalysis,
    load_workflow_analysis,
)
from lib.update.ci._workflow_yaml import WorkflowObject, WorkflowValue, workflow_object
from lib.update.paths import REPO_ROOT

_DOWNLOAD_ACTION_PREFIX = "actions/download-artifact@"
_GLOB_CHARS = frozenset("*?[")
_MATRIX_EXPR = re.compile(r"\$\{\{\s*matrix\.([A-Za-z0-9_]+)\s*\}\}")
_OUTPUT_PATH_RE = re.compile(r"--output\s+([^\s\\]+)")
_SOURCES_MATERIALIZER_MARKERS = (
    "nix run .#nixcfg -- ci pipeline sources",
    "nix run path:.#nixcfg -- ci pipeline sources",
)
_UPLOAD_ACTION_PREFIX = "actions/upload-artifact@"
_REFRESH_FINAL_ARTIFACT_NAME = "merged-generated-formatted"
_REFRESH_FINAL_ARTIFACT_ALLOWED_SPECS = (
    "flake.lock",
    "packages/superset/bun.nix",
    "packages/superset/bun.lock",
    "packages/**/sources.json",
    "packages/**/uv.lock",
    "packages/**/deno-deps.json",
    "packages/**/build.zig.zon.nix",
    "overlays/**/sources.json",
    "packages/codex/Cargo.nix",
    "packages/codex/crate-hashes.json",
    "overlays/goose-cli/Cargo.nix",
    "overlays/goose-cli/crate-hashes.json",
    "packages/zed-editor-nightly/Cargo.nix",
    "packages/zed-editor-nightly/crate-hashes.json",
)
_REFRESH_FINAL_ARTIFACT_REQUIRED_SPECS = _REFRESH_FINAL_ARTIFACT_ALLOWED_SPECS


@dataclass(frozen=True)
class WorkflowJob:
    """One concrete workflow job after matrix expansion."""

    job_id: str
    instance_id: str
    steps: tuple[WorkflowObject, ...]


@dataclass(frozen=True)
class ArtifactUpload:
    """One upload-artifact step with resolved source and stored paths."""

    artifact_name: str
    artifact_root: str
    job_id: str
    job_instance_id: str
    source_paths: tuple[str, ...]
    step_name: str
    stored_paths: tuple[str, ...]


@dataclass(frozen=True)
class ArtifactDownload:
    """One download-artifact step with resolved materialized paths."""

    artifact_name: str
    destination: str
    job_id: str
    materialized_paths: tuple[str, ...]
    step_name: str


def _normalize_relpath(path: str) -> str:
    """Return *path* as a normalized repo-relative POSIX path."""
    normalized = posixpath.normpath(path.strip())
    return "" if normalized == "." else normalized


def _join_relpath(base: str, child: str) -> str:
    """Join repo-relative POSIX paths without introducing ``./`` prefixes."""
    if not base:
        return _normalize_relpath(child)
    return _normalize_relpath(posixpath.join(base, child))


def _has_glob(path_spec: str) -> bool:
    """Return whether *path_spec* contains glob syntax."""
    return any(char in path_spec for char in _GLOB_CHARS)


def _parse_path_specs(raw_value: WorkflowValue | None) -> tuple[str, ...]:
    """Split an artifact ``path`` input into normalized path specs."""
    if raw_value is None:
        return ()

    if isinstance(raw_value, list):
        values = [str(item) for item in raw_value]
    else:
        values = str(raw_value).splitlines()

    parsed: list[str] = []
    for value in values:
        stripped = value.strip()
        if not stripped:
            continue
        if stripped.startswith("!"):
            msg = (
                f"Exclude paths are unsupported in artifact contract checks: {stripped}"
            )
            raise RuntimeError(msg)
        if posixpath.isabs(stripped):
            msg = f"Absolute artifact paths are unsupported in workflow checks: {stripped}"
            raise RuntimeError(msg)
        parsed.append(_normalize_relpath(stripped))
    return tuple(parsed)


def _resolve_spec_paths(
    repo_root: Path,
    path_spec: str,
    *,
    available_paths: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    """Resolve one upload path spec into logical repo-relative file paths."""
    if _has_glob(path_spec):
        matched_paths = {
            _normalize_relpath(path.relative_to(repo_root).as_posix())
            for path in repo_root.glob(path_spec)
            if path.is_file()
        }
        matched_paths.update(
            path for path in available_paths if PurePosixPath(path).match(path_spec)
        )
        return tuple(sorted(matched_paths))

    candidate = repo_root / path_spec
    if candidate.is_dir():
        return tuple(
            sorted(
                _normalize_relpath(path.relative_to(repo_root).as_posix())
                for path in candidate.rglob("*")
                if path.is_file()
            )
        )

    if candidate.is_file() or path_spec in available_paths:
        return (path_spec,)

    msg = (
        f"Artifact path {path_spec!r} does not exist under {repo_root} and was not "
        "materialized earlier in the job"
    )
    raise RuntimeError(msg)


def _resolve_source_paths(
    repo_root: Path,
    path_specs: tuple[str, ...],
    *,
    available_paths: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    """Resolve upload path specs into unique logical file paths."""
    resolved: dict[str, None] = {}
    for path_spec in path_specs:
        for path in _resolve_spec_paths(
            repo_root, path_spec, available_paths=available_paths
        ):
            resolved[path] = None
    return tuple(resolved)


def _resolve_existing_specs(
    repo_root: Path,
    path_specs: tuple[str, ...],
    *,
    available_paths: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    """Resolve path specs that exist now or were materialized earlier."""
    resolved: dict[str, None] = {}
    for path_spec in path_specs:
        if _has_glob(path_spec):
            for path in _resolve_spec_paths(
                repo_root,
                path_spec,
                available_paths=available_paths,
            ):
                resolved[path] = None
            continue

        candidate = repo_root / path_spec
        if candidate.exists() or path_spec in available_paths:
            resolved[path_spec] = None
    return tuple(resolved)


def _path_matches_any(path: str, specs: tuple[str, ...]) -> bool:
    """Return whether one repo-relative path matches any allowed spec."""
    candidate = PurePosixPath(path)
    return any(candidate.match(spec) for spec in specs)


def _artifact_root_for(source_paths: tuple[str, ...]) -> str:
    """Return the effective stored root used by upload-artifact."""
    if not source_paths:
        return ""
    if len(source_paths) == 1:
        return posixpath.dirname(source_paths[0])
    return os.path.commonpath(source_paths)


def _stored_paths_for(source_paths: tuple[str, ...]) -> tuple[str, ...]:
    """Return stored artifact entry paths for resolved logical sources."""
    artifact_root = _artifact_root_for(source_paths)
    if not artifact_root:
        return source_paths
    return tuple(posixpath.relpath(path, artifact_root) for path in source_paths)


def _materialized_paths_for(
    upload: ArtifactUpload, destination: str
) -> tuple[str, ...]:
    """Return the repo-relative paths visible after download-artifact."""
    return tuple(_join_relpath(destination, path) for path in upload.stored_paths)


def _substitute_matrix(
    value: WorkflowValue,
    matrix_values: WorkflowObject,
) -> WorkflowValue:
    """Render simple ``${{ matrix.foo }}`` expressions in YAML values."""
    if isinstance(value, str):
        return _MATRIX_EXPR.sub(
            lambda match: str(matrix_values.get(match.group(1), match.group(0))),
            value,
        )
    if isinstance(value, list):
        return [_substitute_matrix(item, matrix_values) for item in value]
    if isinstance(value, dict):
        return {
            key: _substitute_matrix(item, matrix_values) for key, item in value.items()
        }
    return value


def _expand_jobs(
    workflow_jobs: dict[str, WorkflowObject] | dict[str, WorkflowJobAnalysis],
) -> tuple[WorkflowJob, ...]:
    """Expand matrix ``include`` jobs into concrete workflow jobs."""
    jobs: list[WorkflowJob] = []
    for job_id, job in WorkflowAnalysis.from_jobs(workflow_jobs).jobs.items():
        if matrix_include := job.optional_matrix_include():
            for index, matrix_context in enumerate(matrix_include, start=1):
                steps_list: list[WorkflowObject] = []
                for step_index, step in enumerate(job.steps, start=1):
                    substituted = _substitute_matrix(step, matrix_context)
                    if not isinstance(substituted, dict):
                        msg = (
                            f"Workflow step in job {job_id} expanded to {substituted!r}"
                        )
                        raise TypeError(msg)
                    steps_list.append(
                        workflow_object(
                            substituted,
                            context=f"workflow job {job_id} step {step_index}",
                        )
                    )
                label = ",".join(
                    f"{key}={matrix_context[key]}" for key in sorted(matrix_context)
                ) or str(index)
                jobs.append(
                    WorkflowJob(
                        job_id=job_id,
                        instance_id=f"{job_id}[{label}]",
                        steps=tuple(steps_list),
                    )
                )
            continue

        jobs.append(
            WorkflowJob(
                job_id=job_id,
                instance_id=job_id,
                steps=job.steps,
            )
        )

    return tuple(jobs)


def _step_name(step: WorkflowObject, *, fallback: str) -> str:
    """Return a stable, human-readable name for one workflow step."""
    raw_name = step.get("name")
    return str(raw_name).strip() if raw_name else fallback


def _build_upload(
    step: WorkflowObject,
    *,
    available_paths: frozenset[str] = frozenset(),
    job_id: str,
    job_instance_id: str,
    repo_root: Path,
    step_index: int,
) -> ArtifactUpload:
    """Resolve one upload-artifact step into a contract model."""
    with_data = step.get("with")
    if not isinstance(with_data, dict):
        msg = (
            f"Upload step {_step_name(step, fallback=str(step_index))} "
            f"in {job_id} is missing 'with'"
        )
        raise TypeError(msg)

    artifact_name = str(with_data.get("name", "")).strip()
    if not artifact_name:
        msg = (
            f"Upload step {_step_name(step, fallback=str(step_index))} "
            f"in {job_id} is missing a name"
        )
        raise RuntimeError(msg)

    path_specs = _parse_path_specs(with_data.get("path"))
    source_paths = _resolve_source_paths(
        repo_root,
        path_specs,
        available_paths=available_paths,
    )
    return ArtifactUpload(
        artifact_name=artifact_name,
        artifact_root=_artifact_root_for(source_paths),
        job_id=job_id,
        job_instance_id=job_instance_id,
        source_paths=source_paths,
        step_name=_step_name(step, fallback=f"upload-artifact[{step_index}]"),
        stored_paths=_stored_paths_for(source_paths),
    )


def _build_download(
    step: WorkflowObject,
    *,
    job_id: str,
    step_index: int,
    upload: ArtifactUpload,
) -> ArtifactDownload:
    """Resolve one download-artifact step into a contract model."""
    with_data = step.get("with")
    if not isinstance(with_data, dict):
        msg = (
            f"Download step {_step_name(step, fallback=str(step_index))} "
            f"in {job_id} is missing 'with'"
        )
        raise TypeError(msg)

    destination = _normalize_relpath(str(with_data.get("path", ".")))
    return ArtifactDownload(
        artifact_name=upload.artifact_name,
        destination=destination,
        job_id=job_id,
        materialized_paths=_materialized_paths_for(upload, destination),
        step_name=_step_name(step, fallback=f"download-artifact[{step_index}]"),
    )


def _render_missing_path_error(
    *,
    consumer_upload: ArtifactUpload,
    download: ArtifactDownload,
    producer_upload: ArtifactUpload,
    missing_paths: list[str],
) -> str:
    """Describe one artifact path mismatch in workflow terms."""
    preview = ", ".join(f"`{path}`" for path in missing_paths[:4])
    materialized = (
        ", ".join(f"`{path}`" for path in download.materialized_paths[:4])
        or "(no materialized files)"
    )
    root_display = producer_upload.artifact_root or "."
    return (
        f"Job `{consumer_upload.job_instance_id}` re-uploads {preview} in step "
        f"`{consumer_upload.step_name}`, but artifact `{producer_upload.artifact_name}` "
        f"downloaded by `{download.step_name}` lands under root `{root_display}` and "
        f"materializes as {materialized}."
    )


def _build_needs_graph(
    workflow_jobs: dict[str, WorkflowObject] | dict[str, WorkflowJobAnalysis],
) -> tuple[dict[str, frozenset[str]], dict[str, frozenset[str]]]:
    """Validate job dependencies and return direct/transitive needs."""
    return WorkflowAnalysis.from_jobs(workflow_jobs).needs_graph()


def _render_missing_need_error(
    *,
    artifact_name: str,
    consumer_job: WorkflowJob,
    producer_upload: ArtifactUpload,
    transitive_needs: frozenset[str],
) -> str:
    """Describe an artifact consumer that does not depend on its producer."""
    needs_display = ", ".join(f"`{need}`" for need in sorted(transitive_needs))
    if not needs_display:
        needs_display = "(none)"
    return (
        f"Job `{consumer_job.instance_id}` downloads artifact `{artifact_name}` from "
        f"`{producer_upload.job_instance_id}`, but `{consumer_job.job_id}` does not "
        f"depend on `{producer_upload.job_id}`. Transitive needs: {needs_display}."
    )


def _materialized_paths_from_run_step(
    step: WorkflowObject, *, repo_root: Path
) -> tuple[str, ...]:
    """Return repo-relative paths materialized by known transformation steps."""
    run_value = step.get("run")
    if not isinstance(run_value, str):
        return ()

    materialized_paths: dict[str, None] = {}
    if any(marker in run_value for marker in _SOURCES_MATERIALIZER_MARKERS):
        for path in _resolve_source_paths(
            repo_root,
            (
                "packages/**/sources.json",
                "overlays/**/sources.json",
            ),
        ):
            materialized_paths[path] = None

    for match in _OUTPUT_PATH_RE.findall(run_value):
        output_path = _normalize_relpath(match.strip("\"'"))
        if not output_path or "$" in output_path or posixpath.isabs(output_path):
            continue
        materialized_paths[output_path] = None

    return tuple(materialized_paths)


def _collect_uploads(
    jobs: tuple[WorkflowJob, ...], *, repo_root: Path
) -> tuple[dict[str, ArtifactUpload], list[str]]:
    """Resolve all artifact uploads and report duplicate names."""
    errors: list[str] = []
    uploads: dict[str, ArtifactUpload] = {}

    for job in jobs:
        materialized_paths: set[str] = set()
        for step_index, step in enumerate(job.steps, start=1):
            uses = str(step.get("uses", "")).strip()
            materialized_paths.update(
                _materialized_paths_from_run_step(step, repo_root=repo_root)
            )
            if uses.startswith(_DOWNLOAD_ACTION_PREFIX):
                with_data = step.get("with")
                if isinstance(with_data, dict):
                    artifact_name = str(with_data.get("name", "")).strip()
                    producer_upload = uploads.get(artifact_name)
                    if artifact_name and producer_upload is not None:
                        materialized_paths.update(
                            _materialized_paths_for(
                                producer_upload,
                                _normalize_relpath(str(with_data.get("path", "."))),
                            )
                        )
                continue
            if not uses.startswith(_UPLOAD_ACTION_PREFIX):
                continue

            try:
                upload = _build_upload(
                    step,
                    available_paths=frozenset(materialized_paths),
                    job_id=job.job_id,
                    job_instance_id=job.instance_id,
                    repo_root=repo_root,
                    step_index=step_index,
                )
            except (RuntimeError, TypeError) as exc:
                errors.append(str(exc))
                continue
            existing = uploads.get(upload.artifact_name)
            if existing is not None:
                errors.append(
                    f"Artifact `{upload.artifact_name}` is uploaded multiple times: "
                    f"`{existing.job_instance_id}` and `{upload.job_instance_id}`"
                )
                continue
            uploads[upload.artifact_name] = upload

    return uploads, errors


def _validate_job_artifact_flows(
    job: WorkflowJob,
    *,
    repo_root: Path,
    transitive_needs: dict[str, frozenset[str]],
    uploads: dict[str, ArtifactUpload],
) -> list[str]:
    """Validate one job's artifact downloads against later re-uploads."""
    errors: list[str] = []
    downloaded_artifacts: dict[str, ArtifactDownload] = {}
    materialized_paths: set[str] = set()

    for step_index, step in enumerate(job.steps, start=1):
        uses = str(step.get("uses", "")).strip()
        materialized_paths.update(
            _materialized_paths_from_run_step(step, repo_root=repo_root)
        )

        if uses.startswith(_DOWNLOAD_ACTION_PREFIX):
            with_data = step.get("with")
            if not isinstance(with_data, dict):
                errors.append(
                    f"Download step {_step_name(step, fallback=str(step_index))} "
                    f"in {job.instance_id} is missing 'with'"
                )
                continue

            artifact_name = str(with_data.get("name", "")).strip()
            if not artifact_name:
                errors.append(
                    f"Download step {_step_name(step, fallback=str(step_index))} "
                    f"in {job.instance_id} is missing a name"
                )
                continue

            producer_upload = uploads.get(artifact_name)
            if producer_upload is None:
                errors.append(
                    f"Job `{job.instance_id}` downloads unknown artifact `{artifact_name}`"
                )
                continue

            job_needs = transitive_needs[job.job_id]
            if producer_upload.job_id not in job_needs:
                errors.append(
                    _render_missing_need_error(
                        artifact_name=artifact_name,
                        consumer_job=job,
                        producer_upload=producer_upload,
                        transitive_needs=job_needs,
                    )
                )

            download = _build_download(
                step,
                job_id=job.instance_id,
                step_index=step_index,
                upload=producer_upload,
            )
            existing_download = downloaded_artifacts.get(artifact_name)
            if existing_download is None:
                downloaded_artifacts[artifact_name] = download
            else:
                downloaded_artifacts[artifact_name] = ArtifactDownload(
                    artifact_name=artifact_name,
                    destination=existing_download.destination,
                    job_id=job.instance_id,
                    materialized_paths=tuple(
                        sorted(
                            set(existing_download.materialized_paths).union(
                                download.materialized_paths
                            )
                        )
                    ),
                    step_name=existing_download.step_name,
                )
            materialized_paths.update(download.materialized_paths)
            continue

        if not uses.startswith(_UPLOAD_ACTION_PREFIX):
            continue

        try:
            consumer_upload = _build_upload(
                step,
                available_paths=frozenset(materialized_paths),
                job_id=job.job_id,
                job_instance_id=job.instance_id,
                repo_root=repo_root,
                step_index=step_index,
            )
        except (RuntimeError, TypeError) as exc:
            errors.append(str(exc))
            continue
        consumer_sources = set(consumer_upload.source_paths)

        for artifact_name, download in downloaded_artifacts.items():
            producer_upload = uploads[artifact_name]
            overlapping_paths = sorted(
                consumer_sources.intersection(producer_upload.source_paths)
            )
            if not overlapping_paths:
                continue

            missing_paths = [
                path
                for path in overlapping_paths
                if path not in set(download.materialized_paths)
                and path not in materialized_paths
            ]
            if missing_paths:
                errors.append(
                    _render_missing_path_error(
                        consumer_upload=consumer_upload,
                        download=download,
                        producer_upload=producer_upload,
                        missing_paths=missing_paths,
                    )
                )

    return errors


def _validate_refresh_final_artifact_scope(
    uploads: dict[str, ArtifactUpload],
    *,
    repo_root: Path,
) -> list[str]:
    """Ensure the PR artifact contains only generated update outputs."""
    upload = uploads.get(_REFRESH_FINAL_ARTIFACT_NAME)
    if upload is None:
        return []

    errors: list[str] = []
    source_paths = set(upload.source_paths)
    unexpected_paths = sorted(
        path
        for path in source_paths
        if not _path_matches_any(path, _REFRESH_FINAL_ARTIFACT_ALLOWED_SPECS)
    )
    if unexpected_paths:
        preview = ", ".join(f"`{path}`" for path in unexpected_paths[:4])
        errors.append(
            f"Artifact `{_REFRESH_FINAL_ARTIFACT_NAME}` includes non-update "
            f"path(s): {preview}"
        )

    required_paths = set(
        _resolve_existing_specs(
            repo_root,
            _REFRESH_FINAL_ARTIFACT_REQUIRED_SPECS,
            available_paths=frozenset(source_paths),
        )
    )
    missing_paths = sorted(required_paths - source_paths)
    if missing_paths:
        preview = ", ".join(f"`{path}`" for path in missing_paths[:4])
        errors.append(
            f"Artifact `{_REFRESH_FINAL_ARTIFACT_NAME}` is missing update "
            f"path(s): {preview}"
        )

    return errors


def validate_workflow_artifact_contracts(
    *,
    workflow_path: Path | os.PathLike[str] | None = None,
    repo_root: Path | os.PathLike[str] = REPO_ROOT,
) -> None:
    """Raise ``RuntimeError`` when artifact flow semantics are inconsistent."""
    repo_root = Path(repo_root)
    workflow_path = (
        repo_root / ".github/workflows/update.yml"
        if workflow_path is None
        else Path(workflow_path)
    )
    workflow = load_workflow_analysis(
        workflow_path,
        context=f"workflow jobs {workflow_path}",
        missing_jobs_message="Workflow {workflow_path} does not contain a jobs mapping",
    )

    _, transitive_needs = workflow.needs_graph()
    jobs = _expand_jobs(workflow.jobs)
    uploads, errors = _collect_uploads(jobs, repo_root=repo_root)
    errors.extend(_validate_refresh_final_artifact_scope(uploads, repo_root=repo_root))
    for job in jobs:
        errors.extend(
            _validate_job_artifact_flows(
                job,
                repo_root=repo_root,
                transitive_needs=transitive_needs,
                uploads=uploads,
            )
        )

    if errors:
        raise RuntimeError("\n".join(errors))


__all__ = ["validate_workflow_artifact_contracts"]
