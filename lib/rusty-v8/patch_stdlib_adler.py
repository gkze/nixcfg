"""Patch Chromium Rust stdlib adler selection for Nix rustc."""

from __future__ import annotations

import sys
from pathlib import Path

_EXPECTED_ARGC = 2
_USAGE = "usage: patch_stdlib_adler.py <build/rust/std/BUILD.gn path>"
_MISSING_ANCHOR = "rust stdlib adler selection anchor not found"

_LEGACY_BLOCK = """    if (rustc_nightly_capability) {
      stdlib_files += [ "adler2" ]
    } else {
      stdlib_files += [ "adler" ]
    }
"""
_REPLACEMENT = """    stdlib_files += [ "adler2" ]
"""
_ADLER2_LIBRARY = '"adler2"'
_ADLER_LIBRARY = '"adler"'


def _patch_text(text: str) -> str:
    if _LEGACY_BLOCK in text:
        return text.replace(_LEGACY_BLOCK, _REPLACEMENT, 1)

    if _ADLER2_LIBRARY in text and _ADLER_LIBRARY not in text:
        return text

    raise SystemExit(_MISSING_ANCHOR)


def main() -> int:
    """Patch a Rust stdlib BUILD.gn file in place."""
    if len(sys.argv) != _EXPECTED_ARGC:
        raise SystemExit(_USAGE)

    target = Path(sys.argv[1])
    target.write_text(_patch_text(target.read_text(encoding="utf-8")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
