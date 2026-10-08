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
_CACHIX_DAEMON_DIR_ENV = "CACHIX_DAEMON_DIR"
_CACHIX_DAEMON_SOCKET_ENV = "CACHIX_DAEMON_SOCKET"
_CACHIX_DAEMON_SOCKET_NAME = "daemon.sock"
_STORAGE_MOUNTS = ("/", "/nix", "/nix/store")
# Live-written on hosted macOS; rmtree can lose a race (ENOTEMPTY) after children
# are gone. Cleanup is disk reclaim, not a correctness gate for these trees.
_VOLATILE_IMAGE_LEAVES = frozenset({"Caches", "hostedtoolcache"})
# Must match .github/actions/update-runtime/action.yml extra-conf.
_NIX_MIN_FREE_BYTES = 34359738368
_NIX_MAX_FREE_BYTES = 68719476736
# Floor: max-free + min-free. Raise to measured zeus peak + margin once
# 37740898487 storage.jsonl exists. macos-15 starts at ~43 GiB free, so
# the skip path does not fire until that peak is known and lower than 96 GiB.
_IMAGE_HEADROOM_BYTES = _NIX_MAX_FREE_BYTES + _NIX_MIN_FREE_BYTES
_IMAGE_LOG_LOCK = threading.Lock()
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


def record_runner_storage(
    label: str,
    log: TextIO | None = None,
    *,
    detail: bool = True,
    live: bool = True,
) -> dict[str, object]:
    """Record df, inodes, and used bytes before or after a heavy hosted phase."""
    snapshot: dict[str, object] = {
        "label": label,
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mounts": {},
    }
    mounts: dict[str, dict[str, int]] = {}
    for mount in _STORAGE_MOUNTS:
        path = Path(mount)
        if not path.exists():
            continue
        usage = shutil.disk_usage(mount)
        mounts[mount] = {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
        }
    snapshot["mounts"] = mounts
    df_bin = shutil.which("df")
    if df_bin is None:
        snapshot["df_h"] = ""
        snapshot["df_i"] = ""
    else:
        df_h = subprocess.run(  # noqa: S603 -- resolved df path
            [df_bin, "-h", *list(mounts)],
            check=False,
            capture_output=True,
            text=True,
        )
        df_i = subprocess.run(  # noqa: S603 -- resolved df path
            [df_bin, "-i", *list(mounts)],
            check=False,
            capture_output=True,
            text=True,
        )
        snapshot["df_h"] = df_h.stdout
        snapshot["df_i"] = df_i.stdout
    root = mounts.get("/", {})
    store = mounts.get("/nix/store", mounts.get("/nix", {}))
    message = (
        f"storage {label} free={root.get('free', 0)} "
        f"used={root.get('used', 0)} store_used={store.get('used', 0)}"
    )
    if detail:
        message = f"{message}\n{snapshot['df_h']}{snapshot['df_i']}"
    if os.environ.get("RUNNER_TEMP"):
        try:
            artifacts = _temp() / "update-artifacts"
            artifacts.mkdir(parents=True, exist_ok=True)
            with (artifacts / "storage.jsonl").open("a") as handle:
                handle.write(json.dumps(snapshot) + "\n")
        except OSError:
            pass
    if live:
        if log is not None:
            _write_diagnostic(log, message.rstrip())
        else:
            sys.stderr.write(message if message.endswith("\n") else message + "\n")
            sys.stderr.flush()
    return snapshot


def cachix_daemon_socket() -> Path | None:
    """Return the socket cachix-action started, never the unused default path.

    cachix-action v16 exports ``CACHIX_DAEMON_DIR`` and binds
    ``$CACHIX_DAEMON_DIR/daemon.sock`` (or ``CACHIX_DAEMON_SOCKET``). Bare
    ``cachix daemon stop`` talks to ``~/.cache/cachix/cachix-daemon.sock``,
    which this action never creates.
    """
    if socket := os.environ.get(_CACHIX_DAEMON_SOCKET_ENV, "").strip():
        return Path(socket)
    if daemon_dir := os.environ.get(_CACHIX_DAEMON_DIR_ENV, "").strip():
        return Path(daemon_dir) / _CACHIX_DAEMON_SOCKET_NAME
    return None


