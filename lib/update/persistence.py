"""Persistence helpers for update source and artifact results."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Self, cast

from filelock import FileLock, Timeout

from lib.nix.models.sources import SourcesFile
from lib.update import artifacts as update_artifacts
from lib.update import crate2nix as update_crate2nix
from lib.update import flake as update_flake
from lib.update import paths as update_paths
from lib.update import sources as update_sources
from lib.update.paths import (
    get_repo_root,
    package_dir_for_in,
    package_file_map_in,
    sources_file_for_updater,
)


@dataclass(frozen=True, slots=True)
class _WorkspaceFileState:
    content: bytes
    mode: int
    symlink: bool = False


type _WorkspacePathState = _WorkspaceFileState | None

_PROCESS_WORKSPACE_LOCK = Lock()
_OPEN_DIRECTORY_FLAGS = (
    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
)
_OPEN_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
_SNAPSHOT_ATTEMPTS = 3
_TRANSACTION_JOURNAL = "nixcfg-update-transaction.json"
_SHA256_HEX_LENGTH = 64

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping

    from lib.nix.models.sources import SourceEntry
    from lib.update.artifacts import GeneratedArtifact
    from lib.update.ui_state import SummaryStatus
    from lib.update.updaters import UpdaterClass


class _WorkspaceSnapshotError(RuntimeError):
    """Internal signal that a path changed while its state was being read."""


class UpdateWorkspaceError(RuntimeError):
    """Base error for isolated update workspace promotion failures."""


class UpdateWorkspaceUnexpectedPathsError(UpdateWorkspaceError):
    """Raised when an updater writes outside its declared path set."""

    def __init__(self, paths: Iterable[Path]) -> None:
        """Report the paths produced outside the declared write set."""
        self.paths = tuple(paths)
        joined = ", ".join(os.fspath(path) for path in self.paths)
        super().__init__(f"Update produced unexpected paths: {joined}")


class UpdateWorkspaceConflictError(UpdateWorkspaceError):
    """Raised when promotion or rollback would overwrite an external edit."""

    def __init__(self, paths: Iterable[Path]) -> None:
        """Report paths preserved because their live state diverged."""
        self.paths = tuple(paths)
        joined = ", ".join(os.fspath(path) for path in self.paths)
        super().__init__(f"Update workspace conflict; preserved paths: {joined}")


def _git_binary() -> str:
    git = shutil.which("git")
    if git is None:
        msg = "Git is required to create an isolated update workspace"
        raise UpdateWorkspaceError(msg)
    return git


def _git_paths(root: Path, *args: str) -> tuple[Path, ...]:
    git = _git_binary()
    result = subprocess.run(  # noqa: S603 -- fixed local Git operation
        [git, *args, "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return tuple(
        Path(os.fsdecode(raw_path))
        for raw_path in result.stdout.split(b"\0")
        if raw_path
    )


def _run_git(root: Path, *args: str) -> None:
    git = _git_binary()
    subprocess.run(  # noqa: S603 -- fixed local Git operation
        [git, *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def _git_common_dir(root: Path) -> Path:
    git = _git_binary()
    result = subprocess.run(  # noqa: S603 -- fixed local Git operation
        [git, "rev-parse", "--git-common-dir"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    path = Path(result.stdout.strip())
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _stat_fingerprint(metadata: os.stat_result) -> tuple[int, ...]:
    """Return every stat field that must stay stable while snapshotting."""
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


@contextmanager
def _open_parent_descriptor(
    root_descriptor: int,
    path: Path,
) -> Iterator[int]:
    """Open a repository-relative parent without following any symlink."""
    descriptor = os.dup(root_descriptor)
    try:
        for component in path.parent.parts:
            child = os.open(component, _OPEN_DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def _snapshot_leaf(
    parent_descriptor: int,
    name: str,
    *,
    display_path: Path,
) -> _WorkspacePathState:
    try:
        before = os.lstat(name, dir_fd=parent_descriptor)
    except FileNotFoundError:
        return None
    mode = stat.S_IMODE(before.st_mode)
    if stat.S_ISREG(before.st_mode):
        try:
            descriptor = os.open(name, _OPEN_FILE_FLAGS, dir_fd=parent_descriptor)
        except OSError as error:
            if error.errno not in {
                errno.ENOENT,
                errno.ELOOP,
            }:
                raise
            raise _WorkspaceSnapshotError(display_path) from error
        try:
            opened = os.fstat(descriptor)
            if _stat_fingerprint(opened) != _stat_fingerprint(before):
                raise _WorkspaceSnapshotError(display_path)
            with os.fdopen(os.dup(descriptor), "rb") as stream:
                content = stream.read()
            if _stat_fingerprint(os.fstat(descriptor)) != _stat_fingerprint(opened):
                raise _WorkspaceSnapshotError(display_path)
        finally:
            os.close(descriptor)
        return _WorkspaceFileState(content, mode)
    if stat.S_ISLNK(before.st_mode):
        try:
            target = os.readlink(name, dir_fd=parent_descriptor)
            after = os.lstat(name, dir_fd=parent_descriptor)
        except FileNotFoundError as error:
            raise _WorkspaceSnapshotError(display_path) from error
        if _stat_fingerprint(after) != _stat_fingerprint(before):
            raise _WorkspaceSnapshotError(display_path)
        return _WorkspaceFileState(os.fsencode(target), mode, symlink=True)
    msg = f"Update workspace path is not a regular file or symlink: {display_path}"
    raise UpdateWorkspaceError(msg)


def _snapshot_relative_path(
    root_descriptor: int,
    path: Path,
    *,
    display_root: Path,
) -> _WorkspacePathState:
    try:
        with _open_parent_descriptor(root_descriptor, path) as parent_descriptor:
            return _snapshot_leaf(
                parent_descriptor,
                path.name,
                display_path=display_root / path,
            )
    except FileNotFoundError:
        return None
    except NotADirectoryError as error:
        msg = f"Update workspace path traverses a non-directory: {display_root / path}"
        raise UpdateWorkspaceError(msg) from error


def _install_workspace_state(path: Path, state: _WorkspacePathState) -> None:
    """Materialize one validated state in the disposable tree."""
    if state is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if state.symlink:
        path.symlink_to(os.fsdecode(state.content))
    else:
        path.write_bytes(state.content)
        path.chmod(state.mode)


def _normalize_workspace_paths(paths: Iterable[str | Path]) -> tuple[Path, ...]:
    normalized: set[Path] = set()
    for raw_path in paths:
        path = Path(raw_path)
        if (
            path.is_absolute()
            or path == Path()
            or ".." in path.parts
            or (path.parts and path.parts[0] == ".git")
        ):
            msg = f"Update workspace path must be repository-relative: {raw_path}"
            raise ValueError(msg)
        normalized.add(path)
    return tuple(sorted(normalized))


def _read_source_view(
    root: Path,
    root_descriptor: int,
) -> dict[Path, _WorkspacePathState]:
    paths = _git_paths(
        root,
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
    )
    return {
        path: _snapshot_relative_path(root_descriptor, path, display_root=root)
        for path in paths
    }


def _snapshot_source_view(
    root: Path,
    root_descriptor: int,
) -> dict[Path, _WorkspacePathState]:
    """Capture two identical source views or reject a moving working tree."""
    for _attempt in range(_SNAPSHOT_ATTEMPTS):
        try:
            first = _read_source_view(root, root_descriptor)
            second = _read_source_view(root, root_descriptor)
        except _WorkspaceSnapshotError:
            continue
        if first == second:
            return second
    msg = f"Update source changed while creating a stable snapshot: {root}"
    raise UpdateWorkspaceError(msg)


def _restore_process_context(cwd: Path, repo_root: str | None) -> None:
    """Restore process-global repository discovery after isolated execution."""
    try:
        os.chdir(cwd)
    finally:
        try:
            if repo_root is None:
                os.environ.pop("REPO_ROOT", None)
            else:
                os.environ["REPO_ROOT"] = repo_root
        finally:
            try:
                update_paths._clear_root_cache()  # noqa: SLF001 -- root switch
            finally:
                update_flake.invalidate_flake_lock()


def _validate_workspace_symlink(
    root: Path,
    path: Path,
    state: _WorkspacePathState,
) -> None:
    """Reject links that could make isolated writes escape the repository."""
    if state is None or not state.symlink:
        return
    target = Path(os.fsdecode(state.content))
    if target.is_absolute():
        msg = f"Update workspace symlink must be repository-relative: {path}"
        raise UpdateWorkspaceError(msg)
    try:
        (root / path.parent / target).resolve().relative_to(root)
    except ValueError as error:
        msg = f"Update workspace symlink escapes repository: {path}"
        raise UpdateWorkspaceError(msg) from error


def _source_conflicts(
    root: Path,
    descriptor: int,
    expected: dict[Path, _WorkspacePathState],
    *,
    ignored_paths: set[Path] | None = None,
) -> tuple[Path, ...]:
    """Return every tracked or visible source path that differs from expected."""
    ignored = set() if ignored_paths is None else ignored_paths
    current_paths = set(
        _git_paths(
            root,
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
        )
    )
    conflicts: list[Path] = []
    for path in sorted((expected.keys() | current_paths) - ignored):
        try:
            current = (
                _snapshot_relative_path(descriptor, path, display_root=root)
                if path in current_paths
                else None
            )
        except (OSError, UpdateWorkspaceError, _WorkspaceSnapshotError):
            conflicts.append(path)
            continue
        if current != expected.get(path):
            conflicts.append(path)
    return tuple(conflicts)


def _acquire_process_workspace_lock() -> None:
    """Serialize process-global cwd and repository-root switching."""
    if not _PROCESS_WORKSPACE_LOCK.acquire(blocking=False):
        msg = "Another isolated update is already running in this process"
        raise UpdateWorkspaceError(msg)


if sys.platform == "darwin":  # pragma: no branch -- platform selected at import
    _RENAME_FLAGS = ("renameatx_np", 0x00000004, 0x00000002)
elif sys.platform.startswith("linux"):  # pragma: no cover -- exercised by Linux CI
    _RENAME_FLAGS = ("renameat2", 1, 2)
else:  # pragma: no cover -- only Darwin and Linux are supported
    _RENAME_FLAGS = None


def _rename_with_flag(
    source: str,
    destination: str,
    *,
    source_descriptor: int,
    destination_descriptor: int,
    flag_index: int,
) -> None:
    """Rename leaves with one supported atomic platform flag."""
    if _RENAME_FLAGS is None:  # pragma: no cover -- unsupported platform guard
        msg = "Atomic update promotion is unsupported"
        raise UpdateWorkspaceError(msg)
    function_name, *flags = _RENAME_FLAGS
    try:
        rename = getattr(ctypes.CDLL(None, use_errno=True), function_name)
    except AttributeError as error:  # pragma: no cover -- supported libc contract
        msg = "Atomic flagged rename is unavailable"
        raise UpdateWorkspaceError(msg) from error
    result = rename(
        source_descriptor,
        os.fsencode(source),
        destination_descriptor,
        os.fsencode(destination),
        flags[flag_index],
    )
    if result:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), destination)


def _rename_no_replace(
    source: str,
    destination: str,
    *,
    source_descriptor: int,
    destination_descriptor: int,
) -> None:
    """Atomically move one leaf without replacing any destination entry."""
    _rename_with_flag(
        source,
        destination,
        source_descriptor=source_descriptor,
        destination_descriptor=destination_descriptor,
        flag_index=0,
    )


def _rename_exchange(
    source: str,
    destination: str,
    *,
    descriptor: int,
) -> None:
    """Atomically exchange two leaves in one directory."""
    _rename_with_flag(
        source,
        destination,
        source_descriptor=descriptor,
        destination_descriptor=descriptor,
        flag_index=1,
    )


@dataclass(frozen=True, slots=True)
class _StateFingerprint:
    kind: str
    mode: int
    sha256: str


type _OptionalFingerprint = _StateFingerprint | None
_INVALID_FINGERPRINT = _StateFingerprint(kind="invalid", mode=-1, sha256="")


@dataclass(slots=True)
class _PromotedPath:
    path: Path
    parent_descriptor: int
    retained: str


@dataclass(frozen=True, slots=True)
class _TransactionPath:
    path: Path
    retained: str
    original: _OptionalFingerprint
    produced: _OptionalFingerprint


@dataclass(frozen=True, slots=True)
class _Transaction:
    root: Path
    committed: bool
    paths: tuple[_TransactionPath, ...]


def _state_fingerprint(state: _WorkspacePathState) -> _OptionalFingerprint:
    if state is None:
        return None
    return _StateFingerprint(
        kind="symlink" if state.symlink else "file",
        mode=state.mode,
        sha256=hashlib.sha256(state.content).hexdigest(),
    )


def _fingerprint_leaf(
    descriptor: int,
    leaf: str,
    *,
    path: Path,
) -> _OptionalFingerprint:
    try:
        state = _snapshot_leaf(descriptor, leaf, display_path=path)
    except (UpdateWorkspaceError, _WorkspaceSnapshotError):
        return _INVALID_FINGERPRINT
    return _state_fingerprint(state)


def _transaction_path_to_json(record: _TransactionPath) -> dict[str, object]:
    def _fingerprint(value: _OptionalFingerprint) -> dict[str, object] | None:
        if value is None:
            return None
        return {"kind": value.kind, "mode": value.mode, "sha256": value.sha256}

    return {
        "path": os.fspath(record.path),
        "retained": record.retained,
        "original": _fingerprint(record.original),
        "produced": _fingerprint(record.produced),
    }


def _transaction_to_json(transaction: _Transaction) -> bytes:
    return json.dumps(
        {
            "version": 1,
            "root": os.fspath(transaction.root),
            "committed": transaction.committed,
            "paths": [
                _transaction_path_to_json(record) for record in transaction.paths
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _journal_error() -> ValueError:
    return ValueError("invalid update transaction journal")


def _parse_fingerprint(value: object) -> _OptionalFingerprint:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _journal_error()
    fingerprint = cast("dict[str, object]", value)
    kind = fingerprint.get("kind")
    mode = fingerprint.get("mode")
    digest = fingerprint.get("sha256")
    if (
        not isinstance(kind, str)
        or kind not in {"file", "symlink"}
        or not isinstance(mode, int)
    ):
        raise _journal_error()
    if not isinstance(digest, str) or len(digest) != _SHA256_HEX_LENGTH:
        raise _journal_error()
    return _StateFingerprint(kind=kind, mode=mode, sha256=digest)


def _parse_transaction(data: bytes, expected_root: Path) -> _Transaction:
    try:
        value = json.loads(data)
        if not isinstance(value, dict) or value.get("version") != 1:
            raise _journal_error()
        root_value = value.get("root")
        committed = value.get("committed")
        raw_paths = value.get("paths")
        if (
            not isinstance(root_value, str)
            or Path(root_value) != expected_root
            or not isinstance(committed, bool)
            or not isinstance(raw_paths, list)
        ):
            raise _journal_error()
        records: list[_TransactionPath] = []
        canonical_paths: set[Path] = set()
        retained_paths: set[Path] = set()
        for raw_record in raw_paths:
            if not isinstance(raw_record, dict):
                raise _journal_error()
            path_value = raw_record.get("path")
            retained = raw_record.get("retained")
            if not isinstance(path_value, str) or not isinstance(retained, str):
                raise _journal_error()
            path = _normalize_workspace_paths((path_value,))[0]
            if Path(retained).name != retained or not retained.startswith(
                f".{path.name}.nixcfg-transaction-"
            ):
                raise _journal_error()
            retained_path = path.with_name(retained)
            if path in canonical_paths or retained_path in retained_paths:
                raise _journal_error()
            canonical_paths.add(path)
            retained_paths.add(retained_path)
            records.append(
                _TransactionPath(
                    path=path,
                    retained=retained,
                    original=_parse_fingerprint(raw_record.get("original")),
                    produced=_parse_fingerprint(raw_record.get("produced")),
                )
            )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        msg = f"Invalid update transaction journal: {expected_root}"
        raise UpdateWorkspaceError(msg) from error
    return _Transaction(expected_root, committed, tuple(records))


def _fsync_directory_descriptor(descriptor: int) -> None:
    """Make preceding namespace mutations durable."""
    os.fsync(descriptor)


def _write_transaction(journal: Path, transaction: _Transaction) -> None:
    """Atomically persist one transaction state and its directory entry."""
    temporary = journal.with_name(
        f".{journal.name}.tmp-{secrets.token_hex(12)}",
    )
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        try:
            remaining = memoryview(_transaction_to_json(transaction))
            while remaining:
                remaining = remaining[os.write(descriptor, remaining) :]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        temporary.replace(journal)
        directory = os.open(journal.parent, _OPEN_DIRECTORY_FLAGS)
        try:
            _fsync_directory_descriptor(directory)
        finally:
            os.close(directory)
    except BaseException:
        with suppress(FileNotFoundError):
            temporary.unlink()
        raise


def _remove_transaction(journal: Path) -> None:
    journal.unlink()
    directory = os.open(journal.parent, _OPEN_DIRECTORY_FLAGS)
    try:
        _fsync_directory_descriptor(directory)
    finally:
        os.close(directory)


def _read_transaction(journal: Path, root: Path) -> _Transaction | None:
    try:
        descriptor = os.open(journal, _OPEN_FILE_FLAGS)
    except FileNotFoundError:
        return None
    except OSError as error:
        msg = f"Invalid update transaction journal: {journal}"
        raise UpdateWorkspaceError(msg) from error
    try:
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            data = stream.read()
    finally:
        os.close(descriptor)
    return _parse_transaction(data, root)


def _transaction_leaf_fingerprints(
    root_descriptor: int,
    record: _TransactionPath,
) -> tuple[_OptionalFingerprint, _OptionalFingerprint, int | None]:
    try:
        context = _open_parent_descriptor(root_descriptor, record.path)
        with context as opened_parent:
            parent_descriptor = os.dup(opened_parent)
    except (OSError, UpdateWorkspaceError):
        return _INVALID_FINGERPRINT, None, None
    return (
        _fingerprint_leaf(
            parent_descriptor,
            record.path.name,
            path=record.path,
        ),
        _fingerprint_leaf(
            parent_descriptor,
            record.retained,
            path=record.path,
        ),
        parent_descriptor,
    )


def _swap_back_produced(
    descriptor: int,
    record: _TransactionPath,
    retained: _OptionalFingerprint,
) -> bool:
    if record.produced is None:
        if retained is None:
            return False
        _rename_no_replace(
            record.retained,
            record.path.name,
            source_descriptor=descriptor,
            destination_descriptor=descriptor,
        )
        _fsync_directory_descriptor(descriptor)
        return False
    if record.original is None and retained is None:
        _rename_no_replace(
            record.path.name,
            record.retained,
            source_descriptor=descriptor,
            destination_descriptor=descriptor,
        )
    elif retained == record.original:
        _rename_exchange(
            record.path.name,
            record.retained,
            descriptor=descriptor,
        )
    elif retained is not None:
        _rename_exchange(
            record.path.name,
            record.retained,
            descriptor=descriptor,
        )
        _fsync_directory_descriptor(descriptor)
        return False
    else:
        return False
    _fsync_directory_descriptor(descriptor)
    restored = (
        _fingerprint_leaf(
            descriptor,
            record.path.name,
            path=record.path,
        )
        == record.original
    )
    candidate_intact = (
        _fingerprint_leaf(
            descriptor,
            record.retained,
            path=record.path,
        )
        == record.produced
    )
    if restored and candidate_intact:
        os.unlink(record.retained, dir_fd=descriptor)
    return restored and candidate_intact


def _rollback_transaction_path(
    root_descriptor: int,
    record: _TransactionPath,
) -> bool:
    """Restore one uncommitted path, preserving every ambiguous leaf."""
    canonical, retained, descriptor = _transaction_leaf_fingerprints(
        root_descriptor,
        record,
    )
    if descriptor is None:
        return False
    try:
        restored = True
        if canonical == record.original:
            if retained is not None:
                restored = retained == record.produced
                if restored:
                    os.unlink(record.retained, dir_fd=descriptor)
        elif (
            record.original is not None
            and canonical is None
            and retained == record.original
        ):
            _rename_no_replace(
                record.retained,
                record.path.name,
                source_descriptor=descriptor,
                destination_descriptor=descriptor,
            )
            restored = (
                _fingerprint_leaf(
                    descriptor,
                    record.path.name,
                    path=record.path,
                )
                == record.original
            )
        elif canonical == record.produced:
            restored = _swap_back_produced(descriptor, record, retained)
        else:
            restored = False
        _fsync_directory_descriptor(descriptor)
        return restored
    finally:
        os.close(descriptor)


def _cleanup_committed_path(
    root_descriptor: int,
    record: _TransactionPath,
) -> bool:
    """Remove an exact retained original after a durable commit marker."""
    canonical, retained, descriptor = _transaction_leaf_fingerprints(
        root_descriptor,
        record,
    )
    if descriptor is None:
        return False
    try:
        if canonical != record.produced:
            return False
        if retained is None:
            return True
        if retained != record.original:
            return False
        os.unlink(record.retained, dir_fd=descriptor)
        _fsync_directory_descriptor(descriptor)
    except OSError:
        return False
    else:
        return True
    finally:
        os.close(descriptor)


def _recover_transaction(
    journal: Path,
    root: Path,
    root_descriptor: int,
) -> None:
    """Complete or roll back one journal after the repository lock is held."""
    transaction = _read_transaction(journal, root)
    if transaction is None:
        return
    try:
        conflicts = tuple(
            record.path
            for record in reversed(transaction.paths)
            if not (
                _cleanup_committed_path(root_descriptor, record)
                if transaction.committed
                else _rollback_transaction_path(root_descriptor, record)
            )
        )
    except OSError as error:
        msg = f"Update transaction recovery was not durable: {root}"
        raise UpdateWorkspaceError(msg) from error
    if conflicts:
        raise UpdateWorkspaceConflictError(tuple(sorted(conflicts)))
    try:
        _remove_transaction(journal)
    except OSError as error:
        msg = f"Update transaction recovery cleanup failed: {root}"
        raise UpdateWorkspaceError(msg) from error


def _create_candidate(
    parent_descriptor: int,
    state: _WorkspaceFileState,
    *,
    name: str,
) -> None:
    descriptor = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        state.mode,
        dir_fd=parent_descriptor,
    )
    try:
        try:
            remaining = memoryview(state.content)
            while remaining:
                remaining = remaining[os.write(descriptor, remaining) :]
            os.fchmod(descriptor, state.mode)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(name, dir_fd=parent_descriptor)
        raise


def _prepare_retained(
    parent_descriptor: int,
    state: _WorkspaceFileState,
    retained: str,
) -> None:
    """Create one journaled produced leaf without touching the canonical name."""
    if state.symlink:
        os.symlink(os.fsdecode(state.content), retained, dir_fd=parent_descriptor)
        return
    _create_candidate(parent_descriptor, state, name=retained)


def _promote_path(
    root_descriptor: int,
    path: Path,
    original: _WorkspacePathState,
    produced: _WorkspacePathState,
    *,
    retained: str,
) -> _PromotedPath:
    """Apply one journaled leaf change through exchange or no-replace rename."""
    try:
        context = _open_parent_descriptor(
            root_descriptor,
            path,
        )
        with context as opened_parent:
            parent_descriptor = os.dup(opened_parent)
    except OSError as error:
        raise UpdateWorkspaceConflictError((path,)) from error
    succeeded = False
    try:
        if _snapshot_leaf(parent_descriptor, path.name, display_path=path) != original:
            raise UpdateWorkspaceConflictError((path,))  # noqa: TRY301 -- rollback
        expected_retained = produced if produced is not None else None
        if (
            _snapshot_leaf(parent_descriptor, retained, display_path=path)
            != expected_retained
        ):
            raise UpdateWorkspaceConflictError((path,))  # noqa: TRY301 -- rollback
        if original is None:
            _rename_no_replace(
                retained,
                path.name,
                source_descriptor=parent_descriptor,
                destination_descriptor=parent_descriptor,
            )
        elif produced is None:
            _rename_no_replace(
                path.name,
                retained,
                source_descriptor=parent_descriptor,
                destination_descriptor=parent_descriptor,
            )
        else:
            _rename_exchange(path.name, retained, descriptor=parent_descriptor)
        _fsync_directory_descriptor(parent_descriptor)
        if (
            _snapshot_leaf(parent_descriptor, path.name, display_path=path) != produced
            or _snapshot_leaf(parent_descriptor, retained, display_path=path)
            != original
        ):
            raise UpdateWorkspaceConflictError((path,))  # noqa: TRY301 -- recovery
        succeeded = True
    except BaseException as error:
        if isinstance(error, OSError) and error.errno == errno.EEXIST:
            raise UpdateWorkspaceConflictError((path,)) from error
        raise
    finally:
        if not succeeded:
            os.close(parent_descriptor)
    return _PromotedPath(path, parent_descriptor, retained)


class IsolatedUpdateWorkspace:
    """Run an update in a disposable Git tree and promote declared outputs."""

    def __init__(self, repo_root: Path | None = None) -> None:
        """Capture the live root used for copying and eventual promotion."""
        self._live_root = (
            get_repo_root() if repo_root is None else repo_root.expanduser().resolve()
        )
        self._root: Path | None = None
        self._start: dict[Path, _WorkspacePathState] = {}
        self._committed = False
        self._resources: ExitStack | None = None
        self._live_descriptor: int | None = None
        self._workspace_descriptor: int | None = None
        self._journal: Path | None = None

    @property
    def root(self) -> Path:
        """Return the active disposable repository root."""
        if self._root is None:
            msg = "Update workspace is not active"
            raise RuntimeError(msg)
        return self._root

    def __enter__(self) -> Self:
        """Copy the current source view, establish a baseline, and activate it."""
        if self._resources is not None:
            msg = "Update workspace cannot be entered more than once"
            raise RuntimeError(msg)
        cleanup = ExitStack()
        try:
            _acquire_process_workspace_lock()
            cleanup.callback(_PROCESS_WORKSPACE_LOCK.release)
            common_dir = _git_common_dir(self._live_root)
            lock = FileLock(common_dir / "nixcfg-update.lock")
            try:
                lock.acquire(timeout=0)
            except Timeout as error:
                msg = (
                    f"Another isolated update is already running for {self._live_root}"
                )
                raise UpdateWorkspaceError(msg) from error
            cleanup.callback(lock.release)
            live_descriptor = os.open(self._live_root, _OPEN_DIRECTORY_FLAGS)
            cleanup.callback(os.close, live_descriptor)
            journal = common_dir / _TRANSACTION_JOURNAL
            _recover_transaction(journal, self._live_root, live_descriptor)
            temporary = tempfile.TemporaryDirectory(prefix="nixcfg-update-")
            cleanup.callback(temporary.cleanup)
            root = Path(temporary.name, "repo").resolve()
            root.mkdir()
            workspace_descriptor = os.open(root, _OPEN_DIRECTORY_FLAGS)
            cleanup.callback(os.close, workspace_descriptor)
            self._start = _snapshot_source_view(self._live_root, live_descriptor)
            for path, state in self._start.items():
                _validate_workspace_symlink(self._live_root, path, state)
                _install_workspace_state(root / path, state)
            _run_git(root, "init", "--quiet")
            _run_git(root, "add", "--all", "--force")
            _run_git(
                root,
                "-c",
                "user.name=nixcfg",
                "-c",
                "user.email=nixcfg@localhost",
                "-c",
                "commit.gpgSign=false",
                "commit",
                "--quiet",
                "--allow-empty",
                "-m",
                "update workspace baseline",
            )
            old_cwd = Path.cwd()
            old_repo_root = os.environ.get("REPO_ROOT")
            cleanup.callback(_restore_process_context, old_cwd, old_repo_root)
            os.environ["REPO_ROOT"] = os.fspath(root)
            os.chdir(root)
            update_paths._clear_root_cache()  # noqa: SLF001 -- process-global root switch
            update_flake.invalidate_flake_lock()
        except BaseException:
            cleanup.close()
            raise
        self._root = root
        self._live_descriptor = live_descriptor
        self._workspace_descriptor = workspace_descriptor
        self._journal = journal
        self._resources = cleanup
        return self

    def _workspace_changes(self) -> dict[Path, _WorkspacePathState]:
        root = self.root
        descriptor = cast("int", self._workspace_descriptor)
        current = _snapshot_source_view(root, descriptor)
        return {
            path: current.get(path)
            for path in sorted(self._start.keys() | current.keys())
            if current.get(path) != self._start.get(path)
        }

    def changed_paths(self) -> tuple[Path, ...]:
        """Return changed tracked and untracked, non-ignored relative paths."""
        return tuple(self._workspace_changes())

    def _validated_changes(
        self,
        allowed_paths: Iterable[str | Path],
    ) -> tuple[tuple[Path, ...], dict[Path, _WorkspacePathState]]:
        if self._committed:
            msg = "Update workspace has already been committed"
            raise RuntimeError(msg)
        allowed = set(_normalize_workspace_paths(allowed_paths))
        produced = self._workspace_changes()
        changed = tuple(produced)
        if unexpected := tuple(path for path in changed if path not in allowed):
            raise UpdateWorkspaceUnexpectedPathsError(unexpected)

        for path, state in produced.items():
            _validate_workspace_symlink(self.root, path, state)
        return changed, produced

    def validate_changes(
        self,
        allowed_paths: Iterable[str | Path],
    ) -> tuple[Path, ...]:
        """Validate the declared output boundary without promoting changes."""
        changed, _produced = self._validated_changes(allowed_paths)
        return changed

    def promote(self, allowed_paths: Iterable[str | Path]) -> tuple[Path, ...]:
        """Promote only declared changes after conflict checks and verification."""
        changed, produced = self._validated_changes(allowed_paths)
        if not changed:
            self._committed = True
            return ()
        live_descriptor = cast("int", self._live_descriptor)
        journal = cast("Path", self._journal)
        conflicts = _source_conflicts(
            self._live_root,
            live_descriptor,
            self._start,
        )
        if conflicts:
            raise UpdateWorkspaceConflictError(conflicts)

        transaction = _Transaction(
            root=self._live_root,
            committed=False,
            paths=tuple(
                _TransactionPath(
                    path=path,
                    retained=(
                        f".{path.name}.nixcfg-transaction-{secrets.token_hex(12)}"
                    ),
                    original=_state_fingerprint(self._start.get(path)),
                    produced=_state_fingerprint(produced[path]),
                )
                for path in changed
            ),
        )
        promoted: list[_PromotedPath] = []
        for record in transaction.paths:
            try:
                with _open_parent_descriptor(live_descriptor, record.path):
                    pass
            except OSError as error:
                raise UpdateWorkspaceConflictError((record.path,)) from error
        try:
            _write_transaction(journal, transaction)
            for record in transaction.paths:
                state = produced[record.path]
                if state is None:
                    continue
                with _open_parent_descriptor(
                    live_descriptor,
                    record.path,
                ) as parent_descriptor:
                    _prepare_retained(
                        parent_descriptor,
                        state,
                        record.retained,
                    )
                    _fsync_directory_descriptor(parent_descriptor)
            promoted.extend(
                (
                    _promote_path(
                        live_descriptor,
                        record.path,
                        self._start.get(record.path),
                        produced[record.path],
                        retained=record.retained,
                    )
                )
                for record in transaction.paths
            )
            expected_live = {**self._start, **produced}
            ignored_paths = {
                record.path.with_name(record.retained) for record in transaction.paths
            }
            if conflicts := _source_conflicts(
                self._live_root,
                live_descriptor,
                expected_live,
                ignored_paths=ignored_paths,
            ):
                raise UpdateWorkspaceConflictError(  # noqa: TRY301 -- rollback path
                    conflicts
                )
            for promoted_record, transaction_record in zip(
                promoted,
                transaction.paths,
                strict=True,
            ):
                if (
                    _state_fingerprint(
                        _snapshot_leaf(
                            promoted_record.parent_descriptor,
                            promoted_record.path.name,
                            display_path=promoted_record.path,
                        )
                    )
                    != transaction_record.produced
                    or _state_fingerprint(
                        _snapshot_leaf(
                            promoted_record.parent_descriptor,
                            promoted_record.retained,
                            display_path=promoted_record.path,
                        )
                    )
                    != transaction_record.original
                ):
                    raise UpdateWorkspaceConflictError(  # noqa: TRY301 -- rollback
                        (promoted_record.path,)
                    )
            committed = _Transaction(
                root=transaction.root,
                committed=True,
                paths=transaction.paths,
            )
            _write_transaction(journal, committed)
            self._committed = True
            cleanup_complete = all(
                _cleanup_committed_path(live_descriptor, record)
                for record in transaction.paths
            )
            if cleanup_complete:
                with suppress(OSError):
                    _remove_transaction(journal)
        except BaseException as error:
            try:
                _recover_transaction(journal, self._live_root, live_descriptor)
            except UpdateWorkspaceConflictError as recovery_error:
                original_conflicts = (
                    error.paths
                    if isinstance(error, UpdateWorkspaceConflictError)
                    else ()
                )
                raise UpdateWorkspaceConflictError(
                    tuple(sorted(set(original_conflicts) | set(recovery_error.paths)))
                ) from error
            raise
        finally:
            for record in promoted:
                os.close(record.parent_descriptor)
        return changed

    def __exit__(self, *_exc_info: object) -> None:
        """Restore process context and remove the disposable repository."""
        resources = self._resources
        if resources is None:  # pragma: no cover -- context protocol guarantees entry
            return
        try:
            resources.close()
        finally:
            self._root = None
            self._live_descriptor = None
            self._workspace_descriptor = None
            self._journal = None
            self._resources = None


def _sidecar_owner_dir(path: Path, *, filename: str) -> Path | None:
    """Return the package directory for a directory-layout sidecar."""
    return path.parent if path.name == filename else None


def planned_update_paths(
    source_names: list[str],
    updaters: Mapping[str, UpdaterClass],
) -> tuple[Path, ...]:
    """Return the complete write set that must be captured before phases run."""
    root = get_repo_root()
    source_paths = package_file_map_in(root, "sources.json")
    updater_paths = package_file_map_in(root, "updater.py")
    paths: set[Path] = set()
    for name in source_names:
        source_path = source_paths.get(name)
        updater_path = updater_paths.get(name)
        if source_path is None:
            if updater_path is not None:
                source_path = sources_file_for_updater(updater_path, name)
            elif package_dir := package_dir_for_in(root, name):
                source_path = package_dir / "sources.json"
        if source_path is None:
            msg = f"No source sidecar owner found for: {name}"
            raise RuntimeError(msg)
        paths.add(update_artifacts.resolve_repo_path(source_path, repo_root=root))
        updater = updaters.get(name)
        artifact_files = (
            updater.get_generated_artifact_files() if updater is not None else ()
        )
        if artifact_files and updater_path is None:
            msg = f"No updater sidecar owner found for generated artifacts: {name}"
            raise RuntimeError(msg)
        artifact_owner = (
            _sidecar_owner_dir(updater_path, filename="updater.py")
            if updater_path is not None
            else None
        )
        if artifact_files and artifact_owner is None:
            msg = (
                f"Flat updater sidecar cannot own relative generated artifacts: {name}"
            )
            raise RuntimeError(msg)
        if artifact_owner is not None:
            paths.update(
                update_artifacts.resolve_repo_path(
                    artifact_owner / relative,
                    repo_root=root,
                )
                for relative in artifact_files
            )
        if target := update_crate2nix.TARGETS.get(name):
            paths.update(
                update_artifacts.resolve_repo_path(path, repo_root=root)
                for path in (target.cargo_nix, target.crate_hashes)
            )
    return tuple(sorted(paths))


def merge_source_updates(
    existing_entries: dict[str, SourceEntry],
    source_updates: dict[str, SourceEntry],
    *,
    native_only: bool,
) -> dict[str, SourceEntry]:
    """Merge source updates into existing entries for native-only runs."""
    if not native_only:
        return source_updates
    return {
        name: existing_entries[name].merge(entry) if name in existing_entries else entry
        for name, entry in source_updates.items()
    }


def flatten_artifact_updates(
    artifact_updates: dict[str, tuple[GeneratedArtifact, ...]],
) -> list[GeneratedArtifact]:
    """Flatten per-source generated artifact updates into one list."""
    return [
        artifact
        for source in sorted(artifact_updates)
        for artifact in artifact_updates[source]
    ]


def persist_generated_artifacts(
    *,
    do_sources: bool,
    source_names: list[str],
    dry_run: bool,
    artifact_updates: dict[str, tuple[GeneratedArtifact, ...]],
    details: dict[str, SummaryStatus],
) -> tuple[Path, ...]:
    """Persist generated artifacts emitted by successful source updaters."""
    if not (do_sources and source_names):
        return ()
    if dry_run or not artifact_updates:
        return ()
    completed_updates = {
        source: artifacts
        for source, artifacts in artifact_updates.items()
        if details.get(source) in {"updated", "no_change"}
    }
    successful_updates = {
        source: artifacts
        for source, artifacts in completed_updates.items()
        if details.get(source) == "updated"
    }
    if not successful_updates:
        return ()
    # A no-change producer can still disagree with a changed producer for the
    # same path. Validate every completed producer before dropping baselines.
    update_artifacts.dedupe_generated_artifacts(
        flatten_artifact_updates(completed_updates)
    )
    successful_artifacts = flatten_artifact_updates(successful_updates)
    update_artifacts.save_generated_artifacts(successful_artifacts)
    root = get_repo_root()
    return tuple(
        artifact.resolved_path(repo_root=root) for artifact in successful_artifacts
    )


def persist_source_updates(
    *,
    do_sources: bool,
    source_names: list[str],
    dry_run: bool,
    native_only: bool,
    sources: SourcesFile,
    source_updates: dict[str, SourceEntry],
    details: dict[str, SummaryStatus],
) -> tuple[Path, ...]:
    """Persist per-package sources.json updates from one update run."""
    if not (do_sources and source_names):
        return ()

    selected_names = set(source_names)
    successful_updates = {
        name: entry
        for name, entry in source_updates.items()
        if name in selected_names and details.get(name) == "updated"
    }
    if not successful_updates:
        return ()

    if native_only and not dry_run:
        merged_updates = update_sources.save_source_updates(
            successful_updates,
            merge_existing=True,
        )
    else:
        merged_updates = merge_source_updates(
            sources.entries,
            successful_updates,
            native_only=native_only,
        )

    if not dry_run and not native_only:
        update_sources.save_sources(SourcesFile(entries=merged_updates))
    sources.entries.update(merged_updates)
    if dry_run:
        return ()
    root = get_repo_root()
    path_map = package_file_map_in(root, "sources.json")
    return tuple(
        update_artifacts.resolve_repo_path(path_map[name], repo_root=root)
        for name in sorted(successful_updates)
        if name in path_map
    )


def persist_materialized_updates(
    *,
    do_sources: bool,
    source_names: list[str],
    dry_run: bool,
    native_only: bool,
    sources: SourcesFile,
    source_updates: dict[str, SourceEntry],
    artifact_updates: dict[str, tuple[GeneratedArtifact, ...]],
    details: dict[str, SummaryStatus],
) -> tuple[Path, ...]:
    """Persist generated artifacts first, then per-package sources."""
    artifact_paths = persist_generated_artifacts(
        do_sources=do_sources,
        source_names=source_names,
        dry_run=dry_run,
        artifact_updates=artifact_updates,
        details=details,
    )
    source_paths = persist_source_updates(
        do_sources=do_sources,
        source_names=source_names,
        dry_run=dry_run,
        native_only=native_only,
        sources=sources,
        source_updates=source_updates,
        details=details,
    )
    return (*artifact_paths, *source_paths)


__all__ = [
    "IsolatedUpdateWorkspace",
    "UpdateWorkspaceConflictError",
    "UpdateWorkspaceError",
    "UpdateWorkspaceUnexpectedPathsError",
    "flatten_artifact_updates",
    "merge_source_updates",
    "persist_generated_artifacts",
    "persist_materialized_updates",
    "persist_source_updates",
    "planned_update_paths",
]
