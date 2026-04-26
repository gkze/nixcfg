"""Workflow-step helpers for update pull-request body generation."""

from __future__ import annotations

import dataclasses
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from lib.update.ci.pr_body import (
    FlakeInputSnapshot,
    FlakeInputUpdate,
    LinkValue,
    PRBodyModel,
    SourceChange,
)

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Callable, Sequence

    from lib.update.ci.flake_lock_diff import InputInfo

    type CollectChanges = Callable[
        [Path, Path],
        tuple[
            list[InputInfo],
            list[InputInfo],
            list[tuple[InputInfo, InputInfo]],
        ],
    ]
    type RunCommand = Callable[..., subprocess.CompletedProcess[str]]


@dataclasses.dataclass(frozen=True)
class PRBodyOptions:
    """Inputs used to render compare and workflow links in PR body output."""

    workflow_url: str
    server_url: str
    repository: str
    base_ref: str
    compare_head: str = "update_flake_lock_action"


class GitHistoryReadError(RuntimeError):
    """Raised when Git history cannot be read for PR body generation."""


def is_missing_history_pathspec(stderr: str, pathspec: str) -> bool:
    """Return whether git stderr describes a missing historical path."""
    missing_markers = (
        "exists on disk, but not in",
        "does not exist in",
        "pathspec",
    )
    stderr_lower = stderr.lower()
    path_tail = pathspec.split(":", 1)[-1]
    return path_tail.lower() in stderr_lower and any(
        marker in stderr_lower for marker in missing_markers
    )


def git_show(
    pathspec: str,
    *,
    cwd: Path | None = None,
    run: RunCommand,
    missing_ok: bool = True,
) -> str:
    """Read one file from git history, tolerating missing paths when requested."""
    if cwd is None:
        result = run(["git", "show", pathspec], check=False, capture_output=True)
    else:
        result = run(
            ["git", "show", pathspec],
            check=False,
            capture_output=True,
            cwd=cwd,
        )
    if result.returncode == 0:
        return result.stdout

    stderr = (result.stderr or "").strip()
    if missing_ok and stderr and is_missing_history_pathspec(stderr, pathspec):
        return "{}\n"

    message = stderr or f"git show {pathspec!r} exited with code {result.returncode}"
    raise GitHistoryReadError(message)


def source_diff_pathspecs(
    *,
    package_dirs: Sequence[str],
    repo_root: Path,
    sources_git_pathspecs: tuple[str, ...],
    sources_file_name: str,
) -> tuple[str, ...]:
    """Return pathspecs to diff sources files, including flat layout when present."""
    if any(
        child.is_file() and child.name.endswith(f".{sources_file_name}")
        for directory in package_dirs
        if (root := repo_root / directory).is_dir()
        for child in root.iterdir()
    ):
        return sources_git_pathspecs

    return tuple(
        pathspec
        for pathspec in sources_git_pathspecs
        if pathspec.endswith(f"/{sources_file_name}")
    )


def compare_url(options: PRBodyOptions) -> str:
    """Build the compare URL for the update PR."""
    return (
        f"{options.server_url}/{options.repository}/compare/"
        f"{options.base_ref}...{options.compare_head}"
    )


def collect_changed_source_files(
    *,
    cwd: Path | None = None,
    run: RunCommand,
    pathspecs: tuple[str, ...],
    is_sources_file_path: Callable[[str], bool],
) -> list[str]:
    """Collect modified and untracked `sources.json` paths."""
    diff_args = [
        "git",
        "diff",
        "--name-only",
        "HEAD",
        "--",
        *pathspecs,
    ]
    if cwd is None:
        source_files_result = run(diff_args, capture_output=True)
    else:
        source_files_result = run(diff_args, capture_output=True, cwd=cwd)
    source_files = [
        line.strip() for line in source_files_result.stdout.splitlines() if line.strip()
    ]

    untracked_args = ["git", "ls-files", "--others", "--exclude-standard"]
    if cwd is None:
        untracked_result = run(untracked_args, capture_output=True)
    else:
        untracked_result = run(untracked_args, capture_output=True, cwd=cwd)
    untracked_sources = [
        path
        for line in untracked_result.stdout.splitlines()
        if (path := line.strip()) and is_sources_file_path(path)
    ]
    return sorted({*source_files, *untracked_sources})


def _current_sources_snapshot(file_path: str, *, repo_root: Path) -> str:
    current_path = repo_root / file_path
    if current_path.is_file():
        return current_path.read_text(encoding="utf-8")
    # Deletions and renames can leave git-diff path entries without a working-tree file.
    return "{}\n"


def _source_change_url(file_path: str, options: PRBodyOptions) -> str:
    return (
        f"{options.server_url}/{options.repository}/blob/"
        f"{options.compare_head}/{file_path}"
    )


