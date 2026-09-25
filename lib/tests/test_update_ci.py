"""Exercise Python Actions jobs with real processes and Git boundaries."""

import json
import os
import subprocess
import sys
from itertools import pairwise
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from lib.system_policy import supported_systems
from lib.tests._update_workspace_helpers import init_update_workspace_repo
from lib.update.candidate import git
from lib.update.ci import jobs
from lib.update.ci.candidate import app

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "lib/update/ci/jobs.py"


@pytest.fixture
def native_job(tmp_path: Path) -> tuple[dict[str, str], Path]:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "flake.lock").write_text("baseline")
    tools = tmp_path / "tools"
    tools.mkdir()
    boundary = tools / "boundary"
    boundary.write_text(
        f"#!{sys.executable}\n"
        "import json, os, subprocess, sys\n"
        "from pathlib import Path\n"
        "name = Path(sys.argv[0]).name\n"
        "args = sys.argv[1:]\n"
        "if name == 'nix':\n"
        "    print('devshell startup', flush=True)\n"
        "    sys.exit(subprocess.call(args[args.index('--command') + 1:]))\n"
        "Path(os.environ['TEST_LOG']).write_text(json.dumps(args))\n"
        "Path(args[args.index('--output') + 1]).write_text('candidate or evidence')\n"
        "print(json.dumps({'success': int(os.environ.get('TEST_EXIT', '0')) == 0}))\n"
        "print('diagnostic evidence', file=sys.stderr)\n"
        "sys.exit(int(os.environ.get('TEST_EXIT', '0')))\n"
    )
    boundary.chmod(0o755)
    (tools / "nix").symlink_to(boundary)
    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin/nixcfg").symlink_to(boundary)
    env = os.environ | {
        "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
        "TEST_LOG": str(tmp_path / "command.json"),
        "RUNNER_TEMP": str(tmp_path / "runner temp"),
        "NIXCFG_RUNTIME": str(runtime),
        "NIXCFG_DEVSHELL": str(tmp_path / "devshell"),
        "NIXCFG_CI_STAGE": "prepare",
    }
    return env, checkout


def invoke(env: dict[str, str], checkout: Path) -> subprocess.CompletedProcess[str]:
    """Run the same Python entrypoint used by hosted Actions."""
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT)],
        cwd=checkout,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("exit_code", [0, 17])
@pytest.mark.parametrize("stage", ["prepare", "validate"])
def test_native_job_keeps_evidence_and_propagates_failure(
    native_job, exit_code: int, stage: str
) -> None:
    env, checkout = native_job
    env |= {
        "TEST_EXIT": str(exit_code),
        "NIXCFG_CI_STAGE": stage,
        "NIXCFG_PREVIOUS_CANDIDATE": "/candidate from previous job.json",
        "NIXCFG_UPDATE_TARGETS": "alpha beta",
    }
    result = invoke(env, checkout)
    assert result.returncode == exit_code, result.stdout + result.stderr
    args = json.loads(Path(env["TEST_LOG"]).read_text())
    assert args[:3] == ["ci", "update", stage]
    previous_flag = "--previous" if stage == "prepare" else "--candidate"
    assert args[args.index(previous_flag) + 1] == env["NIXCFG_PREVIOUS_CANDIDATE"]
    if stage == "prepare":
        assert args[-3:] == ["--", "alpha", "beta"]
    artifacts = Path(env["RUNNER_TEMP"]) / "update-artifacts"
    assert json.loads((artifacts / "result.json").read_bytes()) == {
        "success": exit_code == 0
    }
    assert (artifacts / "stderr.log").read_text() == "diagnostic evidence\n"
    assert (checkout / "flake.lock").read_text() == "baseline"


@pytest.mark.parametrize(
    "targets", ["alpha --check", "--patch unwanted", "alpha\nbeta"]
)
def test_dispatch_targets_cannot_override_execution_policy(
    native_job, targets: str
) -> None:
    env, checkout = native_job
    assert invoke(env | {"NIXCFG_UPDATE_TARGETS": targets}, checkout).returncode != 0
    assert not Path(env["TEST_LOG"]).exists()


@pytest.mark.parametrize("targets", ["", " \t "])
def test_first_job_uses_default_inventory(native_job, targets: str) -> None:
    env, checkout = native_job
    result = invoke(env | {"NIXCFG_UPDATE_TARGETS": targets}, checkout)
    assert result.returncode == 0, result.stdout + result.stderr
    args = json.loads(Path(env["TEST_LOG"]).read_text())
    assert "--" not in args
    assert "--previous" not in args


