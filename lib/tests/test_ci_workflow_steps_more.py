"""Additional tests for workflow_steps command routing and helpers."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib.update.ci import workflow_certification as cert
from lib.update.ci import workflow_pr_body as wpr
from lib.update.ci import workflow_steps as ws
from lib.update.ci._workflow_analysis import WorkflowAnalysis
from lib.update.ci.flake_lock_diff import InputInfo


def _completed(
    args: list[str], *, stdout: str = "", returncode: int = 0
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout)


def _write_certification_workflow(path: Path) -> Path:
    path.write_text(
        """
name: certify
on: workflow_dispatch
jobs:
  darwin-priority-heavy:
    runs-on: macos-latest
    strategy:
      matrix:
        include:
          - package: alpha
            target: .#pkgs.aarch64-darwin.alpha
    steps:
      - run: nix build --impure ${{ matrix.target }}
  darwin-extra-heavy:
    runs-on: macos-latest
    strategy:
      matrix:
        include:
          - package: beta
            target: .#pkgs.aarch64-darwin.beta
    steps:
      - run: nix build --impure ${{ matrix.target }}
  darwin-shared:
    runs-on: macos-latest
    steps:
      - run: |
          nix run .#nixcfg -- ci cache closure \
            --mode intersection \
            --exclude-ref .#pkgs.aarch64-darwin.alpha \
            --exclude-ref .#pkgs.aarch64-darwin.beta \
            .#darwinConfigurations.argus.system \
            .#darwinConfigurations.rocinante.system
  darwin-argus:
    runs-on: macos-latest
    steps:
      - run: nix run .#nixcfg -- ci workflow build-darwin-config argus
  darwin-rocinante:
    runs-on: macos-latest
    steps:
      - run: nix run .#nixcfg -- ci workflow build-darwin-config rocinante
  linux-x86_64:
    runs-on: ubuntu-latest
    steps:
      - run: nix build .#pkgs.x86_64-linux.nixcfg
""".lstrip(),
        encoding="utf-8",
    )
    return path


def _write_existing_pr_body(path: Path) -> Path:
    ws.write_pr_body(
        output=path,
        model=ws.PRBodyModel(
            workflow_run_url="https://example.test/workflow",
            compare_url="https://example.test/compare/main...update_flake_lock_action",
        ),
    )
    return path


def _input_info(
    name: str,
    *,
    input_type: str = "github",
    owner: str = "acme",
    repo: str = "demo",
    rev: str = "abc1234",
    rev_full: str = "abc123456789",
) -> InputInfo:
    return InputInfo(
        name=name,
        type=input_type,
        owner=owner,
        repo=repo,
        rev=rev,
        rev_full=rev_full,
        date="2026-04-24",
    )


def test_workflow_pr_body_helper_edge_paths(tmp_path: Path) -> None:
    """Cover direct helper branches that full PR-body rendering normally skips."""

    def _git_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert "cwd" not in kwargs
        return _completed(args, stdout="historical\n")

    assert wpr.git_show("HEAD:flake.lock", run=_git_run, cwd=None) == "historical\n"

    def _source_file_run(
        args: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        assert "cwd" not in kwargs
        if args[:4] == ["git", "diff", "--name-only", "HEAD"]:
            return _completed(args, stdout="packages/demo/sources.json\n")
        return _completed(
            args,
            stdout="packages/other/sources.json\nREADME.md\n",
        )

    assert wpr.collect_changed_source_files(
        cwd=None,
        run=_source_file_run,
        pathspecs=("packages/*/sources.json",),
        is_sources_file_path=lambda path: path.endswith("sources.json"),
    ) == ["packages/demo/sources.json", "packages/other/sources.json"]

    github = _input_info("github-input")
    plain = _input_info(
        "plain-input",
        input_type="path",
        owner="",
        repo="",
        rev_full="",
    )
    assert wpr._flake_source_link(github).url == "https://github.com/acme/demo"
    assert wpr._flake_source_link(plain).label == "plain-input"
    assert wpr._flake_revision_link(github).url == (
        "https://github.com/acme/demo/commit/abc123456789"
    )
    assert wpr._flake_revision_link(plain).label == "abc1234"
    assert wpr._flake_compare_link(
        github, _input_info("github-input", rev_full="def456")
    ) == (
        wpr.LinkValue(
            label="Diff",
            url="https://github.com/acme/demo/compare/abc123456789...def456",
        )
    )
    assert wpr._flake_compare_link(github, plain).label == "-"

    with pytest.raises(FileNotFoundError, match="Expected flake.lock"):
        wpr.build_update_pr_body_model(
            repo_root=tmp_path,
            temp_root=tmp_path,
            options=wpr.PRBodyOptions(
                workflow_url="https://example.test/workflow",
                server_url="https://example.test",
                repository="org/repo",
                base_ref="main",
            ),
            git_show=lambda *_args, **_kwargs: "{}\n",
            collect_changes=lambda *_args: ([], [], []),
            collect_changed_source_files=list,
            run_sources_diff=lambda *_args, **_kwargs: ws.NoChangesMessage,
            no_changes_message=ws.NoChangesMessage,
        )


def test_workflow_certification_helper_error_paths() -> None:
    """Exercise malformed certification workflow metadata branches."""
    with pytest.raises(TypeError, match="non-empty string field"):
        cert.required_string_field({"name": "   "}, field="name", context="payload")

    assert cert.parse_github_timestamp("2026-04-24T12:00:00") == datetime(
        2026,
        4,
        24,
        12,
        0,
        tzinfo=UTC,
    )
    with pytest.raises(ValueError, match="Invalid GitHub timestamp"):
        cert.parse_github_timestamp("not-a-timestamp")

    assert cert._ordered_unique(["a", "a", "b"]) == ("a", "b")

    invalid_matrix = WorkflowAnalysis.from_jobs({
        "darwin-priority-heavy": {
            "strategy": {"matrix": {"include": [{"target": ""}]}},
            "steps": [],
        }
    })
    with pytest.raises(TypeError, match="non-empty string target"):
        cert._certification_matrix_targets(
            invalid_matrix,
            job_id="darwin-priority-heavy",
        )

    with pytest.raises(RuntimeError, match="exactly one shared-closure step"):
        cert._certification_shared_closure_refs(
            WorkflowAnalysis.from_jobs({"darwin-shared": {"steps": []}})
        )

    with pytest.raises(RuntimeError, match="--exclude-ref"):
        cert._certification_shared_closure_refs(
            WorkflowAnalysis.from_jobs({
                "darwin-shared": {
                    "steps": [
                        {
                            "run": (
                                "nix run .#nixcfg -- ci cache closure "
                                ".#darwinConfigurations.argus.system"
                            )
                        }
                    ]
                }
            })
        )

    with pytest.raises(RuntimeError, match="at least one flake ref"):
        cert._certification_shared_closure_refs(
            WorkflowAnalysis.from_jobs({
                "darwin-shared": {
                    "steps": [
                        {
                            "run": (
                                "nix run .#nixcfg -- ci cache closure "
                                "--exclude-ref .#darwinConfigurations.argus.system "
                                ".#darwinConfigurations.argus.system .#nixcfg"
                            )
                        }
                    ]
                }
            })
        )

    with pytest.raises(RuntimeError, match="must build exactly one darwin host"):
        cert._certification_darwin_host_targets(
            WorkflowAnalysis.from_jobs({
                "darwin-argus": {"steps": []},
                "darwin-rocinante": {
                    "steps": [
                        {
                            "run": "nix run .#nixcfg -- ci workflow build-darwin-config rocinante"
                        }
                    ]
                },
            })
        )

    with pytest.raises(RuntimeError, match="at least one nix build target"):
        cert._certification_linux_targets(
            WorkflowAnalysis.from_jobs({"linux-x86_64": {"steps": []}})
        )


def test_xcode_version_key_and_git_show_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sort Xcode app names and fallback only for missing historical files."""
    assert ws._xcode_version_key(Path("/Applications/Xcode-16.2.app")) == (16, 2)
    assert ws._xcode_version_key(Path("/Applications/Xcode.app")) == ()

    monkeypatch.setattr(
        ws,
        "_run",
        lambda _args, **_kwargs: subprocess.CompletedProcess(
            args=["git"],
            returncode=1,
            stdout="",
            stderr="fatal: path 'missing' exists on disk, but not in 'HEAD'",
        ),
    )
    assert ws._git_show("HEAD:missing") == "{}\n"


