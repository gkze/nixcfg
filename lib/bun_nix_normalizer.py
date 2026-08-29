"""Canonicalize generated bun2nix expressions with repository formatter tools."""

import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    type CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _run_tool(args: list[str], *, runner: CommandRunner) -> None:
    result = runner(
        args,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return
    detail = result.stderr.strip() or result.stdout.strip()
    message = f"{args[0]} failed (exit {result.returncode})"
    raise RuntimeError(f"{message}: {detail}" if detail else message)


def normalize_bun_nix_path(
    path: Path,
    *,
    runner: CommandRunner | None = None,
) -> None:
    """Normalize one generated bun.nix file in place."""
    if not path.is_file():
        msg = f"Generated bun.nix is not a regular file: {path}"
        raise RuntimeError(msg)
    effective_runner = subprocess.run if runner is None else runner
    _run_tool(
        ["deadnix", "--edit", "--quiet", str(path)],
        runner=effective_runner,
    )
    _run_tool(["nixfmt", str(path)], runner=effective_runner)


def normalize_bun_nix(
    text: str,
    *,
    runner: CommandRunner | None = None,
) -> str:
    """Return canonical bun2nix output without mutating the source tree."""
    with tempfile.TemporaryDirectory(prefix="bun-nix-normalize-") as tmpdir:
        path = Path(tmpdir) / "bun.nix"
        path.write_text(text, encoding="utf-8", newline="")
        normalize_bun_nix_path(path, runner=runner)
        return path.read_text(encoding="utf-8")


__all__ = ["normalize_bun_nix", "normalize_bun_nix_path"]
