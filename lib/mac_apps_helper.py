"""Helpers for nixcfg macOS application activation scripts."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

EXPECTED_ARGC = 3


def _print_stderr(message: str) -> None:
    sys.stderr.write(f"{message}\n")


def _load_payload(argv: list[str]) -> tuple[str, dict[str, Any]]:
    if len(argv) != EXPECTED_ARGC:
        _print_stderr("usage: mac_apps_helper.py <command> <payload-json>")
        raise SystemExit(2)

    command = argv[1]
    payload_path = Path(argv[2])
    with payload_path.open(encoding="utf-8") as payload_file:
        payload = json.load(payload_file)

    if not isinstance(payload, dict):
        _print_stderr(f"expected JSON object payload in {payload_path}")
        raise SystemExit(2)

    return command, payload


def _path_exists(path: Path) -> bool:
    return os.path.lexists(path)


def _remove_path(path: Path) -> None:
    if not _path_exists(path):
        return

    if path.is_symlink() or path.is_file():
        path.unlink()
        return

    if path.is_dir():
        shutil.rmtree(path)
        return

    path.unlink()


def _read_manifest(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def _app_in_other_manifests(
    needle: str, state_directory: Path, state_file: Path
) -> bool:
    for manifest in sorted(state_directory.glob("*.txt")):
        if manifest == state_file or not manifest.is_file():
            continue
        if needle in _read_manifest(manifest):
            return True
    return False


def _rsync_copy(src: Path, dst: Path, *, rsync_path: str, writable: bool) -> None:
    rsync_flags = [
        "--checksum",
        "--copy-unsafe-links",
        "--archive",
        "--delete",
        "--chmod=+w" if writable else "--chmod=-w",
        "--no-group",
        "--no-owner",
    ]
    result = subprocess.run(  # noqa: S603
        [rsync_path, *rsync_flags, f"{src}/", str(dst)],
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def _install_managed_app(
    *,
    bundle_name: str,
    mode: str,
    source_path: str,
    target_directory: Path,
    rsync_path: str,
    writable: bool,
) -> None:
    src = Path(source_path)
    dst = target_directory / bundle_name

    if not src.is_dir():
        _print_stderr(f"Expected macOS app bundle at {src}")
        raise SystemExit(1)

    _print_stderr(f"setting up {dst}...")

    if mode == "symlink":
        if _path_exists(dst):
            _remove_path(dst)
        dst.symlink_to(src)
        return

    if dst.is_symlink() or (_path_exists(dst) and not dst.is_dir()):
        _remove_path(dst)

    dst.mkdir(parents=True, exist_ok=True)
    _rsync_copy(src, dst, rsync_path=rsync_path, writable=writable)


def _profile_bundle_leak_audit(payload: dict[str, Any]) -> None:
    label = str(payload["label"])
    managed_bundle_names = {str(name) for name in payload["managedBundleNames"]}
    offending_bundles: list[str] = []

    for package_path in payload["packagePaths"]:
        applications_directory = Path(str(package_path)) / "Applications"
        if not applications_directory.is_dir():
            continue

        for app_bundle in sorted(applications_directory.glob("*.app")):
            if not app_bundle.is_dir():
                continue

            bundle_name = app_bundle.name
            if bundle_name in managed_bundle_names:
                offending_bundles.append(f"{bundle_name} <= {package_path}")

    if not offending_bundles:
        return

    _print_stderr(f"Managed macOS app bundles must not be exposed through {label}.")
    _print_stderr(
        f"Move those packages out of {label} so /Applications stays the only "
        "mutable app-bundle surface."
    )
    for offending_bundle in offending_bundles:
        _print_stderr(f" - {offending_bundle}")
    raise SystemExit(1)


def _system_applications(payload: dict[str, Any]) -> None:
    target_directory = Path(str(payload["targetDirectory"]))
    state_directory = Path(str(payload["stateDirectory"]))
    state_file = state_directory / f"{payload['stateName']}.txt"
    current_apps = [str(entry["bundleName"]) for entry in payload["entries"]]
    current_app_set = set(current_apps)
    rsync_path = str(payload["rsyncPath"])
    writable = bool(payload["writable"])

    target_directory.mkdir(parents=True, exist_ok=True)
    state_directory.mkdir(parents=True, exist_ok=True)

    for managed_app in _read_manifest(state_file):
        if not managed_app or managed_app in current_app_set:
            continue

        target_path = target_directory / managed_app
        if _app_in_other_manifests(managed_app, state_directory, state_file):
            _print_stderr(
                f"keeping {target_path} because another manifest still manages it..."
            )
            continue

        _print_stderr(f"removing stale managed app {target_path}...")
        _remove_path(target_path)

    for entry in payload["entries"]:
        _install_managed_app(
            bundle_name=str(entry["bundleName"]),
            mode=str(entry["mode"]),
            source_path=str(entry["sourcePath"]),
            target_directory=target_directory,
            rsync_path=rsync_path,
            writable=writable,
        )

    state_file.write_text(
        "".join(f"{bundle_name}\n" for bundle_name in current_apps),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    """Run one helper subcommand from a JSON payload file."""
    active_argv = sys.argv if argv is None else argv
    command, payload = _load_payload(active_argv)

    if command == "profile-bundle-leak-audit":
        _profile_bundle_leak_audit(payload)
        return 0

    if command == "system-applications":
        _system_applications(payload)
        return 0

    _print_stderr(f"unknown command: {command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