def test_json_object_rejects_non_string_keys() -> None:
    """Reject JSON objects whose runtime keys are not strings."""
    with pytest.raises(TypeError, match="Expected string keys"):
        ws._json_object({1: "x"}, context="payload")


def test_git_show_raises_for_unexpected_history_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raise explicit errors for Git failures unrelated to missing files."""
    monkeypatch.setattr(
        ws,
        "_run",
        lambda _args, **_kwargs: subprocess.CompletedProcess(
            args=["git"],
            returncode=128,
            stdout="",
            stderr="fatal: bad revision 'HEAD'",
        ),
    )

    with pytest.raises(ws.GitHistoryReadError, match="bad revision"):
        ws._git_show("HEAD:flake.lock", missing_ok=False)


def test_source_diff_pathspecs_switches_for_flat_layout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Include flat-layout pathspecs only when matching files exist."""
    pkg_root = tmp_path / "packages"
    overlay_root = tmp_path / "overlays"
    pkg_root.mkdir()
    overlay_root.mkdir()

    monkeypatch.setattr(ws, "PACKAGE_DIRS", (str(pkg_root), str(overlay_root)))
    monkeypatch.setattr(ws, "get_repo_root", lambda: tmp_path)
    assert ws._source_diff_pathspecs() == (
        ":(glob)packages/**/sources.json",
        ":(glob)overlays/**/sources.json",
    )
    (pkg_root / "demo.sources.json").write_text("{}\n", encoding="utf-8")
    assert ws._source_diff_pathspecs() == ws.SOURCES_GIT_PATHSPECS


