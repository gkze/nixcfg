"""Python implementations for CI workflow shell steps."""

from __future__ import annotations

import asyncio
import dataclasses
import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
    from collections.abc import Callable

import typer

from lib.update.bun_lock import (
    prepare_source_package_lock,
    validate_source_package_exact_versions,
)
from lib.update.ci._cli import make_main, make_typer_app
from lib.update.ci._subprocess import run_command as _run
from lib.update.ci._time import format_duration as _format_duration
from lib.update.ci._workflow_analysis import load_workflow_analysis
from lib.update.ci.flake_lock_diff import run_diff as run_flake_lock_diff
from lib.update.ci.sources_json_diff import NoChangesMessage
from lib.update.ci.sources_json_diff import run_diff as run_sources_diff
from lib.update.ci.workflow_artifact_contracts import (
    validate_workflow_artifact_contracts,
)
from lib.update.ci.workflow_structure_contracts import (
    validate_workflow_structure_contracts,
)
from lib.update.paths import (
    PACKAGE_DIRS,
    SOURCES_FILE_NAME,
    SOURCES_GIT_PATHSPECS,
    is_sources_file_path,
)


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


@dataclasses.dataclass(frozen=True)
class CertificationPRBodyOptions:
    """Inputs used to render the certification section onto an update PR."""

    workflow_url: str
    started_at: str
    updated_at: str
    cachix_name: str
    workflow_path: Path = Path(".github/workflows/update-certify.yml")


_CERTIFICATION_SECTION_START = "<!-- update-certification:start -->"
_CERTIFICATION_SECTION_END = "<!-- update-certification:end -->"
_CERTIFICATION_SHARED_CLOSURE_MARKER = "nix run .#nixcfg -- ci cache closure"
_CERTIFICATION_EXCLUDE_REF_RE = re.compile(r"--exclude-ref\s+([^\s\\]+)")
_CERTIFICATION_FLAKE_REF_RE = re.compile(r"(\.#[^\s\\]+)")
_CERTIFICATION_BUILD_REF_RE = re.compile(r"nix build\s+([^\s\\]+)")
_CERTIFICATION_DARWIN_HOST_RE = re.compile(r"build-darwin-config\s+([^\s\\]+)")
_PAIR_ITEM_COUNT = 2


def _cmd_nix_flake_update() -> int:
    _run(["nix", "flake", "update"])
    return 0


def _xcode_version_key(app_path: Path) -> tuple[int, ...]:
    stem = app_path.stem.removeprefix("Xcode")
    parts = [
        int(token) for token in stem.replace("-", ".").split(".") if token.isdigit()
    ]
    return tuple(parts)


# Large preinstalled directories documented by the official Ubuntu 24.04
# runner image inventory. Keep this list focused on tooling the update
# workflow does not rely on after setup-nix/setup-uv complete.
_LINUX_CLEANUP_PATHS = (
    "/usr/local/lib/android",
    "/usr/share/swift",
    "/usr/share/dotnet",
    "/usr/share/miniconda",
    "/usr/local/.ghcup",
    "/usr/lib/jvm",
    "/opt/hostedtoolcache/CodeQL",
    "/opt/az",
    "/usr/lib/google-cloud-sdk",
    "/usr/local/aws-cli",
    "/usr/local/aws-sam-cli",
    "/opt/google",
    "/opt/microsoft",
    "/usr/lib/firefox",
    "/home/linuxbrew",
    "/usr/local/share/chromium",
    "/usr/local/share/chromedriver-linux64",
    "/usr/local/share/edge_driver",
    "/usr/local/share/gecko_driver",
)


def _report_disk_usage(*paths: str) -> None:
    args = ["df", "-h", *paths] if paths else ["df", "-h", "/"]
    _run(args, check=False)


def _cmd_free_disk_space_macos() -> None:
    xcodes = sorted(Path("/Applications").glob("Xcode*.app"), key=_xcode_version_key)
    latest_xcode = xcodes[-1] if xcodes else None
    for xcode in xcodes:
        if xcode == latest_xcode:
            continue
        _run(["sudo", "rm", "-rf", str(xcode)])

    home = Path.home()
    _run(["sudo", "rm", "-rf", str(home / "Library/Developer/CoreSimulator")])
    _run(["xcrun", "simctl", "delete", "all"], check=False)
    _run([
        "sudo",
        "rm",
        "-rf",
        str(home / "Library/Android/sdk"),
        "/usr/local/share/dotnet",
        str(home / "hostedtoolcache"),
    ])


