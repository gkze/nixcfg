"""Bounded repair behavior at the real isolation and process boundaries."""

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lib.tests._update_workspace_helpers import init_update_workspace_repo
from lib.update import repair
from lib.update.candidate import git
from lib.update.ci import candidate as pipeline
from lib.update.cli_options import RepairAgent, UpdateOptions
from lib.update.events import CommandResult, UpdateEvent, UpdateEventKind
from lib.update.persistence import UpdateWorkspaceError


@pytest.fixture
def repair_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    init_update_workspace_repo(
        root,
        tracked_files={
            ".root": "",
            "packages/example/updater.py": "# broken\n",
            "packages/example/sources.json": "baseline\n",
            "lib/update/validator.py": "# gate\n",
        },
    )
    (root / "unrelated.txt").write_text("keep my work\n")
    return root


@pytest.mark.parametrize("agent", list(RepairAgent))
def test_agent_invocation_is_noninteractive_and_cannot_choose_evidence_command(
    agent: RepairAgent,
) -> None:
    """A path containing shell syntax remains a prompt argument, never a command."""
    evidence = Path("/tmp/evidence $(touch escaped)")
    command = repair.agent_command(agent, evidence)
    assert command[0] == agent.value
    assert command[command.index("--add-dir") + 1] == str(evidence)
    assert "one bounded repair attempt" in command[-1]
    if agent is RepairAgent.CODEX:
        assert command[command.index("--sandbox") + 1] == "workspace-write"
    else:
        assert "--no-ask-user" in command


@pytest.mark.parametrize(
    "changed", ["packages/example/updater.py", "lib/update/validator.py", None]
)
def test_proposal_runs_in_isolation_and_rejects_gate_edits(
    repair_root: Path,
    tmp_path: Path,
    monkeypatch,
    changed: str | None,
) -> None:
    """An actual child process can propose packaging changes, never alter live files."""
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "failure.log").write_text("upstream changed\n")
    output = tmp_path / "repair.patch"
    program = "print('investigated failure')"
    if changed:
        program += (
            f"; from pathlib import Path; Path({changed!r}).write_text('# repaired\\n')"
        )
    monkeypatch.setattr(
        repair,
        "agent_command",
        lambda _agent, _evidence: [sys.executable, "-c", program],
    )
    monkeypatch.setattr(pipeline, "get_repo_root", lambda: repair_root)
    result = CliRunner().invoke(
        pipeline.app,
        [
            "repair",
            "--evidence",
            str(evidence),
            "--output",
            str(output),
        ],
    )
    if changed == "packages/example/updater.py":
        assert result.exit_code == 0, result.exception
        assert b"+# repaired" in output.read_bytes()
    else:
        assert result.exit_code != 0
        assert not output.exists()
    assert (evidence / "repair.log").read_text() == "investigated failure\n"
    assert (repair_root / "packages/example/updater.py").read_text() == "# broken\n"
    assert (repair_root / "lib/update/validator.py").read_text() == "# gate\n"
    assert git(repair_root, "status", "--porcelain").decode() == "?? unrelated.txt\n"


def test_failed_agent_never_exports_a_proposal(
    repair_root, tmp_path, monkeypatch
) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "failure.log").write_text("failure")
    monkeypatch.setattr(
        repair,
        "agent_command",
        lambda _agent, _evidence: [sys.executable, "-c", "raise SystemExit(17)"],
    )
    with pytest.raises(RuntimeError, match="exit code 17"):
        repair.propose_repair(
            repair_root,
            evidence=evidence,
            output=tmp_path / "repair.patch",
            agent=RepairAgent.CODEX,
        )
    assert not (tmp_path / "repair.patch").exists()


@pytest.mark.parametrize(
    "invalid", ["evidence-inside", "output-inside", "empty", "absent"]
)
def test_repair_requires_external_failure_evidence(
    repair_root, tmp_path, invalid
) -> None:
    evidence = repair_root if invalid == "evidence-inside" else tmp_path / "evidence"
    output = (repair_root if invalid == "output-inside" else tmp_path) / "repair.patch"
    if invalid != "absent" and evidence != repair_root:
        evidence.mkdir()
    with pytest.raises(ValueError, match="outside the repository|failure evidence"):
        repair.propose_repair(
            repair_root, evidence=evidence, output=output, agent=RepairAgent.CODEX
        )


