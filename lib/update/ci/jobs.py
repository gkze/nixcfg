"""Python entrypoint for disposable Actions jobs.

This file also bootstraps Nix before the packaged CLI exists, so it supports the
hosted runners' Python 3.12 with standard-library imports only. Invoke the file
directly; Actions owns the job graph.
"""

import json
import math
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TextIO

_BINARY_CACHE = "gkze"
_VALIDATION_SCOPES = frozenset({"all", "packages", "closures", "closure-shard"})
_HEARTBEAT_INTERVAL_SECONDS = 60
_OUTPUT_LOG_NAME = "output.log"
_APPLICATIONS = Path("/Applications")
_DARWIN_SYSTEM_SIMULATORS = Path("/Library/Developer/CoreSimulator")
_STORE_PATH_PREFIX = Path("/nix/store")
# Live-written on hosted macOS; rmtree can lose a race (ENOTEMPTY) after children
# are gone. Cleanup is disk reclaim, not a correctness gate for these trees.
_VOLATILE_IMAGE_LEAVES = frozenset({"Caches", "hostedtoolcache"})
_UNUSED_IMAGE_PATHS = {
    "darwin": (Path("/usr/local/share/dotnet"),),
    "linux": (
        Path("/usr/share/dotnet"),
        Path("/usr/local/lib/android"),
        Path("/opt/ghc"),
        Path("/usr/local/share/boost"),
    ),
}


def _run(
    *args: str, capture: bool = False, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- argv boundaries, never a shell
        args,
        text=True,
        capture_output=capture,
        check=check,
    )


def _temp() -> Path:
    return Path(os.environ["RUNNER_TEMP"])


def _runtime() -> str:
    return str(Path(os.environ["NIXCFG_RUNTIME"]) / "bin/nixcfg")


def _is_positive_number(value: str) -> bool:
    """Return whether *value* is a finite number of seconds greater than zero."""
    try:
        number = float(value)
    except ValueError:
        return False
    return math.isfinite(number) and number > 0


def _outputs(**values: str) -> None:
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.writelines(f"{name}={value}\n" for name, value in values.items())


def bootstrap() -> None:
    """Keep the baseline executable and tools as GC roots for this job."""
    runtime, devshell = _temp() / "nixcfg-runtime", _temp() / "nixcfg-devshell"
    _run("nix", "build", "--no-write-lock-file", "--out-link", str(runtime), ".#nixcfg")
    _run(
        "nix",
        "develop",
        "--no-write-lock-file",
        "--profile",
        str(devshell),
        "--command",
        "python",
        "-c",
        "pass",
    )
    _outputs(runtime=str(runtime), devshell=str(devshell))


def clean_runner_image() -> None:
    """Reclaim unused image tools, exclusively on disposable hosted runners."""
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
    ):
        msg = "Image cleanup requires a disposable GitHub-hosted runner"
        raise RuntimeError(msg)
    paths = list(_UNUSED_IMAGE_PATHS[sys.platform])
    sys.stdout.write(
        f"Available before image cleanup: {shutil.disk_usage('/').free} bytes\n"
    )
    if sys.platform == "darwin":
        selected = Path(
            _run("xcode-select", "--print-path", capture=True).stdout.strip()
        )
        if not selected.is_absolute() or not selected.is_dir():
            msg = "Cannot identify the active Xcode; refusing image cleanup"
            raise ValueError(msg)
        selected = selected.resolve()
        # Keep the active developer tools and their aliases. Nix supplies its
        # language toolchains; the updater does not need mobile SDKs.
        paths.extend(
            path
            for path in _APPLICATIONS.glob("Xcode*.app")
            if not path.is_symlink() and not selected.is_relative_to(path.resolve())
        )
        paths.append(Path.home() / "Library/Android/sdk")
        # iOS simulators are unused by Nix Darwin roots and occupy tens of GB.
        paths.append(Path.home() / "Library/Developer/CoreSimulator")
        paths.append(_DARWIN_SYSTEM_SIMULATORS)
        xcode = Path.home() / "Library/Developer/Xcode"
        paths.extend((
            xcode / "iOS DeviceSupport",
            xcode / "watchOS DeviceSupport",
            xcode / "tvOS DeviceSupport",
            xcode / "DerivedData",
        ))
        paths.append(Path.home() / "Library/Caches")
        paths.append(Path.home() / "hostedtoolcache")
        tool_cache = os.environ.get("RUNNER_TOOL_CACHE")
        if tool_cache:
            paths.append(Path(tool_cache))
    for path in paths:
        if path.is_dir() and not path.is_symlink():
            sys.stdout.write(f"Removing unused runner image tool: {path}\n")
            sys.stdout.flush()
            _remove_unused_image_path(path)
    sys.stdout.write(
        f"Available after image cleanup: {shutil.disk_usage('/').free} bytes\n"
    )