def _cmd_free_disk_space_linux() -> None:
    _run(["sudo", "apt-get", "clean"], check=False)
    _run(
        ["sudo", "docker", "system", "prune", "--all", "--force", "--volumes"],
        check=False,
    )
    _run(["sudo", "swapoff", "-a"], check=False)

    julia_paths = tuple(str(path) for path in Path("/usr/local").glob("julia*"))
    _run([
        "sudo",
        "rm",
        "-rf",
        "/mnt/swapfile",
        *_LINUX_CLEANUP_PATHS,
        *julia_paths,
    ])


def _cmd_free_disk_space(*, force_local: bool = False) -> int:
    running_in_ci = os.environ.get("CI", "").lower() in {"1", "true", "yes"}
    if not running_in_ci and not force_local:
        sys.stderr.write(
            "Refusing to run free-disk-space outside CI. Re-run with --force-local to override.\n"
        )
        return 2

    if sys.platform == "darwin":
        disk_paths = ("/",)
        cleanup = _cmd_free_disk_space_macos
    elif sys.platform.startswith("linux"):
        disk_paths = ("/", "/mnt")
        cleanup = _cmd_free_disk_space_linux
    else:
        sys.stderr.write(
            f"free-disk-space only supports Linux and macOS runners, got {sys.platform!r}.\n"
        )
        return 2

    sys.stdout.write("=== Before cleanup ===\n")
    _report_disk_usage(*disk_paths)
    cleanup()
    sys.stdout.write("=== After cleanup ===\n")
    _report_disk_usage(*disk_paths)
    return 0


def _cmd_install_darwin_tools() -> int:
    _run(["brew", "install", "--cask", "macfuse"])
    _run(["brew", "install", "1password-cli"])
    return 0


_PREFETCH_FLAKE_INPUTS_ARGS = ["nix", "flake", "archive", "--json"]
_PREFETCH_FLAKE_INPUTS_ATTEMPTS = 3
_PREFETCH_FLAKE_INPUTS_RETRY_DELAYS = (1.0, 2.0)