def test_cmd_list_update_targets_imports_update_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run update target listing through dynamic import path."""
    called: dict[str, object] = {}

    class _Options:
        def __init__(self, *, list_targets: bool) -> None:
            called["list_targets"] = list_targets

    async def _run_updates(_opts: object) -> int:
        return 9

    fake_module = SimpleNamespace(UpdateOptions=_Options, run_updates=_run_updates)
    monkeypatch.setattr(ws.importlib, "import_module", lambda _name: fake_module)

    assert ws._cmd_list_update_targets() == 9
    assert called["list_targets"] is True


def test_direct_command_helpers_call_expected_subprocesses(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Run direct helper commands that just dispatch subprocess calls."""
    (tmp_path / "flake.lock").write_text(
        json.dumps({
            "nodes": {
                "root": {
                    "inputs": {
                        "alpha": "alpha-node",
                        "nh": "nh-node",
                        "nested-follow": ["alpha", "nixpkgs"],
                    }
                }
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    commands: list[list[str]] = []

    def _fake_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return _completed(args)

    monkeypatch.setattr(ws, "_run", _fake_run)

    assert ws._cmd_nix_flake_update() == 0
    assert ws._cmd_install_darwin_tools() == 0
    assert ws._cmd_prefetch_flake_inputs() == 0
    assert ws._cmd_build_darwin_config(host="argus") == 0
    assert ws._cmd_eval_darwin_lock_smoke() == 0
    assert ws._cmd_eval_darwin_full_smoke() == 0
    assert ws._cmd_smoke_check_update_app() == 0

    assert ["nix", "flake", "lock", "--update-input", "alpha"] in commands
    assert ["nix", "flake", "lock", "--update-input", "nh"] not in commands
    assert ["nix", "flake", "lock", "--update-input", "nested-follow"] not in commands
    assert ["brew", "install", "--cask", "macfuse"] in commands
    assert ["brew", "install", "1password-cli"] in commands
    assert [
        "env",
        "NIXPKGS_ALLOW_UNFREE=1",
        "nix",
        "build",
        "--impure",
        ".#darwinConfigurations.argus.system",
    ] in commands
    assert [
        "env",
        "NIXPKGS_ALLOW_UNFREE=1",
        "nix",
        "eval",
        "--json",
        "--impure",
        ".#darwinConfigurations.argus.config.home-manager.users.george.programs.nixvim.content",
    ] in commands
    assert [
        "env",
        "NIXPKGS_ALLOW_UNFREE=1",
        "nix",
        "eval",
        "--json",
        "--impure",
        ".#darwinConfigurations.rocinante.config.home-manager.users.george.programs.nixvim.content",
    ] in commands
    assert [
        "env",
        "NIXPKGS_ALLOW_UNFREE=1",
        "nix",
        "build",
        "--dry-run",
        "--impure",
        ".#darwinConfigurations.argus.system",
    ] in commands
    assert [
        "env",
        "NIXPKGS_ALLOW_UNFREE=1",
        "nix",
        "build",
        "--dry-run",
        "--impure",
        ".#darwinConfigurations.rocinante.system",
    ] in commands
    assert [
        "env",
        "NIXPKGS_ALLOW_UNFREE=1",
        "nix",
        "build",
        "--dry-run",
        "--impure",
        ".#homeConfigurations.george.activationPackage",
    ] in commands


def test_cmd_prefetch_flake_inputs_retries_and_then_continues(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Treat prefetch as best-effort cache warming with bounded retries."""
    attempts = 0
    sleeps: list[float] = []

    def _fake_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise subprocess.CalledProcessError(1, args, stderr="boom")
        return _completed(args)

    monkeypatch.setattr(ws, "_run", _fake_run)
    monkeypatch.setattr(ws.time, "sleep", lambda delay: sleeps.append(delay))

    assert ws._cmd_prefetch_flake_inputs() == 0
    assert attempts == 3
    assert sleeps == [1.0, 2.0]
    stderr = capsys.readouterr().err
    assert "retrying in 1.0s" in stderr
    assert "retrying in 2.0s" in stderr


def test_cmd_prefetch_flake_inputs_warns_after_exhausting_retries(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Continue after repeated prefetch failures because builds are authoritative."""
    attempts = 0
    sleeps: list[float] = []

    def _fake_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal attempts
        attempts += 1
        raise subprocess.CalledProcessError(1, args, stderr="boom")

    monkeypatch.setattr(ws, "_run", _fake_run)
    monkeypatch.setattr(ws.time, "sleep", lambda delay: sleeps.append(delay))

    assert ws._cmd_prefetch_flake_inputs() == 0
    assert attempts == 3
    assert sleeps == [1.0, 2.0]
    stderr = capsys.readouterr().err
    assert "failed after 3 attempts; continuing" in stderr


def test_cmd_prefetch_flake_inputs_handles_zero_attempt_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allow tests to exercise the loop-exhausted branch explicitly."""
    monkeypatch.setattr(ws, "_PREFETCH_FLAKE_INPUTS_ATTEMPTS", 0)
    monkeypatch.setattr(
        ws,
        "_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("prefetch command should not run")
        ),
    )

    assert ws._cmd_prefetch_flake_inputs() == 0


def test_cmd_validate_bun_lock_reports_success_and_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Render Bun lock validation results to stdout and stderr."""
    monkeypatch.setattr(
        ws, "validate_source_package_exact_versions", lambda _path: None
    )
    assert ws._cmd_validate_bun_lock(lock_file=Path("bun.lock")) == 0
    assert "Validated Bun source package overrides" in capsys.readouterr().out

    def _fail(_path: Path) -> None:
        msg = "bun lock mismatch"
        raise RuntimeError(msg)

    monkeypatch.setattr(ws, "validate_source_package_exact_versions", _fail)
    assert ws._cmd_validate_bun_lock(lock_file=Path("bun.lock")) == 1
    assert "bun lock mismatch" in capsys.readouterr().err


def test_cmd_prepare_bun_lock_reports_validation_relock_and_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Render Bun lock preparation results to stdout and stderr."""
    monkeypatch.setattr(
        ws,
        "prepare_source_package_lock",
        lambda *_args, **_kwargs: False,
    )
    assert (
        ws._cmd_prepare_bun_lock(
            workspace_root=Path(),
            lock_file=Path("bun.lock"),
            bun_executable="bun",
        )
        == 0
    )
    assert "Validated Bun source package overrides" in capsys.readouterr().out

    monkeypatch.setattr(
        ws,
        "prepare_source_package_lock",
        lambda *_args, **_kwargs: True,
    )
    assert (
        ws._cmd_prepare_bun_lock(
            workspace_root=Path(),
            lock_file=Path("bun.lock"),
            bun_executable="/nix/store/demo/bin/bun",
        )
        == 0
    )
    assert "Relocked Bun source package overrides" in capsys.readouterr().out

    def _fail(*_args: object, **_kwargs: object) -> bool:
        msg = "relock failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(ws, "prepare_source_package_lock", _fail)
    assert (
        ws._cmd_prepare_bun_lock(
            workspace_root=Path(),
            lock_file=Path("bun.lock"),
            bun_executable="bun",
        )
        == 1
    )
    assert "relock failed" in capsys.readouterr().err


def test_json_object_and_flake_lock_helpers_cover_error_edges(tmp_path: Path) -> None:
    """Reject non-object payloads and treat null locked metadata as empty."""
    with pytest.raises(TypeError, match="Expected JSON object for demo"):
        ws._json_object([], context="demo")

    lock_file = tmp_path / "flake.lock"
    lock_file.write_text('{"nodes":{"demo":{"locked":null}}}\n', encoding="utf-8")

    assert ws._load_flake_lock_input_locked(lock_file=lock_file, node="demo") == {}


def test_load_flake_lock_input_locked_rejects_missing_node(tmp_path: Path) -> None:
    """Missing flake.lock nodes should fail explicitly instead of diffing empty payloads."""
    lock_file = tmp_path / "flake.lock"
    lock_file.write_text(
        '{"nodes":{"demo":{"locked":{"rev":"abc"}}}}\n', encoding="utf-8"
    )

    with pytest.raises(ValueError, match="does not contain flake.lock node 'missing'"):
        ws._load_flake_lock_input_locked(lock_file=lock_file, node="missing")


def test_snapshot_and_compare_flake_input_report_io_failures(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Return exit code 1 when snapshot loading or GitHub output writes fail."""
    missing_lock = tmp_path / "missing.lock"
    snapshot_output = tmp_path / "snapshots" / "demo.json"

    assert (
        ws._cmd_snapshot_flake_input(
            node="demo",
            lock_file=missing_lock,
            output=snapshot_output,
        )
        == 1
    )
    assert "missing.lock" in capsys.readouterr().err

    before = tmp_path / "before.json"
    before.write_text('{"rev":"abc"}\n', encoding="utf-8")
    broken_lock = tmp_path / "broken.lock"
    broken_lock.write_text('{"nodes":{"demo":[]}}\n', encoding="utf-8")

    assert (
        ws._cmd_compare_flake_input(
            node="demo",
            before=before,
            lock_file=broken_lock,
            github_output=None,
            output_name="changed",
        )
        == 1
    )
    assert "Expected JSON object" in capsys.readouterr().err

    lock_file = tmp_path / "flake.lock"
    lock_file.write_text(
        '{"nodes":{"demo":{"locked":{"rev":"def"}}}}\n', encoding="utf-8"
    )

    def _boom(*_args: object, **_kwargs: object) -> object:
        msg = "cannot append"
        raise OSError(msg)

    assert (
        ws._cmd_compare_flake_input(
            node="demo",
            before=before,
            lock_file=lock_file,
            github_output=SimpleNamespace(open=_boom),
            output_name="changed",
        )
        == 1
    )
    assert "cannot append" in capsys.readouterr().err


def test_snapshot_and_compare_flake_input_report_missing_nodes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Snapshot and compare should surface unknown flake inputs as errors."""
    before = tmp_path / "before.json"
    before.write_text('{"rev":"abc"}\n', encoding="utf-8")
    lock_file = tmp_path / "flake.lock"
    lock_file.write_text(
        '{"nodes":{"demo":{"locked":{"rev":"def"}}}}\n', encoding="utf-8"
    )

    assert (
        ws._cmd_snapshot_flake_input(
            node="missing",
            lock_file=lock_file,
            output=tmp_path / "snapshot.json",
        )
        == 1
    )
    assert "does not contain flake.lock node 'missing'" in capsys.readouterr().err

    assert (
        ws._cmd_compare_flake_input(
            node="missing",
            before=before,
            lock_file=lock_file,
            github_output=None,
            output_name="changed",
        )
        == 1
    )
    assert "does not contain flake.lock node 'missing'" in capsys.readouterr().err


def test_cmd_free_disk_space_runs_darwin_cleanup_in_ci(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Run macOS cleanup commands when CI env is enabled."""
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(ws.sys, "platform", "darwin")
    commands: list[list[str]] = []

    def _fake_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return _completed(args)

    monkeypatch.setattr(ws, "_run", _fake_run)
    monkeypatch.setattr(ws.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(
        ws.Path,
        "glob",
        lambda _self, _pattern: [
            Path("/Applications/Xcode-15.4.app"),
            Path("/Applications/Xcode-16.1.app"),
        ],
    )

    assert ws._cmd_free_disk_space(force_local=False) == 0
    assert ["sudo", "rm", "-rf", "/Applications/Xcode-15.4.app"] in commands
    assert any(cmd[:2] == ["xcrun", "simctl"] for cmd in commands)


def test_cmd_free_disk_space_runs_linux_cleanup_in_ci(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run Linux cleanup commands when CI env is enabled."""
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(ws.sys, "platform", "linux")
    commands: list[list[str]] = []

    def _fake_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return _completed(args)

    def _fake_glob(self: Path, pattern: str) -> list[Path]:
        if self == Path("/usr/local") and pattern == "julia*":
            return [Path("/usr/local/julia1.12.5")]
        return []

    monkeypatch.setattr(ws, "_run", _fake_run)
    monkeypatch.setattr(ws.Path, "glob", _fake_glob)

    assert ws._cmd_free_disk_space(force_local=False) == 0
    assert ["sudo", "apt-get", "clean"] in commands
    assert ["sudo", "swapoff", "-a"] in commands
    assert any(
        cmd == ["sudo", "docker", "system", "prune", "--all", "--force", "--volumes"]
        for cmd in commands
    )
    assert any(
        cmd[:3] == ["sudo", "rm", "-rf"]
        and "/usr/local/lib/android" in cmd
        and "/usr/local/julia1.12.5" in cmd
        for cmd in commands
    )


def test_cmd_free_disk_space_rejects_unsupported_platform(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Reject cleanup on unsupported runner platforms."""
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(ws.sys, "platform", "win32")
    monkeypatch.setattr(
        ws,
        "_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cleanup command should not run")
        ),
    )

    assert ws._cmd_free_disk_space(force_local=False) == 2
    assert "only supports Linux and macOS" in capsys.readouterr().err


def test_command_routing_for_new_and_legacy_aliases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Exercise Typer commands for grouped and legacy aliases."""
    output = tmp_path / "pr.md"
    snapshot = tmp_path / "snapshot.json"

    monkeypatch.setattr(
        ws, "_cmd_build_darwin_config", lambda *, host: 11 if host == "argus" else 1
    )
    monkeypatch.setattr(ws, "_cmd_eval_darwin_lock_smoke", lambda: 22)
    monkeypatch.setattr(ws, "_cmd_eval_darwin_full_smoke", lambda: 24)
    monkeypatch.setattr(
        ws,
        "_cmd_free_disk_space",
        lambda *, force_local=False: 12 if force_local else 2,
    )
    monkeypatch.setattr(ws, "_cmd_install_darwin_tools", lambda: 13)
    monkeypatch.setattr(ws, "_cmd_prefetch_flake_inputs", lambda: 14)
    monkeypatch.setattr(ws, "_cmd_nix_flake_update", lambda: 15)
    monkeypatch.setattr(ws, "_cmd_smoke_check_update_app", lambda: 16)
    monkeypatch.setattr(ws, "_cmd_list_update_targets", lambda: 17)
    monkeypatch.setattr(ws, "_cmd_generate_pr_body", lambda **_kwargs: 18)
    monkeypatch.setattr(ws, "_cmd_render_certification_pr_body", lambda **_kwargs: 27)
    monkeypatch.setattr(ws, "_cmd_verify_artifacts", lambda *, workflow: 19)
    monkeypatch.setattr(ws, "_cmd_verify_structure", lambda *, workflow: 23)
    monkeypatch.setattr(ws, "_cmd_validate_bun_lock", lambda *, lock_file: 20)
    monkeypatch.setattr(ws, "_cmd_snapshot_flake_input", lambda **_kwargs: 25)
    monkeypatch.setattr(ws, "_cmd_compare_flake_input", lambda **_kwargs: 26)
    monkeypatch.setattr(
        ws,
        "_cmd_prepare_bun_lock",
        lambda *, workspace_root, lock_file, bun_executable: 21,
    )

    assert ws.main(["darwin", "build", "argus"]) == 11
    assert ws.main(["darwin", "eval-lock-smoke"]) == 22
    assert ws.main(["darwin", "eval-full-smoke"]) == 24
    assert ws.main(["darwin", "eval-smoke"]) == 24
    assert ws.main(["darwin", "free", "--force-local"]) == 12
    assert ws.main(["darwin", "install"]) == 13
    assert ws.main(["flake", "prefetch"]) == 14
    assert ws.main(["flake", "update"]) == 15
    assert (
        ws.main([
            "flake-input",
            "snapshot",
            "--node",
            "superset",
            "--output",
            str(snapshot),
        ])
        == 25
    )
    assert (
        ws.main([
            "flake-input",
            "compare",
            "--node",
            "superset",
            "--before",
            str(snapshot),
        ])
        == 26
    )
    assert ws.main(["update-app"]) == 16
    assert ws.main(["update-targets"]) == 17
    assert (
        ws.main([
            "pr-body",
            "--output",
            str(output),
            "--workflow-url",
            "https://example.test/workflow",
            "--server-url",
            "https://example.test",
            "--repository",
            "org/repo",
            "--base-ref",
            "main",
        ])
        == 18
    )
    assert ws.main(["build-darwin-config", "argus"]) == 11
    assert ws.main(["eval-darwin-lock-smoke"]) == 22
    assert ws.main(["eval-darwin-full-smoke"]) == 24
    assert ws.main(["eval-darwin-smoke"]) == 24
    assert ws.main(["free-disk-space", "--force-local"]) == 12
    assert ws.main(["install-darwin-tools"]) == 13
    assert ws.main(["prefetch-flake-inputs"]) == 14
    assert ws.main(["nix-flake-update"]) == 15
    assert ws.main(["smoke-check-update-app"]) == 16
    assert ws.main(["list-update-targets"]) == 17
    assert ws.main(["verify-artifacts"]) == 19
    assert ws.main(["verify-structure"]) == 23
    assert ws.main(["validate-bun-lock", "--lock-file", "bun.lock"]) == 20
    assert (
        ws.main([
            "prepare-bun-lock",
            "--workspace-root",
            ".",
            "--lock-file",
            "bun.lock",
        ])
        == 21
    )
    assert (
        ws.main([
            "generate-pr-body",
            "--output",
            str(output),
            "--workflow-url",
            "https://example.test/workflow",
            "--server-url",
            "https://example.test",
            "--repository",
            "org/repo",
            "--base-ref",
            "main",
        ])
        == 18
    )
    assert (
        ws.main([
            "render-certification-pr-body",
            "--existing-body",
            str(output),
            "--output",
            str(output),
            "--run-json",
            str(snapshot),
            "--cachix-name",
            "gkze",
        ])
        == 27
    )


def test_registered_command_metadata_preserves_cli_surface() -> None:
    """Keep command names, alias help text, and mounted groups stable."""
    assert [(command.name, command.help) for command in ws.app.registered_commands] == [
        ("verify-artifacts", None),
        ("verify-structure", None),
        ("validate-bun-lock", None),
        ("prepare-bun-lock", None),
        ("build-darwin-config", "Alias for `darwin build`."),
        ("eval-darwin-lock-smoke", "Alias for `darwin eval-lock-smoke`."),
        ("eval-darwin-full-smoke", "Alias for `darwin eval-full-smoke`."),
        (
            "eval-darwin-smoke",
            "Backward-compatible alias for `darwin eval-full-smoke`.",
        ),
        ("free-disk-space", "Legacy alias for CI runner disk cleanup."),
        ("install-darwin-tools", "Alias for `darwin install`."),
        ("prefetch-flake-inputs", "Alias for `flake prefetch`."),
        ("nix-flake-update", "Alias for pinned-aware flake input updates."),
        ("generate-pr-body", "Alias for `pr-body`."),
        (
            "render-certification-pr-body",
            "Render certification details into an existing PR body.",
        ),
        ("smoke-check-update-app", "Alias for `update-app`."),
        ("list-update-targets", "Alias for `update-targets`."),
    ]
    assert [
        (group.name, group.typer_instance.info.help)
        for group in ws.app.registered_groups
    ] == [
        ("darwin", "Darwin workflow steps."),
        ("flake", "Flake-related workflow steps."),
        ("flake-input", "flake.lock input snapshot/compare workflow steps."),
        ("pr-body", "Pull request body generation workflow step."),
        ("update-app", "Update app smoke-check workflow step."),
        ("update-targets", "Update target listing workflow step."),
    ]
    assert [
        (command.name, command.help)
        for command in ws.workflow_darwin_app.registered_commands
    ] == [
        ("build", None),
        ("eval-lock-smoke", None),
        ("eval-full-smoke", None),
        ("eval-smoke", "Backward-compatible alias for `darwin eval-full-smoke`."),
        ("free", None),
        ("install", None),
    ]
    assert [
        (command.name, command.help)
        for command in ws.workflow_flake_app.registered_commands
    ] == [("prefetch", None), ("update", None)]
    assert [
        (command.name, command.help)
        for command in ws.workflow_flake_input_app.registered_commands
    ] == [("snapshot", None), ("compare", None)]
    assert ws.workflow_pr_body_app.registered_callback is not None
    assert (
        ws.workflow_pr_body_app.registered_callback.callback
        is ws.command_generate_pr_body
    )
    assert ws.workflow_pr_body_app.registered_callback.invoke_without_command is True
    assert ws.workflow_update_app.registered_callback is not None
    assert (
        ws.workflow_update_app.registered_callback.callback
        is ws.command_smoke_check_update_app
    )
    assert ws.workflow_update_app.registered_callback.invoke_without_command is True
    assert ws.workflow_update_targets_app.registered_callback is not None
    assert (
        ws.workflow_update_targets_app.registered_callback.callback
        is ws.command_list_update_targets
    )
    assert (
        ws.workflow_update_targets_app.registered_callback.invoke_without_command
        is True
    )


def test_cmd_verify_artifacts_reports_validation_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Render workflow artifact contract failures to stderr."""

    def _fail(*, workflow_path: Path) -> None:
        del workflow_path
        msg = "artifact contract mismatch"
        raise RuntimeError(msg)

    monkeypatch.setattr(ws, "validate_workflow_artifact_contracts", _fail)

    assert ws._cmd_verify_artifacts(workflow=Path("workflow.yml")) == 1
    assert "artifact contract mismatch" in capsys.readouterr().err


def test_cmd_verify_artifacts_reports_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Render successful workflow artifact validation to stdout."""

    def _ok(*, workflow_path: Path) -> None:
        del workflow_path

    monkeypatch.setattr(ws, "validate_workflow_artifact_contracts", _ok)

    assert ws._cmd_verify_artifacts(workflow=Path("workflow.yml")) == 0
    assert "Verified workflow artifact contracts" in capsys.readouterr().out


def test_cmd_verify_structure_reports_validation_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Render workflow structure contract failures to stderr."""

    def _fail(*, workflow_path: Path) -> None:
        del workflow_path
        msg = "structure contract mismatch"
        raise RuntimeError(msg)

    monkeypatch.setattr(ws, "validate_workflow_structure_contracts", _fail)

    assert ws._cmd_verify_structure(workflow=Path("workflow.yml")) == 1
    assert "structure contract mismatch" in capsys.readouterr().err


def test_cmd_verify_structure_reports_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Render successful workflow structure validation to stdout."""

    def _ok(*, workflow_path: Path) -> None:
        del workflow_path

    monkeypatch.setattr(ws, "validate_workflow_structure_contracts", _ok)

    assert ws._cmd_verify_structure(workflow=Path("workflow.yml")) == 0
    assert "Verified workflow structure contracts" in capsys.readouterr().out


def test_generate_pr_body_skips_no_change_package_diffs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not render per-package section when all package diffs are empty."""
    output = tmp_path / "pr.md"
    (tmp_path / "flake.lock").write_text("{}\n", encoding="utf-8")
    demo_sources = tmp_path / "packages/demo/sources.json"
    demo_sources.parent.mkdir(parents=True, exist_ok=True)
    demo_sources.write_text("{}\n", encoding="utf-8")

    def _fake_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["git", "show"]:
            return _completed(args, stdout="{}\n")
        if args[:4] == ["git", "diff", "--name-only", "HEAD"]:
            return _completed(args, stdout="packages/demo/sources.json\n")
        if args[:4] == ["git", "ls-files", "--others", "--exclude-standard"]:
            return _completed(args, stdout="")
        return _completed(args, stdout="")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ws, "get_repo_root", lambda: tmp_path)
    monkeypatch.setattr(ws, "_run", _fake_run)
    monkeypatch.setattr(ws, "collect_changes", lambda *_a: ([], [], []))
    monkeypatch.setattr(ws, "run_sources_diff", lambda *_a, **_k: ws.NoChangesMessage)

    rc = ws.generate_pr_body(
        output=output,
        options=ws.PRBodyOptions(
            workflow_url="https://example.test/workflow",
            server_url="https://example.test",
            repository="org/repo",
            base_ref="main",
        ),
    )
    assert rc == 0
    rendered = output.read_text(encoding="utf-8")
    assert "No flake.lock input changes detected." in rendered
    assert "Per-package sources.json changes" not in rendered


def test_generate_pr_body_renders_package_diff_for_deleted_sources_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Render a per-package diff when a changed sources file was deleted."""
    output = tmp_path / "pr.md"
    (tmp_path / "flake.lock").write_text("{}\n", encoding="utf-8")

    def _fake_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["git", "show"]:
            return _completed(args, stdout='{"old":1}\n')
        if args[:4] == ["git", "diff", "--name-only", "HEAD"]:
            return _completed(args, stdout="packages/demo/sources.json\n")
        if args[:4] == ["git", "ls-files", "--others", "--exclude-standard"]:
            return _completed(args, stdout="")
        return _completed(args, stdout="")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ws, "get_repo_root", lambda: tmp_path)
    monkeypatch.setattr(ws, "_run", _fake_run)
    monkeypatch.setattr(ws, "collect_changes", lambda *_a: ([], [], []))
    monkeypatch.setattr(ws, "run_sources_diff", lambda *_a, **_k: "@@ diff")

    rc = ws.generate_pr_body(
        output=output,
        options=ws.PRBodyOptions(
            workflow_url="https://example.test/workflow",
            server_url="https://example.test",
            repository="org/repo",
            base_ref="main",
        ),
    )

    assert rc == 0
    rendered = output.read_text(encoding="utf-8")
    assert "Per-package sources.json changes" in rendered
    assert "@@ diff" in rendered


def test_generate_pr_body_renders_sources_header_once_for_multiple_diffs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Render sources section once when multiple package diffs are present."""
    output = tmp_path / "pr.md"
    (tmp_path / "flake.lock").write_text("{}\n", encoding="utf-8")
    for relative_path in ("packages/a/sources.json", "packages/b/sources.json"):
        current_path = tmp_path / relative_path
        current_path.parent.mkdir(parents=True, exist_ok=True)
        current_path.write_text('{"new":1}\n', encoding="utf-8")

    def _fake_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["git", "show"]:
            return _completed(args, stdout='{"old":1}\n')
        if args[:4] == ["git", "diff", "--name-only", "HEAD"]:
            return _completed(
                args,
                stdout=("packages/a/sources.json\npackages/b/sources.json\n"),
            )
        if args[:4] == ["git", "ls-files", "--others", "--exclude-standard"]:
            return _completed(args, stdout="")
        return _completed(args, stdout="")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ws, "get_repo_root", lambda: tmp_path)
    monkeypatch.setattr(ws, "_run", _fake_run)
    monkeypatch.setattr(ws, "collect_changes", lambda *_a: ([], [], []))
    monkeypatch.setattr(ws, "run_sources_diff", lambda *_a, **_k: "@@ diff")

    rc = ws.generate_pr_body(
        output=output,
        options=ws.PRBodyOptions(
            workflow_url="https://example.test/workflow",
            server_url="https://example.test",
            repository="org/repo",
            base_ref="main",
        ),
    )
    assert rc == 0
    rendered = output.read_text(encoding="utf-8")
    assert rendered.count("### Per-package sources.json changes") == 1


def test_cmd_generate_pr_body_reports_history_errors(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return a clean CLI error when PR-body generation cannot read git history."""

    def _fail(*, output: str | Path, options: ws.PRBodyOptions) -> int:
        del output, options
        msg = "bad revision 'HEAD'"
        raise ws.GitHistoryReadError(msg)

    monkeypatch.setattr(ws, "generate_pr_body", _fail)

    rc = ws._cmd_generate_pr_body(
        output=Path("pr.md"),
        workflow_url="https://example.test/workflow",
        server_url="https://example.test",
        repository="org/repo",
        base_ref="main",
        compare_head="update_flake_lock_action",
    )

    assert rc == 1
    assert "bad revision 'HEAD'" in capsys.readouterr().err


def test_render_certification_pr_body_appends_section(
    tmp_path: Path,
) -> None:
    """Append certification details onto an existing PR body."""
    existing_body = tmp_path / "body.md"
    output = tmp_path / "updated.md"
    workflow = _write_certification_workflow(tmp_path / "workflow.yml")
    _write_existing_pr_body(existing_body)

    rc = ws.render_certification_pr_body(
        existing_body=existing_body,
        output=output,
        options=ws.CertificationPRBodyOptions(
            workflow_url="https://example.test/actions/runs/42",
            started_at="2026-04-24T12:00:00Z",
            updated_at="2026-04-24T14:15:00Z",
            cachix_name="gkze",
            workflow_path=workflow,
        ),
    )

    assert rc == 0
    rendered = output.read_text(encoding="utf-8")
    assert rendered.startswith("**[Workflow run](https://example.test/workflow)**")
    assert "## Certification" in rendered
    assert (
        "Latest certification: [workflow run](https://example.test/actions/runs/42)"
        in rendered
    )
    assert "Updated: `2026-04-24 14:15 UTC`" in rendered
    assert "Elapsed: `2h 15m`" in rendered
    assert "Closures pushed to Cachix (`gkze`):" in rendered
    assert "- `.#pkgs.aarch64-darwin.alpha`" in rendered
    assert "- `.#pkgs.aarch64-darwin.beta`" in rendered
    assert (
        "- Shared Darwin closure for `.#darwinConfigurations.argus.system` and "
        "`.#darwinConfigurations.rocinante.system` excluding 2 heavy package closures"
        in rendered
    )
    assert "- `.#darwinConfigurations.argus.system`" in rendered
    assert "- `.#darwinConfigurations.rocinante.system`" in rendered
    assert "- `.#pkgs.x86_64-linux.nixcfg`" in rendered
    assert ws.extract_pr_body_model(rendered).certification is not None


def test_render_certification_pr_body_replaces_existing_section(
    tmp_path: Path,
) -> None:
    """Replace the prior certification block instead of duplicating it."""
    existing_body = tmp_path / "body.md"
    output = tmp_path / "updated.md"
    workflow = _write_certification_workflow(tmp_path / "workflow.yml")
    _write_existing_pr_body(existing_body)
    assert (
        ws.render_certification_pr_body(
            existing_body=existing_body,
            output=output,
            options=ws.CertificationPRBodyOptions(
                workflow_url="https://example.test/actions/runs/42",
                started_at="2026-04-24T12:00:00Z",
                updated_at="2026-04-24T14:15:00Z",
                cachix_name="gkze",
                workflow_path=workflow,
            ),
        )
        == 0
    )
    output.replace(existing_body)

    rc = ws.render_certification_pr_body(
        existing_body=existing_body,
        output=output,
        options=ws.CertificationPRBodyOptions(
            workflow_url="https://example.test/actions/runs/99",
            started_at="2026-04-24T12:00:00Z",
            updated_at="2026-04-24T12:45:00Z",
            cachix_name="gkze",
            workflow_path=workflow,
        ),
    )

    assert rc == 0
    rendered = output.read_text(encoding="utf-8")
    assert rendered.count("## Certification") == 1
    assert "https://example.test/actions/runs/42" not in rendered
    assert "https://example.test/actions/runs/99" in rendered
    assert "Elapsed: `45m 0s`" in rendered


def test_render_certification_pr_body_replaces_legacy_marker_section(
    tmp_path: Path,
) -> None:
    """Keep certification updates working for PR bodies from the old renderer."""
    existing_body = tmp_path / "body.md"
    output = tmp_path / "updated.md"
    workflow = _write_certification_workflow(tmp_path / "workflow.yml")
    existing_body.write_text(
        "\n".join([
            "**[Workflow run](https://example.test/workflow)**",
            "",
            "<!-- update-certification:start -->",
            "## Certification",
            "old certification",
            "<!-- update-certification:end -->",
            "",
        ]),
        encoding="utf-8",
    )

    rc = ws.render_certification_pr_body(
        existing_body=existing_body,
        output=output,
        options=ws.CertificationPRBodyOptions(
            workflow_url="https://example.test/actions/runs/42",
            started_at="2026-04-24T12:00:00Z",
            updated_at="2026-04-24T14:15:00Z",
            cachix_name="gkze",
            workflow_path=workflow,
        ),
    )

    assert rc == 0
    rendered = output.read_text(encoding="utf-8")
    assert rendered.count("<!-- update-certification:start -->") == 1
    assert rendered.count("<!-- update-certification:end -->") == 1
    assert "old certification" not in rendered
    assert (
        "Latest certification: [workflow run](https://example.test/actions/runs/42)"
        in rendered
    )
    with pytest.raises(ValueError, match="does not contain"):
        ws.extract_pr_body_model(rendered)


@pytest.mark.parametrize(
    ("body", "match"),
    [
        pytest.param(
            "<!-- update-certification:start -->\nmissing end\n",
            "unbalanced certification section markers",
            id="unbalanced",
        ),
        pytest.param(
            "\n".join([
                "<!-- update-certification:start -->",
                "one",
                "<!-- update-certification:end -->",
                "<!-- update-certification:start -->",
                "two",
                "<!-- update-certification:end -->",
            ]),
            "multiple certification sections",
            id="multiple",
        ),
    ],
)
def test_legacy_certification_section_rejects_invalid_marker_shapes(
    body: str,
    match: str,
) -> None:
    """Reject malformed legacy certification marker pairs."""
    with pytest.raises(ValueError, match=match):
        cert._replace_legacy_certification_section(body=body, section="new")


def test_legacy_certification_section_handles_empty_and_unmarked_bodies() -> None:
    """Render legacy certification sections into empty or unmarked PR bodies."""
    empty = cert._replace_legacy_certification_section(body=" \n", section="new\n")
    appended = cert._replace_legacy_certification_section(
        body="Existing body\n",
        section="new\n",
    )

    assert empty == (
        "<!-- update-certification:start -->\nnew\n<!-- update-certification:end -->\n"
    )
    assert appended == (
        "Existing body\n\n"
        "<!-- update-certification:start -->\n"
        "new\n"
        "<!-- update-certification:end -->\n"
    )


def test_render_certification_pr_body_preserves_unexpected_model_errors(
    tmp_path: Path,
) -> None:
    """Only the known missing-model error should fall back to legacy markers."""
    existing_body = tmp_path / "body.md"
    output = tmp_path / "updated.md"
    workflow = _write_certification_workflow(tmp_path / "workflow.yml")
    existing_body.write_text("Existing body\n", encoding="utf-8")

    def _extract(_body: str) -> ws.PRBodyModel:
        msg = "bad serialized model"
        raise ValueError(msg)

    def _write(**_kwargs: object) -> int:
        msg = "write_pr_body should not be called"
        raise AssertionError(msg)

    with pytest.raises(ValueError, match="bad serialized model"):
        cert.render_certification_pr_body(
            existing_body=existing_body,
            output=output,
            options=cert.CertificationPRBodyOptions(
                workflow_url="https://example.test/actions/runs/42",
                started_at="2026-04-24T12:00:00Z",
                updated_at="2026-04-24T14:15:00Z",
                cachix_name="gkze",
                workflow_path=workflow,
            ),
            extract_pr_body_model=_extract,
            write_pr_body=_write,
        )

    assert not output.exists()


def test_cmd_render_certification_pr_body_reports_invalid_run_payload(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Fail clearly when the GitHub run payload is missing required fields."""
    existing_body = tmp_path / "body.md"
    output = tmp_path / "updated.md"
    run_json = tmp_path / "run.json"
    workflow = _write_certification_workflow(tmp_path / "workflow.yml")
    existing_body.write_text("Existing PR body\n", encoding="utf-8")
    run_json.write_text(
        json.dumps({
            "html_url": "https://example.test/actions/runs/42",
            "updated_at": "2026-04-24T14:15:00Z",
        })
        + "\n",
        encoding="utf-8",
    )

    rc = ws._cmd_render_certification_pr_body(
        existing_body=existing_body,
        output=output,
        run_json=run_json,
        cachix_name="gkze",
        workflow=workflow,
    )

    assert rc == 1
    assert "run_started_at" in capsys.readouterr().err
