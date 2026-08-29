"""Crash-recovery contracts for isolated update promotion."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from lib.update import persistence as persistence_module
from lib.update.persistence import (
    IsolatedUpdateWorkspace,
    UpdateWorkspaceConflictError,
    UpdateWorkspaceError,
)


def _init_repo(root: Path) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("Git is required")
    root.mkdir()
    (root / ".root").write_text("\n", encoding="utf-8")
    (root / "a.txt").write_text("a-original\n", encoding="utf-8")
    (root / "b.txt").write_text("b-original\n", encoding="utf-8")
    subprocess.run([git, "init", "--quiet"], cwd=root, check=True)  # noqa: S603
    subprocess.run([git, "add", "--all"], cwd=root, check=True)  # noqa: S603
    subprocess.run(  # noqa: S603
        [
            git,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgSign=false",
            "commit",
            "--quiet",
            "-m",
            "baseline",
        ],
        cwd=root,
        check=True,
    )


def _fingerprint(
    content: bytes | None,
    *,
    symlink: bool = False,
) -> persistence_module._StateFingerprint | None:
    if content is None:
        return None
    return persistence_module._state_fingerprint(
        persistence_module._WorkspaceFileState(
            content,
            (0o755 if sys.platform == "darwin" else 0o777) if symlink else 0o644,
            symlink,
        ),
    )


def _record(
    path: str,
    *,
    original: bytes | None,
    produced: bytes | None,
    original_symlink: bool = False,
    produced_symlink: bool = False,
) -> persistence_module._TransactionPath:
    leaf = Path(path).name
    return persistence_module._TransactionPath(
        path=Path(path),
        retained=f".{leaf}.nixcfg-transaction-test",
        original=_fingerprint(original, symlink=original_symlink),
        produced=_fingerprint(produced, symlink=produced_symlink),
    )


def _write_journal(
    root: Path,
    records: list[persistence_module._TransactionPath],
    *,
    committed: bool,
) -> Path:
    journal = root / ".git" / persistence_module._TRANSACTION_JOURNAL
    persistence_module._write_transaction(
        journal,
        persistence_module._Transaction(root.resolve(), committed, tuple(records)),
    )
    return journal


def _retained(root: Path, record: persistence_module._TransactionPath) -> Path:
    return root / record.path.parent / record.retained


def _valid_journal_envelope(root: Path) -> tuple[dict[str, object], dict[str, object]]:
    transaction = persistence_module._Transaction(
        root=root.resolve(),
        committed=False,
        paths=(_record("a.txt", original=b"a-original\n", produced=b"a-update\n"),),
    )
    envelope = cast(
        "dict[str, object]",
        json.loads(persistence_module._transaction_to_json(transaction)),
    )
    record = cast("dict[str, object]", cast("list[object]", envelope["paths"])[0])
    return envelope, record


def test_startup_rolls_back_precommit_replacement(
    tmp_path: Path,
) -> None:
    """An uncommitted exchanged leaf is restored before a new run starts."""
    live = tmp_path / "live"
    _init_repo(live)
    record = _record("a.txt", original=b"a-original\n", produced=b"a-update\n")
    (live / "a.txt").write_bytes(b"a-update\n")
    _retained(live, record).write_bytes(b"a-original\n")
    journal = _write_journal(live, [record], committed=False)

    with IsolatedUpdateWorkspace(live):
        assert (live / "a.txt").read_bytes() == b"a-original\n"

    assert not _retained(live, record).exists()
    assert not journal.exists()


def test_startup_finishes_postcommit_cleanup(
    tmp_path: Path,
) -> None:
    """A durable commit marker makes startup retain output and clean originals."""
    live = tmp_path / "live"
    _init_repo(live)
    record = _record("a.txt", original=b"a-original\n", produced=b"a-update\n")
    (live / "a.txt").write_bytes(b"a-update\n")
    _retained(live, record).write_bytes(b"a-original\n")
    journal = _write_journal(live, [record], committed=True)

    with IsolatedUpdateWorkspace(live):
        assert (live / "a.txt").read_bytes() == b"a-update\n"

    assert not _retained(live, record).exists()
    assert not journal.exists()


def test_workspace_disables_background_git_maintenance(tmp_path: Path) -> None:
    """Disposable repositories must not outlive cleanup via detached maintenance."""
    live = tmp_path / "live"
    _init_repo(live)
    git = shutil.which("git")
    assert git is not None

    with IsolatedUpdateWorkspace(live) as workspace:
        config = subprocess.run(  # noqa: S603
            [
                git,
                "config",
                "--local",
                "--get-regexp",
                r"^(maintenance\.auto|gc\.auto)$",
            ],
            cwd=workspace.root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()

    assert dict(line.split(maxsplit=1) for line in config) == {
        "gc.auto": "0",
        "maintenance.auto": "false",
    }


def test_startup_rolls_back_partial_multi_path_progress(
    tmp_path: Path,
) -> None:
    """Recovery handles both an exchanged path and a merely prepared candidate."""
    live = tmp_path / "live"
    _init_repo(live)

    first = _record("a.txt", original=b"a-original\n", produced=b"a-update\n")
    second = _record("b.txt", original=b"b-original\n", produced=b"b-update\n")
    (live / "a.txt").write_bytes(b"a-update\n")
    _retained(live, first).write_bytes(b"a-original\n")
    _retained(live, second).write_bytes(b"b-update\n")
    journal = _write_journal(live, [first, second], committed=False)

    with IsolatedUpdateWorkspace(live):
        pass

    assert (live / "a.txt").read_bytes() == b"a-original\n"
    assert (live / "b.txt").read_bytes() == b"b-original\n"
    assert not _retained(live, first).exists()
    assert not _retained(live, second).exists()
    assert not journal.exists()


@pytest.mark.parametrize("change", ["creation", "deletion", "type"])
def test_startup_recovers_precommit_change_kinds(
    tmp_path: Path,
    change: str,
) -> None:
    """Creation, deletion, and file/symlink replacement all roll back safely."""
    live = tmp_path / "live"
    _init_repo(live)
    if change == "creation":
        record = _record("new.txt", original=None, produced=b"new\n")
        (live / "new.txt").write_bytes(b"new\n")
    elif change == "deletion":
        record = _record("a.txt", original=b"a-original\n", produced=None)
        (live / "a.txt").unlink()
        _retained(live, record).write_bytes(b"a-original\n")
    else:
        record = _record(
            "a.txt",
            original=b"target.txt",
            produced=b"a-update\n",
            original_symlink=True,
        )
        (live / "a.txt").write_bytes(b"a-update\n")
        _retained(live, record).symlink_to("target.txt")
    journal = _write_journal(live, [record], committed=False)

    with IsolatedUpdateWorkspace(live):
        pass

    if change == "creation":
        assert not (live / "new.txt").exists()
    elif change == "deletion":
        assert (live / "a.txt").read_bytes() == b"a-original\n"
    else:
        assert (live / "a.txt").is_symlink()
        assert (live / "a.txt").readlink() == Path("target.txt")
    assert not _retained(live, record).exists()
    assert not journal.exists()


@pytest.mark.parametrize(
    "invalid_case",
    [
        "json",
        "envelope",
        "version",
        "root_type",
        "root_value",
        "committed",
        "paths",
        "record",
        "record_fields",
        "path",
        "retained",
        "fingerprint_type",
        "fingerprint_kind_type",
        "fingerprint_kind_value",
        "fingerprint_mode",
        "fingerprint_digest_type",
        "fingerprint_digest_length",
        "duplicate",
    ],
)
def test_startup_rejects_corrupt_journal_without_mutation(
    tmp_path: Path,
    invalid_case: str,
) -> None:
    """Malformed recovery authority fails closed before inspecting live leaves."""
    live = tmp_path / "live"
    _init_repo(live)
    journal = live / ".git" / persistence_module._TRANSACTION_JOURNAL
    envelope, record = _valid_journal_envelope(live)
    value: object = envelope
    if invalid_case == "json":
        payload = b"not-json"
    else:
        if invalid_case == "envelope":
            value = []
        elif invalid_case == "version":
            envelope["version"] = 2
        elif invalid_case == "root_type":
            envelope["root"] = None
        elif invalid_case == "root_value":
            envelope["root"] = os.fspath(live / "other")
        elif invalid_case == "committed":
            envelope["committed"] = "false"
        elif invalid_case == "paths":
            envelope["paths"] = {}
        elif invalid_case == "record":
            envelope["paths"] = [0]
        elif invalid_case == "record_fields":
            envelope["paths"] = [{}]
        elif invalid_case == "path":
            record["path"] = "../escape"
        elif invalid_case == "retained":
            record["retained"] = "../retained"
        elif invalid_case == "fingerprint_type":
            record["original"] = []
        elif invalid_case == "fingerprint_kind_type":
            record["original"] = {"kind": 1, "mode": 0o644, "sha256": "0" * 64}
        elif invalid_case == "fingerprint_kind_value":
            record["original"] = {
                "kind": "directory",
                "mode": 0o644,
                "sha256": "0" * 64,
            }
        elif invalid_case == "fingerprint_mode":
            record["original"] = {
                "kind": "file",
                "mode": "0644",
                "sha256": "0" * 64,
            }
        elif invalid_case == "fingerprint_digest_type":
            record["original"] = {"kind": "file", "mode": 0o644, "sha256": 0}
        elif invalid_case == "fingerprint_digest_length":
            record["original"] = {"kind": "file", "mode": 0o644, "sha256": "0"}
        else:
            envelope["paths"] = [record, dict(record)]
        payload = json.dumps(value).encode()
    journal.write_bytes(payload)

    with (
        pytest.raises(UpdateWorkspaceError, match="transaction journal"),
        IsolatedUpdateWorkspace(live),
    ):
        pytest.fail("corrupt recovery state must prevent workspace entry")

    assert (live / "a.txt").read_bytes() == b"a-original\n"
    assert journal.read_bytes() == payload


def test_startup_rejects_symlinked_journal_without_mutation(tmp_path: Path) -> None:
    """Recovery never follows a journal path outside Git metadata."""
    live = tmp_path / "live"
    _init_repo(live)
    outside = tmp_path / "outside.json"
    outside.write_text("outside\n", encoding="utf-8")
    journal = live / ".git" / persistence_module._TRANSACTION_JOURNAL
    journal.symlink_to(outside)

    with (
        pytest.raises(UpdateWorkspaceError, match="transaction journal"),
        IsolatedUpdateWorkspace(live),
    ):
        pytest.fail("a symlinked journal must prevent workspace entry")

    assert outside.read_text(encoding="utf-8") == "outside\n"
    assert journal.is_symlink()


def test_startup_preserves_ambiguous_canonical_and_original(
    tmp_path: Path,
) -> None:
    """A competing canonical leaf and retained original both survive recovery."""
    live = tmp_path / "live"
    _init_repo(live)
    record = _record("a.txt", original=b"a-original\n", produced=b"a-update\n")
    (live / "a.txt").write_bytes(b"external\n")
    _retained(live, record).write_bytes(b"a-original\n")
    journal = _write_journal(live, [record], committed=False)

    with (
        pytest.raises(UpdateWorkspaceConflictError) as exc_info,
        IsolatedUpdateWorkspace(live),
    ):
        pytest.fail("ambiguous recovery must prevent workspace entry")

    assert exc_info.value.paths == (Path("a.txt"),)
    assert (live / "a.txt").read_bytes() == b"external\n"
    assert _retained(live, record).read_bytes() == b"a-original\n"
    assert journal.exists()


def test_startup_preserves_produced_leaf_when_original_is_missing(
    tmp_path: Path,
) -> None:
    """An uncommitted replacement without its retained original is ambiguous."""
    live = tmp_path / "live"
    _init_repo(live)
    record = _record("a.txt", original=b"a-original\n", produced=b"a-update\n")
    (live / "a.txt").write_bytes(b"a-update\n")
    journal = _write_journal(live, [record], committed=False)

    with (
        pytest.raises(UpdateWorkspaceConflictError) as exc_info,
        IsolatedUpdateWorkspace(live),
    ):
        pytest.fail("a missing retained original must prevent recovery")

    assert exc_info.value.paths == (Path("a.txt"),)
    assert (live / "a.txt").read_bytes() == b"a-update\n"
    assert journal.exists()


@pytest.mark.parametrize("committed", [False, True])
def test_startup_rejects_transaction_with_missing_parent(
    tmp_path: Path,
    *,
    committed: bool,
) -> None:
    """Recovery cannot authorize a path whose parent topology disappeared."""
    live = tmp_path / "live"
    _init_repo(live)
    record = _record("missing/a.txt", original=None, produced=b"a-update\n")
    journal = _write_journal(live, [record], committed=committed)

    with (
        pytest.raises(UpdateWorkspaceConflictError) as exc_info,
        IsolatedUpdateWorkspace(live),
    ):
        pytest.fail("missing recovery topology must prevent workspace entry")

    assert exc_info.value.paths == (Path("missing/a.txt"),)
    assert not (live / "missing").exists()
    assert journal.exists()


def test_startup_preserves_both_leaves_when_swap_back_races(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A canonical edit immediately after recovery exchange loses no leaf."""
    live = tmp_path / "live"
    _init_repo(live)
    record = _record("a.txt", original=b"a-original\n", produced=b"a-update\n")
    (live / "a.txt").write_bytes(b"a-update\n")
    _retained(live, record).write_bytes(b"a-original\n")
    journal = _write_journal(live, [record], committed=False)
    original_exchange = persistence_module._rename_exchange

    def _exchange_then_edit(
        source: str,
        destination: str,
        *,
        descriptor: int,
    ) -> None:
        original_exchange(source, destination, descriptor=descriptor)
        opened = os.open(source, os.O_WRONLY | os.O_TRUNC, dir_fd=descriptor)
        try:
            os.write(opened, b"external\n")
        finally:
            os.close(opened)

    monkeypatch.setattr(persistence_module, "_rename_exchange", _exchange_then_edit)
    with (
        pytest.raises(UpdateWorkspaceConflictError) as exc_info,
        IsolatedUpdateWorkspace(live),
    ):
        pytest.fail("a recovery exchange race must remain discoverable")

    assert exc_info.value.paths == (Path("a.txt"),)
    assert (live / "a.txt").read_bytes() == b"external\n"
    assert _retained(live, record).read_bytes() == b"a-update\n"
    assert journal.exists()


