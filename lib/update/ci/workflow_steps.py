"""Python implementations for CI workflow shell steps."""

from __future__ import annotations

import importlib
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

import lib.update.ci.workflow_certification as _workflow_certification
import lib.update.ci.workflow_core as _workflow_core
import lib.update.ci.workflow_pr_body as _workflow_pr_body
from lib.update.bun_lock import (
    prepare_source_package_lock,
    validate_source_package_exact_versions,
)
from lib.update.ci._cli import make_main, make_typer_app
from lib.update.ci._subprocess import run_command as _run
from lib.update.ci.flake_lock_diff import collect_changes
from lib.update.ci.pr_body import (
    PRBodyModel as _PRBodyModel,
)
from lib.update.ci.pr_body import (
    extract_pr_body_model,
    write_pr_body,
)
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
    get_repo_root,
    is_sources_file_path,
)

if TYPE_CHECKING:
    from collections.abc import Callable

type _CommandDeps = dict[str, object]

PRBodyOptions = _workflow_pr_body.PRBodyOptions
GitHistoryReadError = _workflow_pr_body.GitHistoryReadError
CertificationPRBodyOptions = _workflow_certification.CertificationPRBodyOptions
PRBodyModel = _PRBodyModel
_xcode_version_key = _workflow_core.xcode_version_key
_json_object = _workflow_core.json_object
_load_flake_lock_input_locked = _workflow_core.load_flake_lock_input_locked
_load_json_snapshot = _workflow_core.load_json_snapshot
_load_json_file = _workflow_certification.load_json_file
_required_string_field = _workflow_certification.required_string_field

_LINUX_CLEANUP_PATHS = _workflow_core.LINUX_CLEANUP_PATHS
_PREFETCH_FLAKE_INPUTS_ARGS = _workflow_core.PREFETCH_FLAKE_INPUTS_ARGS
_PREFETCH_FLAKE_INPUTS_ATTEMPTS = _workflow_core.PREFETCH_FLAKE_INPUTS_ATTEMPTS
_PREFETCH_FLAKE_INPUTS_RETRY_DELAYS = _workflow_core.PREFETCH_FLAKE_INPUTS_RETRY_DELAYS
_DARWIN_LOCK_SMOKE_EXPRS = _workflow_core.DARWIN_LOCK_SMOKE_EXPRS
_DARWIN_FULL_SMOKE_REFS = _workflow_core.DARWIN_FULL_SMOKE_REFS


def _stdout_stderr_deps() -> _CommandDeps:
    return {"stdout": sys.stdout, "stderr": sys.stderr}


def _run_deps() -> _CommandDeps:
    return {"run": _run}


def _run_stdio_deps() -> _CommandDeps:
    return {**_run_deps(), **_stdout_stderr_deps()}


def _stderr_deps() -> _CommandDeps:
    return {"stderr": sys.stderr}


def _bind_core_command(
    func: Callable[..., int],
    *dep_factories: Callable[[], _CommandDeps],
    **bound_kwargs: object,
) -> Callable[..., int]:
    def _command(**call_kwargs: object) -> int:
        kwargs = dict(bound_kwargs)
        for factory in dep_factories:
            kwargs.update(factory())
        kwargs.update(call_kwargs)
        return func(**kwargs)

    return _command


_cmd_free_disk_space = _bind_core_command(
    _workflow_core.cmd_free_disk_space,
    _run_stdio_deps,
    lambda: {
        "platform": sys.platform,
        "env": os.environ,
        "linux_cleanup_paths": _LINUX_CLEANUP_PATHS,
    },
)
_cmd_install_darwin_tools = _bind_core_command(
    _workflow_core.cmd_install_darwin_tools,
    _run_deps,
)
_cmd_prefetch_flake_inputs = _bind_core_command(
    _workflow_core.cmd_prefetch_flake_inputs,
    _run_deps,
    _stderr_deps,
    lambda: {
        "sleep": time.sleep,
        "attempts": _PREFETCH_FLAKE_INPUTS_ATTEMPTS,
        "retry_delays": _PREFETCH_FLAKE_INPUTS_RETRY_DELAYS,
        "args": _PREFETCH_FLAKE_INPUTS_ARGS,
    },
)
_cmd_nix_flake_update = _bind_core_command(
    _workflow_core.cmd_nix_flake_update,
    _run_deps,
)
_cmd_build_darwin_config = _bind_core_command(
    _workflow_core.cmd_build_darwin_config,
    _run_deps,
)
_cmd_eval_darwin_lock_smoke = _bind_core_command(
    _workflow_core.cmd_eval_darwin_lock_smoke,
    _run_deps,
    lambda: {"exprs": _DARWIN_LOCK_SMOKE_EXPRS},
)
_cmd_eval_darwin_full_smoke = _bind_core_command(
    _workflow_core.cmd_eval_darwin_full_smoke,
    _run_deps,
    lambda: {"refs": _DARWIN_FULL_SMOKE_REFS},
)
_cmd_smoke_check_update_app = _bind_core_command(
    _workflow_core.cmd_smoke_check_update_app,
    _run_deps,
)
_cmd_list_update_targets = _bind_core_command(
    _workflow_core.cmd_list_update_targets,
    lambda: {"import_module": importlib.import_module},
)