class CachixFlushError(RuntimeError):
    """The explicit flush could not confirm a clean Cachix daemon drain."""


class ImageCleanupError(RuntimeError):
    """Hosted Darwin still lacks closure headroom after image reclaim."""


def image_headroom_required_bytes() -> int:
    """Free bytes hosted Darwin must keep so min-free cannot fire mid-rustc."""
    return _IMAGE_HEADROOM_BYTES


def runner_free_bytes() -> int:
    """Return APFS-shared free bytes; /nix is absent before Nix install."""
    mount = Path("/nix") if Path("/nix").exists() else Path("/")
    return shutil.disk_usage(mount).free


def _log_runner_disk(label: str) -> None:
    for mount in ("/", "/nix"):
        path = Path(mount)
        if not path.exists():
            sys.stdout.write(f"{label} {mount}: not mounted\n")
            continue
        usage = shutil.disk_usage(mount)
        sys.stdout.write(
            f"{label} {mount}: free={usage.free} used={usage.used} "
            f"total={usage.total}\n"
        )
    sys.stdout.flush()


def _is_darwin_heavy_reclaim_path(path: Path) -> bool:
    if path.name.startswith("Xcode") and path.suffix == ".app":
        return True
    return path.name == "sdk" and "Android" in path.parts


def clean_runner_image() -> None:
    """Reclaim unused image tools, exclusively on disposable hosted runners.

    Hosted macos-15 starts around 43 GiB free. Four concurrent Darwin shards
    already serialize Xcode rmtree for tens of minutes; eight-wide parallel
    deletes on those VMs add host I/O, they do not shorten the wait. Skip
    Xcode/Android when free already covers closure headroom. Otherwise delete
    serially, timed per path, and stop once the threshold is met. Fail closed
    if the disk is still short. Do not overlap this I/O with Nix install.
    """
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
    ):
        msg = "Image cleanup requires a disposable GitHub-hosted runner"
        raise RuntimeError(msg)
    paths = list(_UNUSED_IMAGE_PATHS[sys.platform])
    record_runner_storage("before-image-cleanup")
    _log_runner_disk("df before image cleanup")
    need = image_headroom_required_bytes()
    before = runner_free_bytes()
    sys.stdout.write(f"Available before image cleanup: {before} bytes (need {need})\n")
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
    to_remove = [path for path in paths if path.is_dir() and not path.is_symlink()]
    if sys.platform == "darwin" and need > 0:
        heavy = [path for path in to_remove if _is_darwin_heavy_reclaim_path(path)]
        light = [path for path in to_remove if path not in heavy]
        if before >= need:
            sys.stdout.write(
                f"Skipping Xcode/Android reclaim; free {before} bytes "
                f"already meets headroom {need} bytes\n"
            )
            to_remove = light
        else:
            _reclaim_until_headroom([*heavy, *light], need)
            to_remove = []
    if to_remove:
        _reclaim_unused_image_paths(to_remove)
    after = runner_free_bytes()
    sys.stdout.write(f"Available after image cleanup: {after} bytes\n")
    if sys.platform == "darwin" and need > 0 and after < need:
        msg = (
            f"Image cleanup left {after} bytes free; need {need} bytes "
            "for Darwin root-closure headroom"
        )
        raise ImageCleanupError(msg)
    record_runner_storage("after-image-cleanup")


def _image_cleanup_best_effort(path: Path) -> bool:
    """Return whether a live-written cache tree may race with sudo rmtree."""
    tool_cache = os.environ.get("RUNNER_TOOL_CACHE")
    return path.name in _VOLATILE_IMAGE_LEAVES or (
        tool_cache is not None and path == Path(tool_cache)
    )


def _image_log(message: str) -> None:
    """Write one cleanup line and flush so GHA shows per-path elapsed seconds."""
    with _IMAGE_LOG_LOCK:
        sys.stdout.write(f"{message}\n")
        sys.stdout.flush()


