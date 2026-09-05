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
    UpdatePromotionState,
    UpdateWorkspaceConflictError,
    UpdateWorkspaceError,
    UpdateWorkspacePromotionError,
    visible_source_snapshot,
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
    journal = (
        persistence_module._git_dir(root) / persistence_module._TRANSACTION_JOURNAL
    )
    persistence_module._write_transaction(
        journal,
        persistence_module._Transaction(root.resolve(), committed, tuple(records)),
    )
    return journal


def _add_worktree(main: Path, linked: Path) -> None:
    git = shutil.which("git")
    assert git is not None
    subprocess.run(  # noqa: S603
        [git, "worktree", "add", "--quiet", "-b", linked.name, linked],
        cwd=main,
        check=True,
    )


def test_recovery_journals_are_scoped_to_each_linked_worktree(
    tmp_path: Path,
) -> None:
    """A crashed transaction in one worktree must not block its siblings."""
    live = tmp_path / "live"
    linked = tmp_path / "linked"
    _init_repo(live)
    _add_worktree(live, linked)
    record = _record("a.txt", original=b"a-original\n", produced=b"a-update\n")
    journal = _write_journal(live, [record], committed=False)

    assert journal.parent == persistence_module._git_common_dir(live)
    assert persistence_module._git_dir(linked) != journal.parent
    with IsolatedUpdateWorkspace(linked):
        pass
    assert journal.exists()

    with IsolatedUpdateWorkspace(live):
        pass
    assert not journal.exists()


@pytest.mark.parametrize("committed", [False, True])
def test_linked_worktree_recovers_shared_journal_before_snapshot(
    tmp_path: Path,
    *,
    committed: bool,
) -> None:
    """Old-layout recovery completes before partially promoted bytes become a baseline."""
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    _init_repo(main)
    _add_worktree(main, linked)
    record = _record("a.txt", original=b"a-original\n", produced=b"a-update\n")
    journal = persistence_module._git_common_dir(linked) / (
        persistence_module._TRANSACTION_JOURNAL
    )
    persistence_module._write_transaction(
        journal,
        persistence_module._Transaction(linked.resolve(), committed, (record,)),
    )
    _retained(linked, record).write_bytes(b"a-original\n")
    (linked / "a.txt").write_bytes(b"a-update\n")
    expected = b"a-update\n" if committed else b"a-original\n"

    with IsolatedUpdateWorkspace(linked) as workspace:
        assert (workspace.root / "a.txt").read_bytes() == expected
        assert not (workspace.root / record.retained).exists()
        assert workspace.changed_paths() == ()
        assert not journal.exists()

    assert (linked / "a.txt").read_bytes() == expected
    assert not _retained(linked, record).exists()
    assert (main / "a.txt").read_bytes() == b"a-original\n"


def test_linked_worktree_preserves_shared_journal_owned_by_another_worktree(
    tmp_path: Path,
) -> None:
    """A shared journal never authorizes recovery against a sibling's descriptor."""
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    sibling = tmp_path / "sibling"
    _init_repo(main)
    _add_worktree(main, linked)
    _add_worktree(main, sibling)
    record = _record("a.txt", original=b"a-original\n", produced=b"a-update\n")
    journal = persistence_module._git_common_dir(linked) / (
        persistence_module._TRANSACTION_JOURNAL
    )
    persistence_module._write_transaction(
        journal,
        persistence_module._Transaction(
            root=sibling.resolve(), committed=False, paths=(record,)
        ),
    )
    original_journal = journal.read_bytes()
    _retained(sibling, record).write_bytes(b"a-original\n")
    (sibling / "a.txt").write_bytes(b"a-update\n")

    with IsolatedUpdateWorkspace(linked) as workspace:
        assert (workspace.root / "a.txt").read_bytes() == b"a-original\n"

    assert journal.read_bytes() == original_journal
    assert (sibling / "a.txt").read_bytes() == b"a-update\n"
    assert _retained(sibling, record).read_bytes() == b"a-original\n"
    with IsolatedUpdateWorkspace(sibling):
        pass
    assert (sibling / "a.txt").read_bytes() == b"a-original\n"
    assert not journal.exists()


