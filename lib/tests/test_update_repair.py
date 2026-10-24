"""Bounded repair behavior at the real isolation and process boundaries."""

import json
import sys
from contextlib import nullcontext
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
            "lib/update/planner.py": "# planner\n",
            "lib/update/validator.py": "# gate\n",
            "lib/tests/test_update_planner.py": "# planner test\n",
        },
    )
    (root / "unrelated.txt").write_text("keep my work\n")
    return root


def _agents_md_repair_rules() -> list[str]:
    """Return the AGENTS.md checkPhase-skip rules as single-line sentences."""
    text = (Path(__file__).resolve().parents[2] / "AGENTS.md").read_text()
    section = text.split("may disable\n`checkPhase` only when", 1)[1]
    section = section.split("See [Update safety]", 1)[0]
    rules = [" ".join(rule.split()) for rule in section.split("\n1. ")[1:]]
    rules[-1], never = rules[-1].split(" Never skip ", 1)
    return [rule.replace("`", "") for rule in [*rules, f"Never skip {never}"]]


def test_repair_agent_prompt_states_the_agents_md_skip_rules_verbatim() -> None:
    """The agent must see the AGENTS.md checkPhase rules unweakened."""
    prompt = repair.repair_agent_prompt(Path("/tmp/evidence")).replace("`", "")
    assert "/tmp/evidence" in prompt
    rules = _agents_md_repair_rules()
    assert len(rules) == 6
    for rule in rules:
        assert rule.removesuffix(".") in prompt


@pytest.mark.parametrize("agent", list(RepairAgent))
def test_agent_invocation_is_noninteractive_and_cannot_choose_evidence_command(
    agent: RepairAgent,
) -> None:
    """A path containing shell syntax remains a prompt argument, never a command."""
    evidence = Path("/tmp/evidence $(touch escaped)")
    command = repair.agent_command(agent, evidence)
    assert command[0] == agent.value
    assert command[command.index("--add-dir") + 1] == str(evidence)
    assert command[-1] == repair.repair_agent_prompt(evidence)
    if agent is RepairAgent.CODEX:
        assert command[command.index("--sandbox") + 1] == "workspace-write"
    else:
        assert "--no-ask-user" in command


@pytest.mark.parametrize(
    "changed",
    [
        "packages/example/updater.py",
        "lib/update/planner.py",
        "lib/tests/test_update_planner.py",
        "lib/update/validator.py",
        None,
    ],
)
def test_proposal_runs_in_isolation_and_rejects_gate_edits(
    repair_root: Path,
    tmp_path: Path,
    monkeypatch,
    changed: str | None,
) -> None:
    """Packaging and planner selection may change; gates and live files may not."""
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
    if changed in {
        "packages/example/updater.py",
        "lib/update/planner.py",
        "lib/tests/test_update_planner.py",
    }:
        assert result.exit_code == 0, result.exception
        assert b"+# repaired" in output.read_bytes()
    else:
        assert result.exit_code != 0
        assert not output.exists()
    assert (evidence / "repair.log").read_text() == "investigated failure\n"
    assert (repair_root / "packages/example/updater.py").read_text() == "# broken\n"
    assert (repair_root / "lib/update/planner.py").read_text() == "# planner\n"
    assert (repair_root / "lib/tests/test_update_planner.py").read_text() == (
        "# planner test\n"
    )
    assert (repair_root / "lib/update/validator.py").read_text() == "# gate\n"
    assert git(repair_root, "status", "--porcelain").decode() == "?? unrelated.txt\n"


_PINNED_SKIP = """{ version }:
{
  # test_socket fails with SIGPIPE on Darwin
  doCheck = version != "1.2.3";
}
"""


def _check_skip_change(root: Path, *, old: bytes | None, new: bytes) -> None:
    path = Path("packages/example/default.nix")
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(new)

    def baseline(requested: Path) -> bytes | None:
        assert requested == path
        return old

    repair.reject_unsafe_skips(root, baseline, (path,))


def test_new_check_skip_requires_a_real_version_comparison(tmp_path: Path) -> None:
    """A version identifier by itself is not a failing-version pin."""
    with pytest.raises(ValueError, match="checkPhase-skip policy"):
        _check_skip_change(
            tmp_path,
            old=None,
            new=b"{ version }: { # test_socket SIGPIPE\n doCheck = version; }\n",
        )


@pytest.mark.parametrize(
    "comparison",
    [
        "version != null",
        "version != other_version",
        'version != ""',
        'version != "1.2.${suffix}"',
    ],
)
def test_new_check_skip_requires_an_explicit_failing_version_value(
    tmp_path: Path, comparison: str
) -> None:
    """Comparisons without a nonempty literal version are not constrained pins."""
    content = (
        "{ version, other_version }: {\n"
        "  # test_socket fails with SIGPIPE\n"
        f"  doCheck = {comparison};\n"
        "}\n"
    ).encode()
    with pytest.raises(ValueError, match="checkPhase-skip policy"):
        _check_skip_change(tmp_path, old=None, new=content)