@pytest.mark.parametrize("stage", ["invalid", "validate"])
def test_job_rejects_unknown_stage_or_missing_candidate(native_job, stage: str) -> None:
    env, checkout = native_job
    assert invoke(env | {"NIXCFG_CI_STAGE": stage}, checkout).returncode != 0
    assert not Path(env["TEST_LOG"]).exists()


def test_workflow_prepares_each_required_platform_before_parallel_validation() -> None:
    """The serial shared-file writers and parallel validators cover system policy."""
    workflow = yaml.load(
        (ROOT / ".github/workflows/update.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert set(workflow["on"]) == {"workflow_dispatch", "schedule"}
    assert workflow["permissions"] == {"contents": "read"}
    jobs = workflow["jobs"]
    preparation = [
        (name, job) for name, job in jobs.items() if name.startswith("prepare-")
    ]
    assert [job["with"]["system"] for _, job in preparation] == list(
        supported_systems()
    )
    matrix = json.loads(CliRunner().invoke(app, ["matrix"]).stdout)["include"]
    assert {job["with"]["system"]: job["with"]["runner"] for _, job in preparation} == {
        row["system"]: row["runner"] for row in matrix
    }
    for (name, previous), (_, job) in pairwise(preparation):
        assert job["needs"] == name
        assert job["with"]["previous"] == f"prepare-{previous['with']['system']}"
    assert preparation[-1][0] in jobs["validate"]["needs"]
    assert jobs["validate"]["with"]["previous"] == "prepare-x86_64-linux"
    assert jobs["validate"]["strategy"]["fail-fast"] == "false"
    assert jobs["publish"]["needs"] == "validate"


def test_all_authored_actions_commands_are_python() -> None:
    paths = [
        *ROOT.glob(".github/workflows/*.yml"),
        ROOT / ".github/actions/update-runtime/action.yml",
    ]
    for path in paths:
        workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        groups = (
            workflow.get("jobs", {}).values()
            if "jobs" in workflow
            else [workflow["runs"]]
        )
        for group in groups:
            for step in group.get("steps", []):
                if "run" in step:
                    assert step["shell"] == "python"
                    compile(step["run"], str(path), "exec")


@pytest.mark.parametrize("stage", ["prepare", "validate"])
@pytest.mark.parametrize("targets", ["", "alpha beta", "--force", "alpha\nbeta"])
def test_native_adapter_captures_only_cli_output(
    native_job, monkeypatch, stage, targets
) -> None:
    env, checkout = native_job
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(checkout)
    monkeypatch.setenv("NIXCFG_UPDATE_TARGETS", targets)
    if stage == "validate":
        with pytest.raises(ValueError, match="requires a previous"):
            jobs.main("native-validate")
        monkeypatch.setenv("NIXCFG_PREVIOUS_CANDIDATE", "/previous candidate")
    if stage == "prepare" and (targets.startswith("-") or "\n" in targets):
        with pytest.raises(ValueError, match="space-separated"):
            jobs.main("native-prepare")
    else:
        assert jobs.main(f"native-{stage}") == 0
        assert json.loads(
            (Path(env["RUNNER_TEMP"]) / "update-artifacts/result.json").read_text()
        ) == {"success": True}


def test_native_devshell_startup_stays_outside_json(native_job, monkeypatch) -> None:
    env, checkout = native_job
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(checkout)
    monkeypatch.setenv("REPO_ROOT", "/parent repository")
    assert jobs.main("prepare") == 0
    assert json.loads(
        (Path(env["RUNNER_TEMP"]) / "update-artifacts/result.json").read_text()
    ) == {"success": True}
    monkeypatch.setenv("NIXCFG_PREVIOUS_CANDIDATE", "/previous")
    assert jobs.main("native-prepare") == 0
    assert os.environ["REPO_ROOT"] == "/parent repository"


@pytest.fixture
def job_repository(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    init_update_workspace_repo(
        root, tracked_files={"packages/example/sources.json": "old\n"}
    )
    monkeypatch.chdir(root)
    for key, value in {
        "RUNNER_TEMP": str(tmp_path),
        "NIXCFG_RUNTIME": "/runtime",
        "NIXCFG_DEVSHELL": "/tools",
        "GITHUB_OUTPUT": str(tmp_path / "outputs"),
        "GITHUB_REF_NAME": "main",
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": "example/repo",
        "UPDATE_BASE_BRANCH": "main",
    }.items():
        monkeypatch.setenv(key, value)
    return root


@pytest.mark.parametrize("failure", [None, "prek", "coverage", "untracked"])
def test_quality_stops_at_first_failed_gate(
    job_repository, monkeypatch, failure
) -> None:
    calls = []

    def run(*args, capture=False, check=True):
        calls.append(args)
        if failure in args:
            raise subprocess.CalledProcessError(17, args)
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="new.py\n"
            if failure == "untracked" and args[1] == "ls-files"
            else "",
        )

    monkeypatch.setattr(jobs, "_run", run)
    if failure == "untracked":
        with pytest.raises(RuntimeError, match="untracked"):
            jobs.main("quality")
    elif failure:
        with pytest.raises(subprocess.CalledProcessError):
            jobs.main("quality")
        assert failure in calls[-1]
    else:
        assert jobs.main("quality") == 0
        assert (sys.executable, "-m", "coverage", "report") in calls
        assert ("git", "diff", "--exit-code") in calls


@pytest.mark.parametrize(
    "mode", ["changed", "noop", "repair-noop", "wrong-base", "wrong-tree", "gate-drift"]
)
def test_certification_checks_the_applied_and_post_gate_tree(
    job_repository, tmp_path, monkeypatch, mode
) -> None:
    root = job_repository
    base = git(root, "write-tree").decode().strip()
    source = root / "packages/example/sources.json"
    source.write_text("new\n")
    git(root, "add", ".")
    tree = git(root, "write-tree").decode().strip()
    patch = git(root, "diff", "--cached", "--binary")
    git(root, "restore", "--staged", "--worktree", ".")
    if mode in {"noop", "repair-noop"}:
        patch, tree = b"", base
    if mode == "repair-noop":
        monkeypatch.setenv("GITHUB_REF_NAME", "codex/update-repair-123-1")
    evidence = tmp_path / "evidence/prepare-x86_64-linux"
    evidence.mkdir(parents=True)
    (evidence / "candidate.json").write_text(
        json.dumps({
            "base_tree": "wrong" if mode == "wrong-base" else base,
            "tree": "wrong" if mode == "wrong-tree" else tree,
        })
    )
    report = tmp_path / "evidence/validate-aarch64-darwin"
    report.mkdir()
    (report / "validation.json").write_text("{}")
    real_run = jobs._run
    gates = []

    def run(*args, capture=False, check=True):
        if args[0] == "/runtime/bin/nixcfg":
            assert "--report" in args
            (tmp_path / "update.patch").write_bytes(patch)
        elif args[0] == "nix":
            gates.append(args)
            if mode == "gate-drift":
                source.write_text("changed by gate\n")
                git(root, "add", ".")
        else:
            return real_run(*args, capture=capture, check=check)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(jobs, "_run", run)
    if mode in {"wrong-base", "wrong-tree", "gate-drift"}:
        with pytest.raises(ValueError, match="candidate|baseline"):
            jobs.main("certify")
        assert not (tmp_path / "outputs").exists()
    else:
        assert jobs.main("certify") == 0
        assert (
            tmp_path / "outputs"
        ).read_text() == f"changed={str(mode != 'noop').lower()}\n"
        assert bool(gates) == (mode != "noop")
        assert git(root, "write-tree").decode().strip() == tree


@pytest.mark.parametrize("operation", ["publish", "start-repair"])
@pytest.mark.parametrize("changed", [False, True])
def test_publication_uses_signed_commit_and_bounded_repair(
    job_repository, tmp_path, monkeypatch, operation, changed
) -> None:
    workflow = yaml.load(
        (ROOT / ".github/workflows/update.yml").read_text(), Loader=yaml.BaseLoader
    )
    step = next(
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if step.get("env", {}).get("NIXCFG_CI_STAGE") == operation
    )
    monkeypatch.delenv("NIXCFG_DEVSHELL")
    for name, value in step["env"].items():
        if name.startswith("NIXCFG_"):
            monkeypatch.setenv(
                name, value.replace("${{ steps.runtime.outputs.devshell }}", "/tools")
            )
    monkeypatch.setenv(
        "GITHUB_REF_NAME", "codex/update-repair-100-1" if changed else "main"
    )
    monkeypatch.setenv("NIXCFG_UPDATE_TARGETS", "example")
    calls = []

    def run(*args, capture=False, check=True):
        calls.append(args)
        return subprocess.CompletedProcess(
            args, int(changed) if args[:2] == ("git", "diff") else 0
        )

    monkeypatch.setattr(jobs, "_run", run)
    assert jobs.main(operation) == 0
    commits = [args for args in calls if "commit" in args]
    assert bool(commits) == changed
    if changed:
        assert commits[0][:7] == (
            "nix",
            "develop",
            "/tools",
            "--command",
            "git",
            "commit",
            "-S",
        )
    if operation == "publish":
        assert calls[-1][:3] == ("gh", "pr", "create")
        assert calls[-1][calls[-1].index("--base") + 1] == "main"
        assert (
            "https://github.com/example/repo/actions/runs/123"
            in (tmp_path / "update-body.md").read_text()
        )
    else:
        assert calls[-1][:3] == ("gh", "workflow", "run")
        assert "repair=false" in calls[-1]
        assert "targets=example" in calls[-1]


def test_bootstrap_matrix_and_failure_evidence(
    job_repository, tmp_path, monkeypatch
) -> None:
    calls = []

    def run(*args, capture=False, check=True):
        calls.append(args)
        stdout = ""
        if args[0] == "/runtime/bin/nixcfg":
            stdout = '{"include": []}'
        elif args[:2] == ("gh", "api"):
            if "--paginate" in args:
                stdout = "10\n11\n"
            else:
                assert "--allow-escape-sequences" in args
                stdout = "\x1b[31mupstream failure\x1b[0m\n"
        return subprocess.CompletedProcess(args, 0, stdout=stdout)

    monkeypatch.setattr(jobs, "_run", run)
    for stage in ("bootstrap", "matrix", "collect-evidence", "install-agent", "repair"):
        assert jobs.main(stage) == 0
    outputs = dict(
        line.split("=", 1) for line in (tmp_path / "outputs").read_text().splitlines()
    )
    assert Path(outputs["runtime"]).parent == tmp_path
    assert Path(outputs["devshell"]).parent == tmp_path
    assert json.loads(outputs["matrix"]) == {"include": []}
    assert {p.name for p in (tmp_path / "repair-evidence").iterdir()} == {
        "job-10.log",
        "job-11.log",
    }
    assert (tmp_path / "repair-evidence/job-10.log").read_text() == (
        "\x1b[31mupstream failure\x1b[0m\n"
    )
    assert calls[-2] == (
        "git",
        "apply",
        "--index",
        "--binary",
        str(tmp_path / "repair.patch"),
    )
    assert calls[-1][-3:] == ("python", "lib/update/ci/jobs.py", "quality")


@pytest.mark.parametrize(
    ("actions", "environment"),
    [("false", "github-hosted"), ("true", "self-hosted")],
)
def test_image_cleanup_refuses_developer_and_self_hosted_machines(
    monkeypatch, actions: str, environment: str
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", actions)
    monkeypatch.setenv("RUNNER_ENVIRONMENT", environment)
    with pytest.raises(RuntimeError, match="disposable GitHub-hosted"):
        jobs.main("clean-image")


@pytest.mark.parametrize("system", ["darwin", "linux"])
@pytest.mark.parametrize("active_xcode", ["selected", "missing", "relative"])
def test_cleanup_preserves_active_xcode_aliases_and_unselected_data(
    tmp_path, monkeypatch, system, active_xcode
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setattr(jobs.sys, "platform", system)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    apps = tmp_path / "Applications"
    selected = apps / "Xcode_active.app/Contents/Developer"
    selected.mkdir(parents=True)
    old = apps / "Xcode_old.app"
    old.mkdir()
    alias = apps / "Xcode.app"
    alias.symlink_to(apps / "Xcode_active.app")
    other = apps / "Unrelated.app"
    other.mkdir()
    unused = tmp_path / "unused-tool"
    unused.mkdir()
    android = tmp_path / "Library/Android/sdk"
    android.mkdir(parents=True)
    link = tmp_path / "external-link"
    link.symlink_to(other)
    monkeypatch.setattr(jobs, "_APPLICATIONS", apps)
    monkeypatch.setattr(
        jobs, "_UNUSED_IMAGE_PATHS", {system: (unused, link, tmp_path / "absent")}
    )
    calls = []
    real_run = jobs._run

    def run(*args, capture=False, check=True):
        calls.append(args)
        if args[0] == "sudo":
            # Exercise actual Python deletion, confined to this test's directories.
            assert Path(args[-1]).is_relative_to(tmp_path)
            return real_run(*args[1:], capture=capture, check=check)
        value = {
            "selected": str(selected),
            "missing": str(tmp_path / "missing"),
            "relative": "relative/path",
        }[active_xcode]
        return subprocess.CompletedProcess(args, 0, stdout=value)

    monkeypatch.setattr(jobs, "_run", run)
    rejected = system == "darwin" and active_xcode != "selected"
    if rejected:
        with pytest.raises(ValueError, match="active Xcode"):
            jobs.main("clean-image")
    else:
        assert jobs.main("clean-image") == 0
    assert unused.exists() == rejected
    assert selected.is_dir()
    assert alias.is_symlink()
    assert other.is_dir()
    assert link.is_symlink()
    assert old.exists() == (system != "darwin" or rejected)
    assert android.exists() == (system != "darwin" or rejected)
    if system == "darwin" and not rejected:
        assert ("xcrun", "simctl", "runtime", "delete", "all") in calls
