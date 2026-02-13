"""Unified CLI for CI-oriented update tooling."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from lib.update.ci.build_shared_closure import main as build_shared_closure_main
from lib.update.ci.dedup_cargo_lock import main as dedup_cargo_lock_main
from lib.update.ci.flake_lock_diff import main as flake_lock_diff_main
from lib.update.ci.merge_sources import main as merge_sources_main
from lib.update.ci.resolve_versions import main as resolve_versions_main
from lib.update.ci.sources_json_diff import main as sources_json_diff_main
from lib.update.ci.test_pipeline import main as test_pipeline_main
from lib.update.ci.workflow_steps import main as workflow_steps_main

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


def _workflow_step(step: str, argv: Sequence[str] | None = None) -> int:
    args = list(argv or [])
    return workflow_steps_main([step, *args])


@dataclass(frozen=True)
class CICommand:
    """A CI subcommand with its callable and help text."""

    func: Callable[..., int]
    help: str


CI_COMMANDS: dict[str, CICommand] = {
    "build-shared-closure": CICommand(
        build_shared_closure_main,
        "Build the union of derivations needed by multiple flake outputs.",
    ),
    "dedup-cargo-lock": CICommand(
        dedup_cargo_lock_main,
        "Convert git-sourced gitoxide crates to crates.io and remove duplicates.",
    ),
    "flake-lock-diff": CICommand(
        flake_lock_diff_main,
        "Generate a human-readable diff of flake.lock changes.",
    ),
    "merge-sources": CICommand(
        merge_sources_main,
        "Merge per-package sources.json trees from multiple CI platform artifacts.",
    ),
    "resolve-versions": CICommand(
        resolve_versions_main,
        "Resolve upstream versions for all updaters (version-pinning phase).",
    ),
    "nix-flake-update": CICommand(
        partial(_workflow_step, "nix-flake-update"),
        "Run nix flake update.",
    ),
    "free-disk-space": CICommand(
        partial(_workflow_step, "free-disk-space"),
        "Free disk space on macOS runners.",
    ),
    "install-darwin-tools": CICommand(
        partial(_workflow_step, "install-darwin-tools"),
        "Install macOS-specific tooling for CI builds.",
    ),
    "prefetch-flake-inputs": CICommand(
        partial(_workflow_step, "prefetch-flake-inputs"),
        "Pre-fetch flake inputs for build jobs.",
    ),
    "build-darwin-config": CICommand(
        partial(_workflow_step, "build-darwin-config"),
        "Build one darwin configuration by host name.",
    ),
    "smoke-check-update-app": CICommand(
        partial(_workflow_step, "smoke-check-update-app"),
        "Smoke-check that the update app evaluates.",
    ),
    "list-update-targets": CICommand(
        partial(_workflow_step, "list-update-targets"),
        "List update targets discovered by the update app.",
    ),
    "generate-pr-body": CICommand(
        partial(_workflow_step, "generate-pr-body"),
        "Generate pull request body markdown for update runs.",
    ),
    "sources-diff": CICommand(
        sources_json_diff_main,
        "Generate a diff for source entry JSON changes.",
    ),
    "sources-json-diff": CICommand(
        sources_json_diff_main,
        "Generate a diff for source entry JSON changes (alias).",
    ),
    "test-pipeline": CICommand(
        test_pipeline_main,
        "Simulate the CI update pipeline locally (resolve, compute, merge, validate).",
    ),
}


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch CI utility subcommands."""
    parser = argparse.ArgumentParser(
        prog="nixcfg ci",
        description="Run CI helper tools through a single entrypoint",
    )
    parser.add_argument(
        "command",
        choices=sorted(CI_COMMANDS.keys()),
        help="CI helper command to execute",
    )
    parser.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded to the selected command",
    )
    parsed = parser.parse_args(argv)

    command = CI_COMMANDS[parsed.command]
    return command.func(parsed.args)
