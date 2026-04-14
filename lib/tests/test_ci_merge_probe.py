"""Tests for the shared-hash merge probe helper."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from lib.nix.models.sources import SourceEntry
from lib.update.ci import merge_probe


def test_get_updater_accepts_shared_flake_hash_source() -> None:
    """Shared flake-backed hash sources should be accepted."""
    updater = merge_probe._get_updater("axiom-cli")

    assert updater.hash_type == "vendorHash"
    assert updater.platform_specific is False


def test_get_updater_rejects_non_flake_or_platform_specific_sources() -> None:
    """Platform-specific and non-flake sources should be rejected by the probe."""
    with pytest.raises(RuntimeError, match="already platform-specific"):
        merge_probe._get_updater("emdash")

    with pytest.raises(TypeError, match="is not a flake-backed hash updater"):
        merge_probe._get_updater("linearis")


def test_entry_with_hash_preserves_existing_metadata() -> None:
    """Replacing the hash should keep the rest of the source metadata."""
    entry = SourceEntry.model_validate({
        "hashes": [
            {
                "hashType": "sha256",
                "hash": "sha256-old=",
                "url": "https://example.test/src.tar.gz",
            }
        ],
        "version": "2026.4.4",
        "commit": "0123456789abcdef0123456789abcdef01234567",
        "drvHash": "drv-demo",
    })

    updated = merge_probe._entry_with_hash(
        entry,
        hash_type="sha256",
        hash_value="sha256-newhashnewhashnewhashnewhashnewhashnew=",
    )

    assert updated.version == "2026.4.4"
    assert updated.input is None
    assert updated.urls is None
    assert updated.commit == "0123456789abcdef0123456789abcdef01234567"
    assert updated.drv_hash == "drv-demo"
    assert updated.to_dict()["hashes"] == [
        {
            "hash": "sha256-newhashnewhashnewhashnewhashnewhashnew=",
            "hashType": "sha256",
        }
    ]


def test_overlay_seed_root_copies_nested_tree(tmp_path: Path) -> None:
    """Seed overlays should copy nested artifact trees into the workspace."""
    seed_root = tmp_path / "seed"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (seed_root / "packages" / "demo").mkdir(parents=True)
    (seed_root / "packages" / "demo" / "sources.json").write_text(
        json.dumps({"hashes": []}),
        encoding="utf-8",
    )

    merge_probe._overlay_seed_root(seed_root, workspace)

    assert (workspace / "packages" / "demo" / "sources.json").read_text(
        encoding="utf-8"
    ) == json.dumps({"hashes": []})


def test_shared_probe_targets_exclude_platform_specific_sources() -> None:
    """The all-sources inventory should only include shared-hash sources."""
    targets = {target.source: target for target in merge_probe._shared_probe_targets()}

    assert targets["axiom-cli"].hash_type == "vendorHash"
    assert "emdash" not in targets
    assert "linearis" not in targets


def test_resolve_plan_requires_exactly_one_selector() -> None:
    """CLI planning should reject ambiguous or missing source selectors."""
    with pytest.raises(RuntimeError, match="exactly one"):
        merge_probe._resolve_plan(
            source=None,
            all_sources=False,
            platforms=merge_probe.CI_PLATFORMS,
        )

    with pytest.raises(RuntimeError, match="exactly one"):
        merge_probe._resolve_plan(
            source="axiom-cli",
            all_sources=True,
            platforms=merge_probe.CI_PLATFORMS,
        )


def test_plan_text_reports_expected_work_counts() -> None:
    """Dry-run output should show source, platform, and work totals."""
    plan = merge_probe.ProbePlan(
        targets=(
            merge_probe.ProbeTarget(source="axiom-cli", hash_type="vendorHash"),
            merge_probe.ProbeTarget(source="foo", hash_type="cargoHash"),
        ),
        platforms=("aarch64-darwin", "x86_64-linux"),
    )

    text = merge_probe._plan_text(plan)

    assert "- sources: 2" in text
    assert "- platforms: 2 (aarch64-darwin, x86_64-linux)" in text
    assert "- hash computations: 4" in text
    assert "- merge runs: 2" in text
    assert "  - axiom-cli (vendorHash)" in text
    assert "  - foo (cargoHash)" in text


def test_log_writes_prefixed_message(capsys: pytest.CaptureFixture[str]) -> None:
    """Probe logging should emit the standard stderr prefix."""
    merge_probe._log("hello")

    assert capsys.readouterr().err == "[merge-probe] hello\n"


def test_run_checked_handles_success_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Checked commands should return on success and include output on failure."""

    def _success(
        _args: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=["git"], returncode=0)

    monkeypatch.setattr(merge_probe.subprocess, "run", _success)
    merge_probe._run_checked(["git", "status"])

    def _failure(
        _args: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["git"],
            returncode=7,
            stdout="one\n",
            stderr="two\n",
        )

    monkeypatch.setattr(merge_probe.subprocess, "run", _failure)
    with pytest.raises(RuntimeError, match=r"Command failed \(7\): git status") as exc:
        merge_probe._run_checked(["git", "status"])

    message = str(exc.value)
    assert "stdout:\none" in message
    assert "stderr:\ntwo" in message

    def _stderr_only(
        _args: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["git"],
            returncode=3,
            stdout="",
            stderr="only-stderr\n",
        )

    monkeypatch.setattr(merge_probe.subprocess, "run", _stderr_only)
    with pytest.raises(RuntimeError, match="only-stderr"):
        merge_probe._run_checked(["git", "status"])

    def _stdout_only(
        _args: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["git"],
            returncode=4,
            stdout="only-stdout\n",
            stderr="",
        )

    monkeypatch.setattr(merge_probe.subprocess, "run", _stdout_only)
    with pytest.raises(RuntimeError, match="only-stdout"):
        merge_probe._run_checked(["git", "status"])


