"""Update the checked-in Superset Bun lock and bun.nix artifacts."""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

UPSTREAM_SRC = Path("@UPSTREAM_SRC@")
BUN = "@BUN@"
BUN2NIX_FLAKE = "@BUN2NIX_FLAKE@"
_BUN_NIX_ENV = "NIXCFG_SUPERSET_BUN_NIX"
_WORKSPACE_PATHS_EXPR = f"""
let
  bunNix = builtins.toPath (builtins.getEnv "{_BUN_NIX_ENV}");
  packages = import bunNix {{
    copyPathToStore = path: {{ __nixcfgWorkspacePath = toString path; }};
    fetchFromGitHub = _: {{ }};
    fetchgit = _: {{ }};
    fetchurl = _: {{ }};
  }};
  workspacePackages = builtins.filter (
    value: builtins.isAttrs value && value ? __nixcfgWorkspacePath
  ) (builtins.attrValues packages);
in
builtins.map (value: value.__nixcfgWorkspacePath) workspacePackages
"""


class UpdateBunLockError(RuntimeError):
    """User-facing update helper error."""


_STDERR_TAIL = 1500


def _command_failure(exc: subprocess.CalledProcessError, command: list[str]) -> str:
    """Render a subprocess failure, keeping the nix diagnostic at the tail."""
    message = f"command failed with exit code {exc.returncode}: {' '.join(command)}"
    detail = exc.stderr
    if not isinstance(detail, str) or not detail.strip():
        return message
    tail = detail.strip()
    if len(tail) > _STDERR_TAIL:
        tail = tail[-_STDERR_TAIL:]
    return f"{message}\n{tail}"


def _ensure_repo_root(repo_root: Path) -> None:
    """Validate that the helper is running from the nixcfg repository root."""
    if (
        not (repo_root / "flake.nix").is_file()
        or not (repo_root / "packages" / "superset").is_dir()
    ):
        msg = "run this script from the nixcfg repository root"
        raise UpdateBunLockError(msg)


def _make_user_writable(root: Path) -> None:
    """Add user-write permission to copied files so later steps can mutate them."""
    for path in [root, *root.rglob("*")]:
        if path.is_symlink():
            continue
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IWUSR)


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    capture_output: bool = False,
) -> str:
    """Run one subprocess command and translate failures into user-facing errors."""
    try:
        result = subprocess.run(  # noqa: S603
            command,
            check=True,
            cwd=cwd,
            env=env,
            text=True,
            capture_output=capture_output,
        )
    except FileNotFoundError as exc:
        msg = f"missing required executable: {command[0]}"
        raise UpdateBunLockError(msg) from exc
    except subprocess.CalledProcessError as exc:
        raise UpdateBunLockError(_command_failure(exc, command)) from exc
    return (result.stdout or "") if capture_output else ""


def _workspace_paths_from_bun_nix(bun_nix: Path) -> tuple[Path, ...]:
    """Evaluate generated ``bun.nix`` with inert fetchers to find local paths."""
    output = _run(
        [
            "nix",
            "eval",
            "--option",
            "allow-import-from-derivation",
            "false",
            "--impure",
            "--json",
            "--expr",
            _WORKSPACE_PATHS_EXPR,
        ],
        env=os.environ | {_BUN_NIX_ENV: str(bun_nix)},
        capture_output=True,
    )
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        msg = "could not resolve workspace paths from generated bun.nix"
        raise UpdateBunLockError(msg) from exc
    if (
        not isinstance(payload, list)
        or not payload
        or not all(isinstance(value, str) and value for value in payload)
    ):
        msg = "could not resolve workspace paths from generated bun.nix"
        raise UpdateBunLockError(msg)
    return tuple(Path(value) for value in payload)


