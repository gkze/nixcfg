"""Helpers for nixcfg macOS application activation scripts."""

import json
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING, Never, ReadOnly, TypedDict, TypeIs

from packaging.version import InvalidVersion, Version

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

EXPECTED_ARGC = 3
MAX_INSTALL_WORKERS = 8
LSREGISTER_PATH = Path(
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
    "LaunchServices.framework/Support/lsregister",
)
MDIMPORT_PATH = Path("/usr/bin/mdimport")
MDLS_PATH = Path("/usr/bin/mdls")
SPOTLIGHT_REFRESH_TIMEOUT_SECONDS = 12.0
SPOTLIGHT_REFRESH_SECONDS_PER_APP = 0.4
SPOTLIGHT_REFRESH_MAX_TIMEOUT_SECONDS = 60.0
SPOTLIGHT_COMMAND_TIMEOUT_SECONDS = 2.0
SPOTLIGHT_VERIFY_INTERVAL_SECONDS = 0.5
SPOTLIGHT_PLIST_ERRORS = (OSError, plistlib.InvalidFileException)


class _ProfileBundleLeakAuditPayload(TypedDict):
    label: ReadOnly[str]
    managedBundleNames: ReadOnly[list[str]]
    packagePaths: ReadOnly[list[str]]


class _RemoveProfileCopiesPayload(TypedDict):
    bundleNames: ReadOnly[list[str]]
    targetDirectory: ReadOnly[str]


class _SystemApplicationEntryPayload(TypedDict):
    bundleName: ReadOnly[str]
    mode: ReadOnly[str]
    preventDowngrade: ReadOnly[bool]
    sourcePath: ReadOnly[str]


class _SystemApplicationsPayload(TypedDict):
    entries: ReadOnly[list[_SystemApplicationEntryPayload]]
    rsyncPath: ReadOnly[str]
    stateDirectory: ReadOnly[str]
    stateName: ReadOnly[str]
    targetDirectory: ReadOnly[str]
    writable: ReadOnly[bool]


type _BundleVersionMetadata = tuple[str | None, str | None, str | None]


def _print_stderr(message: str) -> None:
    sys.stderr.write(f"{message}\n")


def _write_managed_app_manifest(
    state_file: Path,
    bundle_names: Sequence[str],
) -> None:
    """Atomically replace one manager's complete app-ownership manifest."""
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=state_file.parent,
            prefix=f".{state_file.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            os.fchmod(temporary.fileno(), 0o644)
            temporary.write("".join(f"{bundle_name}\n" for bundle_name in bundle_names))
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path.replace(state_file)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


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
            "preventDowngrade": _required_bool(entry, "preventDowngrade"),
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


