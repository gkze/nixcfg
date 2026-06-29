"""Patch node-addon-api headers for the Emdash Electron build."""

from __future__ import annotations

import sys
from pathlib import Path

from lib.codemods.text import replace_file_once

OLD = (
    "static const napi_typedarray_type unknown_array_type = "
    "static_cast<napi_typedarray_type>(-1);"
)
NEW = (
    "static const napi_typedarray_type unknown_array_type = "
    "static_cast<napi_typedarray_type>(0);"
)
TARGETS = [
    Path("node_modules/node-addon-api/napi.h"),
    Path("node_modules/keytar/node_modules/node-addon-api/napi.h"),
]


def _stdout(message: str = "") -> None:
    sys.stdout.write(f"{message}\n")


def main() -> int:
    """Rewrite bundled node-addon-api headers to the compatible constant form."""
    patched: list[Path] = []

    for path in TARGETS:
        if not path.exists():
            continue

        if OLD not in path.read_text(encoding="utf-8"):
            continue

        replace_file_once(path, OLD, NEW, context=str(path))
        patched.append(path)

    if patched:
        _stdout("patched node-addon-api headers in:")
        for path in patched:
            _stdout(f"  {path}")
    else:
        _stdout("no node-addon-api headers needed patching")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
