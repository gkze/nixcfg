"""Extract one Bun package into an output directory."""

from __future__ import annotations

import argparse
import shutil
import stat
import subprocess
import sys
from pathlib import Path


class ExtractBunPackageError(RuntimeError):
    """User-facing extraction helper error."""


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="extract-bun-package",
        description="Extract one Bun package into an output directory.",
    )
    parser.add_argument("--bsdtar", required=True, help="Path to bsdtar executable.")
    parser.add_argument("--package", required=True, help="Package path to extract.")
    parser.add_argument("--out", required=True, help="Destination directory.")
    return parser.parse_args()


def make_user_writable(root: Path) -> None:
    """Match the legacy helper by recursively granting user rwx permissions."""

    def visit(path: Path) -> None:
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            return

        if stat.S_ISLNK(mode):
            return

        path.chmod(mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

        if stat.S_ISDIR(mode):
            for child in path.iterdir():
                visit(child)

    visit(root)


def extract_archive(bsdtar: str, package: Path, output: Path) -> None:
    """Extract a .tgz package with the same flags as the old shell helper."""
    try:
        subprocess.run(  # noqa: S603
            [
                bsdtar,
                "--extract",
                "--file",
                str(package),
                "--directory",
                str(output),
                "--strip-components=1",
                "--no-same-owner",
                "--no-same-permissions",
            ],
            check=True,
        )
    except FileNotFoundError as exc:
        msg = f"bsdtar executable not found: {bsdtar}"
        raise ExtractBunPackageError(msg) from exc
    except subprocess.CalledProcessError as exc:
        msg = f"bsdtar failed with exit code {exc.returncode} for {package}"
        raise ExtractBunPackageError(msg) from exc


def copy_directory(package: Path, output: Path) -> None:
    """Copy an unpacked Bun package tree into place."""
    shutil.copytree(package, output, dirs_exist_ok=True, symlinks=True)


def main() -> int:
    """Extract or copy a Bun package into the destination directory."""
    args = parse_args()
    package = Path(args.package)
    output = Path(args.out)

    try:
        output.mkdir(parents=True, exist_ok=True)
        if package.suffix == ".tgz":
            extract_archive(args.bsdtar, package, output)
        else:
            copy_directory(package, output)
        make_user_writable(output)
    except ExtractBunPackageError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