def test_startup_preserves_candidate_edited_before_swap_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A produced leaf edited just before exchange remains retained and reported."""
    live = tmp_path / "live"
    _init_repo(live)
    record = _record("a.txt", original=b"a-original\n", produced=b"a-update\n")
    (live / "a.txt").write_bytes(b"a-update\n")
    _retained(live, record).write_bytes(b"a-original\n")
    journal = _write_journal(live, [record], committed=False)
    original_exchange = persistence_module._rename_exchange

    def _edit_then_exchange(
        source: str,
        destination: str,
        *,
        descriptor: int,
    ) -> None:
        opened = os.open(source, os.O_WRONLY | os.O_TRUNC, dir_fd=descriptor)
        try:
            os.write(opened, b"external\n")
        finally:
            os.close(opened)
        original_exchange(source, destination, descriptor=descriptor)

    monkeypatch.setattr(persistence_module, "_rename_exchange", _edit_then_exchange)
    with (
        pytest.raises(UpdateWorkspaceConflictError) as exc_info,
        IsolatedUpdateWorkspace(live),
    ):
        pytest.fail("a dirty produced leaf must survive recovery")

    assert exc_info.value.paths == (Path("a.txt"),)
    assert (live / "a.txt").read_bytes() == b"a-original\n"
    assert _retained(live, record).read_bytes() == b"external\n"
    assert journal.exists()


def test_startup_restores_dirty_retained_deletion_without_discarding_it(
    tmp_path: Path,
) -> None:
    """A changed deleted original returns to its canonical name but stays ambiguous."""
    live = tmp_path / "live"
    _init_repo(live)
    record = _record("a.txt", original=b"a-original\n", produced=None)
    (live / "a.txt").unlink()
    _retained(live, record).write_bytes(b"external\n")
    journal = _write_journal(live, [record], committed=False)

    with (
        pytest.raises(UpdateWorkspaceConflictError) as exc_info,
        IsolatedUpdateWorkspace(live),
    ):
        pytest.fail("a dirty retained deletion must survive recovery")

    assert exc_info.value.paths == (Path("a.txt"),)
    assert (live / "a.txt").read_bytes() == b"external\n"
    assert not _retained(live, record).exists()
    assert journal.exists()


def test_startup_keeps_journal_when_deleted_original_is_missing(
    tmp_path: Path,
) -> None:
    """An uncommitted deletion without its retained original cannot be resolved."""
    live = tmp_path / "live"
    _init_repo(live)
    record = _record("a.txt", original=b"a-original\n", produced=None)
    (live / "a.txt").unlink()
    journal = _write_journal(live, [record], committed=False)

    with (
        pytest.raises(UpdateWorkspaceConflictError) as exc_info,
        IsolatedUpdateWorkspace(live),
    ):
        pytest.fail("a missing deleted original must remain discoverable")

    assert exc_info.value.paths == (Path("a.txt"),)
    assert not (live / "a.txt").exists()
    assert journal.exists()


@pytest.mark.parametrize("conflict", ["canonical", "retained"])
def test_startup_preserves_ambiguous_postcommit_cleanup(
    tmp_path: Path,
    conflict: str,
) -> None:
    """Committed cleanup deletes only the exact journaled original."""
    live = tmp_path / "live"
    _init_repo(live)
    record = _record("a.txt", original=b"a-original\n", produced=b"a-update\n")
    (live / "a.txt").write_bytes(
        b"external\n" if conflict == "canonical" else b"a-update\n"
    )
    _retained(live, record).write_bytes(
        b"external\n" if conflict == "retained" else b"a-original\n"
    )
    journal = _write_journal(live, [record], committed=True)

    with (
        pytest.raises(UpdateWorkspaceConflictError) as exc_info,
        IsolatedUpdateWorkspace(live),
    ):
        pytest.fail("ambiguous committed cleanup must prevent workspace entry")

    assert exc_info.value.paths == (Path("a.txt"),)
    assert (live / "a.txt").exists()
    assert _retained(live, record).exists()
    assert journal.exists()


def test_startup_keeps_journal_when_directory_fsync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recovery durability failure leaves discoverable state for the next run."""
    live = tmp_path / "live"
    _init_repo(live)
    record = _record("a.txt", original=b"a-original\n", produced=b"a-update\n")
    (live / "a.txt").write_bytes(b"a-update\n")
    _retained(live, record).write_bytes(b"a-original\n")
    journal = _write_journal(live, [record], committed=False)

    def _fail_fsync(_descriptor: int) -> None:
        raise OSError("directory fsync failed")

    monkeypatch.setattr(
        persistence_module,
        "_fsync_directory_descriptor",
        _fail_fsync,
    )
    with (
        pytest.raises(UpdateWorkspaceError, match="recovery"),
        IsolatedUpdateWorkspace(live),
    ):
        pytest.fail("failed recovery durability must prevent entry")

    assert (live / "a.txt").read_bytes() == b"a-original\n"
    assert _retained(live, record).read_bytes() == b"a-update\n"
    assert journal.exists()

    monkeypatch.undo()
    with IsolatedUpdateWorkspace(live):
        pass
    assert not _retained(live, record).exists()
    assert not journal.exists()