def test_overlay_seed_root_file_and_missing(tmp_path: Path) -> None:
    """Seed overlays should support single files and reject missing paths."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    seed_file = tmp_path / "flake.lock"
    seed_file.write_text("{}\n", encoding="utf-8")

    merge_probe._overlay_seed_root(seed_file, workspace)

    assert (workspace / "flake.lock").read_text(encoding="utf-8") == "{}\n"

    with pytest.raises(RuntimeError, match="does not exist"):
        merge_probe._overlay_seed_root(tmp_path / "missing", workspace)


def test_instantiate_updater_handles_unknown_and_configured_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Updater instantiation should reject unknown names and pass config when needed."""

    class NeedsConfig:
        def __init__(self, config: object) -> None:
            self.config = config

    monkeypatch.setattr(merge_probe, "ensure_updaters_loaded", lambda: None)
    monkeypatch.setattr(merge_probe, "UPDATERS", {"demo": NeedsConfig})
    monkeypatch.setattr(merge_probe, "resolve_active_config", lambda _value: "cfg")

    updater = merge_probe._instantiate_updater("demo")

    assert updater.config == "cfg"

    class NoConfig:
        def __init__(self) -> None:
            self.marker = "plain"

    monkeypatch.setattr(merge_probe, "UPDATERS", {"plain": NoConfig})
    plain = merge_probe._instantiate_updater("plain")
    assert plain.marker == "plain"

    monkeypatch.setattr(merge_probe, "UPDATERS", {})
    with pytest.raises(RuntimeError, match="Unknown source: missing"):
        merge_probe._instantiate_updater("missing")


