"""Python entrypoint for disposable Actions jobs.

This file also bootstraps Nix before the packaged CLI exists, so it supports the
hosted runners' Python 3.12 with standard-library imports only. Invoke the file
directly; Actions owns the job graph.
"""

import hashlib
import json
import math
import os
import queue
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TextIO, cast
from urllib.parse import quote

# A direct script invocation has only lib/update/ci on sys.path. The policy and
# paths modules and both package initializers are dependency-free, preserving the
# bootstrap.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from lib.update import sibling_failfast
from lib.update.nix_policy import NO_IMPORT_FROM_DERIVATION_ARGS
from lib.update.paths import local_flake_url
from lib.update.prepare_shard import hosted_prepare_shard

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
    "leaf-prebuild",
    "zed-warmup",
})
_HEARTBEAT_INTERVAL_SECONDS = 60
# A required sibling's completed failure is visible on the next poll. The
# build keeps its heartbeat cadence; this does not add a serial gate.
_SIBLING_POLL_SECONDS = 60.0
_SIBLING_STOP_GRACE_SECONDS = 5.0
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
# cachix (1.11.1 on the runners, unchanged through 1.13.0) builds its HTTP
# manager with ``responseTimeoutNone``: a stalled upload or narinfo query
# never times out. 40048292663 warm-rust (4): from 06:05:45 no path the
# post-build hook queued reached gkze (81 consecutive cargo-package outputs
# 404 on gkze and cache.nixos.org), and flush's ``cachix daemon stop`` then
# sat 3h until the 360-minute job cap. Daemon shutdown waits for the wedged
# narinfo processor before it replies, while each client handler still
# answers keep-alive pings, so the stop client never times out by itself.
# Every Cachix call in flush is therefore bounded here, and a stop that does
# not confirm a clean drain republishes every locally built path.
_CACHIX_STOP_TIMEOUT_SECONDS = 300.0
_CACHIX_STOP_TIMEOUT_SECONDS_ENV = "NIXCFG_CACHIX_STOP_TIMEOUT_SECONDS"
# The longest healthy flush in 25 Update runs was 405s (39375722862
# plan-darwin-closures pushing 1727 FODs). Retries resume: cachix push skips
# whatever an earlier attempt already uploaded.
# 43695499090 prepare-darwin-1: the updater finished, then this same client
# printed ``Pushing ...-zen.macos-universal.dmg`` and never returned.
# The prepare hand-off had no timeout, so the 6h job cap was the only stop.
_CACHIX_PUSH_TIMEOUT_SECONDS = 1200.0
_CACHIX_PUSH_ATTEMPTS = 3
_CACHIX_PUSH_CHUNK = 1000
_CACHIX_QUERY_TIMEOUT_SECONDS = 120.0
_STORE_QUERY_TIMEOUT_SECONDS = 600.0
# Shards stop building at 5 of the job's 6 hours; the workflow step's
# timeout-minutes (45) is the backstop behind this in-process budget.
_CACHIX_FLUSH_BUDGET_SECONDS = 2400.0
_CACHIX_DAEMON_EXIT_GRACE_SECONDS = 10.0
_DAEMON_LOG_TAIL_LINES = 20
_TIMEOUT_RETURNCODE = 124
# 40089810663 bootstrap exited 1 with no diagnostic: the captured stderr of the
# failing ``nix eval`` was discarded with the exception.
_FAILURE_STDERR_TAIL_LINES = 50
# cachix push omits paths its upstream (cache.nixos.org) already serves, so
# a recovered path is proven by a narinfo on either cache.
_RECOVERY_SUBSTITUTERS = ("https://gkze.cachix.org", "https://cache.nixos.org")
_NARINFO_TIMEOUT_SECONDS = 30.0
_NARINFO_ATTEMPTS = 3
_NARINFO_WORKERS = 16
# The Cachix edge answers 403 to urllib's default Python-urllib agent.
_NARINFO_USER_AGENT = "nixcfg-cachix-flush"
_STORE_PATH_RE = re.compile(r"/nix/store/[0-9a-z]{32}-[^\s]+")
# 38821223775: each Darwin root shard built darwin-system-26.11.57a3171
# locally, then `cachix daemon stop` returned 0. assert-coverage still
# 404'd those paths on gkze.cachix.org. The post-build-hook queue did
# not publish the thin wrapper. Flush `cachix push`es this receipt.
# The receipt is the validating `nix build`'s output. 38934724529:
# `nix path-info <installable>` recorded a stale path already on Cachix.
# 43531986200: `nix eval --option allow-import-from-derivation false
# .outPath` recorded l0y1sjsx-home-manager-generation, which that build
# never realized; the daemon had pushed z8fblck-home-manager-generation.
_REALIZED_ROOT_PATHS_NAME = "realized-root-paths.json"
_REPAIR_RECEIPT_NAME = "repair-receipt.json"
_LINUX_BUILDER_OUTPUT = "nixos-disk-image"
# 43613142360: ``darwin-system-qaj6kyaf`` returned 402
# "Pin storage limit exceeded" while ``linux-builder-aarch64-linux`` was
# revision 25 kept for 14 days. A stable name with one revision is what
# still protects the next substitute without filling the pin quota.
# 43667571490 and 43738140207: DELETE of that pin returned 403 with the
# same body an absent token gets ("You're not authorized to access binary
# cache gkze."). ``cachix pin`` in that job sent Authorization as the raw
# token (the servant log value has no scheme prefix) and the POST was
# authorized (402). Prefixing ``Bearer `` makes Cachix ignore the
# credential. Listing pins does not prove the token: GET is public. A 403
# is an authorization refusal. Pruning is housekeeping. A path the latest
# revision already retains is not pinned again: ``--keep-revisions`` trims
# only after Cachix accepts a new revision, and that POST 402s while the
# account is over the pin quota.
# 43770072139: home-george's closure was already on gkze, then
# ``cachix pin home-george`` returned 402. The public pin list still held
# ``darwin-system-748wmbw5`` (~38 GiB of narinfo on gkze) and
# ``home-generation-vijljmrs`` (~6.6 GiB) under ``--keep-days 14``, and
# DELETE of each returned 403 with the raw token. A new stable pin is
# charged the whole closure before ``--keep-revisions 1`` can trim, which
# does not fit in the 50 GiB plan beside those pins. The authorized POST
# can retarget the legacy name at one empty file so that trim drops the
# old closure. A later 402 does not fail the flush when the path is
# already on gkze: failing the shard sibling-stops the other roots.
_CACHIX_PIN_KEEP_REVISIONS = 1
_CACHIX_PIN_DELETE_FORBIDDEN = 403
_CACHIX_PIN_API = "https://cachix.org/api/v1/cache/gkze/pin"
_LEGACY_DARWIN_PIN_RE = re.compile(r"^(?:darwin-system|home-generation)-[0-9a-z]{8}$")
_LINUX_BUILDER_PIN_RE = re.compile(r"^linux-builder-(?:aarch64|x86_64)-linux$")
_DELETABLE_PIN_RE = re.compile(
    r"^(?:darwin-system-[0-9a-z]{8}|home-generation-[0-9a-z]{8}|"
    r"linux-builder-(?:aarch64|x86_64)-linux)$"
)
_PIN_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_AUTH_LOG_RE = re.compile(
    r"(?i)authorization(?:['\"]\s*,\s*['\"]|\s*[:=]\s*(?:bearer\s+)?)[^,}\]\s\"']+"
)
_BINARY_CACHE_STORE = "https://gkze.cachix.org"
_STORAGE_MOUNTS = ("/", "/nix", "/nix/store")
# Live-written on hosted macOS; rmtree can lose a race (ENOTEMPTY) after children
# are gone. Cleanup is disk reclaim, not a correctness gate for these trees.
_VOLATILE_IMAGE_LEAVES = frozenset({"Caches", "hostedtoolcache"})
# update-runtime passes these to Nix through the clean-image step outputs.
_NIX_MIN_FREE_BYTES = 34359738368
_NIX_MAX_FREE_BYTES = 68719476736
# Hosted Linux has no image headroom gate, and most hosts leave ~110 GB after
# cleanup, so the 32 GiB min-free never fires there. 41172807038
# plan-darwin-closures drew a 72G cow-root host: 14 GB free, 32.04 GB after
# cleanup, below min-free. The installer's own auto-GC then deleted the
# unrooted nss-cacert it was adding to the default profile, and Nix install
# failed. Linux min-free shrinks to what the host can keep through the
# job's peak growth. Peak Linux growth after cleanup: prepare-x86
# 39166099302 27 GB, validate-arm 41157698790 23 GB. Rounded up to 28 GiB.
_LINUX_PEAK_STORE_GROWTH_BYTES = 28 * 1024 * 1024 * 1024
_LINUX_MIN_FREE_FLOOR_BYTES = 1024 * 1024 * 1024
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
# A hosted VM boots for one job; 7h spans the 360-minute job cap plus boot.
# Queries start at the VM's boot instead when ``kern.boottime`` is readable
# and inside that cap.
_KERNEL_LOG_WINDOW_HOURS = 7
_KERNEL_LOG_WINDOW = f"{_KERNEL_LOG_WINDOW_HOURS}h"
_BOOT_SECONDS = re.compile(r"\bsec = (\d+)")
_BOOT_QUERY_SECONDS = 10
# 39883371442 prepare-darwin: the VM's virtual disk logged 99241 kernel
# "(IOStorageFamily) disk2s7: I/O error." lines across the Nix Store, Data and
# container devices (02:31:54-02:33:27) with 43+ GiB guest free, above
# min-free, so not auto-GC. Paths written then stayed registered with bad
# bytes: 35 min later evals read a .drv failing "expected string 'D'" and a
# binary bun.lock. 39835075034 logged 257514 such lines before temproot
# EILSEQ. Failed jobs without a device fault (39850402233, 39791273689,
# 39774011035) logged none. One such line makes the VM's store untrusted.
_DEVICE_FAULT_LINE = re.compile(r"\(IOStorageFamily\) disk\d+(?:s\d+)*: I/O error\.")
_DEVICE_FAULT_PREDICATE = 'process == "kernel" AND eventMessage CONTAINS[c] "I/O error"'
# 42975052923 prepare-darwin-0: after a 1h35m shard on a storage-starved VM
# (openchamber's node_modules installPhase took 20m20s, 1m27s-2m59s on the
# three prior runs) this query passed 300s. The other shards answered in 3-6s.
_DEVICE_FAULT_QUERY_SECONDS = 300
_DEVICE_FAULT_NAME = "device-fault.log"
_DEVICE_UNVERIFIED_NAME = "device-unverified.log"
_RUNNER_FAULT_NAME = "runner-fault.log"
_DEVICE_FAULT_EXCERPT_LINES = 50
# 41339729368 validate-darwin-cascade-warmup (3): 110892 kernel
# "(IOStorageFamily) disk2s7: I/O error." lines from 00:52:44, then
# bun-cache-shard-22's bsdtar could not open a verified tgz at 00:53:40.
# The same drv rebuilds bit-identical off the hosted VM. The faulted slot
# failed, slot 0 sibling-stopped and publish failed coverage. Warmup slots
# are explicit work units like prepare shards, so they hand off the same way.
# 43344701312 validate-darwin-roots (darwin-argus): 563366 kernel
# "(IOStorageFamily) disk2s7: I/O error." lines at 14:08, then substituting
# rust-docs failed "clearing flags of path ...: No such file or directory"
# and the daemon died; all three in-job retries hit the dead socket. The
# shard failed and sibling-stopped the three healthy roots. A planned root
# shard is fixed by its matrix entry, so it hands off the same way too.
_DEVICE_FAULT_RETRY_SCOPES = frozenset({
    "rust-warmup",
    "cascade-warmup",
    "closure-shard",
})
_DEVICE_FAULT_RETRY_ENV = "NIXCFG_DEVICE_FAULT_RETRY"
_DEVICE_FAULT_ARTIFACT_SUFFIX = "-device-fault"
_ARTIFACT_PAGE_SIZE = 100
_ARTIFACT_MAX_PAGES = 20
_ARTIFACT_HTTP_TIMEOUT_SECONDS = 30.0
_ARTIFACT_HTTP_ATTEMPTS = 3
_ARTIFACT_HTTP_RETRY_SECONDS = 5.0
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
    *args: str,
    capture: bool = False,
    check: bool = True,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run *args*; a *timeout* kills the child and reports ``_TIMEOUT_RETURNCODE``."""
    try:
        return subprocess.run(  # noqa: S603 -- argv boundaries, never a shell
            args,
            text=True,
            capture_output=capture,
            check=check,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        result = subprocess.CompletedProcess(
            args,
            _TIMEOUT_RETURNCODE,
            stdout=_timeout_text(error.stdout),
            stderr=f"{_timeout_text(error.stderr)}timed out after {timeout:.0f}s",
        )
    if check:
        raise subprocess.CalledProcessError(
            result.returncode, args, result.stdout, result.stderr
        )
    return result


def _timeout_text(value: str | bytes | None) -> str:
    """Return partial output from a killed child as text."""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value or ""


class _FlushBudget:
    """Wall-clock allowance that keeps one Cachix flush inside the job's slack."""

    def __init__(
        self, seconds: float, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._clock = clock
        self._deadline = clock() + seconds

    def timeout(self, limit: float) -> float:
        """Return *limit* capped by the remaining budget; fail once it is spent."""
        remaining = self._deadline - self._clock()
        if remaining <= 0:
            msg = (
                "Cachix flush budget exhausted; cannot confirm that every "
                "built path reached gkze before the job deadline"
            )
            raise CachixFlushError(msg)
        return min(limit, remaining)


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
    flake_url = json.dumps(local_flake_url(Path.cwd()))
    nixpkgs = _run(
        "nix",
        "eval",
        "--impure",
        "--raw",
        "--no-write-lock-file",
        *NO_IMPORT_FROM_DERIVATION_ARGS,
        "--expr",
        f"(builtins.getFlake {flake_url}).inputs.nixpkgs.outPath",
        capture=True,
    ).stdout.strip()
    if not nixpkgs:
        msg = "Pinned nixpkgs path is empty"
        raise ValueError(msg)
    _outputs(runtime=str(runtime), devshell=str(devshell), nixpkgs=nixpkgs)
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


def _cachix_stop_timeout_seconds() -> float:
    """Seconds ``cachix daemon stop`` may take before flush treats it as wedged.

    The proof workflow lowers this to exercise the wedged-daemon recovery.
    """
    raw = os.environ.get(_CACHIX_STOP_TIMEOUT_SECONDS_ENV, "").strip()
    if raw:
        return max(1.0, float(raw))
    return _CACHIX_STOP_TIMEOUT_SECONDS


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
    _emit_nix_gc_thresholds(after)
    record_runner_storage("after-image-cleanup")


def _emit_nix_gc_thresholds(free_after_cleanup: int) -> None:
    """Log and export the Nix GC thresholds update-runtime installs with."""
    min_free, max_free = nix_gc_thresholds(free_after_cleanup, sys.platform)
    sys.stdout.write(f"Nix GC thresholds: min-free={min_free} max-free={max_free}\n")
    if os.environ.get("GITHUB_OUTPUT"):
        _outputs(nix_min_free=str(min_free), nix_max_free=str(max_free))


def nix_gc_thresholds(free_after_cleanup: int, platform: str) -> tuple[int, int]:
    """Return Nix ``min-free`` and ``max-free`` for a host after image cleanup.

    Darwin keeps the fixed values; image cleanup fails closed unless they
    hold through peak growth. Linux lowers min-free only when the host
    cannot keep 32 GiB through its peak growth, so auto-GC never fires
    during Nix install or mid-job on a small host.
    """
    if platform == "darwin":
        return _NIX_MIN_FREE_BYTES, _NIX_MAX_FREE_BYTES
    min_free = min(
        _NIX_MIN_FREE_BYTES,
        max(
            _LINUX_MIN_FREE_FLOOR_BYTES,
            free_after_cleanup - _LINUX_PEAK_STORE_GROWTH_BYTES,
        ),
    )
    return min_free, _NIX_MAX_FREE_BYTES


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

    Emits ``device_fault=true`` when this VM's kernel logged a block-device
    I/O error, so the workflow reruns the stage on a fresh VM.
    """
    if not is_hosted_darwin_runner():
        return
    artifacts = _storage_fault_artifacts()
    record_runner_storage("storage-fault")
    report = artifacts / "storage-fault.txt"
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
    kernel_log = _dump_kernel_storage_log(artifacts / "apfs-io.log", report)
    marker = artifacts / _DEVICE_FAULT_NAME
    if not marker.is_file() and (faults := device_fault_lines(kernel_log)):
        _record_device_fault(artifacts, faults)
    if os.environ.get("GITHUB_OUTPUT"):
        _outputs(device_fault=str(marker.is_file()).lower())


def _boot_epoch() -> int | None:
    """Return this machine's boot time in epoch seconds, or None if unreadable."""
    sysctl = shutil.which("sysctl")
    if sysctl is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603 -- resolved sysctl path
            [sysctl, "-n", "kern.boottime"],
            check=False,
            capture_output=True,
            text=True,
            timeout=_BOOT_QUERY_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = _BOOT_SECONDS.search(result.stdout) if result.returncode == 0 else None
    return int(match.group(1)) if match else None


def _kernel_log_window() -> list[str]:
    """Return ``log show`` arguments that cover this VM since boot.

    The window never exceeds the job cap, and it falls back to that cap when
    the boot time is unreadable or not in the past. A start after a fault
    would hide that fault, so a doubtful boot time never narrows the query.
    """
    now = time.time()
    boot = _boot_epoch()
    if boot is None or not now - _KERNEL_LOG_WINDOW_HOURS * 3600 <= boot <= now:
        return ["--last", _KERNEL_LOG_WINDOW]
    return ["--start", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(boot))]


