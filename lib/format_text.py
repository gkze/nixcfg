"""Normalize tracked plain-text formats that lack a dedicated formatter."""

from __future__ import annotations

import sys
from pathlib import Path


def normalize_text(text: str, *, trim_trailing_whitespace: bool) -> str:
    """Normalize line endings, EOF newlines, and optional trailing whitespace."""
    normalized_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if trim_trailing_whitespace:
        normalized_lines = [line.rstrip(" \t") for line in normalized_lines]

    while normalized_lines and normalized_lines[-1] == "":
        normalized_lines.pop()

    if not normalized_lines:
        return ""

    return "\n".join(normalized_lines) + "\n"


def _read_text(path: Path) -> str:
    with path.open(encoding="utf-8", newline=None) as file:
        return file.read()


def _write_text(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        file.write(text)


def _should_trim_trailing_whitespace(path: Path) -> bool:
    # Trailing spaces inside diff hunks can be semantically significant.
    return path.suffix != ".patch"


def format_path(path: Path) -> bool:
    """Normalize a tracked plain-text file in place."""
    original = _read_text(path)
    normalized = normalize_text(
        original,
        trim_trailing_whitespace=_should_trim_trailing_whitespace(path),
    )
    if normalized == original:
        return False
    _write_text(path, normalized)
    return True


def main(argv: list[str] | None = None) -> int:
    """Format each path passed by treefmt."""
    args = sys.argv if argv is None else argv
    for raw_path in args[1:]:
        format_path(Path(raw_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
