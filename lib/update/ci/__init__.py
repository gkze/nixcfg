"""Unified CLI for CI-oriented update tooling."""

from __future__ import annotations

import argparse
from functools import partial
from typing import TYPE_CHECKING

from lib.update.ci.build_shared_closure import main as build_shared_closure_main
from lib.update.ci.dedup_cargo_lock import main as dedup_cargo_lock_main
from lib.update.ci.flake_lock_diff import main as flake_lock_diff_main
from lib.update.ci.merge_sources import main as merge_sources_main
from lib.update.ci.sources_json_diff import main as sources_json_diff_main
from lib.update.ci.workflow_steps import main as workflow_steps_main

if TYPE_CHECKING:
    from collections.abc import Sequence


def _workflow_step(step: str, argv: Sequence[str] | None = None) -> int:
    args = list(argv or [])
    return workflow_steps_main([step, *args])


_CI_COMMANDS = {
    "build-shared-closure": build_shared_closure_main,
    "dedup-cargo-lock": dedup_cargo_lock_main,
    "flake-lock-diff": flake_lock_diff_main,
    "merge-sources": merge_sources_main,
    "nix-flake-update": partial(_workflow_step, "nix-flake-update"),
    "install-flake-edit": partial(_workflow_step, "install-flake-edit"),
    "free-disk-space": partial(_workflow_step, "free-disk-space"),
    "install-darwin-tools": partial(_workflow_step, "install-darwin-tools"),
    "prefetch-flake-inputs": partial(_workflow_step, "prefetch-flake-inputs"),
    "build-darwin-config": partial(_workflow_step, "build-darwin-config"),
    "smoke-check-update-app": partial(_workflow_step, "smoke-check-update-app"),
    "list-update-targets": partial(_workflow_step, "list-update-targets"),
    "generate-pr-body": partial(_workflow_step, "generate-pr-body"),
    "sources-diff": sources_json_diff_main,
    "sources-json-diff": sources_json_diff_main,
}

CI_COMMANDS: frozenset[str] = frozenset(_CI_COMMANDS)


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch CI utility subcommands."""
    parser = argparse.ArgumentParser(
        prog="update.py ci",
        description="Run CI helper tools through a single entrypoint",
    )
    parser.add_argument(
        "command",
        choices=sorted(_CI_COMMANDS.keys()),
        help="CI helper command to execute",
    )
    parser.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded to the selected command",
    )
    parsed = parser.parse_args(argv)

    command = _CI_COMMANDS[parsed.command]
    return command(parsed.args)