@pytest.mark.parametrize(
    "invalid_case",
    ["json", "relative_root", "parent_root", "foreign_record", "symlink"],
)
def test_linked_worktree_rejects_invalid_shared_journal(
    tmp_path: Path,
    invalid_case: str,
) -> None:
    """Malformed shared state cannot be ignored as an unrelated transaction."""
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    _init_repo(main)
    _add_worktree(main, linked)
    journal = persistence_module._git_common_dir(linked) / (
        persistence_module._TRANSACTION_JOURNAL
    )
    envelope, record = _valid_journal_envelope(linked)
    if invalid_case == "relative_root":
        envelope["root"] = "linked"
    elif invalid_case == "parent_root":
        envelope["root"] = os.fspath(linked / ".." / "linked")
    elif invalid_case == "foreign_record":
        envelope["root"] = os.fspath(main)
        record["path"] = "../escape"
    payload = b"not-json" if invalid_case == "json" else json.dumps(envelope).encode()
    if invalid_case == "symlink":
        destination = tmp_path / "journal-target"
        destination.write_bytes(payload)
        journal.symlink_to(destination)
    else:
        journal.write_bytes(payload)

    with (
        pytest.raises(UpdateWorkspaceError, match="transaction journal"),
        IsolatedUpdateWorkspace(linked),
    ):
        pytest.fail("invalid shared recovery authority must prevent startup")

    assert journal.read_bytes() == payload
    assert (linked / "a.txt").read_bytes() == b"a-original\n"
    assert (main / "a.txt").read_bytes() == b"a-original\n"


def test_linked_worktree_rejects_simultaneous_shared_and_local_journals(
    tmp_path: Path,
) -> None:
    """Two authorities for the same worktree require explicit reconciliation."""
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    _init_repo(main)
    _add_worktree(main, linked)
    old_record = _record("a.txt", original=b"a-original\n", produced=b"a-update\n")
    shared = persistence_module._git_common_dir(linked) / (
        persistence_module._TRANSACTION_JOURNAL
    )
    persistence_module._write_transaction(
        shared,
        persistence_module._Transaction(
            root=linked.resolve(), committed=False, paths=(old_record,)
        ),
    )
    new_record = _record("b.txt", original=b"b-original\n", produced=b"b-update\n")
    local = _write_journal(linked, [new_record], committed=False)
    shared_payload, local_payload = shared.read_bytes(), local.read_bytes()
    for record, original, produced in (
        (old_record, b"a-original\n", b"a-update\n"),
        (new_record, b"b-original\n", b"b-update\n"),
    ):
        _retained(linked, record).write_bytes(original)
        (linked / record.path).write_bytes(produced)

    with (
        pytest.raises(UpdateWorkspaceError, match="Conflicting update transaction"),
        IsolatedUpdateWorkspace(linked),
    ):
        pytest.fail("conflicting recovery authorities must prevent startup")

    assert shared.read_bytes() == shared_payload
    assert local.read_bytes() == local_payload
    assert (linked / "a.txt").read_bytes() == b"a-update\n"
    assert (linked / "b.txt").read_bytes() == b"b-update\n"
    assert _retained(linked, old_record).read_bytes() == b"a-original\n"
    assert _retained(linked, new_record).read_bytes() == b"b-original\n"


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


