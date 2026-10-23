"""Python entrypoint for disposable Actions jobs.

This file also bootstraps Nix before the packaged CLI exists, so it supports the
hosted runners' Python 3.12 with standard-library imports only. Invoke the file
directly; Actions owns the job graph.
"""

import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

_BINARY_CACHE = "gkze"
_VALIDATION_SCOPES = frozenset({
    "all",
    "packages",
    "closures",
    "closure-shard",
    "rust-warmup",
    "cascade-warmup",
    "cascade-canary",
    "fod-canary",
    "zed-warmup",
})
_HEARTBEAT_INTERVAL_SECONDS = 60
_OUTPUT_LOG_NAME = "output.log"
_APPLICATIONS = Path("/Applications")
_DARWIN_SYSTEM_SIMULATORS = Path("/Library/Developer/CoreSimulator")
_STORE_PATH_PREFIX = Path("/nix/store")
_CACHIX_DAEMON_DIR_ENV = "CACHIX_DAEMON_DIR"
_CACHIX_DAEMON_SOCKET_ENV = "CACHIX_DAEMON_SOCKET"
_CACHIX_DAEMON_SOCKET_NAME = "daemon.sock"
# cachix-action can return before daemon.sock exists. Poll so the
# require-before-bootstrap gate still fail-closes without racing the
# listener. Tests disable the wait through NIXCFG_CACHIX_DAEMON_READY_SECONDS.
_CACHIX_DAEMON_READY_SECONDS = 60.0
_CACHIX_DAEMON_READY_POLL_SECONDS = 0.1
_CACHIX_DAEMON_READY_SECONDS_ENV = "NIXCFG_CACHIX_DAEMON_READY_SECONDS"
# cachix daemon stop waits 60s then DaemonPushFailure / exit 3
# ("Failed to push some store paths"). 38577749898 canary-warm-rust:
# rust_litemap-0.8.0 logged Pushing at 16:40:35 and never Pushed;
# drain timed out at 17:15:19. A direct `cachix push` recovers leftovers.
_CACHIX_PUSH_FAILURE = "Failed to push some store paths"
_CACHIX_DRAIN_TIMEOUT = "drain timed out"
_CACHIX_STUCK_JOBS = "stuck jobs after drain timeout"
_STORE_PATH_RE = re.compile(r"/nix/store/[0-9a-z]{32}-[^\s]+")
# 38821223775: each Darwin root shard built darwin-system-26.11.57a3171
# locally, then `cachix daemon stop` returned 0. assert-coverage still
# 404'd those paths on gkze.cachix.org. The post-build-hook queue did
# not publish the thin wrapper. Flush `cachix push`es this receipt.
# 38934724529: `nix path-info <installable>` recorded a stale
# darwin-system / home-manager-generation already on Cachix
# ("Nothing to push") while coverage's `eval .outPath` was the
# newly built wrapper. Remember must use the same eval as coverage.
_REALIZED_ROOT_PATHS_NAME = "realized-root-paths.json"
_LINUX_BUILDER_OUTPUT = "nixos-disk-image"
_CACHIX_PIN_KEEP_DAYS = 14
_BINARY_CACHE_STORE = "https://gkze.cachix.org"
_STORAGE_MOUNTS = ("/", "/nix", "/nix/store")
# Live-written on hosted macOS; rmtree can lose a race (ENOTEMPTY) after children
# are gone. Cleanup is disk reclaim, not a correctness gate for these trees.
_VOLATILE_IMAGE_LEAVES = frozenset({"Caches", "hostedtoolcache"})
# Must match .github/actions/update-runtime/action.yml extra-conf.
_NIX_MIN_FREE_BYTES = 34359738368
_NIX_MAX_FREE_BYTES = 68719476736
# Store GC before root closures is skipped once free covers max-free + min-free.
_STORE_GC_SKIP_BYTES = _NIX_MAX_FREE_BYTES + _NIX_MIN_FREE_BYTES
# Peak store growth of one heavy hosted Darwin job, measured as free bytes
# after image cleanup minus the job's lowest free bytes (flush-end):
# prepare-darwin 39811796194 103.8 GB, 39774011035 102.2 GB, 39760093470
# 96.2 GB; validate-darwin-roots 39166099302 ~83 GB. Rounded up to 104 GiB.
_DARWIN_PEAK_STORE_GROWTH_BYTES = 104 * 1024 * 1024 * 1024
# Image cleanup must leave peak growth + min-free so auto-GC never fires
# mid-job. Auto-GC deletes unrooted .drv files between an updater's eval and
# its build: 39850402233 started prepare-darwin at 103 GB (the old 96 GiB
# max-free + min-free floor), fell below min-free, and GC deleted the
# t3code-desktop node_modules .drv ("failed to obtain derivation").
# Full reclaim leaves ~155.7 GB (145 GiB) on macos-15, above this 136 GiB.
_IMAGE_HEADROOM_BYTES = _DARWIN_PEAK_STORE_GROWTH_BYTES + _NIX_MIN_FREE_BYTES
# Publish/coverage/repair do not compile Darwin closures. 8 GiB is enough
# for certify pytest plus Nix GC; skip the 3.3-4.6m Xcode/Android purge.
_LIGHT_IMAGE_RECLAIM_ENV = "NIXCFG_LIGHT_IMAGE_RECLAIM"
_LIGHT_IMAGE_HEADROOM_BYTES = 8 * 1024 * 1024 * 1024
_DETERMINATE_NIX_BIN = Path("/nix/var/nix/profiles/default/bin")
_STORAGE_FAULT_LOG_SECONDS = 90
_STORAGE_FAULT_PREDICATE = (
    'eventMessage CONTAINS[c] "Input/output error" OR '
    'eventMessage CONTAINS[c] "I/O error" OR '
    'subsystem CONTAINS[c] "apfs" OR '
    'senderImagePath CONTAINS[c] "IOStorage" OR '
    'eventMessage CONTAINS[c] "IOStorage"'
)
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
    record_runner_storage("before-bootstrap")
    runtime, devshell = _temp() / "nixcfg-runtime", _temp() / "nixcfg-devshell"
    # 38330944599 slot 3: gkze NAR timeouts then HTTP 416 left
    # exit-codes-1.3.0 unrealized; nix build .#nixcfg died without
    # --fallback after ~44 min. Prefer substitutes; build from source
    # after a stalled NAR instead of failing the job.
    _run(
        "nix",
        "build",
        "--no-write-lock-file",
        "--fallback",
        "--option",
        "stalled-download-timeout",
        "60",
        "--out-link",
        str(runtime),
        ".#nixcfg",
    )
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
    record_runner_storage("after-bootstrap")