def _image_cleanup_best_effort(path: Path) -> bool:
    """Return whether a live-written cache tree may race with sudo rmtree."""
    tool_cache = os.environ.get("RUNNER_TOOL_CACHE")
    return path.name in _VOLATILE_IMAGE_LEAVES or (
        tool_cache is not None and path == Path(tool_cache)
    )


def _remove_unused_image_path(path: Path) -> None:
    """Delete one unused image tree; ignore leftover writers in cache dirs."""
    snippet = (
        "import shutil, sys; shutil.rmtree(sys.argv[1], ignore_errors=True)"
        if _image_cleanup_best_effort(path)
        else "import shutil, sys; shutil.rmtree(sys.argv[1])"
    )
    _run("sudo", sys.executable, "-c", snippet, str(path))


def is_hosted_darwin_runner() -> bool:
    """Return whether this process is a disposable hosted macos-15 job."""
    return (
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and sys.platform == "darwin"
    )


def reclaim_hosted_store() -> None:
    """Reclaim unused store paths on hosted Darwin before root-closure fetches."""
    if not is_hosted_darwin_runner():
        return
    sys.stdout.write(
        f"Available before store GC: {shutil.disk_usage('/').free} bytes\n"
    )
    sys.stdout.flush()
    _run("nix", "store", "gc")
    sys.stdout.write(f"Available after store GC: {shutil.disk_usage('/').free} bytes\n")


def _develop(*args: str) -> tuple[str, ...]:
    return "nix", "develop", os.environ["NIXCFG_DEVSHELL"], "--command", *args


def _quality_command() -> tuple[str, ...]:
    return _develop("python", "lib/update/ci/jobs.py", "quality")


def quality() -> None:
    """Apply existing gates and reject any generated or formatter drift."""
    _run("prek", "run", "-a")
    # Module invocation keeps this checkout ahead of the packaged runtime's lib.
    _run(sys.executable, "-m", "coverage", "run", "-m", "pytest")
    _run(sys.executable, "-m", "coverage", "report")
    _run("git", "diff", "--exit-code")
    if _run("git", "ls-files", "--others", "--exclude-standard", capture=True).stdout:
        msg = "Quality checks introduced untracked source files"
        raise RuntimeError(msg)


def _prefetched_paths_from_receipts(receipts: Path) -> list[str]:
    """Read exact direct-prefetch imports, rejecting malformed cache handoffs."""
    if not receipts.exists():
        return []
    paths: set[str] = set()
    for line in receipts.read_text().splitlines():
        try:
            receipt = json.loads(line)
            store_path = receipt["storePath"]
        except (TypeError, KeyError, json.JSONDecodeError) as error:
            msg = "Invalid prefetch receipt"
            raise ValueError(msg) from error
        path = Path(store_path) if isinstance(store_path, str) else None
        if (
            path is None
            or not path.is_absolute()
            or path.parent != _STORE_PATH_PREFIX
            or path.name in {"", ".", ".."}
        ):
            msg = "Prefetch receipt contains an unsafe store path"
            raise ValueError(msg)
        paths.add(str(path))
    return sorted(paths)


