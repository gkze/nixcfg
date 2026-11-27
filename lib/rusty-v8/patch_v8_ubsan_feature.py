"""Stop trap-only UBSan from selecting V8's sanitizer-runtime code.

Codex's V8 compiles ``v8/src/sandbox/testing.cc`` whenever
``V8_USE_UNDEFINED_BEHAVIOR_SANITIZER`` is set. That macro follows
``__has_feature(undefined_behavior_sanitizer)``, which becomes true when
Chromium's trap checks (``-fsanitize=array-bounds`` and ``-fsanitize=return``)
are passed without ``-fsanitize-ignore-for-ubsan-feature``. The translation
unit then includes ``sanitizer/common_interface_defs.h`` and calls
``__sanitizer_set_death_callback``.

The toolchain defines ``V8_NIX_TRAP_ONLY_UBSAN`` when it has to drop the
ignore flag. This patch makes that macro keep the feature off. A V8 that
does not consult the feature, such as Goose's current fork, is left as it
is. A V8 that moved the check fails closed instead of building the runtime
path again.
"""

from __future__ import annotations

import sys
from pathlib import Path

_EXPECTED_ARGC = 2
_USAGE = "usage: patch_v8_ubsan_feature.py <v8/src/base/macros.h>"
_FEATURE = "__has_feature(undefined_behavior_sanitizer)"
_SAFE_STACK = "V8_USE_SAFE_STACK"
_MISSING_BLOCK = "V8 UBSan feature block not found exactly once"
_BLOCK = """\
// Define V8_USE_UNDEFINED_BEHAVIOR_SANITIZER macro.
#if defined(__has_feature)
#if __has_feature(undefined_behavior_sanitizer)
#define V8_USE_UNDEFINED_BEHAVIOR_SANITIZER 1
#endif
#endif
"""
_REPLACEMENT = """\
// Define V8_USE_UNDEFINED_BEHAVIOR_SANITIZER macro.
#if defined(__has_feature)
#if __has_feature(undefined_behavior_sanitizer) && !defined(V8_NIX_TRAP_ONLY_UBSAN)
#define V8_USE_UNDEFINED_BEHAVIOR_SANITIZER 1
#endif
#endif
"""


def patch_text(text: str) -> str:
    """Return ``text`` with trap-only UBSan excluded from V8's sanitizer macro."""
    if _FEATURE not in text:
        if _SAFE_STACK not in text:
            raise SystemExit(_MISSING_BLOCK)
        return text
    if text.count(_BLOCK) != 1:
        raise SystemExit(_MISSING_BLOCK)
    return text.replace(_BLOCK, _REPLACEMENT, 1)


def main() -> int:
    """Patch one ``macros.h`` in place."""
    if len(sys.argv) != _EXPECTED_ARGC:
        raise SystemExit(_USAGE)
    target = Path(sys.argv[1])
    target.write_text(patch_text(target.read_text(encoding="utf-8")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
