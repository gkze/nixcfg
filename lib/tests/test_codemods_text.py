"""Tests for exact-count text codemod helpers."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lib.codemods.errors import CodemodError
from lib.codemods.text import (
    regex_replace_exactly,
    regex_replace_file_exactly,
    replace_exactly,
    replace_file_exactly,
    replace_file_once,
    replace_once,
)


def test_replace_once_requires_one_occurrence() -> None:
    """Single replacement should succeed only for one target."""
    assert replace_once("alpha beta", "beta", "gamma") == "alpha gamma"

    with pytest.raises(CodemodError, match="expected 1 match"):
        replace_once("alpha beta beta", "beta", "gamma", context="demo")


def test_replace_exactly_supports_multiple_required_occurrences() -> None:
    """Callers can require a deliberate non-one replacement count."""
    assert (
        replace_exactly("one two one", "one", "three", expected_count=2)
        == "three two three"
    )


def test_replace_exactly_rejects_missing_and_empty_targets() -> None:
    """Missing or empty needles should fail loudly."""
    with pytest.raises(CodemodError, match="found 0"):
        replace_exactly("alpha", "beta", "gamma", context="demo")

    with pytest.raises(CodemodError, match="empty search text"):
        replace_exactly("alpha", "", "gamma", context="demo")


def test_replace_file_helpers_report_whether_the_file_changed(tmp_path: Path) -> None:
    """File helpers should rewrite only after exact-count validation."""
    path = tmp_path / "source.txt"
    path.write_text("alpha beta\n", encoding="utf-8")

    assert replace_file_once(path, "beta", "gamma") is True
    assert path.read_text(encoding="utf-8") == "alpha gamma\n"

    assert replace_file_exactly(path, "gamma", "gamma") is False


def test_regex_replace_exactly_counts_before_replacing() -> None:
    """Regex replacement should reject too many matches instead of truncating."""
    assert (
        regex_replace_exactly("v1 v2", re.compile(r"v\d"), "version", expected_count=2)
        == "version version"
    )

    with pytest.raises(CodemodError, match="found 2"):
        regex_replace_exactly("v1 v2", r"v\d", "version", expected_count=1)


def test_regex_replace_file_exactly_rewrites_file(tmp_path: Path) -> None:
    """Regex file helper should apply exact-count replacements in place."""
    path = tmp_path / "source.txt"
    path.write_text("item = old\n", encoding="utf-8")

    assert (
        regex_replace_file_exactly(
            path,
            r"old$",
            "new",
            flags=re.MULTILINE,
            context="demo",
        )
        is True
    )
    assert path.read_text(encoding="utf-8") == "item = new\n"
