"""Apply bb's Electron 44 better-sqlite3 fix, or accept upstream's copy.

better-sqlite3 12.10.0 calls ``v8::External::New`` without the tagged slot
Electron 44 requires. The checked-in patch is that fix. 12.11.1 already
contains the same macros, so applying it again is a reversed patch and the
build stops. A later source that matches neither shape is still an error:
skipping the patch there would compile the pre-fix module or ignore an
unreviewed ABI.
"""

import argparse
import shutil
import subprocess
from pathlib import Path


class BetterSqliteAbiError(RuntimeError):
    """Raised when better-sqlite3 is neither the pre-fix nor the fixed source."""


def _patch_command(
    *,
    executable: str,
    module: Path,
    patch: Path,
    reverse: bool,
    dry_run: bool,
) -> list[str]:
    command = [
        executable,
        "-d",
        str(module),
        "-p1",
        "--batch",
        "-i",
        str(patch),
        "--reverse" if reverse else "--forward",
    ]
    if dry_run:
        command.append("--dry-run")
    return command


def _run_patch(
    *,
    executable: str,
    module: Path,
    patch: Path,
    reverse: bool,
    dry_run: bool,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- argv is the fixed patch(1) invocation
        _patch_command(
            executable=executable,
            module=module,
            patch=patch,
            reverse=reverse,
            dry_run=dry_run,
        ),
        check=False,
        capture_output=True,
        text=True,
    )


def apply_electron_44_abi_patch(module: Path, patch: Path) -> str:
    """Apply the ABI patch, or return ``already-present`` when upstream has it."""
    if not module.is_dir():
        msg = f"better-sqlite3 module directory is missing: {module}"
        raise BetterSqliteAbiError(msg)
    if not patch.is_file():
        msg = f"better-sqlite3 ABI patch is missing: {patch}"
        raise BetterSqliteAbiError(msg)
    # patch -d changes directory before opening a relative -i path.
    module = module.resolve()
    patch = patch.resolve()
    executable = shutil.which("patch")
    if executable is None:
        msg = "patch is not on PATH"
        raise BetterSqliteAbiError(msg)
    forward = _run_patch(
        executable=executable,
        module=module,
        patch=patch,
        reverse=False,
        dry_run=True,
    )
    if forward.returncode == 0:
        applied = _run_patch(
            executable=executable,
            module=module,
            patch=patch,
            reverse=False,
            dry_run=False,
        )
        if applied.returncode != 0:
            msg = f"better-sqlite3 ABI patch did not apply\n{applied.stderr}"
            raise BetterSqliteAbiError(msg)
        return "applied"
    reverse = _run_patch(
        executable=executable,
        module=module,
        patch=patch,
        reverse=True,
        dry_run=True,
    )
    if reverse.returncode == 0:
        return "already-present"
    msg = (
        "better-sqlite3 matches neither the Electron 44 pre-fix source "
        f"nor the upstream fix\nforward:\n{forward.stderr}\nreverse:\n{reverse.stderr}"
    )
    raise BetterSqliteAbiError(msg)


def main(argv: list[str] | None = None) -> int:
    """Apply or accept the Electron 44 better-sqlite3 fix in one module tree."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", required=True, type=Path)
    parser.add_argument("--patch", required=True, type=Path)
    args = parser.parse_args(argv)
    apply_electron_44_abi_patch(args.module, args.patch)
    return 0


if __name__ == "__main__":  # pragma: no cover -- exercised through public main
    raise SystemExit(main())