def record_named_storage() -> None:
    """Write one labeled df snapshot for a hosted runtime sub-step."""
    label = os.environ.get("NIXCFG_STORAGE_LABEL", "").strip()
    if not label:
        msg = "NIXCFG_STORAGE_LABEL is required"
        raise ValueError(msg)
    record_runner_storage(label)


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


def _cachix_daemon_ready_wait_seconds() -> float:
    """Seconds to wait for ``daemon.sock`` after cachix-action returns."""
    raw = os.environ.get(_CACHIX_DAEMON_READY_SECONDS_ENV, "").strip()
    if raw:
        return max(0.0, float(raw))
    return _CACHIX_DAEMON_READY_SECONDS


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


class CachixDaemonError(RuntimeError):
    """useDaemon did not start a live post-build-hook daemon."""


class ImageCleanupError(RuntimeError):
    """Hosted Darwin still lacks closure headroom after image reclaim."""


def _light_image_reclaim() -> bool:
    """Return whether this job skips Xcode/Android purge."""
    return os.environ.get(_LIGHT_IMAGE_RECLAIM_ENV) == "true"


def image_headroom_required_bytes() -> int:
    """Free bytes hosted Darwin must keep so min-free cannot fire mid-rustc."""
    if _light_image_reclaim():
        return _LIGHT_IMAGE_HEADROOM_BYTES
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
    if path.name == "sdk" and "Android" in path.parts:
        return True
    if path == _DARWIN_SYSTEM_SIMULATORS:
        return True
    return path.name in {
        "CoreSimulator",
        "iOS DeviceSupport",
        "watchOS DeviceSupport",
        "tvOS DeviceSupport",
    }


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
        # Reclaim stops at headroom, so order trees by bytes freed per second.
        # macos-15 (39791273689, 39774011035, 39538011102): system simulators
        # free ~55 GB in <1 s, Android ~15 GB in 19-37 s, each Xcode ~5 GB in
        # 23-44 s. Xcode-first spent ~4 min on eight Xcodes before reaching
        # the simulators; this order meets the same headroom in under a minute.
        # iOS simulators are unused by Nix Darwin roots.
        paths.append(_DARWIN_SYSTEM_SIMULATORS)
        paths.append(Path.home() / "Library/Developer/CoreSimulator")
        paths.append(Path.home() / "Library/Android/sdk")
        xcode = Path.home() / "Library/Developer/Xcode"
        paths.extend((
            xcode / "iOS DeviceSupport",
            xcode / "watchOS DeviceSupport",
            xcode / "tvOS DeviceSupport",
        ))
        # Keep the active developer tools and their aliases. Nix supplies its
        # language toolchains; the updater does not need mobile SDKs.
        paths.extend(
            path
            for path in _APPLICATIONS.glob("Xcode*.app")
            if not path.is_symlink() and not selected.is_relative_to(path.resolve())
        )
        paths.append(xcode / "DerivedData")
        paths.append(Path.home() / "Library/Caches")
        paths.append(Path.home() / "hostedtoolcache")
        tool_cache = os.environ.get("RUNNER_TOOL_CACHE")
        if tool_cache:
            paths.append(Path(tool_cache))
    to_remove = [path for path in paths if path.is_dir() and not path.is_symlink()]
    if sys.platform == "darwin" and need > 0:
        heavy = [path for path in to_remove if _is_darwin_heavy_reclaim_path(path)]
        light = [path for path in to_remove if path not in heavy]
        if _light_image_reclaim():
            sys.stdout.write(
                f"Skipping Xcode/Android reclaim; light job headroom "
                f"{need} bytes, free {before} bytes\n"
            )
            to_remove = light
        elif before >= need:
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
    _log_runner_disk(f"df after removing {path}")


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


def _storage_fault_artifacts() -> Path:
    artifacts = _temp() / "update-artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    return artifacts


