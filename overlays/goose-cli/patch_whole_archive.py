"""Patch Chromium's whole_archive.py to always whole-archive Rust allocator libs.

The Rust allocator crate already tries to propagate an always-link config via
GN, but on Goose's V8/mksnapshot build the allocator rlib still reaches the
final link only as a plain Rust archive argument. With newer Rust toolchains,
that leaves allocator shim symbols unresolved at link time.

This patch teaches the link-wrapper helper to additionally whole-archive any
`liballocator_<hash>.rlib` arguments it sees directly on the linker command
line.
"""

from __future__ import annotations

import sys
from pathlib import Path

_EXPECTED_ARGC = 2
_USAGE = "usage: patch_whole_archive.py <whole_archive.py path>"
_MISSING_ANCHOR = "whole_archive.py anchor not found"

_NEEDLE = """  # The set of libraries we want to apply `--whole-archive`` to.
  whole_archive_libs = [
      extract_libname(x) for x in command
      if x.startswith("-LinkWrapper,add-whole-archive=")
  ]
"""

_REPLACEMENT = """  def is_allocator_rlib(s):
    return re.search(r'(^|/)liballocator_[^/]+\\.rlib$', s) is not None

  # The set of libraries we want to apply `--whole-archive`` to.
  whole_archive_libs = [
      extract_libname(x) for x in command
      if x.startswith("-LinkWrapper,add-whole-archive=")
  ] + [x for x in command if is_allocator_rlib(x)]
"""


def main() -> int:
    """Patch the target whole_archive.py file in place."""
    if len(sys.argv) != _EXPECTED_ARGC:
        raise SystemExit(_USAGE)

    target = Path(sys.argv[1])
    original = target.read_text(encoding="utf-8")
    if _NEEDLE not in original:
        raise SystemExit(_MISSING_ANCHOR)

    target.write_text(original.replace(_NEEDLE, _REPLACEMENT, 1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
