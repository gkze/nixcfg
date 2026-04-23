"""Normalize invalid single-line multi-except clauses before other formatters run."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path
from typing import Final

_MULTI_EXCEPT_PATTERN: Final = re.compile(
    r"^(?P<prefix>\s*except\s+)(?!\()"
    r"(?P<exceptions>[^:#\n]+?,[^:\n]*?)"
    r"(?P<alias>\s+as\s+[A-Za-z_][A-Za-z0-9_]*)?"
    r"(?P<colon>\s*:)(?P<comment>\s*#.*)?(?P<newline>\r?\n)?$"
)


def _normalize_multi_except_line(line: str) -> str:
    """Parenthesize one invalid single-line multi-except clause."""
    match = _MULTI_EXCEPT_PATTERN.match(line)
    if match is None:
        return line

    comment = match.group("comment") or ""
    newline = match.group("newline") or ""
    return (
        f"{match.group('prefix')}({match.group('exceptions').strip()})"
        f"{match.group('alias') or ''}{match.group('colon')}{comment}{newline}"
    )


def normalize_multi_except_text(source: str) -> str:
    """Return *source* with invalid single-line multi-excepts parenthesized."""
    return "".join(
        _normalize_multi_except_line(line) for line in source.splitlines(keepends=True)
    )


def normalize_multi_except_path(path: Path) -> bool:
    """Rewrite *path* in place when it contains invalid single-line multi-excepts."""
    source = path.read_text(encoding="utf-8")
    normalized = normalize_multi_except_text(source)
    if normalized == source:
        return False

    path.write_text(normalized, encoding="utf-8")
    return True


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parenthesize invalid single-line Python multi-except clauses.",
    )
    parser.add_argument(
        "--pyupgrade-exe",
        help="Optional pyupgrade executable to run after normalizing files.",
    )
    parser.add_argument(
        "--pyupgrade-arg",
        action="append",
        default=[],
        help="Argument to forward to pyupgrade after normalization.",
    )
    parser.add_argument("paths", nargs="*", help="Python files to normalize.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Normalize requested files and optionally run pyupgrade afterward."""
    args = _build_parser().parse_args(argv)
    paths = [Path(raw_path) for raw_path in args.paths]

    for path in paths:
        normalize_multi_except_path(path)

    if args.pyupgrade_exe and paths:
        subprocess.run(  # noqa: S603
            [
                args.pyupgrade_exe,
                *args.pyupgrade_arg,
                *(str(path) for path in paths),
            ],
            check=True,
        )

    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
