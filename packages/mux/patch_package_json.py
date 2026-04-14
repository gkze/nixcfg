"""Patch mux package.json for hermetic Darwin packaging."""

from __future__ import annotations

import json
from pathlib import Path

_EXPECTED_ARGC = 2


def patch_package_json(path: Path, electron_dist: str) -> None:
    """Apply the Darwin Electron packaging overrides to one package.json file."""
    data = json.loads(path.read_text())
    build = data.setdefault("build", {})
    build["electronDist"] = electron_dist

    mac = build.setdefault("mac", {})
    mac["target"] = "dir"
    mac["hardenedRuntime"] = False
    mac["notarize"] = False

    path.write_text(json.dumps(data, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    """Patch the requested mux package.json file in place."""
    args = list(argv or [])
    if len(args) != _EXPECTED_ARGC:
        msg = "usage: patch_package_json.py <package.json> <electron-dist>"
        raise SystemExit(msg)
    package_json, electron_dist = args
    patch_package_json(Path(package_json), electron_dist)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    import sys

    raise SystemExit(main(sys.argv[1:]))