def _validate_workspace_parity(
    *,
    bun_nix: Path,
    prepared_root: Path,
    upstream_root: Path,
) -> None:
    """Ensure bun.nix local paths have identical raw and prepared NAR contents."""
    prepared_root = prepared_root.absolute()
    resolved_prepared_root = prepared_root.resolve()
    relative_paths: list[Path] = []
    for generated_workspace_path in _workspace_paths_from_bun_nix(bun_nix):
        workspace_path = generated_workspace_path.absolute()
        try:
            relative_path = workspace_path.relative_to(prepared_root)
            workspace_path.resolve().relative_to(resolved_prepared_root)
        except ValueError as exc:
            msg = (
                "bun.nix referenced a workspace outside the prepared source: "
                f"{workspace_path}"
            )
            raise UpdateBunLockError(msg) from exc
        relative_paths.append(relative_path)

    _require_workspace_paths(
        relative_paths,
        prepared_root=prepared_root,
        upstream_root=upstream_root,
    )
    prepared_paths = [prepared_root / path for path in relative_paths]
    upstream_paths = [upstream_root / path for path in relative_paths]
    hash_paths = [*prepared_paths, *upstream_paths]
    hashes = _run(
        ["nix", "hash", "path", "--type", "sha256", *map(str, hash_paths)],
        capture_output=True,
    ).splitlines()
    if len(hashes) != len(hash_paths):
        msg = "nix hash path returned an unexpected number of workspace hashes"
        raise UpdateBunLockError(msg)

    workspace_count = len(relative_paths)
    drifted = [
        relative_path
        for index, relative_path in enumerate(relative_paths)
        if hashes[index] != hashes[index + workspace_count]
    ]
    if drifted:
        rendered = ", ".join(path.as_posix() for path in drifted)
        msg = f"prepared workspace differs from locked upstream source: {rendered}"
        raise UpdateBunLockError(msg)


def _require_workspace_paths(
    relative_paths: list[Path],
    *,
    prepared_root: Path,
    upstream_root: Path,
) -> None:
    """Fail before hashing when bun.nix names a workspace the pin removed.

    ``nix hash path`` only reports that some argument is missing, and its
    stderr used to be discarded. Name the workspace so the updater holds the
    last pin whose paths exist instead of publishing an unevaluable tree.
    """
    missing_prepared = _missing_workspaces(prepared_root, relative_paths)
    missing_upstream = _missing_workspaces(upstream_root, relative_paths)
    if missing_prepared:
        rendered = ", ".join(missing_prepared)
        msg = f"bun.nix workspace path is not in the prepared Superset tree: {rendered}"
        raise UpdateBunLockError(msg)
    if missing_upstream:
        rendered = ", ".join(missing_upstream)
        msg = f"bun.nix workspace path is not in the pinned Superset tree: {rendered}"
        raise UpdateBunLockError(msg)


def _missing_workspaces(root: Path, relative_paths: list[Path]) -> list[str]:
    """Return workspace paths that are not present under ``root``."""
    return [
        relative.as_posix()
        for relative in relative_paths
        if not (root / relative).exists()
    ]


def _local_flake_installable(repo_root: Path, attr: str) -> str:
    """Return a local installable through Git's clean source view.

    This standalone script cannot import ``lib.update.paths.local_flake_url``; its
    test keeps the two URLs identical, including the shallow-checkout opt-in.
    """
    return f"git+file://{repo_root.resolve()}?dirty=1&shallow=1#{attr}"


def main() -> int:
    """Refresh ``bun.lock`` and ``bun.nix`` for the pinned Superset source."""
    repo_root = Path.cwd()
    try:
        _ensure_repo_root(repo_root)

        with tempfile.TemporaryDirectory(prefix="superset-bun-lock.") as tmpdir_str:
            tmpdir = Path(tmpdir_str)
            # Hash a snapshot rather than the live store path. Hosted runners
            # auto-GC during this command, and the locked source has no GC root
            # once evaluation of the update script has finished (Update
            # 42032332040). The snapshot is outside the store.
            upstream_snapshot = tmpdir / "upstream"
            prepared_root = tmpdir / "prepared"
            shutil.copytree(UPSTREAM_SRC, upstream_snapshot, symlinks=True)
            shutil.copytree(upstream_snapshot, prepared_root, symlinks=True)
            _make_user_writable(prepared_root)

            bun_lock = prepared_root / "bun.lock"
            bun_nix = prepared_root / "bun.nix"

            _run([
                "nix",
                "run",
                _local_flake_installable(repo_root, "nixcfg"),
                "--",
                "ci",
                "pipeline",
                "bun-lock",
                "prepare",
                "--workspace-root",
                str(prepared_root),
                "--lock-file",
                str(bun_lock),
                "--bun-executable",
                BUN,
            ])
            _run(
                [
                    "nix",
                    "run",
                    f"{BUN2NIX_FLAKE}#bun2nix",
                    "--",
                    "--lock-file",
                    "bun.lock",
                    "--copy-prefix",
                    "./",
                    "--output-file",
                    str(bun_nix),
                ],
                cwd=prepared_root,
            )
            _validate_workspace_parity(
                bun_nix=bun_nix,
                prepared_root=prepared_root,
                upstream_root=upstream_snapshot,
            )

            output_dir = repo_root / "packages" / "superset"
            shutil.copy2(bun_lock, output_dir / "bun.lock")
            shutil.copy2(bun_nix, output_dir / "bun.nix")
    except UpdateBunLockError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
