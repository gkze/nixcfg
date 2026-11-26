"""Keep Chromium's trap-only UBSan checks from looking like a sanitizer runtime.

``-fsanitize-ignore-for-ubsan-feature`` is what makes
``__has_feature(undefined_behavior_sanitizer)`` stay false. Nix clang 21
rejects that flag. The toolchain wrapper drops it and defines
``V8_NIX_TRAP_ONLY_UBSAN``, which ``patch_v8_ubsan_feature.py`` honors, so
``v8/src/sandbox/testing.cc`` does not include the sanitizer runtime. A clang
that accepts the flag keeps the original arguments.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

_IGNORE_OPTION = "-fsanitize-ignore-for-ubsan-feature"
_IGNORE_JOINED = f"{_IGNORE_OPTION}="
_TRAP_ONLY_DEFINE = "-DV8_NIX_TRAP_ONLY_UBSAN"


def rewrite_args(
    args: Sequence[str],
    *,
    ignore_flag_supported: bool,
) -> list[str]:
    """Return compiler arguments with trap-only UBSan expressed for this clang."""
    if ignore_flag_supported:
        return list(args)

    rewritten: list[str] = []
    suppressed = False
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == _IGNORE_OPTION:
            suppressed = True
            index += 2 if index + 1 < len(args) else 1
            continue
        if arg.startswith(_IGNORE_JOINED):
            suppressed = True
            index += 1
            continue
        rewritten.append(arg)
        index += 1
    if suppressed:
        rewritten.append(_TRAP_ONLY_DEFINE)
    return rewritten
