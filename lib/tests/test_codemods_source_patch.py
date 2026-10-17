"""Behavior of the shared declarative source-patch engine."""

import re
from typing import TYPE_CHECKING

import pytest

from lib.codemods.errors import CodemodError
from lib.codemods.source_patch import (
    SourceAnchor,
    SourcePatch,
    apply_patches,
    plan_sources,
    require_absent,
    run_source_tree_cli,
    select_variant,
    source_tree_parser,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write(root: Path, sources: dict[str, str]) -> None:
    for relative_path, source in sources.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


def _read(root: Path, sources: dict[str, str]) -> dict[str, str]:
    return {name: (root / name).read_text(encoding="utf-8") for name in sources}


def test_apply_patches_writes_every_file_after_anchors_and_patches_validate(
    tmp_path: Path,
) -> None:
    """A valid plan rewrites each patched file and leaves anchors unmodified."""
    sources = {
        "a.ts": "alpha();\nalpha();\nbeta();\n",
        "b.ts": "gamma();\n",
        "seam.ts": "keep();\n",
    }
    _write(tmp_path, sources)

    apply_patches(
        tmp_path,
        (
            SourcePatch("a.ts", "alpha();", "ALPHA();", expected_count=2),
            SourcePatch("a.ts", "beta();", "BETA();"),
            SourcePatch("b.ts", "gamma();", "GAMMA();"),
        ),
        anchors=(SourceAnchor("seam.ts", "keep();"),),
    )

    assert _read(tmp_path, sources) == {
        "a.ts": "ALPHA();\nALPHA();\nBETA();\n",
        "b.ts": "GAMMA();\n",
        "seam.ts": "keep();\n",
    }


def test_apply_patches_check_validates_without_writing(tmp_path: Path) -> None:
    """``check`` is a dry run: a valid plan succeeds and the tree is unchanged."""
    sources = {"a.ts": "alpha();\n"}
    _write(tmp_path, sources)

    apply_patches(tmp_path, (SourcePatch("a.ts", "alpha();", "ALPHA();"),), check=True)

    assert _read(tmp_path, sources) == sources


@pytest.mark.parametrize("copies", [0, 2])
def test_late_patch_drift_leaves_every_file_untouched(
    tmp_path: Path,
    copies: int,
) -> None:
    """A later file's drift fails closed before an earlier file is written."""
    sources = {"a.ts": "alpha();\n", "b.ts": "gamma();\n" * copies}
    _write(tmp_path, sources)

    with pytest.raises(
        CodemodError,
        match=rf"^expected 1 match for {re.escape(str(tmp_path / 'b.ts'))}, "
        rf"found {copies}$",
    ):
        apply_patches(
            tmp_path,
            (
                SourcePatch("a.ts", "alpha();", "ALPHA();"),
                SourcePatch("b.ts", "gamma();", "GAMMA();"),
            ),
        )

    assert _read(tmp_path, sources) == sources


def test_anchor_drift_fails_before_any_patch_is_written(tmp_path: Path) -> None:
    """An unpatched audited seam that moved rejects the whole plan."""
    sources = {"a.ts": "alpha();\n", "seam.ts": "moved();\n"}
    _write(tmp_path, sources)

    with pytest.raises(
        CodemodError,
        match=rf"^expected 1 match for anchor in "
        rf"{re.escape(str(tmp_path / 'seam.ts'))}, found 0$",
    ):
        apply_patches(
            tmp_path,
            (SourcePatch("a.ts", "alpha();", "ALPHA();"),),
            anchors=(SourceAnchor("seam.ts", "keep();"),),
        )

    assert _read(tmp_path, sources) == sources


def test_patches_are_counted_against_original_and_evolving_contents() -> None:
    """An earlier replacement may not create or consume a later patch's anchor."""
    consumed = (
        SourcePatch("a.ts", "one two", "three"),
        SourcePatch("a.ts", "two", "TWO"),
    )
    with pytest.raises(CodemodError, match="^expected 1 match for a.ts, found 0$"):
        plan_sources({"a.ts": "one two\n"}, consumed)

    created = (
        SourcePatch("a.ts", "one", "two"),
        SourcePatch("a.ts", "two", "TWO"),
    )
    with pytest.raises(CodemodError, match="^expected 1 match for a.ts, found 0$"):
        plan_sources({"a.ts": "one\n"}, created)


def test_plan_sources_is_pure_and_rejects_missing_sources() -> None:
    """The planner never mutates its input and names a missing file."""
    sources = {"a.ts": "alpha();\n"}

    assert plan_sources(sources, (SourcePatch("a.ts", "alpha", "ALPHA"),)) == {
        "a.ts": "ALPHA();\n"
    }
    assert sources == {"a.ts": "alpha();\n"}
    with pytest.raises(CodemodError, match="^missing patched source file: b.ts$"):
        plan_sources(sources, (SourcePatch("b.ts", "beta", "BETA"),))


def test_require_absent_refuses_an_already_patched_tree(tmp_path: Path) -> None:
    """A patch marker anywhere in the patched files means re-application."""
    _write(tmp_path, {"a.ts": "clean\n", "b.ts": "NIX_MANAGED\n"})

    require_absent(tmp_path, ("a.ts",), "NIX_MANAGED", message="already patched")
    with pytest.raises(CodemodError, match="^already patched$"):
        require_absent(
            tmp_path, ("a.ts", "b.ts", "a.ts"), "NIX_MANAGED", message="already patched"
        )


_VARIANTS = {
    "v2": (
        SourcePatch("a.ts", "v2();", "V2();"),
        SourcePatch("b.ts", "shared();", "SHARED();"),
    ),
    "v1": (SourcePatch("a.ts", "v1();", "V1();"),),
}


@pytest.mark.parametrize(
    ("sources", "selected"),
    [
        ({"a.ts": "v2();\n", "b.ts": "shared();\n"}, "v2"),
        ({"a.ts": "v1();\n"}, "v1"),
    ],
)
def test_select_variant_returns_the_one_matching_layout(
    tmp_path: Path,
    sources: dict[str, str],
    selected: str,
) -> None:
    """Exactly one complete layout selects; a missing file just disqualifies."""
    _write(tmp_path, sources)

    assert select_variant(tmp_path, _VARIANTS) == (selected, _VARIANTS[selected])


@pytest.mark.parametrize(
    ("sources", "found"),
    [
        ({"a.ts": "v2();\n"}, "none"),
        ({"a.ts": "v1();\nv1();\n"}, "none"),
        ({"a.ts": "v1();\nv2();\n", "b.ts": "shared();\n"}, "v2, v1"),
    ],
)
def test_select_variant_rejects_drifted_and_ambiguous_layouts(
    tmp_path: Path,
    sources: dict[str, str],
    found: str,
) -> None:
    """Zero matches is drift and several matches is ambiguity; both fail."""
    _write(tmp_path, sources)

    with pytest.raises(
        CodemodError,
        match=rf"^expected exactly one supported source layout in "
        rf"{re.escape(str(tmp_path))} \(v2, v1\), found {found}$",
    ):
        select_variant(tmp_path, _VARIANTS)


def test_source_tree_cli_passes_the_root_and_reports_success(tmp_path: Path) -> None:
    """The shared CLI parses one source root and delegates to the patcher."""
    seen: list[Path] = []

    assert (
        run_source_tree_cli([str(tmp_path)], description=None, patch_tree=seen.append)
        == 0
    )
    assert seen == [tmp_path]


def test_source_tree_cli_requires_a_root() -> None:
    """A missing source root is a usage error, not a silent no-op."""
    with pytest.raises(SystemExit) as raised:
        run_source_tree_cli([], description=None, patch_tree=lambda _root: None)

    assert raised.value.code == 2


def test_optional_root_parser_accepts_a_missing_root() -> None:
    """Scripts with alternate input modes may defer the root requirement."""
    assert (
        source_tree_parser(None, optional_root=True).parse_args([]).source_root is None
    )
