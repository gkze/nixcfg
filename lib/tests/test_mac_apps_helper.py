"""Failure-lifecycle contracts for standalone macOS application activation."""

import json
import multiprocessing
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from lib import mac_apps_helper as helper

if TYPE_CHECKING:
    from collections.abc import Sequence


@pytest.fixture(autouse=True)
def _disable_live_metadata_services(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every activation test inside tmp_path and away from macOS services."""
    monkeypatch.setattr(
        helper,
        "_refresh_launch_services_registration",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        helper,
        "_refresh_spotlight_metadata",
        lambda *_args, **_kwargs: None,
    )


def _app(root: Path, name: str, marker: str) -> Path:
    app = root / name
    app.mkdir(parents=True)
    (app / "marker.txt").write_text(marker, encoding="utf-8")
    return app


def _marker(app: Path) -> str:
    return (app / "marker.txt").read_text(encoding="utf-8")


def _manifest_names(state_file: Path) -> str:
    """Return the ownership manifest as name-only lines for assertions."""
    return "".join(
        f"{line.split(chr(9), 1)[0]}\n"
        for line in state_file.read_text(encoding="utf-8").splitlines()
    )


def _manifest_records(state_file: Path) -> list[tuple[str, str]]:
    """Return the raw ownership records exactly as the manifest stores them."""
    records: list[tuple[str, str]] = []
    for line in state_file.read_text(encoding="utf-8").splitlines():
        bundle_name, _, source_path = line.partition(chr(9))
        records.append((bundle_name, source_path))
    return records


def test_system_applications_skips_bundle_whose_recorded_source_is_current(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A matching recorded store path must leave the live bundle untouched."""
    source = _app(tmp_path / "source", "Good.app", "current")
    payload, target_directory, state_file = _payload(
        tmp_path,
        entries=[_entry(source)],
        previous_manifest=[],
    )
    live = _app(target_directory, "Good.app", "current")
    state_file.write_text(f"Good.app\t{source}\n", encoding="utf-8")
    inode_before = live.stat().st_ino

    def _unexpected_copy(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("an unchanged bundle must never be staged")

    monkeypatch.setattr(helper, "_rsync_copy", _unexpected_copy)

    helper._system_applications(payload)

    assert live.stat().st_ino == inode_before
    assert _transaction_directories(target_directory) == []
    assert _manifest_records(state_file) == [("Good.app", str(source))]


def test_system_applications_replaces_bundle_whose_recorded_source_moved(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A different recorded store path means the bundle changed and is replaced."""
    previous_source = _app(tmp_path / "previous", "Good.app", "stale")
    source = _app(tmp_path / "source", "Good.app", "fresh")
    payload, target_directory, state_file = _payload(
        tmp_path,
        entries=[_entry(source)],
        previous_manifest=[],
    )
    live = _app(target_directory, "Good.app", "stale")
    state_file.write_text(f"Good.app\t{previous_source}\n", encoding="utf-8")

    monkeypatch.setattr(helper, "_rsync_copy", _copy_rsync)

    helper._system_applications(payload)

    assert _marker(live) == "fresh"
    assert _manifest_records(state_file) == [("Good.app", str(source))]


@pytest.mark.parametrize(
    ("trees_match", "expected_marker"),
    [(True, "stale"), (False, "fresh")],
)
def test_system_applications_compares_content_for_legacy_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    trees_match: bool,
    expected_marker: str,
) -> None:
    """A manifest without sources falls back to one content comparison."""
    source = _app(tmp_path / "source", "Good.app", "fresh")
    payload, target_directory, _state_file = _payload(
        tmp_path,
        entries=[_entry(source)],
        previous_manifest=["Good.app"],
    )
    live = _app(target_directory, "Good.app", "stale")

    monkeypatch.setattr(
        helper,
        "_bundle_trees_match",
        lambda *_args, **_kwargs: trees_match,
    )
    monkeypatch.setattr(helper, "_rsync_copy", _copy_rsync)

    helper._system_applications(payload)

    assert _marker(live) == expected_marker


def test_system_applications_skips_symlink_bundle_that_already_points_at_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A symlink bundle is current only while it still points at the source."""
    source = _app(tmp_path / "source", "Link.app", "linked")
    payload, target_directory, state_file = _payload(
        tmp_path,
        entries=[_entry(source, mode="symlink")],
        previous_manifest=[],
    )
    target_directory.mkdir(parents=True, exist_ok=True)
    live = target_directory / "Link.app"
    live.symlink_to(source)

    def _unexpected_copy(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a current symlink bundle must never be staged")

    monkeypatch.setattr(helper, "_rsync_copy", _unexpected_copy)

    helper._system_applications(payload)

    assert live.is_symlink()
    assert _transaction_directories(target_directory) == []
    assert _manifest_records(state_file) == [("Link.app", str(source))]


def test_bundle_trees_match_reports_identical_for_a_quiet_dry_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Rsync lists nothing when the installed bundle already matches."""
    source = tmp_path / "source"
    live = tmp_path / "live"
    source.mkdir()
    live.mkdir()
    monkeypatch.setattr(
        helper.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        ),
    )

    assert (
        helper._bundle_trees_match(
            source,
            live,
            rsync_path="/rsync",
            writable=False,
        )
        is True
    )


def test_bundle_trees_match_reports_change_for_an_itemised_dry_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Any itemised transfer or a failing rsync means the bundle changed."""
    source = tmp_path / "source"
    live = tmp_path / "live"
    source.mkdir()
    live.mkdir()
    monkeypatch.setattr(
        helper.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=">f+++++++++ marker.txt\n",
            stderr="",
        ),
    )

    assert (
        helper._bundle_trees_match(
            source,
            live,
            rsync_path="/rsync",
            writable=False,
        )
        is False
    )


def test_bundle_trees_match_reports_change_when_rsync_cannot_run(
    tmp_path: Path,
) -> None:
    """An unusable rsync path must fail closed by asking for a fresh copy."""
    source = tmp_path / "source"
    live = tmp_path / "live"
    source.mkdir()
    live.mkdir()

    assert (
        helper._bundle_trees_match(
            source,
            live,
            rsync_path=str(tmp_path / "missing-rsync"),
            writable=False,
        )
        is False
    )


def _entry(source: Path, *, mode: str = "copy") -> dict[str, object]:
    return {
        "bundleName": source.name,
        "mode": mode,
        "preventDowngrade": False,
        "sourcePath": str(source),
    }


def _payload(
    tmp_path: Path,
    *,
    entries: Sequence[dict[str, object]],
    previous_manifest: Sequence[str],
) -> tuple[dict[str, object], Path, Path]:
    target_directory = tmp_path / "Applications"
    state_directory = tmp_path / "state"
    state_directory.mkdir()
    state_file = state_directory / "manager.txt"
    state_file.write_text(
        "".join(f"{name}\n" for name in previous_manifest),
        encoding="utf-8",
    )
    return (
        {
            "entries": list(entries),
            "rsyncPath": "/rsync",
            "stateDirectory": str(state_directory),
            "stateName": "manager",
            "targetDirectory": str(target_directory),
            "writable": False,
        },
        target_directory,
        state_file,
    )


def _copy_rsync(
    src: Path,
    dst: Path,
    *,
    rsync_path: str,
    writable: bool,
) -> None:
    assert rsync_path == "/rsync"
    assert writable is False
    shutil.copytree(src, dst, dirs_exist_ok=True, symlinks=True)


def _transaction_directories(target_directory: Path) -> list[Path]:
    return list(target_directory.glob(".nixcfg-app-activation-*"))


def _crash_activation(payload: dict[str, object], crash_point: str) -> None:
    """Exit only a forked test child after one exact durable boundary."""
    target_directory = Path(str(payload["targetDirectory"]))
    real_replace = Path.replace
    real_write_journal = helper._write_application_journal
    real_write_manifest = helper._write_managed_app_manifest

    def _replace(path: Path, destination: str | Path) -> Path:
        result = real_replace(path, destination)
        source = Path(path)
        destination_path = Path(destination)
        current_backup = (
            crash_point == "current-backup-rename"
            and source == target_directory / "Good.app"
            and "backups" in destination_path.parts
        )
        current_replacement = (
            crash_point == "current-replacement-rename"
            and source.name == "Good.app"
            and "staged" in source.parts
            and destination_path == target_directory / "Good.app"
        )
        stale_backup = (
            crash_point == "stale-backup-rename"
            and source == target_directory / "Stale.app"
            and "backups" in destination_path.parts
        )
        if current_backup or current_replacement or stale_backup:
            os._exit(70)
        return result

    def _write_journal(transaction: helper._ApplicationTransaction) -> None:
        real_write_journal(transaction)
        current = transaction.changes[0]
        stale = transaction.changes[-1]
        should_exit = {
            "current-backup-journal": current.original_moved
            and not current.replacement_moved,
            "current-replacement-journal": current.replacement_moved,
            "stale-backup-journal": stale.replacement is None and stale.original_moved,
            "before-publication": transaction.phase == "publishing",
            "commit": transaction.phase == "committed",
        }.get(crash_point, False)
        if should_exit:
            os._exit(70)

    def _write_manifest(
        state_file: Path,
        bundle_names: Sequence[str],
    ) -> None:
        real_write_manifest(state_file, bundle_names)
        if crash_point == "manifest-publication":
            os._exit(70)

    with (
        patch.object(helper, "_rsync_copy", _copy_rsync),
        patch.object(
            helper,
            "_refresh_launch_services_registration",
            lambda *_args, **_kwargs: None,
        ),
        patch.object(
            helper,
            "_refresh_spotlight_metadata",
            lambda *_args, **_kwargs: None,
        ),
        patch.object(helper, "_write_application_journal", _write_journal),
        patch.object(helper, "_write_managed_app_manifest", _write_manifest),
        patch.object(Path, "replace", _replace),
    ):
        helper._system_applications(payload)


def _wait_for_path(path: Path) -> None:
    deadline = time.monotonic() + 10
    while not path.exists():
        if time.monotonic() >= deadline:
            msg = f"timed out waiting for test signal {path}"
            raise TimeoutError(msg)
        time.sleep(0.01)


def _locked_activation(
    payload: dict[str, object],
    *,
    is_first: bool,
    signal_directory: Path,
) -> None:
    """Coordinate two spawned processes using only filesystem barriers."""
    real_flock = helper.fcntl.flock
    real_preflight = helper._preflight_managed_apps
    real_reconcile = helper._reconcile_managed_apps
    role = "first" if is_first else "second"
    preflight_entered = signal_directory / f"{role}-preflight"
    lock_attempted = signal_directory / f"{role}-lock-attempt"
    release_preflight = signal_directory / "release-first"
    first_committed = signal_directory / "first-committed"

    def _flock(descriptor: int, operation: int) -> None:
        lock_attempted.touch()
        real_flock(descriptor, operation)

    def _preflight(
        entries: Sequence[helper._SystemApplicationEntryPayload],
        target_directory: Path,
    ) -> None:
        preflight_entered.touch()
        if is_first:
            _wait_for_path(release_preflight)
        elif not first_committed.exists():
            msg = "second activation entered preflight before the first commit"
            raise AssertionError(msg)
        real_preflight(entries, target_directory)

    def _reconcile(
        entries: Sequence[helper._SystemApplicationEntryPayload],
        **kwargs: object,
    ) -> None:
        real_reconcile(entries, **kwargs)
        if is_first:
            first_committed.touch()

    with (
        patch.object(helper, "_rsync_copy", _copy_rsync),
        patch.object(
            helper,
            "_refresh_launch_services_registration",
            lambda *_args, **_kwargs: None,
        ),
        patch.object(
            helper,
            "_refresh_spotlight_metadata",
            lambda *_args, **_kwargs: None,
        ),
        patch.object(helper.fcntl, "flock", _flock),
        patch.object(helper, "_preflight_managed_apps", _preflight),
        patch.object(helper, "_reconcile_managed_apps", _reconcile),
    ):
        helper._system_applications(payload)


def test_successful_activation_promotes_current_apps_before_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    installed = tmp_path / "Applications"
    first_source = _app(source, "First.app", "new-first")
    link_source = _app(source, "Link.app", "new-link")
    new_source = _app(source, "New.app", "new-app")
    old_link_source = _app(tmp_path / "old-source", "OldLink.app", "old-link")
    first = _app(installed, "First.app", "old-first")
    link = installed / "Link.app"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(old_link_source, target_is_directory=True)
    stale = _app(installed, "Stale.app", "old-stale")
    payload, target_directory, state_file = _payload(
        tmp_path,
        entries=[
            _entry(first_source),
            _entry(link_source, mode="symlink"),
            _entry(new_source),
        ],
        previous_manifest=["First.app", "Link.app", "Stale.app"],
    )
    monkeypatch.setattr(helper, "_rsync_copy", _copy_rsync)

    helper._system_applications(payload)

    assert _marker(first) == "new-first"
    assert link.is_symlink()
    assert link.resolve() == link_source.resolve()
    assert _marker(target_directory / "New.app") == "new-app"
    assert not stale.exists()
    assert _manifest_names(state_file) == ("First.app\nLink.app\nNew.app\n")
    assert _transaction_directories(target_directory) == []


@pytest.mark.parametrize(
    "cleanup_fails", [False, True], ids=["cleaned", "cleanup-error"]
)
def test_rsync_failure_preserves_live_apps_stale_apps_and_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    cleanup_fails: bool,
) -> None:
    source = tmp_path / "source"
    installed = tmp_path / "Applications"
    good_source = _app(source, "Good.app", "new-good")
    bad_source = _app(source, "Bad.app", "new-bad")
    good = _app(installed, "Good.app", "old-good")
    bad = _app(installed, "Bad.app", "old-bad")
    stale = _app(installed, "Stale.app", "old-stale")
    payload, target_directory, state_file = _payload(
        tmp_path,
        entries=[_entry(good_source), _entry(bad_source)],
        previous_manifest=["Good.app", "Bad.app", "Stale.app"],
    )
    staged_destinations: list[Path] = []

    def _fail_rsync(
        src: Path,
        dst: Path,
        *,
        rsync_path: str,
        writable: bool,
    ) -> None:
        assert rsync_path == "/rsync"
        assert writable is False
        assert target_directory in dst.parents
        assert dst != target_directory / src.name
        assert _marker(target_directory / src.name) == f"old-{src.stem.lower()}"
        staged_destinations.append(dst)
        if src == bad_source:
            raise SystemExit(23)
        _copy_rsync(src, dst, rsync_path=rsync_path, writable=writable)

    monkeypatch.setattr(helper, "_rsync_copy", _fail_rsync)
    if cleanup_fails:
        real_remove_path = helper._remove_path

        def _fail_cleanup(path: Path) -> None:
            if path.name.startswith(".nixcfg-app-activation-"):
                raise OSError("injected cleanup failure")
            real_remove_path(path)

        monkeypatch.setattr(helper, "_remove_path", _fail_cleanup)

    with pytest.raises(SystemExit) as exc_info:
        helper._system_applications(payload)

    assert exc_info.value.code == 23
    assert staged_destinations
    assert _marker(good) == "old-good"
    assert _marker(bad) == "old-bad"
    assert _marker(stale) == "old-stale"
    assert _manifest_names(state_file) == ("Good.app\nBad.app\nStale.app\n")
    transactions = _transaction_directories(target_directory)
    if cleanup_fails:
        assert len(transactions) == 1
        assert "injected cleanup failure" in capsys.readouterr().err
    else:
        assert transactions == []


def test_symlink_failure_preserves_previous_link_current_apps_and_stale_apps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    installed = tmp_path / "Applications"
    good_source = _app(source, "Good.app", "new-good")
    link_source = _app(source, "Link.app", "new-link")
    old_link_source = _app(tmp_path / "old-source", "OldLink.app", "old-link")
    good = _app(installed, "Good.app", "old-good")
    link = installed / "Link.app"
    link.symlink_to(old_link_source, target_is_directory=True)
    stale = _app(installed, "Stale.app", "old-stale")
    payload, target_directory, state_file = _payload(
        tmp_path,
        entries=[_entry(good_source), _entry(link_source, mode="symlink")],
        previous_manifest=["Good.app", "Link.app", "Stale.app"],
    )
    monkeypatch.setattr(helper, "_rsync_copy", _copy_rsync)
    real_symlink_to = Path.symlink_to

    def _fail_symlink(
        path: Path,
        target: str | Path,
        *,
        target_is_directory: bool = False,
    ) -> None:
        if path.name == "Link.app" and target_directory in path.parents:
            raise OSError("injected symlink failure")
        real_symlink_to(
            path,
            target,
            target_is_directory=target_is_directory,
        )

    monkeypatch.setattr(Path, "symlink_to", _fail_symlink)

    with pytest.raises(OSError, match="injected symlink failure"):
        helper._system_applications(payload)

    assert _marker(good) == "old-good"
    assert link.is_symlink()
    assert link.resolve() == old_link_source.resolve()
    assert _marker(stale) == "old-stale"
    assert _manifest_names(state_file) == ("Good.app\nLink.app\nStale.app\n")
    assert _transaction_directories(target_directory) == []


def test_promotion_failure_rolls_back_every_earlier_promotion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    installed = tmp_path / "Applications"
    good_source = _app(source, "Good.app", "new-good")
    bad_source = _app(source, "Bad.app", "new-bad")
    good = _app(installed, "Good.app", "old-good")
    bad = _app(installed, "Bad.app", "old-bad")
    stale = _app(installed, "Stale.app", "old-stale")
    payload, target_directory, state_file = _payload(
        tmp_path,
        entries=[_entry(good_source), _entry(bad_source)],
        previous_manifest=["Good.app", "Bad.app", "Stale.app"],
    )
    monkeypatch.setattr(helper, "_rsync_copy", _copy_rsync)
    real_replace = Path.replace

    def _fail_promotion(path: Path, destination: str | Path) -> Path:
        if (
            path.name == "Bad.app"
            and "staged" in path.parts
            and Path(destination) == target_directory / "Bad.app"
        ):
            raise OSError("injected promotion failure")
        return real_replace(path, destination)

    monkeypatch.setattr(Path, "replace", _fail_promotion)

    with pytest.raises(OSError, match="injected promotion failure"):
        helper._system_applications(payload)

    assert _marker(good) == "old-good"
    assert _marker(bad) == "old-bad"
    assert _marker(stale) == "old-stale"
    assert _manifest_names(state_file) == ("Good.app\nBad.app\nStale.app\n")
    assert _transaction_directories(target_directory) == []


def test_incomplete_rollback_preserves_primary_error_and_recovery_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source"
    installed = tmp_path / "Applications"
    good_source = _app(source, "Good.app", "new-good")
    bad_source = _app(source, "Bad.app", "new-bad")
    good = _app(installed, "Good.app", "old-good")
    bad = _app(installed, "Bad.app", "old-bad")
    stale = _app(installed, "Stale.app", "old-stale")
    payload, target_directory, state_file = _payload(
        tmp_path,
        entries=[_entry(good_source), _entry(bad_source)],
        previous_manifest=["Good.app", "Bad.app", "Stale.app"],
    )
    monkeypatch.setattr(helper, "_rsync_copy", _copy_rsync)
    real_replace = Path.replace

    def _fail_promotion_and_rollback(path: Path, destination: str | Path) -> Path:
        if (
            path.name == "Bad.app"
            and "staged" in path.parts
            and Path(destination) == target_directory / "Bad.app"
        ):
            raise OSError("injected promotion failure")
        if (
            path.name == "Good.app"
            and "backups" in path.parts
            and Path(destination) == good
        ):
            raise OSError("injected rollback failure")
        return real_replace(path, destination)

    monkeypatch.setattr(Path, "replace", _fail_promotion_and_rollback)

    with pytest.raises(OSError, match="injected promotion failure"):
        helper._system_applications(payload)

    assert not good.exists()
    assert _marker(bad) == "old-bad"
    assert _marker(stale) == "old-stale"
    assert _manifest_names(state_file) == ("Good.app\nBad.app\nStale.app\n")
    (transaction,) = _transaction_directories(target_directory)
    assert _marker(transaction / "backups" / "0" / "Good.app") == "old-good"
    assert _marker(transaction / "staged" / "0" / "Good.app") == "new-good"
    output = capsys.readouterr().err
    assert "OSError: injected promotion failure" in output
    assert "rollback is incomplete" in output
    assert "injected rollback failure" in output
    assert str(transaction) in output
    assert str(state_file) in output


def test_stale_deletion_failure_restores_current_apps_and_keeps_stale_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = _app(tmp_path / "source", "Good.app", "new-good")
    good = _app(tmp_path / "Applications", "Good.app", "old-good")
    stale = _app(tmp_path / "Applications", "Stale.app", "old-stale")
    payload, target_directory, state_file = _payload(
        tmp_path,
        entries=[_entry(source)],
        previous_manifest=["Good.app", "Stale.app"],
    )
    monkeypatch.setattr(helper, "_rsync_copy", _copy_rsync)
    real_replace = Path.replace

    def _fail_stale_deletion(path: Path, destination: str | Path) -> Path:
        if path == stale and "backups" in Path(destination).parts:
            raise OSError("injected stale-deletion failure")
        return real_replace(path, destination)

    monkeypatch.setattr(Path, "replace", _fail_stale_deletion)

    with pytest.raises(OSError, match="injected stale-deletion failure"):
        helper._system_applications(payload)

    assert _marker(good) == "old-good"
    assert _marker(stale) == "old-stale"
    assert _manifest_names(state_file) == "Good.app\nStale.app\n"
    assert _transaction_directories(target_directory) == []


def test_manifest_publication_failure_restores_current_and_stale_apps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = _app(tmp_path / "source", "Good.app", "new-good")
    good = _app(tmp_path / "Applications", "Good.app", "old-good")
    stale = _app(tmp_path / "Applications", "Stale.app", "old-stale")
    source.chmod(0o555)
    good.chmod(0o555)
    stale.chmod(0o555)
    payload, target_directory, state_file = _payload(
        tmp_path,
        entries=[_entry(source)],
        previous_manifest=["Good.app", "Stale.app"],
    )
    monkeypatch.setattr(helper, "_rsync_copy", _copy_rsync)

    def _fail_manifest(_state_file: Path, _bundle_names: Sequence[str]) -> None:
        raise OSError("injected manifest-publication failure")

    monkeypatch.setattr(helper, "_write_managed_app_manifest", _fail_manifest)

    with pytest.raises(OSError, match="injected manifest-publication failure"):
        helper._system_applications(payload)

    assert _marker(good) == "old-good"
    assert _marker(stale) == "old-stale"
    assert stat.S_IMODE(good.stat().st_mode) == 0o555
    assert stat.S_IMODE(stale.stat().st_mode) == 0o555
    assert _manifest_names(state_file) == "Good.app\nStale.app\n"
    assert _transaction_directories(target_directory) == []


def test_post_commit_cleanup_failure_is_explicit_and_retains_backups(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = _app(tmp_path / "source", "Good.app", "new-good")
    good = _app(tmp_path / "Applications", "Good.app", "old-good")
    stale = _app(tmp_path / "Applications", "Stale.app", "old-stale")
    payload, target_directory, state_file = _payload(
        tmp_path,
        entries=[_entry(source)],
        previous_manifest=["Good.app", "Stale.app"],
    )
    monkeypatch.setattr(helper, "_rsync_copy", _copy_rsync)
    real_remove_path = helper._remove_path

    def _fail_cleanup(path: Path) -> None:
        if path.name.startswith(".nixcfg-app-activation-"):
            raise OSError("injected post-commit cleanup failure")
        real_remove_path(path)

    monkeypatch.setattr(helper, "_remove_path", _fail_cleanup)

    helper._system_applications(payload)

    assert _marker(good) == "new-good"
    assert not stale.exists()
    assert _manifest_names(state_file) == "Good.app\n"
    (transaction,) = _transaction_directories(target_directory)
    assert _marker(transaction / "backups" / "0" / "Good.app") == "old-good"
    assert _marker(transaction / "backups" / "1" / "Stale.app") == "old-stale"
    output = capsys.readouterr().err
    assert "injected post-commit cleanup failure" in output
    assert "transaction artifacts remain" in output


@pytest.mark.parametrize("manifest_existed", [True, False], ids=["old", "absent"])
def test_manifest_writer_interrupt_after_publish_restores_apps_and_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    manifest_existed: bool,
) -> None:
    source = _app(tmp_path / "source", "Good.app", "new-good")
    good = _app(tmp_path / "Applications", "Good.app", "old-good")
    previous_manifest = ["Good.app", "Stale.app"] if manifest_existed else []
    stale = (
        _app(tmp_path / "Applications", "Stale.app", "old-stale")
        if manifest_existed
        else None
    )
    payload, target_directory, state_file = _payload(
        tmp_path,
        entries=[_entry(source)],
        previous_manifest=previous_manifest,
    )
    previous_bytes = b"Good.app\nStale.app"
    if manifest_existed:
        state_file.write_bytes(previous_bytes)
    else:
        state_file.unlink()
    monkeypatch.setattr(helper, "_rsync_copy", _copy_rsync)
    real_write_manifest = helper._write_managed_app_manifest

    def _publish_then_interrupt(
        path: Path,
        bundle_names: Sequence[str],
    ) -> None:
        real_write_manifest(path, bundle_names)
        raise KeyboardInterrupt

    monkeypatch.setattr(helper, "_write_managed_app_manifest", _publish_then_interrupt)

    with pytest.raises(KeyboardInterrupt):
        helper._system_applications(payload)

    assert _marker(good) == "old-good"
    if stale is not None:
        assert _marker(stale) == "old-stale"
        assert state_file.read_bytes() == previous_bytes
    else:
        assert not os.path.lexists(state_file)
    assert _transaction_directories(target_directory) == []


def test_incomplete_rollback_reports_manifest_restoration_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = _app(tmp_path / "source", "Good.app", "new-good")
    good = _app(tmp_path / "Applications", "Good.app", "old-good")
    stale = _app(tmp_path / "Applications", "Stale.app", "old-stale")
    payload, target_directory, state_file = _payload(
        tmp_path,
        entries=[_entry(source)],
        previous_manifest=["Good.app", "Stale.app"],
    )
    monkeypatch.setattr(helper, "_rsync_copy", _copy_rsync)
    real_write_manifest = helper._write_managed_app_manifest
    real_replace = Path.replace
    published = False

    def _publish_then_interrupt(path: Path, bundle_names: Sequence[str]) -> None:
        nonlocal published
        real_write_manifest(path, bundle_names)
        published = True
        raise KeyboardInterrupt

    def _fail_manifest_restore(path: Path, destination: str | Path) -> Path:
        if published and Path(destination) == state_file:
            raise OSError("injected manifest-restoration failure")
        return real_replace(path, destination)

    monkeypatch.setattr(helper, "_write_managed_app_manifest", _publish_then_interrupt)
    monkeypatch.setattr(Path, "replace", _fail_manifest_restore)

    with pytest.raises(KeyboardInterrupt):
        helper._system_applications(payload)

    assert _marker(good) == "old-good"
    assert _marker(stale) == "old-stale"
    assert _manifest_names(state_file) == "Good.app\n"
    (transaction,) = _transaction_directories(target_directory)
    output = capsys.readouterr().err
    assert "KeyboardInterrupt" in output
    assert "rollback is incomplete" in output
    assert f"could not restore manifest {state_file}" in output
    assert "injected manifest-restoration failure" in output
    assert str(transaction) in output


def test_target_lock_serializes_two_managers_across_processes(
    tmp_path: Path,
) -> None:
    first_source = _app(tmp_path / "source", "First.app", "new-first")
    second_source = _app(tmp_path / "source", "Second.app", "new-second")
    first_payload, target_directory, first_state = _payload(
        tmp_path,
        entries=[_entry(first_source)],
        previous_manifest=[],
    )
    second_payload = dict(first_payload)
    second_payload["entries"] = [_entry(second_source)]
    second_payload["stateName"] = "other-manager"
    second_state = Path(str(second_payload["stateDirectory"])) / "other-manager.txt"

    context = multiprocessing.get_context("spawn")
    signals = tmp_path / "signals"
    signals.mkdir()
    first_preflight = signals / "first-preflight"
    second_preflight = signals / "second-preflight"
    second_lock_attempt = signals / "second-lock-attempt"
    release_first = signals / "release-first"
    first = context.Process(
        target=_locked_activation,
        args=(first_payload,),
        kwargs={"is_first": True, "signal_directory": signals},
    )
    second = context.Process(
        target=_locked_activation,
        args=(second_payload,),
        kwargs={"is_first": False, "signal_directory": signals},
    )
    started: list[multiprocessing.Process] = []
    try:
        first.start()
        started.append(first)
        _wait_for_path(first_preflight)

        descriptor = os.open(
            target_directory / helper.APPLICATION_LOCK_NAME,
            os.O_RDWR,
        )
        try:
            with pytest.raises(BlockingIOError):
                helper.fcntl.flock(
                    descriptor,
                    helper.fcntl.LOCK_EX | helper.fcntl.LOCK_NB,
                )
        finally:
            os.close(descriptor)

        second.start()
        started.append(second)
        _wait_for_path(second_lock_attempt)
        assert not second_preflight.exists()
        release_first.touch()
        first.join(15)
        second.join(15)
        assert not first.is_alive()
        assert not second.is_alive()
        assert first.exitcode == 0
        assert second.exitcode == 0
        assert second_preflight.exists()
    finally:
        release_first.touch()
        for process in started:
            if process.is_alive():
                process.terminate()
            process.join(5)

    assert _marker(target_directory / "First.app") == "new-first"
    assert _marker(target_directory / "Second.app") == "new-second"
    assert _manifest_names(first_state) == "First.app\n"
    assert _manifest_names(second_state) == "Second.app\n"


def _crash_in_child(payload: dict[str, object], crash_point: str) -> None:
    process = multiprocessing.get_context("spawn").Process(
        target=_crash_activation,
        args=(payload, crash_point),
    )
    process.start()
    try:
        process.join(15)
        assert not process.is_alive()
        assert process.exitcode == 70
    finally:
        if process.is_alive():
            process.terminate()
        process.join(5)


def _assert_crash_state(
    crash_point: str,
    transaction: Path,
    journal: dict[str, object],
    target_directory: Path,
    state_file: Path,
) -> None:
    good = target_directory / "Good.app"
    stale = target_directory / "Stale.app"
    expected_phase = (
        "committed"
        if crash_point == "commit"
        else "publishing"
        if crash_point in {"before-publication", "manifest-publication"}
        else "prepared"
    )
    assert journal["phase"] == expected_phase
    changes = journal["changes"]
    assert isinstance(changes, list)
    current_record = changes[0]
    stale_record = changes[1]
    assert isinstance(current_record, dict)
    assert isinstance(stale_record, dict)
    if crash_point == "current-backup-rename":
        assert current_record["originalMoved"] is False
        assert _marker(transaction / "backups" / "0" / "Good.app") == "old-good"
    if crash_point == "current-backup-journal":
        assert current_record["originalMoved"] is True
    if crash_point in {
        "current-replacement-rename",
        "current-replacement-journal",
    }:
        assert current_record["originalMoved"] is True
        assert current_record["replacementMoved"] is (
            crash_point == "current-replacement-journal"
        )
        assert _marker(good) == "new-good"
    if crash_point == "stale-backup-rename":
        assert stale_record["originalMoved"] is False
        assert not stale.exists()
    if crash_point == "stale-backup-journal":
        assert stale_record["originalMoved"] is True
    if crash_point == "manifest-publication":
        assert _manifest_names(state_file) == "Good.app\n"


@pytest.mark.parametrize(
    "crash_point",
    [
        "current-backup-rename",
        "current-backup-journal",
        "current-replacement-rename",
        "current-replacement-journal",
        "stale-backup-rename",
        "stale-backup-journal",
        "before-publication",
        "manifest-publication",
        "commit",
    ],
)
def test_next_activation_recovers_each_crash_boundary_under_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    crash_point: str,
) -> None:
    source = _app(tmp_path / "source", "Good.app", "new-good")
    good = _app(tmp_path / "Applications", "Good.app", "old-good")
    stale = _app(tmp_path / "Applications", "Stale.app", "old-stale")
    payload, target_directory, state_file = _payload(
        tmp_path,
        entries=[_entry(source)],
        previous_manifest=["Good.app", "Stale.app"],
    )
    legacy = target_directory / ".nixcfg-app-activation-legacy"
    legacy.mkdir()
    (legacy / "keep.txt").write_text("unknown data", encoding="utf-8")

    _crash_in_child(payload, crash_point)

    (transaction,) = (
        path
        for path in _transaction_directories(target_directory)
        if (path / helper.APPLICATION_JOURNAL_NAME).is_file()
    )
    journal = json.loads(
        (transaction / helper.APPLICATION_JOURNAL_NAME).read_text(encoding="utf-8")
    )
    _assert_crash_state(
        crash_point,
        transaction,
        journal,
        target_directory,
        state_file,
    )

    monkeypatch.setattr(helper, "_rsync_copy", _copy_rsync)
    real_preflight = helper._preflight_managed_apps
    recovered: dict[str, object] = {}

    def _observe_recovered_state(
        entries: Sequence[helper._SystemApplicationEntryPayload],
        observed_target: Path,
    ) -> None:
        recovered["good"] = _marker(good) if good.exists() else None
        recovered["stale"] = _marker(stale) if stale.exists() else None
        recovered["manifest"] = [
            line.split(chr(9), 1)[0]
            for line in state_file.read_text(encoding="utf-8").splitlines()
        ]
        real_preflight(entries, observed_target)

    monkeypatch.setattr(helper, "_preflight_managed_apps", _observe_recovered_state)
    helper._system_applications(payload)

    committed = crash_point == "commit"
    assert recovered == {
        "good": "new-good" if committed else "old-good",
        "stale": None if committed else "old-stale",
        "manifest": ["Good.app"] if committed else ["Good.app", "Stale.app"],
    }
    assert _marker(good) == "new-good"
    assert not stale.exists()
    assert _manifest_names(state_file) == "Good.app\n"
    assert not transaction.exists()
    assert (legacy / "keep.txt").read_text(encoding="utf-8") == "unknown data"
    assert _transaction_directories(target_directory) == [legacy]


def test_unrecoverable_crash_state_stops_with_explicit_instructions(
    tmp_path: Path,
) -> None:
    source = _app(tmp_path / "source", "Good.app", "new-good")
    good = _app(tmp_path / "Applications", "Good.app", "old-good")
    _app(tmp_path / "Applications", "Stale.app", "old-stale")
    payload, target_directory, state_file = _payload(
        tmp_path,
        entries=[_entry(source)],
        previous_manifest=["Good.app", "Stale.app"],
    )
    _crash_in_child(payload, "current-backup-rename")
    (transaction,) = _transaction_directories(target_directory)
    shutil.rmtree(transaction / "backups" / "0" / "Good.app")

    with pytest.raises(RuntimeError) as exc_info:
        helper._system_applications(payload)

    message = str(exc_info.value)
    assert "cannot safely recover application transaction" in message
    assert str(transaction) in message
    assert "restore the recorded app bundles and ownership manifest manually" in message
    assert "remove only that transaction directory" in message
    assert "rerun activation" in message
    assert transaction.is_dir()
    assert not good.exists()
    assert _manifest_names(state_file) == "Good.app\nStale.app\n"


def _active_test_transaction(
    tmp_path: Path,
) -> tuple[
    dict[str, object],
    Path,
    Path,
    helper._ApplicationTransaction,
    Path,
]:
    source = _app(tmp_path / "source", "Good.app", "new-good")
    good = _app(tmp_path / "Applications", "Good.app", "old-good")
    payload, _target_directory, state_file = _payload(
        tmp_path,
        entries=[_entry(source)],
        previous_manifest=["Good.app"],
    )
    directory = good.parent / ".nixcfg-app-activation-test"
    directory.mkdir()
    staged = _app(directory / "staged" / "0", "Good.app", "new-good")
    change = helper._ApplicationChange(
        target=good,
        backup=directory / "backups" / "0" / "Good.app",
        replacement=staged,
    )
    transaction = helper._ApplicationTransaction(
        directory=directory,
        target_directory=good.parent,
        state_file=state_file,
        previous_manifest_exists=True,
        changes=[change],
    )
    transaction.previous_manifest_path.write_bytes(state_file.read_bytes())
    helper._write_application_journal(transaction)
    return payload, good.parent, state_file, transaction, good


def test_payload_and_legacy_error_paths_remain_explicit(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(TypeError, match="Expected JSON object"):
        helper._as_object_dict([], context="test")
    with pytest.raises(TypeError, match="Expected string key"):
        helper._as_object_dict({1: "value"}, context="test")

    failing_calls = [
        lambda: helper._required({}, "missing"),
        lambda: helper._required_str({"key": 1}, "key"),
        lambda: helper._required_bool({"key": 1}, "key"),
        lambda: helper._required_str_list({"key": [1]}, "key"),
        lambda: helper._required_entries({"entries": {}}),
        lambda: helper._required_entries({"entries": [1]}),
    ]
    for call in failing_calls:
        with pytest.raises(SystemExit) as exc_info:
            call()
        assert exc_info.value.code == 2

    with pytest.raises(SystemExit) as exc_info:
        helper._load_payload(["program"])
    assert exc_info.value.code == 2
    invalid_payload = tmp_path / "invalid.json"
    invalid_payload.write_text("[]", encoding="utf-8")
    with pytest.raises(SystemExit) as exc_info:
        helper._load_payload(["program", "system-applications", str(invalid_payload)])
    assert exc_info.value.code == 2

    profile_payload = tmp_path / "profile.json"
    profile_payload.write_text(
        json.dumps({
            "label": "test profile",
            "managedBundleNames": [],
            "packagePaths": [],
        }),
        encoding="utf-8",
    )
    assert (
        helper.main(["program", "profile-bundle-leak-audit", str(profile_payload)]) == 0
    )
    unknown_payload = tmp_path / "unknown.json"
    unknown_payload.write_text("{}", encoding="utf-8")
    assert helper.main(["program", "unknown", str(unknown_payload)]) == 2
    assert "unknown command: unknown" in capsys.readouterr().err


def test_small_legacy_io_and_install_branches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    helper._remove_path(tmp_path / "missing")
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    helper._remove_path(fifo)
    assert not fifo.exists()

    state_directory = tmp_path / "state"
    state_directory.mkdir()
    manager = state_directory / "manager.txt"
    manager.write_text("", encoding="utf-8")
    (state_directory / "other.txt").write_text("Other.app\n", encoding="utf-8")
    (state_directory / "owner.txt").write_text("Needle.app\n", encoding="utf-8")
    assert helper._app_in_other_manifests("Needle.app", state_directory, manager)
    assert not helper._app_in_other_manifests("Missing.app", state_directory, manager)

    package = tmp_path / "package"
    package.mkdir()
    helper._profile_bundle_leak_audit({
        "label": "test profile",
        "managedBundleNames": [],
        "packagePaths": [str(package)],
    })
    applications = package / "Applications"
    applications.mkdir()
    (applications / "Not-a-bundle.app").write_text("file", encoding="utf-8")
    helper._profile_bundle_leak_audit({
        "label": "test profile",
        "managedBundleNames": [],
        "packagePaths": [str(package)],
    })

    with pytest.raises(SystemExit) as exc_info:
        helper._install_managed_app(
            bundle_name="Missing.app",
            mode="copy",
            source_path=str(tmp_path / "missing-source"),
            target_directory=tmp_path / "installed",
            rsync_path="/rsync",
            writable=False,
        )
    assert exc_info.value.code == 1

    source = _app(tmp_path / "source", "Link.app", "new")
    old_source = _app(tmp_path / "old", "Old.app", "old")
    destination_directory = tmp_path / "destination"
    destination_directory.mkdir()
    destination = destination_directory / "Link.app"
    destination.symlink_to(old_source, target_is_directory=True)
    helper._install_managed_app(
        bundle_name="Link.app",
        mode="symlink",
        source_path=str(source),
        target_directory=destination_directory,
        rsync_path="/rsync",
        writable=False,
    )
    assert destination.resolve() == source.resolve()

    monkeypatch.setattr(
        helper.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 23),
    )
    with pytest.raises(SystemExit) as exc_info:
        helper._rsync_copy(source, destination, rsync_path="/rsync", writable=False)
    assert exc_info.value.code == 23


def test_lock_acquisition_failure_closes_its_descriptor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    real_close = os.close
    closed: list[int] = []

    def _record_close(descriptor: int) -> None:
        closed.append(descriptor)
        real_close(descriptor)

    monkeypatch.setattr(helper.os, "close", _record_close)
    monkeypatch.setattr(
        helper.fcntl,
        "flock",
        lambda *_args: (_ for _ in ()).throw(KeyboardInterrupt),
    )
    with pytest.raises(KeyboardInterrupt):
        helper._acquire_application_lock(tmp_path)
    assert len(closed) == 1


@pytest.mark.parametrize(
    ("location", "field", "value"),
    [
        ("root", None, []),
        ("raw", None, "{"),
        ("journal", "version", True),
        ("journal", "version", 2),
        ("journal", "targetDirectory", None),
        ("journal", "targetDirectory", "/elsewhere"),
        ("journal", "stateFile", "relative.txt"),
        ("journal", "stateFile", "/tmp/manifest"),
        ("journal", "stateFile", "inside-transaction"),
        ("journal", "phase", "unknown"),
        ("journal", "changes", {}),
        ("journal", "previousManifestExists", "yes"),
        ("item", None, 1),
        ("change", "replacement", 1),
        ("change", "target", "/outside/Target.app"),
        ("change", "target", "parent"),
        ("change", "backup", "/outside/Backup.app"),
        ("change", "replacement", "/outside/Replacement.app"),
        ("change", "originalExists", "yes"),
        ("change", "originalMode", "read-only"),
        ("change", "originalMode", 0o10000),
        ("duplicate", None, None),
    ],
)
def test_invalid_journals_fail_closed_with_recovery_instructions(
    tmp_path: Path,
    location: str,
    field: str | None,
    value: object,
) -> None:
    payload, target_directory, _state_file, transaction, good = (
        _active_test_transaction(tmp_path)
    )
    journal = json.loads(transaction.journal_path.read_text(encoding="utf-8"))
    document = journal
    raw: str | None = None
    if location == "raw":
        raw = str(value)
    elif location == "root":
        document = value
    elif location == "item":
        journal["changes"] = [value]
    elif location == "duplicate":
        journal["changes"].append(dict(journal["changes"][0]))
    else:
        container = journal if location == "journal" else journal["changes"][0]
        if location == "change" and field == "target" and value == "parent":
            value = str(target_directory / "..")
        if (
            location == "journal"
            and field == "stateFile"
            and value == "inside-transaction"
        ):
            value = str(transaction.directory / "state.txt")
        assert field is not None
        container[field] = value
    transaction.journal_path.write_text(
        raw if raw is not None else json.dumps(document),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError) as exc_info:
        helper._system_applications(payload)

    assert "cannot safely recover application transaction" in str(exc_info.value)
    assert "rerun activation" in str(exc_info.value)
    assert _marker(good) == "old-good"
    assert transaction.directory.is_dir()


def test_recovery_record_failure_retains_reconciled_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload, _target_directory, _state_file, transaction, good = (
        _active_test_transaction(tmp_path)
    )
    real_write_journal = helper._write_application_journal

    def _fail_recovered_record(current: helper._ApplicationTransaction) -> None:
        if current.phase == "recovered":
            raise OSError("injected recovery-record failure")
        real_write_journal(current)

    monkeypatch.setattr(helper, "_write_application_journal", _fail_recovered_record)
    with pytest.raises(RuntimeError, match="could not record completed recovery"):
        helper._system_applications(payload)
    assert _marker(good) == "old-good"
    assert transaction.directory.is_dir()


def test_exception_rollback_reports_recovery_record_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = _app(tmp_path / "source", "Good.app", "new-good")
    good = _app(tmp_path / "Applications", "Good.app", "old-good")
    payload, target_directory, _state_file = _payload(
        tmp_path,
        entries=[_entry(source)],
        previous_manifest=["Good.app"],
    )
    monkeypatch.setattr(helper, "_rsync_copy", _copy_rsync)
    real_write_journal = helper._write_application_journal

    def _fail_recovered_record(current: helper._ApplicationTransaction) -> None:
        if current.phase == "recovered":
            raise OSError("injected rollback-record failure")
        real_write_journal(current)

    monkeypatch.setattr(helper, "_write_application_journal", _fail_recovered_record)
    monkeypatch.setattr(
        helper,
        "_write_managed_app_manifest",
        lambda *_args: (_ for _ in ()).throw(OSError("injected publication failure")),
    )
    with pytest.raises(OSError, match="injected publication failure"):
        helper._system_applications(payload)
    assert _marker(good) == "old-good"
    assert "could not record completed rollback" in capsys.readouterr().err
    assert len(_transaction_directories(target_directory)) == 1


def test_commit_record_failure_never_rolls_back_published_apps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = _app(tmp_path / "source", "Good.app", "new-good")
    good = _app(tmp_path / "Applications", "Good.app", "old-good")
    payload, target_directory, state_file = _payload(
        tmp_path,
        entries=[_entry(source)],
        previous_manifest=["Good.app"],
    )
    monkeypatch.setattr(helper, "_rsync_copy", _copy_rsync)
    real_write_journal = helper._write_application_journal

    def _fail_commit_record(current: helper._ApplicationTransaction) -> None:
        if current.phase == "committed":
            raise OSError("injected commit-record failure")
        real_write_journal(current)

    monkeypatch.setattr(helper, "_write_application_journal", _fail_commit_record)
    with pytest.raises(OSError, match="injected commit-record failure"):
        helper._system_applications(payload)
    assert _marker(good) == "new-good"
    assert _manifest_names(state_file) == "Good.app\n"
    assert len(_transaction_directories(target_directory)) == 1


def test_prepublication_failure_restores_previously_absent_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = _app(tmp_path / "source", "Good.app", "new-good")
    good = _app(tmp_path / "Applications", "Good.app", "old-good")
    payload, target_directory, state_file = _payload(
        tmp_path,
        entries=[_entry(source)],
        previous_manifest=[],
    )
    state_file.unlink()
    monkeypatch.setattr(helper, "_rsync_copy", _copy_rsync)
    monkeypatch.setattr(
        helper,
        "_write_managed_app_manifest",
        lambda *_args: (_ for _ in ()).throw(OSError("injected publication failure")),
    )
    with pytest.raises(OSError, match="injected publication failure"):
        helper._system_applications(payload)
    assert _marker(good) == "old-good"
    assert not state_file.exists()
    assert _transaction_directories(target_directory) == []


def test_apply_change_rejects_live_or_staging_drift(tmp_path: Path) -> None:
    plain_file = tmp_path / "plain-file"
    plain_file.write_text("contents", encoding="utf-8")
    assert helper._make_directory_renameable(plain_file) is None

    target = _app(tmp_path / "live", "Target.app", "old")
    replacement = _app(tmp_path / "staged", "Target.app", "new")
    change = helper._ApplicationChange(
        target=target,
        backup=tmp_path / "backup" / "Target.app",
        replacement=replacement,
    )
    transaction = helper._ApplicationTransaction(
        directory=tmp_path / "transaction",
        target_directory=target.parent,
        state_file=tmp_path / "state.txt",
        previous_manifest_exists=False,
        changes=[change],
    )
    shutil.rmtree(target)
    with pytest.raises(OSError, match="changed after transaction preparation"):
        helper._apply_application_change(change, transaction)

    absent_target = tmp_path / "absent-live" / "Target.app"
    absent_target.parent.mkdir()
    missing_replacement = _app(tmp_path / "missing", "Target.app", "new")
    missing_change = helper._ApplicationChange(
        target=absent_target,
        backup=tmp_path / "absent-backup" / "Target.app",
        replacement=missing_replacement,
    )
    missing_transaction = helper._ApplicationTransaction(
        directory=tmp_path / "missing-transaction",
        target_directory=absent_target.parent,
        state_file=tmp_path / "missing-state.txt",
        previous_manifest_exists=False,
        changes=[missing_change],
    )
    shutil.rmtree(missing_replacement)
    with pytest.raises(OSError, match="staged app disappeared before promotion"):
        helper._apply_application_change(missing_change, missing_transaction)


@pytest.mark.parametrize(
    "scenario",
    ["absent", "unexpected-backup", "target-and-backup", "both-missing", "symlink"],
)
def test_restore_stale_change_covers_every_artifact_pattern(
    tmp_path: Path,
    scenario: str,
) -> None:
    target = tmp_path / "Stale.app"
    backup = tmp_path / "Backup.app"
    source: Path | None = None
    if scenario in {"unexpected-backup", "target-and-backup"}:
        _app(tmp_path, backup.name, "old")
    if scenario == "target-and-backup":
        _app(tmp_path, target.name, "unexpected")
    if scenario == "symlink":
        source = _app(tmp_path / "source", "Original.app", "old")
        target.symlink_to(source, target_is_directory=True)
    change = helper._ApplicationChange(
        target=target,
        backup=backup,
        replacement=None,
    )
    if scenario == "both-missing":
        change.original_exists = True

    expected_errors = {
        "unexpected-backup": "unexpected backup",
        "target-and-backup": "both target and backup exist",
        "both-missing": "both missing",
    }
    if scenario in expected_errors:
        with pytest.raises(OSError, match=expected_errors[scenario]):
            helper._restore_application_change(change)
    else:
        helper._restore_application_change(change)
        if scenario == "symlink":
            assert source is not None
            assert target.resolve() == source.resolve()
        else:
            assert not target.exists()


@pytest.mark.parametrize(
    "scenario",
    [
        "initial",
        "promoted-directory",
        "promoted-file",
        "unexpected-backup",
        "target-and-staged",
        "missing-both",
    ],
)
def test_restore_originally_absent_change_covers_every_artifact_pattern(
    tmp_path: Path,
    scenario: str,
) -> None:
    target = tmp_path / "Target.app"
    backup = tmp_path / "Backup.app"
    replacement = tmp_path / "Staged.app"
    if scenario in {"promoted-directory", "target-and-staged"}:
        _app(tmp_path, target.name, "new")
    if scenario == "promoted-file":
        target.write_text("new", encoding="utf-8")
    if scenario in {"initial", "unexpected-backup", "target-and-staged"}:
        _app(tmp_path, replacement.name, "new")
    if scenario == "unexpected-backup":
        _app(tmp_path, backup.name, "unexpected")
    change = helper._ApplicationChange(
        target=target,
        backup=backup,
        replacement=replacement,
    )
    change.original_exists = False
    if scenario == "promoted-directory":
        change.replacement_mode = 0o555

    expected_errors = {
        "unexpected-backup": "unexpected backup",
        "target-and-staged": "unrecorded target appeared",
        "missing-both": "promoted app and its staged replacement are missing",
    }
    if scenario in expected_errors:
        with pytest.raises(OSError, match=expected_errors[scenario]):
            helper._restore_application_change(change)
        return

    helper._restore_application_change(change)
    assert not target.exists()
    if scenario == "initial":
        assert _marker(replacement) == "new"
    elif scenario == "promoted-directory":
        assert _marker(replacement) == "new"
        assert stat.S_IMODE(replacement.stat().st_mode) == 0o555
    else:
        assert replacement.read_text(encoding="utf-8") == "new"


@pytest.mark.parametrize("scenario", ["all-three", "incomplete", "symlink-untouched"])
def test_restore_existing_change_handles_remaining_artifact_patterns(
    tmp_path: Path,
    scenario: str,
) -> None:
    target = tmp_path / "Target.app"
    backup = tmp_path / "Backup.app"
    replacement = tmp_path / "Staged.app"
    source: Path | None = None
    if scenario == "all-three":
        _app(tmp_path, target.name, "unexpected")
        _app(tmp_path, backup.name, "old")
        _app(tmp_path, replacement.name, "new")
    elif scenario == "incomplete":
        _app(tmp_path, backup.name, "old")
    else:
        source = _app(tmp_path / "source", "Original.app", "old")
        target.symlink_to(source, target_is_directory=True)
        _app(tmp_path, replacement.name, "new")
    change = helper._ApplicationChange(
        target=target,
        backup=backup,
        replacement=replacement,
    )
    if scenario == "incomplete":
        change.original_exists = True

    if scenario == "symlink-untouched":
        helper._restore_application_change(change)
        assert source is not None
        assert target.resolve() == source.resolve()
    else:
        expected_error = (
            "target, backup, and staged app all exist"
            if scenario == "all-three"
            else "all live and staged copies are incomplete"
        )
        with pytest.raises(OSError, match=expected_error):
            helper._restore_application_change(change)


def test_recovery_preserves_nondirectory_legacy_candidates(tmp_path: Path) -> None:
    plain_file = tmp_path / ".nixcfg-app-activation-file"
    plain_file.write_text("unknown", encoding="utf-8")
    link = tmp_path / ".nixcfg-app-activation-link"
    link.symlink_to(plain_file)

    helper._recover_application_transactions(tmp_path)

    assert plain_file.read_text(encoding="utf-8") == "unknown"
    assert link.is_symlink()


def test_register_application_bundles_covers_every_promoted_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Registration fans out through a pool without dropping or repeating a bundle."""
    registered: list[Path] = []

    def _record(app_path: Path) -> None:
        registered.append(app_path)

    monkeypatch.setattr(helper, "_refresh_launch_services_registration", _record)
    bundles = [tmp_path / name for name in ("A.app", "B.app", "C.app")]

    helper._register_application_bundles(bundles)

    assert sorted(registered) == sorted(bundles)


def test_register_application_bundles_does_nothing_without_bundles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A no-op activation must not spawn any registration process."""

    def _unexpected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("registration must not run for an empty bundle list")

    monkeypatch.setattr(helper, "_refresh_launch_services_registration", _unexpected)

    helper._register_application_bundles([])


def test_changed_application_entries_reports_only_unsatisfied_bundles(
    tmp_path: Path,
) -> None:
    """Current bundles are skipped and changed bundles keep declaration order."""
    sources = tmp_path / "source"
    first_missing = _app(sources, "First.app", "fresh")
    current = _app(sources, "Current.app", "same")
    second_missing = _app(sources, "Second.app", "fresh")
    target_directory = tmp_path / "Applications"
    _app(target_directory, "Current.app", "same")
    recorded_sources = {"Current.app": str(current)}

    changed = helper._changed_application_entries(
        [_entry(first_missing), _entry(current), _entry(second_missing)],
        target_directory=target_directory,
        recorded_sources=recorded_sources,
        rsync_path="/rsync",
        writable=False,
    )

    assert changed == [_entry(first_missing), _entry(second_missing)]


def test_changed_application_entries_is_empty_for_no_entries(
    tmp_path: Path,
) -> None:
    """An empty declaration needs no comparison work."""
    assert (
        helper._changed_application_entries(
            [],
            target_directory=tmp_path,
            recorded_sources={},
            rsync_path="/rsync",
            writable=False,
        )
        == []
    )


def test_post_commit_metadata_covers_only_promoted_bundles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """LaunchServices and Spotlight work follows what changed, not the managed set."""
    current_source = _app(tmp_path / "source-current", "Kept.app", "same")
    promoted_source = _app(tmp_path / "source-new", "Fresh.app", "new")
    payload, target_directory, state_file = _payload(
        tmp_path,
        entries=[_entry(current_source), _entry(promoted_source)],
        previous_manifest=["Kept.app"],
    )
    _app(target_directory, "Kept.app", "same")
    state_file.write_text(f"Kept.app\t{current_source}\n", encoding="utf-8")
    registered: list[Path] = []
    refreshed: list[list[Path]] = []

    monkeypatch.setattr(helper, "_rsync_copy", _copy_rsync)
    monkeypatch.setattr(
        helper,
        "_register_application_bundles",
        lambda paths, **_kwargs: registered.extend(paths),
    )
    monkeypatch.setattr(
        helper,
        "_refresh_spotlight_metadata",
        lambda paths, **_kwargs: refreshed.append(list(paths)),
    )

    helper._system_applications(payload)

    promoted = target_directory / "Fresh.app"
    assert registered == [promoted]
    assert refreshed == [[promoted]]


def test_bundle_is_current_rejects_a_symlink_for_copy_mode(tmp_path: Path) -> None:
    """A copied declaration never accepts a symlinked live bundle as current."""
    source = _app(tmp_path / "source", "Good.app", "fresh")
    target_directory = tmp_path / "Applications"
    target_directory.mkdir()
    (target_directory / "Good.app").symlink_to(source)

    assert not helper._bundle_is_current(
        _entry(source),
        target_directory / "Good.app",
        recorded_source=str(source),
        rsync_path="/rsync",
        writable=False,
    )
