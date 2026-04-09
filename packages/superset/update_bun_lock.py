"""Update the checked-in Superset Bun lock and bun.nix artifacts."""

from __future__ import annotations

import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

UPSTREAM_SRC = Path("@UPSTREAM_SRC@")
BUN = "@BUN@"
BUN2NIX_FLAKE = "@BUN2NIX_FLAKE@"


class UpdateBunLockError(RuntimeError):
    """User-facing update helper error."""


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


def _run(command: list[str], *, cwd: Path | None = None) -> None:
    """Run one subprocess command and translate failures into user-facing errors."""
    try:
        subprocess.run(command, check=True, cwd=cwd)  # noqa: S603
    except FileNotFoundError as exc:
        msg = f"missing required executable: {command[0]}"
        raise UpdateBunLockError(msg) from exc
    except subprocess.CalledProcessError as exc:
        msg = f"command failed with exit code {exc.returncode}: {' '.join(command)}"
        raise UpdateBunLockError(msg) from exc


def main() -> int:
    """Refresh ``bun.lock`` and ``bun.nix`` for the pinned Superset source."""
    repo_root = Path.cwd()
    try:
        _ensure_repo_root(repo_root)

        with tempfile.TemporaryDirectory(prefix="superset-bun-lock.") as tmpdir_str:
            tmpdir = Path(tmpdir_str)
            shutil.copytree(UPSTREAM_SRC, tmpdir, dirs_exist_ok=True, symlinks=True)
            _make_user_writable(tmpdir)

            bun_lock = tmpdir / "bun.lock"
            bun_nix = tmpdir / "bun.nix"

            _run([
                "nix",
                "run",
                f"path:{repo_root}#nixcfg",
                "--",
                "ci",
                "workflow",
                "prepare-bun-lock",
                "--workspace-root",
                str(tmpdir),
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
                cwd=tmpdir,
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