def _cmd_prefetch_flake_inputs() -> int:
    for attempt in range(1, _PREFETCH_FLAKE_INPUTS_ATTEMPTS + 1):
        try:
            _run(
                _PREFETCH_FLAKE_INPUTS_ARGS,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError:
            if attempt == _PREFETCH_FLAKE_INPUTS_ATTEMPTS:
                sys.stderr.write(
                    "Warning: prefetch-flake-inputs failed after "
                    f"{_PREFETCH_FLAKE_INPUTS_ATTEMPTS} attempts; continuing "
                    "because this step only warms caches.\n"
                )
                break
            delay = _PREFETCH_FLAKE_INPUTS_RETRY_DELAYS[attempt - 1]
            sys.stderr.write(
                "Warning: prefetch-flake-inputs failed; "
                f"retrying in {delay:.1f}s (attempt {attempt + 1}/"
                f"{_PREFETCH_FLAKE_INPUTS_ATTEMPTS}).\n"
            )
            time.sleep(delay)
        else:
            break
    return 0


_DARWIN_LOCK_SMOKE_EXPRS = (
    ".#darwinConfigurations.argus.config.home-manager.users.george.programs.nixvim.content",
    ".#darwinConfigurations.rocinante.config.home-manager.users.george.programs.nixvim.content",
)

_DARWIN_FULL_SMOKE_REFS = (
    ".#darwinConfigurations.argus.system",
    ".#darwinConfigurations.rocinante.system",
    ".#homeConfigurations.george.activationPackage",
)


def _cmd_build_darwin_config(*, host: str) -> int:
    _run(["nix", "build", "--impure", f".#darwinConfigurations.{host}.system"])
    return 0


def _cmd_eval_darwin_lock_smoke() -> int:
    for expr in _DARWIN_LOCK_SMOKE_EXPRS:
        _run(["nix", "eval", "--json", "--impure", expr], stdout=subprocess.DEVNULL)
    return 0


def _cmd_eval_darwin_full_smoke() -> int:
    for ref in _DARWIN_FULL_SMOKE_REFS:
        _run(["nix", "build", "--dry-run", "--impure", ref])
    return 0


def _cmd_smoke_check_update_app() -> int:
    _run(
        ["nix", "eval", "--raw", ".#apps.x86_64-linux.nixcfg.program"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return 0


def _cmd_list_update_targets() -> int:
    update_cli = importlib.import_module("lib.update.cli")
    return asyncio.run(
        update_cli.run_updates(update_cli.UpdateOptions(list_targets=True))
    )


def _cmd_verify_workflow_contracts(
    *,
    workflow: Path,
    validator: Callable[..., None],
    description: str,
) -> int:
    try:
        validator(workflow_path=workflow)
    except (RuntimeError, TypeError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    sys.stdout.write(f"Verified {description} for {workflow}\n")
    return 0


def _cmd_verify_artifacts(*, workflow: Path) -> int:
    return _cmd_verify_workflow_contracts(
        workflow=workflow,
        validator=validate_workflow_artifact_contracts,
        description="workflow artifact contracts",
    )


def _cmd_verify_structure(*, workflow: Path) -> int:
    return _cmd_verify_workflow_contracts(
        workflow=workflow,
        validator=validate_workflow_structure_contracts,
        description="workflow structure contracts",
    )


def _cmd_validate_bun_lock(*, lock_file: Path) -> int:
    try:
        validate_source_package_exact_versions(lock_file)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    sys.stdout.write(f"Validated Bun source package overrides for {lock_file}\n")
    return 0


def _cmd_prepare_bun_lock(
    *,
    workspace_root: Path,
    lock_file: Path,
    bun_executable: str,
) -> int:
    try:
        relocked = prepare_source_package_lock(
            workspace_root,
            lock_file,
            bun_executable=bun_executable,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1

    if relocked:
        sys.stdout.write(
            f"Relocked Bun source package overrides for {lock_file} via {bun_executable}\n"
        )
    else:
        sys.stdout.write(f"Validated Bun source package overrides for {lock_file}\n")
    return 0


def _json_object(value: object, *, context: str) -> dict[str, Any]:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                msg = f"Expected string keys for JSON object {context}"
                raise TypeError(msg)
            result[key] = item
        return result
    msg = f"Expected JSON object for {context}"
    raise TypeError(msg)


def _load_flake_lock_input_locked(*, lock_file: Path, node: str) -> dict[str, Any]:
    payload = _json_object(
        json.loads(lock_file.read_text(encoding="utf-8")),
        context=f"{lock_file}",
    )
    nodes = _json_object(payload.get("nodes", {}), context=f"{lock_file} nodes")
    if node not in nodes:
        msg = f"{lock_file} does not contain flake.lock node {node!r}"
        raise ValueError(msg)
    node_payload = _json_object(nodes[node], context=f"{lock_file} node {node!r}")
    locked = node_payload.get("locked", {})
    if locked is None:
        return {}
    return _json_object(locked, context=f"{lock_file} node {node!r} locked")


def _load_json_snapshot(*, snapshot_path: Path) -> dict[str, Any]:
    return _json_object(
        json.loads(snapshot_path.read_text(encoding="utf-8")),
        context=f"{snapshot_path}",
    )


def _cmd_snapshot_flake_input(
    *,
    node: str,
    lock_file: Path,
    output: Path,
) -> int:
    try:
        locked = _load_flake_lock_input_locked(lock_file=lock_file, node=node)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(locked, sort_keys=True) + "\n", encoding="utf-8")
    except (OSError, TypeError, ValueError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1

    sys.stdout.write(f"Snapshotted flake.lock input {node!r} to {output}\n")
    return 0


def _cmd_compare_flake_input(
    *,
    node: str,
    before: Path,
    lock_file: Path,
    github_output: Path | None,
    output_name: str,
) -> int:
    try:
        before_snapshot = _load_json_snapshot(snapshot_path=before)
        after_snapshot = _load_flake_lock_input_locked(lock_file=lock_file, node=node)
    except (OSError, TypeError, ValueError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1

    changed = before_snapshot != after_snapshot
    if github_output is not None:
        try:
            with github_output.open("a", encoding="utf-8") as handle:
                handle.write(f"{output_name}={'true' if changed else 'false'}\n")
        except OSError as exc:
            sys.stderr.write(f"{exc}\n")
            return 1

    sys.stdout.write(
        f"flake.lock input {node!r} changed: {'true' if changed else 'false'}\n"
    )
    return 0


def _is_missing_history_pathspec(stderr: str, pathspec: str) -> bool:
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


def _git_show(pathspec: str, *, missing_ok: bool = True) -> str:
    result = _run(["git", "show", pathspec], check=False, capture_output=True)
    if result.returncode == 0:
        return result.stdout

    stderr = (result.stderr or "").strip()
    if missing_ok and stderr and _is_missing_history_pathspec(stderr, pathspec):
        return "{}\n"

    message = stderr or f"git show {pathspec!r} exited with code {result.returncode}"
    raise GitHistoryReadError(message)


def _source_diff_pathspecs() -> tuple[str, ...]:
    """Return pathspecs to diff sources files, including flat layout when present."""
    if any(
        child.is_file() and child.name.endswith(f".{SOURCES_FILE_NAME}")
        for directory in PACKAGE_DIRS
        if (root := Path(directory)).is_dir()
        for child in root.iterdir()
    ):
        return SOURCES_GIT_PATHSPECS

    return tuple(
        pathspec
        for pathspec in SOURCES_GIT_PATHSPECS
        if pathspec.endswith(f"/{SOURCES_FILE_NAME}")
    )


def _compare_url(options: PRBodyOptions) -> str:
    return (
        f"{options.server_url}/{options.repository}/compare/"
        f"{options.base_ref}...{options.compare_head}"
    )


def _collect_changed_source_files() -> list[str]:
    source_files_result = _run(
        [
            "git",
            "diff",
            "--name-only",
            "HEAD",
            "--",
            *_source_diff_pathspecs(),
        ],
        capture_output=True,
    )
    source_files = [
        line.strip() for line in source_files_result.stdout.splitlines() if line.strip()
    ]

    untracked_result = _run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        capture_output=True,
    )
    untracked_sources = [
        path
        for line in untracked_result.stdout.splitlines()
        if (path := line.strip()) and is_sources_file_path(path)
    ]
    return sorted({*source_files, *untracked_sources})


def _current_sources_snapshot(file_path: str) -> str:
    current_path = Path(file_path)
    if current_path.is_file():
        return current_path.read_text(encoding="utf-8")
    return "{}\n"


def _render_source_diff_sections(
    *,
    temp_root: Path,
    source_files: list[str],
    options: PRBodyOptions,
) -> list[str]:
    body_lines: list[str] = []
    rendered_sources_diffs = False
    for index, file_path in enumerate(source_files):
        old_json = temp_root / f"old-sources-{index}.json"
        new_json = temp_root / f"new-sources-{index}.json"
        old_json.write_text(
            _git_show(f"HEAD:{file_path}", missing_ok=True),
            encoding="utf-8",
        )
        new_json.write_text(_current_sources_snapshot(file_path), encoding="utf-8")

        pkg_diff = run_sources_diff(old_json, new_json, output_format="unified")
        if pkg_diff == NoChangesMessage:
            continue

        if not rendered_sources_diffs:
            body_lines.extend(["", "### Per-package sources.json changes"])
            rendered_sources_diffs = True

        file_diff_url = (
            f"{options.server_url}/{options.repository}/blob/"
            f"{options.compare_head}/{file_path}"
        )
        body_lines.extend([
            "",
            "<details>",
            f'<summary><a href="{file_diff_url}"><code>{file_path}</code></a></summary>',
            "",
            "```diff",
            pkg_diff,
            "```",
            "</details>",
        ])
    return body_lines


def generate_pr_body(
    *,
    output: str | Path,
    options: PRBodyOptions,
) -> int:
    """Generate pull-request body markdown for update runs.

    This is the shared implementation called by the Typer CLI command.
    """
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        old_lock_path = temp_root / "old-flake.lock"
        old_lock_path.write_text(
            _git_show("HEAD:flake.lock", missing_ok=False),
            encoding="utf-8",
        )

        flake_diff = run_flake_lock_diff(old_lock_path, Path("flake.lock"))
        body_lines = [
            f"**[Workflow run]({options.workflow_url})**",
            "",
            f"**[Compare]({_compare_url(options)})**",
            "",
            flake_diff or "No flake.lock input changes detected.",
        ]
        body_lines.extend(
            _render_source_diff_sections(
                temp_root=temp_root,
                source_files=_collect_changed_source_files(),
                options=options,
            )
        )

    output_path.write_text("\n".join(body_lines) + "\n", encoding="utf-8")
    return 0


def _cmd_generate_pr_body(
    *,
    output: str | Path,
    workflow_url: str,
    server_url: str,
    repository: str,
    base_ref: str,
    compare_head: str,
) -> int:
    return generate_pr_body(
        output=output,
        options=PRBodyOptions(
            workflow_url=workflow_url,
            server_url=server_url,
            repository=repository,
            base_ref=base_ref,
            compare_head=compare_head,
        ),
    )


def _load_json_file(*, input_path: Path, context: str) -> dict[str, Any]:
    return _json_object(
        json.loads(input_path.read_text(encoding="utf-8")),
        context=context,
    )


def _required_string_field(payload: dict[str, Any], *, field: str, context: str) -> str:
    value = payload.get(field)
    if isinstance(value, str) and value.strip():
        return value
    msg = f"Expected non-empty string field {field!r} in {context}"
    raise TypeError(msg)


def _parse_github_timestamp(value: str) -> datetime:
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


def _format_github_timestamp(value: str) -> str:
    return _parse_github_timestamp(value).strftime("%Y-%m-%d %H:%M UTC")


def _ordered_unique(items: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return tuple(ordered)


def _render_code_list(values: tuple[str, ...]) -> str:
    rendered = [f"`{value}`" for value in values]
    if not rendered:
        msg = "Expected at least one value to render"
        raise ValueError(msg)
    if len(rendered) == 1:
        return rendered[0]
    if len(rendered) == _PAIR_ITEM_COUNT:
        return f"{rendered[0]} and {rendered[1]}"
    return ", ".join(rendered[:-1]) + f", and {rendered[-1]}"


def _certification_matrix_targets(
    workflow_path: Path, *, job_id: str
) -> tuple[str, ...]:
    workflow = load_workflow_analysis(workflow_path)
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
    workflow_path: Path,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    workflow = load_workflow_analysis(workflow_path)
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
        if ref not in excluded
    )
    if not included:
        msg = "darwin-shared shared-closure step must include at least one flake ref"
        raise RuntimeError(msg)

    return included, excluded


def _certification_darwin_host_targets(workflow_path: Path) -> tuple[str, ...]:
    workflow = load_workflow_analysis(workflow_path)
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


def _certification_linux_targets(workflow_path: Path) -> tuple[str, ...]:
    workflow = load_workflow_analysis(workflow_path)
    targets = _ordered_unique([
        match.strip("\"'")
        for run in workflow.require_job(job_id="linux-x86_64").run_strings
        for match in _CERTIFICATION_BUILD_REF_RE.findall(run)
    ])
    if not targets:
        msg = "linux-x86_64 must define at least one nix build target"
        raise RuntimeError(msg)
    return targets


def _render_certification_targets(
    workflow_path: Path, *, cachix_name: str
) -> list[str]:
    darwin_heavy_targets = [
        *_certification_matrix_targets(
            workflow_path,
            job_id="darwin-priority-heavy",
        ),
        *_certification_matrix_targets(
            workflow_path,
            job_id="darwin-extra-heavy",
        ),
    ]
    shared_refs, shared_excludes = _certification_shared_closure_refs(workflow_path)
    darwin_host_targets = _certification_darwin_host_targets(workflow_path)
    linux_targets = _certification_linux_targets(workflow_path)

    rendered_targets = [
        f"- `{target}`" for target in _ordered_unique(darwin_heavy_targets)
    ]
    plural = "s" if len(shared_excludes) != 1 else ""
    rendered_targets.append(
        "- Shared Darwin closure for "
        + _render_code_list(shared_refs)
        + f" excluding {len(shared_excludes)} heavy package closure{plural}"
    )
    rendered_targets.extend(f"- `{target}`" for target in darwin_host_targets)
    rendered_targets.extend(f"- `{target}`" for target in linux_targets)
    return [f"Closures pushed to Cachix (`{cachix_name}`):", *rendered_targets]


def _render_certification_section(options: CertificationPRBodyOptions) -> str:
    started_at = _parse_github_timestamp(options.started_at)
    updated_at = _parse_github_timestamp(options.updated_at)
    elapsed_seconds = max(0.0, (updated_at - started_at).total_seconds())
    lines = [
        "## Certification",
        "",
        f"Latest certification: [workflow run]({options.workflow_url})",
        f"Updated: `{_format_github_timestamp(options.updated_at)}`",
        f"Elapsed: `{_format_duration(elapsed_seconds)}`",
        "",
        *_render_certification_targets(
            options.workflow_path,
            cachix_name=options.cachix_name,
        ),
    ]
    return "\n".join(lines)


def _replace_certification_section(*, body: str, section: str) -> str:
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
) -> int:
    """Render a PR body with the certification section inserted or updated."""
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    current_body = Path(existing_body).read_text(encoding="utf-8")
    updated_body = _replace_certification_section(
        body=current_body,
        section=_render_certification_section(options),
    )
    output_path.write_text(updated_body, encoding="utf-8")
    return 0


def _cmd_render_certification_pr_body(
    *,
    existing_body: str | Path,
    output: str | Path,
    run_json: Path,
    cachix_name: str,
    workflow: Path,
) -> int:
    try:
        run_payload = _load_json_file(
            input_path=run_json,
            context=f"workflow run payload {run_json}",
        )
        return render_certification_pr_body(
            existing_body=existing_body,
            output=output,
            options=CertificationPRBodyOptions(
                workflow_url=_required_string_field(
                    run_payload,
                    field="html_url",
                    context=f"workflow run payload {run_json}",
                ),
                started_at=_required_string_field(
                    run_payload,
                    field="run_started_at",
                    context=f"workflow run payload {run_json}",
                ),
                updated_at=_required_string_field(
                    run_payload,
                    field="updated_at",
                    context=f"workflow run payload {run_json}",
                ),
                cachix_name=cachix_name,
                workflow_path=workflow,
            ),
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1


app = make_typer_app(
    help_text="CI workflow helper steps.",
    no_args_is_help=True,
)

workflow_darwin_app = make_typer_app(
    help_text="Darwin workflow steps.",
    no_args_is_help=True,
)
workflow_flake_app = make_typer_app(
    help_text="Flake-related workflow steps.",
    no_args_is_help=True,
)
workflow_flake_input_app = make_typer_app(
    help_text="flake.lock input snapshot/compare workflow steps.",
    no_args_is_help=True,
)
workflow_pr_body_app = make_typer_app(
    help_text="Pull request body generation workflow step.",
    no_args_is_help=False,
)
workflow_update_app = make_typer_app(
    help_text="Update app smoke-check workflow step.",
    no_args_is_help=False,
)
workflow_update_targets_app = make_typer_app(
    help_text="Update target listing workflow step.",
    no_args_is_help=False,
)


app.add_typer(workflow_darwin_app, name="darwin")
app.add_typer(workflow_flake_app, name="flake")
app.add_typer(workflow_flake_input_app, name="flake-input")
app.add_typer(workflow_pr_body_app, name="pr-body")
app.add_typer(workflow_update_app, name="update-app")
app.add_typer(workflow_update_targets_app, name="update-targets")


def _exit_with_code(code: int) -> None:
    raise typer.Exit(code=code)


def command_build_darwin_config(
    host: str = typer.Argument(..., help="Darwin host from matrix, e.g. argus."),
) -> None:
    """Build one darwin configuration by host name."""
    _exit_with_code(_cmd_build_darwin_config(host=host))


def command_eval_darwin_lock_smoke() -> None:
    """Evaluate lock-only-safe Darwin config expressions after a lock refresh."""
    _exit_with_code(_cmd_eval_darwin_lock_smoke())


def command_eval_darwin_full_smoke() -> None:
    """Dry-run full Darwin outputs once generated artifacts are materialized."""
    _exit_with_code(_cmd_eval_darwin_full_smoke())


def command_free_disk_space(
    *,
    force_local: Annotated[
        bool,
        typer.Option(
            "-f",
            "--force-local",
            help="Allow destructive cleanup when not running in CI.",
        ),
    ] = False,
) -> None:
    """Free disk space on Linux or macOS CI runners."""
    _exit_with_code(_cmd_free_disk_space(force_local=force_local))


def command_install_darwin_tools() -> None:
    """Install macOS-specific tools for CI."""
    _exit_with_code(_cmd_install_darwin_tools())


def command_prefetch_flake_inputs() -> None:
    """Pre-fetch flake inputs for CI jobs."""
    _exit_with_code(_cmd_prefetch_flake_inputs())


def command_nix_flake_update() -> None:
    """Run `nix flake update`."""
    _exit_with_code(_cmd_nix_flake_update())


def command_snapshot_flake_input(
    *,
    node: Annotated[
        str,
        typer.Option("-n", "--node", help="flake.lock node name to snapshot."),
    ],
    lock_file: Annotated[
        Path,
        typer.Option("-l", "--lock-file", help="flake.lock file to read."),
    ] = Path("flake.lock"),
    output: Annotated[
        Path,
        typer.Option("-o", "--output", help="Path to write the JSON snapshot."),
    ],
) -> None:
    """Snapshot one flake.lock input's locked payload."""
    _exit_with_code(
        _cmd_snapshot_flake_input(node=node, lock_file=lock_file, output=output)
    )


def command_compare_flake_input(
    *,
    node: Annotated[
        str,
        typer.Option("-n", "--node", help="flake.lock node name to compare."),
    ],
    before: Annotated[
        Path,
        typer.Option("-b", "--before", help="Path to the previous JSON snapshot."),
    ],
    lock_file: Annotated[
        Path,
        typer.Option("-l", "--lock-file", help="flake.lock file to read."),
    ] = Path("flake.lock"),
    github_output: Annotated[
        Path | None,
        typer.Option(
            "-g",
            "--github-output",
            help="Optional GitHub output file to append changed=true/false.",
        ),
    ] = None,
    output_name: Annotated[
        str,
        typer.Option(
            "-O",
            "--output-name",
            help="Output variable name written to --github-output.",
        ),
    ] = "changed",
) -> None:
    """Compare one flake.lock input against a prior JSON snapshot."""
    _exit_with_code(
        _cmd_compare_flake_input(
            node=node,
            before=before,
            lock_file=lock_file,
            github_output=github_output,
            output_name=output_name,
        )
    )


def command_generate_pr_body(
    *,
    base_ref: Annotated[
        str,
        typer.Option("-b", "--base-ref", help="Base branch for compare."),
    ],
    compare_head: Annotated[
        str,
        typer.Option("-c", "--compare-head", help="Head branch used in compare links."),
    ] = "update_flake_lock_action",
    output: Annotated[
        Path,
        typer.Option("-o", "--output", help="Path to write PR body."),
    ],
    repository: Annotated[
        str,
        typer.Option("-r", "--repository", help="owner/repo."),
    ],
    server_url: Annotated[
        str,
        typer.Option("-s", "--server-url", help="GitHub server URL."),
    ],
    workflow_url: Annotated[
        str,
        typer.Option("-w", "--workflow-url", help="Workflow run URL."),
    ],
) -> None:
    """Generate pull request body markdown for update runs."""
    _exit_with_code(
        _cmd_generate_pr_body(
            output=output,
            workflow_url=workflow_url,
            server_url=server_url,
            repository=repository,
            base_ref=base_ref,
            compare_head=compare_head,
        )
    )


def command_render_certification_pr_body(
    *,
    cachix_name: Annotated[
        str,
        typer.Option("--cachix-name", help="Cachix cache name to render in PR body."),
    ],
    existing_body: Annotated[
        Path,
        typer.Option("--existing-body", help="Path to the current PR body."),
    ],
    output: Annotated[
        Path,
        typer.Option("-o", "--output", help="Path to write updated PR body."),
    ],
    run_json: Annotated[
        Path,
        typer.Option("--run-json", help="GitHub Actions run JSON payload from gh api."),
    ],
    workflow: Annotated[
        Path,
        typer.Option(
            "-w",
            "--workflow",
            help="Certification workflow file used to enumerate cached closures.",
        ),
    ] = Path(".github/workflows/update-certify.yml"),
) -> None:
    """Render or replace the certification section in an existing PR body."""
    _exit_with_code(
        _cmd_render_certification_pr_body(
            existing_body=existing_body,
            output=output,
            run_json=run_json,
            cachix_name=cachix_name,
            workflow=workflow,
        )
    )


def command_smoke_check_update_app() -> None:
    """Smoke-check that the update app evaluates."""
    _exit_with_code(_cmd_smoke_check_update_app())


def command_list_update_targets() -> None:
    """List update targets discovered by the update app."""
    _exit_with_code(_cmd_list_update_targets())


def command_verify_artifacts(
    *,
    workflow: Annotated[
        Path,
        typer.Option(
            "-w",
            "--workflow",
            help="Workflow file to validate.",
        ),
    ] = Path(".github/workflows/update.yml"),
) -> None:
    """Validate artifact path contracts in one workflow file."""
    _exit_with_code(_cmd_verify_artifacts(workflow=workflow))


def command_verify_structure(
    *,
    workflow: Annotated[
        Path,
        typer.Option(
            "-w",
            "--workflow",
            help="Workflow file to validate.",
        ),
    ] = Path(".github/workflows/update.yml"),
) -> None:
    """Validate higher-level structure contracts in one workflow file."""
    _exit_with_code(_cmd_verify_structure(workflow=workflow))


def command_validate_bun_lock(
    *,
    lock_file: Annotated[
        Path,
        typer.Option(
            "-l",
            "--lock-file",
            help="Textual bun.lock file to validate.",
        ),
    ] = Path("bun.lock"),
) -> None:
    """Validate exact-version consistency for source-package Bun overrides."""
    _exit_with_code(_cmd_validate_bun_lock(lock_file=lock_file))


def command_prepare_bun_lock(
    *,
    workspace_root: Annotated[
        Path,
        typer.Option(
            "-w",
            "--workspace-root",
            help="Workspace root used when relocking bun.lock.",
        ),
    ],
    lock_file: Annotated[
        Path,
        typer.Option(
            "-l",
            "--lock-file",
            help="Textual bun.lock file to validate or relock.",
        ),
    ] = Path("bun.lock"),
    bun_executable: Annotated[
        str,
        typer.Option(
            "-b",
            "--bun-executable",
            help="Bun executable used for lockfile regeneration.",
        ),
    ] = "bun",
) -> None:
    """Validate a Bun lock and relock once when source overrides disagree."""
    _exit_with_code(
        _cmd_prepare_bun_lock(
            workspace_root=workspace_root,
            lock_file=lock_file,
            bun_executable=bun_executable,
        )
    )


workflow_darwin_app.command("build")(command_build_darwin_config)
workflow_darwin_app.command("eval-lock-smoke")(command_eval_darwin_lock_smoke)
workflow_darwin_app.command("eval-full-smoke")(command_eval_darwin_full_smoke)
workflow_darwin_app.command(
    "eval-smoke",
    help="Backward-compatible alias for `darwin eval-full-smoke`.",
)(command_eval_darwin_full_smoke)
workflow_darwin_app.command("free")(command_free_disk_space)
workflow_darwin_app.command("install")(command_install_darwin_tools)

workflow_flake_app.command("prefetch")(command_prefetch_flake_inputs)
workflow_flake_app.command("update")(command_nix_flake_update)
workflow_flake_input_app.command("snapshot")(command_snapshot_flake_input)
workflow_flake_input_app.command("compare")(command_compare_flake_input)

workflow_pr_body_app.callback(invoke_without_command=True)(command_generate_pr_body)
workflow_update_app.callback(invoke_without_command=True)(
    command_smoke_check_update_app
)
workflow_update_targets_app.callback(invoke_without_command=True)(
    command_list_update_targets
)

app.command("verify-artifacts")(command_verify_artifacts)
app.command("verify-structure")(command_verify_structure)
app.command("validate-bun-lock")(command_validate_bun_lock)
app.command("prepare-bun-lock")(command_prepare_bun_lock)
app.command("build-darwin-config", help="Alias for `darwin build`.")(
    command_build_darwin_config
)
app.command(
    "eval-darwin-lock-smoke",
    help="Alias for `darwin eval-lock-smoke`.",
)(command_eval_darwin_lock_smoke)
app.command(
    "eval-darwin-full-smoke",
    help="Alias for `darwin eval-full-smoke`.",
)(command_eval_darwin_full_smoke)
app.command(
    "eval-darwin-smoke",
    help="Backward-compatible alias for `darwin eval-full-smoke`.",
)(command_eval_darwin_full_smoke)
app.command(
    "free-disk-space",
    help="Legacy alias for CI runner disk cleanup.",
)(command_free_disk_space)
app.command("install-darwin-tools", help="Alias for `darwin install`.")(
    command_install_darwin_tools
)
app.command("prefetch-flake-inputs", help="Alias for `flake prefetch`.")(
    command_prefetch_flake_inputs
)
app.command("nix-flake-update", help="Alias for `flake update`.")(
    command_nix_flake_update
)
app.command("generate-pr-body", help="Alias for `pr-body`.")(command_generate_pr_body)
app.command(
    "render-certification-pr-body",
    help="Render certification details into an existing PR body.",
)(command_render_certification_pr_body)
app.command("smoke-check-update-app", help="Alias for `update-app`.")(
    command_smoke_check_update_app
)
app.command("list-update-targets", help="Alias for `update-targets`.")(
    command_list_update_targets
)


main = make_main(app, prog_name="workflow-steps")


if __name__ == "__main__":
    raise SystemExit(main())