def _build_source_changes(
    *,
    repo_root: Path,
    temp_root: Path,
    source_files: list[str],
    options: PRBodyOptions,
    git_show: Callable[..., str],
    run_sources_diff: Callable[..., str],
    no_changes_message: str,
) -> tuple[SourceChange, ...]:
    changes: list[SourceChange] = []
    for index, file_path in enumerate(source_files):
        old_json = temp_root / f"old-sources-{index}.json"
        new_json = temp_root / f"new-sources-{index}.json"
        old_json.write_text(
            git_show(f"HEAD:{file_path}", missing_ok=True),
            encoding="utf-8",
        )
        new_json.write_text(
            _current_sources_snapshot(file_path, repo_root=repo_root),
            encoding="utf-8",
        )

        pkg_diff = run_sources_diff(old_json, new_json, output_format="unified")
        if pkg_diff == no_changes_message:
            continue

        changes.append(
            SourceChange(
                path=file_path,
                url=_source_change_url(file_path, options),
                diff=pkg_diff,
            )
        )
    return tuple(changes)


def _flake_source_link(info: InputInfo) -> LinkValue:
    if info.owner and info.repo and info.type == "github":
        return LinkValue(
            label=f"{info.owner}/{info.repo}",
            url=f"https://github.com/{info.owner}/{info.repo}",
        )
    return LinkValue(label=info.name)


def _flake_revision_link(info: InputInfo) -> LinkValue:
    if info.owner and info.repo and info.type == "github" and info.rev_full:
        return LinkValue(
            label=info.rev,
            url=f"https://github.com/{info.owner}/{info.repo}/commit/{info.rev_full}",
        )
    return LinkValue(label=info.rev)


def _flake_compare_link(old_info: InputInfo, new_info: InputInfo) -> LinkValue:
    if (
        old_info.type == "github"
        and new_info.type == "github"
        and old_info.owner
        and old_info.repo
        and old_info.owner == new_info.owner
        and old_info.repo == new_info.repo
        and old_info.rev_full
        and new_info.rev_full
    ):
        return LinkValue(
            label="Diff",
            url=(
                f"https://github.com/{old_info.owner}/{old_info.repo}/compare/"
                f"{old_info.rev_full}...{new_info.rev_full}"
            ),
        )
    return LinkValue(label="-")


def build_update_pr_body_model(
    *,
    repo_root: Path,
    temp_root: Path,
    options: PRBodyOptions,
    git_show: Callable[..., str],
    collect_changes: CollectChanges,
    collect_changed_source_files: Callable[[], list[str]],
    run_sources_diff: Callable[..., str],
    no_changes_message: str,
) -> PRBodyModel:
    """Build the structured PR-body model for the current update diff."""
    new_lock_path = repo_root / "flake.lock"
    if not new_lock_path.is_file():
        msg = f"Expected flake.lock at {new_lock_path} while generating PR body"
        raise FileNotFoundError(msg)

    old_lock_path = temp_root / "old-flake.lock"
    old_lock_path.write_text(
        git_show("HEAD:flake.lock", missing_ok=False),
        encoding="utf-8",
    )
    added, removed, updated = collect_changes(old_lock_path, new_lock_path)
    return PRBodyModel(
        workflow_run_url=options.workflow_url,
        compare_url=compare_url(options),
        updated_flake_inputs=tuple(
            FlakeInputUpdate(
                input_name=new_info.name,
                source=_flake_source_link(new_info),
                previous=_flake_revision_link(old_info),
                current=_flake_revision_link(new_info),
                diff=_flake_compare_link(old_info, new_info),
            )
            for old_info, new_info in updated
        ),
        added_flake_inputs=tuple(
            FlakeInputSnapshot(
                input_name=info.name,
                source=_flake_source_link(info),
                revision=_flake_revision_link(info),
            )
            for info in added
        ),
        removed_flake_inputs=tuple(
            FlakeInputSnapshot(
                input_name=info.name,
                source=_flake_source_link(info),
                revision=_flake_revision_link(info),
            )
            for info in removed
        ),
        source_changes=_build_source_changes(
            repo_root=repo_root,
            temp_root=temp_root,
            source_files=collect_changed_source_files(),
            options=options,
            git_show=git_show,
            run_sources_diff=run_sources_diff,
            no_changes_message=no_changes_message,
        ),
    )


def generate_pr_body(
    *,
    output: str | Path,
    options: PRBodyOptions,
    repo_root: Path,
    write_pr_body: Callable[..., int],
    collect_changes: CollectChanges,
    collect_changed_source_files: Callable[[], list[str]],
    run_sources_diff: Callable[..., str],
    no_changes_message: str,
    git_show: Callable[..., str],
) -> int:
    """Generate pull-request body markdown for update runs."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        model = build_update_pr_body_model(
            repo_root=repo_root,
            temp_root=temp_root,
            options=options,
            git_show=git_show,
            collect_changes=collect_changes,
            collect_changed_source_files=collect_changed_source_files,
            run_sources_diff=run_sources_diff,
            no_changes_message=no_changes_message,
        )
    return write_pr_body(output=output, model=model)
