"""Patch Codex's vendored Rust allocator for Darwin static linking."""

from __future__ import annotations

from pathlib import Path

from lib.codemods.text import replace_file_exactly

_EXPECTED_ARGC = 1
_WEAK_LINKAGE_ATTR = '#[linkage = "weak"]\n'
_EXPECTED_WEAK_LINKAGE_ATTRS = 5


def patch_allocator(allocator_lib: Path) -> None:
    """Remove weak linkage attributes from the allocator source in place."""
    replace_file_exactly(
        allocator_lib,
        _WEAK_LINKAGE_ATTR,
        "",
        expected_count=_EXPECTED_WEAK_LINKAGE_ATTRS,
        context="Codex allocator weak linkage attributes",
    )


def main(argv: list[str] | None = None) -> int:
    """Patch the requested allocator source file."""
    args = list(argv or [])
    if len(args) != _EXPECTED_ARGC:
        msg = "usage: patch_allocator_weak_linkage.py <allocator lib.rs>"
        raise SystemExit(msg)
    patch_allocator(Path(args[0]))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    import sys

    raise SystemExit(main(sys.argv[1:]))