def test_visible_source_snapshot_is_exact_and_excludes_git_and_ignored_files(
    tmp_path: Path,
) -> None:
    """Re-exec snapshots preserve visible bytes without Git clean-filter effects."""
    live = tmp_path / "live"
    _init_repo(live)
    (live / ".gitattributes").write_text("*.txt text eol=lf\n", encoding="utf-8")
    (live / "a.txt").write_bytes(b"a-working\r\n")
    (live / "new-runtime.py").write_text("VALUE = 1\n", encoding="utf-8")
    (live / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (live / "ignored.txt").write_text("ignored\n", encoding="utf-8")

    with visible_source_snapshot(live) as snapshot:
        snapshot_path = snapshot
        assert (snapshot / "a.txt").read_bytes() == b"a-working\r\n"
        assert (snapshot / "new-runtime.py").is_file()
        assert not (snapshot / "ignored.txt").exists()
        assert not (snapshot / ".git").exists()

    assert not snapshot_path.exists()


def test_validation_snapshot_binds_the_bytes_allowed_for_promotion(
    tmp_path: Path,
) -> None:
    """Never promote candidate bytes that were changed after root validation."""
    live = tmp_path / "live"
    _init_repo(live)

    with IsolatedUpdateWorkspace(live) as workspace:
        candidate = workspace.root / "a.txt"
        candidate.write_text("validated update\n", encoding="utf-8")
        with workspace.validation_snapshot() as snapshot:
            assert snapshot.changed_paths == (Path("a.txt"),)
            assert (snapshot.root / "a.txt").read_text(encoding="utf-8") == (
                "validated update\n"
            )
        candidate.write_text("unvalidated update\n", encoding="utf-8")

        with pytest.raises(UpdateWorkspaceError, match="changed after root closure"):
            workspace.promote({"a.txt"})

    assert (live / "a.txt").read_text(encoding="utf-8") == "a-original\n"


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


def test_postcommit_canonical_drift_is_reported_as_indeterminate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never report success after an external edit supersedes committed output."""
    live = tmp_path / "live"
    _init_repo(live)
    original_write = persistence_module._write_transaction

    with IsolatedUpdateWorkspace(live) as workspace:
        (workspace.root / "a.txt").write_text("a-update\n", encoding="utf-8")

        def _write_then_drift(
            journal: Path,
            transaction: persistence_module._Transaction,
        ) -> None:
            original_write(journal, transaction)
            if transaction.committed:
                (live / "a.txt").write_text("external edit\n", encoding="utf-8")

        monkeypatch.setattr(
            persistence_module,
            "_write_transaction",
            _write_then_drift,
        )
        with pytest.raises(UpdateWorkspaceConflictError) as exc_info:
            workspace.promote({"a.txt"})

    assert exc_info.value.paths == (Path("a.txt"),)
    assert exc_info.value.promotion_state is UpdatePromotionState.UNKNOWN
    assert (live / "a.txt").read_text(encoding="utf-8") == "external edit\n"
    assert (
        persistence_module._git_dir(live) / persistence_module._TRANSACTION_JOURNAL
    ).exists()


def test_postcommit_parent_move_is_reported_as_indeterminate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An open directory descriptor cannot prove output at a moved live path."""
    live = tmp_path / "live"
    _init_repo(live)
    parent = live / "nested"
    parent.mkdir()
    (parent / "source.txt").write_text("original\n", encoding="utf-8")
    moved = tmp_path / "moved"
    original_write = persistence_module._write_transaction

    with IsolatedUpdateWorkspace(live) as workspace:
        (workspace.root / "nested/source.txt").write_text(
            "candidate\n", encoding="utf-8"
        )

        def _write_then_move_parent(
            journal: Path,
            transaction: persistence_module._Transaction,
        ) -> None:
            original_write(journal, transaction)
            if transaction.committed:
                parent.rename(moved)

        monkeypatch.setattr(
            persistence_module, "_write_transaction", _write_then_move_parent
        )
        with pytest.raises(UpdateWorkspaceConflictError) as exc_info:
            workspace.promote({"nested/source.txt"})

    assert exc_info.value.paths == (Path("nested/source.txt"),)
    assert exc_info.value.promotion_state is UpdatePromotionState.UNKNOWN
    assert not parent.exists()
    assert (moved / "source.txt").read_text(encoding="utf-8") == "candidate\n"
    assert (
        persistence_module._git_dir(live) / persistence_module._TRANSACTION_JOURNAL
    ).exists()


@pytest.mark.parametrize(
    ("commit_marker_written", "expected_state", "expected_content"),
    [
        pytest.param(
            False,
            UpdatePromotionState.ROLLED_BACK,
            "a-original\n",
            id="failure-before-commit-marker",
        ),
        pytest.param(
            True,
            UpdatePromotionState.PROMOTED,
            "a-update\n",
            id="failure-after-commit-marker",
        ),
    ],
)
def test_promotion_io_failure_reports_recovered_commit_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    commit_marker_written: bool,
    expected_state: UpdatePromotionState,
    expected_content: str,
) -> None:
    """Derive the live outcome from the recovered durable transaction marker."""
    live = tmp_path / "live"
    _init_repo(live)
    original_write = persistence_module._write_transaction

    with IsolatedUpdateWorkspace(live) as workspace:
        (workspace.root / "a.txt").write_text("a-update\n", encoding="utf-8")

        def _fail_commit_write(
            journal: Path,
            transaction: persistence_module._Transaction,
        ) -> None:
            if not transaction.committed:
                original_write(journal, transaction)
                return
            if commit_marker_written:
                original_write(journal, transaction)
            raise OSError("simulated commit journal I/O failure")

        monkeypatch.setattr(
            persistence_module,
            "_write_transaction",
            _fail_commit_write,
        )
        with pytest.raises(UpdateWorkspacePromotionError) as exc_info:
            workspace.promote({"a.txt"})

    assert exc_info.value.promotion_state is expected_state
    assert (live / "a.txt").read_text(encoding="utf-8") == expected_content
    assert not (live / ".git" / persistence_module._TRANSACTION_JOURNAL).exists()
    assert not list(live.glob(".a.txt.nixcfg-transaction-*"))


def test_promotion_conflict_reports_successful_rollback_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Attach a rolled-back state when final validation rejects the candidate."""
    live = tmp_path / "live"
    _init_repo(live)
    original_conflicts = persistence_module._source_conflicts
    calls = 0

    with IsolatedUpdateWorkspace(live) as workspace:
        (workspace.root / "a.txt").write_text("a-update\n", encoding="utf-8")

        def _fail_final_validation(*args: object, **kwargs: object) -> tuple[Path, ...]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return original_conflicts(*args, **kwargs)
            return (Path("external.txt"),)

        monkeypatch.setattr(
            persistence_module,
            "_source_conflicts",
            _fail_final_validation,
        )
        with pytest.raises(UpdateWorkspaceConflictError) as exc_info:
            workspace.promote({"a.txt"})

    assert exc_info.value.paths == (Path("external.txt"),)
    assert exc_info.value.promotion_state is UpdatePromotionState.ROLLED_BACK
    assert (live / "a.txt").read_text(encoding="utf-8") == "a-original\n"
