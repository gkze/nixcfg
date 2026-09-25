"""Integrity and atomicity of the per-run SQLite candidate store."""

import asyncio
import sqlite3
from pathlib import Path

import pytest

from lib.tests._update_workspace_helpers import init_update_workspace_repo
from lib.update import durable
from lib.update.cli_options import UpdateOptions
from lib.update.config import resolve_config
from lib.update.persistence import IsolatedUpdateWorkspace, UpdateWorkspaceError
from lib.update.run_store import RunStore


def test_store_roundtrip_deduplicates_and_rolls_back(tmp_path: Path) -> None:
    """Snapshots commit as units and metadata failures do not corrupt prior values."""
    store = RunStore(tmp_path / "run")
    files = {"a": (b"bytes", 0o644, False), "link": (b"a", 0o755, True), "gone": None}
    identity = store.save_snapshot(files)
    assert store.save_snapshot(files) == identity
    assert store.load_snapshot(identity) == files
    store.write("request", {"original": True})
    with pytest.raises(TypeError):
        store.write("request", object())
    assert store.read("request") == {"original": True}
    with store.connect() as connection:
        assert connection.execute("SELECT count(*) FROM update_content").fetchone() == (
            2,
        )
    readonly = RunStore(tmp_path / "run", readonly=True)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        readonly.write("request", {})
    with pytest.raises(FileNotFoundError):
        store.read("missing")


@pytest.mark.parametrize(
    "corruption", ["missing", "manifest", "content", "missing-content"]
)
def test_checkpoint_corruption_is_rejected(tmp_path: Path, corruption: str) -> None:
    """No unverified persisted bytes are handed back to a workspace."""
    store = RunStore(tmp_path)
    identity = store.save_snapshot({"file": (b"original", 0o644, False)})
    with store.connect() as connection:
        if corruption == "manifest":
            connection.execute("UPDATE update_snapshots SET manifest = '{}' ")
        elif corruption == "content":
            connection.execute("UPDATE update_content SET content = ?", (b"changed",))
        elif corruption == "missing-content":
            connection.execute("DELETE FROM update_content")
        else:
            identity = "absent"
    with pytest.raises(ValueError, match="Missing or corrupt"):
        store.load_snapshot(identity)


def test_checkpoint_restores_files_modes_links_and_deletions(tmp_path: Path) -> None:
    """A recreated workspace restores candidate state, while the live baseline stays dirty."""
    root = tmp_path / "repo"
    init_update_workspace_repo(root)
    (root / "tracked.txt").write_text("dirty")
    store = RunStore(tmp_path / "run")
    with IsolatedUpdateWorkspace(root, run_store=store) as workspace:
        (workspace.root / "tracked.txt").write_text("candidate")
        (workspace.root / "tracked.txt").chmod(0o755)
        (workspace.root / "new-link").symlink_to("tracked.txt")
        (workspace.root / "nested/tracked.txt").unlink()
        checkpoint = workspace.checkpoint()
        (workspace.root / "tracked.txt").write_text("later")
        workspace.restore_checkpoint(checkpoint)
        assert (workspace.root / "tracked.txt").read_text() == "candidate"
    with IsolatedUpdateWorkspace(root, run_store=store) as workspace:
        (workspace.root / "unexpected").write_text("discarded")
        workspace.restore_checkpoint(checkpoint)
        assert (workspace.root / "tracked.txt").read_text() == "candidate"
        assert (workspace.root / "tracked.txt").stat().st_mode & 0o777 == 0o755
        assert (workspace.root / "new-link").is_symlink()
        assert not (workspace.root / "nested/tracked.txt").exists()
        assert not (workspace.root / "unexpected").exists()
        assert workspace.baseline_content("tracked.txt") == b"dirty"
    assert (root / "tracked.txt").read_text() == "dirty"