def test_startup_keeps_journal_when_journal_removal_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recovered leaves remain safe and discoverable if journal cleanup fails."""
    live = tmp_path / "live"
    _init_repo(live)
    record = _record("a.txt", original=b"a-original\n", produced=b"a-update\n")
    _retained(live, record).write_bytes(b"a-update\n")
    journal = _write_journal(live, [record], committed=False)

    def _fail_remove(_journal: Path) -> None:
        raise OSError("journal removal failed")

    monkeypatch.setattr(persistence_module, "_remove_transaction", _fail_remove)
    with (
        pytest.raises(UpdateWorkspaceError, match="recovery cleanup"),
        IsolatedUpdateWorkspace(live),
    ):
        pytest.fail("failed journal cleanup must prevent entry")

    assert (live / "a.txt").read_bytes() == b"a-original\n"
    assert not _retained(live, record).exists()
    assert journal.exists()

    monkeypatch.undo()
    with IsolatedUpdateWorkspace(live):
        pass
    assert not journal.exists()


def test_partial_committed_cleanup_never_rolls_back_visible_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup failure after the commit marker is resumable, not rollback-worthy."""
    live = tmp_path / "live"
    _init_repo(live)
    original_unlink = os.unlink
    failed = False

    with IsolatedUpdateWorkspace(live) as workspace:
        (workspace.root / "a.txt").write_text("a-update\n", encoding="utf-8")
        (workspace.root / "b.txt").write_text("b-update\n", encoding="utf-8")

        def _fail_one_cleanup(
            path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
            *,
            dir_fd: int | None = None,
        ) -> None:
            nonlocal failed
            if "nixcfg-transaction" in os.fsdecode(path) and not failed:
                failed = True
                raise PermissionError("retained cleanup failed")
            original_unlink(path, dir_fd=dir_fd)

        with monkeypatch.context() as scoped:
            scoped.setattr(os, "unlink", _fail_one_cleanup)
            assert workspace.promote({"a.txt", "b.txt"}) == (
                Path("a.txt"),
                Path("b.txt"),
            )

    assert failed
    assert (live / "a.txt").read_text(encoding="utf-8") == "a-update\n"
    assert (live / "b.txt").read_text(encoding="utf-8") == "b-update\n"
    journal = live / ".git" / persistence_module._TRANSACTION_JOURNAL
    assert journal.exists()
    assert list(live.glob(".*.nixcfg-transaction-*"))

    with IsolatedUpdateWorkspace(live):
        pass
    assert (live / "a.txt").read_text(encoding="utf-8") == "a-update\n"
    assert (live / "b.txt").read_text(encoding="utf-8") == "b-update\n"
    assert not journal.exists()
    assert list(live.glob(".*.nixcfg-transaction-*")) == []
