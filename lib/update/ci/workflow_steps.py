"""Python implementations for CI workflow shell steps."""

from __future__ import annotations

import asyncio
import dataclasses
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from lib.update.ci._cli import make_typer_app, run_main
from lib.update.ci._subprocess import run_command as _run
from lib.update.ci.flake_lock_diff import run_diff as run_flake_lock_diff
from lib.update.ci.sources_json_diff import NoChangesMessage
from lib.update.ci.sources_json_diff import run_diff as run_sources_diff
from lib.update.cli import UpdateOptions, run_updates

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclasses.dataclass(frozen=True)
class PRBodyOptions:
    """Inputs used to render compare and workflow links in PR body output."""

    workflow_url: str
    server_url: str
    repository: str
    base_ref: str
    compare_head: str = "update_flake_lock_action"


def _cmd_nix_flake_update() -> int:
    _run(["nix", "flake", "update"])
    return 0


def _xcode_version_key(app_path: Path) -> tuple[int, ...]:
    stem = app_path.stem.removeprefix("Xcode")
    parts = [
        int(token) for token in stem.replace("-", ".").split(".") if token.isdigit()
    ]
    return tuple(parts)


def _cmd_free_disk_space(*, force_local: bool = False) -> int:
    running_in_ci = os.environ.get("CI", "").lower() in {"1", "true", "yes"}
    if not running_in_ci and not force_local:
        sys.stderr.write(
            "Refusing to run free-disk-space outside CI. "
            "Re-run with --force-local to override.\n"
        )
        return 2

    sys.stdout.write("=== Before cleanup ===\n")
    _run(["df", "-h", "/"])

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

    sys.stdout.write("=== After cleanup ===\n")
    _run(["df", "-h", "/"])
    return 0


def _cmd_install_darwin_tools() -> int:
    _run(["brew", "install", "--cask", "macfuse"])
    _run(["brew", "install", "1password-cli"])
    return 0


def _cmd_prefetch_flake_inputs() -> int:
    _run(
        ["nix", "flake", "archive", "--json"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return 0


def _cmd_build_darwin_config(*, host: str) -> int:
    _run(["nix", "build", "--impure", f".#darwinConfigurations.{host}.system"])
    return 0


def _cmd_smoke_check_update_app() -> int:
    _run(
        ["nix", "eval", "--raw", ".#apps.x86_64-linux.nixcfg.program"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return 0


def _cmd_list_update_targets() -> int:
    return asyncio.run(run_updates(UpdateOptions(list_targets=True)))


def _git_show(pathspec: str) -> str:
    result = _run(["git", "show", pathspec], check=False, capture_output=True)
    if result.returncode != 0:
        return "{}\n"
    return result.stdout


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
        old_lock_path.write_text(_git_show("HEAD:flake.lock"), encoding="utf-8")

        flake_diff = run_flake_lock_diff(old_lock_path, Path("flake.lock"))
        compare_url = (
            f"{options.server_url}/{options.repository}/compare/"
            f"{options.base_ref}...{options.compare_head}"
        )
        body_lines = [
            f"**[Workflow run]({options.workflow_url})**",
            "",
            f"**[Compare]({compare_url})**",
            "",
        ]
        if flake_diff:
            body_lines.append(flake_diff)
        else:
            body_lines.append("No flake.lock input changes detected.")

        source_files_result = _run(
            [
                "git",
                "diff",
                "--name-only",
                "HEAD",
                "--",
                ":(glob)packages/**/sources.json",
                ":(glob)overlays/**/sources.json",
            ],
            capture_output=True,
        )
        source_files = [
            line.strip()
            for line in source_files_result.stdout.splitlines()
            if line.strip()
        ]

        untracked_result = _run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True,
        )
        untracked_sources = [
            line.strip()
            for line in untracked_result.stdout.splitlines()
            if line.strip()
            and line.strip().endswith("/sources.json")
            and (
                line.strip().startswith("packages/")
                or line.strip().startswith("overlays/")
            )
        ]
        source_files = sorted({*source_files, *untracked_sources})

        rendered_sources_diffs = False
        for file_path in source_files:
            old_json = temp_root / "old-sources.json"
            new_json = temp_root / "new-sources.json"
            old_json.write_text(_git_show(f"HEAD:{file_path}"), encoding="utf-8")

            current_path = Path(file_path)
            if current_path.is_file():
                new_json.write_text(
                    current_path.read_text(encoding="utf-8"), encoding="utf-8"
                )
            else:
                new_json.write_text("{}\n", encoding="utf-8")

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


app = make_typer_app(
    help_text="CI workflow helper steps.",
    no_args_is_help=True,
)


@app.command("nix-flake-update")
def command_nix_flake_update() -> None:
    """Run `nix flake update`."""
    raise typer.Exit(code=_cmd_nix_flake_update())


@app.command("free-disk-space")
def command_free_disk_space(
    *,
    force_local: Annotated[
        bool,
        typer.Option(
            "--force-local",
            help="Allow destructive cleanup when not running in CI.",
        ),
    ] = False,
) -> None:
    """Free disk space on macOS CI runners."""
    raise typer.Exit(code=_cmd_free_disk_space(force_local=force_local))


@app.command("install-darwin-tools")
def command_install_darwin_tools() -> None:
    """Install macOS-specific tools for CI."""
    raise typer.Exit(code=_cmd_install_darwin_tools())


@app.command("prefetch-flake-inputs")
def command_prefetch_flake_inputs() -> None:
    """Pre-fetch flake inputs for CI jobs."""
    raise typer.Exit(code=_cmd_prefetch_flake_inputs())


@app.command("build-darwin-config")
def command_build_darwin_config(
    host: str = typer.Argument(..., help="Darwin host from matrix, e.g. argus."),
) -> None:
    """Build one darwin configuration by host name."""
    raise typer.Exit(code=_cmd_build_darwin_config(host=host))


@app.command("smoke-check-update-app")
def command_smoke_check_update_app() -> None:
    """Smoke-check that the update app evaluates."""
    raise typer.Exit(code=_cmd_smoke_check_update_app())


@app.command("list-update-targets")
def command_list_update_targets() -> None:
    """List update targets discovered by the update app."""
    raise typer.Exit(code=_cmd_list_update_targets())


@app.command("generate-pr-body")
def command_generate_pr_body(
    *,
    output: Annotated[
        Path,
        typer.Option("--output", help="Path to write PR body."),
    ],
    workflow_url: Annotated[
        str,
        typer.Option("--workflow-url", help="Workflow run URL."),
    ],
    server_url: Annotated[
        str,
        typer.Option("--server-url", help="GitHub server URL."),
    ],
    repository: Annotated[
        str,
        typer.Option("--repository", help="owner/repo."),
    ],
    base_ref: Annotated[
        str,
        typer.Option("--base-ref", help="Base branch for compare."),
    ],
    compare_head: Annotated[
        str,
        typer.Option("--compare-head", help="Head branch used in compare links."),
    ] = "update_flake_lock_action",
) -> None:
    """Generate pull request body markdown for update runs."""
    raise typer.Exit(
        code=_cmd_generate_pr_body(
            output=output,
            workflow_url=workflow_url,
            server_url=server_url,
            repository=repository,
            base_ref=base_ref,
            compare_head=compare_head,
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run CI helper steps that replace shell snippets in workflow files."""
    return run_main(app, argv=argv, prog_name="workflow-steps")


if __name__ == "__main__":
    raise SystemExit(main())
