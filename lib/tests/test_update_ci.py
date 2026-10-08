"""Exercise Python Actions jobs with real processes and Git boundaries."""

import ast
import json
import os
import subprocess
import sys
import threading
import time
from io import StringIO
from itertools import pairwise
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from lib.system_policy import supported_systems
from lib.tests._update_workspace_helpers import init_update_workspace_repo
from lib.update.candidate import git
from lib.update.ci import candidate as pipeline
from lib.update.ci import jobs
from lib.update.ci.candidate import app

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "lib/update/ci/jobs.py"


def test_job_launcher_supports_hosted_runner_python() -> None:
    """Bootstrap must parse before Nix supplies the project's Python runtime."""
    ast.parse(SCRIPT.read_text(), filename=str(SCRIPT), feature_version=(3, 12))


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
        "import json, os, subprocess, sys, time\n"
        "from pathlib import Path\n"
        "name = Path(sys.argv[0]).name\n"
        "args = sys.argv[1:]\n"
        "if name == 'nix':\n"
        "    print('devshell startup', flush=True)\n"
        "    sys.exit(subprocess.call(args[args.index('--command') + 1:]))\n"
        "if name == 'cachix':\n"
        "    Path(os.environ['TEST_CACHE_LOG']).write_text(json.dumps(args))\n"
        "    if seconds := os.environ.get('TEST_CACHE_SLEEP_SECONDS'):\n"
        "        time.sleep(float(seconds))\n"
        "    sys.exit(int(os.environ.get('TEST_CACHE_EXIT', '0')))\n"
        "Path(os.environ['TEST_LOG']).write_text(json.dumps(args))\n"
        "Path(args[args.index('--output') + 1]).write_text('candidate or evidence')\n"
        "if os.environ.get('UPDATE_RUN_LOG') == '1':\n"
        "    logs = Path(os.environ['UPDATE_RUN_LOG_DIR']) / 'test-run'\n"
        "    logs.mkdir(parents=True, exist_ok=True)\n"
        "    (logs / 'output.log').write_text('source failure detail\\n')\n"
        "if receipts := os.environ.get('TEST_PREFETCH_RECEIPTS'):\n"
        "    Path(os.environ['UPDATE_PREFETCH_RECEIPTS']).write_text(receipts)\n"
        "print(json.dumps({'success': int(os.environ.get('TEST_EXIT', '0')) == 0}))\n"
        "print('diagnostic evidence', file=sys.stderr)\n"
        "if stop := os.environ.get('TEST_STOP_FILE'):\n"
        "    end = time.time() + float(os.environ.get('TEST_STOP_TIMEOUT_SECONDS', '5'))\n"
        "    while not Path(stop).exists() and time.time() < end:\n"
        "        time.sleep(0.01)\n"
        "elif seconds := os.environ.get('TEST_QUIET_SLEEP_SECONDS'):\n"
        "    logs = Path(os.environ.get('UPDATE_RUN_LOG_DIR', '')) / 'test-run'\n"
        "    output = logs / 'output.log'\n"
        "    end = time.time() + float(seconds)\n"
        "    n = 0\n"
        "    while time.time() < end:\n"
        "        if os.environ.get('TEST_APPEND_RUN_LOG') == '1':\n"
        "            logs.mkdir(parents=True, exist_ok=True)\n"
        "            with output.open('a') as fh:\n"
        "                fh.write(f'live build line {n}\\n')\n"
        "            n += 1\n"
        "        time.sleep(0.01)\n"
        "sys.exit(int(os.environ.get('TEST_EXIT', '0')))\n"
    )
    boundary.chmod(0o755)
    (tools / "nix").symlink_to(boundary)
    (tools / "cachix").symlink_to(boundary)
    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin/nixcfg").symlink_to(boundary)
    env = os.environ | {
        "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
        "TEST_LOG": str(tmp_path / "command.json"),
        "TEST_CACHE_LOG": str(tmp_path / "cache.json"),
        "RUNNER_TEMP": str(tmp_path / "runner temp"),
        "NIXCFG_RUNTIME": str(runtime),
        "NIXCFG_DEVSHELL": str(tmp_path / "devshell"),
        "NIXCFG_CI_STAGE": "prepare",
    }
    return env, checkout


def _wait_for_log_text(path: Path, needle: str, *, timeout: float = 5.0) -> str:
    """Block until *path* contains *needle*, or raise with the observed text."""
    deadline = time.monotonic() + timeout
    text = ""
    while time.monotonic() < deadline:
        if path.is_file():
            text = path.read_text()
            if needle in text:
                return text
        time.sleep(0.01)
    message = f"did not observe {needle!r} in {path}: {text!r}"
    raise AssertionError(message)


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
@pytest.mark.parametrize(
    "stage", ["prepare", "validate", "cache-root-deps", "plan-shards"]
)
@pytest.mark.parametrize("validate_all_packages", [False, True])
def test_native_job_keeps_evidence_and_propagates_failure(
    native_job, exit_code: int, stage: str, validate_all_packages: bool
) -> None:
    env, checkout = native_job
    env |= {
        "TEST_EXIT": str(exit_code),
        "NIXCFG_CI_STAGE": stage,
        "NIXCFG_PREVIOUS_CANDIDATE": "/candidate from previous job.json",
        "NIXCFG_UPDATE_TARGETS": "alpha beta",
        "NIXCFG_VALIDATE_ALL_PACKAGES": str(validate_all_packages).lower(),
    }
    result = invoke(env, checkout)
    assert result.returncode == exit_code, result.stdout + result.stderr
    args = json.loads(Path(env["TEST_LOG"]).read_text())
    assert args[:3] == ["ci", "update", stage]
    assert ("--validate-all-packages" in args) == (
        validate_all_packages and stage == "prepare"
    )
    previous_flag = "--previous" if stage == "prepare" else "--candidate"
    assert args[args.index(previous_flag) + 1] == env["NIXCFG_PREVIOUS_CANDIDATE"]
    if stage == "prepare":
        assert args[-3:] == ["--", "alpha", "beta"]
    artifacts = Path(env["RUNNER_TEMP"]) / "update-artifacts"
    assert json.loads((artifacts / "result.json").read_bytes()) == {
        "success": exit_code == 0
    }
    stderr_log = (artifacts / "stderr.log").read_text()
    assert "Starting updater stage=" + stage in stderr_log
    assert "diagnostic evidence\n" in stderr_log
    assert "diagnostic evidence\n" in result.stderr
    assert "source failure detail\n" in stderr_log
    assert "source failure detail\n" in result.stderr
    assert (artifacts / "runs/test-run/output.log").read_text() == (
        "source failure detail\n"
    )
    if exit_code:
        assert (
            "Updater failed; inspect the retained result and run-log artifacts.\n"
            in result.stderr
        )
        assert "Collected 0 prefetched store paths" in result.stderr
    else:
        assert "Updater failed" not in result.stderr
    assert (checkout / "flake.lock").read_text() == "baseline"


def test_closure_shard_forwards_named_roots_and_budget(native_job) -> None:
    """An always-run root shard records a receipt, not a continuation output."""
    env, checkout = native_job
    env |= {
        "GITHUB_OUTPUT": str(Path(env["RUNNER_TEMP"]) / "github-output"),
        "NIXCFG_CI_STAGE": "validate",
        "NIXCFG_PREVIOUS_CANDIDATE": "/candidate from previous job.json",
        "NIXCFG_VALIDATE_SCOPE": "closure-shard",
        "NIXCFG_CLOSURE_BUDGET_SECONDS": "18000",
        "NIXCFG_CLOSURE_ROOTS": "darwin-argus",
        "NIXCFG_CLOSURE_SHARD": "darwin-argus",
    }
    result = invoke(env, checkout)
    assert result.returncode == 0, result.stderr
    assert not Path(env["GITHUB_OUTPUT"]).exists() or (
        "closure_complete" not in Path(env["GITHUB_OUTPUT"]).read_text()
    )
    args = json.loads(Path(env["TEST_LOG"]).read_text())
    assert args[args.index("--scope") + 1] == "closure-shard"
    assert args[args.index("--closure-budget-seconds") + 1] == "18000"
    assert args[args.index("--closure-roots") + 1] == "darwin-argus"
    assert args[args.index("--shard") + 1] == "darwin-argus"
    artifacts = Path(env["RUNNER_TEMP"]) / "update-artifacts"
    assert (artifacts / "shard-receipt.json").is_file()
    assert not (artifacts / "validation.json").exists()


@pytest.mark.parametrize("exit_code", [0, 1])
def test_finished_closure_aggregate_does_not_emit_continuation(
    native_job, exit_code: int
) -> None:
    """The aggregate closures gate fails closed and writes certify evidence."""
    env, checkout = native_job
    env |= {
        "GITHUB_OUTPUT": str(Path(env["RUNNER_TEMP"]) / "github-output"),
        "NIXCFG_CI_STAGE": "validate",
        "NIXCFG_PREVIOUS_CANDIDATE": "/candidate from previous job.json",
        "NIXCFG_VALIDATE_SCOPE": "closures",
        "NIXCFG_CLOSURE_BUDGET_SECONDS": "18000",
        "TEST_EXIT": str(exit_code),
    }
    result = invoke(env, checkout)
    assert result.returncode == exit_code, result.stderr
    assert "--closure-yield" not in json.loads(Path(env["TEST_LOG"]).read_text())
    artifacts = Path(env["RUNNER_TEMP"]) / "update-artifacts"
    assert (artifacts / "validation.json").is_file()