@pytest.mark.parametrize(
    "comparison",
    [
        'version != "1.2.3"',
        '"1.2.3" != version',
        '(version) != ("1.2.3")',
        'final.version != "1.2.3"',
        'finalAttrs.version != "1.2.3"',
        '!(version == "1.2.3")',
    ],
)
def test_new_check_skip_preserves_literal_version_comparison_forms(
    tmp_path: Path, comparison: str
) -> None:
    """Equivalent exact-version AST forms remain valid pinned skips."""
    content = (
        f"{{\n  # test_socket fails with SIGPIPE\n  doCheck = {comparison};\n}}\n"
    ).encode()
    _check_skip_change(tmp_path, old=None, new=content)


def _check_pinned_binding(tmp_path: Path, binding: str) -> None:
    content = (
        "{ version, final, finalAttrs, lib, prev, pkgs, old }:\n"
        "{\n"
        "  # test_socket fails with SIGPIPE\n"
        f"  {binding}\n"
        "}\n"
    ).encode()
    _check_skip_change(tmp_path, old=None, new=content)


@pytest.mark.parametrize(
    "binding",
    [
        'dontCheck = version == "1.2.3";',
        'dontCheck = "1.2.3" == final.version;',
        'dontCheck = finalAttrs.version == "1.2.3";',
        'dontCheck = !(version != "1.2.3");',
    ],
)
def test_new_dontcheck_preserves_only_failing_version_forms(
    tmp_path: Path, binding: str
) -> None:
    """The dontCheck binding has the opposite safe polarity from doCheck."""
    _check_pinned_binding(tmp_path, binding)


@pytest.mark.parametrize(
    "binding",
    [
        'checkFlags = lib.optionals (version == "1.2.3") [ "--skip=flaky" ];',
        'checkFlags = prev.lib.optionals ("1.2.3" == final.version) [ "--skip=flaky" ];',
        'checkFlags = (prev.lib).optionals (version == "1.2.3") [ "--skip=flaky" ];',
        'checkFlags = pkgs.lib.optionals (finalAttrs.version == "1.2.3") [ "--skip=flaky" ];',
        'checkFlags = lib.optionals (!(version != "1.2.3")) [ "--skip=flaky" ];',
        'checkFlags = if version == "1.2.3" then [ "--skip=flaky" ] else [ ];',
        'checkFlags = if version != "1.2.3" then [ ] else [ "--skip=flaky" ];',
        'checkFlags = (old.checkFlags or [ ]) ++ lib.optionals (version == "1.2.3") [ "--skip=flaky" ];',
    ],
)
def test_new_checkflags_requires_skips_only_in_the_failing_version_branch(
    tmp_path: Path, binding: str
) -> None:
    """Conditional skip flags are valid only behind an exact version gate."""
    _check_pinned_binding(tmp_path, binding)


@pytest.mark.parametrize(
    "binding",
    [
        'doCheck = version == "1.2.3";',
        'doCheck = "1.2.3" == version;',
        'doCheck = !(version != "1.2.3");',
        'dontCheck = version != "1.2.3";',
        'dontCheck = "1.2.3" != final.version;',
        'dontCheck = !(version == "1.2.3");',
        'checkFlags = lib.optionals (version != "1.2.3") [ "--skip=flaky" ];',
        'checkFlags = if version != "1.2.3" then [ "--skip=flaky" ] else [ ];',
        'checkFlags = if version == "1.2.3" then [ ] else [ "--skip=flaky" ];',
    ],
)
def test_new_check_skip_rejects_wrong_polarity(tmp_path: Path, binding: str) -> None:
    """A comparison does not authorize a skip with the opposite semantics."""
    with pytest.raises(ValueError, match="checkPhase-skip policy"):
        _check_pinned_binding(tmp_path, binding)


@pytest.mark.parametrize(
    "binding",
    [
        'doCheck = lib.version != "1.2.3";',
        'doCheck = other.version != "1.2.3";',
        'dontCheck = lib.version == "1.2.3";',
        'checkFlags = lib.optionals (lib.version == "1.2.3") [ "--skip=flaky" ];',
    ],
)
def test_new_check_skip_rejects_unrelated_version_owners(
    tmp_path: Path, binding: str
) -> None:
    """Only the package's version can own the failing-version pin."""
    with pytest.raises(ValueError, match="checkPhase-skip policy"):
        _check_pinned_binding(tmp_path, binding)


