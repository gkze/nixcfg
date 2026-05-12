"""Update the checked-in T3 Code runtime Bun lockfiles."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

UPSTREAM_SRC = Path("@UPSTREAM_SRC@")
BUN = "@BUN@"
ELECTRON_BUILDER_VERSION = "@ELECTRON_BUILDER_VERSION@"
T3CODE_COMMIT_HASH = "@T3CODE_COMMIT_HASH@"


class UpdateRuntimeLocksError(RuntimeError):
    """User-facing update helper error."""


def _ensure_repo_root(repo_root: Path) -> None:
    """Validate that the helper is running from the nixcfg repository root."""
    if (
        not (repo_root / "flake.nix").is_file()
        or not (repo_root / "packages" / "t3code").is_dir()
        or not (repo_root / "packages" / "t3code-desktop").is_dir()
    ):
        msg = "run this script from the nixcfg repository root"
        raise UpdateRuntimeLocksError(msg)


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> None:
    """Run one subprocess command and translate failures into user-facing errors."""
    try:
        subprocess.run(command, check=True, cwd=cwd, env=env)  # noqa: S603
    except FileNotFoundError as exc:
        msg = f"missing required executable: {command[0]}"
        raise UpdateRuntimeLocksError(msg) from exc
    except subprocess.CalledProcessError as exc:
        msg = f"command failed with exit code {exc.returncode}: {' '.join(command)}"
        raise UpdateRuntimeLocksError(msg) from exc


def _refresh_bun_lockfile(workspace: Path) -> None:
    """Run Bun in isolated XDG directories to refresh the workspace lockfile."""
    with (
        tempfile.TemporaryDirectory(prefix="t3code-bun-lock-home.") as home_dir,
        tempfile.TemporaryDirectory(prefix="t3code-bun-lock-cache.") as cache_dir,
        tempfile.TemporaryDirectory(prefix="t3code-bun-lock-config.") as config_dir,
        tempfile.TemporaryDirectory(prefix="t3code-bun-lock-data.") as data_dir,
        tempfile.TemporaryDirectory(prefix="t3code-bun-lock-state.") as state_dir,
    ):
        env = os.environ | {
            "HOME": home_dir,
            "XDG_CACHE_HOME": cache_dir,
            "XDG_CONFIG_HOME": config_dir,
            "XDG_DATA_HOME": data_dir,
            "XDG_STATE_HOME": state_dir,
        }
        _run(
            [
                BUN,
                "install",
                "--lockfile-only",
                "--ignore-scripts",
                "--no-progress",
            ],
            cwd=workspace,
            env=env,
        )


def _render_runtime_manifest(
    repo_root: Path,
    workspace: Path,
    *,
    electron_builder_version: str | None = None,
    commit_hash: str | None = None,
) -> None:
    """Render the package-specific runtime manifest into *workspace*."""
    command = [
        sys.executable,
        str(
            repo_root / "packages" / "t3code-desktop" / "render_runtime_package_json.py"
        ),
        str(UPSTREAM_SRC),
        "--output",
        str(workspace / "package.json"),
    ]
    if electron_builder_version is not None:
        command.extend(["--electron-builder-version", electron_builder_version])
    if commit_hash is not None:
        command.extend(["--commit-hash", commit_hash])
    _run(command)


def _refresh_lock(
    repo_root: Path,
    lock_file: Path,
    *,
    electron_builder_version: str | None = None,
    commit_hash: str | None = None,
) -> None:
    """Refresh one runtime lockfile from the rendered manifest."""
    with tempfile.TemporaryDirectory(prefix="t3code-runtime-lock.") as tmpdir_str:
        workspace = Path(tmpdir_str)
        _render_runtime_manifest(
            repo_root,
            workspace,
            electron_builder_version=electron_builder_version,
            commit_hash=commit_hash,
        )
        shutil.copy2(lock_file, workspace / "bun.lock")
        _refresh_bun_lockfile(workspace)
        shutil.copy2(workspace / "bun.lock", lock_file)


def main() -> int:
    """Refresh runtime lockfiles for the pinned T3 Code source."""
    repo_root = Path.cwd()
    try:
        _ensure_repo_root(repo_root)
        _refresh_lock(repo_root, repo_root / "packages" / "t3code" / "bun.lock")
        _refresh_lock(
            repo_root,
            repo_root / "packages" / "t3code-desktop" / "bun.lock",
            electron_builder_version=ELECTRON_BUILDER_VERSION,
            commit_hash=T3CODE_COMMIT_HASH,
        )
    except UpdateRuntimeLocksError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
