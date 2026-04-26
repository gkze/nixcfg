"""Helpers for nixcfg macOS application activation scripts."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Never, ReadOnly, TypedDict, TypeIs

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

EXPECTED_ARGC = 3


class _ProfileBundleLeakAuditPayload(TypedDict):
    label: ReadOnly[str]
    managedBundleNames: ReadOnly[list[str]]
    packagePaths: ReadOnly[list[str]]


class _SystemApplicationEntryPayload(TypedDict):
    bundleName: ReadOnly[str]
    mode: ReadOnly[str]
    sourcePath: ReadOnly[str]


class _SystemApplicationsPayload(TypedDict):
    entries: ReadOnly[list[_SystemApplicationEntryPayload]]
    rsyncPath: ReadOnly[str]
    stateDirectory: ReadOnly[str]
    stateName: ReadOnly[str]
    targetDirectory: ReadOnly[str]
    writable: ReadOnly[bool]


def _print_stderr(message: str) -> None:
    sys.stderr.write(f"{message}\n")


def _payload_error(message: str) -> Never:
    _print_stderr(message)
    raise SystemExit(2)


def _as_object_dict(value: object, *, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        msg = f"Expected JSON object for {context}"
        raise TypeError(msg)

    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            msg = f"Expected string key in {context}, got {type(key).__name__}"
            raise TypeError(msg)
        result[key] = item
    return result


def _required(payload: Mapping[str, object], key: str) -> object:
    try:
        return payload[key]
    except KeyError:
        _payload_error(f"missing required payload field: {key}")


def _required_str(payload: Mapping[str, object], key: str) -> str:
    value = _required(payload, key)
    if isinstance(value, str):
        return value
    _payload_error(f"payload field {key!r} must be a string")


def _required_bool(payload: Mapping[str, object], key: str) -> bool:
    value = _required(payload, key)
    if isinstance(value, bool):
        return value
    _payload_error(f"payload field {key!r} must be a boolean")


def _is_str_list(value: object) -> TypeIs[list[str]]:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _required_str_list(payload: Mapping[str, object], key: str) -> list[str]:
    value = _required(payload, key)
    if _is_str_list(value):
        return value
    _payload_error(f"payload field {key!r} must be a list of strings")


def _required_entries(
    payload: Mapping[str, object],
) -> list[_SystemApplicationEntryPayload]:
    value = _required(payload, "entries")
    if not isinstance(value, list):
        _payload_error("payload field 'entries' must be a list of objects")

    entries: list[_SystemApplicationEntryPayload] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            _payload_error(f"payload field 'entries[{index}]' must be an object")
        entry = _as_object_dict(item, context=f"entries[{index}]")
        entries.append({
            "bundleName": _required_str(entry, "bundleName"),
            "mode": _required_str(entry, "mode"),
            "sourcePath": _required_str(entry, "sourcePath"),
        })
    return entries


def _profile_bundle_leak_audit_payload(
    payload: Mapping[str, object],
) -> _ProfileBundleLeakAuditPayload:
    return {
        "label": _required_str(payload, "label"),
        "managedBundleNames": _required_str_list(payload, "managedBundleNames"),
        "packagePaths": _required_str_list(payload, "packagePaths"),
    }


def _system_applications_payload(
    payload: Mapping[str, object],
) -> _SystemApplicationsPayload:
    return {
        "entries": _required_entries(payload),
        "rsyncPath": _required_str(payload, "rsyncPath"),
        "stateDirectory": _required_str(payload, "stateDirectory"),
        "stateName": _required_str(payload, "stateName"),
        "targetDirectory": _required_str(payload, "targetDirectory"),
        "writable": _required_bool(payload, "writable"),
    }


def _load_payload(argv: Sequence[str]) -> tuple[str, dict[str, object]]:
    if len(argv) != EXPECTED_ARGC:
        _print_stderr("usage: mac_apps_helper.py <command> <payload-json>")
        raise SystemExit(2)

    command = argv[1]
    payload_path = Path(argv[2])
    with payload_path.open(encoding="utf-8") as payload_file:
        loaded: object = json.load(payload_file)

    try:
        payload = _as_object_dict(loaded, context=str(payload_path))
    except TypeError:
        _print_stderr(f"expected JSON object payload in {payload_path}")
        raise SystemExit(2) from None

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


def _profile_bundle_leak_audit(payload: Mapping[str, object]) -> None:
    parsed = _profile_bundle_leak_audit_payload(payload)
    label = parsed["label"]
    managed_bundle_names = set(parsed["managedBundleNames"])
    offending_bundles: list[str] = []

    for package_path in parsed["packagePaths"]:
        applications_directory = Path(package_path) / "Applications"
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


def _system_applications(payload: Mapping[str, object]) -> None:
    parsed = _system_applications_payload(payload)
    entries = parsed["entries"]
    target_directory = Path(parsed["targetDirectory"])
    state_directory = Path(parsed["stateDirectory"])
    state_file = state_directory / f"{parsed['stateName']}.txt"
    current_apps = [entry["bundleName"] for entry in entries]
    current_app_set = set(current_apps)
    rsync_path = parsed["rsyncPath"]
    writable = parsed["writable"]

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

    for entry in entries:
        _install_managed_app(
            bundle_name=entry["bundleName"],
            mode=entry["mode"],
            source_path=entry["sourcePath"],
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