@pytest.mark.parametrize(
    "binding",
    [
        "doCheck = false;",
        'doCheck = (version != "1.2.3") && false;',
        'doCheck = if version == "1.2.3" then false else false;',
        'dontCheck = (version == "1.2.3") && true;',
        'checkFlags = [ "--skip=always" ] ++ lib.optionals (version == "1.2.3") [ "--skip=flaky" ];',
        'checkFlags = lib.optionals (version == "1.2.3") [ "--skip=flaky" ] ++ [ "--skip=always" ];',
        'checkFlags = evil.optionals (version == "1.2.3") [ "--skip=flaky" ];',
        'checkFlags = optionals (version == "1.2.3") [ "--skip=flaky" ];',
        'checkFlags = lib.optional (version == "1.2.3") "--skip=flaky";',
        'checkFlags = if other then [ "--skip=flaky" ] else [ ];',
    ],
)
def test_new_check_skip_rejects_unpinned_effects_in_the_whole_binding(
    tmp_path: Path, binding: str
) -> None:
    """Nested comparisons cannot hide constant or unconditional skip behavior."""
    with pytest.raises(ValueError, match="checkPhase-skip policy"):
        _check_pinned_binding(tmp_path, binding)


def test_relocated_check_skip_is_revalidated_against_its_new_owner(
    tmp_path: Path,
) -> None:
    """An unchanged baseline skip cannot authorize the same text at a new owner."""
    old = b"{ original = { doCheck = false; }; }\n"
    _check_skip_change(tmp_path, old=old, new=old)
    with pytest.raises(ValueError, match="checkPhase-skip policy"):
        _check_skip_change(
            tmp_path,
            old=old,
            new=b"{ relocated = { doCheck = false; }; }\n",
        )


def test_duplicate_check_skip_is_a_new_occurrence(tmp_path: Path) -> None:
    """One baseline occurrence cannot authorize a duplicate at the same path."""
    old = b"{ packages = [ { doCheck = false; } ]; }\n"
    new = b"{ packages = [ { doCheck = false; } { doCheck = false; } ]; }\n"
    with pytest.raises(ValueError, match="checkPhase-skip policy"):
        _check_skip_change(tmp_path, old=old, new=new)