@pytest.fixture
def repair_commands(repair_root, tmp_path, monkeypatch) -> dict[str, int | bool]:
    """Nix and the agent are boundaries; Git, snapshots, logs and promotion are real."""
    state = {
        "attempts": 0,
        "fail_first": True,
        "fail_retry": False,
        "quality": 0,
        "drift": False,
        "agent_calls": 0,
    }
    monkeypatch.setattr(repair, "default_run_log_root", lambda: tmp_path / "state/runs")
    monkeypatch.setattr(
        repair.derivation_validation, "validate_root_closures", lambda **_kwargs: ()
    )

    async def command(args, *, options, emit):
        code = 0
        lines = [UpdateEvent(source="test", kind=UpdateEventKind.COMMAND_START)]
        if args[0] == sys.executable:
            state["attempts"] += 1
            opts = json.loads(args[-1])
            assert opts["repair"] is None
            assert opts["run_id"] is None
            assert opts["resume"] is None
            assert opts["strict"]
            assert not opts["check"]
            assert Path(options.env["REPO_ROOT"]) == Path.cwd()
            assert Path.cwd() != repair_root
            code = int(
                state["fail_first"] if state["attempts"] == 1 else state["fail_retry"]
            )
            if not code:
                Path("packages/example/sources.json").write_text("updated\n")
            lines.extend([
                UpdateEvent(
                    source="test",
                    kind=UpdateEventKind.LINE,
                    stream="stdout",
                    message=json.dumps({"success": not code}),
                ),
                UpdateEvent(
                    source="test",
                    kind=UpdateEventKind.LINE,
                    stream="stderr",
                    message="attempt diagnostic",
                ),
            ])
        elif args[0] == "nix":
            code = state["quality"]
            lines.append(
                UpdateEvent(
                    source="test",
                    kind=UpdateEventKind.LINE,
                    message="quality diagnostic",
                )
            )
            if state["drift"]:
                Path("packages/example/updater.py").write_text("# formatter mutation\n")
        else:
            state["agent_calls"] += 1
            Path("packages/example/updater.py").write_text("# repaired\n")
            lines.append(
                UpdateEvent(
                    source="test",
                    kind=UpdateEventKind.LINE,
                    message="repair diagnostic",
                )
            )
        for event in lines:
            await emit(event)
        return CommandResult(
            args=args, returncode=code, stdout="", stderr="", allow_failure=True
        )

    monkeypatch.setattr(repair, "run_command", command)
    return state


@pytest.mark.parametrize(
    "mode",
    ["success", "check", "already-works", "retry-fails", "quality-fails", "quiet"],
)
def test_local_repair_is_bounded_and_promotes_only_checked_results(
    repair_root,
    repair_commands,
    tmp_path,
    capsys,
    mode,
) -> None:
    if mode == "already-works":
        repair_commands["fail_first"] = False
    repair_commands["fail_retry"] = mode == "retry-fails"
    repair_commands["quality"] = int(mode == "quality-fails")
    patch = tmp_path / "update.patch"
    options = UpdateOptions(
        repair=RepairAgent.CODEX,
        check=mode == "check",
        json=mode != "quiet",
        quiet=mode == "quiet",
        patch=str(patch),
    )
    status = repair.run_repairing_update(options, repair_root)
    failed = mode in {"retry-fails", "quality-fails"}
    assert status == int(failed)
    assert repair_commands["attempts"] == (1 if mode == "already-works" else 2)
    assert repair_commands["agent_calls"] == (0 if mode == "already-works" else 1)
    promoted = not failed and mode != "check"
    assert (repair_root / "packages/example/sources.json").read_text() == (
        "updated\n" if promoted else "baseline\n"
    )
    assert (repair_root / "unrelated.txt").read_text() == "keep my work\n"
    assert bool(patch.read_bytes()) is not failed
    captured = capsys.readouterr()
    if mode != "quiet":
        result = json.loads(captured.out)
        assert result["success"] is not failed
        assert Path(result["repair_evidence"]).is_dir()
    else:
        assert captured.out == ""


def test_quality_mutation_invalidates_build_evidence(
    repair_root, repair_commands
) -> None:
    repair_commands["drift"] = True
    with pytest.raises(
        UpdateWorkspaceError, match="changed after root closure validation"
    ):
        repair.run_repairing_update(
            UpdateOptions(repair=RepairAgent.CODEX), repair_root
        )
    assert (repair_root / "packages/example/updater.py").read_text() == "# broken\n"


@pytest.mark.parametrize(
    "options",
    [
        UpdateOptions(),
        UpdateOptions(repair=RepairAgent.CODEX, resume="old"),
        UpdateOptions(repair=RepairAgent.CODEX, run_id="old"),
    ],
)
def test_repair_cannot_reuse_old_execution_history(repair_root, options) -> None:
    with pytest.raises(ValueError, match="requires a fresh update"):
        repair.run_repairing_update(options, repair_root)


@pytest.mark.parametrize("status", [0, 1])
def test_update_cli_dispatches_the_explicit_repair_option(monkeypatch, status) -> None:
    from lib.update import cli

    observed = []
    monkeypatch.setattr(cli, "_maybe_reexec_checkout_update", lambda: None)
    monkeypatch.setattr(cli, "_handle_preflight_requests", lambda *_args: None)

    def run(options, root):
        observed.append((options.repair, root))
        return status

    monkeypatch.setattr(repair, "run_repairing_update", run)
    result = CliRunner().invoke(cli.app, ["--repair", "codex", "example"])
    assert result.exit_code == status, result.output
    assert observed == [(RepairAgent.CODEX, cli.get_repo_root())]


@pytest.mark.parametrize("failed", [False, True])
def test_successful_repair_text_output_and_optional_patch(
    repair_root,
    repair_commands,
    capsys,
    failed,
) -> None:
    repair_commands["fail_retry"] = failed
    assert repair.run_repairing_update(
        UpdateOptions(repair=RepairAgent.CODEX), repair_root
    ) == int(failed)
    assert capsys.readouterr().out == (
        "Update failed\n" if failed else "Update succeeded\n"
    )
