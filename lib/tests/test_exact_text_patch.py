"""Behavioral tests for exact source-patch planning."""

import ast
from pathlib import Path

import pytest

from lib.exact_text_patch import ExactTextPatch, plan_exact_text_patches
from lib.update.paths import REPO_ROOT


def test_exact_text_patch_defers_annotations_for_python_312_consumers() -> None:
    """Package builds using Python 3.12 must import this shared helper safely."""
    module = ast.parse(
        (REPO_ROOT / "lib/exact_text_patch.py").read_text(encoding="utf-8")
    )
    future_imports = [
        statement
        for statement in module.body
        if isinstance(statement, ast.ImportFrom) and statement.module == "__future__"
    ]

    assert [
        alias.name for statement in future_imports for alias in statement.names
    ] == ["annotations"]


def _message(patch: ExactTextPatch, count: int) -> str:
    return f"expected {patch.expected_count} anchors in {patch.path}, found {count}"


def test_plan_exact_text_patches_validates_then_updates_each_source() -> None:
    """A plan may combine repeated edits without mutating its input mapping."""
    first = Path("first.txt")
    second = Path("second.txt")
    sources = {first: "alpha beta beta", second: "gamma"}

    planned = plan_exact_text_patches(
        sources,
        (
            ExactTextPatch(first, "alpha", "managed"),
            ExactTextPatch(first, "beta", "owned", expected_count=2),
            ExactTextPatch(second, "gamma", "fixed"),
        ),
        mismatch_message=_message,
    )

    assert planned == {first: "managed owned owned", second: "fixed"}
    assert sources == {first: "alpha beta beta", second: "gamma"}


def test_plan_exact_text_patches_validates_every_original_before_planning() -> None:
    """One replacement may not manufacture a later vendor anchor."""
    path = Path("source.txt")
    sources = {path: "first"}

    with pytest.raises(RuntimeError, match="expected 1 anchors.*found 0"):
        plan_exact_text_patches(
            sources,
            (
                ExactTextPatch(path, "first", "second"),
                ExactTextPatch(path, "second", "third"),
            ),
            mismatch_message=_message,
        )

    assert sources == {path: "first"}


@pytest.mark.parametrize(
    ("source", "patches", "found"),
    [
        (
            "abc",
            (
                ExactTextPatch(Path("source.txt"), "ab", "x"),
                ExactTextPatch(Path("source.txt"), "bc", "y"),
            ),
            0,
        ),
        (
            "alpha beta",
            (
                ExactTextPatch(Path("source.txt"), "alpha", "beta"),
                ExactTextPatch(Path("source.txt"), "beta", "owned"),
            ),
            2,
        ),
    ],
)
def test_plan_exact_text_patches_revalidates_each_planned_replacement(
    source: str,
    patches: tuple[ExactTextPatch, ...],
    found: int,
) -> None:
    """Earlier edits may neither remove nor duplicate a reviewed anchor."""
    path = Path("source.txt")
    sources = {path: source}

    with pytest.raises(
        RuntimeError,
        match=rf"expected 1 anchors.*found {found}",
    ):
        plan_exact_text_patches(
            sources,
            patches,
            mismatch_message=_message,
        )

    assert sources == {path: source}