def test_restoration_rejects_invalid_paths_before_any_writes(tmp_path: Path) -> None:
    """Content addressing does not grant authority to escape the repository."""
    root = tmp_path / "repo"
    init_update_workspace_repo(root)
    store = RunStore(tmp_path / "run")
    snapshot = store.save_snapshot({"../outside": (b"bad", 0o644, False)})
    with IsolatedUpdateWorkspace(root, run_store=store) as workspace:
        with pytest.raises(ValueError, match="repository-relative"):
            workspace.restore_checkpoint(snapshot)
        assert not (workspace.root.parent / "outside").exists()
    store.write("baseline", [])
    with (
        pytest.raises(UpdateWorkspaceError, match="Invalid durable update baseline"),
        IsolatedUpdateWorkspace(root, run_store=store),
    ):
        pytest.fail("invalid baseline was accepted")


def test_workspace_without_store_rejects_checkpointing(tmp_path: Path) -> None:
    """Durability is never silently implied for a direct ephemeral caller."""
    root = tmp_path / "repo"
    init_update_workspace_repo(root)
    with IsolatedUpdateWorkspace(root) as workspace:
        with pytest.raises(UpdateWorkspaceError, match="no durable run store"):
            workspace.checkpoint()
        with pytest.raises(UpdateWorkspaceError, match="no durable run store"):
            workspace.restore_checkpoint("missing")


def test_runtime_identity_covers_source_platform_and_dependencies(
    tmp_path: Path,
) -> None:
    """Recovery rejects runtime changes while allowing source-metadata changes."""
    from lib.update.paths import get_repo_root

    root = get_repo_root()
    (tmp_path / "pyproject.toml").write_bytes((root / "pyproject.toml").read_bytes())
    (tmp_path / "lib").mkdir()
    (tmp_path / "packages").mkdir()
    (tmp_path / "overlays").mkdir()
    (tmp_path / "nixcfg.py").write_text("# cli")
    (tmp_path / "uv.lock").write_text("dependencies")
    identity = durable.runtime_identity(tmp_path)
    (tmp_path / "packages/sources.json").write_text("{}")
    assert durable.runtime_identity(tmp_path) == identity
    (tmp_path / "lib/new.py").write_text("# updater")
    assert durable.runtime_identity(tmp_path) != identity


def test_changed_step_identity_and_out_of_session_execution_fail_closed() -> None:
    """A result for other inputs cannot satisfy a validation step."""
    assert durable.checkpoint_sync("same", lambda: 42) == 42
    with pytest.raises(ValueError, match="Durable step changed"):
        durable._checked("new", ("old", 42))
    with pytest.raises(RuntimeError, match="active update session"):
        asyncio.run(durable.supervise(lambda: asyncio.sleep(0)))


@pytest.mark.parametrize(
    "failure", ["missing", "invalid", "version", "root", "runtime", "override"]
)
def test_resume_rejects_unknown_or_changed_contracts(
    tmp_path: Path, failure: str
) -> None:
    """Stored commands cannot be silently reinterpreted after runtime or policy drift."""
    from dataclasses import replace

    from pydantic import TypeAdapter

    store = RunStore(tmp_path / "run")
    request = durable.RunRequest(
        tmp_path, "runtime", UpdateOptions(check=True), resolve_config()
    )
    if failure == "invalid":
        store.write("request", [])
    elif failure != "missing":
        request = replace(
            request,
            **{
                "version": {"schema": 2},
                "root": {"root": tmp_path / "other"},
                "runtime": {"runtime": "old"},
                "override": {},
            }[failure],
        )
        store.write(
            "request", TypeAdapter(durable.RunRequest).dump_python(request, mode="json")
        )
    opts = UpdateOptions(resume="run", no_input=failure == "override")
    with pytest.raises(durable.ResumeError):
        durable._resume_request(opts, store, tmp_path, "runtime")


def test_resume_preserves_policy_and_allows_presentation_changes(
    tmp_path: Path,
) -> None:
    """Presentation controls do not turn a recorded check into a live promotion."""
    from pydantic import TypeAdapter

    store = RunStore(tmp_path / "run")
    request = durable.RunRequest(
        tmp_path, "runtime", UpdateOptions(check=True), resolve_config()
    )
    store.write(
        "request", TypeAdapter(durable.RunRequest).dump_python(request, mode="json")
    )
    resumed = durable._resume_request(
        UpdateOptions(resume="run", json=True), store, tmp_path, "runtime"
    )
    assert resumed.options.check
    assert resumed.options.json
    assert resumed.config == request.config