def _cmd_verify_workflow_contracts(
    *,
    workflow: Path,
    validator: Callable[..., None],
    description: str,
) -> int:
    return _workflow_core.cmd_verify_workflow_contracts(
        workflow=workflow,
        validator=validator,
        description=description,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


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


_cmd_validate_bun_lock = _bind_core_command(
    _workflow_core.cmd_validate_bun_lock,
    _stdout_stderr_deps,
    lambda: {"validate": validate_source_package_exact_versions},
)
_cmd_prepare_bun_lock = _bind_core_command(
    _workflow_core.cmd_prepare_bun_lock,
    _stdout_stderr_deps,
    lambda: {"prepare": prepare_source_package_lock},
)
_cmd_snapshot_flake_input = _bind_core_command(
    _workflow_core.cmd_snapshot_flake_input,
    _stdout_stderr_deps,
)
_cmd_compare_flake_input = _bind_core_command(
    _workflow_core.cmd_compare_flake_input,
    _stdout_stderr_deps,
)


def _git_show(pathspec: str, *, missing_ok: bool = True) -> str:
    return _workflow_pr_body.git_show(
        pathspec,
        cwd=get_repo_root(),
        run=_run,
        missing_ok=missing_ok,
    )


def _source_diff_pathspecs() -> tuple[str, ...]:
    return _workflow_pr_body.source_diff_pathspecs(
        package_dirs=PACKAGE_DIRS,
        repo_root=get_repo_root(),
        sources_git_pathspecs=SOURCES_GIT_PATHSPECS,
        sources_file_name=SOURCES_FILE_NAME,
    )


def _collect_changed_source_files() -> list[str]:
    return _workflow_pr_body.collect_changed_source_files(
        cwd=get_repo_root(),
        run=_run,
        pathspecs=_source_diff_pathspecs(),
        is_sources_file_path=is_sources_file_path,
    )


def generate_pr_body(
    *,
    output: str | Path,
    options: PRBodyOptions,
) -> int:
    """Generate pull-request body markdown for update runs."""
    return _workflow_pr_body.generate_pr_body(
        output=output,
        options=options,
        repo_root=get_repo_root(),
        write_pr_body=write_pr_body,
        collect_changes=collect_changes,
        collect_changed_source_files=_collect_changed_source_files,
        run_sources_diff=run_sources_diff,
        no_changes_message=NoChangesMessage,
        git_show=_git_show,
    )


def _cmd_generate_pr_body(
    *,
    output: str | Path,
    workflow_url: str,
    server_url: str,
    repository: str,
    base_ref: str,
    compare_head: str,
) -> int:
    try:
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
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1


def render_certification_pr_body(
    *,
    existing_body: str | Path,
    output: str | Path,
    options: CertificationPRBodyOptions,
) -> int:
    """Update the serialized PR body model with certification metadata."""
    return _workflow_certification.render_certification_pr_body(
        existing_body=existing_body,
        output=output,
        options=options,
        extract_pr_body_model=extract_pr_body_model,
        write_pr_body=write_pr_body,
    )


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
    """Update flake inputs except operational pins."""
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
        typer.Option(
            "-c",
            "--cachix-name",
            help="Cachix cache name to render in PR body.",
        ),
    ],
    existing_body: Annotated[
        Path,
        typer.Option("-b", "--existing-body", help="Path to the current PR body."),
    ],
    output: Annotated[
        Path,
        typer.Option("-o", "--output", help="Path to write updated PR body."),
    ],
    run_json: Annotated[
        Path,
        typer.Option(
            "-j", "--run-json", help="GitHub Actions run JSON payload from gh api."
        ),
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

for _name, _command, _help in (
    ("verify-artifacts", command_verify_artifacts, None),
    ("verify-structure", command_verify_structure, None),
    ("validate-bun-lock", command_validate_bun_lock, None),
    ("prepare-bun-lock", command_prepare_bun_lock, None),
    ("build-darwin-config", command_build_darwin_config, "Alias for `darwin build`."),
    (
        "eval-darwin-lock-smoke",
        command_eval_darwin_lock_smoke,
        "Alias for `darwin eval-lock-smoke`.",
    ),
    (
        "eval-darwin-full-smoke",
        command_eval_darwin_full_smoke,
        "Alias for `darwin eval-full-smoke`.",
    ),
    (
        "eval-darwin-smoke",
        command_eval_darwin_full_smoke,
        "Backward-compatible alias for `darwin eval-full-smoke`.",
    ),
    (
        "free-disk-space",
        command_free_disk_space,
        "Legacy alias for CI runner disk cleanup.",
    ),
    (
        "install-darwin-tools",
        command_install_darwin_tools,
        "Alias for `darwin install`.",
    ),
    (
        "prefetch-flake-inputs",
        command_prefetch_flake_inputs,
        "Alias for `flake prefetch`.",
    ),
    (
        "nix-flake-update",
        command_nix_flake_update,
        "Alias for pinned-aware flake input updates.",
    ),
    ("generate-pr-body", command_generate_pr_body, "Alias for `pr-body`."),
    (
        "render-certification-pr-body",
        command_render_certification_pr_body,
        "Render certification details into an existing PR body.",
    ),
    (
        "smoke-check-update-app",
        command_smoke_check_update_app,
        "Alias for `update-app`.",
    ),
    ("list-update-targets", command_list_update_targets, "Alias for `update-targets`."),
):
    app.command(_name, help=_help)(_command)


main = make_main(app, prog_name="workflow-steps")


if __name__ == "__main__":
    raise SystemExit(main())
