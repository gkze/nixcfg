"""Step runner, Buildkite agent adapter, and bootstrap upload."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from lib.buildkite import __main__ as upload_cli
from lib.buildkite.concurrency import BuildRef
from lib.buildkite.emit import step_key
from lib.buildkite.execute import (
    BuildkiteAgent,
    CommandResult,
    MemoryAgent,
    RunState,
    WorkflowParseError,
    _run_native,
    _run_script,
    _stop_one,
    action_kind,
    aggregate,
    cell_from_env,
    fanout,
    output_key,
    result_key,
    run_job,
    skip_job,
    subprocess_daemon,
    subprocess_runner,
)
from lib.buildkite.github_expr import ExprContext
from lib.buildkite.graph import ActionStep
from lib.buildkite.step import main as step_main

ROOT = Path(__file__).resolve().parents[2]


class _Runner:
    def __init__(self, code: int = 0, *, fail_exit: bool = False) -> None:
        self.code = code
        self.fail_exit = fail_exit
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str]] = []
        self.stdins: list[str | None] = []

    def __call__(
        self,
        argv: list[str],
        *,
        env: dict[str, str],
        cwd: Path,
        timeout: float | None,
        stdin: str | None,
    ) -> CommandResult:
        del cwd, timeout
        self.calls.append(list(argv))
        self.envs.append(dict(env))
        self.stdins.append(stdin)
        script = Path(argv[-1]) if argv else None
        if (
            self.fail_exit
            and script is not None
            and script.is_file()
            and "SystemExit(1)" in script.read_text()
        ):
            return CommandResult(1, "")
        if env.get("NIXCFG_CI_STAGE") == "certify":
            output = env.get("GITHUB_OUTPUT")
            if output:
                Path(output).write_text("changed=true\n", encoding="utf-8")
        if argv[:1] == ["git"] and argv[1:2] == ["rev-parse"]:
            return CommandResult(0, "true\n")
        if argv[:2] == ["git", "fetch"]:
            return CommandResult(1, "")
        return CommandResult(self.code, "")


class _Daemon:
    def __init__(self, *, alive: bool = True, explode: bool = False) -> None:
        self.alive = alive
        self.explode = explode
        self.killed = False

    def poll(self) -> int | None:
        if self.alive:
            return None
        return 0

    def terminate(self) -> None:
        self.alive = False

    def kill(self) -> None:
        if self.explode:
            message = "kill failed"
            raise OSError(message)
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        if self.explode:
            raise subprocess.TimeoutExpired("daemon", timeout)
        return 0


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    values = {
        "BUILDKITE_BRANCH": "main",
        "BUILDKITE_COMMIT": "abc123",
        "BUILDKITE_PULL_REQUEST": "false",
        "BUILDKITE_BUILD_NUMBER": "12",
        "BUILDKITE_BUILD_ID": "build-12",
        "NIXCFG_REPOSITORY": "gkze/nixcfg",
        "NIXCFG_HEAD_REPOSITORY": "gkze/nixcfg",
        "NIXCFG_DEFAULT_BRANCH": "main",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_TOKEN": "ghs",
        "CACHIX_AUTH_TOKEN": "cachix-token",
        "UPDATE_SELF_HEAL_GITHUB_TOKEN": "pat",
        "GPG_PRIVATE_KEY": "key-material",
    }
    values.update(extra)
    return values


def test_pr_quality_runs_checkout_and_python_steps(tmp_path: Path) -> None:
    runner = _Runner()
    agent = MemoryAgent()
    code = run_job(
        ROOT,
        "pr-quality.yml",
        "certify-python",
        agent=agent,
        env=_env(tmp_path),
        runner=runner,
        spawn=lambda *args, **kwargs: _Daemon(),
    )
    assert code == 0
    assert result_key("pr-quality--certify-python") in agent.meta
    assert agent.meta[result_key("pr-quality--certify-python")] == "success"
    assert ["git", "checkout", "--detach", "abc123"] in runner.calls
    assert all("GITHUB_ACTIONS" not in env for env in runner.envs)
    assert any(env.get("NIXCFG_SIBLING_REFRESH_ARGV") for env in runner.envs)


def _mark_publish_needs(agent: MemoryAgent) -> None:
    for job_id in (
        "prepare-merge",
        "plan-darwin-closures",
        "validate-arm",
        "validate-x86",
        "validate-darwin-packages",
        "validate-darwin-roots",
        "validate-darwin-closures",
    ):
        agent.set_meta(result_key(step_key("update.yml", job_id)), "success")
    for job_id in ("validate-darwin-warm-rust", "validate-darwin-cascade-warmup"):
        for slot in range(5):
            key = step_key("update.yml", job_id, {"slot": str(slot)})
            agent.set_meta(result_key(key), "success")


def test_publish_stays_gated_until_the_flag_is_true(tmp_path: Path) -> None:
    runner = _Runner()
    agent = MemoryAgent()
    agent.artifacts["prepare-x86_64-linux"] = {"candidate.json": b"{}"}
    _mark_publish_needs(agent)
    code = run_job(
        ROOT,
        "update.yml",
        "publish",
        agent=agent,
        env=_env(tmp_path),
        runner=runner,
        spawn=lambda *args, **kwargs: _Daemon(),
    )
    assert code == 0
    stages = [env.get("NIXCFG_CI_STAGE") for env in runner.envs]
    assert "publish" not in stages
    assert "flush-cachix" in stages
    assert ["gpg", "--batch", "--yes", "--import"] not in runner.calls
    runner = _Runner()
    allowed = MemoryAgent()
    allowed.artifacts["prepare-x86_64-linux"] = {"candidate.json": b"{}"}
    _mark_publish_needs(allowed)
    code = run_job(
        ROOT,
        "update.yml",
        "publish",
        agent=allowed,
        env=_env(tmp_path, NIXCFG_BUILDKITE_PUBLISH="true"),
        runner=runner,
        spawn=lambda *args, **kwargs: _Daemon(),
    )
    assert code == 0
    assert "publish" in [env.get("NIXCFG_CI_STAGE") for env in runner.envs]
    assert ["gpg", "--batch", "--yes", "--import"] in runner.calls
    assert "key-material" in runner.stdins


def test_failed_checkout_still_flushes_and_cancels_matrix_siblings(
    tmp_path: Path,
) -> None:
    runner = _Runner(1)
    agent = MemoryAgent()
    agent.set_meta(result_key(step_key("update.yml", "prepare-merge")), "success")
    env = _env(tmp_path)
    env["NIXCFG_BUILDKITE_CELL"] = json.dumps({"host": "0"})
    code = run_job(
        ROOT,
        "update.yml",
        "canary-darwin-fods",
        agent=agent,
        env=env,
        runner=runner,
        spawn=lambda *args, **kwargs: _Daemon(),
    )
    assert code == 1
    cell = step_key("update.yml", "canary-darwin-fods", {"host": "0"})
    assert agent.meta[result_key(cell)] == "failure"
    assert agent.cancelled == [("canary-darwin-fods (1)",)]


def test_continue_on_error_keeps_the_proof_job_green(tmp_path: Path) -> None:
    runner = _Runner(fail_exit=True)
    agent = MemoryAgent()
    env = _env(tmp_path, BUILDKITE_PULL_REQUEST="4", BUILDKITE_SOURCE="webhook")
    code = run_job(
        ROOT,
        "cachix-flush-proof.yml",
        "fail-after-push",
        agent=agent,
        env=env,
        runner=runner,
        spawn=lambda *args, **kwargs: _Daemon(),
    )
    assert code == 0
    assert agent.meta[result_key("cachix-flush-proof--fail-after-push")] == "success"


def test_dynamic_matrix_fanout_aggregate_and_skip(tmp_path: Path) -> None:
    agent = MemoryAgent()
    for job_id in ("canary-slots", "prepare-merge", "plan-darwin-closures"):
        agent.set_meta(result_key(step_key("update.yml", job_id)), "success")
    for host in ("0", "1"):
        key = step_key("update.yml", "canary-darwin-fods", {"host": host})
        agent.set_meta(result_key(key), "success")
    producer = step_key("update.yml", "canary-slots")
    agent.set_meta(output_key(producer, "enabled"), "true")
    agent.set_meta(output_key(producer, "matrix"), json.dumps({"slot": ["0"]}))
    env = _env(tmp_path)
    assert fanout(ROOT, "update.yml", "canary-warm-rust", agent=agent, env=env) == 0
    uploaded = agent.pipelines[-1]
    assert any(step.get("key", "").endswith("--slot-0") for step in uploaded["steps"])
    agent.set_meta(output_key(producer, "enabled"), "false")
    assert fanout(ROOT, "update.yml", "canary-warm-rust", agent=agent, env=env) == 0
    agent.set_meta(output_key(producer, "enabled"), "true")
    agent.set_meta(output_key(producer, "matrix"), "")
    assert fanout(ROOT, "update.yml", "canary-warm-rust", agent=agent, env=env) == 1
    agent.set_meta(output_key(producer, "matrix"), "not-json")
    assert fanout(ROOT, "update.yml", "canary-warm-rust", agent=agent, env=env) == 1
    agent.set_meta(output_key(producer, "matrix"), json.dumps({"include": []}))
    assert fanout(ROOT, "update.yml", "canary-warm-rust", agent=agent, env=env) == 1
    parent = step_key("update.yml", "canary-warm-rust")
    agent.set_meta(f"cells--{parent}", "slot-0,slot-1")
    agent.set_meta(result_key(f"{parent}--slot-0"), "success")
    agent.set_meta(result_key(f"{parent}--slot-1"), "timed_out")
    assert aggregate(ROOT, "update.yml", "canary-warm-rust", agent=agent, env=env) == 0
    assert agent.meta[result_key(parent)] == "timed_out"
    assert skip_job(ROOT, "update.yml", "canary-warm-rust", agent=agent, env=env) == 0
    assert agent.meta[result_key(parent)] == "skipped"
    with pytest.raises(WorkflowParseError, match="not a dynamic matrix"):
        fanout(ROOT, "update.yml", "prepare-pins", agent=agent, env=env)


def test_shallow_checkout_failure_and_local_edges(tmp_path: Path) -> None:
    runner = _Runner()
    agent = MemoryAgent()
    env = _env(tmp_path, BUILDKITE_PULL_REQUEST="9")
    code = run_job(
        ROOT,
        "pr-update-prepare.yml",
        "targets",
        agent=agent,
        env=env,
        runner=runner,
        spawn=lambda *args, **kwargs: _Daemon(),
    )
    assert code == 1
    assert ["git", "fetch", "--unshallow", "origin"] in runner.calls
    dead = _Daemon(alive=False)
    runner = _Runner()
    pinned = run_job(
        ROOT,
        "update.yml",
        "prepare-pins",
        agent=MemoryAgent(),
        env=_env(tmp_path),
        runner=runner,
        spawn=lambda *args, **kwargs: dead,
    )
    assert pinned == 1
    quiet = _Runner()
    assert (
        run_job(
            ROOT,
            "update.yml",
            "prepare-pins",
            agent=MemoryAgent(),
            env=_env(tmp_path, CACHIX_AUTH_TOKEN=""),
            runner=quiet,
            spawn=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("no daemon")
            ),
        )
        == 0
    )
    state = RunState(
        root=ROOT,
        agent=MemoryAgent(),
        runner=runner,
        spawn=lambda *args, **kwargs: dead,
        env=_env(tmp_path),
        job_name="step",
        runner_temp=tmp_path,
    )
    context = ExprContext(
        inputs={},
        needs={},
        steps={},
        github={},
        runner={"temp": str(tmp_path)},
        secrets={},
        matrix={},
    )
    with pytest.raises(WorkflowParseError, match="no command"):
        _run_script(
            state,
            ActionStep(
                None,
                "empty",
                None,
                "python",
                (),
                None,
                continue_on_error=False,
                timeout_minutes=None,
                uses=None,
                with_args=(),
            ),
            context,
        )
    with pytest.raises(WorkflowParseError, match="unsupported shell"):
        _run_script(
            state,
            ActionStep(
                None,
                "shell",
                "echo hi",
                "zsh",
                (),
                None,
                continue_on_error=False,
                timeout_minutes=None,
                uses=None,
                with_args=(),
            ),
            context,
        )
    with pytest.raises(WorkflowParseError, match="not a step"):
        _run_native(
            state,
            ActionStep(
                None,
                "native",
                None,
                None,
                (),
                None,
                continue_on_error=False,
                timeout_minutes=None,
                uses=_NATIVE,
                with_args=(),
            ),
            context,
        )
    with pytest.raises(WorkflowParseError, match="unsupported action"):
        action_kind("actions/not-real@v1")
    with pytest.raises(WorkflowParseError, match="not an object"):
        cell_from_env({"NIXCFG_BUILDKITE_CELL": "[]"})
    with pytest.raises(WorkflowParseError, match="invalid meta-data"):
        MemoryAgent().set_meta("bad key", "x")
    assert MemoryAgent().download("missing", tmp_path / "out") == 1
    _stop_one(_Daemon(alive=False))
    _stop_one(_Daemon(explode=True))


_NATIVE = "./.github/workflows/update-native.yml"


def test_buildkite_agent_and_process_helpers(tmp_path: Path) -> None:
    calls: list[tuple[list[str], str | None]] = []

    def runner(
        argv: list[str],
        *,
        env: dict[str, str],
        cwd: Path,
        timeout: float | None,
        stdin: str | None,
    ) -> CommandResult:
        del env, timeout
        calls.append((list(argv), stdin))
        if "get" in argv:
            return CommandResult(1, "")
        if "download" in argv:
            staging = Path(argv[-1])
            target = staging / "bk-artifacts" / "note"
            target.mkdir(parents=True)
            (target / "a.txt").write_text("hi", encoding="utf-8")
            return CommandResult(0, "")
        return CommandResult(0, "")

    agent = BuildkiteAgent({"BUILDKITE_BUILD_CHECKOUT_PATH": str(tmp_path)}, runner)
    assert agent.get_meta("result--job") is None
    agent.set_meta("result--job", "success")
    source = tmp_path / "file.txt"
    source.write_text("body", encoding="utf-8")
    assert agent.upload("note", source) == 0
    assert agent.upload("note", tmp_path / "missing") == 0
    dest = tmp_path / "down"
    assert agent.download("note", dest) == 0
    assert (dest / "a.txt").read_text(encoding="utf-8") == "hi"
    agent.upload_pipeline({"steps": []})
    assert calls[-1][1] == '{"steps": []}'
    agent.cancel_labeled(())
    agent.cancel_labeled(("validate (0)",))
    failing = BuildkiteAgent({}, lambda *args, **kwargs: CommandResult(1, ""))
    with pytest.raises(WorkflowParseError, match="meta-data set failed"):
        failing.set_meta("result--job", "failure")
    with pytest.raises(WorkflowParseError, match="pipeline upload failed"):
        failing.upload_pipeline({"steps": []})
    download_fail = BuildkiteAgent(
        {},
        lambda *args, **kwargs: CommandResult(0, ""),
    )
    assert download_fail.download("missing", tmp_path / "empty-down") == 1
    result = subprocess_runner(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        env={},
        cwd=tmp_path,
        timeout=0.05,
        stdin=None,
    )
    assert result.timed_out is True
    assert result.returncode == 124
    quick = subprocess_runner(["true"], env={}, cwd=tmp_path, timeout=None, stdin=None)
    assert quick.returncode == 0
    daemon = subprocess_daemon(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        env={},
        cwd=tmp_path,
    )
    _stop_one(daemon)


def test_cancel_labeled_uses_the_builds_api(tmp_path: Path) -> None:
    seen: list[str] = []

    class _Response:
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, length: int = -1) -> bytes:
            del length
            return b'{"jobs":[{"name":"canary-darwin-fods (1)","id":"job-9"}]}'

    def opener(request: object, timeout: int) -> _Response:
        del timeout
        seen.append(getattr(request, "full_url", ""))
        return _Response()

    agent = BuildkiteAgent(
        {
            "BUILDKITE_API_TOKEN": "token",
            "BUILDKITE_ORGANIZATION_SLUG": "gkze",
            "BUILDKITE_PIPELINE_SLUG": "nixcfg",
            "BUILDKITE_BUILD_NUMBER": "4",
            "BUILDKITE_BUILD_CHECKOUT_PATH": str(tmp_path),
        },
        lambda *args, **kwargs: CommandResult(0, ""),
        opener=opener,
    )
    agent.cancel_labeled(("canary-darwin-fods (1)",))
    assert any(url.endswith("/jobs/job-9/cancel") for url in seen)
    broken = BuildkiteAgent(
        {
            "BUILDKITE_API_TOKEN": "token",
            "BUILDKITE_ORGANIZATION_SLUG": "gkze",
            "BUILDKITE_PIPELINE_SLUG": "nixcfg",
            "BUILDKITE_BUILD_NUMBER": "4",
        },
        lambda *args, **kwargs: CommandResult(0, ""),
        opener=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("down")),
    )
    broken.cancel_labeled(("canary-darwin-fods (1)",))


def test_direct_step_edges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: PLR0915
    from lib.buildkite.execute import (
        _fail_fast_labels,
        _fold,
        _job_steps,
        _run_cache,
        _run_setup_python,
        _run_upload,
        parse_github_output,
    )
    from lib.buildkite.graph import Job

    assert (
        parse_github_output("name<<EOF\nline\nEOF\nplain\nkey=value\n")["name"]
        == "line"
    )
    assert parse_github_output("name<<EOF\nline\nEOF\n")["name"] == "line"

    def expire(*args: object, **kwargs: object) -> CommandResult:
        del args, kwargs
        raise subprocess.TimeoutExpired("cmd", 1, output=b"partial")

    monkeypatch.setattr(subprocess, "run", expire)
    timed = subprocess_runner(["true"], env={}, cwd=tmp_path, timeout=1, stdin=None)
    assert timed.timed_out is True
    assert timed.stdout == "partial"
    step = ActionStep(
        None,
        "cache",
        None,
        None,
        (),
        None,
        continue_on_error=False,
        timeout_minutes=None,
        uses="actions/cache@v5",
        with_args=(),
    )
    state = RunState(
        root=ROOT,
        agent=MemoryAgent(),
        runner=_Runner(),
        spawn=lambda *args, **kwargs: _Daemon(),
        env=_env(tmp_path),
        job_name="step",
        runner_temp=tmp_path,
    )
    context = ExprContext(
        inputs={},
        needs={},
        steps={},
        github={},
        runner={"temp": str(tmp_path)},
        secrets={},
        matrix={},
    )
    assert _run_cache(state, step, context).returncode == 0
    python = ActionStep(
        None,
        "python",
        None,
        None,
        (),
        None,
        continue_on_error=False,
        timeout_minutes=1,
        uses="actions/setup-python@v6",
        with_args=(("python-version", "3.12"),),
    )
    assert _run_setup_python(state, python, context).returncode == 0
    upload = ActionStep(
        None,
        "upload",
        None,
        None,
        (),
        None,
        continue_on_error=False,
        timeout_minutes=None,
        uses="actions/upload-artifact@v7",
        with_args=(),
    )
    assert _run_upload(state, upload, context).returncode == 1
    directory = tmp_path / "tree"
    nested = directory / "sub"
    nested.mkdir(parents=True)
    (nested / "a.txt").write_text("a", encoding="utf-8")
    (directory / "b.txt").write_text("b", encoding="utf-8")
    filled = ActionStep(
        None,
        "upload",
        None,
        None,
        (),
        None,
        continue_on_error=False,
        timeout_minutes=None,
        uses="actions/upload-artifact@v7",
        with_args=(("name", "tree"), ("path", str(directory))),
    )
    assert _run_upload(state, filled, context).returncode == 0
    single = tmp_path / "solo.txt"
    single.write_text("s", encoding="utf-8")
    file_upload = ActionStep(
        None,
        "upload",
        None,
        None,
        (),
        None,
        continue_on_error=False,
        timeout_minutes=None,
        uses="actions/upload-artifact@v7",
        with_args=(("name", "solo"), ("path", str(single))),
    )
    assert _run_upload(state, file_upload, context).returncode == 0
    assert state.agent.upload("solo", single) == 0
    assert MemoryAgent().upload("gone", tmp_path / "absent") == 0
    bash = ActionStep(
        None,
        "shell",
        "echo hi",
        "bash",
        (),
        None,
        continue_on_error=False,
        timeout_minutes=None,
        uses=None,
        with_args=(),
    )
    assert _run_script(state, bash, context).returncode == 0
    assert state.agent.download(None, tmp_path / "all") == 0
    from lib.buildkite.execute import StepOutcome

    assert _fold(
        "success",
        upload,
        StepOutcome(124, timed_out=True, outputs={}),
        hard=False,
    ) == ("timed_out", True)
    bare = Job(
        "demo.yml",
        "build",
        "build",
        (),
        "",
        None,
        None,
        None,
        None,
        (),
        (),
        None,
        "actions/checkout@v6",
        (),
        (),
    )
    with pytest.raises(WorkflowParseError, match="unsupported reusable"):
        _job_steps(bare)
    assert _fail_fast_labels("build", bare, {}) == ()
    agent = MemoryAgent()
    code = run_job(
        ROOT,
        "cachix-flush-proof.yml",
        "fork-read-only",
        agent=agent,
        env=_env(tmp_path),
        runner=_Runner(),
        spawn=lambda *args, **kwargs: _Daemon(),
    )
    assert code == 0
    assert agent.meta[result_key("cachix-flush-proof--fork-read-only")] == "skipped"
    reader = BuildkiteAgent(
        {"BUILDKITE_BUILD_CHECKOUT_PATH": str(tmp_path)},
        lambda *args, **kwargs: CommandResult(0, "success\n"),
    )
    assert reader.get_meta("result--job") == "success"
    missing = BuildkiteAgent({}, lambda *args, **kwargs: CommandResult(3, ""))
    assert missing.download(None, tmp_path / "nope") == 3

    def nix_timeout(
        argv: list[str],
        *,
        env: dict[str, str],
        cwd: Path,
        timeout: float | None,
        stdin: str | None,
    ) -> CommandResult:
        del env, cwd, timeout, stdin
        if argv[:1] == ["nix"]:
            return CommandResult(124, "", timed_out=True)
        return CommandResult(0, "")

    assert (
        run_job(
            ROOT,
            "update.yml",
            "prepare-pins",
            agent=MemoryAgent(),
            env=_env(tmp_path),
            runner=nix_timeout,
            spawn=lambda *args, **kwargs: _Daemon(),
        )
        == 1
    )

    def full_history(
        argv: list[str],
        *,
        env: dict[str, str],
        cwd: Path,
        timeout: float | None,
        stdin: str | None,
    ) -> CommandResult:
        del env, cwd, timeout, stdin
        if argv[:2] == ["git", "rev-parse"]:
            return CommandResult(0, "false\n")
        return CommandResult(0, "")

    assert (
        run_job(
            ROOT,
            "pr-update-prepare.yml",
            "targets",
            agent=MemoryAgent(),
            env=_env(tmp_path, BUILDKITE_PULL_REQUEST="9"),
            runner=full_history,
            spawn=lambda *args, **kwargs: _Daemon(),
        )
        == 0
    )

    def fetched(
        argv: list[str],
        *,
        env: dict[str, str],
        cwd: Path,
        timeout: float | None,
        stdin: str | None,
    ) -> CommandResult:
        del env, cwd, timeout, stdin
        if argv[:2] == ["git", "rev-parse"]:
            return CommandResult(0, "true\n")
        return CommandResult(0, "")

    assert (
        run_job(
            ROOT,
            "pr-update-prepare.yml",
            "targets",
            agent=MemoryAgent(),
            env=_env(tmp_path, BUILDKITE_PULL_REQUEST="9"),
            runner=fetched,
            spawn=lambda *args, **kwargs: _Daemon(),
        )
        == 0
    )


def test_upload_admits_then_uploads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _env(tmp_path)
    env["NIXCFG_BUILDKITE_WORKFLOW"] = "pr-quality.yml"
    env["NIXCFG_CHANGED_PATHS"] = ""
    env["BUILDKITE_BUILD_CHECKOUT_PATH"] = str(ROOT)
    document = upload_cli.pipeline_document(env, root=ROOT, runner=_Runner())
    assert document["steps"][0]["group"] == "PR quality"
    assert upload_cli.main(["render"], env) == 0
    assert upload_cli.main(["nope"], env) == 2
    assert step_main(["nope"], env) == 2

    uploaded: list[str] = []

    def runner(
        argv: list[str],
        *,
        env: dict[str, str],
        cwd: Path,
        timeout: float | None,
        stdin: str | None,
    ) -> CommandResult:
        del env, cwd, timeout
        uploaded.append(stdin or "")
        assert argv[0] == "buildkite-agent"
        return CommandResult(0, "")

    assert upload_cli.upload(env, root=ROOT, runner=runner) == 0
    assert '"group": "PR quality"' in uploaded[-1]
    update_env = dict(env)
    update_env["NIXCFG_BUILDKITE_WORKFLOW"] = "update.yml"
    update_env["BUILDKITE_BRANCH"] = "topic"
    assert upload_cli.upload(update_env, root=ROOT, runner=runner) == 4

    class _Response:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, length: int = -1) -> bytes:
            del length
            return json.dumps(self.payload).encode()

    pages = [
        [
            {"id": "build-12", "number": 12, "state": "running"},
            {"id": "older", "number": 11, "state": "running"},
        ]
    ]

    def opener(request: object, timeout: int) -> _Response:
        del timeout
        url = getattr(request, "full_url", "")
        if url.endswith("/cancel"):
            return _Response({})
        return _Response(pages[0])

    update_env["BUILDKITE_API_TOKEN"] = "token"  # noqa: S105 -- fixture credential, not a secret
    update_env["BUILDKITE_ORGANIZATION_SLUG"] = "gkze"
    update_env["BUILDKITE_PIPELINE_SLUG"] = "nixcfg"
    slept: list[float] = []
    assert (
        upload_cli.upload(
            update_env,
            root=ROOT,
            runner=runner,
            opener=opener,
            sleep=slept.append,
            attempts=1,
        )
        == 0
    )
    pages[0] = [
        {"id": "build-12", "number": 12, "state": "running"},
        {"id": "newer", "number": 13, "state": "running"},
    ]
    assert (
        upload_cli.upload(
            update_env,
            root=ROOT,
            runner=runner,
            opener=opener,
            sleep=slept.append,
            attempts=1,
            interval=0.1,
        )
        == 5
    )
    assert slept == [0.1]
    assert upload_cli.changed_paths(env, _Runner()) == ()
    missing = dict(env)
    del missing["NIXCFG_CHANGED_PATHS"]
    missing["BUILDKITE_SOURCE"] = "schedule"
    assert upload_cli.changed_paths(missing, _Runner(1)) is None
    missing["BUILDKITE_SOURCE"] = "webhook"
    assert upload_cli.changed_paths(missing, _Runner(1)) is None

    class _Diff:
        def __call__(
            self,
            argv: list[str],
            *,
            env: dict[str, str],
            cwd: Path,
            timeout: float | None,
            stdin: str | None,
        ) -> CommandResult:
            del argv, env, cwd, timeout, stdin
            return CommandResult(0, "lib/update/ci/jobs.py\n\n")

    assert upload_cli.changed_paths(missing, _Diff()) == ("lib/update/ci/jobs.py",)
    monkeypatch.setattr(upload_cli, "upload", lambda values: 9)
    assert upload_cli.main(["upload"], env) == 9
    monkeypatch.setattr(sys, "argv", ["nixcfg-buildkite", "upload"])
    assert upload_cli.main(None, env) == 9
    monkeypatch.setattr(upload_cli.time, "sleep", lambda interval: None)
    upload_cli._sleep(0)
    assert isinstance(BuildRef("a", 1, "running"), BuildRef)
