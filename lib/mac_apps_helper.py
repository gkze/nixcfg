"""Helpers for nixcfg macOS application activation scripts.

This file is also shipped standalone: ``lib/mac-apps.nix`` writes it into the
store with ``writeText "nixcfg-mac-apps-helper.py" (readFile ./mac_apps_helper.py)``
and runs it under ``python3.withPackages (ps: [ ps.packaging ])``. It may
therefore import only the standard library and ``packaging`` -- never ``lib.*``,
which is not on that interpreter's path. ``_as_object_dict`` is deliberately
kept in step with ``lib.json_utils.as_object_dict`` for that reason: dedup by
importing the shared helper would pass the test suite and then fail activation.

System applications are staged in a transaction directory on the target
filesystem. Staging, bundle comparisons, and the post-commit LaunchServices
refresh run in bounded pools because they are independent per bundle. Live
bundles change only by rename after every install stages, and
the ownership manifest is published only after current and stale promotions.
A target-wide lock serializes managers, while a durable journal records the
previous manifest and every rename. Activation recovers recognized incomplete
transactions under that lock; unrelated legacy transaction directories remain
untouched. Any failure before the publication commit point restores both apps
and manifest, and incomplete rollback retains artifacts with recovery guidance.
"""

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

# flock is provided by macOS's standard library; the shipped helper still has
# no third-party runtime dependency beyond packaging.
fcntl = __import__("fcntl")

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

EXPECTED_ARGC = 3
MAX_INSTALL_WORKERS = 8
MAX_METADATA_WORKERS = 4
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
APPLICATION_LOCK_NAME = ".nixcfg-app-activation.lock"
APPLICATION_TRANSACTION_PREFIX = ".nixcfg-app-activation-"
APPLICATION_JOURNAL_NAME = "journal.json"
APPLICATION_JOURNAL_VERSION = 1
MAX_FILE_MODE = 0o7777
APPLICATION_ACTIVE_PHASES = {"prepared", "publishing"}
APPLICATION_FINISHED_PHASES = {"committed", "recovered"}
MANIFEST_FIELD_SEPARATOR = "\t"


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


class _ApplicationChange:
    """One journaled rename pair, including stale removal with no replacement."""

    __slots__ = (
        "backup",
        "original_exists",
        "original_mode",
        "original_moved",
        "replacement",
        "replacement_mode",
        "replacement_moved",
        "target",
    )

    def __init__(
        self,
        *,
        target: Path,
        backup: Path,
        replacement: Path | None,
    ) -> None:
        self.target = target
        self.backup = backup
        self.replacement = replacement
        self.original_exists = _path_exists(target)
        self.original_mode = _directory_mode(target)
        self.replacement_mode = (
            _directory_mode(replacement) if replacement is not None else None
        )
        self.original_moved = False
        self.replacement_moved = False

    @classmethod
    def from_journal(cls, payload: Mapping[str, object]) -> _ApplicationChange:
        """Rehydrate recorded state without inspecting the interrupted live paths."""
        change = cls.__new__(cls)
        change.target = Path(_journal_string(payload, "target"))
        change.backup = Path(_journal_string(payload, "backup"))
        replacement = payload.get("replacement")
        if replacement is not None and not isinstance(replacement, str):
            msg = "journal field 'replacement' must be a string or null"
            raise TypeError(msg)
        change.replacement = Path(replacement) if replacement is not None else None
        change.original_exists = _journal_bool(payload, "originalExists")
        change.original_mode = _journal_mode(payload, "originalMode")
        change.replacement_mode = _journal_mode(payload, "replacementMode")
        change.original_moved = _journal_bool(payload, "originalMoved")
        change.replacement_moved = _journal_bool(payload, "replacementMoved")
        return change

    def journal_payload(self) -> dict[str, object]:
        return {
            "target": str(self.target),
            "backup": str(self.backup),
            "replacement": (
                str(self.replacement) if self.replacement is not None else None
            ),
            "originalExists": self.original_exists,
            "originalMode": self.original_mode,
            "replacementMode": self.replacement_mode,
            "originalMoved": self.original_moved,
            "replacementMoved": self.replacement_moved,
        }


