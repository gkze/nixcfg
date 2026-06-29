"""Exact-count text rewrite helpers for last-resort source codemods."""

from __future__ import annotations

import re
from collections.abc import Callable
from re import Pattern
from typing import TYPE_CHECKING, Final

from lib.codemods.errors import CodemodError

if TYPE_CHECKING:
    from pathlib import Path

_DEFAULT_CONTEXT: Final = "source text"
type RegexReplacement = str | Callable[[re.Match[str]], str]


def _plural_suffix(count: int) -> str:
    return "" if count == 1 else "s"


def _require_non_empty_needle(needle: str, *, context: str) -> None:
    if needle:
        return
    msg = f"empty search text for {context}"
    raise CodemodError(msg)


def _count_error(*, context: str, expected: int, actual: int) -> CodemodError:
    return CodemodError(
        f"expected {expected} match{_plural_suffix(expected)} for {context}, "
        f"found {actual}",
    )


def replace_exactly(
    text: str,
    old: str,
    new: str,
    *,
    expected_count: int = 1,
    context: str = _DEFAULT_CONTEXT,
) -> str:
    """Replace old with new only when it appears exactly as expected."""
    _require_non_empty_needle(old, context=context)
    actual_count = text.count(old)
    if actual_count != expected_count:
        raise _count_error(
            context=context,
            expected=expected_count,
            actual=actual_count,
        )
    return text.replace(old, new, expected_count)


def replace_once(
    text: str,
    old: str,
    new: str,
    *,
    context: str = _DEFAULT_CONTEXT,
) -> str:
    """Replace one required occurrence of old with new."""
    return replace_exactly(text, old, new, expected_count=1, context=context)


def replace_file_exactly(
    path: Path,
    old: str,
    new: str,
    *,
    expected_count: int = 1,
    context: str | None = None,
) -> bool:
    """Apply an exact-count text replacement to path and report changes."""
    label = str(path) if context is None else context
    original = path.read_text(encoding="utf-8")
    updated = replace_exactly(
        original,
        old,
        new,
        expected_count=expected_count,
        context=label,
    )
    if updated == original:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def replace_file_once(
    path: Path,
    old: str,
    new: str,
    *,
    context: str | None = None,
) -> bool:
    """Apply a required single text replacement to path and report changes."""
    return replace_file_exactly(
        path,
        old,
        new,
        expected_count=1,
        context=context,
    )


def regex_replace_exactly(
    text: str,
    pattern: str | Pattern[str],
    replacement: RegexReplacement,
    *,
    expected_count: int = 1,
    flags: int = 0,
    context: str = _DEFAULT_CONTEXT,
) -> str:
    """Replace regex matches only when the match count is exactly as expected."""
    compiled = re.compile(pattern, flags=flags) if isinstance(pattern, str) else pattern
    matches = list(compiled.finditer(text))
    actual_count = len(matches)
    if actual_count != expected_count:
        raise _count_error(
            context=context,
            expected=expected_count,
            actual=actual_count,
        )
    return compiled.sub(replacement, text, count=expected_count)


def regex_replace_file_exactly(
    path: Path,
    pattern: str | Pattern[str],
    replacement: RegexReplacement,
    *,
    expected_count: int = 1,
    flags: int = 0,
    context: str | None = None,
) -> bool:
    """Apply an exact-count regex replacement to path and report changes."""
    label = str(path) if context is None else context
    original = path.read_text(encoding="utf-8")
    updated = regex_replace_exactly(
        original,
        pattern,
        replacement,
        expected_count=expected_count,
        flags=flags,
        context=label,
    )
    if updated == original:
        return False
    path.write_text(updated, encoding="utf-8")
    return True