def _dump_kernel_storage_log(log_path: Path, report: Path) -> str:
    """Retain this VM's storage kernel log since boot and return its text."""
    log_bin = shutil.which("log")
    if log_bin is None:
        log_path.write_text("log show missing\n", encoding="utf-8")
        return ""
    try:
        result = subprocess.run(  # noqa: S603 -- resolved log path
            [
                log_bin,
                "show",
                *_kernel_log_window(),
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
        return ""
    log_path.write_text(result.stdout + result.stderr, encoding="utf-8")
    with report.open("a", encoding="utf-8") as handle:
        handle.write(
            f"## log show exit={result.returncode} bytes={log_path.stat().st_size}\n"
        )
    return result.stdout


class HostedDeviceStateError(RuntimeError):
    """The kernel log could not show whether the hosted block device faulted."""


class HostedDeviceQueryTimeoutError(HostedDeviceStateError):
    """``log show`` ran but did not finish inside its budget."""


def device_fault_lines(text: str) -> list[str]:
    """Return kernel block-device I/O error lines from ``log show`` output."""
    return [line for line in text.splitlines() if _DEVICE_FAULT_LINE.search(line)]


def hosted_device_faults() -> list[str]:
    """Return this VM's kernel block-device I/O errors since boot.

    Fails closed: a missing or failing ``log show`` cannot prove the store
    sound, so it raises instead of reporting no fault. A query that outlives
    its budget raises :class:`HostedDeviceQueryTimeoutError`, so the caller
    can tell "too slow to answer" apart from "cannot answer".
    """
    log_bin = shutil.which("log")
    if log_bin is None:
        msg = "log is missing; cannot check the hosted block device"
        raise HostedDeviceStateError(msg)
    try:
        result = subprocess.run(  # noqa: S603 -- resolved log path
            [
                log_bin,
                "show",
                *_kernel_log_window(),
                "--style",
                "compact",
                "--predicate",
                _DEVICE_FAULT_PREDICATE,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=_DEVICE_FAULT_QUERY_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        msg = f"Kernel log query failed: {type(error).__name__}: {error}"
        raise HostedDeviceQueryTimeoutError(msg) from error
    except OSError as error:
        msg = f"Kernel log query failed: {type(error).__name__}: {error}"
        raise HostedDeviceStateError(msg) from error
    if result.returncode:
        msg = f"log show exited {result.returncode}: {result.stderr.strip()}"
        raise HostedDeviceStateError(msg)
    return device_fault_lines(result.stdout)


def _record_device_fault(artifacts: Path, faults: list[str]) -> Path:
    """Write the marker that keeps a faulted VM from publishing store paths."""
    marker = artifacts / _DEVICE_FAULT_NAME
    excerpt = "\n".join(faults[:_DEVICE_FAULT_EXCERPT_LINES])
    marker.write_text(
        f"{len(faults)} kernel block-device I/O errors\n{excerpt}\n",
        encoding="utf-8",
    )
    return marker


def _quarantine_faulted_runner(artifacts: Path, faults: list[str], log: TextIO) -> None:
    """Stop a VM whose block device faulted from publishing more store paths.

    The Cachix daemon uploads every realized path; paths realized after the
    fault can hold bad bytes under a valid registration. Killing it drops the
    queue instead of draining it. The marker makes ``flush-cachix`` publish
    nothing and hide the dead daemon from cachix-action's post hook.
    """
    _record_device_fault(artifacts, faults)
    _write_diagnostic(
        log,
        f"Hosted block device faulted ({len(faults)} kernel I/O errors, first: "
        f"{faults[0].strip()}); publishing nothing from this runner",
    )
    _drop_cachix_queue(log)


def _drop_cachix_queue(log: TextIO) -> None:
    """Kill the Cachix daemon without draining, so a bad store cannot upload."""
    daemon_dir = os.environ.get(_CACHIX_DAEMON_DIR_ENV, "").strip()
    if not daemon_dir:
        return
    try:
        pid = int((Path(daemon_dir) / "daemon.pid").read_text().strip())
    except (OSError, ValueError) as error:
        _write_diagnostic(log, f"Cachix daemon pid unavailable: {error}")
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    _write_diagnostic(log, f"Killed Cachix daemon pid={pid} without draining")


def _withhold_unverified_runner(
    artifacts: Path, error: HostedDeviceQueryTimeoutError, log: TextIO
) -> None:
    """Keep the stage result but publish nothing from an unverified store.

    A timed-out kernel log query says the VM was slow, not that its disk
    faulted, so the stage keeps its outcome. Its store is still unverified,
    and a corrupt fixed-output path in Cachix would be substituted by later
    validation instead of refetched and checked. The marker makes
    ``flush-cachix`` publish nothing, as a device fault does.
    """
    (artifacts / _DEVICE_UNVERIFIED_NAME).write_text(f"{error}\n", encoding="utf-8")
    _write_diagnostic(
        log,
        f"Hosted block device unverified ({error}); publishing nothing from "
        "this runner",
    )
    _drop_cachix_queue(log)
    sys.stdout.write(
        "::warning title=Hosted block device check timed out::"
        f"the kernel log query did not finish in {_DEVICE_FAULT_QUERY_SECONDS}s; "
        "this runner's result stands but none of its store paths are published\n"
    )
    sys.stdout.flush()


def _discard_runner_store_attempt(artifacts: Path, log: TextIO) -> None:
    """Keep a daemon or store fault from publishing this shard's paths.

    The fresh VM reruns the shard. Paths realized on this VM stay untrusted
    for the same reason a faulted disk's paths do: the attempt did not finish,
    and a crashed daemon can register a partial NAR.
    """
    (artifacts / _RUNNER_FAULT_NAME).write_text(
        "Nix daemon or store fault; this attempt is discarded\n",
        encoding="utf-8",
    )
    _write_diagnostic(
        log,
        "Hosted Nix daemon or store faulted; publishing nothing from this runner",
    )
    _drop_cachix_queue(log)


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


def _sibling_watch_configured(stage: str) -> bool:
    """Return whether this hosted stage should poll for a completed sibling failure."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return False
    if not os.environ.get("NIXCFG_ACTIONS_TOKEN", "").strip():
        return False
    if not os.environ.get("GITHUB_REPOSITORY", "") or not os.environ.get(
        "GITHUB_RUN_ID", ""
    ):
        return False
    return (
        sibling_failfast.failfast_group(
            stage=stage,
            system=os.environ.get("NIXCFG_SYSTEM", ""),
            scope=os.environ.get("NIXCFG_VALIDATE_SCOPE", "all"),
            canary=os.environ.get("NIXCFG_CANARY") == "true",
        )
        != ""
    )


def _poll_sibling_stop(stage: str, log: TextIO) -> sibling_failfast.SiblingStop | None:
    """Read the current attempt's jobs and return a sibling stop, or nothing.

    A poll error keeps the build running. The log names the error type, not
    the token or the response body.
    """
    group = sibling_failfast.failfast_group(
        stage=stage,
        system=os.environ.get("NIXCFG_SYSTEM", ""),
        scope=os.environ.get("NIXCFG_VALIDATE_SCOPE", "all"),
        canary=os.environ.get("NIXCFG_CANARY") == "true",
    )
    try:
        jobs = sibling_failfast.fetch_workflow_jobs(
            repository=os.environ.get("GITHUB_REPOSITORY", ""),
            run_id=os.environ.get("GITHUB_RUN_ID", ""),
            token=os.environ.get("NIXCFG_ACTIONS_TOKEN", ""),
        )
    except sibling_failfast.SiblingPollError as error:
        _write_diagnostic(log, f"Sibling failure poll skipped: {error}")
        return None
    return sibling_failfast.sibling_stop(group, jobs)


def _record_sibling_stop(
    log: TextIO, artifacts: Path, stop: sibling_failfast.SiblingStop
) -> None:
    """Persist and annotate the sibling that already blocked publish."""
    payload = {
        "conclusion": stop.conclusion,
        "failedStep": stop.failed_step,
        "group": stop.group,
        "sibling": stop.job,
    }
    (artifacts / sibling_failfast.SIBLING_STOP_FILENAME).write_text(
        json.dumps(payload, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    message = sibling_failfast.stop_message(stop)
    _write_diagnostic(log, f"::error title=Sibling real failure::{message}")


def _sibling_stop_recorded(artifacts: Path) -> bool:
    """Return whether this stage already recorded a sibling stop."""
    return (artifacts / sibling_failfast.SIBLING_STOP_FILENAME).is_file()


def _process_is_group_leader(pid: int) -> bool:
    """Return whether *pid* is its own process-group leader."""
    try:
        return os.getpgid(pid) == pid
    except OSError:
        return False


def _signal_updater(process: subprocess.Popen[str], sig: int) -> None:
    """Signal the updater and the Nix children started in its session."""
    if process.poll() is not None:
        return
    if _process_is_group_leader(process.pid):
        try:
            os.killpg(process.pid, sig)
        except OSError:
            process.send_signal(sig)
        return
    try:
        process.send_signal(sig)
    except OSError:
        return


def _terminate_updater(process: subprocess.Popen[str]) -> None:
    """Stop the updater process group so a sibling failure does not keep building."""
    _signal_updater(process, signal.SIGTERM)
    try:
        process.wait(timeout=_SIBLING_STOP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        _signal_updater(process, signal.SIGKILL)
        try:
            process.wait(timeout=_SIBLING_STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            return


def _abort_for_sibling_failure(
    stage: str,
    process: subprocess.Popen[str],
    log: TextIO,
    artifacts: Path,
    poll_clock: list[float],
    *,
    watch: bool,
) -> bool:
    """Stop *process* when a required sibling has completed in real failure."""
    if not watch or process.poll() is not None:
        return False
    now = time.monotonic()
    if now - poll_clock[0] < _SIBLING_POLL_SECONDS:
        return False
    poll_clock[0] = now
    stop = _poll_sibling_stop(stage, log)
    if stop is None:
        return False
    try:
        _record_sibling_stop(log, artifacts, stop)
    finally:
        # A record failure must still kill the updater. Otherwise the stderr
        # reader blocks and the job never reaches the sibling annotation.
        _terminate_updater(process)
    return True


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
    watch = _sibling_watch_configured(stage)
    poll_clock = [time.monotonic()]
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
        daemon=True,
    )
    reader.start()
    # None waits for the reader. A sibling stop bounds that wait so a blocked
    # stderr read cannot hold the job open after the updater has been signaled.
    reader_join_timeout: list[float | None] = [None]
    try:
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
                if _abort_for_sibling_failure(
                    stage, process, log, artifacts, poll_clock, watch=watch
                ):
                    reader_join_timeout[0] = _SIBLING_STOP_GRACE_SECONDS
                    return 1
                continue
            if diagnostic is None:
                break
            _forward_text(log, diagnostic)
            if _abort_for_sibling_failure(
                stage, process, log, artifacts, poll_clock, watch=watch
            ):
                reader_join_timeout[0] = _SIBLING_STOP_GRACE_SECONDS
                return 1
    finally:
        reader.join(timeout=reader_join_timeout[0])
    _forward_text(log, _read_new_run_log_text(run_logs, offsets))
    return process.wait()


def _prefetch_publication_limit(timeout: float | None) -> tuple[float, int]:
    """Return one attempt's timeout and how many times to push.

    Flush passes the time left in its budget and must not multiply that
    budget. Prepare omits *timeout*; that path is the one 43695499090 left
    unbounded, so it uses the flush client's bound and retries.
    """
    if timeout is None:
        return _CACHIX_PUSH_TIMEOUT_SECONDS, _CACHIX_PUSH_ATTEMPTS
    return timeout, 1


_PREFETCH_PUSH_NAME_LIMIT = 8


def _prefetch_push_names(paths: list[str]) -> str:
    """Name a short push in the timeout diagnostic; keep a long one countable."""
    names = [Path(path).name for path in paths]
    if len(names) <= _PREFETCH_PUSH_NAME_LIMIT:
        return ", ".join(names)
    return f"{len(names)} paths, last {names[-1]}"


def _stop_prefetch_push(
    process: subprocess.Popen[str] | subprocess.Popen[bytes],
) -> None:
    """Kill a wedged ``cachix push`` and the workers in its own session."""
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        process.kill()
    try:
        process.wait(timeout=_SIBLING_STOP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _push_prefetched_paths(
    paths: list[str],
    log: TextIO,
    artifacts: Path,
    timeout: float,
) -> None:
    """Publish raw imports while retaining liveness evidence for quiet Cachix uploads.

    *timeout* kills a stalled push. The push runs in its own session so the
    kill reaches upload workers and does not signal the updater. A stall
    reports ``_TIMEOUT_RETURNCODE``.
    """
    args = ["cachix", "push", _BINARY_CACHE, *paths]
    started = time.monotonic()
    names = _prefetch_push_names(paths)
    _write_diagnostic(
        log,
        f"Publishing {len(paths)} prefetched paths cache={_BINARY_CACHE} "
        f"artifacts={artifacts}",
    )
    with subprocess.Popen(  # noqa: S603 -- fixed Cachix invocation.
        args, start_new_session=True
    ) as process:
        while True:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                _stop_prefetch_push(process)
                _write_diagnostic(
                    log,
                    f"Cachix publication timed out pid={process.pid} "
                    f"after {timeout:.0f}s paths={len(paths)} ({names})",
                )
                raise subprocess.CalledProcessError(_TIMEOUT_RETURNCODE, args)
            try:
                returncode = process.wait(
                    timeout=min(_HEARTBEAT_INTERVAL_SECONDS, remaining)
                )
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
            "fod-canary, leaf-prebuild, or zed-warmup"
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
            "leaf-prebuild",
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
    receipts: Path,
    log: TextIO,
    artifacts: Path,
    returncode: int,
    timeout: float | None = None,
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
    limit, attempts = _prefetch_publication_limit(timeout)
    attempt = 1
    while True:
        try:
            _push_prefetched_paths(paths, log, artifacts, limit)
        except subprocess.CalledProcessError as error:
            _write_diagnostic(
                log,
                "Cachix prefetch publication "
                f"attempt {attempt}/{attempts} failed "
                f"returncode={error.returncode} paths={len(paths)} "
                f"({_prefetch_push_names(paths)})",
            )
            if attempt >= attempts:
                if returncode:
                    _write_diagnostic(
                        log,
                        "Prefetch publication failed after updater failure; "
                        f"retaining updater status cache_exit={error.returncode}",
                    )
                    return returncode
                raise
            attempt += 1
        else:
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


def _merge_prepare_shard_args(artifacts: Path) -> list[str]:
    """Return argv that folds same-platform prepare shards."""
    shards = os.environ.get("NIXCFG_PREPARE_SHARDS", "")
    if not shards:
        msg = "Same-platform prepare merge requires NIXCFG_PREPARE_SHARDS"
        raise ValueError(msg)
    return [
        _runtime(),
        "ci",
        "update",
        "merge-prepare-shards",
        "--shards",
        shards,
        "--output",
        str(artifacts / "candidate.json"),
    ]


def _append_prepare_shard_args(args: list[str], stage: str) -> None:
    """Add the public-pool slice flags before the target separator."""
    parsed = hosted_prepare_shard(
        os.environ.get("NIXCFG_PREPARE_SHARD", ""),
        os.environ.get("NIXCFG_PREPARE_SHARD_COUNT", ""),
    )
    if parsed is None:
        return
    if stage != "prepare":
        msg = "Prepare shards are only valid for the native hash stage"
        raise ValueError(msg)
    shard, count = parsed
    args.extend((
        "--prepare-shard",
        str(shard),
        "--prepare-shard-count",
        str(count),
    ))


def _merged_native_args(stage: str, artifacts: Path) -> list[str]:
    """Return argv for a candidate fold that does not hash."""
    if stage == "merge-candidates":
        return _merge_candidate_args(artifacts)
    return _merge_prepare_shard_args(artifacts)


def _native_args(stage: str, artifacts: Path) -> list[str]:
    """Return the updater argv for one hosted native stage."""
    if stage in {"merge-candidates", "merge-prepare-shards"}:
        return _merged_native_args(stage, artifacts)
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
        _append_prepare_shard_args(args, stage)
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
            # A new session lets a sibling stop signal Nix children with the
            # updater. Local and unwatched runs stay in the caller's group.
            start_new_session=_sibling_watch_configured(stage),
        ) as process:
            returncode = _wait_for_diagnostics(process, log, stage, artifacts, args)
        _write_diagnostic(
            log, f"Updater finished stage={stage} returncode={returncode}"
        )
        record_runner_storage(f"after-native-{stage}", log, detail=False, live=False)
        return _finish_native_stage(stage, artifacts, receipts, log, returncode)


def _finish_native_stage(
    stage: str,
    artifacts: Path,
    receipts: Path,
    log: TextIO,
    returncode: int,
) -> int:
    """Hand off the updater result without turning a sibling stop into a retry.

    A sibling stop already blocked publish. Prefetch receipts still upload so
    the next attempt can substitute them. A device-fault retry stays reserved
    for a prepare shard, warmup slot or root shard that finished on a faulted
    disk with no sibling failure. A validate retry that faults again fails. A
    prepare device check that times out keeps the updater's outcome and
    publishes nothing; a validate slot's still fails.
    """
    if not _sibling_stop_recorded(artifacts) and (
        summary := _failure_summary(artifacts / "result.json")
    ):
        _write_diagnostic(log, summary)
        _write_diagnostic(log, f"::error title=Updater failed::{summary}")
    if _sibling_stop_recorded(artifacts):
        _write_diagnostic(
            log,
            "Sibling real failure recorded; this result is not a device-fault retry",
        )
        return _publish_prefetched_receipts(receipts, log, artifacts, returncode or 1)
    unverified = False
    # A green updater on a faulted disk still hashed untrusted bytes.
    if _hands_off_device_faults(stage) and is_hosted_darwin_runner():
        try:
            faults = hosted_device_faults()
        except HostedDeviceQueryTimeoutError as error:
            # A warmup slot or root shard exists to publish; withholding would
            # only move its failure to a later --max-jobs 0 consumer.
            if stage != "prepare":
                raise
            _withhold_unverified_runner(artifacts, error, log)
            unverified = True
        else:
            if faults:
                _quarantine_faulted_runner(artifacts, faults, log)
                if (
                    stage == "validate"
                    and os.environ.get(_DEVICE_FAULT_RETRY_ENV) == "true"
                ):
                    return _fail_device_fault_retry(faults, returncode)
                return _hand_off_device_fault(faults, returncode)
    if returncode and _hands_off_runner_store_fault(stage, artifacts):
        return _hand_off_runner_store_fault(log, artifacts, returncode)
    if unverified:
        return returncode
    return _publish_prefetched_receipts(receipts, log, artifacts, returncode)


def _hands_off_device_faults(stage: str) -> bool:
    """Return whether a fresh-VM retry job can redo this stage's exact work.

    Prepare shards, the rust/cascade warmup slots and the planned root
    shards have one. Canary, package and aggregate closure validation do
    not, so a fault there still fails the job.
    """
    if stage == "prepare":
        return True
    return (
        stage == "validate"
        and os.environ.get("NIXCFG_VALIDATE_SCOPE", "all") in _DEVICE_FAULT_RETRY_SCOPES
        and os.environ.get("NIXCFG_CANARY") != "true"
    )


def _hands_off_runner_store_fault(stage: str, artifacts: Path) -> bool:
    """Return whether this Darwin prepare shard asked for a fresh-VM retry.

    Only ``prepare`` has ``prepare-darwin-retry-N``. A pin refresh or a Linux
    prepare has no second VM, so those failures still stop the group.
    """
    if stage != "prepare" or not is_hosted_darwin_runner():
        return False
    return _result_requests_runner_retry(artifacts / "result.json")


def _result_requests_runner_retry(result_path: Path) -> bool:
    """Return whether the updater JSON named a runner store fault."""
    try:
        result = json.loads(result_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(result, dict) and result.get("runnerStoreFault") is True


def _hand_off_runner_store_fault(log: TextIO, artifacts: Path, returncode: int) -> int:
    """Discard this shard and let a fresh VM redo it before sibling-stop.

    Exiting 0 with ``runner_fault=true`` is what keeps the other prepare jobs
    alive. The uploaded candidate uses a ``-runner-fault`` name, so merge
    cannot consume it. A retry that faults again also exits 0 with the flag,
    and merge then refuses the shard. Without a step output nothing can hand
    the work off, so the stage fails instead.
    """
    _discard_runner_store_attempt(artifacts, log)
    if not os.environ.get("GITHUB_OUTPUT"):
        return returncode or 1
    _outputs(runner_fault="true")
    sys.stdout.write(
        "::warning title=Hosted Nix daemon faulted::"
        "this prepare shard lost the Nix daemon or the store; "
        "its result is discarded and the shard reruns on a fresh VM\n"
    )
    sys.stdout.flush()
    return 0


def _hand_off_device_fault(faults: list[str], returncode: int) -> int:
    """Report a device fault as this stage's outcome; a fresh VM redoes the work.

    The result is never consumed: it uploads under a ``-device-fault`` name
    and the consumers require a fault-free attempt. Without a step output
    nothing can hand the work off, so the stage fails instead.
    """
    if not os.environ.get("GITHUB_OUTPUT"):
        return returncode or 1
    _outputs(device_fault="true")
    sys.stdout.write(
        "::warning title=Hosted block device faulted::"
        f"{len(faults)} kernel I/O errors; this runner's result is discarded "
        "and the stage reruns on a fresh VM\n"
    )
    sys.stdout.flush()
    return 0


def _fail_device_fault_retry(faults: list[str], returncode: int) -> int:
    """Fail a validate retry whose fresh VM faulted too; there is no third attempt."""
    sys.stdout.write(
        "::error title=Hosted block device faulted on retry::"
        f"{len(faults)} kernel I/O errors on the fresh-VM retry; "
        "this slot has no result\n"
    )
    sys.stdout.flush()
    return returncode or 1


def _device_fault_slot_names() -> tuple[str, tuple[str, ...]]:
    """Return the artifact prefix and slots ``device-fault-slots`` inspects."""
    prefix = os.environ.get("NIXCFG_FAULT_ARTIFACT_PREFIX", "").strip()
    slots = tuple(os.environ.get("NIXCFG_FAULT_SLOTS", "").split())
    if not prefix or not slots or not all(slot.isdigit() for slot in slots):
        msg = (
            "device-fault-slots requires NIXCFG_FAULT_ARTIFACT_PREFIX and "
            "digit NIXCFG_FAULT_SLOTS"
        )
        raise RuntimeError(msg)
    return prefix, slots


def _read_artifact_page(url: str, token: str) -> dict[str, object]:
    """Read one run-artifacts page, retrying transient HTTP failures."""
    request = urllib.request.Request(  # noqa: S310 -- fixed https://api.github.com URL
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    error: Exception | None = None
    for attempt in range(_ARTIFACT_HTTP_ATTEMPTS):
        if attempt:
            time.sleep(_ARTIFACT_HTTP_RETRY_SECONDS)
        try:
            with urllib.request.urlopen(  # noqa: S310 -- fixed https://api.github.com URL
                request, timeout=_ARTIFACT_HTTP_TIMEOUT_SECONDS
            ) as response:
                payload = json.loads(response.read())
        except (OSError, ValueError) as caught:
            error = caught
            continue
        if isinstance(payload, dict):
            return payload
        error = ValueError("artifact list is not an object")
    msg = f"could not list this run's artifacts: {type(error).__name__}: {error}"
    raise RuntimeError(msg)


def run_artifact_names() -> set[str]:
    """Return every artifact name uploaded to this workflow run so far."""
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    token = os.environ.get("NIXCFG_ACTIONS_TOKEN", "").strip()
    if not repository or not run_id.isdigit() or not token:
        msg = (
            "listing run artifacts requires GITHUB_REPOSITORY, GITHUB_RUN_ID "
            "and NIXCFG_ACTIONS_TOKEN"
        )
        raise RuntimeError(msg)
    names: set[str] = set()
    for page in range(1, _ARTIFACT_MAX_PAGES + 1):
        payload = _read_artifact_page(
            f"https://api.github.com/repos/{repository}/actions/runs/{run_id}"
            f"/artifacts?per_page={_ARTIFACT_PAGE_SIZE}&page={page}",
            token,
        )
        items = payload.get("artifacts")
        total = payload.get("total_count")
        if not isinstance(items, list) or not isinstance(total, int):
            msg = "artifact list is missing artifacts or total_count"
            raise TypeError(msg)
        names.update(
            item["name"]
            for item in items
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        )
        if len(items) < _ARTIFACT_PAGE_SIZE or page * _ARTIFACT_PAGE_SIZE >= total:
            return names
    msg = f"artifact list exceeds {_ARTIFACT_MAX_PAGES} pages"
    raise RuntimeError(msg)


def device_fault_slots() -> None:
    """Emit the warmup slots whose first attempt handed off a device fault.

    A matrix cell cannot publish its own job output, so the faulted slot is
    read back from its ``<prefix><slot>-device-fault`` artifact. An answer
    that cannot be read fails this job, and the consumers then do not run.
    """
    prefix, slots = _device_fault_slot_names()
    names = run_artifact_names()
    faulted = [
        int(slot)
        for slot in slots
        if f"{prefix}{slot}{_DEVICE_FAULT_ARTIFACT_SUFFIX}" in names
    ]
    sys.stdout.write(f"Device-faulted slots for {prefix}*: {faulted}\n")
    _outputs(slots=json.dumps(faulted, separators=(",", ":")))


def _planned_closure_shards() -> tuple[str, list[dict[str, str]]]:
    """Return the artifact prefix and planned shards ``device-fault-shards`` inspects.

    The shards are ``plan-darwin-closures``' matrix entries, so a retry cell
    reruns one with exactly the inputs its first attempt had.
    """
    prefix = os.environ.get("NIXCFG_FAULT_ARTIFACT_PREFIX", "").strip()
    try:
        matrix = json.loads(os.environ.get("NIXCFG_FAULT_MATRIX", ""))
    except json.JSONDecodeError:
        matrix = None
    include = matrix.get("include") if isinstance(matrix, dict) else None
    if (
        not prefix
        or not isinstance(include, list)
        or not include
        or not all(
            isinstance(entry, dict)
            and set(entry) == {"shard", "roots", "system"}
            and all(isinstance(value, str) and value for value in entry.values())
            for entry in include
        )
    ):
        msg = (
            "device-fault-shards requires NIXCFG_FAULT_ARTIFACT_PREFIX and a "
            "NIXCFG_FAULT_MATRIX of shard/roots/system entries"
        )
        raise RuntimeError(msg)
    return prefix, cast("list[dict[str, str]]", include)


def device_fault_shards() -> None:
    """Emit the planned root shards whose first attempt handed off a device fault.

    Root shards are named by the plan, not numbered, so the faulted entries
    are emitted whole as a ``strategy.matrix.include`` list. An answer that
    cannot be read fails this job, and the consumers then do not run.
    """
    prefix, shards = _planned_closure_shards()
    names = run_artifact_names()
    faulted = [
        entry
        for entry in shards
        if f"{prefix}{entry['shard']}{_DEVICE_FAULT_ARTIFACT_SUFFIX}" in names
    ]
    sys.stdout.write(
        f"Device-faulted shards for {prefix}*: {[entry['shard'] for entry in faulted]}\n"
    )
    _outputs(shards=json.dumps(faulted, separators=(",", ":")))


def _candidate_identity() -> dict[str, str]:
    """Return the identity of the candidate the Linux prepare runner certified."""
    candidate = _temp() / "evidence" / "prepare-x86_64-linux" / "candidate.json"
    return cast("dict[str, str]", json.loads(candidate.read_bytes()))


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
    if not patch.stat().st_size and not (
        os.environ["GITHUB_REF_NAME"].startswith("codex/update-repair-")
        or os.environ.get("NIXCFG_REPAIR_COMMIT")
    ):
        _outputs(changed="false")
        return
    identity = _candidate_identity()
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
    baseline_tree: str | None = None,
    repair_commit: str | None = None,
) -> str:
    branch = (
        os.environ["GITHUB_REF_NAME"]
        if repair_commit is not None
        else f"codex/update-{kind}{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}"
    )
    if repair_commit is not None and not branch.startswith("codex/update-repair-"):
        msg = "Approved repair publication must keep its repair branch as the PR head"
        raise ValueError(msg)
    if base is None:
        if repair_commit is not None:
            msg = "Repair publication requires its original base branch"
            raise ValueError(msg)
        _run("git", "switch", "-c", branch)
    else:
        if tree is None or baseline_tree is None:
            msg = (
                "Publishing from a base requires a certified tree and the "
                "baseline it was certified against"
            )
            raise ValueError(msg)
        _run("git", "fetch", "origin", base)
        # The certified tree is published whole, so the branch it lands on must
        # still be the baseline it was certified against. Otherwise the commit
        # silently reverts everything the base gained in the meantime.
        target_tree = _run(
            "git", "rev-parse", f"origin/{base}^{{tree}}", capture=True
        ).stdout.strip()
        if target_tree != baseline_tree:
            msg = (
                f"Base branch {base} no longer matches the certified baseline; "
                "publishing the certified tree would revert the commits that "
                "landed since certification"
            )
            raise ValueError(msg)
        _run(
            "git",
            "switch",
            "--discard-changes",
            "-c",
            branch,
            repair_commit if repair_commit is not None else f"origin/{base}",
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


def _quarantine_publication_note() -> str:
    """Count packages the sealed candidate held at their last-good pins."""
    candidate = _temp() / "evidence" / "prepare-x86_64-linux" / "candidate.json"
    payload = json.loads(candidate.read_bytes())
    quarantines = payload.get("quarantines")
    if not isinstance(quarantines, list):
        quarantines = []
    lines = [
        f"Quarantined packages held at last-good pins: {len(quarantines)}",
        "",
    ]
    for item in quarantines:
        if not isinstance(item, dict):
            continue
        lines.append(f"- {item.get('package')} ({item.get('phase')})")
    if quarantines:
        lines.append("")
    return "\n".join(lines)


def sync_quarantines() -> None:
    """Open or refresh one issue per quarantined package, and close clean ones.

    Missing evidence is a no-op. The publish job runs this after coverage and
    before certify, including when an earlier step failed, so a package that
    only broke during validation still gets an issue.
    """
    evidence = _temp() / "evidence"
    candidate = evidence / "prepare-x86_64-linux" / "candidate.json"
    if not candidate.is_file() and not any(evidence.glob("validate-*/validation.json")):
        return
    _run(
        _runtime(),
        "ci",
        "update",
        "sync-quarantines",
        "--evidence",
        str(evidence),
    )


def publish() -> None:
    """Open the verified update as a PR against the default branch and squash-merge it.

    Normal exercise runs keep source refreshes off the feature branch under
    review. Repair follow-ups keep the approved repair branch as PR head and use
    its parent tree as the default-branch baseline, preserving auto-mergeability.
    """
    base = os.environ["UPDATE_BASE_BRANCH"]
    tree = _run("git", "write-tree", capture=True).stdout.strip()
    identity = _candidate_identity()
    baseline_tree = identity["base_tree"]
    repair_commit = os.environ.get("NIXCFG_REPAIR_COMMIT", "").strip() or None
    if repair_commit is not None:
        approved_tree = _run(
            "git", "rev-parse", f"{repair_commit}^{{tree}}", capture=True
        ).stdout.strip()
        if approved_tree != identity["base_tree"]:
            msg = "Approved repair commit does not match the candidate repair baseline"
            raise ValueError(msg)
        # The repair commit sits on the original certified main checkout. Its
        # parent, not the repair tree, is the base that a whole-tree PR may replace.
        baseline_tree = _run(
            "git", "rev-parse", f"{repair_commit}^1^{{tree}}", capture=True
        ).stdout.strip()
    branch = _commit_and_push(
        "",
        "chore(update): refresh validated sources",
        base=base,
        tree=tree,
        baseline_tree=baseline_tree,
        repair_commit=repair_commit,
    )
    body = _temp() / "update-body.md"
    run_url = (
        f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}"
        f"/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    )
    body.write_text(
        "Prepared and validated against one Git tree on three native platforms.\n\n"
        f"{_quarantine_publication_note()}"
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
    tree = _run("git", "write-tree", capture=True).stdout.strip()
    if not tree:
        msg = "Repair proposal did not produce a Git tree receipt"
        raise ValueError(msg)
    receipt = {
        "tree": tree,
        "patchSha256": hashlib.sha256(patch.read_bytes()).hexdigest(),
    }
    (_temp() / _REPAIR_RECEIPT_NAME).write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    _outputs(tree=tree)


def _approved_repair_tree(patch: Path) -> str:
    """Verify the exact artifact and tree approved for credentialed repair."""
    expected = os.environ.get("NIXCFG_REPAIR_TREE", "").strip()
    if not expected:
        msg = "NIXCFG_REPAIR_TREE is required for approved repair publication"
        raise ValueError(msg)
    try:
        receipt = json.loads((_temp() / _REPAIR_RECEIPT_NAME).read_text())
    except (OSError, json.JSONDecodeError) as error:
        msg = "Approved repair artifact is missing its tree receipt"
        raise ValueError(msg) from error
    digest = hashlib.sha256(patch.read_bytes()).hexdigest()
    if (
        not isinstance(receipt, dict)
        or receipt.get("tree") != expected
        or receipt.get("patchSha256") != digest
    ):
        msg = "Repair artifact does not match the approved tree and patch"
        raise ValueError(msg)
    return expected


def commit_heal() -> None:
    """Commit an approved repair onto the update branch that failed.

    The commit stays on that branch. This path does not open a pull request,
    does not start a second branch, and does not force-push.
    """
    branch = os.environ.get("GITHUB_REF_NAME", "")
    if not branch.startswith("update/"):
        msg = "Heal commits stay on the update branch that failed"
        raise ValueError(msg)
    patch = _temp() / "repair.patch"
    approved_tree = _approved_repair_tree(patch)
    _run("git", "apply", "--index", "--binary", str(patch))
    _run(*_develop(_runtime(), "ci", "update", "check-repair"))
    if _run("git", "write-tree", capture=True).stdout.strip() != approved_tree:
        msg = "Applied repair does not match the approved tree"
        raise ValueError(msg)
    if _run("git", "diff", "--cached", "--quiet", check=False).returncode:
        _run(
            *_develop(
                "git", "commit", "-S", "-m", "fix(update): repair packaging failure"
            )
        )
    committed_tree = _run(
        "git", "rev-parse", "HEAD^{tree}", capture=True
    ).stdout.strip()
    if committed_tree != approved_tree:
        msg = "Committed repair does not match the approved tree"
        raise ValueError(msg)
    _run("gh", "auth", "setup-git")
    _run("git", "push", "origin", "HEAD")


def start_repair() -> None:
    """Commit the repair job's checked patch and start a fresh attempt.

    The repair job holds no write credential, so this job applies its patch.
    The environment-approved artifact receipt binds this exact tree before any
    write. The fresh attempt has repair disabled, bounding automatic retries.
    """
    patch = _temp() / "repair.patch"
    approved_tree = _approved_repair_tree(patch)
    _run("git", "apply", "--index", "--binary", str(patch))
    _run(*_develop(_runtime(), "ci", "update", "check-repair"))
    if _run("git", "write-tree", capture=True).stdout.strip() != approved_tree:
        msg = "Applied repair does not match the approved tree"
        raise ValueError(msg)
    branch = _commit_and_push("repair-", "fix(update): repair packaging failure")
    commit = _run("git", "rev-parse", "HEAD", capture=True).stdout.strip()
    committed_tree = _run(
        "git", "rev-parse", "HEAD^{tree}", capture=True
    ).stdout.strip()
    if not commit or committed_tree != approved_tree:
        msg = "Committed repair does not match the approved tree"
        raise ValueError(msg)
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
        f"repair_commit={commit}",
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
        # Rewrite GITHUB_ENV only in the hosted flush-cachix process. Tests
        # never see the hosted file (conftest.py, 39113211468).
        if github_env and os.environ.get("NIXCFG_CI_STAGE") == "flush-cachix":
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


def _cachix_push(
    paths: tuple[str, ...], label: str, log: TextIO, budget: _FlushBudget
) -> subprocess.CompletedProcess[str]:
    """``cachix push`` *paths* in bounded, retried chunks.

    Return the first chunk that failed every attempt, else the last success.
    """
    result = subprocess.CompletedProcess(["cachix", "push", _BINARY_CACHE], 0, "", "")
    for start in range(0, len(paths), _CACHIX_PUSH_CHUNK):
        chunk = paths[start : start + _CACHIX_PUSH_CHUNK]
        for attempt in range(1, _CACHIX_PUSH_ATTEMPTS + 1):
            started = time.monotonic()
            result = _run(
                "cachix",
                "push",
                _BINARY_CACHE,
                *chunk,
                check=False,
                capture=True,
                timeout=budget.timeout(_CACHIX_PUSH_TIMEOUT_SECONDS),
            )
            _write_diagnostic(
                log,
                f"cachix push {label} chunk={start // _CACHIX_PUSH_CHUNK} "
                f"attempt={attempt}/{_CACHIX_PUSH_ATTEMPTS} paths={len(chunk)} "
                f"returncode={result.returncode} "
                f"elapsed={time.monotonic() - started:.0f}s",
            )
            if result.returncode == 0:
                break
        else:
            return result
    return result


def _publish_unpushed_warm_paths(
    daemon_log: str, log: TextIO, budget: _FlushBudget
) -> None:
    """``cachix push`` leftovers and realized receipts after any job outcome.

    Failed cascade/rust-warmup jobs still built paths the post-build hook
    queued. Flush then stops the daemon and clears ``CACHIX_DAEMON_DIR``
    so the action post logs ``Cachix Daemon not started. Skipping push``.
    Direct ``cachix push`` of leftovers plus ``realized-root-paths.json``
    keeps that warm work even when the daemon is already gone.
    """
    leftover = unfinished_cachix_daemon_paths(daemon_log)
    if leftover:
        _write_diagnostic(
            log,
            f"retrying {len(leftover)} unpushed Cachix paths from daemon.log",
        )
        push = _cachix_push(leftover, "leftover", log, budget)
        _write_diagnostic(
            log,
            f"cachix push leftover returncode={push.returncode} "
            f"paths={' '.join(leftover)} stderr={push.stderr.strip()}",
        )
        if push.returncode:
            _write_diagnostic(
                log,
                "leftover warmup push failed; continuing with recorded receipts",
            )
    _push_realized_root_paths(log, budget)


def _ultimate_store_paths(sigs_listing: str) -> tuple[str, ...]:
    """Return locally built outputs from ``nix path-info --all --sigs``.

    Nix marks each path this store built itself ``ultimate``; substituted
    paths carry only signatures. The post-build hook never pushes ``.drv``s.
    """
    paths: list[str] = []
    for line in sigs_listing.splitlines():
        fields = line.split()
        if (
            len(fields) > 1
            and "ultimate" in fields[1:]
            and _STORE_PATH_RE.fullmatch(fields[0])
            and not fields[0].endswith(".drv")
        ):
            paths.append(fields[0])
    return tuple(dict.fromkeys(paths))


def _locally_built_store_paths(budget: _FlushBudget) -> tuple[str, ...]:
    """Return every non-derivation path this runner's store built itself."""
    listing = _run(
        "nix",
        "path-info",
        "--all",
        "--sigs",
        check=False,
        capture=True,
        timeout=budget.timeout(_STORE_QUERY_TIMEOUT_SECONDS),
    )
    if listing.returncode:
        msg = (
            "could not list locally built store paths "
            f"returncode={listing.returncode} stderr={listing.stderr.strip()}"
        )
        raise CachixFlushError(msg)
    return _ultimate_store_paths(listing.stdout)


def _narinfo_status(url: str, timeout: float) -> int:
    """Return the HTTP status of a binary-cache narinfo ``HEAD``."""
    request = urllib.request.Request(  # noqa: S310 -- fixed https substituters
        url, method="HEAD", headers={"User-Agent": _NARINFO_USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 -- fixed https substituters
            return int(response.status)
    except urllib.error.HTTPError as error:
        return error.code


def _path_substitutable(
    path: str,
    budget: _FlushBudget,
    status: Callable[[str, float], int],
) -> bool:
    """Return whether gkze or its upstream serves a narinfo for *path*.

    Only 200 proves presence and only 404 proves absence; anything else is
    retried and then counted as absent, so verification fails closed.
    """
    digest = path.removeprefix(f"{_STORE_PATH_PREFIX}/").split("-", 1)[0]
    for cache in _RECOVERY_SUBSTITUTERS:
        url = f"{cache}/{digest}.narinfo"
        for attempt in range(1, _NARINFO_ATTEMPTS + 1):
            try:
                code = status(url, budget.timeout(_NARINFO_TIMEOUT_SECONDS))
            except OSError:
                code = 0
            if code == 200:  # noqa: PLR2004 -- HTTP OK
                return True
            if code == 404:  # noqa: PLR2004 -- HTTP Not Found
                break
            if attempt < _NARINFO_ATTEMPTS:
                time.sleep(attempt)
    return False


def _unsubstitutable_paths(
    paths: tuple[str, ...],
    budget: _FlushBudget,
    status: Callable[[str, float], int] | None = None,
) -> tuple[str, ...]:
    """Return the *paths* that neither gkze nor cache.nixos.org can substitute."""
    fetch = _narinfo_status if status is None else status
    with ThreadPoolExecutor(max_workers=_NARINFO_WORKERS) as pool:
        served = list(
            pool.map(lambda path: _path_substitutable(path, budget, fetch), paths)
        )
    return tuple(path for path, ok in zip(paths, served, strict=True) if not ok)


def _recover_undrained_daemon_paths(log: TextIO, budget: _FlushBudget) -> None:
    """Republish and verify every locally built path after an unconfirmed drain.

    The daemon logs a queued path only once a worker starts it, so neither
    its log nor its exit status names what a wedged queue dropped. Nix marks
    every output this runner built ``ultimate``; the daemon starts before any
    build, so that set covers everything the post-build hook queued.
    """
    paths = _locally_built_store_paths(budget)
    _write_diagnostic(
        log,
        f"republishing {len(paths)} locally built paths after an unconfirmed "
        "Cachix daemon drain",
    )
    if not paths:
        return
    push = _cachix_push(paths, "locally-built", log, budget)
    if push.returncode:
        msg = (
            "cachix push of locally built paths failed after an unconfirmed "
            f"daemon drain returncode={push.returncode} "
            f"stderr={push.stderr.strip()}"
        )
        raise CachixFlushError(msg)
    missing = _unsubstitutable_paths(paths, budget)
    if missing:
        msg = (
            f"{len(missing)} locally built paths are on neither gkze.cachix.org "
            f"nor cache.nixos.org after push: {', '.join(missing[:20])}"
        )
        _write_diagnostic(log, msg)
        raise CachixFlushError(msg)
    _write_diagnostic(
        log,
        f"verified {len(paths)} locally built paths on gkze.cachix.org or "
        "cache.nixos.org",
    )


def _terminate_cachix_daemon(log: TextIO) -> None:
    """Stop a daemon that did not exit through ``cachix daemon stop``.

    A wedged daemon keeps its socket and pid; cachix-action's post hook would
    otherwise run a second unbounded stop against it.
    """
    daemon_dir = os.environ.get(_CACHIX_DAEMON_DIR_ENV, "").strip()
    try:
        pid = int((Path(daemon_dir) / "daemon.pid").read_text().strip())
    except (OSError, ValueError):
        _write_diagnostic(log, f"no readable daemon.pid under {daemon_dir!r}")
        return
    command = _run(
        "ps",
        "-ww",
        "-p",
        str(pid),
        "-o",
        "command=",
        check=False,
        capture=True,
        timeout=_CACHIX_QUERY_TIMEOUT_SECONDS,
    )
    if command.returncode or "cachix" not in command.stdout:
        _write_diagnostic(log, f"Cachix daemon pid={pid} already exited")
        return
    try:
        # SIGCONT lets a stopped daemon act on SIGTERM.
        os.kill(pid, signal.SIGTERM)
        os.kill(pid, signal.SIGCONT)
        deadline = time.monotonic() + _CACHIX_DAEMON_EXIT_GRACE_SECONDS
        while time.monotonic() < deadline:
            os.kill(pid, 0)
            time.sleep(0.1)
        os.kill(pid, signal.SIGKILL)
        _write_diagnostic(log, f"killed wedged Cachix daemon pid={pid}")
    except ProcessLookupError:
        _write_diagnostic(log, f"terminated wedged Cachix daemon pid={pid}")
    except PermissionError as error:
        _write_diagnostic(log, f"cannot signal Cachix daemon pid={pid}: {error}")


def _printed_store_paths(stdout: str) -> tuple[str, ...]:
    """Return store paths printed one per line, ignoring other stdout."""
    return tuple(
        line.strip()
        for line in stdout.splitlines()
        if _STORE_PATH_RE.fullmatch(line.strip())
    )


def _realized_installable_out_paths(
    installables: tuple[str, ...],
    *,
    run: Callable[..., object],
) -> tuple[str, ...]:
    """Return the outputs the validating root ``nix build`` realizes.

    43531986200 home-george built
    ``p9qfa2l4…-home-manager-generation.drv`` and the daemon pushed
    ``z8fblck…-home-manager-generation``. Flush then required
    ``l0y1sjsx…-home-manager-generation`` from ``nix eval --option
    allow-import-from-derivation false .outPath``. That path was not in
    the store. ``nix derivation show`` on the same runner, with
    import-from-derivation disabled, instantiated a third output
    (``arck7db9…``). The receipt repeats that build. The flags are the
    root-build branch of ``_validation_args`` (``--no-update-lock-file
    --no-link --keep-going --fallback``), copied here so this module
    stays free of the derivation-validation import, plus
    ``--print-out-paths``. Import-from-derivation stays at Nix's
    default. ``nix path-info`` on the installable is not a substitute:
    38934724529 returned a stale path already on Cachix.
    """
    result = run(
        "nix",
        "build",
        "--no-update-lock-file",
        "--no-link",
        "--keep-going",
        "--fallback",
        "--print-out-paths",
        *installables,
        check=False,
        capture=True,
    )
    returncode = int(getattr(result, "returncode", 1))
    detail = str(getattr(result, "stderr", "")).strip()
    if returncode:
        msg = (
            "could not resolve realized root outputs "
            f"returncode={returncode} stderr={detail}"
        )
        raise CachixFlushError(msg)
    paths = _printed_store_paths(str(getattr(result, "stdout", "")))
    if len(paths) != len(installables):
        msg = (
            "nix build --print-out-paths returned "
            f"{len(paths)} outputs for {len(installables)} root installables"
        )
        raise CachixFlushError(msg)
    return paths


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
    """Record the validating build's outputs so flush can ``cachix push`` them.

    38821223775 argus built ``darwin-system-26.11.57a3171`` at 05:31:46
    and finished validate at 05:31:51. Daemon stop returned 0 ten seconds
    later. ``https://gkze.cachix.org`` still 404'd
    ``/nix/store/98bwk8gpzhmr12navdg9knjscv09k2nm-darwin-system-26.11.57a3171``.

    38934724529 recorded ``nix path-info`` results that were already on
    Cachix (``Nothing to push``). 43531986200 recorded an
    import-from-derivation-disabled ``.outPath`` the build never realized.
    """
    if not installables:
        return ()
    runner = _run if run is None else run
    paths = _realized_installable_out_paths(installables, run=runner)
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


def _realized_root_path_in_cachix(store_path: str, budget: _FlushBudget) -> bool:
    """Return whether *store_path* is present on gkze.cachix.org."""
    result = _run(
        "nix",
        "path-info",
        "--store",
        _BINARY_CACHE_STORE,
        store_path,
        check=False,
        capture=True,
        timeout=budget.timeout(_CACHIX_QUERY_TIMEOUT_SECONDS),
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


def _store_output_name(path: str) -> str | None:
    """Return the output name of a Nix store path, without its hash."""
    if not _STORE_PATH_RE.fullmatch(path.rstrip("/")):
        return None
    leaf = path.rstrip("/").rsplit("/", 1)[-1]
    return leaf.split("-", 1)[1]


def _darwin_pin_kind(path: str) -> str | None:
    """Return ``darwin`` or ``home`` when *path* is a pinnable root wrapper."""
    output = _store_output_name(path)
    if output is None:
        return None
    if output.startswith("darwin-system"):
        return "darwin"
    if output.startswith("home-manager-generation"):
        return "home"
    return None


def _closure_root_names() -> tuple[str, ...]:
    """Return the shard's root names from the flush step environment."""
    return tuple(os.environ.get("NIXCFG_CLOSURE_ROOTS", "").split())


def _roots_by_pin_kind(roots: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    """Group shard root names into darwin-system and home-manager pins."""
    grouped: dict[str, list[str]] = {"darwin": [], "home": []}
    for root in roots:
        if root.startswith("darwin-"):
            grouped["darwin"].append(root)
        elif root.startswith("home-"):
            grouped["home"].append(root)
        else:
            msg = f"closure root {root!r} is not a darwin-* or home-* pin name"
            raise CachixFlushError(msg)
    return {kind: tuple(names) for kind, names in grouped.items()}


def _stable_darwin_pin_names(paths: tuple[str, ...]) -> dict[str, str]:
    """Map each realized Darwin root to its shard's stable pin name.

    Hash-prefixed names (``darwin-system-qaj6kyaf``) are a new pin on every
    closure. 43613142360 already held two of those plus a home-generation
    pin, and the next one returned 402. The shard root (``darwin-argus``,
    ``home-george``) stays one pin; ``--keep-revisions 1`` replaces it.
    """
    by_kind: dict[str, list[str]] = {"darwin": [], "home": []}
    for path in paths:
        kind = _darwin_pin_kind(path)
        if kind is not None:
            by_kind[kind].append(path)
    if not any(by_kind.values()):
        return {}
    grouped = _roots_by_pin_kind(_closure_root_names())
    assigned: dict[str, str] = {}
    for kind, kind_paths in by_kind.items():
        if not kind_paths:
            continue
        if len(kind_paths) != 1:
            msg = f"expected one {kind} root to pin, got {len(kind_paths)}"
            raise CachixFlushError(msg)
        choices = grouped[kind]
        if len(choices) != 1:
            rendered = ", ".join(choices) if choices else "<unset>"
            msg = (
                f"cannot name a Cachix pin for {kind_paths[0]}: {kind} roots={rendered}"
            )
            raise CachixFlushError(msg)
        name = choices[0]
        if _PIN_NAME_RE.fullmatch(name) is None:
            msg = f"refusing Cachix pin name {name!r}"
            raise CachixFlushError(msg)
        assigned[kind_paths[0]] = name
    return assigned


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
    return _darwin_pin_kind(path) is not None


def _redact_cachix_log(text: str) -> str:
    """Drop Authorization material before a Cachix error reaches the job log."""
    return _AUTH_LOG_RE.sub("Authorization <redacted>", text)


def _cachix_pin_api(
    method: str,
    name: str | None,
    timeout: float,
    *,
    token: str | None = None,
) -> tuple[int, bytes]:
    """Call the gkze pin collection. The token stays in the request header."""
    if name is None:
        url = _CACHIX_PIN_API
    elif _DELETABLE_PIN_RE.fullmatch(name) is None:
        msg = f"refusing Cachix pin API name {name!r}"
        raise CachixFlushError(msg)
    else:
        url = f"{_CACHIX_PIN_API}/{quote(name, safe='')}"
    headers = {
        "Accept": "application/json",
        "User-Agent": _NARINFO_USER_AGENT,
    }
    if token is not None:
        # cachix 1.11.1's pin POST puts the token in Authorization with no
        # scheme. ``Bearer <token>`` is a different credential and 403s.
        headers["Authorization"] = token
    request = urllib.request.Request(  # noqa: S310 -- fixed https://cachix.org pin API
        url,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(  # noqa: S310 -- fixed https://cachix.org pin API
            request, timeout=timeout
        ) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as error:
        return int(error.code), error.read()
    except urllib.error.URLError as error:
        msg = f"Cachix pin API {method} failed: {type(error).__name__}"
        raise CachixFlushError(msg) from error


def _list_cachix_pins(budget: _FlushBudget) -> tuple[dict[str, object], ...]:
    """Return the public gkze pin list. Names and retention only."""
    status, body = _cachix_pin_api(
        "GET",
        None,
        budget.timeout(_CACHIX_QUERY_TIMEOUT_SECONDS),
    )
    if status != 200:  # noqa: PLR2004 -- HTTP OK
        msg = f"could not list Cachix pins status={status}"
        raise CachixFlushError(msg)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        msg = "Cachix pin list is not JSON"
        raise CachixFlushError(msg) from error
    if not isinstance(payload, list):
        msg = "Cachix pin list is not a list"
        raise CachixFlushError(msg)
    pins: list[dict[str, object]] = []
    for item in payload:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            msg = "Cachix pin list entry has no name"
            raise CachixFlushError(msg)
        pins.append(item)
    return tuple(pins)


def _pin_keeps_one_revision(pin: dict[str, object]) -> bool:
    """Return whether *pin* already retains only the latest revision."""
    keep = pin.get("keep")
    if not isinstance(keep, dict):
        return False
    return (
        keep.get("tag") == "Revisions"
        and keep.get("contents") == _CACHIX_PIN_KEEP_REVISIONS
    )


def _pin_latest_store_path(pin: dict[str, object] | None) -> str | None:
    """Return the latest revision's store path, when the pin records one."""
    if pin is None:
        return None
    revision = pin.get("lastRevision")
    if not isinstance(revision, dict):
        return None
    path = revision.get("storePath")
    if not isinstance(path, str) or not path.startswith("/nix/store/"):
        return None
    return path.rstrip("/")


def _pin_retains_path(pin: dict[str, object] | None, path: str) -> bool:
    """Return whether *pin*'s latest revision is already *path*."""
    latest = _pin_latest_store_path(pin)
    return latest is not None and latest == path.rstrip("/")


def _cachix_auth_token() -> str:
    """Return the flush step's Cachix token without putting it in an error."""
    token = os.environ.get("CACHIX_AUTH_TOKEN", "").strip()
    if not token:
        msg = "cannot delete a Cachix pin: CACHIX_AUTH_TOKEN is unset"
        raise CachixFlushError(msg)
    return token


def _delete_cachix_pin(name: str, budget: _FlushBudget) -> int:
    """Delete one known pin.

    404 means a concurrent shard already removed it. 403 means Cachix
    refused authorization. An absent token gets that same status, so
    callers log the refusal and do not treat it as proof of an Admin
    role. A missing token and every other status still fail closed, as
    do push and a pin of a path the latest revision does not already
    retain.
    """
    if _DELETABLE_PIN_RE.fullmatch(name) is None:
        msg = f"refusing to delete Cachix pin {name!r}"
        raise CachixFlushError(msg)
    status, _body = _cachix_pin_api(
        "DELETE",
        name,
        budget.timeout(_CACHIX_QUERY_TIMEOUT_SECONDS),
        token=_cachix_auth_token(),
    )
    if status in {200, 202, 204, 404, _CACHIX_PIN_DELETE_FORBIDDEN}:
        return status
    msg = f"could not delete Cachix pin {name} status={status}"
    raise CachixFlushError(msg)


def _record_pin_delete(log: TextIO, kind: str, name: str, status: int) -> None:
    """Log a pin delete. A 403 is pruning and does not fail the flush."""
    if status == _CACHIX_PIN_DELETE_FORBIDDEN:
        # Length only. The response body stays out of the log, and the
        # token value is never written. GET /pin is public, so a 200
        # there does not identify this credential.
        token_len = len(os.environ.get("CACHIX_AUTH_TOKEN", "").strip())
        _write_diagnostic(
            log,
            f"warning: could not delete Cachix pin {name} status={status}; "
            f"authorization refused token_len={token_len}",
        )
        return
    _write_diagnostic(log, f"deleted {kind} Cachix pin {name} status={status}")


def _delete_legacy_darwin_pins(log: TextIO, budget: _FlushBudget) -> None:
    """Remove hash-named Darwin pins that ``--keep-days`` would hold."""
    legacy = [
        str(pin["name"])
        for pin in _list_cachix_pins(budget)
        if _LEGACY_DARWIN_PIN_RE.fullmatch(str(pin["name"]))
    ]
    for name in legacy:
        _record_pin_delete(log, "legacy", name, _delete_cachix_pin(name, budget))


def _collapse_linux_builder_history(
    name: str, log: TextIO, budget: _FlushBudget
) -> dict[str, object] | None:
    """Drop a day-based linux-builder pin. Return the pin that remains.

    ``cachix pin --keep-revisions 1`` trims history only after Cachix accepts
    the new revision. 43613142360 was already over the pin quota, so a pin
    that adds a revision is rejected before that trim. Delete the day-based
    pin first when this token is allowed to. None means the pin is gone and
    the caller must pin the image it just pushed. The returned pin is still
    on the cache: it already keeps one revision, or deletion was refused.
    """
    if _LINUX_BUILDER_PIN_RE.fullmatch(name) is None:
        msg = f"refusing to collapse Cachix pin {name!r}"
        raise CachixFlushError(msg)
    current = next(
        (pin for pin in _list_cachix_pins(budget) if pin.get("name") == name),
        None,
    )
    if current is None or _pin_keeps_one_revision(current):
        return current
    status = _delete_cachix_pin(name, budget)
    _record_pin_delete(log, "historical", name, status)
    if status == _CACHIX_PIN_DELETE_FORBIDDEN:
        return current
    return None


def _run_cachix_pin(
    name: str, path: str, log: TextIO, budget: _FlushBudget
) -> subprocess.CompletedProcess[str]:
    """Pin *path* under *name* and keep only that revision."""
    pin = _run(
        "cachix",
        "pin",
        _BINARY_CACHE,
        name,
        path,
        "--keep-revisions",
        str(_CACHIX_PIN_KEEP_REVISIONS),
        check=False,
        capture=True,
        timeout=budget.timeout(_CACHIX_QUERY_TIMEOUT_SECONDS),
    )
    _write_diagnostic(
        log,
        f"cachix pin {name} returncode={pin.returncode} path={path} "
        f"stderr={_redact_cachix_log(pin.stderr.strip())}",
    )
    return pin


def _pin_failure(name: str, pin: subprocess.CompletedProcess[str]) -> CachixFlushError:
    msg = (
        f"cachix pin of {name} failed "
        f"returncode={pin.returncode} stderr={_redact_cachix_log(pin.stderr.strip())}"
    )
    return CachixFlushError(msg)


def _pin_storage_limited(pin: subprocess.CompletedProcess[str]) -> bool:
    return "Pin storage limit exceeded" in pin.stderr


def _quota_release_store_path(log: TextIO, budget: _FlushBudget) -> str:
    """Push one empty file whose pin can replace a multi-gigabyte revision.

    ``nix-store --add`` of fixed bytes is content-addressed, so every shard
    retargets the legacy name at the same path. The file has no references,
    so the pin is charged bytes, not a Darwin closure.
    """
    marker = _temp() / "update-artifacts" / "pin-quota-release"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("nixcfg-pin-quota-release\n")
    added = _run(
        "nix-store",
        "--add",
        str(marker),
        check=False,
        capture=True,
        timeout=budget.timeout(_CACHIX_QUERY_TIMEOUT_SECONDS),
    )
    path = added.stdout.strip().splitlines()[-1] if added.stdout.strip() else ""
    if added.returncode or not path.startswith("/nix/store/"):
        msg = f"could not add the pin-quota release file returncode={added.returncode}"
        raise CachixFlushError(msg)
    push = _cachix_push((path,), "quota-release", log, budget)
    if push.returncode:
        msg = f"could not push the pin-quota release file returncode={push.returncode}"
        raise CachixFlushError(msg)
    _write_diagnostic(log, f"pin-quota release path={path}")
    return path


def _shrink_legacy_pin_closures(log: TextIO, budget: _FlushBudget) -> bool:
    """Retarget hash-named pins at one empty file so their closures can drop.

    Return whether a legacy pin accepted that revision. DELETE of those
    names is 403 for this token (43770072139). ``cachix pin`` is authorized
    and ``--keep-revisions 1`` trims after Cachix accepts the new revision.
    A storage-limit response on the empty file means even that revision does
    not fit; the caller then keeps a closure that is already on gkze.
    """
    legacy = [
        pin
        for pin in _list_cachix_pins(budget)
        if _LEGACY_DARWIN_PIN_RE.fullmatch(str(pin["name"]))
    ]
    if not legacy:
        return False
    path = _quota_release_store_path(log, budget)
    released = False
    for pin in legacy:
        name = str(pin["name"])
        if _pin_retains_path(pin, path):
            _skip_current_pin(name, path, log)
            continue
        result = _run_cachix_pin(name, path, log, budget)
        if result.returncode == 0:
            released = True
            continue
        if _pin_storage_limited(result):
            _write_diagnostic(
                log,
                f"Cachix pin {name} could not be retargeted at {path}; "
                "pin storage is still over the plan",
            )
            continue
        raise _pin_failure(name, result)
    return released


def _cachix_pin(name: str, path: str, log: TextIO, budget: _FlushBudget) -> None:
    """Pin *path* for one revision.

    A storage-limit response deletes hash-named Darwin pins and retries
    once. When that delete is refused, the same response retargets those
    pins at one empty file so ``--keep-revisions 1`` can drop the old
    closure, then retries again. A further 402 does not fail the flush
    when *path* is already on gkze: the 50 GiB plan cannot hold another
    full Darwin closure, and failing the shard stops the other roots.
    Any other pin error still fails the flush, as does a 402 for a path
    that is not on gkze.
    """
    pin = _run_cachix_pin(name, path, log, budget)
    if pin.returncode == 0:
        return
    if not _pin_storage_limited(pin):
        raise _pin_failure(name, pin)
    _write_diagnostic(
        log,
        f"Cachix pin storage limit for {name}; deleting legacy pins and retrying once",
    )
    _delete_legacy_darwin_pins(log, budget)
    pin = _run_cachix_pin(name, path, log, budget)
    if pin.returncode == 0:
        return
    if not _pin_storage_limited(pin):
        raise _pin_failure(name, pin)
    if _shrink_legacy_pin_closures(log, budget):
        pin = _run_cachix_pin(name, path, log, budget)
        if pin.returncode == 0:
            return
        if not _pin_storage_limited(pin):
            raise _pin_failure(name, pin)
    if _realized_root_path_in_cachix(path, budget):
        _write_diagnostic(
            log,
            f"Cachix pin {name} still exceeds pin storage; {path} is already on gkze",
        )
        return
    raise _pin_failure(name, pin)


def _skip_current_pin(name: str, path: str, log: TextIO) -> None:
    """Record that *path* is already the latest revision of *name*."""
    _write_diagnostic(
        log,
        f"Cachix pin {name} already retains {path}; not adding a revision",
    )


def _pin_linux_builder_paths(
    paths: tuple[str, ...], log: TextIO, budget: _FlushBudget
) -> None:
    """Pin the Rosetta VM so gkze LRU cannot drop a substituted recache.

    39006154683: cache-root-deps copied the image from gkze at 08:22 and
    did not re-upload. Concurrent Darwin packages then LRU-evicted it
    before closures ``--max-jobs 0`` at 08:57. One revision is enough;
    day-based history of the image is what filled the pin quota.
    43738140207 pushed ``h1af85b11...-nixos-disk-image``, which was already
    revision 25. Another ``cachix pin`` adds a revision before
    ``--keep-revisions 1`` can trim, and that POST returned 402. Keep the
    existing revision instead of failing the push.
    """
    images = [path for path in paths if _LINUX_BUILDER_OUTPUT in path]
    if not images:
        return
    if len(images) != 1:
        msg = f"expected one linux-builder image to pin, got {len(images)}: {images}"
        _write_diagnostic(log, msg)
        raise CachixFlushError(msg)
    name = _linux_builder_pin_name()
    remaining = _collapse_linux_builder_history(name, log, budget)
    if _pin_retains_path(remaining, images[0]):
        _skip_current_pin(name, images[0], log)
        return
    _cachix_pin(name, images[0], log, budget)


def _pin_realized_darwin_roots(
    paths: tuple[str, ...], log: TextIO, budget: _FlushBudget
) -> None:
    """Pin one Darwin-system or home-manager wrapper per shard root.

    39166099302 closures spent 40m after four green roots. If that was
    compile because LRU dropped the wrappers, the latest pin keeps the
    next run on substitutes. Do not pin every rust_*. Hash-named pins
    from ``--keep-days 14`` are deleted first so this pin can fit.
    """
    names = _stable_darwin_pin_names(paths)
    if not names:
        return
    _delete_legacy_darwin_pins(log, budget)
    catalog = {str(pin.get("name")): pin for pin in _list_cachix_pins(budget)}
    for path, name in names.items():
        if _pin_retains_path(catalog.get(name), path):
            _skip_current_pin(name, path, log)
            continue
        _cachix_pin(name, path, log, budget)


def _push_warmup_leftovers(
    paths: tuple[str, ...], log: TextIO, budget: _FlushBudget
) -> None:
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
    push = _cachix_push(present, "warmup-leftovers", log, budget)
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


def _push_realized_root_paths(log: TextIO, budget: _FlushBudget) -> None:
    """Publish locally realized Darwin roots that the daemon hook missed."""
    recorded = load_realized_root_paths()
    if not recorded:
        return
    leftovers = tuple(
        path for path in recorded if not _coverage_critical_store_path(path)
    )
    critical = tuple(path for path in recorded if _coverage_critical_store_path(path))
    if leftovers:
        _push_warmup_leftovers(leftovers, log, budget)
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
    push = _cachix_push(critical, "realized-roots", log, budget)
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
    _pin_linux_builder_paths(critical, log, budget)
    _pin_realized_darwin_roots(critical, log, budget)
    # 38934724529: cachix exit 0 + "Nothing to push" meant the *wrong*
    # stale path was already on gkze. Coverage still 404'd the eval path.
    missing_remote = [
        path for path in critical if not _realized_root_path_in_cachix(path, budget)
    ]
    if missing_remote:
        msg = (
            "realized root paths missing from gkze.cachix.org after push: "
            f"{', '.join(missing_remote)}"
        )
        _write_diagnostic(log, msg)
        raise CachixFlushError(msg)


def _recover_unconfirmed_drain(
    stop: subprocess.CompletedProcess[str],
    socket: Path,
    daemon_log: str,
    log: TextIO,
    budget: _FlushBudget,
) -> None:
    """Stop the daemon, then republish and verify what it may have dropped.

    Raise ``CachixFlushError`` unless every locally built path is proven
    substitutable; the daemon is hidden from cachix-action's post hook first.
    """
    tail = "\n".join(daemon_log.splitlines()[-_DAEMON_LOG_TAIL_LINES:])
    _write_diagnostic(
        log,
        "Cachix daemon did not confirm a clean drain "
        f"returncode={stop.returncode}; daemon.log tail:\n{tail}",
    )
    _terminate_cachix_daemon(log)
    try:
        _recover_undrained_daemon_paths(log, budget)
    except CachixFlushError as error:
        msg = (
            "Cachix daemon stop failed; cannot confirm a clean drain "
            f"socket={socket} returncode={stop.returncode} "
            f"stderr={stop.stderr.strip()} recovery={error}"
        )
        _write_diagnostic(log, msg)
        try:
            _publish_unpushed_warm_paths(daemon_log, log, budget)
        except CachixFlushError as cleanup:
            _write_diagnostic(
                log, f"receipt publication after failed recovery: {cleanup}"
            )
        # Hide the stopped daemon from cachix-action's post hook so a closed
        # socket does not fail the job again (38577749898).
        _clear_cachix_daemon_env()
        raise CachixFlushError(msg) from error


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
      stuck jobs. Queued paths no worker started never appear in daemon.log,
      so flush republishes every locally built (``ultimate``) path and fails
      closed unless each one is then on gkze or cache.nixos.org.
    - Wedged daemon (40048292663 warm-rust 4): a Cachix HTTP call with no
      response timeout froze the queue at 06:05:45 and ``daemon stop`` never
      returned, burning the job's last 3 hours. Every Cachix call here now
      has a timeout inside ``_CACHIX_FLUSH_BUDGET_SECONDS``; a stop timeout
      kills the daemon and takes the drain-timeout recovery above.
    - Silent hook miss (38821223775): ``daemon stop`` returned 0 after
      each Darwin root shard built ``darwin-system-26.11.57a3171``, but
      gkze.cachix.org 404'd those paths. Flush then ``cachix push``es
      ``realized-root-paths.json``.
    - Stale path-info (38934724529): flush pushed a substituted
      ``darwin-system`` / ``home-manager-generation`` already on Cachix
      (``Nothing to push``). Receipts are the validating build's outputs.
    - Eval outPath (43531986200): an import-from-derivation-disabled
      ``nix eval .outPath`` recorded ``l0y1sjsx-home-manager-generation``,
      which was not the output of the ``p9qfa2l4`` derivation the shard
      built and the daemon pushed (``z8fblck``). Flush still fail-closes
      unless ``nix path-info --store`` sees the recorded path.
    - Substituted linux-builder (39006154683): cache-root-deps copied
      ``nixos-disk-image`` from gkze, so the hook uploaded nothing.
      Flush ``cachix push``es those outputs and ``cachix pin``s them
      so rust_* LRU cannot drop the VM before Darwin ``--max-jobs 0``.
    - Darwin-root LRU (39166099302): four green shards still left
      closures compiling for 40m if Starter evicted the wrappers.
      Flush pins each shard root (``darwin-argus``, ``home-george``)
      with ``--keep-revisions 1``. Hash-named pins and day-based
      linux-builder history are deleted first: 43613142360 pinned
      ``darwin-system-qaj6kyaf`` into a 402 "Pin storage limit exceeded"
      while ``linux-builder-aarch64-linux`` was revision 25.
      43667571490 and 43738140207 then 403'd that delete. The pin POST
      in that job authorized with the raw token and returned 402. A path
      the latest revision already retains is not pinned again.
      43770072139 then 402'd ``home-george`` after the closure was already
      on gkze; ``darwin-system-748wmbw5`` and ``home-generation-vijljmrs``
      still held the quota and DELETE returned 403. Flush retargets those
      hash-named pins at one empty file so ``--keep-revisions 1`` can drop
      the old closure, then retries the stable pin. A further 402 does not
      fail the flush when the path is on gkze. A path that is not on gkze
      still fails the flush, and so does the push.
    - Failed warmup (39301503361 cascade slot 3): the action post hook
      then logs ``Cachix Daemon not started. Skipping push``. Flush still
      ``cachix push``es leftovers and ``realized-root-paths.json`` so the
      successful stripe members reach gkze.
    - Block-device fault (39883371442): the ``device-fault.log`` marker
      makes this flush publish nothing from the untrusted store.
    - Runner daemon or store fault: ``runner-fault.log`` does the same for
      the discarded prepare attempt. The fresh VM publishes its own paths.
    - Unverified block device (42975052923): ``device-unverified.log``
      does the same when the kernel log query timed out.
    """
    os.environ["NIXCFG_CI_STAGE"] = "flush-cachix"
    artifacts = _temp() / "update-artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    receipts = artifacts / "prefetch-receipts.jsonl"
    budget = _FlushBudget(_CACHIX_FLUSH_BUDGET_SECONDS)
    with (artifacts / "cachix-flush.log").open("a") as log:
        _write_diagnostic(log, "Flushing Cachix daemon and prefetch receipts")
        record_runner_storage("flush-start", log)
        _retain_cachix_daemon_evidence(artifacts, log)
        if (artifacts / _DEVICE_FAULT_NAME).is_file():
            untrusted = (
                "this runner's block device faulted, so its store paths are untrusted"
            )
        elif (artifacts / _RUNNER_FAULT_NAME).is_file():
            untrusted = (
                "this runner's Nix daemon or store faulted, so this attempt "
                "is discarded"
            )
        elif (artifacts / _DEVICE_UNVERIFIED_NAME).is_file():
            untrusted = (
                "this runner's block device could not be verified, so its "
                "store paths are untrusted"
            )
        else:
            untrusted = ""
        if untrusted:
            _release_cachix_daemon_dir()
            _write_diagnostic(
                log,
                "Skipping Cachix flush: "
                f"{untrusted}; cleared CACHIX_DAEMON_DIR "
                "so cachix-action post skips its drain",
            )
            return
        if receipts.exists():
            _publish_prefetched_receipts(
                receipts,
                log,
                artifacts,
                0,
                timeout=budget.timeout(_CACHIX_PUSH_TIMEOUT_SECONDS),
            )
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
            _publish_unpushed_warm_paths(daemon_log_text, log, budget)
            raise CachixFlushError(msg)
        if not socket.exists():
            msg = f"Cachix daemon socket missing: {socket}"
            _write_diagnostic(log, msg)
            _publish_unpushed_warm_paths(daemon_log_text, log, budget)
            raise CachixFlushError(msg)
        stop = _run(
            "cachix",
            "daemon",
            "stop",
            "--socket",
            str(socket),
            check=False,
            capture=True,
            timeout=budget.timeout(_cachix_stop_timeout_seconds()),
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
            _recover_unconfirmed_drain(stop, socket, daemon_log_text, log, budget)
        try:
            _publish_unpushed_warm_paths(daemon_log_text, log, budget)
        finally:
            # 43613142360: pin raised after daemon stop, so CACHIX_DAEMON_DIR
            # stayed set and the action post hook failed on the closed socket.
            # The flush exception still fails the job; this only skips that
            # second stop.
            _release_cachix_daemon_dir()
            _write_diagnostic(
                log,
                "cleared CACHIX_DAEMON_DIR so cachix-action post skips a second stop",
            )
        record_runner_storage("flush-end", log)


_UPDATE_PLAN_FALLBACK_BRANCH = "main"


def restore_canary_plan(
    *,
    dest: Path | None = None,
    run: Callable[..., object] | None = None,
    expected_tree: str | None = None,
) -> Path:
    """Restore only a warmup plan bound to the checked-out Git tree.

    Feature-branch Zed kicks may search the default branch, but a fallback
    artifact is usable only when its source tree is byte-for-byte identical.
    Unbound legacy artifacts are skipped rather than realized.
    """
    target = dest if dest is not None else Path(os.environ["NIXCFG_CANARY_PLAN_DIR"])
    target.mkdir(parents=True, exist_ok=True)
    runner: Callable[..., object] = _run if run is None else run
    if expected_tree is None:
        identity = runner("git", "rev-parse", "HEAD^{tree}", capture=True, check=True)
        raw_tree = getattr(identity, "stdout", identity)
        expected_tree = str(raw_tree).strip()
    if not expected_tree:
        msg = "Canary plan restore could not determine the checked-out Git tree"
        raise ValueError(msg)
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
            plan_path = target / "warmup-plan.json"
            if code != 0 or not plan_path.is_file():
                continue
            try:
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                plan = None
            if isinstance(plan, dict) and plan.get("tree") == expected_tree:
                return target
            # Do not leave a stale plan available to a later consumer.
            plan_path.unlink(missing_ok=True)
    loc = ", ".join(f"{repo}@{name}" for name in searched)
    msg = f"No warmup plan artifact matches checked-out tree {expected_tree} on {loc}"
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
        "merge-prepare-shards",
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
        "device-fault-slots": device_fault_slots,
        "device-fault-shards": device_fault_shards,
        "require-cachix-daemon": require_cachix_daemon,
        "bootstrap": bootstrap,
        "quality": quality,
        "certify": certify,
        "publish": publish,
        "sync-quarantines": sync_quarantines,
        "collect-evidence": collect_evidence,
        "install-agent": install_agent,
        "repair": repair,
        "start-repair": start_repair,
        "commit-heal": commit_heal,
        "flush-cachix": flush_cachix,
        "restore-canary-plan": restore_canary_plan,
    }
    operations[stage]()
    return 0


def _report_process_failure(error: subprocess.CalledProcessError, log: TextIO) -> None:
    """Name the failed command and the tail of any stderr it captured."""
    command = (
        error.cmd if isinstance(error.cmd, str) else shlex.join(map(str, error.cmd))
    )
    print(
        f"::error::Command failed with exit status {error.returncode}: {command}",
        file=log,
    )
    stderr = _timeout_text(error.stderr)
    if stderr.strip():
        lines = stderr.rstrip("\n").splitlines()
        omitted = len(lines) - _FAILURE_STDERR_TAIL_LINES
        if omitted > 0:
            print(f"[{omitted} earlier stderr lines omitted]", file=log)
        print("\n".join(lines[-_FAILURE_STDERR_TAIL_LINES:]), file=log)
    log.flush()


def entrypoint(stage: str, log: TextIO | None = None) -> int:
    """Run *stage* and keep a failed child's exit status and diagnostics."""
    try:
        return main(stage)
    except subprocess.CalledProcessError as error:
        _report_process_failure(error, sys.stderr if log is None else log)
        return error.returncode


if (
    __name__ == "__main__"
):  # pragma: no cover -- entrypoint delegates to tested operations
    sys.exit(
        entrypoint(sys.argv[1] if len(sys.argv) > 1 else os.environ["NIXCFG_CI_STAGE"])
    )
