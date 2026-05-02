"""Shared implementations for non-PR-body workflow helper commands."""

from __future__ import annotations

import asyncio
import importlib
import json
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

from lib import json_utils
from lib.update.refs import PINNED_REF_INPUTS

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from typing import Protocol, TextIO

    class _UpdateCLI(Protocol):
        class UpdateOptions:
            def __init__(self, *, list_targets: bool) -> None: ...

        async def run_updates(self, options: object) -> int: ...


LINUX_CLEANUP_PATHS = (
    "/usr/local/lib/android",
    "/usr/share/swift",
    "/usr/share/dotnet",
    "/usr/share/miniconda",
    "/usr/local/.ghcup",
    "/usr/lib/jvm",
    "/opt/hostedtoolcache/CodeQL",
    "/opt/az",
    "/usr/lib/google-cloud-sdk",
    "/usr/local/aws-cli",
    "/usr/local/aws-sam-cli",
    "/opt/google",
    "/opt/microsoft",
    "/usr/lib/firefox",
    "/home/linuxbrew",
    "/usr/local/share/chromium",
    "/usr/local/share/chromedriver-linux64",
    "/usr/local/share/edge_driver",
    "/usr/local/share/gecko_driver",
)

PREFETCH_FLAKE_INPUTS_ARGS = ["nix", "flake", "archive", "--json"]
PREFETCH_FLAKE_INPUTS_ATTEMPTS = 3
PREFETCH_FLAKE_INPUTS_RETRY_DELAYS = (1.0, 2.0)

DARWIN_LOCK_SMOKE_EXPRS = (
    ".#darwinConfigurations.argus.config.home-manager.users.george.programs.nixvim.content",
    ".#darwinConfigurations.rocinante.config.home-manager.users.george.programs.nixvim.content",
)

DARWIN_FULL_SMOKE_REFS = (
    ".#darwinConfigurations.argus.system",
    ".#darwinConfigurations.rocinante.system",
    ".#homeConfigurations.george.activationPackage",
)


def xcode_version_key(app_path: Path) -> tuple[int, ...]:
    """Return sortable numeric components from an Xcode app path."""
    stem = app_path.stem.removeprefix("Xcode")
    parts = [
        int(token) for token in stem.replace("-", ".").split(".") if token.isdigit()
    ]
    return tuple(parts)


def report_disk_usage(*, run: Callable[..., object], paths: tuple[str, ...]) -> None:
    """Run `df -h` for the provided paths."""
    args = ["df", "-h", *paths] if paths else ["df", "-h", "/"]
    run(args, check=False)


def _free_disk_space_macos(*, run: Callable[..., object]) -> None:
    xcodes = sorted(Path("/Applications").glob("Xcode*.app"), key=xcode_version_key)
    latest_xcode = xcodes[-1] if xcodes else None
    for xcode in xcodes:
        if xcode == latest_xcode:
            continue
        run(["sudo", "rm", "-rf", str(xcode)])

    home = Path.home()
    run(["sudo", "rm", "-rf", str(home / "Library/Developer/CoreSimulator")])
    run(["xcrun", "simctl", "delete", "all"], check=False)
    run([
        "sudo",
        "rm",
        "-rf",
        str(home / "Library/Android/sdk"),
        "/usr/local/share/dotnet",
        str(home / "hostedtoolcache"),
    ])


def _free_disk_space_linux(
    *,
    run: Callable[..., object],
    linux_cleanup_paths: tuple[str, ...],
) -> None:
    run(["sudo", "apt-get", "clean"], check=False)
    run(
        ["sudo", "docker", "system", "prune", "--all", "--force", "--volumes"],
        check=False,
    )
    run(["sudo", "swapoff", "-a"], check=False)

    julia_paths = tuple(str(path) for path in Path("/usr/local").glob("julia*"))
    run([
        "sudo",
        "rm",
        "-rf",
        "/mnt/swapfile",
        *linux_cleanup_paths,
        *julia_paths,
    ])


