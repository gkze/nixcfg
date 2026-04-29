"""Patch Codex's vendored Rust allocator for Darwin static linking."""

from __future__ import annotations

from pathlib import Path

_EXPECTED_ARGC = 1
_WEAK_LINKAGE_ATTR = '#[linkage = "weak"]\n'


def patch_allocator(allocator_lib: Path) -> None:
    """Remove weak linkage attributes from the allocator source in place."""
    text = allocator_lib.read_text(encoding="utf-8")
    allocator_lib.write_text(text.replace(_WEAK_LINKAGE_ATTR, ""), encoding="utf-8")


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
