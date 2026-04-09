"""Patch node-addon-api include paths in bundled binding.gyp files."""

from __future__ import annotations

import sys
from pathlib import Path

OLD = '"<!@(node -p \\"require(\'node-addon-api\').include\\")"'
NEW = '"../../node-addon-api"'


def _stdout(message: str = "") -> None:
    sys.stdout.write(f"{message}\n")


def main() -> int:
    """Rewrite node-addon-api include lookups to use the vendored path."""
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("apps/desktop/node_modules")
    patched: list[Path] = []

    for path in root.rglob("binding.gyp"):
        text = path.read_text(encoding="utf-8")
        if OLD not in text:
            continue
        path.write_text(text.replace(OLD, NEW), encoding="utf-8")
        patched.append(path)

    if patched:
        _stdout("patched node-addon-api include paths in:")
        for path in patched:
            _stdout(f"  {path}")
    else:
        _stdout("no binding.gyp files needed node-addon-api include patching")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