def cmd_free_disk_space(
    *,
    run: Callable[..., object],
    platform: str,
    env: Mapping[str, str],
    stdout: TextIO,
    stderr: TextIO,
    force_local: bool = False,
    linux_cleanup_paths: tuple[str, ...] = LINUX_CLEANUP_PATHS,
) -> int:
    """Free disk space on a CI runner."""
    running_in_ci = env.get("CI", "").lower() in {"1", "true", "yes"}
    if not running_in_ci and not force_local:
        stderr.write(
            "Refusing to run free-disk-space outside CI. Re-run with --force-local to override.\n"
        )
        return 2

    if platform == "darwin":
        disk_paths = ("/",)

        def cleanup() -> None:
            _free_disk_space_macos(run=run)

    elif platform.startswith("linux"):
        disk_paths = ("/", "/mnt")

        def cleanup() -> None:
            _free_disk_space_linux(
                run=run,
                linux_cleanup_paths=linux_cleanup_paths,
            )

    else:
        stderr.write(
            f"free-disk-space only supports Linux and macOS runners, got {platform!r}.\n"
        )
        return 2

    stdout.write("=== Before cleanup ===\n")
    report_disk_usage(run=run, paths=disk_paths)
    cleanup()
    stdout.write("=== After cleanup ===\n")
    report_disk_usage(run=run, paths=disk_paths)
    return 0


def cmd_install_darwin_tools(*, run: Callable[..., object]) -> int:
    """Install Darwin-specific CI tools."""
    run(["brew", "install", "--cask", "macfuse"])
    run(["brew", "install", "1password-cli"])
    return 0


def cmd_prefetch_flake_inputs(
    *,
    run: Callable[..., object],
    sleep: Callable[[float], None] = time.sleep,
    stderr: TextIO,
    attempts: int = PREFETCH_FLAKE_INPUTS_ATTEMPTS,
    retry_delays: tuple[float, ...] = PREFETCH_FLAKE_INPUTS_RETRY_DELAYS,
    args: list[str] | None = None,
) -> int:
    """Warm flake input caches with bounded retries."""
    command = args or PREFETCH_FLAKE_INPUTS_ARGS
    for attempt in range(1, attempts + 1):
        try:
            run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError:
            if attempt == attempts:
                stderr.write(
                    "Warning: prefetch-flake-inputs failed after "
                    f"{attempts} attempts; continuing because this step only warms caches.\n"
                )
                break
            delay = retry_delays[attempt - 1]
            stderr.write(
                "Warning: prefetch-flake-inputs failed; "
                f"retrying in {delay:.1f}s (attempt {attempt + 1}/{attempts}).\n"
            )
            sleep(delay)
        else:
            break
    return 0


def _flake_update_input_names(
    *,
    lock_file: Path = Path("flake.lock"),
    pinned_inputs: frozenset[str] = PINNED_REF_INPUTS,
) -> list[str]:
    """Return root flake input names that should be lock-refreshed."""
    lock = json_utils.as_object_dict(
        json.loads(lock_file.read_text(encoding="utf-8")),
        context=str(lock_file),
    )
    nodes = json_utils.as_object_dict(lock.get("nodes"), context="flake.lock.nodes")
    root = json_utils.as_object_dict(nodes.get("root"), context="flake.lock.nodes.root")
    root_inputs = json_utils.as_object_dict(
        root.get("inputs", {}),
        context="flake.lock.nodes.root.inputs",
    )
    return [
        name
        for name, target in sorted(root_inputs.items())
        if name not in pinned_inputs and not isinstance(target, list)
    ]


def cmd_nix_flake_update(*, run: Callable[..., object]) -> int:
    """Update root flake inputs while preserving operational pins."""
    for input_name in _flake_update_input_names():
        run(["nix", "flake", "lock", "--update-input", input_name])
    return 0


def cmd_build_darwin_config(*, run: Callable[..., object], host: str) -> int:
    """Build one Darwin configuration by host name."""
    run(["nix", "build", "--impure", f".#darwinConfigurations.{host}.system"])
    return 0


def cmd_eval_darwin_lock_smoke(
    *,
    run: Callable[..., object],
    exprs: tuple[str, ...] = DARWIN_LOCK_SMOKE_EXPRS,
) -> int:
    """Evaluate lock-only-safe Darwin expressions."""
    for expr in exprs:
        run(["nix", "eval", "--json", "--impure", expr], stdout=subprocess.DEVNULL)
    return 0


def cmd_eval_darwin_full_smoke(
    *,
    run: Callable[..., object],
    refs: tuple[str, ...] = DARWIN_FULL_SMOKE_REFS,
) -> int:
    """Dry-run full Darwin outputs once generated artifacts are available."""
    for ref in refs:
        run(["nix", "build", "--dry-run", "--impure", ref])
    return 0


