"""Declarative exact-count source patches for build-time patch scripts.

Package ``patch_*.py`` scripts declare *what* changes as :class:`SourcePatch`
and :class:`SourceAnchor` data plus genuinely package-specific policy. This
module owns *how* those declarations are validated and applied:

* every patch is counted against the original file contents before any
  replacement is planned, so a drifted upstream tree fails before the first
  write;
* every replacement is counted again against the evolving contents, so an
  earlier replacement cannot silently create or consume a later anchor;
* files are written only after the whole plan validates.

Build-time consumers receive this module through ``lib/codemods-pythonpath.nix``
and must run it with the repository's ``python3``.
"""

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from lib.codemods.errors import CodemodError
from lib.codemods.text import replace_exactly, require_count

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

type PlannedSources = dict[Path, str]


@dataclass(frozen=True, slots=True)
class SourcePatch:
    """Replace ``old`` with ``new`` exactly ``expected_count`` times in one file."""

    relative_path: str
    old: str
    new: str
    expected_count: int = 1


@dataclass(frozen=True, slots=True)
class SourceAnchor:
    """Require ``text`` to occur exactly ``expected_count`` times, unmodified."""

    relative_path: str
    text: str
    expected_count: int = 1


def validate_anchor(root: Path, anchor: SourceAnchor) -> None:
    """Fail unless one audited, unpatched upstream seam still has its shape."""
    path = root / anchor.relative_path
    require_count(
        path.read_text(encoding="utf-8"),
        anchor.text,
        expected_count=anchor.expected_count,
        context=f"anchor in {path}",
    )


def require_absent(
    root: Path,
    relative_paths: Iterable[str],
    marker: str,
    *,
    message: str,
) -> None:
    """Refuse a tree that already carries a patch's own marker text."""
    for relative_path in dict.fromkeys(relative_paths):
        if marker in (root / relative_path).read_text(encoding="utf-8"):
            raise CodemodError(message)


def plan_sources(
    sources: Mapping[str, str],
    patches: Iterable[SourcePatch],
    *,
    root: Path | None = None,
) -> dict[str, str]:
    """Return ``sources`` with every patch applied, without performing I/O.

    ``sources`` maps relative paths to original contents. ``root`` only labels
    diagnostics with the tree the sources came from.
    """
    selected = tuple(patches)

    def context(relative_path: str) -> str:
        return relative_path if root is None else str(root / relative_path)

    for patch in selected:
        if patch.relative_path not in sources:
            msg = f"missing patched source file: {context(patch.relative_path)}"
            raise CodemodError(msg)
        require_count(
            sources[patch.relative_path],
            patch.old,
            expected_count=patch.expected_count,
            context=context(patch.relative_path),
        )
    planned = dict(sources)
    for patch in selected:
        planned[patch.relative_path] = replace_exactly(
            planned[patch.relative_path],
            patch.old,
            patch.new,
            expected_count=patch.expected_count,
            context=context(patch.relative_path),
        )
    return planned


def plan_patches(
    root: Path,
    patches: Iterable[SourcePatch],
    *,
    anchors: Iterable[SourceAnchor] = (),
) -> PlannedSources:
    """Validate anchors and patches, then return every patched file's contents."""
    for anchor in anchors:
        validate_anchor(root, anchor)
    selected = tuple(patches)
    originals = {
        relative_path: (root / relative_path).read_text(encoding="utf-8")
        for relative_path in dict.fromkeys(patch.relative_path for patch in selected)
    }
    planned = plan_sources(originals, selected, root=root)
    return {root / relative_path: source for relative_path, source in planned.items()}


def write_planned(planned: Mapping[Path, str]) -> None:
    """Write a fully validated plan."""
    for path, source in planned.items():
        path.write_text(source, encoding="utf-8")


def apply_patches(
    root: Path,
    patches: Iterable[SourcePatch],
    *,
    anchors: Iterable[SourceAnchor] = (),
    check: bool = False,
) -> None:
    """Validate the whole patch set, then write it unless ``check`` is set."""
    planned = plan_patches(root, patches, anchors=anchors)
    if not check:
        write_planned(planned)


def _matches_tree(root: Path, patches: Sequence[SourcePatch]) -> bool:
    sources: dict[str, str | None] = {}
    for patch in patches:
        if patch.relative_path not in sources:
            path = root / patch.relative_path
            sources[patch.relative_path] = (
                path.read_text(encoding="utf-8") if path.is_file() else None
            )
        source = sources[patch.relative_path]
        if source is None or source.count(patch.old) != patch.expected_count:
            return False
    return True


def select_variant(
    root: Path,
    variants: Mapping[str, Sequence[SourcePatch]],
) -> tuple[str, Sequence[SourcePatch]]:
    """Return the one named patch set whose every anchor matches the tree.

    Variants model distinct supported upstream layouts. Zero matches is
    upstream drift; more than one match is an ambiguous layout. Both fail.
    """
    matching = [
        (name, patches)
        for name, patches in variants.items()
        if _matches_tree(root, patches)
    ]
    if len(matching) != 1:
        found = ", ".join(name for name, _patches in matching) or "none"
        msg = (
            f"expected exactly one supported source layout in {root} "
            f"({', '.join(variants)}), found {found}"
        )
        raise CodemodError(msg)
    return matching[0]


def source_tree_parser(
    description: str | None,
    *,
    optional_root: bool = False,
) -> argparse.ArgumentParser:
    """Return the shared patch-script parser with its ``source_root`` argument."""
    parser = argparse.ArgumentParser(description=description)
    if optional_root:
        parser.add_argument("source_root", nargs="?", type=Path)
    else:
        parser.add_argument("source_root", type=Path)
    return parser


def run_source_tree_cli(
    argv: Sequence[str] | None,
    *,
    description: str | None,
    patch_tree: Callable[[Path], object],
) -> int:
    """Run a patch script whose only input is the unpacked source tree."""
    args = source_tree_parser(description).parse_args(argv)
    patch_tree(args.source_root)
    return 0
