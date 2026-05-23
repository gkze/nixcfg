"""Patch Chromium compiler GN defaults for Nix-built rusty_v8."""

from __future__ import annotations

import sys
from pathlib import Path

_EXPECTED_ARGC = 2
_USAGE = "usage: patch_compiler_gni.py <compiler.gni|compiler BUILD.gn>"
_MISSING_ANCHOR = "compiler.gni use_lld anchor not found"

_NEEDLES = (
    '  use_lld = is_clang && current_os != "zos" && experimental_linker_path == ""\n',
    '  use_lld = is_clang && current_os != "zos"\n',
)
_REPLACEMENT = "  use_lld = false\n"
_UNSUPPORTED_BUILD_GN_FLAG_LINES = (
    '      cflags += [ "-fno-lifetime-dse" ]\n',
    '      "-fsanitize-ignore-for-ubsan-feature=array-bounds",\n',
)


def _patch_compiler_build_gn(text: str) -> str:
    for flag_line in _UNSUPPORTED_BUILD_GN_FLAG_LINES:
        text = text.replace(flag_line, "")
    return text


def main() -> int:
    """Patch a compiler.gni file in place."""
    if len(sys.argv) != _EXPECTED_ARGC:
        raise SystemExit(_USAGE)

    target = Path(sys.argv[1])
    text = target.read_text(encoding="utf-8")
    if target.name == "BUILD.gn":
        target.write_text(_patch_compiler_build_gn(text), encoding="utf-8")
        return 0

    for needle in _NEEDLES:
        if needle in text:
            target.write_text(text.replace(needle, _REPLACEMENT, 1), encoding="utf-8")
            return 0

    raise SystemExit(_MISSING_ANCHOR)


if __name__ == "__main__":
    raise SystemExit(main())