def _failure_summary(result_path: Path) -> str | None:
    """Return the concise source failure identity for a hosted job log."""
    try:
        result = json.loads(result_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(result, dict) or result.get("success") is not False:
        return None
    errors = result.get("errors")
    if not isinstance(errors, list) or not all(
        isinstance(error, str) for error in errors
    ):
        return "Updater failed; inspect the retained result and run-log artifacts."
    sources = ", ".join(errors)
    return f"Updater failed for: {sources}. Inspect the retained result and run-log artifacts."


def _write_diagnostic(log: TextIO, message: str) -> None:
    """Retain a CI diagnostic and mirror it to the live Actions log."""
    _forward_text(log, message + "\n")


def _forward_text(log: TextIO, text: str) -> None:
    """Retain already-terminated updater output and mirror it to the live log."""
    if not text:
        return
    log.write(text)
    log.flush()
    sys.stderr.write(text)
    sys.stderr.flush()


def _read_new_run_log_text(run_logs: Path, offsets: dict[str, int]) -> str:
    """Return unread run-log bytes and advance *offsets* past them."""
    if not run_logs.is_dir():
        return ""
    chunks: list[str] = []
    seen: set[Path] = set()
    for path in sorted(run_logs.rglob(_OUTPUT_LOG_NAME)):
        if not path.is_file():
            continue
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        key = str(resolved)
        try:
            data = path.read_bytes()
        except OSError:
            continue
        previous = offsets.get(key, 0)
        if len(data) < previous:
            previous = 0
        if len(data) == previous:
            continue
        offsets[key] = len(data)
        chunks.append(data[previous:].decode("utf-8", errors="replace"))
    return "".join(chunks)


def _drain_stderr(stderr: TextIO, diagnostics: queue.SimpleQueue[str | None]) -> None:
    """Transfer child diagnostics without making liveness depend on its output."""
    try:
        for diagnostic in stderr:
            diagnostics.put(diagnostic)
    finally:
        diagnostics.put(None)


def _wait_for_diagnostics(
    process: subprocess.Popen[str],
    log: TextIO,
    stage: str,
    artifacts: Path,
    command: list[str],
) -> int:
    """Forward updater output promptly and prove liveness when every stream is quiet."""
    if process.stderr is None:  # pragma: no cover -- PIPE above guarantees stderr.
        msg = "Cannot collect updater diagnostics"
        raise RuntimeError(msg)
    diagnostics: queue.SimpleQueue[str | None] = queue.SimpleQueue()
    offsets: dict[str, int] = {}
    run_logs = artifacts / "runs"
    started = time.monotonic()
    _write_diagnostic(
        log,
        "Starting updater "
        f"stage={stage} pid={process.pid} command={command!r} "
        f"artifacts={artifacts} run_logs={run_logs}",
    )
    reader = threading.Thread(
        target=_drain_stderr,
        args=(process.stderr, diagnostics),
        name="nixcfg-update-stderr",
    )
    reader.start()
    while True:
        try:
            diagnostic = diagnostics.get(timeout=_HEARTBEAT_INTERVAL_SECONDS)
        except queue.Empty:
            if text := _read_new_run_log_text(run_logs, offsets):
                _forward_text(log, text)
            elif process.poll() is None:
                _write_diagnostic(
                    log,
                    f"Updater still running stage={stage} pid={process.pid} "
                    f"elapsed={time.monotonic() - started:.0f}s artifacts={artifacts} "
                    f"run_logs={run_logs}",
                )
            continue
        if diagnostic is None:
            break
        _forward_text(log, diagnostic)
    _forward_text(log, _read_new_run_log_text(run_logs, offsets))
    returncode = process.wait()
    reader.join()
    return returncode


def _push_prefetched_paths(paths: list[str], log: TextIO, artifacts: Path) -> None:
    """Publish raw imports while retaining liveness evidence for quiet Cachix uploads."""
    args = ["cachix", "push", _BINARY_CACHE, *paths]
    started = time.monotonic()
    _write_diagnostic(
        log,
        f"Publishing {len(paths)} prefetched paths cache={_BINARY_CACHE} "
        f"artifacts={artifacts}",
    )
    with subprocess.Popen(args) as process:  # noqa: S603 -- fixed Cachix invocation.
        while True:
            try:
                returncode = process.wait(timeout=_HEARTBEAT_INTERVAL_SECONDS)
            except subprocess.TimeoutExpired:
                _write_diagnostic(
                    log,
                    f"Cachix publication still running pid={process.pid} "
                    f"elapsed={time.monotonic() - started:.0f}s paths={len(paths)} "
                    f"artifacts={artifacts}",
                )
            else:
                break
    if returncode:
        raise subprocess.CalledProcessError(returncode, args)


def _append_validation_scope(args: list[str]) -> str:
    """Add shard flags and return the scope the hosted job requested."""
    scope = os.environ.get("NIXCFG_VALIDATE_SCOPE", "all")
    if scope not in _VALIDATION_SCOPES:
        msg = "Validation scope must be all, packages, closures, or closure-shard"
        raise ValueError(msg)
    if scope != "all":
        args.extend(("--scope", scope))
    budget = os.environ.get("NIXCFG_CLOSURE_BUDGET_SECONDS", "")
    roots = os.environ.get("NIXCFG_CLOSURE_ROOTS", "")
    shard = os.environ.get("NIXCFG_CLOSURE_SHARD", "")
    if budget and (
        scope not in {"closures", "closure-shard"} or not _is_positive_number(budget)
    ):
        msg = "Closure budget must be a positive number of seconds on a closure validation"
        raise ValueError(msg)
    if scope == "closure-shard" and (not roots or not shard):
        msg = "closure-shard requires NIXCFG_CLOSURE_ROOTS and NIXCFG_CLOSURE_SHARD"
        raise ValueError(msg)
    if scope != "closure-shard" and (roots or shard):
        msg = "Named closure roots are only valid for the closure-shard scope"
        raise ValueError(msg)
    if budget:
        args.extend(("--closure-budget-seconds", budget))
    if roots:
        args.extend(("--closure-roots", roots))
    if shard:
        args.extend(("--shard", shard))
    return scope


def _publish_prefetched_receipts(
    receipts: Path, log: TextIO, artifacts: Path, returncode: int
) -> int:
    """Push exact prefetch receipts on every exit path that produced them."""
    try:
        paths = _prefetched_paths_from_receipts(receipts)
    except ValueError:
        if returncode:
            _write_diagnostic(
                log,
                "Prefetch receipts were unusable after updater failure; "
                "retaining the updater status",
            )
            return returncode
        raise
    _write_diagnostic(log, f"Collected {len(paths)} prefetched store paths")
    if not paths:
        return returncode
    try:
        _push_prefetched_paths(paths, log, artifacts)
    except subprocess.CalledProcessError as error:
        if returncode:
            _write_diagnostic(
                log,
                "Prefetch publication failed after updater failure; "
                f"retaining updater status cache_exit={error.returncode}",
            )
            return returncode
        raise
    return returncode


def _native_args(stage: str, artifacts: Path) -> list[str]:
    """Return the updater argv for one hosted native stage."""
    args = [_runtime(), "ci", "update", stage]
    previous = os.environ.get("NIXCFG_PREVIOUS_CANDIDATE", "")
    if stage == "prepare":
        args.extend(("--output", str(artifacts / "candidate.json")))
        if previous:
            args.extend(("--previous", previous))
        if os.environ.get("NIXCFG_VALIDATE_ALL_PACKAGES") == "true":
            args.append("--validate-all-packages")
        raw_targets = os.environ.get("NIXCFG_UPDATE_TARGETS", "")
        targets = raw_targets.split()
        if (
            "\n" in raw_targets
            or "\r" in raw_targets
            or any(target.startswith("-") for target in targets)
        ):
            msg = "Targets must be space-separated names, not updater options"
            raise ValueError(msg)
        if targets:
            args.extend(("--", *targets))
        return args
    if not previous:
        if stage == "cache-root-deps":
            msg = "Foreign-root dependency cache requires a previous candidate"
        elif stage == "validate":
            msg = "Validation requires a previous candidate"
        elif stage == "plan-shards":
            msg = "Shard planning requires a previous candidate"
        elif stage == "assert-coverage":
            msg = "Coverage requires a previous candidate"
        else:
            msg = f"Unknown native stage: {stage}"
        raise ValueError(msg)
    if stage == "cache-root-deps":
        args.extend((
            "--candidate",
            previous,
            "--output",
            str(artifacts / "cache-root-deps.json"),
        ))
        return args
    if stage == "validate":
        scope = _append_validation_scope(args)
        report_name = (
            "shard-receipt.json" if scope == "closure-shard" else "validation.json"
        )
        args.extend((
            "--candidate",
            previous,
            "--output",
            str(artifacts / report_name),
        ))
        return args
    if stage == "plan-shards":
        args.extend((
            "--candidate",
            previous,
            "--output",
            str(artifacts / "darwin-closure-shards.json"),
        ))
        github_output = os.environ.get("GITHUB_OUTPUT", "")
        if github_output:
            args.extend(("--github-output", github_output))
        return args
    if stage == "assert-coverage":
        evidence = os.environ.get("NIXCFG_COVERAGE_EVIDENCE", "")
        if not evidence:
            msg = "Coverage requires NIXCFG_COVERAGE_EVIDENCE"
            raise ValueError(msg)
        args.extend((
            "--candidate",
            previous,
            "--evidence",
            evidence,
            "--job-results",
            os.environ.get("NIXCFG_JOB_RESULTS", ""),
        ))
        return args
    msg = f"Unknown native stage: {stage}"
    raise ValueError(msg)


def native(stage: str) -> int:
    """Prepare or validate with immutable inputs and retained failure evidence."""
    artifacts = _temp() / "update-artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    args = _native_args(stage, artifacts)
    receipts = artifacts / "prefetch-receipts.jsonl"
    with (
        (artifacts / "result.json").open("w") as output,
        (artifacts / "stderr.log").open("w") as log,
    ):
        _write_diagnostic(log, f"Starting native stage={stage} artifacts={artifacts}")
        with subprocess.Popen(  # noqa: S603 -- fixed executable and separate target arguments
            args,
            stdout=output,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=os.environ
            | {
                "REPO_ROOT": str(Path.cwd()),
                "UPDATE_RUN_LOG": "1",
                "UPDATE_RUN_LOG_DIR": str(artifacts / "runs"),
                "UPDATE_PREFETCH_RECEIPTS": str(receipts),
            },
        ) as process:
            returncode = _wait_for_diagnostics(process, log, stage, artifacts, args)
        _write_diagnostic(
            log, f"Updater finished stage={stage} returncode={returncode}"
        )
        if summary := _failure_summary(artifacts / "result.json"):
            _write_diagnostic(log, summary)
        return _publish_prefetched_receipts(receipts, log, artifacts, returncode)


def certify() -> None:
    """Require native evidence and quality checks for the exact published tree."""
    evidence = _temp() / "evidence"
    candidate = evidence / "prepare-x86_64-linux/candidate.json"
    patch = _temp() / "update.patch"
    reports = [
        arg
        for report in sorted(evidence.glob("validate-*/validation.json"))
        for arg in ("--report", str(report))
    ]
    _run(
        _runtime(),
        "ci",
        "update",
        "certify",
        "--candidate",
        str(candidate),
        *reports,
        "--output",
        str(patch),
    )
    if not patch.stat().st_size and not os.environ["GITHUB_REF_NAME"].startswith(
        "codex/update-repair-"
    ):
        _outputs(changed="false")
        return
    identity = json.loads(candidate.read_bytes())
    if _run("git", "write-tree", capture=True).stdout.strip() != identity["base_tree"]:
        msg = "Publication checkout does not match the candidate baseline"
        raise ValueError(msg)
    if patch.stat().st_size:
        _run("git", "apply", "--index", "--binary", str(patch))
    if _run("git", "write-tree", capture=True).stdout.strip() != identity["tree"]:
        msg = "Applied patch does not match the certified candidate"
        raise ValueError(msg)
    _run(*_quality_command())
    if _run("git", "write-tree", capture=True).stdout.strip() != identity["tree"]:
        msg = "Quality checks changed the certified candidate"
        raise ValueError(msg)
    _outputs(changed="true")


def _commit_and_push(
    kind: str,
    message: str,
    *,
    base: str | None = None,
    tree: str | None = None,
) -> str:
    branch = f"codex/update-{kind}{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}"
    if base is None:
        _run("git", "switch", "-c", branch)
    else:
        if tree is None:
            msg = "Publishing from a base requires a certified tree"
            raise ValueError(msg)
        _run("git", "fetch", "origin", base)
        _run(
            "git",
            "switch",
            "--discard-changes",
            "-c",
            branch,
            f"origin/{base}",
        )
        _run("git", "restore", "--source", tree, "--worktree", "--staged", ".")
    if _run("git", "diff", "--cached", "--quiet", check=False).returncode:
        _run(*_develop("git", "commit", "-S", "-m", message))
    _run("gh", "auth", "setup-git")
    _run("git", "push", "--set-upstream", "origin", branch)
    return branch


def _queue_squash_auto_merge(branch: str) -> None:
    """Queue squash auto-merge, or squash-merge a PR that is already clean.

    GitHub rejects ``enablePullRequestAutoMerge`` with "clean status" when the
    base has no pending required checks. The certified tree is already good
    then, so merge it now instead of leaving the PR open.
    """
    result = _run(
        "gh",
        "pr",
        "merge",
        branch,
        "--auto",
        "--squash",
        capture=True,
        check=False,
    )
    if result.returncode == 0:
        return
    if "clean status" not in f"{result.stdout}{result.stderr}":
        raise subprocess.CalledProcessError(
            result.returncode,
            result.args,
            output=result.stdout,
            stderr=result.stderr,
        )
    _run("gh", "pr", "merge", branch, "--squash")


def publish() -> None:
    """Open the verified update as a PR against the default branch and squash-merge it.

    The product PR always targets the repository default branch, including when
    Update was exercised from a feature or repair ref. That keeps validated
    source refreshes off the branch under review and makes the PR auto-mergeable.
    """
    base = os.environ["UPDATE_BASE_BRANCH"]
    tree = _run("git", "write-tree", capture=True).stdout.strip()
    branch = _commit_and_push(
        "",
        "chore(update): refresh validated sources",
        base=base,
        tree=tree,
    )
    body = _temp() / "update-body.md"
    run_url = (
        f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}"
        f"/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    )
    body.write_text(
        "Prepared and validated against one Git tree on three native platforms.\n\n"
        f"Evidence: {run_url}\n"
    )
    _run(
        "gh",
        "pr",
        "create",
        "--base",
        base,
        "--head",
        branch,
        "--title",
        "chore(update): refresh validated sources",
        "--body-file",
        str(body),
    )
    _queue_squash_auto_merge(branch)


def collect_evidence() -> None:
    """Fetch completed failed-job logs even while the overall run is unfinished."""
    evidence = _temp() / "repair-evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    repository = os.environ["GITHUB_REPOSITORY"]
    result = _run(
        "gh",
        "api",
        f"repos/{repository}/actions/runs/{os.environ['GITHUB_RUN_ID']}/jobs",
        "--paginate",
        "--jq",
        '.jobs[] | select(.conclusion == "failure") | .id',
        capture=True,
    )
    for job in result.stdout.splitlines():
        identity = int(job)
        log = _run(
            "gh",
            "api",
            f"repos/{repository}/actions/jobs/{identity}/logs",
            "--allow-escape-sequences",
            capture=True,
        )
        (evidence / f"job-{identity}.log").write_text(log.stdout)


def install_agent() -> None:
    """Install the explicitly pinned agent CLI, independently of package updates."""
    _run("npm", "install", "--global", "@github/copilot@1.0.88")


def repair() -> None:
    """Check one isolated repair proposal before the new attempt can be admitted."""
    patch = _temp() / "repair.patch"
    _run(
        *_develop(
            _runtime(),
            "ci",
            "update",
            "repair",
            "--agent",
            "copilot",
            "--evidence",
            str(_temp() / "repair-evidence"),
            "--output",
            str(patch),
        )
    )
    _run("git", "apply", "--index", "--binary", str(patch))
    _run(*_quality_command())


def start_repair() -> None:
    """Start fresh execution with repair disabled, bounding automatic retries."""
    branch = _commit_and_push("repair-", "fix(update): repair packaging failure")
    _run(
        "gh",
        "workflow",
        "run",
        "update.yml",
        "--ref",
        branch,
        "-f",
        "repair=false",
        "-f",
        "validate_all_packages=true",
        "-f",
        f"targets={os.environ.get('NIXCFG_UPDATE_TARGETS', '')}",
    )


def flush_cachix() -> None:
    """Push leftover prefetch receipts and stop the Cachix daemon.

    Failure points and what reaches gkze:
    - Build failure: every path the daemon already uploaded, plus prefetch
      receipts flushed here.
    - Job timeout at 360 minutes: shards end their build budget at 5 hours so
      this step and the action post hook still have slack. A SIGKILL during
      flush can still drop the queue tail.
    - Cancellation: this step is ``if: always()``. GitHub may still skip later
      post hooks; the explicit flush is the mitigation and does not survive a
      cancel that kills the runner first.
    - Runner loss: only paths Cachix already acknowledged.
    """
    artifacts = _temp() / "update-artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    receipts = artifacts / "prefetch-receipts.jsonl"
    with (artifacts / "cachix-flush.log").open("a") as log:
        _write_diagnostic(log, "Flushing Cachix daemon and prefetch receipts")
        if receipts.exists():
            _publish_prefetched_receipts(receipts, log, artifacts, 0)
        stop = _run("cachix", "daemon", "stop", check=False, capture=True)
        _write_diagnostic(
            log,
            f"cachix daemon stop returncode={stop.returncode} "
            f"stdout={stop.stdout.strip()} stderr={stop.stderr.strip()}",
        )


def main(stage: str) -> int:
    """Dispatch the finite set of Actions operations, preserving process failures."""
    native_stages = {
        "prepare",
        "validate",
        "cache-root-deps",
        "plan-shards",
        "assert-coverage",
    }
    if stage in native_stages:
        # Capture CLI JSON inside the environment, after any devshell startup output.
        return _run(
            *_develop("python", str(Path(__file__).resolve()), f"native-{stage}"),
            check=False,
        ).returncode
    if stage.startswith("native-") and stage.removeprefix("native-") in native_stages:
        return native(stage.removeprefix("native-"))
    operations = {
        "clean-image": clean_runner_image,
        "reclaim-store": reclaim_hosted_store,
        "bootstrap": bootstrap,
        "quality": quality,
        "certify": certify,
        "publish": publish,
        "collect-evidence": collect_evidence,
        "install-agent": install_agent,
        "repair": repair,
        "start-repair": start_repair,
        "flush-cachix": flush_cachix,
    }
    operations[stage]()
    return 0


if (
    __name__ == "__main__"
):  # pragma: no cover -- entrypoint delegates to tested operations
    try:
        sys.exit(
            main(sys.argv[1] if len(sys.argv) > 1 else os.environ["NIXCFG_CI_STAGE"])
        )
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
