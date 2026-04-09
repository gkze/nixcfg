"""Rewrite packageManager fields to match the Bun used in the build."""

from __future__ import annotations

import json
import sys
from pathlib import Path

EXPECTED_ARGC = 3


class SyncPackageManagerError(RuntimeError):
    """User-facing sync helper error."""


def iter_package_json_files(root: Path) -> list[Path]:
    """Return package.json files below the given root in stable order."""
    return sorted(path for path in root.rglob("package.json") if path.is_file())


def update_package_manager(path: Path, bun_version: str) -> bool:
    """Update packageManager when present and return whether the file changed."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "packageManager" not in data:
        return False

    desired = f"bun@{bun_version}"
    if data.get("packageManager") == desired:
        return False

    data["packageManager"] = desired
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True


def require_bun_version(bun_version: str) -> str:
    """Return a validated Bun version string."""
    if bun_version:
        return bun_version
    msg = "bun version must be non-empty"
    raise SyncPackageManagerError(msg)


def main(argv: list[str] | None = None) -> int:
    """Rewrite packageManager fields to match the active Bun version."""
    active_argv = sys.argv if argv is None else argv
    if len(active_argv) != EXPECTED_ARGC:
        sys.stderr.write(
            "usage: sync_package_manager_bun_version.py ROOT BUN_VERSION\n"
        )
        return 2

    root = Path(active_argv[1])
    bun_version = active_argv[2]

    try:
        validated_bun_version = require_bun_version(bun_version)
        for package_json in iter_package_json_files(root):
            update_package_manager(package_json, validated_bun_version)
    except (OSError, json.JSONDecodeError, SyncPackageManagerError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