def _remove_unused_image_path(path: Path) -> None:
    """Delete one unused image tree; ignore leftover writers in cache dirs."""
    snippet = (
        "import shutil, sys; shutil.rmtree(sys.argv[1], ignore_errors=True)"
        if _image_cleanup_best_effort(path)
        else "import shutil, sys; shutil.rmtree(sys.argv[1])"
    )
    _run("sudo", sys.executable, "-c", snippet, str(path))


def _remove_unused_image_path_timed(path: Path) -> None:
    """Delete one unused tree and log elapsed seconds for the GHA breakdown."""
    started = time.perf_counter()
    _image_log(f"Removing unused runner image tool: {path}")
    try:
        _remove_unused_image_path(path)
    except (OSError, subprocess.CalledProcessError):
        _image_log(
            f"Failed unused runner image tool: {path} in "
            f"{time.perf_counter() - started:.1f}s"
        )
        raise
    _image_log(
        f"Removed unused runner image tool: {path} in "
        f"{time.perf_counter() - started:.1f}s"
    )


def _reclaim_unused_image_paths(paths: list[Path]) -> None:
    """Delete unused image trees serially with elapsed seconds per path."""
    for path in paths:
        _remove_unused_image_path_timed(path)


def _reclaim_until_headroom(paths: list[Path], need: int) -> None:
    """Delete unused trees only until hosted Darwin has closure headroom."""
    for path in paths:
        free = runner_free_bytes()
        if free >= need:
            sys.stdout.write(
                f"Stopped image reclaim; free {free} bytes meets headroom {need} bytes\n"
            )
            sys.stdout.flush()
            return
        _remove_unused_image_path_timed(path)


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
    record_runner_storage("before-store-gc")
    free = shutil.disk_usage("/").free
    sys.stdout.write(f"Available before store GC: {free} bytes\n")
    sys.stdout.flush()
    # Image cleanup typically leaves ~150 GiB. Closure shards do not inherit a
    # package store, so nix store gc cannot free tens of GiB. Skip when free
    # already covers max-free plus min-free; otherwise a 60+ GiB fetch can
    # still trip min-free mid-rustc. Combined packages+closures on a tight
    # disk still GCs unused outputs first.
    skip_free = image_headroom_required_bytes()
    if skip_free > 0 and free >= skip_free:
        sys.stdout.write(
            f"Skipping store GC; free {free} bytes already meets "
            f"headroom {skip_free} bytes\n"
        )
        sys.stdout.flush()
        record_runner_storage("after-store-gc")
        return
    _run("nix", "store", "gc")
    sys.stdout.write(f"Available after store GC: {shutil.disk_usage('/').free} bytes\n")
    record_runner_storage("after-store-gc")


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
                    f"run_logs={run_logs} "
                    f"free={shutil.disk_usage('/').free}",
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
        record_runner_storage(f"before-native-{stage}", log, detail=False, live=False)
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
        record_runner_storage(f"after-native-{stage}", log, detail=False, live=False)
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


def _retain_cachix_daemon_evidence(artifacts: Path, log: TextIO) -> None:
    """Keep the action's daemon log, hook, and env so a missing socket is diagnosable."""
    daemon_dir = os.environ.get(_CACHIX_DAEMON_DIR_ENV, "")
    socket = cachix_daemon_socket()
    hook_files = os.environ.get("NIX_USER_CONF_FILES", "")
    nix_conf = os.environ.get("NIX_CONF", "")
    _write_diagnostic(
        log,
        "Cachix daemon wiring "
        f"{_CACHIX_DAEMON_DIR_ENV}={daemon_dir!r} "
        f"{_CACHIX_DAEMON_SOCKET_ENV}={os.environ.get(_CACHIX_DAEMON_SOCKET_ENV, '')!r} "
        f"socket={socket} socket_exists={bool(socket and socket.exists())} "
        f"NIX_USER_CONF_FILES={hook_files!r} "
        f"NIX_CONF_has_post_build_hook={'post-build-hook' in nix_conf}",
    )
    if not daemon_dir:
        return
    source = Path(daemon_dir)
    retained = artifacts / "cachix-daemon"
    if not source.is_dir():
        _write_diagnostic(log, f"CACHIX_DAEMON_DIR missing on disk: {source}")
        return
    retained.mkdir(parents=True, exist_ok=True)
    for name in ("daemon.log", "daemon.pid", "nix.conf", "post-build-hook.sh"):
        path = source / name
        if path.is_file():
            shutil.copy2(path, retained / name)
            _write_diagnostic(log, f"retained {name} bytes={path.stat().st_size}")