def _append_command_output(report: Path, heading: str, args: list[str]) -> None:
    """Run one diagnostic command and append stdout/stderr to the fault report."""
    with report.open("a", encoding="utf-8") as handle:
        handle.write(f"## {heading}\n")
        if not args:
            handle.write("\n")
            return
        try:
            result = subprocess.run(  # noqa: S603 -- fixed diagnostic argv
                args,
                check=False,
                capture_output=True,
                text=True,
                timeout=_STORAGE_FAULT_LOG_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            handle.write(f"{type(error).__name__}: {error}\n\n")
            return
        if result.stdout:
            handle.write(result.stdout)
            if not result.stdout.endswith("\n"):
                handle.write("\n")
        if result.stderr:
            handle.write(result.stderr)
            if not result.stderr.endswith("\n"):
                handle.write("\n")
        handle.write(f"exit={result.returncode}\n\n")


def _diskutil_info_targets() -> tuple[str, ...]:
    """Return mount and APFS container identifiers for the hosted /nix disk."""
    targets = ["/", "/nix"] if Path("/nix").exists() else ["/"]
    diskutil = shutil.which("diskutil")
    if diskutil is None:
        return tuple(targets)
    try:
        listing = subprocess.run(  # noqa: S603 -- resolved diskutil path
            [diskutil, "info", targets[-1]],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return tuple(targets)
    extras: list[str] = []
    for line in listing.stdout.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        name = key.strip()
        ident = value.strip()
        if name in {"APFS Container", "Part of Whole", "Device Node"} and ident:
            extras.append(ident)
    return tuple(dict.fromkeys([*targets, *extras]))


def dump_hosted_storage_fault() -> None:
    """Keep diskutil and APFS I/O logs after a hosted Darwin builder EIO.

    Guest ``df`` can show tens of GiB free while the host backing store or
    I/O path is saturated. A process-only ranlib EIO is not enough: capture
    the device layer before the VM disappears.
    """
    if not is_hosted_darwin_runner():
        return
    artifacts = _storage_fault_artifacts()
    record_runner_storage("storage-fault")
    report = artifacts / "storage-fault.txt"
    log_path = artifacts / "apfs-io.log"
    report.write_text("hosted Darwin storage-fault dump\n\n", encoding="utf-8")
    diskutil = shutil.which("diskutil")
    if diskutil is None:
        _append_command_output(report, "diskutil missing", [])
    else:
        _append_command_output(report, "diskutil list", [diskutil, "list"])
        for target in _diskutil_info_targets():
            _append_command_output(
                report, f"diskutil info {target}", [diskutil, "info", target]
            )
    log_bin = shutil.which("log")
    if log_bin is None:
        log_path.write_text("log show missing\n", encoding="utf-8")
        return
    try:
        result = subprocess.run(  # noqa: S603 -- resolved log path
            [
                log_bin,
                "show",
                "--last",
                "2h",
                "--style",
                "compact",
                "--predicate",
                _STORAGE_FAULT_PREDICATE,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=_STORAGE_FAULT_LOG_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        log_path.write_text(f"{type(error).__name__}: {error}\n", encoding="utf-8")
        return
    log_path.write_text(result.stdout + result.stderr, encoding="utf-8")
    with report.open("a", encoding="utf-8") as handle:
        handle.write(
            f"## log show exit={result.returncode} bytes={log_path.stat().st_size}\n"
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
    skip_free = _STORE_GC_SKIP_BYTES
    if free >= skip_free:
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
        msg = (
            "Validation scope must be all, packages, closures, "
            "closure-shard, rust-warmup, cascade-warmup, cascade-canary, "
            "fod-canary, or zed-warmup"
        )
        raise ValueError(msg)
    if scope != "all":
        args.extend(("--scope", scope))
    budget = os.environ.get("NIXCFG_CLOSURE_BUDGET_SECONDS", "")
    roots = os.environ.get("NIXCFG_CLOSURE_ROOTS", "")
    shard = os.environ.get("NIXCFG_CLOSURE_SHARD", "")
    slot = os.environ.get("NIXCFG_WARMUP_SLOT", "").strip()
    warmup = os.environ.get("NIXCFG_WARMUP_PLAN", "").strip()
    if budget and (
        scope not in {"closures", "closure-shard"} or not _is_positive_number(budget)
    ):
        msg = "Closure budget must be a positive number of seconds on a closure validation"
        raise ValueError(msg)
    if scope == "closure-shard" and (not roots or not shard):
        msg = "closure-shard requires NIXCFG_CLOSURE_ROOTS and NIXCFG_CLOSURE_SHARD"
        raise ValueError(msg)
    if scope == "rust-warmup" and (not warmup or not slot):
        msg = "rust-warmup requires NIXCFG_WARMUP_PLAN and NIXCFG_WARMUP_SLOT"
        raise ValueError(msg)
    if scope == "cascade-warmup" and (not warmup or not slot):
        msg = "cascade-warmup requires NIXCFG_WARMUP_PLAN and NIXCFG_WARMUP_SLOT"
        raise ValueError(msg)
    if scope in {"cascade-canary", "zed-warmup"} and not warmup:
        msg = f"{scope} requires NIXCFG_WARMUP_PLAN"
        raise ValueError(msg)
    if scope != "closure-shard" and roots:
        msg = "Named closure roots are only valid for the closure-shard scope"
        raise ValueError(msg)
    if (
        scope
        not in {
            "closure-shard",
            "rust-warmup",
            "cascade-warmup",
            "cascade-canary",
            "fod-canary",
            "zed-warmup",
        }
        and shard
    ):
        msg = "Named closure roots are only valid for the closure-shard scope"
        raise ValueError(msg)
    if scope not in {"rust-warmup", "cascade-warmup"} and slot:
        msg = "warmup slots are only valid for rust-warmup or cascade-warmup"
        raise ValueError(msg)
    if budget:
        args.extend(("--closure-budget-seconds", budget))
    if roots:
        args.extend(("--closure-roots", roots))
    if scope == "closure-shard" and shard:
        args.extend(("--shard", shard))
    if warmup:
        args.extend(("--warmup-plan", warmup))
    if slot:
        args.extend(("--warmup-slot", slot))
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


def _merge_candidate_args(artifacts: Path) -> list[str]:
    """Return argv that folds two or three native prepare artifacts."""
    previous = os.environ.get("NIXCFG_PREVIOUS_CANDIDATE", "")
    right = os.environ.get("NIXCFG_MERGE_RIGHT", "")
    extra = os.environ.get("NIXCFG_MERGE_EXTRA", "").strip()
    if not previous or not right:
        msg = "Merge requires NIXCFG_PREVIOUS_CANDIDATE and NIXCFG_MERGE_RIGHT"
        raise ValueError(msg)
    args = [
        _runtime(),
        "ci",
        "update",
        "merge-candidates",
        "--left",
        previous,
        "--right",
        right,
        "--output",
        str(artifacts / "candidate.json"),
    ]
    if extra:
        args.extend(("--extra", extra))
    return args


def _validate_native_args(args: list[str], artifacts: Path, previous: str) -> list[str]:
    """Append validate flags, allowing canary to omit a previous candidate."""
    if not previous and os.environ.get("NIXCFG_CANARY") != "true":
        msg = "Validation requires a previous candidate"
        raise ValueError(msg)
    scope = _append_validation_scope(args)
    report_name = (
        "shard-receipt.json" if scope == "closure-shard" else "validation.json"
    )
    if previous:
        args.extend(("--candidate", previous))
    args.extend(("--output", str(artifacts / report_name)))
    return args


_PREPARE_STAGES = frozenset({"prepare", "prepare-pins"})


def _native_args(stage: str, artifacts: Path) -> list[str]:
    """Return the updater argv for one hosted native stage."""
    if stage == "merge-candidates":
        return _merge_candidate_args(artifacts)
    args = [_runtime(), "ci", "update", stage]
    previous = os.environ.get("NIXCFG_PREVIOUS_CANDIDATE", "")
    if stage in _PREPARE_STAGES:
        args[-1] = "prepare"
        args.extend(("--output", str(artifacts / "candidate.json")))
        if previous:
            args.extend(("--previous", previous))
        if stage == "prepare-pins":
            args.append("--pins-only")
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
        if stage == "validate":
            return _validate_native_args(args, artifacts, previous)
        if stage == "cache-root-deps":
            msg = "Foreign-root dependency cache requires a previous candidate"
        elif stage == "plan-shards":
            msg = "Shard planning requires a previous candidate"
        elif stage in {"assert-cached-boundary", "assert-coverage"}:
            msg = (
                "Cached-boundary canary requires a previous candidate"
                if stage == "assert-cached-boundary"
                else "Coverage requires a previous candidate"
            )
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
        return _validate_native_args(args, artifacts, previous)
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
    if stage in {"assert-cached-boundary", "assert-coverage"}:
        return _coverage_native_args(args, previous, stage)
    msg = f"Unknown native stage: {stage}"
    raise ValueError(msg)


def _coverage_native_args(args: list[str], previous: str, stage: str) -> list[str]:
    """Append evidence flags for coverage and the pre-closures cache canary."""
    evidence = os.environ.get("NIXCFG_COVERAGE_EVIDENCE", "")
    if not evidence:
        msg = (
            "Cached-boundary canary requires NIXCFG_COVERAGE_EVIDENCE"
            if stage == "assert-cached-boundary"
            else "Coverage requires NIXCFG_COVERAGE_EVIDENCE"
        )
        raise ValueError(msg)
    args.extend(("--candidate", previous, "--evidence", evidence))
    if stage == "assert-coverage":
        args.extend(("--job-results", os.environ.get("NIXCFG_JOB_RESULTS", "")))
    return args


def _native_updater_env(artifacts: Path, receipts: Path, stage: str) -> dict[str, str]:
    """Return the updater env; prefer Determinate Nix over the devshell nixpkgs nix.

    ``nix develop`` puts ``pkgs.nix`` on PATH via the format-repo hook. That
    binary warns ``unknown setting 'eval-cores'`` and ignores Determinate
    parallel eval. Graph eval still uses ``DARWIN_GRAPH_EVAL_WORKERS = 1``.

    Hosted prepare stages run one updater build per runner core. The
    updater's default of one build exists for a shared, memory-constrained
    remote builder; a hosted runner builds locally and alone. 39867887491
    prepare-darwin ran 126 hash builds back to back (77.2 of 79.9 min busy,
    zero overlap) while Linux prepares took 8-15 min, so Darwin prepare was
    the critical path. Every build and hash check still runs; only their
    overlap changes. An explicit ``UPDATE_MAX_NIX_BUILDS`` wins.
    """
    env = os.environ | {
        "REPO_ROOT": str(Path.cwd()),
        "UPDATE_RUN_LOG": "1",
        "UPDATE_RUN_LOG_DIR": str(artifacts / "runs"),
        "UPDATE_PREFETCH_RECEIPTS": str(receipts),
    }
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    nix = _DETERMINATE_NIX_BIN / "nix"
    if hosted and nix.is_file():
        env["PATH"] = f"{_DETERMINATE_NIX_BIN}{os.pathsep}{env.get('PATH', '')}"
    if hosted and stage in _PREPARE_STAGES and "UPDATE_MAX_NIX_BUILDS" not in env:
        env["UPDATE_MAX_NIX_BUILDS"] = str(os.cpu_count() or 1)
    return env


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
            env=_native_updater_env(artifacts, receipts, stage),
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
    """Require native evidence and quality checks for the exact published tree.

    39081529525: publish prek failed after green Darwin roots/closures/
    coverage. ``pyupgrade --py314-plus`` rewrites redundant
    ``from __future__ import annotations``, and ty fail-closes on
    untyped ``iter()`` mocks in the Darwin Python suite.

    39097503443: pyupgrade left an extra blank after that drop;
    Darwin ``format-python-ruff`` then rejected
    ``lib/github_actions/page_session.py`` and ``packages/reason/updater*.py``.
    Run ruff format after pyupgrade, not pyupgrade alone.

    39113211468: Darwin certify pytest was 5979 passed / 1 skipped, then
    ``coverage report`` failed ``fail-under=100``. The 100% gate is the
    publish contract; new CI branches need behavior tests, not pragmas.
    """
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
        # 39113211468: pytest on Darwin certify inherited GITHUB_ENV from the
        # publish job. Tests that call flush_cachix() set NIXCFG_CI_STAGE to
        # flush-cachix and then emptied CACHIX_DAEMON_* in that live file, so
        # the always() flush saw unset sockets and masked the coverage miss.
        # Rewrite GITHUB_ENV only in the hosted flush-cachix process.
        if (
            github_env
            and os.environ.get("NIXCFG_CI_STAGE") == "flush-cachix"
            and not os.environ.get("PYTEST_CURRENT_TEST")
        ):
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


def require_cachix_daemon() -> None:
    """Fail closed unless cachix-action started a live post-build-hook daemon.

    Per-derivation push is the Nix hook talking to ``$CACHIX_DAEMON_DIR``.
    Flush-after-retain cannot prove that hook ran during the build. If the
    daemon never started, nothing a shard compiles reaches gkze, which
    breaks push-at-the-smallest-unit. This check runs after cachix-action
    and before bootstrap so a silent skip cannot burn a five-hour shard.
    Evidence is copied into artifacts here so retain (before flush) keeps it.
    """
    artifacts = _temp() / "update-artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    with (artifacts / "cachix-daemon-require.log").open("a") as log:
        deadline = time.monotonic() + _cachix_daemon_ready_wait_seconds()
        socket: Path | None = None
        while True:
            socket = cachix_daemon_socket()
            if socket is not None and socket.exists():
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _retain_cachix_daemon_evidence(artifacts, log)
                if socket is None:
                    msg = (
                        "Cachix daemon socket is unknown: "
                        f"{_CACHIX_DAEMON_DIR_ENV} and {_CACHIX_DAEMON_SOCKET_ENV} "
                        "are unset. useDaemon did not start a post-build-hook daemon."
                    )
                else:
                    msg = f"Cachix daemon socket missing: {socket}"
                _write_diagnostic(log, msg)
                raise CachixDaemonError(msg)
            time.sleep(min(_CACHIX_DAEMON_READY_POLL_SECONDS, remaining))
        _retain_cachix_daemon_evidence(artifacts, log)
        daemon_dir = os.environ.get(_CACHIX_DAEMON_DIR_ENV, "").strip()
        missing: list[str] = []
        if not daemon_dir:
            missing.append(_CACHIX_DAEMON_DIR_ENV)
        else:
            directory = Path(daemon_dir)
            pid = directory / "daemon.pid"
            hook = directory / "post-build-hook.sh"
            nix_conf = directory / "nix.conf"
            if not pid.is_file():
                missing.append("daemon.pid")
            if not hook.is_file():
                missing.append("post-build-hook.sh")
            if not nix_conf.is_file() or "post-build-hook" not in nix_conf.read_text(
                encoding="utf-8"
            ):
                missing.append("nix.conf post-build-hook")
        if missing:
            msg = (
                "Cachix daemon started incompletely; per-derivation push "
                f"cannot run: missing {', '.join(missing)}"
            )
            _write_diagnostic(log, msg)
            raise CachixDaemonError(msg)
        _write_diagnostic(
            log,
            f"Cachix daemon ready socket={socket} dir={daemon_dir}",
        )


def unfinished_cachix_daemon_paths(daemon_log: str) -> tuple[str, ...]:
    """Return store paths still in flight in a cachix daemon.log.

    38577749898 canary-warm-rust: ``Pushing rust_litemap-0.8.0`` never
    gained a matching ``Pushed`` line. ``daemon stop`` then hit the 60s
    drain timeout and exited 3. Those leftovers are still on disk and
    can be published with a direct ``cachix push``.
    """
    pushing: list[str] = []
    pushed: set[str] = set()
    for line in daemon_log.splitlines():
        if "[Info] Pushing " in line:
            match = _STORE_PATH_RE.search(line)
            if match:
                pushing.append(match.group(0))
        elif "[Info] Pushed " in line:
            match = _STORE_PATH_RE.search(line)
            if match:
                pushed.add(match.group(0))
    return tuple(path for path in dict.fromkeys(pushing) if path not in pushed)


def _cachix_stop_left_unpushed_paths(*, stderr: str, daemon_log: str) -> bool:
    """Return whether stop failed on Cachix's 60s drain timeout."""
    combined = f"{stderr}\n{daemon_log}"
    return (
        _CACHIX_PUSH_FAILURE in combined
        or _CACHIX_DRAIN_TIMEOUT in combined
        or _CACHIX_STUCK_JOBS in combined
    )


def _push_unfinished_cachix_paths(
    leftover: tuple[str, ...], log: TextIO
) -> subprocess.CompletedProcess[str]:
    """Publish leftover paths after ``cachix daemon stop`` drain timeout."""
    _write_diagnostic(
        log,
        f"retrying {len(leftover)} unpushed Cachix paths after drain timeout",
    )
    return _run("cachix", "push", _BINARY_CACHE, *leftover, check=False, capture=True)


def _publish_unpushed_warm_paths(daemon_log: str, log: TextIO) -> bool:
    """``cachix push`` leftovers and realized receipts after any job outcome.

    Failed cascade/rust-warmup jobs still built paths the post-build hook
    queued. Flush then stops the daemon and clears ``CACHIX_DAEMON_DIR``
    so the action post logs ``Cachix Daemon not started. Skipping push``.
    Direct ``cachix push`` of leftovers plus ``realized-root-paths.json``
    keeps that warm work even when the daemon is already gone.

    Return whether leftover recovery succeeded (or was unneeded).
    """
    leftover = unfinished_cachix_daemon_paths(daemon_log)
    leftover_ok = True
    if leftover:
        push = _push_unfinished_cachix_paths(leftover, log)
        _write_diagnostic(
            log,
            f"cachix push leftover returncode={push.returncode} "
            f"paths={' '.join(leftover)} stderr={push.stderr.strip()}",
        )
        leftover_ok = push.returncode == 0
        if not leftover_ok:
            _write_diagnostic(
                log,
                "leftover warmup push failed; continuing with recorded receipts",
            )
    _push_realized_root_paths(log)
    return leftover_ok


def _eval_installable_out_path(
    installable: str,
    *,
    run: Callable[..., object],
) -> str:
    """Return ``installable.outPath`` — the same query ``assert-coverage`` uses.

    38934724529: ``nix path-info`` on the flake installable printed a
    substituted ``home-manager-generation`` or a stale ``darwin-system``
    already on Cachix. Coverage then 404'd the check's eval'd outPath.
    """
    result = run(
        "nix",
        "eval",
        "--raw",
        "--no-write-lock-file",
        f"{installable}.outPath",
        check=False,
        capture=True,
    )
    path = str(getattr(result, "stdout", "")).strip()
    if int(getattr(result, "returncode", 1)) or not path.startswith("/nix/store/"):
        detail = str(getattr(result, "stderr", "")).strip() or "nix eval failed"
        msg = f"could not resolve realized root out paths for {installable}: {detail}"
        raise CachixFlushError(msg)
    return path


def drv_output_store_paths(
    installables: tuple[str, ...],
    *,
    run: Callable[..., object] | None = None,
) -> tuple[str, ...]:
    """Return realized output paths for ``drv^*`` installables.

    39006154683: ``nix build nixos-disk-image.drv^*`` substituted
    ``wy15yizd…-nixos-disk-image`` from gkze. The post-build hook does
    not fire on substitutes, so flush must ``cachix push`` these outputs
    explicitly.
    """
    if not installables:
        return ()
    runner = _run if run is None else run
    result = runner("nix", "path-info", *installables, check=False, capture=True)
    if int(getattr(result, "returncode", 1)):
        detail = str(getattr(result, "stderr", "")).strip() or "nix path-info failed"
        msg = f"could not resolve realized dependency outputs: {detail}"
        raise CachixFlushError(msg)
    paths = tuple(
        line.strip()
        for line in str(getattr(result, "stdout", "")).splitlines()
        if line.strip().startswith("/nix/store/")
    )
    if len(paths) != len(installables):
        msg = (
            "nix path-info returned "
            f"{len(paths)} outputs for {len(installables)} dependency installables"
        )
        raise CachixFlushError(msg)
    return paths


def remember_realized_store_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    """Record already-resolved store paths so flush can ``cachix push`` them.

    Later windows append. Cascade/rust-warmup realize one drv at a time;
    overwriting would drop earlier successes when a later drv fails.
    """
    if not paths:
        return ()
    bad = [path for path in paths if not path.startswith("/nix/store/")]
    if bad:
        msg = f"realized store paths are not Nix store paths: {', '.join(bad)}"
        raise CachixFlushError(msg)
    artifacts = _temp() / "update-artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    existing = ()
    receipt = artifacts / _REALIZED_ROOT_PATHS_NAME
    if receipt.is_file():
        existing = load_realized_root_paths(artifacts)
    merged = tuple(dict.fromkeys((*existing, *paths)))
    receipt.write_text(
        json.dumps({"storePaths": list(merged)}, indent=2) + "\n",
        encoding="utf-8",
    )
    return merged


def remember_realized_installable_paths(
    installables: tuple[str, ...],
    *,
    run: Callable[..., object] | None = None,
) -> tuple[str, ...]:
    """Record realized installable out paths so flush can ``cachix push`` them.

    38821223775 argus built ``darwin-system-26.11.57a3171`` at 05:31:46
    and finished validate at 05:31:51. Daemon stop returned 0 ten seconds
    later. ``https://gkze.cachix.org`` still 404'd
    ``/nix/store/98bwk8gpzhmr12navdg9knjscv09k2nm-darwin-system-26.11.57a3171``.

    38934724529 recorded ``nix path-info`` results that were already on
    Cachix (``Nothing to push``) instead of each check's ``.outPath``.
    """
    if not installables:
        return ()
    runner = _run if run is None else run
    paths = tuple(
        _eval_installable_out_path(installable, run=runner)
        for installable in installables
    )
    artifacts = _temp() / "update-artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / _REALIZED_ROOT_PATHS_NAME).write_text(
        json.dumps({"storePaths": list(paths)}, indent=2) + "\n",
        encoding="utf-8",
    )
    return paths


def _store_path_exists(path: str) -> bool:
    """Return whether a realized store path is still on this runner."""
    return Path(path).exists()


def load_realized_root_paths(artifacts: Path | None = None) -> tuple[str, ...]:
    """Return store paths recorded after a successful root-closure realize."""
    root = artifacts if artifacts is not None else _temp() / "update-artifacts"
    path = root / _REALIZED_ROOT_PATHS_NAME
    if not path.is_file():
        return ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        msg = f"invalid {_REALIZED_ROOT_PATHS_NAME}: {path}"
        raise CachixFlushError(msg) from error
    raw = payload.get("storePaths") if isinstance(payload, dict) else None
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        msg = f"invalid {_REALIZED_ROOT_PATHS_NAME}: {path}"
        raise CachixFlushError(msg)
    return tuple(item for item in raw if item.startswith("/nix/store/"))


def _realized_root_path_in_cachix(store_path: str) -> bool:
    """Return whether *store_path* is present on gkze.cachix.org."""
    result = _run(
        "nix",
        "path-info",
        "--store",
        _BINARY_CACHE_STORE,
        store_path,
        check=False,
        capture=True,
    )
    return result.returncode == 0


def _linux_builder_pin_name() -> str:
    """Return the Cachix pin name for this hosted linux-builder image."""
    arch = {"ARM64": "aarch64", "X64": "x86_64"}.get(os.environ.get("RUNNER_ARCH", ""))
    os_name = {"Linux": "linux"}.get(os.environ.get("RUNNER_OS", ""))
    if not arch or not os_name:
        msg = (
            "cannot name linux-builder pin without hosted RUNNER_ARCH/RUNNER_OS "
            f"(arch={os.environ.get('RUNNER_ARCH')!r} "
            f"os={os.environ.get('RUNNER_OS')!r})"
        )
        raise CachixFlushError(msg)
    return f"linux-builder-{arch}-{os_name}"


def _darwin_root_pin_name(path: str) -> str | None:
    """Return a 14-day pin name for a Darwin-system or home-manager wrapper.

    Four 4-wide shards each realize one root. Pin names include the store
    hash prefix so argus/rocinante/zeus/home do not overwrite each other.
    ``--keep-days 14`` drops stale hashes after the next green Update.
    """
    if not _STORE_PATH_RE.fullmatch(path.rstrip("/")):
        return None
    name = path.rstrip("/").rsplit("/", 1)[-1]
    digest, _separator, rest = name.partition("-")
    if rest.startswith("darwin-system"):
        return f"darwin-system-{digest[:8]}"
    if rest.startswith("home-manager-generation"):
        return f"home-generation-{digest[:8]}"
    return None


def _coverage_critical_store_path(path: str) -> bool:
    """Return whether flush must fail-closed if gkze 404s *path*.

    Darwin wrappers and the Rosetta VM must be on gkze. Source FODs
    (stdenv tarballs, crate2nix, bootstrap-tools) often live on hydra
    and need not. 39375722862 plan-shards recorded 1727 rust-warmup
    source FODs into ``realized-root-paths.json``, ``cachix push``
    exited 0, then ``nix path-info --store gkze`` 404'd hydra-backed
    tarballs and skipped every Darwin shard. Source FOD publish must
    never fail the plan.
    """
    if _LINUX_BUILDER_OUTPUT in path:
        return True
    return _darwin_root_pin_name(path) is not None


def _cachix_pin(name: str, path: str, log: TextIO) -> None:
    """Pin *path* on gkze for ``_CACHIX_PIN_KEEP_DAYS`` and fail closed."""
    pin = _run(
        "cachix",
        "pin",
        _BINARY_CACHE,
        name,
        path,
        "--keep-days",
        str(_CACHIX_PIN_KEEP_DAYS),
        check=False,
        capture=True,
    )
    _write_diagnostic(
        log,
        f"cachix pin {name} returncode={pin.returncode} path={path} "
        f"stderr={pin.stderr.strip()}",
    )
    if pin.returncode:
        msg = (
            f"cachix pin of {name} failed "
            f"returncode={pin.returncode} stderr={pin.stderr.strip()}"
        )
        raise CachixFlushError(msg)


def _pin_linux_builder_paths(paths: tuple[str, ...], log: TextIO) -> None:
    """Pin the Rosetta VM so gkze LRU cannot drop a substituted recache.

    39006154683: cache-root-deps copied the image from gkze at 08:22 and
    did not re-upload. Concurrent Darwin packages then LRU-evicted it
    before closures ``--max-jobs 0`` at 08:57.
    """
    images = [path for path in paths if _LINUX_BUILDER_OUTPUT in path]
    if not images:
        return
    if len(images) != 1:
        msg = f"expected one linux-builder image to pin, got {len(images)}: {images}"
        _write_diagnostic(log, msg)
        raise CachixFlushError(msg)
    _cachix_pin(_linux_builder_pin_name(), images[0], log)


def _pin_realized_darwin_roots(paths: tuple[str, ...], log: TextIO) -> None:
    """Pin Darwin-system and home-manager wrappers against gkze Starter LRU.

    39166099302 closures spent 40m after four green roots. If that was
    compile because LRU dropped the wrappers, a 14-day pin keeps the
    next run on substitutes. Do not pin every rust_*.
    """
    for path in paths:
        name = _darwin_root_pin_name(path)
        if name is None:
            continue
        _cachix_pin(name, path, log)


def _push_warmup_leftovers(paths: tuple[str, ...], log: TextIO) -> None:
    """Best-effort ``cachix push`` of recorded warmup FODs; never fail-close."""
    present = tuple(path for path in paths if _store_path_exists(path))
    skipped = [path for path in paths if path not in present]
    if skipped:
        _write_diagnostic(
            log,
            "warmup leftovers missing locally; skipping push: " + ", ".join(skipped),
        )
    if not present:
        return
    _write_diagnostic(
        log,
        f"pushing {len(present)} warmup leftovers without gkze fail-close",
    )
    push = _run("cachix", "push", _BINARY_CACHE, *present, check=False, capture=True)
    _write_diagnostic(
        log,
        f"cachix push warmup-leftovers returncode={push.returncode} "
        f"paths={' '.join(present)} stderr={push.stderr.strip()}",
    )
    if push.returncode:
        _write_diagnostic(
            log,
            "warmup leftover push failed; continuing without fail-close "
            f"returncode={push.returncode} stderr={push.stderr.strip()}",
        )


def _push_realized_root_paths(log: TextIO) -> None:
    """Publish locally realized Darwin roots that the daemon hook missed."""
    recorded = load_realized_root_paths()
    if not recorded:
        return
    leftovers = tuple(
        path for path in recorded if not _coverage_critical_store_path(path)
    )
    critical = tuple(path for path in recorded if _coverage_critical_store_path(path))
    if leftovers:
        _push_warmup_leftovers(leftovers, log)
    if not critical:
        return
    missing = [path for path in critical if not _store_path_exists(path)]
    if missing:
        msg = f"realized root paths missing locally: {', '.join(missing)}"
        _write_diagnostic(log, msg)
        raise CachixFlushError(msg)
    _write_diagnostic(
        log,
        f"pushing {len(critical)} realized root paths after daemon drain",
    )
    push = _run("cachix", "push", _BINARY_CACHE, *critical, check=False, capture=True)
    _write_diagnostic(
        log,
        f"cachix push realized-roots returncode={push.returncode} "
        f"paths={' '.join(critical)} stderr={push.stderr.strip()}",
    )
    if push.returncode:
        msg = (
            "cachix push of realized Darwin roots failed "
            f"returncode={push.returncode} stderr={push.stderr.strip()}"
        )
        raise CachixFlushError(msg)
    _pin_linux_builder_paths(critical, log)
    _pin_realized_darwin_roots(critical, log)
    # 38934724529: cachix exit 0 + "Nothing to push" meant the *wrong*
    # stale path was already on gkze. Coverage still 404'd the eval path.
    missing_remote = [
        path for path in critical if not _realized_root_path_in_cachix(path)
    ]
    if missing_remote:
        msg = (
            "realized root paths missing from gkze.cachix.org after push: "
            f"{', '.join(missing_remote)}"
        )
        _write_diagnostic(log, msg)
        raise CachixFlushError(msg)


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
    - Drain timeout (38577749898): ``daemon stop`` exits 3 after 60s with
      one stuck job. Recover identifiable ``Pushing`` leftovers with
      ``cachix push``; fail closed if none can be named or the retry fails.
    - Silent hook miss (38821223775): ``daemon stop`` returned 0 after
      each Darwin root shard built ``darwin-system-26.11.57a3171``, but
      gkze.cachix.org 404'd those paths. Flush then ``cachix push``es
      ``realized-root-paths.json``.
    - Stale path-info (38934724529): flush pushed a substituted
      ``darwin-system`` / ``home-manager-generation`` already on Cachix
      (``Nothing to push``). Receipts must be each check's ``.outPath``,
      and flush fail-closes unless ``nix path-info --store`` sees them.
    - Substituted linux-builder (39006154683): cache-root-deps copied
      ``nixos-disk-image`` from gkze, so the hook uploaded nothing.
      Flush ``cachix push``es those outputs and ``cachix pin``s them
      so rust_* LRU cannot drop the VM before Darwin ``--max-jobs 0``.
    - Darwin-root LRU (39166099302): four green shards still left
      closures compiling for 40m if Starter evicted the wrappers.
      Flush ``cachix pin --keep-days 14``s each darwin-system /
      home-manager-generation after the push.
    - Failed warmup (39301503361 cascade slot 3): the action post hook
      then logs ``Cachix Daemon not started. Skipping push``. Flush still
      ``cachix push``es leftovers and ``realized-root-paths.json`` so the
      successful stripe members reach gkze.
    """
    os.environ["NIXCFG_CI_STAGE"] = "flush-cachix"
    artifacts = _temp() / "update-artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    receipts = artifacts / "prefetch-receipts.jsonl"
    with (artifacts / "cachix-flush.log").open("a") as log:
        _write_diagnostic(log, "Flushing Cachix daemon and prefetch receipts")
        record_runner_storage("flush-start", log)
        _retain_cachix_daemon_evidence(artifacts, log)
        if receipts.exists():
            _publish_prefetched_receipts(receipts, log, artifacts, 0)
        daemon_log = artifacts / "cachix-daemon" / "daemon.log"
        daemon_log_text = (
            daemon_log.read_text(encoding="utf-8", errors="replace")
            if daemon_log.is_file()
            else ""
        )
        socket = cachix_daemon_socket()
        if socket is None:
            msg = (
                "Cachix daemon socket is unknown: "
                f"{_CACHIX_DAEMON_DIR_ENV} and {_CACHIX_DAEMON_SOCKET_ENV} "
                "are unset. useDaemon wiring did not export the socket."
            )
            _write_diagnostic(log, msg)
            _publish_unpushed_warm_paths(daemon_log_text, log)
            raise CachixFlushError(msg)
        if not socket.exists():
            msg = f"Cachix daemon socket missing: {socket}"
            _write_diagnostic(log, msg)
            _publish_unpushed_warm_paths(daemon_log_text, log)
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
        _retain_cachix_daemon_evidence(artifacts, log)
        daemon_log_text = (
            daemon_log.read_text(encoding="utf-8", errors="replace")
            if daemon_log.is_file()
            else daemon_log_text
        )
        if stop.returncode:
            leftover = unfinished_cachix_daemon_paths(daemon_log_text)
            if leftover and _cachix_stop_left_unpushed_paths(
                stderr=stop.stderr, daemon_log=daemon_log_text
            ):
                if _publish_unpushed_warm_paths(daemon_log_text, log):
                    _release_cachix_daemon_dir()
                    _write_diagnostic(
                        log,
                        "cleared CACHIX_DAEMON_DIR so cachix-action "
                        "post skips a second stop",
                    )
                    record_runner_storage("flush-end", log)
                    return
            else:
                _write_diagnostic(
                    log,
                    "Cachix daemon stop failed; pushing recorded warmup paths "
                    "before hiding the daemon from the action post hook",
                )
                _publish_unpushed_warm_paths(daemon_log_text, log)
            msg = (
                "Cachix daemon stop failed; cannot confirm a clean drain "
                f"socket={socket} returncode={stop.returncode} "
                f"stderr={stop.stderr.strip()}"
            )
            _write_diagnostic(log, msg)
            # Stop already ran. Hide the dead daemon from cachix-action's
            # post hook so a closed socket does not fail the job again
            # (38577749898 post hook exit 1 after this flush).
            _clear_cachix_daemon_env()
            raise CachixFlushError(msg)
        _publish_unpushed_warm_paths(daemon_log_text, log)
        _release_cachix_daemon_dir()
        _write_diagnostic(
            log,
            "cleared CACHIX_DAEMON_DIR so cachix-action post skips a second stop",
        )
        record_runner_storage("flush-end", log)


_UPDATE_PLAN_FALLBACK_BRANCH = "cursor/no-skip-darwin-shards-6614"


def restore_canary_plan(
    *,
    dest: Path | None = None,
    run: Callable[..., object] | None = None,
) -> Path:
    """Download the latest ``plan-shards-x86_64-linux`` artifact on this ref.

    Feature-branch Zed kicks have no Update runs; fall back to the
    no-skip parent so the family loop can reuse that plan.
    """
    target = dest if dest is not None else Path(os.environ["NIXCFG_CANARY_PLAN_DIR"])
    target.mkdir(parents=True, exist_ok=True)
    runner: Callable[..., object] = _run if run is None else run
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    branch = os.environ.get("GITHUB_REF_NAME", "")
    if not repo or not branch:
        msg = "Canary plan restore requires GITHUB_REPOSITORY and GITHUB_REF_NAME"
        raise ValueError(msg)
    branches = tuple(dict.fromkeys((branch, _UPDATE_PLAN_FALLBACK_BRANCH)))
    searched: list[str] = []
    for candidate in branches:
        searched.append(candidate)
        listed = runner(
            "gh",
            "run",
            "list",
            "-R",
            repo,
            "--workflow=Update",
            "--branch",
            candidate,
            "--limit",
            "20",
            "--json",
            "databaseId,status,conclusion",
            capture=True,
            check=True,
        )
        payload = getattr(listed, "stdout", listed)
        if not isinstance(payload, (str, bytes, bytearray)):
            msg = "Canary plan listing must return JSON text"
            raise TypeError(msg)
        runs = json.loads(payload)
        for run_row in runs:
            run_id = run_row["databaseId"]
            downloaded = runner(
                "gh",
                "run",
                "download",
                str(run_id),
                "-n",
                "plan-shards-x86_64-linux",
                "-D",
                str(target),
                check=False,
                capture=True,
            )
            code = downloaded.returncode if hasattr(downloaded, "returncode") else 0
            if code == 0 and (target / "warmup-plan.json").is_file():
                return target
    loc = ", ".join(f"{repo}@{name}" for name in searched)
    msg = f"No current warmup plan artifact on {loc}"
    raise RuntimeError(msg)


def main(stage: str) -> int:
    """Dispatch the finite set of Actions operations, preserving process failures."""
    native_stages = {
        "prepare",
        "prepare-pins",
        "validate",
        "cache-root-deps",
        "plan-shards",
        "assert-cached-boundary",
        "assert-coverage",
        "merge-candidates",
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
        "record-storage": record_named_storage,
        "dump-storage-fault": dump_hosted_storage_fault,
        "require-cachix-daemon": require_cachix_daemon,
        "bootstrap": bootstrap,
        "quality": quality,
        "certify": certify,
        "publish": publish,
        "collect-evidence": collect_evidence,
        "install-agent": install_agent,
        "repair": repair,
        "start-repair": start_repair,
        "flush-cachix": flush_cachix,
        "restore-canary-plan": restore_canary_plan,
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