def cmd_smoke_check_update_app(*, run: Callable[..., object]) -> int:
    """Smoke-check that the update app evaluates."""
    run(
        ["nix", "eval", "--raw", ".#apps.x86_64-linux.nixcfg.program"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return 0


def cmd_list_update_targets(
    *,
    import_module: Callable[[str], object] = importlib.import_module,
) -> int:
    """List update targets via the update CLI."""
    update_cli = cast("_UpdateCLI", import_module("lib.update.cli"))
    return asyncio.run(
        update_cli.run_updates(update_cli.UpdateOptions(list_targets=True))
    )


def cmd_verify_workflow_contracts(
    *,
    workflow: Path,
    validator: Callable[..., None],
    description: str,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Validate one workflow contract set and report the result."""
    try:
        validator(workflow_path=workflow)
    except (RuntimeError, TypeError) as exc:
        stderr.write(f"{exc}\n")
        return 1
    stdout.write(f"Verified {description} for {workflow}\n")
    return 0


def cmd_validate_bun_lock(
    *,
    validate: Callable[[Path], None],
    lock_file: Path,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Validate Bun source-package override consistency."""
    try:
        validate(lock_file)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        stderr.write(f"{exc}\n")
        return 1
    stdout.write(f"Validated Bun source package overrides for {lock_file}\n")
    return 0


def cmd_prepare_bun_lock(
    *,
    prepare: Callable[..., bool],
    workspace_root: Path,
    lock_file: Path,
    bun_executable: str,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Validate or relock Bun source-package overrides."""
    try:
        relocked = prepare(
            workspace_root,
            lock_file,
            bun_executable=bun_executable,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        stderr.write(f"{exc}\n")
        return 1

    if relocked:
        stdout.write(
            f"Relocked Bun source package overrides for {lock_file} via {bun_executable}\n"
        )
    else:
        stdout.write(f"Validated Bun source package overrides for {lock_file}\n")
    return 0


def json_object(value: object, *, context: str) -> json_utils.JsonObject:
    """Require a JSON object with string keys."""
    if isinstance(value, dict):
        result: json_utils.JsonObject = {}
        for key, item in value.items():
            if not isinstance(key, str):
                msg = f"Expected string keys for JSON object {context}"
                raise TypeError(msg)
            result[key] = json_utils.coerce_json_value(
                item,
                context=f"{context}.{key}",
            )
        return result
    msg = f"Expected JSON object for {context}"
    raise TypeError(msg)


def load_flake_lock_input_locked(
    *, lock_file: Path, node: str
) -> json_utils.JsonObject:
    """Load one flake.lock node's locked payload."""
    payload = json_object(
        json.loads(lock_file.read_text(encoding="utf-8")),
        context=f"{lock_file}",
    )
    nodes = json_object(payload.get("nodes", {}), context=f"{lock_file} nodes")
    if node not in nodes:
        msg = f"{lock_file} does not contain flake.lock node {node!r}"
        raise ValueError(msg)
    node_payload = json_object(nodes[node], context=f"{lock_file} node {node!r}")
    locked = node_payload.get("locked", {})
    if locked is None:
        return {}
    return json_object(locked, context=f"{lock_file} node {node!r} locked")


def load_json_snapshot(*, snapshot_path: Path) -> json_utils.JsonObject:
    """Load a previously snapshotted JSON object."""
    return json_object(
        json.loads(snapshot_path.read_text(encoding="utf-8")),
        context=f"{snapshot_path}",
    )


def cmd_snapshot_flake_input(
    *,
    node: str,
    lock_file: Path,
    output: Path,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Write one flake input locked payload to disk."""
    try:
        locked = load_flake_lock_input_locked(lock_file=lock_file, node=node)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(locked, sort_keys=True) + "\n", encoding="utf-8")
    except (OSError, TypeError, ValueError) as exc:
        stderr.write(f"{exc}\n")
        return 1

    stdout.write(f"Snapshotted flake.lock input {node!r} to {output}\n")
    return 0


def cmd_compare_flake_input(
    *,
    node: str,
    before: Path,
    lock_file: Path,
    github_output: Path | None,
    output_name: str,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Compare one flake input snapshot against the current lock data."""
    try:
        before_snapshot = load_json_snapshot(snapshot_path=before)
        after_snapshot = load_flake_lock_input_locked(lock_file=lock_file, node=node)
    except (OSError, TypeError, ValueError) as exc:
        stderr.write(f"{exc}\n")
        return 1

    changed = before_snapshot != after_snapshot
    if github_output is not None:
        try:
            with github_output.open("a", encoding="utf-8") as handle:
                handle.write(f"{output_name}={'true' if changed else 'false'}\n")
        except OSError as exc:
            stderr.write(f"{exc}\n")
            return 1

    stdout.write(
        f"flake.lock input {node!r} changed: {'true' if changed else 'false'}\n"
    )
    return 0
