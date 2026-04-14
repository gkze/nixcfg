"""Patch Codex Cargo.lock placeholder package versions."""

from __future__ import annotations

from pathlib import Path

import tomlkit

_EXPECTED_ARGC = 2


def patch_lockfile(lock_file: Path, version: str) -> None:
    """Rewrite source-less placeholder package versions in one Cargo.lock file."""
    lock_doc = tomlkit.parse(lock_file.read_text())
    for package in lock_doc.get("package", []):
        if package.get("version") == "0.0.0" and "source" not in package:
            package["version"] = version
    lock_file.write_text(tomlkit.dumps(lock_doc))


def main(argv: list[str] | None = None) -> int:
    """Patch the requested Cargo.lock file in place."""
    args = list(argv or [])
    if len(args) != _EXPECTED_ARGC:
        msg = "usage: patch_cargo_lock_version.py <Cargo.lock> <version>"
        raise SystemExit(msg)
    lock_file, version = args
    patch_lockfile(Path(lock_file), version)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    import sys

    raise SystemExit(main(sys.argv[1:]))