@pytest.mark.parametrize(
    ("changes", "accepted"),
    [
        ({"packages/example/default.nix": _PINNED_SKIP}, True),
        ({"packages/example/default.nix": "{ doCheck = true; }\n"}, True),
        ({"packages/example/skip.nix": None}, True),
        ({"packages/example/skip.nix": "# c\n{ doCheck = false; pname = 1; }\n"}, True),
        ({"packages/example/default.nix": "{ doCheck = false; }\n"}, False),
        (
            {"packages/example/default.nix": '{ dontCheck = version == "1"; }\n'},
            False,
        ),
        (
            {
                "packages/example/default.nix": '{\n  # tests\n  checkPhase = "make";\n}\n'
            },
            True,
        ),
        (
            {"packages/example/default.nix": '{ checkFlags = [ "--skip=flaky" ]; }\n'},
            False,
        ),
        ({"overlays/example/default.nix": _PINNED_SKIP}, True),
        (
            {
                "packages/example/updater.py": (
                    "class Updater:\n    bulk_update_hold = 'flaky upstream'\n"
                )
            },
            False,
        ),
        (
            {
                "packages/example/updater.py": (
                    "class Updater:\n    bulk_update_hold: str | None = None\n"
                )
            },
            True,
        ),
    ],
)
def test_proposal_enforces_the_checkphase_skip_policy_on_the_diff(
    repair_root: Path,
    tmp_path: Path,
    monkeypatch,
    changes: dict[str, str | None],
    accepted: bool,
) -> None:
    """Skips must be version-pinned and explained; repairs never add holds."""
    (repair_root / "packages/example/skip.nix").write_text(
        "# c\n{ doCheck = false; }\n"
    )
    git(repair_root, "add", ".")
    git(
        repair_root,
        *("-c", "user.name=T", "-c", "user.email=t@example.invalid"),
        *("-c", "commit.gpgSign=false", "commit", "-qm", "skip"),
    )
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "failure.log").write_text("checkPhase failed\n")
    program = (
        "import json, sys; from pathlib import Path\n"
        "for name, text in json.loads(sys.argv[1]).items():\n"
        "    path = Path(name)\n"
        "    path.parent.mkdir(parents=True, exist_ok=True)\n"
        "    path.unlink() if text is None else path.write_text(text)\n"
    )
    monkeypatch.setattr(
        repair,
        "agent_command",
        lambda _agent, _evidence: [
            sys.executable,
            "-c",
            program,
            json.dumps(changes),
        ],
    )
    output = tmp_path / "repair.patch"
    rejected = pytest.raises(ValueError, match="checkPhase-skip policy")
    with nullcontext() if accepted else rejected:
        repair.propose_repair(
            repair_root, evidence=evidence, output=output, agent=RepairAgent.CODEX
        )
    assert output.exists() == accepted


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
        "incomplete_retry": False,
        "quality": 0,
        "quality_calls": 0,
        "drift": False,
        "agent_calls": 0,
    }
    monkeypatch.setattr(repair, "default_run_log_root", lambda: tmp_path / "state/runs")

    def unexpected_roots(**_kwargs):
        pytest.fail("The retry owns root validation; the supervisor must not repeat it")

    monkeypatch.setattr(
        "lib.update.derivation_validation.validate_root_closures", unexpected_roots
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
            assert opts["validate_all_packages"] == (state["attempts"] == 2)
            assert not opts["check"]
            assert Path(options.env["REPO_ROOT"]) == Path.cwd()
            assert Path.cwd() != repair_root
            code = int(
                state["fail_first"] if state["attempts"] == 1 else state["fail_retry"]
            )
            if not code:
                Path("packages/example/sources.json").write_text("updated\n")
            payload = {"success": not code}
            if state["incomplete_retry"] and state["attempts"] == 2:
                payload["validationIncomplete"] = "Validation incomplete: deadline"
            lines.extend([
                UpdateEvent(
                    source="test",
                    kind=UpdateEventKind.LINE,
                    stream="stdout",
                    message=json.dumps(payload),
                ),
                UpdateEvent(
                    source="test",
                    kind=UpdateEventKind.LINE,
                    stream="stderr",
                    message="attempt diagnostic",
                ),
            ])
        elif args[0] == "nix":
            state["quality_calls"] += 1
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


def test_incomplete_retry_preserves_json_evidence_without_quality_or_promotion(
    repair_root, repair_commands, tmp_path, capsys
) -> None:
    """A failed child result survives the bounded supervisor as valid JSON."""
    repair_commands["fail_retry"] = True
    repair_commands["incomplete_retry"] = True
    patch = tmp_path / "update.patch"
    assert (
        repair.run_repairing_update(
            UpdateOptions(repair=RepairAgent.CODEX, json=True, patch=str(patch)),
            repair_root,
        )
        == 1
    )
    assert repair_commands["attempts"] == 2
    assert repair_commands["agent_calls"] == 1
    assert repair_commands["quality_calls"] == 0
    assert patch.read_bytes() == b""
    assert (repair_root / "packages/example/updater.py").read_text() == "# broken\n"
    result = json.loads(capsys.readouterr().out)
    assert result["success"] is False
    assert result["validationIncomplete"] == "Validation incomplete: deadline"
    retained = json.loads(
        (Path(result["repair_evidence"]) / "attempt-2" / "result.json").read_text()
    )
    assert retained["validationIncomplete"] == result["validationIncomplete"]


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


@pytest.mark.parametrize("mode", ["repair", "retry"])
def test_metadata_only_validation_cannot_claim_repair_acceptance(mode) -> None:
    """Reject the preflight-only options path before any acceptance can run."""
    with pytest.raises(ValueError, match="Metadata-only --validate"):
        UpdateOptions(
            validate=True,
            repair=RepairAgent.CODEX if mode == "repair" else None,
            validate_all_packages=mode == "retry",
        )


@pytest.mark.parametrize("status", [0, 1])
def test_update_cli_dispatches_the_explicit_repair_option(monkeypatch, status) -> None:
    from lib.update import cli

    observed = []
    monkeypatch.setattr(
        "lib.update.runtime_source.maybe_reexec_checkout_update", lambda: None
    )
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


@pytest.mark.parametrize(
    ("path", "content", "error"),
    [
        ("packages/demo/default.nix", "{ version = 1; }\n", None),
        ("overlays/demo/default.nix", "{ doCheck = false; }\n", "not pinned"),
        ("lib/update/ci/jobs.py", "x = 1\n", "outside packaging"),
    ],
)
def test_check_applied_repair_rechecks_the_staged_patch(
    tmp_path: Path, path: str, content: str, error: str | None
) -> None:
    """The publishing job re-runs the path and skip policy on what it commits."""
    git(tmp_path, "init", "-q")
    git(tmp_path, "commit", "-q", "--allow-empty", "-m", "base")
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_text(content)
    git(tmp_path, "add", path)
    if error is None:
        repair.check_applied_repair(tmp_path)
    else:
        with pytest.raises(ValueError, match=error):
            repair.check_applied_repair(tmp_path)


def test_check_repair_command_checks_the_repository(monkeypatch) -> None:
    """``ci update check-repair`` runs the re-check on the repository root."""
    checked: list[Path] = []
    monkeypatch.setattr(pipeline, "check_applied_repair", checked.append)
    monkeypatch.setattr(pipeline, "get_repo_root", lambda: Path("/repo"))
    result = CliRunner().invoke(pipeline.app, ["check-repair"])
    assert result.exit_code == 0, result.output
    assert checked == [Path("/repo")]