class _ApplicationTransaction:
    """The single durable app transaction owned by one target directory."""

    __slots__ = (
        "changes",
        "directory",
        "phase",
        "previous_manifest_exists",
        "state_file",
        "target_directory",
    )

    def __init__(
        self,
        *,
        directory: Path,
        target_directory: Path,
        state_file: Path,
        previous_manifest_exists: bool,
        changes: Sequence[_ApplicationChange],
        phase: str = "prepared",
    ) -> None:
        self.directory = directory
        self.target_directory = target_directory
        self.state_file = state_file
        self.previous_manifest_exists = previous_manifest_exists
        self.changes = list(changes)
        self.phase = phase

    @property
    def journal_path(self) -> Path:
        return self.directory / APPLICATION_JOURNAL_NAME

    @property
    def previous_manifest_path(self) -> Path:
        return self.directory / "previous-manifest"

    def journal_payload(self) -> dict[str, object]:
        return {
            "version": APPLICATION_JOURNAL_VERSION,
            "phase": self.phase,
            "targetDirectory": str(self.target_directory),
            "stateFile": str(self.state_file),
            "previousManifestExists": self.previous_manifest_exists,
            "changes": [change.journal_payload() for change in self.changes],
        }


type _BundleVersionMetadata = tuple[str | None, str | None, str | None]
type _ManifestRecord = tuple[str, str | None]


def _print_stderr(message: str) -> None:
    sys.stderr.write(f"{message}\n")