def test_get_updater_rejects_non_flake_updater(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-flake updaters should raise a type error."""
    monkeypatch.setattr(merge_probe, "_instantiate_updater", lambda _source: object())

    with pytest.raises(TypeError, match="not a flake-backed hash updater"):
        merge_probe._get_updater("demo")


def test_resolve_plan_all_uses_shared_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    """The --all selector should use the shared target inventory."""
    targets = (merge_probe.ProbeTarget(source="axiom-cli", hash_type="vendorHash"),)
    monkeypatch.setattr(merge_probe, "_shared_probe_targets", lambda: targets)

    plan = merge_probe._resolve_plan(
        source=None,
        all_sources=True,
        platforms=("x86_64-linux",),
    )

    assert plan.targets == targets
    assert plan.platforms == ("x86_64-linux",)


def test_plan_text_omits_target_section_when_empty() -> None:
    """Empty plans should omit the target listing section."""
    text = merge_probe._plan_text(
        merge_probe.ProbePlan(targets=(), platforms=("x86_64-linux",))
    )

    assert "- targets:" not in text


def test_resolve_plan_single_source_uses_get_updater(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single-source plans should derive their hash type from the updater."""
    monkeypatch.setattr(
        merge_probe,
        "_get_updater",
        lambda _source: SimpleNamespace(hash_type="vendorHash"),
    )

    plan = merge_probe._resolve_plan(
        source="axiom-cli",
        all_sources=False,
        platforms=("aarch64-linux",),
    )

    assert plan == merge_probe.ProbePlan(
        targets=(merge_probe.ProbeTarget(source="axiom-cli", hash_type="vendorHash"),),
        platforms=("aarch64-linux",),
    )


def test_load_source_entry_reads_entry_and_reports_missing(tmp_path: Path) -> None:
    """Source entries should load from sources.json files under the workspace."""
    workspace = tmp_path / "workspace"
    source_dir = workspace / "packages" / "demo"
    source_dir.mkdir(parents=True)
    expected = SourceEntry.model_validate({
        "hashes": [],
        "input": "demo",
        "version": "main",
    })
    source_file = source_dir / "sources.json"
    source_file.write_text(json.dumps(expected.to_dict()), encoding="utf-8")

    loaded_path, loaded_entry = merge_probe._load_source_entry("demo", workspace)

    assert loaded_path == source_file
    assert loaded_entry == expected

    with pytest.raises(RuntimeError, match="No sources.json found"):
        merge_probe._load_source_entry("missing", workspace)


def test_compute_hash_and_hashes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Per-platform hash helpers should capture and aggregate computed hashes."""

    async def _fake_events(
        _events: object,
        drain: object,
        *,
        parse: object,
    ):
        drain.value = parse("sha256-demo=")
        yield object()

    monkeypatch.setattr(merge_probe, "drain_value_events", _fake_events)

    hash_value = merge_probe.asyncio.run(
        merge_probe._compute_hash("demo", platform="x86_64-linux", workspace=tmp_path)
    )

    assert hash_value == "sha256-demo="

    async def _fake_compute_hash(
        source: str,
        *,
        platform: str,
        workspace: Path,
    ) -> str:
        assert source == "demo"
        assert workspace == tmp_path
        return f"hash-{platform}"

    monkeypatch.setattr(merge_probe, "_compute_hash", _fake_compute_hash)

    hashes = merge_probe.asyncio.run(
        merge_probe._compute_hashes(
            "demo",
            platforms=("aarch64-darwin", "x86_64-linux"),
            workspace=tmp_path,
        )
    )

    assert hashes == {
        "aarch64-darwin": "hash-aarch64-darwin",
        "x86_64-linux": "hash-x86_64-linux",
    }


def test_write_artifact_entry_writes_under_relative_source_path(tmp_path: Path) -> None:
    """Artifact entries should mirror the sources.json relative path under the root."""
    workspace = tmp_path / "workspace"
    artifact_root = tmp_path / "artifacts"
    source_path = workspace / "packages" / "demo" / "sources.json"
    source_path.parent.mkdir(parents=True)
    entry = SourceEntry.model_validate({
        "hashes": [],
        "input": "demo",
        "version": "main",
    })

    merge_probe._write_artifact_entry(
        artifact_root=artifact_root,
        workspace=workspace,
        source_path=source_path,
        entry=entry,
    )

    written = artifact_root / "packages" / "demo" / "sources.json"
    assert json.loads(written.read_text(encoding="utf-8")) == entry.to_dict()


def test_cli_dry_run_bad_selector_and_run_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI should render dry runs and route validated calls into run()."""
    runner = CliRunner()
    plan = merge_probe.ProbePlan(
        targets=(merge_probe.ProbeTarget(source="axiom-cli", hash_type="vendorHash"),),
        platforms=("x86_64-linux",),
    )

    monkeypatch.setattr(merge_probe, "_resolve_plan", lambda **_kwargs: plan)
    result = runner.invoke(merge_probe.app, ["--source", "axiom-cli", "--dry-run"])
    assert result.exit_code == 0
    assert "Merge probe dry run" in result.stdout

    monkeypatch.setattr(
        merge_probe,
        "_resolve_plan",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("bad selector")),
    )
    bad = runner.invoke(merge_probe.app, ["--source", "axiom-cli"])
    assert bad.exit_code != 0
    assert "bad selector" in bad.output

    many = merge_probe.ProbePlan(
        targets=(
            merge_probe.ProbeTarget(source="axiom-cli", hash_type="vendorHash"),
            merge_probe.ProbeTarget(source="foo", hash_type="cargoHash"),
        ),
        platforms=("x86_64-linux",),
    )
    monkeypatch.setattr(merge_probe, "_resolve_plan", lambda **_kwargs: many)
    all_result = runner.invoke(merge_probe.app, ["--all"])
    assert all_result.exit_code != 0
    assert "--all currently supports --dry-run only" in all_result.output

    seen: dict[str, object] = {}
    monkeypatch.setattr(merge_probe, "_resolve_plan", lambda **_kwargs: plan)
    monkeypatch.setattr(
        merge_probe,
        "run",
        lambda **kwargs: seen.update(kwargs) or 7,
    )
    run_result = runner.invoke(
        merge_probe.app,
        [
            "--source",
            "axiom-cli",
            "--revision",
            "abc123",
            "--seed-root",
            "seed",
            "--keep-artifacts",
            "--platform",
            "linux",
        ],
    )
    assert run_result.exit_code == 7
    assert seen == {
        "source": "axiom-cli",
        "revision": "abc123",
        "seed_root": Path("seed"),
        "keep_artifacts": True,
        "platforms": ("x86_64-linux",),
    }