def test_native_adapter_rejects_a_miswired_closure_shard(
    native_job, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Continuation is only valid for a closure scope with a finite positive budget."""
    env, checkout = native_job
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(checkout)
    monkeypatch.setenv("NIXCFG_PREVIOUS_CANDIDATE", "/previous candidate")
    monkeypatch.setenv("NIXCFG_VALIDATE_SCOPE", "nope")
    with pytest.raises(ValueError, match="scope"):
        jobs.main("native-validate")
    monkeypatch.setenv("NIXCFG_VALIDATE_SCOPE", "packages")
    monkeypatch.setenv("NIXCFG_CLOSURE_BUDGET_SECONDS", "18000")
    with pytest.raises(ValueError, match="budget"):
        jobs.main("native-validate")
    monkeypatch.setenv("NIXCFG_VALIDATE_SCOPE", "closures")
    monkeypatch.setenv("NIXCFG_CLOSURE_BUDGET_SECONDS", "nope")
    with pytest.raises(ValueError, match="budget"):
        jobs.main("native-validate")
    monkeypatch.setenv("NIXCFG_CLOSURE_BUDGET_SECONDS", "inf")
    with pytest.raises(ValueError, match="budget"):
        jobs.main("native-validate")
    monkeypatch.setenv("NIXCFG_CLOSURE_BUDGET_SECONDS", "0")
    with pytest.raises(ValueError, match="budget"):
        jobs.main("native-validate")
    monkeypatch.setenv("NIXCFG_CLOSURE_BUDGET_SECONDS", "")
    monkeypatch.setenv("NIXCFG_VALIDATE_SCOPE", "closure-shard")
    with pytest.raises(ValueError, match="closure-shard"):
        jobs.main("native-validate")
    monkeypatch.setenv("NIXCFG_CLOSURE_ROOTS", "darwin-argus")
    with pytest.raises(ValueError, match="closure-shard"):
        jobs.main("native-validate")
    monkeypatch.setenv("NIXCFG_VALIDATE_SCOPE", "closures")
    monkeypatch.setenv("NIXCFG_CLOSURE_ROOTS", "darwin-argus")
    monkeypatch.setenv("NIXCFG_CLOSURE_SHARD", "darwin-argus")
    with pytest.raises(ValueError, match="Named closure roots"):
        jobs.main("native-validate")
    monkeypatch.delenv("NIXCFG_CLOSURE_ROOTS")
    monkeypatch.delenv("NIXCFG_CLOSURE_SHARD")
    monkeypatch.setenv("NIXCFG_CLOSURE_BUDGET_SECONDS", "18000")
    monkeypatch.setenv("TEST_EXIT", "0")
    assert jobs.main("native-validate") == 0


def test_native_job_forwards_diagnostics_before_the_updater_exits(native_job) -> None:
    env, checkout = native_job
    process = subprocess.Popen(  # noqa: S603 -- tests the Actions entrypoint process boundary.
        [sys.executable, str(SCRIPT)],
        cwd=checkout,
        env=env | {"TEST_QUIET_SLEEP_SECONDS": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stderr is not None
    assert "Starting native stage=prepare" in process.stderr.readline()
    assert "Starting updater stage=prepare" in process.stderr.readline()
    assert process.stderr.readline() == "diagnostic evidence\n"
    stdout, stderr = process.communicate()
    assert process.returncode == 0, stdout + stderr
    artifacts = Path(env["RUNNER_TEMP"]) / "update-artifacts"
    assert "diagnostic evidence\n" in (artifacts / "stderr.log").read_text()


def test_native_job_heartbeats_while_the_updater_is_quiet(
    native_job, monkeypatch, capsys
) -> None:
    env, checkout = native_job
    # Wait for the fallback heartbeat itself. A fixed quiet sleep races the
    # first output.log drain on a loaded Darwin quality runner.
    monkeypatch.setattr(jobs, "_HEARTBEAT_INTERVAL_SECONDS", 0.05)
    monkeypatch.chdir(checkout)
    stop = Path(env["RUNNER_TEMP"]) / "stop-updater"
    for key, value in (env | {"TEST_STOP_FILE": str(stop)}).items():
        monkeypatch.setenv(key, value)
    artifacts = Path(env["RUNNER_TEMP"]) / "update-artifacts"
    stderr_log = artifacts / "stderr.log"
    result: list[int] = []

    def run_native() -> None:
        result.append(jobs.native("prepare"))

    worker = threading.Thread(target=run_native, name="native-quiet-heartbeat")
    worker.start()
    try:
        _wait_for_log_text(stderr_log, "Updater still running stage=prepare pid=")
    finally:
        stop.touch()
        worker.join(timeout=5)
    assert not worker.is_alive()
    assert result == [0]
    captured = capsys.readouterr()
    log_text = stderr_log.read_text()
    assert "Starting updater stage=prepare pid=" in captured.err
    assert "source failure detail\n" in captured.err
    assert "Updater still running stage=prepare pid=" in captured.err
    assert "Updater still running stage=prepare pid=" in log_text
    assert json.loads((artifacts / "result.json").read_bytes()) == {"success": True}


def test_native_job_forwards_run_logs_instead_of_quiet_heartbeats(
    native_job, monkeypatch, capsys
) -> None:
    """Growing output.log is the live job log; a meta heartbeat is only a fallback."""
    env, checkout = native_job
    monkeypatch.setattr(jobs, "_HEARTBEAT_INTERVAL_SECONDS", 0.01)
    monkeypatch.chdir(checkout)
    for key, value in (
        env
        | {
            "TEST_QUIET_SLEEP_SECONDS": "0.08",
            "TEST_APPEND_RUN_LOG": "1",
        }
    ).items():
        monkeypatch.setenv(key, value)
    assert jobs.native("prepare") == 0
    captured = capsys.readouterr()
    artifacts = Path(env["RUNNER_TEMP"]) / "update-artifacts"
    stderr_log = (artifacts / "stderr.log").read_text()
    assert "live build line 0\n" in captured.err
    assert "live build line 0\n" in stderr_log


def test_native_job_heartbeats_while_publishing_prefetched_paths(
    native_job, monkeypatch, capsys
) -> None:
    env, checkout = native_job
    monkeypatch.setattr(jobs, "_HEARTBEAT_INTERVAL_SECONDS", 0.01)
    monkeypatch.chdir(checkout)
    for key, value in (
        env
        | {
            "TEST_PREFETCH_RECEIPTS": '{"storePath": "/nix/store/new.zip"}\n',
            "TEST_CACHE_SLEEP_SECONDS": "0.05",
        }
    ).items():
        monkeypatch.setenv(key, value)
    assert jobs.native("prepare") == 0
    captured = capsys.readouterr()
    artifacts = Path(env["RUNNER_TEMP"]) / "update-artifacts"
    stderr_log = (artifacts / "stderr.log").read_text()
    assert "Cachix publication still running pid=" in captured.err
    assert "Cachix publication still running pid=" in stderr_log


def test_failure_summary_names_failed_sources(tmp_path: Path) -> None:
    result = tmp_path / "result.json"
    result.write_text(json.dumps({"success": False, "errors": ["ara", "buzz"]}))
    assert jobs._failure_summary(result) == (
        "Updater failed for: ara, buzz. Inspect the retained result and run-log artifacts."
    )


@pytest.mark.parametrize("content", [None, "incomplete JSON", "[]"])
def test_failure_summary_tolerates_missing_or_incomplete_result(
    tmp_path: Path, content: str | None
) -> None:
    """A child crash must retain its diagnostics even without a valid result."""
    result = tmp_path / "result.json"
    if content is not None:
        result.write_text(content)
    assert jobs._failure_summary(result) is None


def test_wait_for_diagnostics_prefers_run_logs_to_heartbeat(
    tmp_path, monkeypatch
) -> None:
    """A growing output.log is forwarded instead of the meta liveness line."""
    monkeypatch.setattr(jobs, "_HEARTBEAT_INTERVAL_SECONDS", 0.1)
    output = tmp_path / "runs" / "test-run" / "output.log"
    output.parent.mkdir(parents=True)
    output.write_text("live 0\n")
    stop = threading.Event()

    def writer() -> None:
        n = 1
        # Tight appends so a loaded Darwin quality runner cannot insert a
        # heartbeat-sized gap between live lines. Production uses 60s; this
        # only needs new bytes before each Empty timeout.
        while not stop.is_set():
            with output.open("a") as handle:
                handle.write(f"live {n}\n")
            n += 1
            stop.wait(0.001)

    writer_thread = threading.Thread(target=writer)
    writer_thread.start()
    command = [sys.executable, "-c", "import time; time.sleep(0.35)"]
    try:
        with subprocess.Popen(command, stderr=subprocess.PIPE, text=True) as process:  # noqa: S603 -- controlled Python fixture.
            log = StringIO()
            assert (
                jobs._wait_for_diagnostics(process, log, "validate", tmp_path, command)
                == 0
            )
    finally:
        stop.set()
        writer_thread.join()
    assert "live 0\n" in log.getvalue()
    assert "Updater still running" not in log.getvalue()


def test_diagnostics_drain_after_child_exit(tmp_path, monkeypatch) -> None:
    """Inherited stderr may outlive the child; retain its final diagnostic."""
    monkeypatch.setattr(jobs, "_HEARTBEAT_INTERVAL_SECONDS", 0.01)
    descendant = (
        "import sys, time; time.sleep(0.2); print('final diagnostic', file=sys.stderr)"
    )
    command = [
        sys.executable,
        "-c",
        f"import subprocess, sys; subprocess.Popen([sys.executable, '-c', {descendant!r}])",
    ]
    with subprocess.Popen(command, stderr=subprocess.PIPE, text=True) as process:  # noqa: S603 -- controlled Python fixture.
        assert process.wait() == 0
        log = StringIO()
        assert (
            jobs._wait_for_diagnostics(process, log, "prepare", tmp_path, command) == 0
        )
    assert "final diagnostic\n" in log.getvalue()
    assert "Updater still running" not in log.getvalue()


def test_run_log_tail_reads_only_new_bytes_and_skips_duplicates(tmp_path: Path) -> None:
    """The live tail follows output.log once, including through latest/."""
    run_logs = tmp_path / "runs"
    run_dir = run_logs / "20260101-run"
    run_dir.mkdir(parents=True)
    output = run_dir / "output.log"
    output.write_text("one\n")
    alias = run_logs / "alias"
    alias.mkdir()
    (alias / "output.log").symlink_to(output)
    (run_logs / "latest").symlink_to(run_dir.name)
    (run_logs / "output.log").mkdir()
    offsets: dict[str, int] = {}
    assert jobs._read_new_run_log_text(run_logs, offsets) == "one\n"
    assert jobs._read_new_run_log_text(run_logs, offsets) == ""
    output.write_text("one\ntwo\n")
    assert jobs._read_new_run_log_text(run_logs, offsets) == "two\n"
    output.write_text("short\n")
    assert jobs._read_new_run_log_text(run_logs, offsets) == "short\n"
    assert jobs._read_new_run_log_text(tmp_path / "absent", {}) == ""


def test_run_log_tail_skips_unreadable_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A disappearing run log must not take down the live job log."""
    run_logs = tmp_path / "runs"
    readable = run_logs / "ok"
    unreadable = run_logs / "gone"
    readable.mkdir(parents=True)
    unreadable.mkdir()
    (readable / "output.log").write_text("kept\n")
    (unreadable / "output.log").write_text("lost\n")
    real_read = Path.read_bytes
    real_resolve = Path.resolve

    def read_bytes(self: Path) -> bytes:
        if self.parent.name == "gone":
            raise OSError("gone")
        return real_read(self)

    def resolve(self: Path, **kwargs: object) -> Path:
        if self.parent.name == "gone":
            raise OSError("gone")
        return real_resolve(self, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    assert jobs._read_new_run_log_text(run_logs, {}) == "kept\n"
    monkeypatch.setattr(Path, "read_bytes", real_read)
    monkeypatch.setattr(Path, "resolve", resolve)
    assert jobs._read_new_run_log_text(run_logs, {}) == "kept\n"


def test_flake_lock_has_no_registry_dependent_inputs() -> None:
    """Source refresh must not resolve flake inputs through a runner registry."""
    lock = json.loads((ROOT / "flake.lock").read_text(encoding="utf-8"))
    indirect = {
        name: node["original"]
        for name, node in lock["nodes"].items()
        if node.get("original", {}).get("type") == "indirect"
    }
    assert not indirect


@pytest.mark.parametrize("update_exit", [0, 17])
@pytest.mark.parametrize("cache_exit", [0, 19])
def test_preparation_publishes_only_exact_prefetch_receipts(
    native_job, monkeypatch, update_exit: int, cache_exit: int
) -> None:
    """Only exact direct-prefetch receipts reach the raw-import cache upload."""
    env, checkout = native_job
    for key, value in (
        env
        | {
            "TEST_PREFETCH_RECEIPTS": (
                '{"storePath": "/nix/store/new.zip"}\n'
                '{"storePath": "/nix/store/new.zip"}\n'
            ),
            "TEST_EXIT": str(update_exit),
            "TEST_CACHE_EXIT": str(cache_exit),
        }
    ).items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(checkout)
    if cache_exit and not update_exit:
        with pytest.raises(subprocess.CalledProcessError) as error:
            jobs.native("prepare")
        assert error.value.returncode == cache_exit
    else:
        assert jobs.native("prepare") == update_exit
    log = Path(env["TEST_CACHE_LOG"])
    assert json.loads(log.read_text()) == ["push", "gkze", "/nix/store/new.zip"]


@pytest.mark.parametrize(
    "receipt",
    [
        "not-json\n",
        "{}\n",
        "[]\n",
        '{"storePath": null}\n',
        '{"storePath": "/nix/store/.."}\n',
        '{"storePath": "/nix/store/path/nested"}\n',
        '{"storePath": "relative"}\n',
        '{"storePath": "/tmp/path"}\n',
    ],
)
def test_source_cache_rejects_malformed_or_unsafe_prefetch_receipts(
    tmp_path: Path, receipt: str
) -> None:
    receipts = tmp_path / "prefetch-receipts.jsonl"
    receipts.write_text(receipt)
    with pytest.raises(ValueError, match="[Pp]refetch receipt"):
        jobs._prefetched_paths_from_receipts(receipts)


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


def test_failed_prepare_still_publishes_prefetch_receipts(
    native_job, monkeypatch
) -> None:
    """Prefetch receipts are pushed on the failure path, not only on success."""
    env, checkout = native_job
    for key, value in (
        env
        | {
            "TEST_PREFETCH_RECEIPTS": '{"storePath": "/nix/store/fail.zip"}\n',
            "TEST_EXIT": "17",
        }
    ).items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(checkout)
    assert jobs.native("prepare") == 17
    assert json.loads(Path(env["TEST_CACHE_LOG"]).read_text()) == [
        "push",
        "gkze",
        "/nix/store/fail.zip",
    ]


def test_prefetch_publication_keeps_updater_status_when_both_fail(
    native_job, monkeypatch
) -> None:
    env, checkout = native_job
    for key, value in (
        env
        | {
            "TEST_PREFETCH_RECEIPTS": '{"storePath": "/nix/store/fail.zip"}\n',
            "TEST_EXIT": "17",
            "TEST_CACHE_EXIT": "19",
        }
    ).items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(checkout)
    assert jobs.native("prepare") == 17


def _cachix_daemon_dir(root: Path) -> Path:
    daemon_dir = root / "cachix-daemon"
    daemon_dir.mkdir()
    (daemon_dir / "daemon.sock").write_text("")
    (daemon_dir / "daemon.pid").write_text("123\n")
    (daemon_dir / "nix.conf").write_text("post-build-hook = /hook.sh\n")
    (daemon_dir / "post-build-hook.sh").write_text("#!/bin/sh\n")
    (daemon_dir / "daemon.log").write_text("started\n")
    return daemon_dir


def test_flush_cachix_stops_daemon_and_republishes_receipts(
    native_job, monkeypatch
) -> None:
    env, checkout = native_job
    artifacts = Path(env["RUNNER_TEMP"]) / "update-artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "prefetch-receipts.jsonl").write_text(
        '{"storePath": "/nix/store/flush.zip"}\n'
    )
    daemon_dir = _cachix_daemon_dir(Path(env["RUNNER_TEMP"]))
    github_env = Path(env["RUNNER_TEMP"]) / "github-env"
    github_env.write_text("KEEP=1\n")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("CACHIX_DAEMON_DIR", str(daemon_dir))
    monkeypatch.setenv("CACHIX_DAEMON_SOCKET", str(daemon_dir / "daemon.sock"))
    monkeypatch.setenv("GITHUB_ENV", str(github_env))
    monkeypatch.chdir(checkout)
    assert jobs.main("flush-cachix") == 0
    assert json.loads(Path(env["TEST_CACHE_LOG"]).read_text()) == [
        "daemon",
        "stop",
        "--socket",
        str(daemon_dir / "daemon.sock"),
    ]
    flush_log = (artifacts / "cachix-flush.log").read_text()
    assert "Collected 1 prefetched store paths" in flush_log
    assert "cachix daemon stop returncode=0" in flush_log
    assert (artifacts / "cachix-daemon" / "daemon.log").read_text() == "started\n"
    assert not (daemon_dir / "daemon.sock").exists()
    assert not (daemon_dir / "daemon.pid").exists()
    assert "CACHIX_DAEMON_DIR" not in os.environ
    assert "CACHIX_DAEMON_SOCKET" not in os.environ
    written = github_env.read_text()
    assert "CACHIX_DAEMON_DIR=\n" in written
    assert "CACHIX_DAEMON_SOCKET=\n" in written
    assert "cleared CACHIX_DAEMON_DIR" in flush_log
    empty = Path(env["RUNNER_TEMP"]) / "empty-flush"
    empty.mkdir()
    monkeypatch.setenv("RUNNER_TEMP", str(empty))
    empty_daemon = _cachix_daemon_dir(empty)
    monkeypatch.setenv("CACHIX_DAEMON_DIR", str(empty_daemon))
    assert jobs.main("flush-cachix") == 0
    assert (
        "Flushing Cachix daemon"
        in (empty / "update-artifacts" / "cachix-flush.log").read_text()
    )


def test_flush_cachix_fails_closed_without_action_socket(
    native_job, monkeypatch
) -> None:
    """Bare `cachix daemon stop` hitting ~/.cache is not a drain."""
    env, checkout = native_job
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(checkout)
    monkeypatch.delenv("CACHIX_DAEMON_DIR", raising=False)
    monkeypatch.delenv("CACHIX_DAEMON_SOCKET", raising=False)
    with pytest.raises(jobs.CachixFlushError, match="socket is unknown"):
        jobs.main("flush-cachix")
    flush_log = (
        Path(env["RUNNER_TEMP"]) / "update-artifacts" / "cachix-flush.log"
    ).read_text()
    assert "socket is unknown" in flush_log


def test_flush_cachix_fails_closed_when_socket_is_missing_or_stop_fails(
    native_job, monkeypatch
) -> None:
    env, checkout = native_job
    daemon_dir = Path(env["RUNNER_TEMP"]) / "missing-socket"
    daemon_dir.mkdir(parents=True)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("CACHIX_DAEMON_DIR", str(daemon_dir))
    monkeypatch.chdir(checkout)
    with pytest.raises(jobs.CachixFlushError, match="socket missing"):
        jobs.main("flush-cachix")
    (daemon_dir / "daemon.sock").write_text("")
    monkeypatch.setenv("TEST_CACHE_EXIT", "1")
    with pytest.raises(jobs.CachixFlushError, match="stop failed"):
        jobs.main("flush-cachix")


def test_cachix_daemon_socket_reads_action_env_only(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("CACHIX_DAEMON_DIR", raising=False)
    monkeypatch.delenv("CACHIX_DAEMON_SOCKET", raising=False)
    assert jobs.cachix_daemon_socket() is None
    monkeypatch.setenv("CACHIX_DAEMON_DIR", str(tmp_path / "daemon"))
    assert jobs.cachix_daemon_socket() == tmp_path / "daemon" / "daemon.sock"
    monkeypatch.setenv("CACHIX_DAEMON_SOCKET", str(tmp_path / "explicit.sock"))
    assert jobs.cachix_daemon_socket() == tmp_path / "explicit.sock"


def test_cachix_daemon_helpers_tolerate_missing_or_busy_paths(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("CACHIX_DAEMON_DIR", raising=False)
    jobs._release_cachix_daemon_dir()
    monkeypatch.setenv("CACHIX_DAEMON_DIR", str(tmp_path / "absent"))
    jobs._retain_cachix_daemon_evidence(tmp_path / "artifacts", StringIO())
    jobs._release_cachix_daemon_dir()
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "daemon.sock").mkdir()
    (busy / "daemon.pid").mkdir()
    monkeypatch.setenv("CACHIX_DAEMON_DIR", str(busy))
    github_env = tmp_path / "github-env"
    github_env.write_text("")
    monkeypatch.setenv("GITHUB_ENV", str(github_env))
    jobs._release_cachix_daemon_dir()
    assert (busy / "daemon.sock").is_dir()
    assert "CACHIX_DAEMON_DIR=\n" in github_env.read_text()
    assert "CACHIX_DAEMON_DIR" not in os.environ


def test_clear_cachix_daemon_env_is_the_action_post_hook_skip(
    tmp_path: Path, monkeypatch
) -> None:
    """Unlinking pid/socket is not a skip; empty CACHIX_DAEMON_* in GITHUB_ENV is.

    cachix-action's post hook reads ``$CACHIX_DAEMON_DIR/daemon.pid`` and
    throws if the file is gone. A missing socket fails ``daemon stop``
    after ~30s (cachix#726). The skip path is ``if (!daemonDir)``.
    """
    daemon_dir = tmp_path / "cachixXXXX"
    daemon_dir.mkdir()
    socket = daemon_dir / "daemon.sock"
    socket.write_text("")
    (daemon_dir / "daemon.pid").write_text("123\n")
    github_env = tmp_path / "github-env"
    github_env.write_text("KEEP=1\n")
    monkeypatch.setenv("CACHIX_DAEMON_DIR", str(daemon_dir))
    monkeypatch.setenv("CACHIX_DAEMON_SOCKET", str(socket))
    monkeypatch.setenv("GITHUB_ENV", str(github_env))
    jobs._clear_cachix_daemon_env()
    assert "CACHIX_DAEMON_DIR" not in os.environ
    assert "CACHIX_DAEMON_SOCKET" not in os.environ
    written = github_env.read_text()
    assert written.startswith("KEEP=1\n")
    assert "CACHIX_DAEMON_DIR=\n" in written
    assert "CACHIX_DAEMON_SOCKET=\n" in written
    assert socket.exists()
    assert (daemon_dir / "daemon.pid").exists()


def test_record_runner_storage_writes_df_inodes_and_store_bytes(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    snapshot = jobs.record_runner_storage("phase")
    assert snapshot["label"] == "phase"
    mounts = snapshot["mounts"]
    assert isinstance(mounts, dict)
    assert "/" in mounts
    assert {"total", "used", "free"} <= set(mounts["/"])
    lines = (tmp_path / "update-artifacts" / "storage.jsonl").read_text().splitlines()
    assert json.loads(lines[-1])["label"] == "phase"
    captured = capsys.readouterr()
    assert "storage phase free=" in captured.err
    assert "Filesystem" in snapshot["df_h"] or snapshot["df_h"] == ""
    monkeypatch.setattr(jobs.shutil, "which", lambda _name: None)
    empty = jobs.record_runner_storage("no-df", live=False)
    assert empty["df_h"] == ""
    assert empty["df_i"] == ""
    blocked = tmp_path / "update-artifacts" / "storage.jsonl"
    blocked.unlink(missing_ok=True)
    blocked.mkdir()
    jobs.record_runner_storage("blocked-jsonl", live=False)
    real_exists = Path.exists

    def exists(self: Path) -> bool:
        return str(self) not in {"/nix", "/nix/store"} and real_exists(self)

    monkeypatch.setattr(Path, "exists", exists)
    missing = jobs.record_runner_storage("no-nix", live=False)
    assert "/nix" not in missing["mounts"]
    assert "/" in missing["mounts"]


def test_plan_shards_and_coverage_native_stages(native_job, monkeypatch) -> None:
    env, checkout = native_job
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(checkout)
    with pytest.raises(ValueError, match="previous candidate"):
        jobs.native("plan-shards")
    with pytest.raises(ValueError, match="Coverage requires a previous"):
        jobs.native("assert-coverage")
    monkeypatch.setenv("NIXCFG_PREVIOUS_CANDIDATE", "/candidate.json")
    with pytest.raises(ValueError, match="NIXCFG_COVERAGE_EVIDENCE"):
        jobs.native("assert-coverage")
    monkeypatch.setenv("NIXCFG_COVERAGE_EVIDENCE", "/evidence")
    monkeypatch.setenv("NIXCFG_JOB_RESULTS", "validate-arm=success")
    monkeypatch.setenv("GITHUB_OUTPUT", str(Path(env["RUNNER_TEMP"]) / "github-output"))
    assert jobs.native("plan-shards") == 0
    args = json.loads(Path(env["TEST_LOG"]).read_text())
    assert args[:3] == ["ci", "update", "plan-shards"]
    assert "--github-output" in args
    artifacts = Path(env["RUNNER_TEMP"]) / "update-artifacts"
    coverage_args = jobs._native_args("assert-coverage", artifacts)
    assert coverage_args[1:4] == ["ci", "update", "assert-coverage"]
    assert coverage_args[coverage_args.index("--evidence") + 1] == "/evidence"
    monkeypatch.setenv("NIXCFG_VALIDATE_SCOPE", "closure-shard")
    monkeypatch.setenv("NIXCFG_CLOSURE_BUDGET_SECONDS", "18000")
    monkeypatch.setenv("NIXCFG_CLOSURE_ROOTS", "darwin-argus")
    monkeypatch.setenv("NIXCFG_CLOSURE_SHARD", "darwin-argus")
    shard_args = jobs._native_args("validate", artifacts)
    assert shard_args[shard_args.index("--closure-roots") + 1] == "darwin-argus"
    assert shard_args[shard_args.index("--shard") + 1] == "darwin-argus"
    monkeypatch.delenv("NIXCFG_PREVIOUS_CANDIDATE")
    with pytest.raises(ValueError, match="Unknown native stage"):
        jobs._native_args("unexpected", artifacts)


def test_successful_prepare_rejects_malformed_prefetch_receipts(
    native_job, monkeypatch
) -> None:
    env, checkout = native_job
    for key, value in (
        env
        | {
            "TEST_PREFETCH_RECEIPTS": "not-json\n",
            "TEST_EXIT": "0",
        }
    ).items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(checkout)
    with pytest.raises(ValueError, match="[Pp]refetch receipt"):
        jobs.native("prepare")


def test_malformed_prefetch_receipts_keep_updater_failure(
    native_job, monkeypatch
) -> None:
    env, checkout = native_job
    for key, value in (
        env
        | {
            "TEST_PREFETCH_RECEIPTS": "not-json\n",
            "TEST_EXIT": "17",
        }
    ).items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(checkout)
    assert jobs.native("prepare") == 17


def test_native_job_rejects_unknown_stage(native_job, monkeypatch) -> None:
    env, checkout = native_job
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(checkout)
    monkeypatch.setenv("NIXCFG_PREVIOUS_CANDIDATE", "/previous candidate")
    with pytest.raises(ValueError, match="Unknown native stage"):
        jobs.native("certify")


@pytest.mark.parametrize("stage", ["invalid", "validate", "cache-root-deps"])
def test_job_rejects_unknown_stage_or_missing_candidate(native_job, stage: str) -> None:
    env, checkout = native_job
    assert invoke(env | {"NIXCFG_CI_STAGE": stage}, checkout).returncode != 0
    assert not Path(env["TEST_LOG"]).exists()


def test_repair_validation_mode_reaches_first_native_preparation() -> None:
    """Explicit dispatch scope enters the candidate once; later stages inherit it."""
    workflow = yaml.load(
        (ROOT / ".github/workflows/update.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert (
        workflow["on"]["workflow_dispatch"]["inputs"]["validate_all_packages"][
            "default"
        ]
        == "false"
    )
    assert (
        " ".join(
            workflow["jobs"]["prepare-darwin"]["with"]["validate_all_packages"].split()
        )
        == "${{ github.event_name == 'push' || inputs.validate_all_packages || false }}"
    )
    native = yaml.load(
        (ROOT / ".github/workflows/update-native.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    assert (
        native["on"]["workflow_call"]["inputs"]["validate_all_packages"]["default"]
        == "false"
    )
    step = next(
        step
        for step in native["jobs"]["native"]["steps"]
        if step.get("name") == "Prepare or validate candidate"
    )
    assert (
        step["env"]["NIXCFG_VALIDATE_ALL_PACKAGES"]
        == "${{ inputs.validate_all_packages }}"
    )
    assert step["id"] == "candidate"
    assert step["env"]["NIXCFG_VALIDATE_SCOPE"] == "${{ inputs.scope }}"
    assert (
        step["env"]["NIXCFG_CLOSURE_BUDGET_SECONDS"]
        == "${{ inputs.closure_budget_seconds }}"
    )
    assert step["env"]["NIXCFG_CLOSURE_ROOTS"] == "${{ inputs.closure_roots }}"
    assert step["env"]["NIXCFG_CLOSURE_SHARD"] == "${{ inputs.shard }}"
    assert "NIXCFG_CLOSURE_YIELD" not in step["env"]
    assert native["on"]["workflow_call"]["inputs"]["scope"]["default"] == "all"
    assert "closure_yield" not in native["on"]["workflow_call"]["inputs"]
    assert "closure_complete" not in native["on"]["workflow_call"]["outputs"]
    assert (
        native["on"]["workflow_call"]["outputs"]["darwin_closure_shards"]["value"]
        == "${{ jobs.native.outputs.darwin_closure_shards }}"
    )
    flush = next(
        step
        for step in native["jobs"]["native"]["steps"]
        if step.get("name") == "Flush Cachix daemon"
    )
    assert flush["if"] == "always()"
    assert flush["env"]["NIXCFG_CI_STAGE"] == "flush-cachix"
    assert native["jobs"]["native"]["steps"][-1] is flush
    dump = next(
        step
        for step in native["jobs"]["native"]["steps"]
        if step.get("name") == "Dump hosted storage-fault evidence"
    )
    assert dump["if"] == "failure()"
    assert dump["env"]["NIXCFG_CI_STAGE"] == "dump-storage-fault"
    names = [step.get("name") for step in native["jobs"]["native"]["steps"]]
    assert names.index("Dump hosted storage-fault evidence") < names.index(
        "Retain candidate and failure evidence"
    )
    upload = next(
        step
        for step in native["jobs"]["native"]["steps"]
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    )
    artifact = " ".join(upload["with"]["name"].split())
    assert "inputs.scope == 'all'" in artifact
    assert "format('{0}-{1}', inputs.stage, inputs.system)" in artifact
    assert (
        "format('{0}-{1}-{2}', inputs.stage, inputs.system, inputs.scope)" in artifact
    )
    assert "inputs.shard" in artifact


def _assert_darwin_closure_shards(workflow_jobs: dict) -> None:
    """Darwin packages own zed; always-run root shards then the aggregate."""
    packages = workflow_jobs["validate-darwin-packages"]
    assert packages["needs"] == "prepare-x86"
    assert packages["with"]["scope"] == "packages"
    plan = workflow_jobs["plan-darwin-closures"]
    assert plan["needs"] == "prepare-x86"
    assert plan["with"]["stage"] == "plan-shards"
    assert plan["with"]["runner"] == "ubuntu-24.04"
    roots = workflow_jobs["validate-darwin-roots"]
    assert set(roots["needs"]) == {
        "plan-darwin-closures",
        "prepare-x86",
        "cache-darwin-linux-deps-arm",
        "cache-darwin-linux-deps-x86",
        "validate-darwin-packages",
    }
    roots_if = " ".join(roots["if"].split())
    assert "always() && !cancelled()" in roots_if
    assert "needs.plan-darwin-closures.result == 'success'" in roots_if
    assert "validate-darwin-packages.result" not in roots_if
    assert "closure_complete" not in roots_if
    assert roots["strategy"]["fail-fast"] == "false"
    assert (
        roots["strategy"]["matrix"]
        == "${{ fromJSON(needs.plan-darwin-closures.outputs.darwin_closure_shards) }}"
    )
    assert roots["with"]["scope"] == "closure-shard"
    assert roots["with"]["closure_budget_seconds"] == str(
        pipeline.HOSTED_DARWIN_CLOSURE_BUILD_BUDGET_SECONDS
    )
    assert roots["with"]["closure_roots"] == "${{ matrix.roots }}"
    assert roots["with"]["shard"] == "${{ matrix.shard }}"
    assert "closure_yield" not in roots["with"]
    closures = workflow_jobs["validate-darwin-closures"]
    assert set(closures["needs"]) == {
        "prepare-x86",
        "cache-darwin-linux-deps-arm",
        "cache-darwin-linux-deps-x86",
        "validate-darwin-roots",
    }
    assert closures["with"]["scope"] == "closures"
    assert "closure_yield" not in closures["with"]
    coverage = workflow_jobs["assert-coverage"]
    assert coverage["if"] == "always()"
    assert set(coverage["needs"]) == {
        "prepare-x86",
        "plan-darwin-closures",
        "validate-arm",
        "validate-x86",
        "validate-darwin-packages",
        "validate-darwin-roots",
        "validate-darwin-closures",
    }
    evidence = next(
        step
        for step in coverage["steps"]
        if str(step.get("uses", "")).startswith("actions/download-artifact@")
        and step.get("with", {}).get("path") == "${{ runner.temp }}/evidence"
    )
    assert "name" not in evidence["with"]
    publish_if = " ".join(workflow_jobs["publish"]["if"].split())
    assert "always() && !cancelled()" in publish_if
    assert "closure_complete" not in publish_if
    for name in (
        "validate-arm",
        "validate-x86",
        "validate-darwin-packages",
        "validate-darwin-roots",
        "validate-darwin-closures",
        "assert-coverage",
    ):
        assert f"needs.{name}.result == 'success'" in publish_if


def test_cachix_flush_proof_requires_partial_presence_after_designed_failure() -> None:
    """A unique path must reach gkze even when the builder exits 1.

    Job-level continue-on-error hid the #1250 post-hook throw. Only the
    designed fail step may continue; a post-hook failure must fail the job.
    """
    workflow = yaml.load(
        (ROOT / ".github/workflows/cachix-flush-proof.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    fail = workflow["jobs"]["fail-after-push"]
    assert "continue-on-error" not in fail
    designed = next(
        step
        for step in fail["steps"]
        if step.get("name") == "Realize a unique path and fail"
    )
    assert designed["continue-on-error"] == "true"
    for step in fail["steps"]:
        if step.get("name") == "Realize a unique path and fail":
            continue
        assert "continue-on-error" not in step, step.get("name")
    names = [step.get("name") for step in fail["steps"]]
    assert "Require Cachix daemon socket" in names
    assert "Realize a post-failure path" in names
    assert fail["steps"][-1].get("name") == "Flush Cachix daemon"
    assert fail["steps"][-1].get("if") == "always()"
    post = next(
        step
        for step in fail["steps"]
        if step.get("name") == "Realize a post-failure path"
    )
    assert post["if"] == "always()"
    upload = next(
        step
        for step in fail["steps"]
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    )
    assert "proof-path-after.txt" in upload["with"]["path"]
    assert workflow["jobs"]["assert-partial"]["if"] == "always()"
    proof_if = " ".join(workflow["jobs"]["proof"]["if"].split())
    assert "always() && !cancelled()" in proof_if
    assert_source = next(
        step
        for step in workflow["jobs"]["assert-partial"]["steps"]
        if step.get("name") == "Require the failed job's path in Cachix"
    )["run"]
    assert "proof-path-after.txt" in assert_source
    assert "after-failure" in assert_source
    assert workflow["jobs"]["assert-partial"]["steps"][-1]["name"] == (
        "Flush Cachix daemon"
    )


def _uses_update_runtime_with_cachix(steps: list[object]) -> bool:
    for step in steps:
        if not isinstance(step, dict):
            continue
        uses = str(step.get("uses", ""))
        if not uses.startswith("./.github/actions/update-runtime"):
            continue
        with_ = step.get("with")
        if isinstance(with_, dict) and "cachix-token" in with_:
            return True
    return False


def test_cachix_flush_is_last_step_of_every_update_runtime_cachix_job() -> None:
    """Stopping the daemon before certify/repair/builds drops paths from gkze."""
    workflows = sorted((ROOT / ".github/workflows").glob("*.yml"))
    checked: list[str] = []
    for path in workflows:
        workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        for name, job in (workflow.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            steps = job.get("steps") or []
            if not _uses_update_runtime_with_cachix(steps):
                continue
            last = steps[-1]
            assert isinstance(last, dict)
            env = last.get("env") if isinstance(last.get("env"), dict) else {}
            assert last.get("name") == "Flush Cachix daemon", (
                f"{path.name} job {name} last step is "
                f"{last.get('name') or last.get('uses')!r}"
            )
            assert env.get("NIXCFG_CI_STAGE") == "flush-cachix"
            assert last.get("if") == "always()"
            checked.append(f"{path.name}:{name}")
    assert sorted(checked) == [
        "cachix-flush-proof.yml:assert-partial",
        "cachix-flush-proof.yml:fail-after-push",
        "update-native.yml:native",
        "update.yml:assert-coverage",
        "update.yml:publish",
        "update.yml:repair",
    ]


def test_workflow_builds_linux_dependencies_before_darwin_roots() -> None:
    """VM cache overlaps later prepare; Darwin closures no longer wait on Linux validate."""
    workflow = yaml.load(
        (ROOT / ".github/workflows/update.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert set(workflow["on"]) == {"workflow_dispatch", "schedule", "push"}
    assert workflow["concurrency"]["group"] == "nixcfg-update-${{ github.ref }}"
    assert (
        workflow["concurrency"]["cancel-in-progress"]
        == "${{ github.ref_name != github.event.repository.default_branch }}"
    )
    assert workflow["on"]["push"]["branches"] == [
        "main",
        "cursor/no-skip-darwin-shards-6614",
    ]
    assert workflow["on"]["push"]["paths"] == [".github/update-kick"]
    assert workflow["permissions"] == {"contents": "read"}
    assert (
        "github.event_name == 'push'"
        in (workflow["jobs"]["prepare-darwin"]["with"]["validate_all_packages"])
    )
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
    cache_jobs = {
        name: job
        for name, job in jobs.items()
        if name.startswith("cache-darwin-linux-deps-")
    }
    assert set(cache_jobs) == {
        "cache-darwin-linux-deps-arm",
        "cache-darwin-linux-deps-x86",
    }
    for job in cache_jobs.values():
        assert job["needs"] == "prepare-darwin"
        assert job["with"]["stage"] == "cache-root-deps"
        assert job["with"]["previous"] == "prepare-aarch64-darwin"
    assert (
        cache_jobs["cache-darwin-linux-deps-arm"]["with"]["runner"]
        == "ubuntu-24.04-arm"
    )
    assert cache_jobs["cache-darwin-linux-deps-x86"]["with"]["runner"] == "ubuntu-24.04"
    validators = {
        name: job for name, job in jobs.items() if name.startswith("validate-")
    }
    assert {
        job["with"]["system"]: job["with"]["runner"] for job in validators.values()
    } == {row["system"]: row["runner"] for row in matrix}
    for job in validators.values():
        assert job["with"]["previous"] == "prepare-x86_64-linux"
        assert job["with"]["stage"] == "validate"
    assert jobs["validate-arm"]["needs"] == preparation[-1][0]
    assert jobs["validate-x86"]["needs"] == preparation[-1][0]
    _assert_darwin_closure_shards(jobs)
    assert set(jobs["publish"]["needs"]) == set(validators) | {"assert-coverage"}
    assert set(validators) <= set(jobs["repair"]["needs"])
    assert {"plan-darwin-closures", "assert-coverage"} <= set(jobs["repair"]["needs"])
    assert set(cache_jobs) <= set(jobs["repair"]["needs"])
    assert set(cache_jobs).isdisjoint(jobs["publish"]["needs"])
    assert jobs["repair"]["permissions"] == {
        "actions": "write",
        "contents": "write",
        "copilot-requests": "write",
    }
    agent = next(
        step
        for step in jobs["repair"]["steps"]
        if step.get("name") == "Propose one repair"
    )
    assert agent["env"]["GITHUB_TOKEN"] == "${{ github.token }}"  # noqa: S105 -- Actions expression, not a credential.
    assert agent["env"]["COPILOT_MODEL"] == "gpt-6-astra"
    assert not {"COPILOT_GITHUB_TOKEN", "GH_TOKEN"} & agent["env"].keys()
    assert workflow["on"]["workflow_dispatch"]["inputs"]["repair"]["default"] == "true"
    repair_if = " ".join(workflow["jobs"]["repair"]["if"].split())
    assert "inputs.repair == true" in repair_if
    assert "inputs.repair == 'true'" in repair_if
    assert "github.event_name == 'push'" in repair_if
    for stage in ("publish", "start-repair"):
        step = next(
            step
            for job in jobs.values()
            for step in job.get("steps", [])
            if step.get("env", {}).get("NIXCFG_CI_STAGE") == stage
        )
        assert step["env"]["GH_TOKEN"] == "${{ github.token }}"  # noqa: S105 -- Actions expression, not a credential.


def test_agent_check_limits_permissions_and_uses_selected_model() -> None:
    workflow = yaml.load(
        (ROOT / ".github/workflows/update-agent-check.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    assert set(workflow["on"]) == {"push", "workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["authenticate"]
    assert job["permissions"] == {"contents": "read", "copilot-requests": "write"}
    probe = job["steps"][-1]
    assert probe["env"] == {
        "GITHUB_TOKEN": "${{ github.token }}",
        "COPILOT_MODEL": "gpt-6-astra",
        "COPILOT_AUTO_UPDATE": "false",
    }


def _authored_actions_paths() -> list[Path]:
    return [
        *sorted(ROOT.glob(".github/workflows/*.yml")),
        ROOT / ".github/actions/update-runtime/action.yml",
    ]


def test_all_authored_actions_commands_are_python() -> None:
    for path in _authored_actions_paths():
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
                    ast.parse(step["run"], filename=str(path), feature_version=(3, 12))


def test_authored_actions_yaml_has_explicit_document_start() -> None:
    """Yamlfmt include_document_start: true; publish certify fails without ---."""
    for path in _authored_actions_paths():
        starts = [
            event
            for event in yaml.parse(path.read_text(), Loader=yaml.SafeLoader)
            if isinstance(event, yaml.DocumentStartEvent)
        ]
        assert starts, f"{path} has no YAML document"
        assert all(event.explicit for event in starts), (
            f"{path} is missing an explicit YAML document start"
        )


def test_pr_quality_checks_ruff_format_before_certify_pytest() -> None:
    """Update 37550004735: publish certify failed ruff format after Darwin closures."""
    path = ROOT / ".github/workflows/pr-quality.yml"
    workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    step = next(
        item
        for item in workflow["jobs"]["certify-python"]["steps"]
        if item.get("name") == "Check Python formatting"
    )
    module = ast.parse(step["run"], filename=str(path), feature_version=(3, 12))
    format_args: list[str] = []
    for node in ast.walk(module):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
            and node.args
            and isinstance(node.args[0], ast.List)
        ):
            values = [
                elt.value
                for elt in node.args[0].elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            ]
            if values[:3] == ["uv", "run", "ruff"]:
                format_args = values
                break
    assert format_args == [
        "uv",
        "run",
        "ruff",
        "format",
        "--check",
        "--config",
        "pyproject.toml",
        ".",
    ]


def test_pr_quality_certify_covers_prepare_updater_contracts() -> None:
    """#1234/#1240 pin contracts must run on every PR, not only publish."""
    path = ROOT / ".github/workflows/pr-quality.yml"
    workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    step = next(
        item
        for item in workflow["jobs"]["certify-python"]["steps"]
        if item.get("name") == "Run certify Python contracts"
    )
    module = ast.parse(step["run"], filename=str(path), feature_version=(3, 12))
    pytest_files: list[str] = []
    for node in ast.walk(module):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
            and node.args
            and isinstance(node.args[0], ast.List)
        ):
            values = [
                elt.value
                for elt in node.args[0].elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            ]
            if values[:3] == ["uv", "run", "pytest"]:
                pytest_files = values[3:]
                break
    assert "lib/tests/test_overlay_lane_updaters.py" in pytest_files
    assert "lib/tests/test_ara_package.py" in pytest_files
    assert "packages/linear-cli/updater_test.py" in pytest_files


def test_generator_cache_is_scoped_to_disposable_accelerators() -> None:
    """Downloads/receipts are portable accelerators; credentials and DBOS are not."""
    action = yaml.load(
        (ROOT / ".github/actions/update-runtime/action.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    steps = action["runs"]["steps"]
    cache = next(
        step for step in steps if step.get("uses", "").startswith("actions/cache@")
    )
    assert cache["if"] == "inputs.cache-generators == 'true'"
    assert action["inputs"]["cache-generators"]["default"] == "false"
    assert set(cache["with"]["path"].splitlines()) == {
        "~/.cache/nixcfg/crate2nix-cargo-home/registry",
        "~/.cache/nixcfg/crate2nix-cargo-home/git",
        "~/.cache/nixcfg/generation-receipts",
    }
    prefix = "nixcfg-gen-v1-${{ runner.os }}-${{ runner.arch }}-"
    assert cache["with"]["restore-keys"].strip() == prefix
    assert " ".join(cache["with"]["key"].split()) == (
        "${{ format('nixcfg-gen-v1-{0}-{1}-{2}-{3}-{4}', runner.os, runner.arch, "
        "github.run_id, github.run_attempt, github.job) }}"
    )
    cachix = next(
        step
        for step in steps
        if step.get("uses", "").startswith("cachix/cachix-action@")
    )
    assert cachix["with"]["name"] == jobs._BINARY_CACHE
    assert cachix["with"]["useDaemon"] == "true"
    labels = {
        step["env"]["NIXCFG_STORAGE_LABEL"]: step
        for step in steps
        if step.get("env", {}).get("NIXCFG_CI_STAGE") == "record-storage"
    }
    assert set(labels) == {"after-nix-install", "after-cachix"}
    nix_step = next(
        step
        for step in steps
        if str(step.get("uses", "")).startswith(
            "DeterminateSystems/determinate-nix-action@"
        )
    )
    assert steps.index(nix_step) < steps.index(labels["after-nix-install"])
    assert steps.index(labels["after-nix-install"]) < steps.index(cachix)
    assert steps.index(cachix) < steps.index(labels["after-cachix"])
    native = yaml.load(
        (ROOT / ".github/workflows/update-native.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    setup = next(
        step
        for step in native["jobs"]["native"]["steps"]
        if step.get("id") == "runtime"
    )
    assert setup["with"]["cache-generators"] == "${{ inputs.stage == 'prepare' }}"
    workflow = yaml.load(
        (ROOT / ".github/workflows/update.yml").read_text(), Loader=yaml.BaseLoader
    )
    repair = next(
        step
        for step in workflow["jobs"]["repair"]["steps"]
        if step.get("id") == "runtime"
    )
    assert repair["with"]["cache-generators"] == "true"


@pytest.mark.parametrize(
    "stage", ["prepare", "validate", "cache-root-deps", "plan-shards"]
)
@pytest.mark.parametrize("targets", ["", "alpha beta", "--force", "alpha\nbeta"])
@pytest.mark.parametrize("validate_all_packages", [False, True])
def test_native_adapter_captures_only_cli_output(
    native_job, monkeypatch, stage, targets, validate_all_packages
) -> None:
    env, checkout = native_job
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(checkout)
    monkeypatch.setenv("NIXCFG_UPDATE_TARGETS", targets)
    monkeypatch.setenv(
        "NIXCFG_VALIDATE_ALL_PACKAGES", str(validate_all_packages).lower()
    )
    if stage != "prepare":
        with pytest.raises(ValueError, match="requires a previous"):
            jobs.main(f"native-{stage}")
        monkeypatch.setenv("NIXCFG_PREVIOUS_CANDIDATE", "/previous candidate")
    if stage == "prepare" and (targets.startswith("-") or "\n" in targets):
        with pytest.raises(ValueError, match="space-separated"):
            jobs.main("native-prepare")
    else:
        assert jobs.main(f"native-{stage}") == 0
        args = json.loads(Path(env["TEST_LOG"]).read_text())
        assert ("--validate-all-packages" in args) == (
            validate_all_packages and stage == "prepare"
        )
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
        stdout = "certified-tree\n" if args[:2] == ("git", "write-tree") else ""
        return subprocess.CompletedProcess(
            args,
            int(changed) if args[:2] == ("git", "diff") else 0,
            stdout=stdout,
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
        assert ("git", "fetch", "origin", "main") in calls
        switch = next(
            args for args in calls if args[:3] == ("git", "switch", "--discard-changes")
        )
        assert switch[3] == "-c"
        assert switch[4].startswith("codex/update-")
        assert switch[5] == "origin/main"
        assert (
            "git",
            "restore",
            "--source",
            "certified-tree",
            "--worktree",
            "--staged",
            ".",
        ) in calls
        create = next(args for args in calls if args[:3] == ("gh", "pr", "create"))
        assert create[create.index("--base") + 1] == "main"
        branch = create[create.index("--head") + 1]
        assert calls[-1] == ("gh", "pr", "merge", branch, "--auto", "--squash")
        assert (
            "https://github.com/example/repo/actions/runs/123"
            in (tmp_path / "update-body.md").read_text()
        )
    else:
        assert ("git", "fetch", "origin", "main") not in calls
        assert calls[-1][:3] == ("gh", "workflow", "run")
        assert "repair=false" in calls[-1]
        assert "validate_all_packages=true" in calls[-1]
        assert "targets=example" in calls[-1]


def test_commit_and_push_from_base_requires_a_certified_tree(
    job_repository,
) -> None:
    """A default-branch publication without the certified tree is undefined."""
    with pytest.raises(ValueError, match="certified tree"):
        jobs._commit_and_push("x", "msg", base="main")


def test_publication_from_a_feature_branch_still_targets_default_branch(
    job_repository, monkeypatch
) -> None:
    """Exercise runs must not merge into the reviewed branch; the product PR is on main."""
    monkeypatch.setenv("GITHUB_REF_NAME", "feature")
    calls = []

    def run(*args, capture=False, check=True):
        calls.append(args)
        stdout = "certified-tree\n" if args[:2] == ("git", "write-tree") else ""
        return subprocess.CompletedProcess(args, 0, stdout=stdout)

    monkeypatch.setattr(jobs, "_run", run)
    assert jobs.main("publish") == 0
    assert ("git", "fetch", "origin", "main") in calls
    switch = next(
        args for args in calls if args[:3] == ("git", "switch", "--discard-changes")
    )
    assert switch[-1] == "origin/main"
    create = next(args for args in calls if args[:3] == ("gh", "pr", "create"))
    assert create[create.index("--base") + 1] == "main"
    branch = create[create.index("--head") + 1]
    assert ("gh", "pr", "merge", branch, "--auto", "--squash") in calls


def test_publish_merges_immediately_when_the_pull_request_is_already_clean(
    job_repository, monkeypatch
) -> None:
    """A clean certified PR must merge now; --auto cannot queue without checks."""
    calls: list[tuple[str, ...]] = []

    def run(*args, capture=False, check=True):
        calls.append(args)
        stdout = "certified-tree\n" if args[:2] == ("git", "write-tree") else ""
        if args[:3] == ("gh", "pr", "merge") and "--auto" in args:
            return subprocess.CompletedProcess(
                args,
                1,
                stdout="",
                stderr="GraphQL: Pull request Pull request is in clean status "
                "(enablePullRequestAutoMerge)\n",
            )
        return subprocess.CompletedProcess(args, 0, stdout=stdout)

    monkeypatch.setattr(jobs, "_run", run)
    assert jobs.main("publish") == 0
    merges = [args for args in calls if args[:3] == ("gh", "pr", "merge")]
    assert len(merges) == 2
    assert merges[0][4:6] == ("--auto", "--squash")
    assert merges[1][4:] == ("--squash",)


def test_publish_raises_other_auto_merge_failures(job_repository, monkeypatch) -> None:
    """A real merge failure must still fail publication."""

    def run(*args, capture=False, check=True):
        stdout = "certified-tree\n" if args[:2] == ("git", "write-tree") else ""
        if args[:3] == ("gh", "pr", "merge"):
            return subprocess.CompletedProcess(
                args,
                1,
                stdout="",
                stderr="GraphQL: Protected branch rules not configured\n",
            )
        return subprocess.CompletedProcess(args, 0, stdout=stdout)

    monkeypatch.setattr(jobs, "_run", run)
    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        jobs.main("publish")
    assert exc_info.value.returncode == 1
    assert "Protected branch" in exc_info.value.stderr


def test_bootstrap_and_failure_evidence(job_repository, tmp_path, monkeypatch) -> None:
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
    for stage in ("bootstrap", "collect-evidence", "install-agent", "repair"):
        assert jobs.main(stage) == 0
    outputs = dict(
        line.split("=", 1) for line in (tmp_path / "outputs").read_text().splitlines()
    )
    assert Path(outputs["runtime"]).parent == tmp_path
    assert Path(outputs["devshell"]).parent == tmp_path
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


def _cleanup_image_tree(tmp_path: Path) -> dict[str, Path]:
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
    reclaimable = {
        "android": tmp_path / "Library/Android/sdk",
        "simulators": tmp_path / "Library/Developer/CoreSimulator",
        "system_simulators": tmp_path / "system-core-simulators",
        "device_support": tmp_path / "Library/Developer/Xcode/iOS DeviceSupport",
        "caches": tmp_path / "Library/Caches",
        "hosted": tmp_path / "hostedtoolcache",
        "tool_cache": tmp_path / "runner-tool-cache",
    }
    for path in reclaimable.values():
        path.mkdir(parents=True)
    link = tmp_path / "external-link"
    link.symlink_to(other)
    return {
        "apps": apps,
        "selected": selected,
        "old": old,
        "alias": alias,
        "other": other,
        "unused": unused,
        "link": link,
        **reclaimable,
    }


@pytest.mark.parametrize("system", ["darwin", "linux"])
@pytest.mark.parametrize("active_xcode", ["selected", "missing", "relative"])
def test_cleanup_preserves_active_xcode_aliases_and_unselected_data(
    tmp_path, monkeypatch, system, active_xcode
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setattr(jobs.sys, "platform", system)
    _disable_image_headroom(monkeypatch)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    tree = _cleanup_image_tree(tmp_path)
    monkeypatch.setenv("RUNNER_TOOL_CACHE", str(tree["tool_cache"]))
    monkeypatch.setattr(jobs, "_APPLICATIONS", tree["apps"])
    monkeypatch.setattr(jobs, "_DARWIN_SYSTEM_SIMULATORS", tree["system_simulators"])
    monkeypatch.setattr(
        jobs,
        "_UNUSED_IMAGE_PATHS",
        {system: (tree["unused"], tree["link"], tmp_path / "absent")},
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
            "selected": str(tree["selected"]),
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
    kept_unless_darwin_cleanup = system != "darwin" or rejected
    assert tree["unused"].exists() == rejected
    assert tree["selected"].is_dir()
    assert tree["alias"].is_symlink()
    assert tree["other"].is_dir()
    assert tree["link"].is_symlink()
    assert tree["old"].exists() == kept_unless_darwin_cleanup
    for name in (
        "android",
        "simulators",
        "system_simulators",
        "device_support",
        "caches",
        "hosted",
        "tool_cache",
    ):
        assert tree[name].exists() == kept_unless_darwin_cleanup
    if system == "darwin" and not rejected:
        assert not any(call[:2] == ("xcrun", "simctl") for call in calls)


def test_image_cleanup_skips_xcode_when_free_already_meets_headroom(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setattr(jobs.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    tree = _cleanup_image_tree(tmp_path)
    monkeypatch.setenv("RUNNER_TOOL_CACHE", str(tree["tool_cache"]))
    monkeypatch.setattr(jobs, "_APPLICATIONS", tree["apps"])
    monkeypatch.setattr(jobs, "_DARWIN_SYSTEM_SIMULATORS", tree["system_simulators"])
    monkeypatch.setattr(jobs, "_UNUSED_IMAGE_PATHS", {"darwin": (tree["unused"],)})
    need = jobs.image_headroom_required_bytes()
    monkeypatch.setattr(jobs, "runner_free_bytes", lambda: need)

    def run(*args, capture=False, check=True):
        if args[0] == "sudo":
            assert Path(args[-1]).is_relative_to(tmp_path)
            return subprocess.CompletedProcess(args, 0, stdout="")
        return subprocess.CompletedProcess(args, 0, stdout=str(tree["selected"]))

    monkeypatch.setattr(jobs, "_run", run)
    assert jobs.main("clean-image") == 0
    assert tree["old"].is_dir()
    assert tree["android"].is_dir()
    output = capsys.readouterr().out
    assert "df before image cleanup /:" in output
    assert f"Skipping Xcode/Android reclaim; free {need} bytes" in output
    assert f"already meets headroom {need} bytes" in output


def test_image_cleanup_stops_when_reclaim_crosses_headroom(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setattr(jobs.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    tree = _cleanup_image_tree(tmp_path)
    monkeypatch.setenv("RUNNER_TOOL_CACHE", str(tree["tool_cache"]))
    monkeypatch.setattr(jobs, "_APPLICATIONS", tree["apps"])
    monkeypatch.setattr(jobs, "_DARWIN_SYSTEM_SIMULATORS", tree["system_simulators"])
    monkeypatch.setattr(jobs, "_UNUSED_IMAGE_PATHS", {"darwin": (tree["unused"],)})
    need = jobs.image_headroom_required_bytes()
    free = {"n": 0}
    removed: list[Path] = []

    def current_free() -> int:
        return free["n"]

    def remove(path: Path) -> None:
        removed.append(path)
        free["n"] = need

    monkeypatch.setattr(jobs, "runner_free_bytes", current_free)
    monkeypatch.setattr(jobs, "_remove_unused_image_path", remove)

    def run(*args, capture=False, check=True):
        return subprocess.CompletedProcess(args, 0, stdout=str(tree["selected"]))

    monkeypatch.setattr(jobs, "_run", run)
    assert jobs.main("clean-image") == 0
    assert removed == [tree["old"]]
    assert tree["android"].is_dir()
    output = capsys.readouterr().out
    assert (
        f"Stopped image reclaim; free {need} bytes meets headroom {need} bytes"
        in output
    )


def test_image_cleanup_fails_closed_when_headroom_still_missing(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setattr(jobs.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    tree = _cleanup_image_tree(tmp_path)
    monkeypatch.setenv("RUNNER_TOOL_CACHE", str(tree["tool_cache"]))
    monkeypatch.setattr(jobs, "_APPLICATIONS", tree["apps"])
    monkeypatch.setattr(jobs, "_DARWIN_SYSTEM_SIMULATORS", tree["system_simulators"])
    monkeypatch.setattr(jobs, "_UNUSED_IMAGE_PATHS", {"darwin": (tree["unused"],)})
    monkeypatch.setattr(jobs, "runner_free_bytes", lambda: 0)

    def run(*args, capture=False, check=True):
        if args[0] == "sudo":
            assert Path(args[-1]).is_relative_to(tmp_path)
            return subprocess.CompletedProcess(args, 0, stdout="")
        return subprocess.CompletedProcess(args, 0, stdout=str(tree["selected"]))

    monkeypatch.setattr(jobs, "_run", run)
    with pytest.raises(jobs.ImageCleanupError, match="Image cleanup left 0 bytes free"):
        jobs.main("clean-image")


def test_image_cleanup_reports_elapsed_seconds_per_tree(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setattr(jobs.sys, "platform", "darwin")
    _disable_image_headroom(monkeypatch)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    tree = _cleanup_image_tree(tmp_path)
    monkeypatch.setenv("RUNNER_TOOL_CACHE", str(tree["tool_cache"]))
    monkeypatch.setattr(jobs, "_APPLICATIONS", tree["apps"])
    monkeypatch.setattr(jobs, "_DARWIN_SYSTEM_SIMULATORS", tree["system_simulators"])
    monkeypatch.setattr(jobs, "_UNUSED_IMAGE_PATHS", {"darwin": (tree["unused"],)})

    def run(*args, capture=False, check=True):
        if args[0] == "sudo":
            assert Path(args[-1]).is_relative_to(tmp_path)
            return subprocess.CompletedProcess(args, 0, stdout="")
        return subprocess.CompletedProcess(args, 0, stdout=str(tree["selected"]))

    monkeypatch.setattr(jobs, "_run", run)
    assert jobs.main("clean-image") == 0
    output = capsys.readouterr().out
    assert f"Removing unused runner image tool: {tree['unused']}" in output
    removed = f"Removed unused runner image tool: {tree['unused']} in "
    assert removed in output
    suffix = output.split(removed, 1)[1].splitlines()[0]
    assert suffix.endswith("s")
    assert float(suffix[:-1]) >= 0


def test_image_cleanup_raises_first_failure_after_other_trees_finish(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setattr(jobs.sys, "platform", "darwin")
    _disable_image_headroom(monkeypatch)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    tree = _cleanup_image_tree(tmp_path)
    monkeypatch.setenv("RUNNER_TOOL_CACHE", str(tree["tool_cache"]))
    monkeypatch.setattr(jobs, "_APPLICATIONS", tree["apps"])
    monkeypatch.setattr(jobs, "_DARWIN_SYSTEM_SIMULATORS", tree["system_simulators"])
    monkeypatch.setattr(jobs, "_UNUSED_IMAGE_PATHS", {"darwin": (tree["unused"],)})
    attempted: list[Path] = []

    def remove(path: Path) -> None:
        attempted.append(path)
        if path == tree["old"]:
            raise subprocess.CalledProcessError(1, "sudo")

    monkeypatch.setattr(jobs, "_remove_unused_image_path", remove)

    def run(*args, capture=False, check=True):
        return subprocess.CompletedProcess(args, 0, stdout=str(tree["selected"]))

    monkeypatch.setattr(jobs, "_run", run)
    with pytest.raises(subprocess.CalledProcessError):
        jobs.main("clean-image")
    assert tree["old"] in attempted
    assert len(attempted) > 1
    output = capsys.readouterr().out
    assert f"Failed unused runner image tool: {tree['old']} in " in output


def test_image_cleanup_skips_reclaim_when_no_unused_trees_exist(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setattr(jobs.sys, "platform", "linux")
    monkeypatch.setattr(jobs, "_UNUSED_IMAGE_PATHS", {"linux": (tmp_path / "absent",)})
    monkeypatch.setattr(
        jobs,
        "_run",
        lambda *args, **_kwargs: subprocess.CompletedProcess(args, 0, stdout=""),
    )
    assert jobs.main("clean-image") == 0


def test_image_cleanup_skips_absent_runner_tool_cache(tmp_path, monkeypatch) -> None:
    """Image cleanup must not require RUNNER_TOOL_CACHE to collect Darwin paths."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.delenv("RUNNER_TOOL_CACHE", raising=False)
    monkeypatch.setattr(jobs.sys, "platform", "darwin")
    _disable_image_headroom(monkeypatch)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    tree = _cleanup_image_tree(tmp_path)
    monkeypatch.setattr(jobs, "_APPLICATIONS", tree["apps"])
    monkeypatch.setattr(jobs, "_DARWIN_SYSTEM_SIMULATORS", tree["system_simulators"])
    monkeypatch.setattr(jobs, "_UNUSED_IMAGE_PATHS", {"darwin": (tree["unused"],)})

    def run(*args, capture=False, check=True):
        if args[0] == "sudo":
            assert Path(args[-1]).is_relative_to(tmp_path)
            return subprocess.CompletedProcess(args, 0, stdout="")
        return subprocess.CompletedProcess(args, 0, stdout=str(tree["selected"]))

    monkeypatch.setattr(jobs, "_run", run)
    assert jobs.main("clean-image") == 0
    assert tree["tool_cache"].is_dir()


def test_image_cleanup_tolerates_live_cache_directory_races(
    tmp_path, monkeypatch
) -> None:
    """Hosted macOS can rewrite ~/Library/Caches while sudo rmtree runs."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setattr(jobs.sys, "platform", "darwin")
    _disable_image_headroom(monkeypatch)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    tree = _cleanup_image_tree(tmp_path)
    monkeypatch.setenv("RUNNER_TOOL_CACHE", str(tree["tool_cache"]))
    monkeypatch.setattr(jobs, "_APPLICATIONS", tree["apps"])
    monkeypatch.setattr(jobs, "_DARWIN_SYSTEM_SIMULATORS", tree["system_simulators"])
    monkeypatch.setattr(jobs, "_UNUSED_IMAGE_PATHS", {"darwin": (tree["unused"],)})
    leftover = tree["caches"] / "writer"
    leftover.mkdir()
    real_run = jobs._run

    def run(*args, capture=False, check=True):
        if args[0] == "sudo":
            snippet = args[args.index("-c") + 1]
            target = Path(args[-1])
            assert Path(target).is_relative_to(tmp_path)
            if target == tree["caches"]:
                assert "ignore_errors=True" in snippet
                return subprocess.CompletedProcess(args, 0, stdout="")
            assert "ignore_errors=True" not in snippet or target in {
                tree["hosted"],
                tree["tool_cache"],
            }
            return real_run(*args[1:], capture=capture, check=check)
        return subprocess.CompletedProcess(args, 0, stdout=str(tree["selected"]))

    monkeypatch.setattr(jobs, "_run", run)
    assert jobs.main("clean-image") == 0
    assert leftover.is_dir()
    assert not tree["old"].exists()
    assert not tree["unused"].exists()


def _free_disk(free: int) -> object:
    return type("Usage", (), {"total": free + 1, "used": 1, "free": free})()


def _disable_image_headroom(monkeypatch) -> None:
    monkeypatch.setattr(jobs, "image_headroom_required_bytes", lambda: 0)


def test_runner_free_bytes_prefers_nix_when_mounted(monkeypatch) -> None:
    monkeypatch.setattr(jobs.Path, "exists", lambda self: True)

    def usage(path: Path) -> object:
        return _free_disk(11 if path == Path("/nix") else 3)

    monkeypatch.setattr(jobs.shutil, "disk_usage", usage)
    assert jobs.runner_free_bytes() == 11


def test_runner_free_bytes_uses_root_before_nix_exists(monkeypatch) -> None:
    monkeypatch.setattr(jobs.Path, "exists", lambda self: str(self) != "/nix")

    def usage(path: Path) -> object:
        return _free_disk(5 if path == Path("/") else 0)

    monkeypatch.setattr(jobs.shutil, "disk_usage", usage)
    assert jobs.runner_free_bytes() == 5


def test_log_runner_disk_reports_absent_nix_mount(monkeypatch, capsys) -> None:
    monkeypatch.setattr(jobs.Path, "exists", lambda self: str(self) != "/nix")
    monkeypatch.setattr(jobs.shutil, "disk_usage", lambda _path: _free_disk(9))
    jobs._log_runner_disk("df test")
    output = capsys.readouterr().out
    assert "df test /: free=9 used=1 total=10" in output
    assert "df test /nix: not mounted" in output


@pytest.mark.parametrize(
    ("actions", "environment", "platform", "runs"),
    [
        ("true", "github-hosted", "darwin", True),
        ("true", "github-hosted", "linux", False),
        ("true", "self-hosted", "darwin", False),
        ("false", "github-hosted", "darwin", False),
    ],
)
def test_hosted_darwin_store_gc_is_gated_to_disposable_runners(
    monkeypatch, actions: str, environment: str, platform: str, runs: bool
) -> None:
    """A 62 GiB Darwin root-closure fetch must not inherit a full package store."""
    monkeypatch.setenv("GITHUB_ACTIONS", actions)
    monkeypatch.setenv("RUNNER_ENVIRONMENT", environment)
    monkeypatch.setattr(jobs.sys, "platform", platform)
    monkeypatch.setattr(
        jobs.shutil,
        "disk_usage",
        lambda _path: _free_disk(jobs._NIX_MIN_FREE_BYTES),
    )
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        jobs, "_run", lambda *args, **_kwargs: calls.append(args) or None
    )
    assert jobs.main("reclaim-store") == 0
    assert calls == ([("nix", "store", "gc")] if runs else [])
    assert jobs.is_hosted_darwin_runner() is runs


def test_hosted_darwin_store_gc_skips_when_free_covers_max_and_min_free(
    monkeypatch, capsys
) -> None:
    """Closure shards do not inherit a package store; skip GC with headroom."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setattr(jobs.sys, "platform", "darwin")
    skip_free = jobs._NIX_MAX_FREE_BYTES + jobs._NIX_MIN_FREE_BYTES
    monkeypatch.setattr(jobs.shutil, "disk_usage", lambda _path: _free_disk(skip_free))
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        jobs, "_run", lambda *args, **_kwargs: calls.append(args) or None
    )
    assert jobs.main("reclaim-store") == 0
    assert calls == []
    output = capsys.readouterr().out
    assert f"Skipping store GC; free {skip_free} bytes already meets" in output
    assert f"headroom {skip_free} bytes" in output


def test_hosted_update_runtime_reserves_store_headroom_for_root_closures() -> None:
    """Nix must GC before unpacking a Darwin root that can exceed 60 GiB."""
    action = yaml.load(
        (ROOT / ".github/actions/update-runtime/action.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    extra_conf = next(
        step["with"]["extra-conf"]
        for step in action["runs"]["steps"]
        if str(step.get("uses", "")).startswith(
            "DeterminateSystems/determinate-nix-action@"
        )
    )
    assert "min-free = 34359738368" in extra_conf
    assert "max-free = 68719476736" in extra_conf
    assert jobs._NIX_MIN_FREE_BYTES == 34359738368
    assert jobs._NIX_MAX_FREE_BYTES == 68719476736
    assert jobs._IMAGE_HEADROOM_BYTES == (
        jobs._NIX_MAX_FREE_BYTES + jobs._NIX_MIN_FREE_BYTES
    )
    assert jobs.image_headroom_required_bytes() == jobs._IMAGE_HEADROOM_BYTES


def test_record_named_storage_requires_a_label(monkeypatch) -> None:
    monkeypatch.delenv("NIXCFG_STORAGE_LABEL", raising=False)
    with pytest.raises(ValueError, match="NIXCFG_STORAGE_LABEL"):
        jobs.main("record-storage")


def test_record_named_storage_writes_the_requested_label(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("NIXCFG_STORAGE_LABEL", "after-nix-install")
    monkeypatch.setattr(jobs.shutil, "disk_usage", lambda _path: _free_disk(8))
    monkeypatch.setattr(jobs.shutil, "which", lambda _name: None)
    assert jobs.main("record-storage") == 0
    rows = [
        json.loads(line)
        for line in (tmp_path / "update-artifacts/storage.jsonl")
        .read_text()
        .splitlines()
    ]
    assert rows[-1]["label"] == "after-nix-install"


def test_dump_hosted_storage_fault_skips_non_darwin_runners(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setattr(jobs.sys, "platform", "linux")
    assert jobs.main("dump-storage-fault") == 0
    assert not (tmp_path / "update-artifacts/storage-fault.txt").exists()


def _hosted_darwin_dump_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setattr(jobs.sys, "platform", "darwin")
    monkeypatch.setattr(jobs.shutil, "disk_usage", lambda _path: _free_disk(8))
    real_exists = Path.exists

    def exists(self: Path) -> bool:
        if str(self) in {"/nix", "/nix/store"}:
            return True
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", exists)


def test_dump_hosted_storage_fault_keeps_diskutil_and_unified_logs(
    tmp_path, monkeypatch
) -> None:
    """A guest df with 60+ GiB free is not proof the host backing store is healthy."""
    _hosted_darwin_dump_env(tmp_path, monkeypatch)
    diskutil = tmp_path / "diskutil"
    log_bin = tmp_path / "log"
    diskutil.write_text("")
    log_bin.write_text("")

    def which(name: str) -> str | None:
        return {"diskutil": str(diskutil), "log": str(log_bin), "df": None}.get(name)

    def run(args, **kwargs):
        joined = " ".join(args)
        if args[:2] == [str(diskutil), "list"]:
            return subprocess.CompletedProcess(
                args, 0, stdout="/dev/disk2", stderr="list warn"
            )
        if args[:2] == [str(diskutil), "info"]:
            stdout = (
                "Device Node: disk2s7\n"
                "APFS Container: disk2\n"
                "Part of Whole: disk2\n"
                "Device Node: \n"
                "Random line without a field\n"
                if args[-1] == "/nix"
                else "Volume Name: Macintosh HD\n"
            )
            return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")
        if args[:2] == [str(log_bin), "show"]:
            assert "--last" in args
            assert "2h" in args
            assert jobs._STORAGE_FAULT_PREDICATE in args
            return subprocess.CompletedProcess(
                args, 0, stdout="apfs I/O error disk2s7\n", stderr=""
            )
        raise AssertionError(joined)

    monkeypatch.setattr(jobs.shutil, "which", which)
    monkeypatch.setattr(jobs.subprocess, "run", run)
    assert jobs.main("dump-storage-fault") == 0
    report = (tmp_path / "update-artifacts/storage-fault.txt").read_text()
    assert "diskutil list" in report
    assert "diskutil info /nix" in report
    assert "diskutil info disk2" in report
    assert "list warn" in report
    assert "apfs I/O error" in (tmp_path / "update-artifacts/apfs-io.log").read_text()
    rows = [
        json.loads(line)
        for line in (tmp_path / "update-artifacts/storage.jsonl")
        .read_text()
        .splitlines()
    ]
    assert rows[-1]["label"] == "storage-fault"


def test_dump_hosted_storage_fault_survives_missing_and_timed_out_tools(
    tmp_path, monkeypatch
) -> None:
    _hosted_darwin_dump_env(tmp_path, monkeypatch)
    monkeypatch.setattr(jobs.shutil, "which", lambda _name: None)
    assert jobs.main("dump-storage-fault") == 0
    report = (tmp_path / "update-artifacts/storage-fault.txt").read_text()
    assert "diskutil missing" in report
    assert (
        tmp_path / "update-artifacts/apfs-io.log"
    ).read_text() == "log show missing\n"

    diskutil = tmp_path / "diskutil"
    log_bin = tmp_path / "log"
    diskutil.write_text("")
    log_bin.write_text("")

    def which(name: str) -> str | None:
        return {"diskutil": str(diskutil), "log": str(log_bin), "df": None}.get(name)

    def run(args, **kwargs):
        if args[:2] == [str(diskutil), "list"]:
            raise subprocess.TimeoutExpired(args, jobs._STORAGE_FAULT_LOG_SECONDS)
        if args[:2] == [str(diskutil), "info"]:
            raise OSError("no disk")
        if args[:2] == [str(log_bin), "show"]:
            raise subprocess.TimeoutExpired(args, jobs._STORAGE_FAULT_LOG_SECONDS)
        raise AssertionError(" ".join(args))

    monkeypatch.setattr(jobs.shutil, "which", which)
    monkeypatch.setattr(jobs.subprocess, "run", run)
    assert jobs.main("dump-storage-fault") == 0
    timed = (tmp_path / "update-artifacts/storage-fault.txt").read_text()
    assert "TimeoutExpired" in timed
    assert (
        (tmp_path / "update-artifacts/apfs-io.log")
        .read_text()
        .startswith("TimeoutExpired:")
    )

    def run_log_oserror(args, **kwargs):
        if args[:2] == [str(diskutil), "list"]:
            raise OSError("list failed")
        if args[:2] == [str(diskutil), "info"]:
            raise subprocess.TimeoutExpired(args, jobs._STORAGE_FAULT_LOG_SECONDS)
        if args[:2] == [str(log_bin), "show"]:
            raise OSError("log show failed")
        raise AssertionError(" ".join(args))

    monkeypatch.setattr(jobs.subprocess, "run", run_log_oserror)
    assert jobs.main("dump-storage-fault") == 0
    assert (
        (tmp_path / "update-artifacts/apfs-io.log").read_text().startswith("OSError:")
    )


def test_append_command_output_keeps_empty_stdout_and_terminated_stderr(
    tmp_path, monkeypatch
) -> None:
    report = tmp_path / "report.txt"
    report.write_text("", encoding="utf-8")

    def run(_args, **_kwargs):
        return subprocess.CompletedProcess(["diskutil"], 0, stdout="", stderr="warn\n")

    monkeypatch.setattr(jobs.subprocess, "run", run)
    jobs._append_command_output(report, "empty stdout", ["diskutil", "info"])
    text = report.read_text()
    assert "warn" in text
    assert "exit=0" in text


def test_diskutil_info_targets_tolerate_missing_nix_and_tools(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(jobs.shutil, "which", lambda _name: None)
    monkeypatch.setattr(Path, "exists", lambda self: str(self) != "/nix")
    assert jobs._diskutil_info_targets() == ("/",)

    diskutil = tmp_path / "diskutil"
    diskutil.write_text("")

    def which(name: str) -> str | None:
        return str(diskutil) if name == "diskutil" else None

    monkeypatch.setattr(jobs.shutil, "which", which)

    def run(_args, **_kwargs):
        raise OSError("diskutil info failed")

    monkeypatch.setattr(jobs.subprocess, "run", run)
    assert jobs._diskutil_info_targets() == ("/",)