def _remove_profile_copies_payload(
    payload: Mapping[str, object],
) -> _RemoveProfileCopiesPayload:
    return {
        "bundleNames": _required_str_list(payload, "bundleNames"),
        "targetDirectory": _required_str(payload, "targetDirectory"),
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


def _ensure_single_path_component(value: str, *, field: str) -> None:
    path = Path(value)
    if path.is_absolute() or len(path.parts) != 1 or path.parts[0] in {".", ".."}:
        _payload_error(f"payload field {field!r} must contain only path components")


def _chmod_user_writable(path: Path) -> None:
    if path.is_symlink():
        return

    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return

    if mode & stat.S_IWUSR:
        return

    try:
        path.chmod(mode | stat.S_IWUSR)
    except PermissionError as exc:
        _print_stderr(f"warning: could not make {path} user-writable: {exc}")


def _make_tree_user_writable(path: Path) -> None:
    if not _path_exists(path) or path.is_symlink():
        return

    _chmod_user_writable(path)
    if not path.is_dir():
        return

    for root, directories, _files in os.walk(path, followlinks=False):
        root_path = Path(root)
        _chmod_user_writable(root_path)
        for entry_name in directories:
            _chmod_user_writable(root_path / entry_name)


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


def _refresh_launch_services_registration(
    app_path: Path,
    *,
    lsregister_path: Path = LSREGISTER_PATH,
) -> None:
    if not (app_path / "Contents" / "Info.plist").is_file():
        return
    if not lsregister_path.is_file():
        return

    result = subprocess.run(  # noqa: S603
        [str(lsregister_path), "-f", str(app_path)],
        check=False,
    )
    if result.returncode != 0:
        _print_stderr(
            "warning: could not refresh LaunchServices registration for "
            f"{app_path}: exit {result.returncode}",
        )


def _spotlight_command_timeout(deadline: float) -> float | None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    return min(SPOTLIGHT_COMMAND_TIMEOUT_SECONDS, remaining)


def _bundle_version_metadata(
    app_path: Path,
) -> _BundleVersionMetadata | None:
    try:
        with (app_path / "Contents" / "Info.plist").open("rb") as plist_file:
            payload = plistlib.load(plist_file)
    except SPOTLIGHT_PLIST_ERRORS:
        return None

    if not isinstance(payload, dict):
        return None

    bundle_identifier = payload.get("CFBundleIdentifier")
    short_version = payload.get("CFBundleShortVersionString")
    build_version = payload.get("CFBundleVersion")
    return (
        bundle_identifier if isinstance(bundle_identifier, str) else None,
        short_version if isinstance(short_version, str) else None,
        build_version if isinstance(build_version, str) else None,
    )


def _bundle_spotlight_identity(app_path: Path) -> tuple[str | None, str | None]:
    metadata = _bundle_version_metadata(app_path)
    if metadata is None:
        return None, None

    bundle_identifier, short_version, build_version = metadata
    return bundle_identifier, short_version or build_version


def _protected_app_build_version_problem(
    source_build_version: str | None,
    target_build_version: str | None,
    *,
    source_path: Path,
    target_path: Path,
    source_version: str,
    target_version: str,
) -> str | None:
    if source_build_version is None and target_build_version is None:
        return None
    if source_build_version is None or target_build_version is None:
        return (
            "Refusing protected app update because comparable bundle build "
            f"versions are missing for {target_path} and {source_path}"
        )

    try:
        source_parsed_build = Version(source_build_version)
        target_parsed_build = Version(target_build_version)
    except InvalidVersion:
        return (
            "Refusing protected app update because a bundle build version is "
            f"invalid for {target_path}: installed {target_build_version!r}, "
            f"source {source_build_version!r}"
        )

    if target_parsed_build > source_parsed_build:
        return (
            f"Refusing to downgrade {target_path} from installed version "
            f"{target_version} (build {target_build_version}) to source version "
            f"{source_version} (build {source_build_version})"
        )
    return None


def _protected_app_version_problem(
    source_metadata: _BundleVersionMetadata,
    target_metadata: _BundleVersionMetadata,
    *,
    source_path: Path,
    target_path: Path,
) -> str | None:
    _, source_short_version, source_build_version = source_metadata
    _, target_short_version, target_build_version = target_metadata
    if source_short_version is not None and target_short_version is not None:
        source_version = source_short_version
        target_version = target_short_version
    elif source_short_version is None and target_short_version is None:
        source_version = source_build_version
        target_version = target_build_version
    else:
        source_version = None
        target_version = None

    if source_version is None or target_version is None:
        return (
            "Refusing protected app update because comparable bundle versions are "
            f"missing for {target_path} and {source_path}"
        )

    try:
        source_parsed_version = Version(source_version)
        target_parsed_version = Version(target_version)
    except InvalidVersion:
        return (
            "Refusing protected app update because a bundle version is invalid for "
            f"{target_path}: installed {target_version!r}, source {source_version!r}"
        )

    if target_parsed_version > source_parsed_version:
        return (
            f"Refusing to downgrade {target_path} from installed version "
            f"{target_version} to source version {source_version}"
        )
    if (
        target_parsed_version != source_parsed_version
        or source_short_version is None
        or target_short_version is None
    ):
        return None

    return _protected_app_build_version_problem(
        source_build_version,
        target_build_version,
        source_path=source_path,
        target_path=target_path,
        source_version=source_version,
        target_version=target_version,
    )


def _protected_app_problem(source_path: Path, target_path: Path) -> str | None:
    source_metadata = _bundle_version_metadata(source_path)
    target_metadata = _bundle_version_metadata(target_path)
    if source_metadata is None or target_metadata is None:
        return (
            "Refusing protected app update because bundle metadata could not be "
            f"read for {target_path} and {source_path}"
        )

    source_identifier = source_metadata[0]
    target_identifier = target_metadata[0]
    if source_identifier is None or target_identifier is None:
        return (
            "Refusing protected app update because CFBundleIdentifier is missing "
            f"for {target_path} or {source_path}"
        )
    if source_identifier != target_identifier:
        return (
            "Refusing protected app update because bundle identifiers differ for "
            f"{target_path}: installed {target_identifier!r}, source "
            f"{source_identifier!r}"
        )

    return _protected_app_version_problem(
        source_metadata,
        target_metadata,
        source_path=source_path,
        target_path=target_path,
    )


def _preflight_managed_apps(
    entries: Sequence[_SystemApplicationEntryPayload],
    target_directory: Path,
) -> None:
    problems: list[str] = []
    for entry in entries:
        source_path = Path(entry["sourcePath"])
        target_path = target_directory / entry["bundleName"]
        if not source_path.is_dir():
            problems.append(f"Expected macOS app bundle at {source_path}")
            continue
        if not entry["preventDowngrade"] or not _path_exists(target_path):
            continue

        problem = _protected_app_problem(source_path, target_path)
        if problem is not None:
            problems.append(problem)

    if not problems:
        return

    for problem in problems:
        _print_stderr(problem)
    raise SystemExit(1)


def _import_spotlight_metadata(
    app_paths: Sequence[Path],
    *,
    mdimport_path: Path,
    deadline: float,
) -> str | None:
    problems: list[str] = []
    for app_path in app_paths:
        timeout = _spotlight_command_timeout(deadline)
        if timeout is None:
            problems.append("Spotlight refresh deadline expired before mdimport")
            break

        command = [str(mdimport_path), "-i", str(app_path)]
        try:
            result = subprocess.run(  # noqa: S603
                command,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            problems.append(f"mdimport timed out after {timeout:g} seconds")
            continue
        except OSError as exc:
            problems.append(f"could not launch mdimport: {exc}")
            break

        if result.returncode != 0:
            problems.append(f"mdimport exited {result.returncode}")

    return "; ".join(dict.fromkeys(problems)) or None


def _spotlight_record_matches_bundle(
    app_path: Path,
    *,
    expected_identity: tuple[str | None, str | None],
    mdls_path: Path,
    deadline: float,
) -> tuple[bool, str | None]:
    timeout = _spotlight_command_timeout(deadline)
    if timeout is None:
        return False, None

    command = [str(mdls_path), "-plist", "-", str(app_path)]
    try:
        result = subprocess.run(  # noqa: S603
            command,
            check=False,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"mdls timed out after {timeout:g} seconds"
    except OSError as exc:
        return False, f"could not launch mdls: {exc}"

    if result.returncode != 0:
        return False, None

    try:
        metadata = plistlib.loads(result.stdout)
    except plistlib.InvalidFileException:
        return False, None
    if not isinstance(metadata, dict):
        return False, None

    expected_identifier, expected_version = expected_identity
    indexed_version = metadata.get("kMDItemVersion")
    matches = (
        metadata.get("kMDItemContentType") == "com.apple.application-bundle"
        and (
            expected_identifier is None
            or metadata.get("kMDItemCFBundleIdentifier") == expected_identifier
        )
        and (expected_version is None or indexed_version == expected_version)
    )
    return matches, None


def _wait_for_spotlight_application_records(
    expected_metadata: Mapping[Path, tuple[str | None, str | None]],
    *,
    mdls_path: Path,
    deadline: float,
) -> tuple[dict[Path, tuple[str | None, str | None]], str | None]:
    pending = dict(expected_metadata)
    first_error: str | None = None

    while pending and time.monotonic() < deadline:
        for app_path, expected_identity in tuple(pending.items()):
            if time.monotonic() >= deadline:
                break
            matches, error = _spotlight_record_matches_bundle(
                app_path,
                expected_identity=expected_identity,
                mdls_path=mdls_path,
                deadline=deadline,
            )
            if error is not None and first_error is None:
                first_error = error
            if matches:
                del pending[app_path]

        remaining = deadline - time.monotonic()
        if not pending or remaining <= 0:
            break
        time.sleep(min(SPOTLIGHT_VERIFY_INTERVAL_SECONDS, remaining))

    return pending, first_error


def _refresh_spotlight_metadata(
    app_paths: Sequence[Path],
    *,
    mdimport_path: Path = MDIMPORT_PATH,
    mdls_path: Path = MDLS_PATH,
) -> None:
    eligible_paths = [
        path for path in app_paths if (path / "Contents" / "Info.plist").is_file()
    ]
    if not eligible_paths:
        return
    if not mdimport_path.is_file():
        _print_stderr(
            f"warning: Spotlight refresh skipped for {len(eligible_paths)} app(s): "
            f"mdimport is unavailable at {mdimport_path}",
        )
        return

    started_at = time.monotonic()
    refresh_timeout = min(
        SPOTLIGHT_REFRESH_MAX_TIMEOUT_SECONDS,
        SPOTLIGHT_REFRESH_TIMEOUT_SECONDS
        + (SPOTLIGHT_REFRESH_SECONDS_PER_APP * max(0, len(eligible_paths) - 1)),
    )
    deadline = started_at + refresh_timeout
    problems: list[str] = []
    expected_metadata = {
        path: _bundle_spotlight_identity(path) for path in eligible_paths
    }

    error = _import_spotlight_metadata(
        eligible_paths,
        mdimport_path=mdimport_path,
        deadline=deadline,
    )
    if error is not None:
        problems.append(error)
    initial_import_finished_at = time.monotonic()
    retry_deadline = initial_import_finished_at + (
        max(0.0, deadline - initial_import_finished_at) / 2
    )
    if not mdls_path.is_file():
        problems.append(f"mdls is unavailable at {mdls_path}")
        _print_stderr(f"warning: Spotlight refresh incomplete: {'; '.join(problems)}")
        return

    missing, mdls_error = _wait_for_spotlight_application_records(
        expected_metadata,
        mdls_path=mdls_path,
        deadline=retry_deadline,
    )
    if mdls_error is not None:
        problems.append(mdls_error)

    if missing and time.monotonic() < deadline:
        retry_error = _import_spotlight_metadata(
            list(missing),
            mdimport_path=mdimport_path,
            deadline=deadline,
        )
        if retry_error is not None and retry_error not in problems:
            problems.append(retry_error)
        missing, mdls_error = _wait_for_spotlight_application_records(
            missing,
            mdls_path=mdls_path,
            deadline=deadline,
        )
        if mdls_error is not None and mdls_error not in problems:
            problems.append(mdls_error)

    if missing:
        missing_paths = ", ".join(str(path) for path in missing)
        problems.append(f"metadata is still missing or stale for {missing_paths}")
    if problems:
        _print_stderr(f"warning: Spotlight refresh incomplete: {'; '.join(problems)}")


def _install_managed_app(
    *,
    bundle_name: str,
    mode: str,
    source_path: str,
    target_directory: Path,
    rsync_path: str,
    writable: bool,
) -> None:
    _ensure_single_path_component(bundle_name, field="bundleName")
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
        _refresh_launch_services_registration(dst)
        return

    if dst.is_symlink() or (_path_exists(dst) and not dst.is_dir()):
        _remove_path(dst)

    dst.mkdir(parents=True, exist_ok=True)
    _rsync_copy(src, dst, rsync_path=rsync_path, writable=writable)
    _refresh_launch_services_registration(dst)


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
        f"Move those packages out of {label} so the scoped macOS app manager "
        "stays the only mutable app-bundle surface."
    )
    for offending_bundle in offending_bundles:
        _print_stderr(f" - {offending_bundle}")
    raise SystemExit(1)


def _remove_profile_copies(payload: Mapping[str, object]) -> None:
    parsed = _remove_profile_copies_payload(payload)
    target_directory = Path(parsed["targetDirectory"])
    bundle_names = parsed["bundleNames"]

    for bundle_name in bundle_names:
        _ensure_single_path_component(bundle_name, field="bundleNames")

    if target_directory.is_symlink():
        return

    for bundle_name in bundle_names:
        target_path = target_directory / bundle_name
        if not _path_exists(target_path):
            continue

        _print_stderr(
            f"removing Home Manager copy of scoped managed app {target_path}..."
        )
        _make_tree_user_writable(target_path)
        _remove_path(target_path)


def _system_applications(payload: Mapping[str, object]) -> None:
    parsed = _system_applications_payload(payload)
    entries = parsed["entries"]
    state_name = parsed["stateName"]
    target_directory = Path(parsed["targetDirectory"])
    state_directory = Path(parsed["stateDirectory"])
    _ensure_single_path_component(state_name, field="stateName")
    for entry in entries:
        _ensure_single_path_component(entry["bundleName"], field="entries.bundleName")

    state_file = state_directory / f"{state_name}.txt"
    current_apps = [entry["bundleName"] for entry in entries]
    current_app_set = set(current_apps)
    rsync_path = parsed["rsyncPath"]
    writable = parsed["writable"]

    _preflight_managed_apps(entries, target_directory)

    target_directory.mkdir(parents=True, exist_ok=True)
    state_directory.mkdir(parents=True, exist_ok=True)
    if state_file.is_file():
        state_file.chmod(0o644)

    for managed_app in _read_manifest(state_file):
        if not managed_app or managed_app in current_app_set:
            continue
        _ensure_single_path_component(managed_app, field="manifest entry")

        target_path = target_directory / managed_app
        if _app_in_other_manifests(managed_app, state_directory, state_file):
            _print_stderr(
                f"keeping {target_path} because another manifest still manages it..."
            )
            continue

        _print_stderr(f"removing stale managed app {target_path}...")
        _make_tree_user_writable(target_path)
        _remove_path(target_path)

    if entries:
        with ThreadPoolExecutor(
            max_workers=min(len(entries), MAX_INSTALL_WORKERS)
        ) as executor:
            futures = [
                executor.submit(
                    _install_managed_app,
                    bundle_name=entry["bundleName"],
                    mode=entry["mode"],
                    source_path=entry["sourcePath"],
                    target_directory=target_directory,
                    rsync_path=rsync_path,
                    writable=writable,
                )
                for entry in entries
            ]
            for future in as_completed(futures):
                future.result()

    _write_managed_app_manifest(state_file, current_apps)

    _refresh_spotlight_metadata([
        target_directory / entry["bundleName"] for entry in entries
    ])


def main(argv: list[str] | None = None) -> int:
    """Run one helper subcommand from a JSON payload file."""
    active_argv = sys.argv if argv is None else argv
    command, payload = _load_payload(active_argv)

    if command == "profile-bundle-leak-audit":
        _profile_bundle_leak_audit(payload)
        return 0

    if command == "remove-profile-copies":
        _remove_profile_copies(payload)
        return 0

    if command == "system-applications":
        _system_applications(payload)
        return 0

    _print_stderr(f"unknown command: {command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