def _write_managed_app_manifest(
    state_file: Path,
    records: Sequence[tuple[str, str]],
) -> None:
    """Atomically replace one manager's complete app-ownership manifest.

    Each record pairs the bundle name with the store path it was copied from, so
    the next activation can tell unchanged bundles apart without reading them.
    """
    contents = "".join(
        f"{bundle_name}{MANIFEST_FIELD_SEPARATOR}{source_path}\n"
        for bundle_name, source_path in records
    )
    _atomic_write_bytes(state_file, contents.encode("utf-8"))


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_bytes(path: Path, contents: bytes) -> None:
    """Durably replace one file without exposing partial contents."""
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            os.fchmod(temporary.fileno(), 0o644)
            temporary.write(contents)
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path.replace(path)
        temporary_path = None
        _fsync_directory(path.parent)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _write_application_journal(transaction: _ApplicationTransaction) -> None:
    """Atomically record a transaction phase or completed rename."""
    contents = json.dumps(
        transaction.journal_payload(),
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    _atomic_write_bytes(transaction.journal_path, contents + b"\n")


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


def _directory_mode(path: Path | None) -> int | None:
    if path is None or path.is_symlink() or not path.is_dir():
        return None
    return stat.S_IMODE(path.stat().st_mode)


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


def _read_manifest_records(path: Path) -> list[_ManifestRecord]:
    """Read ownership records, tolerating legacy name-only manifest lines."""
    if not path.is_file():
        return []
    records: list[_ManifestRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        bundle_name, _, source_path = line.partition(MANIFEST_FIELD_SEPARATOR)
        records.append((bundle_name, source_path or None))
    return records


def _read_manifest(path: Path) -> list[str]:
    return [bundle_name for bundle_name, _source in _read_manifest_records(path)]


def _app_in_other_manifests(
    needle: str, state_directory: Path, state_file: Path
) -> bool:
    for manifest in sorted(state_directory.glob("*.txt")):
        if manifest == state_file or not manifest.is_file():
            continue
        if needle in _read_manifest(manifest):
            return True
    return False


def _journal_object(value: object, *, context: str) -> dict[str, object]:
    return _as_object_dict(value, context=context)


def _journal_string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        msg = f"journal field {key!r} must be a string"
        raise TypeError(msg)
    return value


def _journal_bool(payload: Mapping[str, object], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        msg = f"journal field {key!r} must be a boolean"
        raise TypeError(msg)
    return value


def _journal_mode(payload: Mapping[str, object], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"journal field {key!r} must be a file mode or null"
        raise TypeError(msg)
    if not 0 <= value <= MAX_FILE_MODE:
        msg = f"journal field {key!r} is outside the file-mode range"
        raise ValueError(msg)
    return value


def _load_application_transaction(
    transaction_directory: Path,
    target_directory: Path,
) -> _ApplicationTransaction:
    """Read and validate one journal before using any of its paths."""
    journal_path = transaction_directory / APPLICATION_JOURNAL_NAME
    payload = _journal_object(
        json.loads(journal_path.read_text(encoding="utf-8")),
        context=str(journal_path),
    )
    version = payload.get("version")
    if isinstance(version, bool) or version != APPLICATION_JOURNAL_VERSION:
        msg = f"unsupported application journal version: {version!r}"
        raise ValueError(msg)
    recorded_target = Path(_journal_string(payload, "targetDirectory"))
    if recorded_target != target_directory:
        msg = f"journal target {recorded_target} does not match {target_directory}"
        raise ValueError(msg)
    state_file = Path(_journal_string(payload, "stateFile"))
    if (
        not state_file.is_absolute()
        or state_file.suffix != ".txt"
        or state_file.is_relative_to(transaction_directory)
    ):
        msg = f"journal has an unsafe state file path: {state_file}"
        raise ValueError(msg)
    phase = _journal_string(payload, "phase")
    if phase not in APPLICATION_ACTIVE_PHASES | APPLICATION_FINISHED_PHASES:
        msg = f"unknown application transaction phase: {phase!r}"
        raise ValueError(msg)
    raw_changes = payload.get("changes")
    if not isinstance(raw_changes, list):
        msg = "journal field 'changes' must be a list"
        raise TypeError(msg)

    changes: list[_ApplicationChange] = []
    targets: set[Path] = set()
    for index, raw_change in enumerate(raw_changes):
        change = _ApplicationChange.from_journal(
            _journal_object(raw_change, context=f"journal changes[{index}]")
        )
        if (
            change.target.parent != target_directory
            or change.target.name in {"", ".", ".."}
            or change.target in targets
        ):
            msg = f"journal has an unsafe or duplicate app target: {change.target}"
            raise ValueError(msg)
        expected_backup = (
            transaction_directory / "backups" / str(index) / change.target.name
        )
        expected_replacement = (
            transaction_directory / "staged" / str(index) / change.target.name
        )
        if change.backup != expected_backup or (
            change.replacement is not None
            and change.replacement != expected_replacement
        ):
            msg = f"journal has unsafe artifact paths for {change.target}"
            raise ValueError(msg)
        targets.add(change.target)
        changes.append(change)

    return _ApplicationTransaction(
        directory=transaction_directory,
        target_directory=target_directory,
        state_file=state_file,
        previous_manifest_exists=_journal_bool(payload, "previousManifestExists"),
        changes=changes,
        phase=phase,
    )


def _acquire_application_lock(target_directory: Path) -> int:
    """Hold the stable target-wide lock until the caller closes its descriptor."""
    descriptor = os.open(
        target_directory / APPLICATION_LOCK_NAME,
        os.O_RDWR | os.O_CREAT,
        0o600,
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _rsync_flags(*, writable: bool) -> list[str]:
    """Share the copy semantics between the copy and the comparison."""
    return [
        "--checksum",
        "--copy-unsafe-links",
        "--archive",
        "--delete",
        # Finder stores a bundle's custom icon in Icon\r and marks the directory
        # separately. Deleting only the resource leaves a generic folder icon.
        "--filter=P /Icon\r",
        "--chmod=+w" if writable else "--chmod=-w",
        "--no-group",
        "--no-owner",
    ]


def _rsync_copy(src: Path, dst: Path, *, rsync_path: str, writable: bool) -> None:
    result = subprocess.run(  # noqa: S603
        [rsync_path, *_rsync_flags(writable=writable), f"{src}/", str(dst)],
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def _bundle_trees_match(
    src: Path,
    dst: Path,
    *,
    rsync_path: str,
    writable: bool,
) -> bool:
    """Report whether an installed bundle already matches its store bundle.

    This is a dry run with the same flags the copy uses, so the answer agrees
    exactly with what a copy would change. It never writes to either side, which
    matters because installed bundles may be protected from modification.
    """
    try:
        result = subprocess.run(  # noqa: S603
            [
                rsync_path,
                *_rsync_flags(writable=writable),
                "--dry-run",
                "--itemize-changes",
                f"{src}/",
                str(dst),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    return result.returncode == 0 and not result.stdout.strip()


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


def _register_application_bundles(app_paths: Sequence[Path]) -> None:
    """Register promoted bundles with LaunchServices without serialising spawns.

    Registration is one process per bundle. Running them in a bounded pool keeps
    the post-commit phase proportional to bundles that actually changed instead
    of to every managed bundle, and lets the independent processes overlap.
    """
    if not app_paths:
        return

    with ThreadPoolExecutor(
        max_workers=min(len(app_paths), MAX_METADATA_WORKERS)
    ) as executor:
        futures = [
            executor.submit(_refresh_launch_services_registration, app_path)
            for app_path in app_paths
        ]
        for future in as_completed(futures):
            future.result()


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
    announce: bool = True,
    register: bool = True,
) -> None:
    _ensure_single_path_component(bundle_name, field="bundleName")
    src = Path(source_path)
    dst = target_directory / bundle_name

    if not src.is_dir():
        _print_stderr(f"Expected macOS app bundle at {src}")
        raise SystemExit(1)

    if announce:
        _print_stderr(f"setting up {dst}...")

    if mode == "symlink":
        if _path_exists(dst):
            _remove_path(dst)
        dst.symlink_to(src)
        if register:
            _refresh_launch_services_registration(dst)
        return

    if dst.is_symlink() or (_path_exists(dst) and not dst.is_dir()):
        _remove_path(dst)

    dst.mkdir(parents=True, exist_ok=True)
    _rsync_copy(src, dst, rsync_path=rsync_path, writable=writable)
    if register:
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


def _bundle_is_current(
    entry: _SystemApplicationEntryPayload,
    target_path: Path,
    *,
    recorded_source: str | None,
    rsync_path: str,
    writable: bool,
) -> bool:
    """Report whether the live bundle already satisfies the declaration.

    A recorded source that matches is authoritative because store paths are
    content addressed. Legacy manifests carry no source, so fall back to one
    read-only comparison against the declared bundle.
    """
    source_path = Path(entry["sourcePath"])
    if not _path_exists(target_path):
        return False
    if entry["mode"] == "symlink":
        return target_path.is_symlink() and target_path.readlink() == source_path
    if target_path.is_symlink() or not target_path.is_dir():
        return False
    if recorded_source is not None:
        return recorded_source == entry["sourcePath"]
    return _bundle_trees_match(
        source_path,
        target_path,
        rsync_path=rsync_path,
        writable=writable,
    )


def _changed_application_entries(
    entries: Sequence[_SystemApplicationEntryPayload],
    *,
    target_directory: Path,
    recorded_sources: Mapping[str, str | None],
    rsync_path: str,
    writable: bool,
) -> list[_SystemApplicationEntryPayload]:
    """Return declared bundles the target directory does not already satisfy.

    Each check is either a stat plus a manifest comparison or, for legacy
    manifests, one rsync dry run. The dry runs are independent per bundle, so
    they overlap in a bounded pool; results keep declaration order so the
    transaction and its diagnostics stay deterministic.
    """
    if not entries:
        return []

    with ThreadPoolExecutor(
        max_workers=min(len(entries), MAX_INSTALL_WORKERS)
    ) as executor:
        futures = [
            executor.submit(
                _bundle_is_current,
                entry,
                target_directory / entry["bundleName"],
                recorded_source=recorded_sources.get(entry["bundleName"]),
                rsync_path=rsync_path,
                writable=writable,
            )
            for entry in entries
        ]
        current = [future.result() for future in futures]

    return [
        entry
        for entry, is_current in zip(entries, current, strict=True)
        if not is_current
    ]


def _stage_managed_apps(
    entries: Sequence[_SystemApplicationEntryPayload],
    *,
    staging_directory: Path,
    target_directory: Path,
    rsync_path: str,
    writable: bool,
) -> None:
    """Stage every app without changing a live bundle or registering a temp path."""
    if not entries:
        return

    with ThreadPoolExecutor(
        max_workers=min(len(entries), MAX_INSTALL_WORKERS)
    ) as executor:
        futures = []
        for index, entry in enumerate(entries):
            bundle_name = entry["bundleName"]
            staged_parent = staging_directory / str(index)
            staged_parent.mkdir(parents=True)
            _print_stderr(f"setting up {target_directory / bundle_name}...")
            futures.append(
                executor.submit(
                    _install_managed_app,
                    bundle_name=bundle_name,
                    mode=entry["mode"],
                    source_path=entry["sourcePath"],
                    target_directory=staged_parent,
                    rsync_path=rsync_path,
                    writable=writable,
                    announce=False,
                    register=False,
                )
            )
        for future in as_completed(futures):
            future.result()


def _make_directory_renameable(path: Path) -> int | None:
    """Temporarily add user-write permission to a bundle root for macOS rename."""
    if path.is_symlink() or not path.is_dir():
        return None
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & stat.S_IWUSR:
        return None
    path.chmod(mode | stat.S_IWUSR)
    return mode


def _apply_application_change(
    change: _ApplicationChange,
    transaction: _ApplicationTransaction,
) -> None:
    """Apply one rename pair, durably recording each completed rename."""
    if change.original_exists != _path_exists(change.target):
        msg = f"app changed after transaction preparation: {change.target}"
        raise OSError(msg)

    if change.original_exists:
        change.backup.parent.mkdir(parents=True, exist_ok=True)
        if change.original_mode is not None and not change.original_mode & stat.S_IWUSR:
            change.target.chmod(change.original_mode | stat.S_IWUSR)
        change.target.replace(change.backup)
        change.original_moved = True
        _write_application_journal(transaction)

    if change.replacement is not None:
        if not _path_exists(change.replacement):
            msg = f"staged app disappeared before promotion: {change.replacement}"
            raise OSError(msg)
        if (
            change.replacement_mode is not None
            and not change.replacement_mode & stat.S_IWUSR
        ):
            change.replacement.chmod(change.replacement_mode | stat.S_IWUSR)
        change.replacement.replace(change.target)
        change.replacement_moved = True
        if change.replacement_mode is not None:
            change.target.chmod(change.replacement_mode)
        _write_application_journal(transaction)


def _restore_replacement_to_staging(
    change: _ApplicationChange,
    replacement: Path,
) -> None:
    """Move a promoted replacement out of the live target before restoring backup."""
    _make_directory_renameable(change.target)
    change.target.replace(replacement)
    if change.replacement_mode is not None:
        replacement.chmod(change.replacement_mode)


def _restore_application_change(change: _ApplicationChange) -> None:
    """Infer the interrupted position from artifacts and restore the old target.

    A crash can happen after a rename but before its journal update. The recorded
    flags alone therefore cannot identify the state; the backup, replacement,
    and target existence pattern can, including a partially completed rollback.
    """
    target_exists = _path_exists(change.target)
    backup_exists = _path_exists(change.backup)
    replacement = change.replacement

    if replacement is None:
        if not change.original_exists:
            if backup_exists:
                msg = f"unexpected backup for originally absent app {change.target}"
                raise OSError(msg)
            return
        if backup_exists:
            if target_exists:
                msg = f"both target and backup exist for stale app {change.target}"
                raise OSError(msg)
            change.backup.replace(change.target)
        elif not target_exists:
            msg = f"stale app and its backup are both missing: {change.target}"
            raise OSError(msg)
        if change.original_mode is not None:
            change.target.chmod(change.original_mode)
        return

    replacement_exists = _path_exists(replacement)
    if not change.original_exists:
        if backup_exists:
            msg = f"unexpected backup for originally absent app {change.target}"
            raise OSError(msg)
        if replacement_exists:
            if target_exists:
                msg = f"unrecorded target appeared beside staged app {change.target}"
                raise OSError(msg)
            return
        if not target_exists:
            msg = (
                f"promoted app and its staged replacement are missing: {change.target}"
            )
            raise OSError(msg)
        _restore_replacement_to_staging(change, replacement)
        return

    if backup_exists:
        if replacement_exists:
            if target_exists:
                msg = f"target, backup, and staged app all exist for {change.target}"
                raise OSError(msg)
        else:
            if not target_exists:
                msg = f"all live and staged copies are incomplete for {change.target}"
                raise OSError(msg)
            _restore_replacement_to_staging(change, replacement)
        change.backup.replace(change.target)
    elif not replacement_exists or not target_exists:
        msg = f"cannot locate original and staged copies for {change.target}"
        raise OSError(msg)

    if change.original_mode is not None:
        change.target.chmod(change.original_mode)


def _rollback_application_changes(
    changes: Sequence[_ApplicationChange],
) -> list[str]:
    """Restore prior paths in reverse order and retain artifacts after any failure."""
    problems: list[str] = []
    for change in reversed(changes):
        try:
            _restore_application_change(change)
        except OSError as error:
            problems.append(f"could not restore {change.target}: {error}")
    return problems


def _restore_application_manifest(transaction: _ApplicationTransaction) -> None:
    """Restore exact prior manifest bytes, or its prior absence, atomically."""
    if transaction.previous_manifest_exists:
        _atomic_write_bytes(
            transaction.state_file,
            transaction.previous_manifest_path.read_bytes(),
        )
        return
    if _path_exists(transaction.state_file):
        transaction.state_file.unlink()
    _fsync_directory(transaction.state_file.parent)


def _rollback_application_transaction(
    transaction: _ApplicationTransaction,
) -> list[str]:
    """Roll back apps and manifest, reporting every failed restoration."""
    problems = _rollback_application_changes(transaction.changes)
    try:
        _restore_application_manifest(transaction)
    except OSError as error:
        problems.append(f"could not restore manifest {transaction.state_file}: {error}")
    return problems


def _cleanup_application_transaction(transaction_directory: Path) -> None:
    """Remove artifacts without masking an outcome; explicitly retain them on error."""
    try:
        _make_tree_user_writable(transaction_directory)
        _remove_path(transaction_directory)
    except OSError as error:
        _print_stderr(
            f"warning: could not remove application transaction "
            f"{transaction_directory}: {error}; transaction artifacts remain there."
        )


def _recovery_guidance(transaction_directory: Path, detail: str) -> str:
    return (
        f"cannot safely recover application transaction {transaction_directory}: "
        f"{detail}. Activation did not start a new transaction. Inspect "
        f"{transaction_directory / APPLICATION_JOURNAL_NAME} and its artifacts, "
        "restore the recorded app bundles and ownership manifest manually, then "
        "remove only that transaction directory after verification and rerun "
        "activation."
    )


def _recover_application_transactions(target_directory: Path) -> None:
    """Reconcile recognized transactions; preserve unknown legacy directories."""
    candidates = sorted(target_directory.glob(f"{APPLICATION_TRANSACTION_PREFIX}*"))
    for transaction_directory in candidates:
        if transaction_directory.is_symlink() or not transaction_directory.is_dir():
            continue
        journal_path = transaction_directory / APPLICATION_JOURNAL_NAME
        if not _path_exists(journal_path):
            continue
        try:
            transaction = _load_application_transaction(
                transaction_directory,
                target_directory,
            )
        except (OSError, TypeError, UnicodeError, ValueError) as error:
            raise RuntimeError(
                _recovery_guidance(transaction_directory, str(error))
            ) from error

        if transaction.phase in APPLICATION_FINISHED_PHASES:
            _cleanup_application_transaction(transaction_directory)
            continue

        problems = _rollback_application_transaction(transaction)
        if problems:
            raise RuntimeError(
                _recovery_guidance(transaction_directory, "; ".join(problems))
            )
        transaction.phase = "recovered"
        try:
            _write_application_journal(transaction)
        except OSError as error:
            raise RuntimeError(
                _recovery_guidance(
                    transaction_directory,
                    f"could not record completed recovery: {error}",
                )
            ) from error
        _cleanup_application_transaction(transaction_directory)


def _report_incomplete_rollback(
    error: BaseException,
    *,
    problems: Sequence[str],
    transaction_directory: Path,
    state_file: Path,
) -> None:
    _print_stderr(
        f"application activation failed ({type(error).__name__}: {error}) and "
        f"rollback is incomplete; live applications may differ from {state_file}."
    )
    for problem in problems:
        _print_stderr(f"rollback error: {problem}")
    _print_stderr(
        f"transaction artifacts were retained at {transaction_directory}; "
        "reconcile the reported paths and manifest manually, or fix the reported "
        "error so the next activation can retry recovery under the target lock."
    )


def _reconcile_managed_apps(
    entries: Sequence[_SystemApplicationEntryPayload],
    *,
    changed_entries: Sequence[_SystemApplicationEntryPayload],
    stale_apps: Sequence[str],
    state_file: Path,
    target_directory: Path,
    rsync_path: str,
    writable: bool,
) -> None:
    """Stage, promote, publish ownership, and clean up as one app transaction."""
    current_apps = [(entry["bundleName"], entry["sourcePath"]) for entry in entries]
    if not changed_entries and not stale_apps:
        _write_managed_app_manifest(state_file, current_apps)
        return

    previous_manifest = state_file.read_bytes() if _path_exists(state_file) else None
    transaction_directory = Path(
        tempfile.mkdtemp(prefix=APPLICATION_TRANSACTION_PREFIX, dir=target_directory)
    )
    staging_directory = transaction_directory / "staged"
    backup_directory = transaction_directory / "backups"
    transaction: _ApplicationTransaction | None = None
    try:
        _stage_managed_apps(
            changed_entries,
            staging_directory=staging_directory,
            target_directory=target_directory,
            rsync_path=rsync_path,
            writable=writable,
        )

        changes: list[_ApplicationChange] = []
        for index, entry in enumerate(changed_entries):
            bundle_name = entry["bundleName"]
            changes.append(
                _ApplicationChange(
                    target=target_directory / bundle_name,
                    backup=backup_directory / str(index) / bundle_name,
                    replacement=staging_directory / str(index) / bundle_name,
                )
            )
        for stale_index, managed_app in enumerate(
            stale_apps, start=len(changed_entries)
        ):
            changes.append(
                _ApplicationChange(
                    target=target_directory / managed_app,
                    backup=backup_directory / str(stale_index) / managed_app,
                    replacement=None,
                )
            )

        transaction = _ApplicationTransaction(
            directory=transaction_directory,
            target_directory=target_directory,
            state_file=state_file,
            previous_manifest_exists=previous_manifest is not None,
            changes=changes,
        )
        if previous_manifest is not None:
            _atomic_write_bytes(
                transaction.previous_manifest_path,
                previous_manifest,
            )
        _write_application_journal(transaction)

        for change in changes:
            if change.replacement is None:
                _print_stderr(f"removing stale managed app {change.target}...")
            _apply_application_change(change, transaction)

        transaction.phase = "publishing"
        _write_application_journal(transaction)
        _write_managed_app_manifest(state_file, current_apps)
    except BaseException as error:
        if transaction is None:
            _cleanup_application_transaction(transaction_directory)
            raise
        rollback_problems = _rollback_application_transaction(transaction)
        if not rollback_problems:
            transaction.phase = "recovered"
            try:
                _write_application_journal(transaction)
            except (OSError, KeyboardInterrupt) as journal_error:
                rollback_problems.append(
                    f"could not record completed rollback: {journal_error}"
                )
        if rollback_problems:
            _report_incomplete_rollback(
                error,
                problems=rollback_problems,
                transaction_directory=transaction_directory,
                state_file=state_file,
            )
        else:
            _cleanup_application_transaction(transaction_directory)
        raise

    # Successful return from the manifest writer is the commit point. Cleanup,
    # commit recording, and metadata failures must never roll back live apps.
    transaction.phase = "committed"
    _write_application_journal(transaction)
    _cleanup_application_transaction(transaction_directory)

    # Only promoted bundles need new OS registrations. Unchanged bundles keep the
    # registration and Spotlight record they already had, so the cost of this
    # phase follows what changed rather than the size of the managed set.
    promoted_paths = [
        target_directory / entry["bundleName"] for entry in changed_entries
    ]
    _register_application_bundles(promoted_paths)
    _refresh_spotlight_metadata(promoted_paths)


def _system_applications(payload: Mapping[str, object]) -> None:
    parsed = _system_applications_payload(payload)
    entries = parsed["entries"]
    state_name = parsed["stateName"]
    target_directory = Path(parsed["targetDirectory"]).absolute()
    state_directory = Path(parsed["stateDirectory"]).absolute()
    _ensure_single_path_component(state_name, field="stateName")
    for entry in entries:
        _ensure_single_path_component(entry["bundleName"], field="entries.bundleName")

    state_file = state_directory / f"{state_name}.txt"
    current_app_set = {entry["bundleName"] for entry in entries}
    rsync_path = parsed["rsyncPath"]
    writable = parsed["writable"]

    target_directory.mkdir(parents=True, exist_ok=True)
    lock_descriptor = _acquire_application_lock(target_directory)
    try:
        _recover_application_transactions(target_directory)
        _preflight_managed_apps(entries, target_directory)

        state_directory.mkdir(parents=True, exist_ok=True)
        if state_file.is_file():
            state_file.chmod(0o644)

        stale_apps: list[str] = []
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
            stale_apps.append(managed_app)

        recorded_sources = dict(_read_manifest_records(state_file))
        changed_entries = _changed_application_entries(
            entries,
            target_directory=target_directory,
            recorded_sources=recorded_sources,
            rsync_path=rsync_path,
            writable=writable,
        )
        unchanged_count = len(entries) - len(changed_entries)
        if unchanged_count:
            _print_stderr(
                f"keeping {unchanged_count} unchanged app bundle(s) in "
                f"{target_directory}..."
            )

        _reconcile_managed_apps(
            entries,
            changed_entries=changed_entries,
            stale_apps=stale_apps,
            state_file=state_file,
            target_directory=target_directory,
            rsync_path=rsync_path,
            writable=writable,
        )
    finally:
        os.close(lock_descriptor)


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


if __name__ == "__main__":  # pragma: no cover -- standalone module entry point
    raise SystemExit(main())
