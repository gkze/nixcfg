"""Patch Chromium compiler GN defaults for Nix-built rusty_v8."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

_EXPECTED_ARGC = 2
_USAGE = "usage: patch_compiler_gni.py <compiler.gni|compiler BUILD.gn|sanitizers.gni>"
_MISSING_ANCHOR = "compiler.gni use_lld anchor not found"

_USE_LLD_ASSIGNMENT = "use_lld"
_USE_LLD_REPLACEMENT_VALUE = "false"
_UNSUPPORTED_BUILD_GN_FLAGS = (
    "-fdiagnostics-show-inlining-chain",
    "/clang:-fdiagnostics-show-inlining-chain",
    "-fno-lifetime-dse",
    "-fsanitize-ignore-for-ubsan-feature=array-bounds",
)
_UNSUPPORTED_SANITIZERS_GNI_FLAGS = (
    "-fsanitize-ignore-for-ubsan-feature=${invoker.sanitizer}",
)


def _line_contains_any(line: str, needles: Iterable[str]) -> bool:
    return any(needle in line for needle in needles)


def _drop_lines_containing_flags(text: str, flags: Iterable[str]) -> str:
    return "".join(
        line
        for line in text.splitlines(keepends=True)
        if not _line_contains_any(line, flags)
    )


def _assignment_indent(line: str, name: str) -> str | None:
    stripped = line.lstrip()
    if not stripped.startswith(name):
        return None

    after_name = stripped[len(name) :]
    if not after_name or after_name[0].isalnum() or after_name[0] == "_":
        return None
    if not after_name.lstrip().startswith("="):
        return None
    return line[: len(line) - len(stripped)]


def _line_continues_gn_expression(line: str) -> bool:
    return line.rstrip().endswith(("&&", "||"))


def _patch_gn_assignment(text: str, name: str, value: str) -> str:
    lines = text.splitlines(keepends=True)
    patched: list[str] = []
    index = 0
    replaced = False

    while index < len(lines):
        line = lines[index]
        indent = _assignment_indent(line, name)
        if replaced or indent is None:
            patched.append(line)
            index += 1
            continue

        patched.append(f"{indent}{name} = {value}\n")
        replaced = True
        index += 1
        while index < len(lines) and _line_continues_gn_expression(lines[index - 1]):
            index += 1

    if not replaced:
        raise SystemExit(_MISSING_ANCHOR)
    return "".join(patched)


def _patch_compiler_build_gn(text: str) -> str:
    return _drop_lines_containing_flags(text, _UNSUPPORTED_BUILD_GN_FLAGS)


def _patch_sanitizers_gni(text: str) -> str:
    return _drop_lines_containing_flags(text, _UNSUPPORTED_SANITIZERS_GNI_FLAGS)


def main() -> int:
    """Patch a compiler.gni file in place."""
    if len(sys.argv) != _EXPECTED_ARGC:
        raise SystemExit(_USAGE)

    target = Path(sys.argv[1])
    text = target.read_text(encoding="utf-8")
    if target.name == "BUILD.gn":
        target.write_text(_patch_compiler_build_gn(text), encoding="utf-8")
        return 0
    if target.name == "sanitizers.gni":
        target.write_text(_patch_sanitizers_gni(text), encoding="utf-8")
        return 0

    target.write_text(
        _patch_gn_assignment(text, _USE_LLD_ASSIGNMENT, _USE_LLD_REPLACEMENT_VALUE),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
