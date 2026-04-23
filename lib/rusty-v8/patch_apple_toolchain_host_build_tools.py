"""Patch V8's Apple toolchain host-build-tools settings for Nix builds."""

from __future__ import annotations

import sys
from pathlib import Path

_EXPECTED_ARGC = 2
_USAGE = "usage: patch_apple_toolchain_host_build_tools.py <toolchain.gni path>"
_MISSING_ANCHOR = "apple toolchain host-build-tools anchor not found"

_NEEDLE = "      toolchain_for_rust_host_build_tools = true\n"
_REPLACEMENT = """      toolchain_for_rust_host_build_tools = true
      use_lld = false
      fatal_linker_warnings = false
"""


def main() -> int:
    """Patch the provided toolchain.gni file in place."""
    if len(sys.argv) != _EXPECTED_ARGC:
        raise SystemExit(_USAGE)

    toolchain_gni = Path(sys.argv[1])
    original = toolchain_gni.read_text(encoding="utf-8")
    if _NEEDLE not in original:
        raise SystemExit(_MISSING_ANCHOR)

    toolchain_gni.write_text(
        original.replace(_NEEDLE, _REPLACEMENT, 1),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
