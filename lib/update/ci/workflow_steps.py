"""Python implementations for CI workflow shell steps."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from lib.update.ci.flake_lock_diff import run_diff as run_flake_lock_diff
from lib.update.ci.sources_json_diff import NoChangesMessage
from lib.update.ci.sources_json_diff import run_diff as run_sources_diff

if TYPE_CHECKING:
    from collections.abc import Sequence


def _run(
    args: list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
    stdout: int | None = None,
    stderr: int | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        args,
        check=check,
        capture_output=capture_output,
        text=True,
        stdout=stdout,
        stderr=stderr,
    )


def _cmd_nix_flake_update(_: argparse.Namespace) -> int:
    _run(["nix", "flake", "update"])
    return 0


def _cmd_install_flake_edit(_: argparse.Namespace) -> int:
    _run(["nix", "profile", "install", "nixpkgs#flake-edit"])
    return 0


def _xcode_version_key(app_path: Path) -> tuple[int, ...]:
    stem = app_path.stem.removeprefix("Xcode")
    parts = [
        int(token) for token in stem.replace("-", ".").split(".") if token.isdigit()
    ]
    return tuple(parts)


def _cmd_free_disk_space(_: argparse.Namespace) -> int:
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


def _cmd_install_darwin_tools(_: argparse.Namespace) -> int:
    _run(["brew", "install", "--cask", "macfuse"])
    _run(["brew", "install", "1password-cli"])
    return 0


def _cmd_prefetch_flake_inputs(_: argparse.Namespace) -> int:
    _run(
        ["nix", "flake", "archive", "--json"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return 0


def _cmd_build_darwin_config(args: argparse.Namespace) -> int:
    _run(["nix", "build", "--impure", f".#darwinConfigurations.{args.host}.system"])
    return 0


def _cmd_smoke_check_update_app(_: argparse.Namespace) -> int:
    _run(
        ["nix", "eval", "--raw", ".#apps.x86_64-linux.update.program"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return 0


def _cmd_list_update_targets(_: argparse.Namespace) -> int:
    _run(["nix", "run", ".#update", "--", "--list"])
    return 0


def _git_show(pathspec: str) -> str:
    result = _run(["git", "show", pathspec], check=False, capture_output=True)
    if result.returncode != 0:
        return "{}\n"
    return result.stdout


def _cmd_generate_pr_body(args: argparse.Namespace) -> int:
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        old_lock_path = temp_root / "old-flake.lock"
        old_lock_path.write_text(_git_show("HEAD:flake.lock"), encoding="utf-8")

        flake_diff = run_flake_lock_diff(old_lock_path, Path("flake.lock"))
        compare_url = (
            f"{args.server_url}/{args.repository}/compare/"
            f"{args.base_ref}...{args.compare_head}"
        )
        body_lines = [
            f"**[Workflow run]({args.workflow_url})**",
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

            pkg_diff = run_sources_diff(old_json, new_json, output_format="summary")
            if pkg_diff == NoChangesMessage:
                continue

            if not rendered_sources_diffs:
                body_lines.extend(["", "### Per-package sources.json changes"])
                rendered_sources_diffs = True

            file_diff_url = (
                f"{args.server_url}/{args.repository}/blob/"
                f"{args.compare_head}/{file_path}"
            )
            body_lines.extend([
                "",
                "<details>",
                f'<summary><a href="{file_diff_url}"><code>{file_path}</code></a></summary>',
                "",
                "```text",
                pkg_diff,
                "```",
                "</details>",
            ])

    output_path.write_text("\n".join(body_lines) + "\n", encoding="utf-8")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run CI helper steps that replace shell snippets in workflow files."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="step", required=True)

    step_nix_update = subparsers.add_parser("nix-flake-update")
    step_nix_update.set_defaults(func=_cmd_nix_flake_update)

    step_install_flake_edit = subparsers.add_parser("install-flake-edit")
    step_install_flake_edit.set_defaults(func=_cmd_install_flake_edit)

    step_free_disk = subparsers.add_parser("free-disk-space")
    step_free_disk.set_defaults(func=_cmd_free_disk_space)

    step_install_darwin_tools = subparsers.add_parser("install-darwin-tools")
    step_install_darwin_tools.set_defaults(func=_cmd_install_darwin_tools)

    step_prefetch = subparsers.add_parser("prefetch-flake-inputs")
    step_prefetch.set_defaults(func=_cmd_prefetch_flake_inputs)

    step_build_darwin = subparsers.add_parser("build-darwin-config")
    step_build_darwin.add_argument("host", help="Darwin host from matrix, e.g. argus")
    step_build_darwin.set_defaults(func=_cmd_build_darwin_config)

    step_smoke = subparsers.add_parser("smoke-check-update-app")
    step_smoke.set_defaults(func=_cmd_smoke_check_update_app)

    step_list = subparsers.add_parser("list-update-targets")
    step_list.set_defaults(func=_cmd_list_update_targets)

    step_pr_body = subparsers.add_parser("generate-pr-body")
    step_pr_body.add_argument("--output", required=True, help="Path to write PR body")
    step_pr_body.add_argument("--workflow-url", required=True, help="Workflow run URL")
    step_pr_body.add_argument("--server-url", required=True, help="GitHub server URL")
    step_pr_body.add_argument("--repository", required=True, help="owner/repo")
    step_pr_body.add_argument(
        "--base-ref", required=True, help="Base branch for compare"
    )
    step_pr_body.add_argument(
        "--compare-head",
        default="update_flake_lock_action",
        help="Head branch used in compare links",
    )
    step_pr_body.set_defaults(func=_cmd_generate_pr_body)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
