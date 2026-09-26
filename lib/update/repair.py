"""Bounded agent repair proposals, isolated from updater validation authority."""

import asyncio
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from lib.update.cli_options import RepairAgent
from lib.update.events import UpdateEventKind
from lib.update.persistence import IsolatedUpdateWorkspace
from lib.update.process import RunCommandOptions, run_command
from lib.update.run_monitor import default_run_log_root

if TYPE_CHECKING:
    from lib.update.cli_options import UpdateOptions
    from lib.update.events import UpdateEvent


def repair_paths(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    """Repairs may change packaging and references, never the acceptance gates."""
    rejected = [
        path
        for path in paths
        if path.parts[0] not in {"packages", "overlays"}
        and path not in {Path("flake.nix"), Path("flake.lock")}
    ]
    if rejected:
        msg = f"Repair changed files outside packaging: {', '.join(map(str, rejected))}"
        raise ValueError(msg)
    if not paths:
        msg = "Agent produced no repair"
        raise ValueError(msg)
    return paths


def agent_command(agent: RepairAgent, evidence: Path) -> list[str]:
    """Give the agent evidence and authority to propose one packaging repair."""
    prompt = (
        f"Fix the updater or package failure recorded in {evidence}. "
        "Work in this isolated repository. Read AGENTS.md and inspect the causal "
        "failure before editing. Make the smallest coherent fix in packages/, "
        "overlays/, flake.nix or flake.lock. Preserve platform support, discovery, "
        "validation declarations, upstream pin intent, and generated-file ownership. "
        "Do not bypass validation, weaken tests, add holds, suppress errors, change "
        "CI or the updater framework, commit, push, or contact anyone. Regenerate "
        "outputs through their generators. Add a focused package regression test "
        "when behavior changes. This is one bounded repair attempt; explain an "
        "unfixable infrastructure or credential failure instead of masking it. "
        "The caller will independently rerun quality and native build gates."
    )
    match agent:
        case RepairAgent.CODEX:
            return [
                "codex",
                "exec",
                "--sandbox",
                "workspace-write",
                "--add-dir",
                str(evidence),
                "--ephemeral",
                prompt,
            ]
        case RepairAgent.COPILOT:  # pragma: no branch -- enum exhausted
            return [
                "copilot",
                "--no-ask-user",
                "--allow-all-tools",
                "--deny-tool",
                "shell(git push)",
                "--add-dir",
                str(evidence),
                "--prompt",
                prompt,
            ]


async def repair_in_workspace(
    workspace: IsolatedUpdateWorkspace, evidence: Path, agent: RepairAgent
) -> tuple[Path, ...]:
    """Run one agent with the existing subprocess lifetime and bounded log capture."""
    with (evidence / "repair.log").open("w") as log:

        async def emit(event: UpdateEvent) -> None:
            if event.kind is UpdateEventKind.LINE and event.message:
                log.write(event.message + "\n")
                log.flush()

        result = await run_command(
            agent_command(agent, evidence),
            options=RunCommandOptions(
                source="repair",
                command_timeout=1800,
                output_limit=65536,
            ),
            emit=emit,
        )
        if result.returncode:
            msg = f"Repair agent failed with exit code {result.returncode}; see {evidence}"
            raise RuntimeError(msg)
    paths = repair_paths(workspace.changed_paths())
    workspace.validate_changes(paths)
    return paths


def propose_repair(
    root: Path, *, evidence: Path, output: Path, agent: RepairAgent
) -> None:
    """Export an unvalidated proposal; it must start a fresh update attempt."""
    root, evidence, output = root.resolve(), evidence.resolve(), output.resolve()
    if evidence.is_relative_to(root) or output.is_relative_to(root):
        msg = "Repair evidence and output must be outside the repository"
        raise ValueError(msg)
    if not evidence.is_dir() or not any(evidence.iterdir()):
        msg = "Repair requires failure evidence"
        raise ValueError(msg)
    output.parent.mkdir(parents=True, exist_ok=True)
    with IsolatedUpdateWorkspace(root) as workspace:
        paths = asyncio.run(repair_in_workspace(workspace, evidence, agent))
        output.write_bytes(workspace.patch(paths))


async def _attempt(options: UpdateOptions, root: Path, evidence: Path) -> int:
    """Reload repaired code in a fresh process and a fresh DBOS history."""
    # The executable supplies dependencies; the working directory supplies the
    # repaired Python source. Repairs cannot alter pyproject.toml or uv.lock.
    program = (
        "import json, sys; "
        "from lib.update.cli import run_update_command; "
        "from lib.update.cli_options import UpdateOptions; "
        "sys.exit(run_update_command(UpdateOptions.from_mapping(json.loads(sys.argv[1]))))"
    )
    with (
        (evidence / "result.json").open("w") as result_file,
        (evidence / "stderr.log").open("w") as log,
    ):

        async def emit(event: UpdateEvent) -> None:
            if event.kind is UpdateEventKind.LINE and event.message:
                output = result_file if event.stream == "stdout" else log
                output.write(event.message + "\n")
                output.flush()
                if event.stream != "stdout" and not options.quiet:
                    sys.stderr.write(event.message + "\n")

        result = await run_command(
            [sys.executable, "-c", program, json.dumps(asdict(options))],
            options=RunCommandOptions(
                source="update",
                allow_failure=True,
                command_timeout=86400,
                output_limit=65536,
                env={
                    "REPO_ROOT": str(root),
                    "NIXCFG_UPDATE_EXECUTION_SOURCE": str(root),
                    "UPDATE_RUN_LOG_DIR": str(evidence / "runs"),
                },
            ),
            emit=emit,
        )
    return result.returncode


async def _quality(evidence: Path) -> int:
    """Check repairs and generated outputs with the repository's existing gates."""
    with (evidence / "quality.log").open("w") as log:

        async def emit(event: UpdateEvent) -> None:
            if event.kind is UpdateEventKind.LINE and event.message:
                log.write(event.message + "\n")
                log.flush()

        result = await run_command(
            [
                "nix",
                "develop",
                "--no-write-lock-file",
                "--command",
                "python",
                "lib/update/ci/jobs.py",
                "quality",
            ],
            options=RunCommandOptions(
                source="repair-quality", allow_failure=True, command_timeout=3600
            ),
            emit=emit,
        )
    return result.returncode


def run_repairing_update(options: UpdateOptions, root: Path) -> int:
    """Try once, repair once on failure, and promote only a fully checked result."""
    if (
        options.repair is None
        or options.resume is not None
        or options.run_id is not None
    ):
        msg = "--repair requires a fresh update, without --resume or --run-id"
        raise ValueError(msg)
    evidence = default_run_log_root().parent / "repairs" / uuid4().hex
    evidence.mkdir(parents=True, mode=0o700)
    sys.stderr.write(f"Update attempt and repair evidence: {evidence}\n")
    attempt_options = replace(
        options,
        repair=None,
        check=False,
        strict=True,
        patch=None,
        json=True,
        tty="off",
    )
    patch = b""
    with IsolatedUpdateWorkspace(root) as workspace:
        attempt = evidence / "attempt-1"
        attempt.mkdir()
        status = asyncio.run(_attempt(attempt_options, workspace.root, attempt))
        if status:
            paths = asyncio.run(
                repair_in_workspace(workspace, evidence, options.repair)
            )
            (evidence / "repair.patch").write_bytes(workspace.patch(paths))
            attempt = evidence / "attempt-2"
            attempt.mkdir()
            status = asyncio.run(
                _attempt(
                    replace(attempt_options, validate_all_packages=True),
                    workspace.root,
                    attempt,
                )
            )
            if not status:
                workspace.patch(workspace.changed_paths())
                # Freeze the already built tree before hooks; formatter changes
                # cannot inherit successful build evidence.
                with workspace.validation_snapshot():
                    status = asyncio.run(_quality(evidence))
        if not status:
            paths = workspace.changed_paths()
            patch = workspace.patch(paths)
            if not options.check:
                workspace.promote(paths)
    if options.patch:
        Path(options.patch).expanduser().write_bytes(patch)
    if options.json:
        result = json.loads((attempt / "result.json").read_text())
        result["success"] = status == 0
        result["repair_evidence"] = str(evidence)
        sys.stdout.write(json.dumps(result) + "\n")
    elif not options.quiet:
        sys.stdout.write("Update succeeded\n" if status == 0 else "Update failed\n")
    return status