def test_named_run_rejects_changed_inputs_and_concurrent_ownership(
    tmp_path: Path,
) -> None:
    """An idempotency key cannot be repurposed or overwritten by another process."""
    from dataclasses import replace

    from filelock import FileLock
    from pydantic import TypeAdapter

    config = replace(resolve_config(), run_log_dir=tmp_path / "runs")
    opts = UpdateOptions(run_id="update", targets=("alpha",), strict=True)
    store = RunStore(config.run_log_dir / "update")
    request = durable.RunRequest(tmp_path, "runtime", opts, config)
    store.write(
        "request", TypeAdapter(durable.RunRequest).dump_python(request, mode="json")
    )
    assert durable._resume_request(opts, store, tmp_path, "runtime").options == opts
    with pytest.raises(durable.ResumeError, match="conflicting options: targets"):
        durable._resume_request(
            replace(opts, targets=("beta",)), store, tmp_path, "runtime"
        )
    with pytest.raises(durable.ResumeError, match="either"):
        asyncio.run(durable.execute(replace(opts, resume="update"), tmp_path, config))
    with (
        FileLock(store.path.parent / ".lock"),
        pytest.raises(durable.ResumeError, match="already in use"),
    ):
        asyncio.run(durable.execute(opts, tmp_path, config))
    assert store.read("request") == TypeAdapter(durable.RunRequest).dump_python(
        request, mode="json"
    )


@pytest.mark.parametrize("run_id", ["", "../escape", ".", "absent"])
def test_invalid_resume_does_not_create_a_run(tmp_path: Path, run_id: str) -> None:
    """Run selection is validated before workspace or database creation."""
    from dataclasses import replace

    config = replace(resolve_config(), run_log_dir=tmp_path / "runs")
    with pytest.raises(durable.ResumeError):
        asyncio.run(durable.execute(UpdateOptions(resume=run_id), tmp_path, config))
    assert not config.run_log_dir.exists()


def test_supervision_rejects_closed_and_foreign_event_loops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovered work must not outlive or escape its owning resource loop."""
    calls = []

    async def operation():
        calls.append(True)

    async def check():
        run = durable.Run(
            RunStore(tmp_path),
            IsolatedUpdateWorkspace(tmp_path),
            resolve_config(),
            UpdateOptions(),
        )
        monkeypatch.setattr(durable, "_SESSION", run)
        run.closing = True
        with pytest.raises(asyncio.CancelledError):
            await durable.supervise(operation)
        run.closing = False
        foreign = asyncio.new_event_loop()
        try:
            run.loop = foreign
            with pytest.raises(RuntimeError, match="CLI event loop"):
                await durable.supervise(operation)
        finally:
            foreign.close()

    asyncio.run(check())
    assert calls == []


@pytest.mark.parametrize("json_output", [False, True])
def test_cli_reports_invalid_resume_without_creating_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], json_output: bool
) -> None:
    """Resume admission errors obey the CLI's structured and human output contracts."""
    import json

    from lib.update.cli import run_update_command

    assert run_update_command(UpdateOptions(resume="../escape", json=json_output)) == 1
    output = capsys.readouterr()
    if json_output:
        assert json.loads(output.out) == {
            "success": False,
            "error": "Run ID must be a single directory name",
        }
    else:
        assert "Run ID must be a single directory name" in output.err
    assert not (tmp_path / "update-runs").exists()


def test_durable_monitor_failure_cannot_downgrade_to_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unusable durable run directory stops admission instead of losing state silently."""
    from dataclasses import replace

    from lib.tests._run_updates_helpers import make_run_plan
    from lib.update.cli import OutputOptions, _start_run_monitor

    store = RunStore(tmp_path / "run")
    (store.path.parent / "output.log").mkdir()
    config = replace(resolve_config(), run_log=True)

    async def check():
        run = durable.Run(
            store, IsolatedUpdateWorkspace(tmp_path), config, UpdateOptions()
        )
        monkeypatch.setattr(durable, "_SESSION", run)
        with pytest.raises(IsADirectoryError):
            _start_run_monitor(
                make_run_plan(), UpdateOptions(), OutputOptions(), config
            )

    asyncio.run(check())