def _clear_cachix_daemon_env() -> None:
    """Hide a drained daemon from cachix-action's post hook.

    The post hook reads ``$CACHIX_DAEMON_DIR/daemon.pid`` and runs
    ``cachix daemon stop`` again. A missing pid throws; a missing socket
    fails stop after a ~30s retry. Empty ``CACHIX_DAEMON_DIR`` makes the
    hook skip push without failing the job.
    """
    github_env = os.environ.get("GITHUB_ENV", "").strip()
    for key in (_CACHIX_DAEMON_DIR_ENV, _CACHIX_DAEMON_SOCKET_ENV):
        os.environ.pop(key, None)
        if github_env:
            with Path(github_env).open("a", encoding="utf-8") as handle:
                handle.write(f"{key}=\n")


def _release_cachix_daemon_dir() -> None:
    """Remove the action's pid/socket after a confirmed drain."""
    daemon_dir = os.environ.get(_CACHIX_DAEMON_DIR_ENV, "").strip()
    if daemon_dir:
        path = Path(daemon_dir)
        if path.is_dir():
            for name in (_CACHIX_DAEMON_SOCKET_NAME, "daemon.pid"):
                target = path / name
                try:
                    target.unlink()
                except OSError:
                    continue
    _clear_cachix_daemon_env()


def flush_cachix() -> None:
    """Push leftover prefetch receipts and drain the Cachix daemon.

    cachix-action v16 starts ``cachix daemon run --socket
    $CACHIX_DAEMON_DIR/daemon.sock`` and registers a Nix post-build hook.
    This flush must stop that socket. A bare ``cachix daemon stop`` talks to
    ``~/.cache/cachix/cachix-daemon.sock`` and reports success here while
    leaving the real queue undrained.

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
        record_runner_storage("flush-start", log)
        _retain_cachix_daemon_evidence(artifacts, log)
        if receipts.exists():
            _publish_prefetched_receipts(receipts, log, artifacts, 0)
        socket = cachix_daemon_socket()
        if socket is None:
            msg = (
                "Cachix daemon socket is unknown: "
                f"{_CACHIX_DAEMON_DIR_ENV} and {_CACHIX_DAEMON_SOCKET_ENV} "
                "are unset. useDaemon wiring did not export the socket."
            )
            _write_diagnostic(log, msg)
            raise CachixFlushError(msg)
        if not socket.exists():
            msg = f"Cachix daemon socket missing: {socket}"
            _write_diagnostic(log, msg)
            raise CachixFlushError(msg)
        stop = _run(
            "cachix",
            "daemon",
            "stop",
            "--socket",
            str(socket),
            check=False,
            capture=True,
        )
        _write_diagnostic(
            log,
            f"cachix daemon stop returncode={stop.returncode} "
            f"socket={socket} stdout={stop.stdout.strip()} "
            f"stderr={stop.stderr.strip()}",
        )
        if stop.returncode:
            msg = (
                "Cachix daemon stop failed; cannot confirm a clean drain "
                f"socket={socket} returncode={stop.returncode} "
                f"stderr={stop.stderr.strip()}"
            )
            _write_diagnostic(log, msg)
            raise CachixFlushError(msg)
        _release_cachix_daemon_dir()
        _write_diagnostic(
            log,
            "cleared CACHIX_DAEMON_DIR so cachix-action post skips a second stop",
        )
        record_runner_storage("flush-end", log)


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