def test_run_cleans_up_after_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Successful probes should remove the temp worktree and artifact root."""
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    repo_root = tmp_path / "repo-root"
    repo_root.mkdir()
    workspace_path = temp_root / "repo"
    source_path = workspace_path / "packages" / "demo" / "sources.json"
    entry = SourceEntry.model_validate({
        "hashes": [],
        "input": "demo",
        "version": "main",
    })
    seen_run_checked: list[tuple[list[str], Path | None]] = []
    seen_writes: list[tuple[Path, Path, Path, SourceEntry]] = []
    seen_logs: list[str] = []
    cleaned: list[Path] = []

    monkeypatch.setattr(merge_probe.tempfile, "mkdtemp", lambda prefix: str(temp_root))
    monkeypatch.setattr(merge_probe, "get_repo_root", lambda: repo_root)
    monkeypatch.setattr(merge_probe, "_log", seen_logs.append)

    def _fake_run_checked(args: list[str], *, cwd: Path | None = None) -> None:
        seen_run_checked.append((args, cwd))
        if args[:3] == ["git", "worktree", "add"]:
            workspace_path.mkdir(parents=True)

    async def _fake_compute_hashes(
        source: str,
        *,
        platforms: tuple[str, ...],
        workspace: Path,
    ) -> dict[str, str]:
        assert source == "demo"
        assert workspace == workspace_path.resolve()
        return dict.fromkeys(
            platforms,
            "sha256-KZpnZ45k/KVjpGQC4eRBZgV3k29RIbsnjygJ3A23cF0=",
        )

    monkeypatch.setattr(merge_probe, "_run_checked", _fake_run_checked)
    monkeypatch.setattr(
        merge_probe,
        "_get_updater",
        lambda _source: SimpleNamespace(hash_type="vendorHash"),
    )
    monkeypatch.setattr(
        merge_probe,
        "_load_source_entry",
        lambda _source, _workspace: (source_path, entry),
    )
    monkeypatch.setattr(merge_probe, "_compute_hashes", _fake_compute_hashes)
    monkeypatch.setattr(
        merge_probe,
        "_write_artifact_entry",
        lambda **kwargs: seen_writes.append((
            kwargs["artifact_root"],
            kwargs["workspace"],
            kwargs["source_path"],
            kwargs["entry"],
        )),
    )
    monkeypatch.setattr(
        merge_probe.merge_sources,
        "run",
        lambda *, roots, output_root: 0,
    )
    monkeypatch.setattr(
        merge_probe.shutil,
        "rmtree",
        lambda path: cleaned.append(Path(path)),
    )

    status = merge_probe.run(
        source="demo",
        revision="HEAD",
        seed_root=None,
        keep_artifacts=False,
        platforms=("aarch64-darwin", "x86_64-linux"),
    )

    assert status == 0
    assert seen_logs[0] == f"Temp root: {temp_root}"
    assert seen_run_checked == [
        (
            ["git", "worktree", "add", "--detach", str(workspace_path), "HEAD"],
            repo_root,
        ),
        (
            ["git", "worktree", "remove", "--force", str(workspace_path)],
            repo_root,
        ),
    ]
    assert len(seen_writes) == 2
    assert cleaned == [temp_root]


def test_run_keeps_artifacts_on_merge_failure_and_exception(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Failures should preserve temp artifacts for inspection."""
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    repo_root = tmp_path / "repo-root"
    repo_root.mkdir()
    workspace_path = temp_root / "repo"
    source_path = workspace_path / "packages" / "demo" / "sources.json"
    entry = SourceEntry.model_validate({
        "hashes": [],
        "input": "demo",
        "version": "main",
    })
    seen_logs: list[str] = []
    cleaned: list[Path] = []
    overlay_calls: list[tuple[Path, Path]] = []

    monkeypatch.setattr(merge_probe.tempfile, "mkdtemp", lambda prefix: str(temp_root))
    monkeypatch.setattr(merge_probe, "get_repo_root", lambda: repo_root)
    monkeypatch.setattr(merge_probe, "_log", seen_logs.append)
    monkeypatch.setattr(
        merge_probe.shutil, "rmtree", lambda path: cleaned.append(Path(path))
    )

    def _fake_run_checked(args: list[str], *, cwd: Path | None = None) -> None:
        if args[:3] == ["git", "worktree", "add"]:
            workspace_path.mkdir(parents=True, exist_ok=True)

    async def _fake_compute_hashes(
        _source: str,
        *,
        platforms: tuple[str, ...],
        workspace: Path,
    ) -> dict[str, str]:
        assert workspace == workspace_path.resolve()
        return dict.fromkeys(
            platforms,
            "sha256-KZpnZ45k/KVjpGQC4eRBZgV3k29RIbsnjygJ3A23cF0=",
        )

    monkeypatch.setattr(merge_probe, "_run_checked", _fake_run_checked)
    monkeypatch.setattr(
        merge_probe,
        "_overlay_seed_root",
        lambda seed_root, workspace: overlay_calls.append((seed_root, workspace)),
    )
    monkeypatch.setattr(
        merge_probe,
        "_get_updater",
        lambda _source: SimpleNamespace(hash_type="vendorHash"),
    )
    monkeypatch.setattr(
        merge_probe,
        "_load_source_entry",
        lambda _source, _workspace: (source_path, entry),
    )
    monkeypatch.setattr(merge_probe, "_compute_hashes", _fake_compute_hashes)
    monkeypatch.setattr(merge_probe, "_write_artifact_entry", lambda **_kwargs: None)
    monkeypatch.setattr(
        merge_probe.merge_sources,
        "run",
        lambda *, roots, output_root: 5,
    )

    merge_status = merge_probe.run(
        source="demo",
        revision="HEAD",
        seed_root=tmp_path / "seed",
        keep_artifacts=False,
        platforms=("x86_64-linux",),
    )

    assert merge_status == 5
    assert overlay_calls == [(tmp_path / "seed", workspace_path.resolve())]
    assert cleaned == []
    assert any(log == "Merge returned 5" for log in seen_logs)
    assert any(log == f"Artifacts kept at: {temp_root}" for log in seen_logs)

    seen_logs.clear()
    monkeypatch.setattr(
        merge_probe,
        "_get_updater",
        lambda _source: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    error_status = merge_probe.run(
        source="demo",
        revision="HEAD",
        seed_root=None,
        keep_artifacts=False,
        platforms=("x86_64-linux",),
    )

    assert error_status == 1
    assert any(log == "boom" for log in seen_logs)
    assert any(log == f"Artifacts kept at: {temp_root}" for log in seen_logs)


def test_run_skips_cleanup_steps_when_workspace_and_temp_root_are_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Cleanup should tolerate already-absent workspace and temp directories."""
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    repo_root = tmp_path / "repo-root"
    repo_root.mkdir()
    workspace_path = temp_root / "repo"
    source_path = workspace_path / "packages" / "demo" / "sources.json"
    entry = SourceEntry.model_validate({
        "hashes": [],
        "input": "demo",
        "version": "main",
    })
    seen_run_checked: list[tuple[list[str], Path | None]] = []
    removed: list[Path] = []

    monkeypatch.setattr(merge_probe.tempfile, "mkdtemp", lambda prefix: str(temp_root))
    monkeypatch.setattr(merge_probe, "get_repo_root", lambda: repo_root)
    monkeypatch.setattr(merge_probe, "_log", lambda _message: None)

    def _fake_run_checked(args: list[str], *, cwd: Path | None = None) -> None:
        seen_run_checked.append((args, cwd))

    async def _fake_compute_hashes(
        _source: str,
        *,
        platforms: tuple[str, ...],
        workspace: Path,
    ) -> dict[str, str]:
        assert workspace == workspace_path.resolve()
        return dict.fromkeys(
            platforms,
            "sha256-KZpnZ45k/KVjpGQC4eRBZgV3k29RIbsnjygJ3A23cF0=",
        )

    def _fake_merge_run(*, roots: list[str], output_root: Path) -> int:
        removed.extend(Path(root) for root in roots)
        temp_root.rmdir()
        return 0

    monkeypatch.setattr(merge_probe, "_run_checked", _fake_run_checked)
    monkeypatch.setattr(
        merge_probe,
        "_get_updater",
        lambda _source: SimpleNamespace(hash_type="vendorHash"),
    )
    monkeypatch.setattr(
        merge_probe,
        "_load_source_entry",
        lambda _source, _workspace: (source_path, entry),
    )
    monkeypatch.setattr(merge_probe, "_compute_hashes", _fake_compute_hashes)
    monkeypatch.setattr(merge_probe, "_write_artifact_entry", lambda **_kwargs: None)
    monkeypatch.setattr(merge_probe.merge_sources, "run", _fake_merge_run)
    monkeypatch.setattr(
        merge_probe.shutil, "rmtree", lambda path: removed.append(Path(path))
    )

    status = merge_probe.run(
        source="demo",
        revision="HEAD",
        seed_root=None,
        keep_artifacts=False,
        platforms=("x86_64-linux",),
    )

    assert status == 0
    assert seen_run_checked == [
        (
            ["git", "worktree", "add", "--detach", str(workspace_path), "HEAD"],
            repo_root,
        )
    ]
    assert temp_root.exists() is False
