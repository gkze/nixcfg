"""Additional tests for workflow_steps command routing and helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

from lib.tests._assertions import check
from lib.update.ci import workflow_steps as ws

if TYPE_CHECKING:
    import pytest


def _completed(
    args: list[str], *, stdout: str = "", returncode: int = 0
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout)


def test_xcode_version_key_and_git_show_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sort Xcode app names and fallback when git show fails."""
    check(ws._xcode_version_key(Path("/Applications/Xcode-16.2.app")) == (16, 2))
    check(ws._xcode_version_key(Path("/Applications/Xcode.app")) == ())

    monkeypatch.setattr(
        ws,
        "_run",
        lambda _args, **_kwargs: _completed(["git"], returncode=1),
    )
    check(ws._git_show("HEAD:missing") == "{}\n")


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
    check(
        ws._source_diff_pathspecs()
        == (":(glob)packages/**/sources.json", ":(glob)overlays/**/sources.json")
    )

    (pkg_root / "demo.sources.json").write_text("{}\n", encoding="utf-8")
    check(ws._source_diff_pathspecs() == ws.SOURCES_GIT_PATHSPECS)


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

    check(ws._cmd_list_update_targets() == 9)
    check(called["list_targets"] is True)


def test_direct_command_helpers_call_expected_subprocesses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run direct helper commands that just dispatch subprocess calls."""
    commands: list[list[str]] = []

    def _fake_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return _completed(args)

    monkeypatch.setattr(ws, "_run", _fake_run)

    check(ws._cmd_nix_flake_update() == 0)
    check(ws._cmd_install_darwin_tools() == 0)
    check(ws._cmd_prefetch_flake_inputs() == 0)
    check(ws._cmd_build_darwin_config(host="argus") == 0)
    check(ws._cmd_smoke_check_update_app() == 0)

    check(["nix", "flake", "update"] in commands)
    check(["brew", "install", "--cask", "macfuse"] in commands)
    check(["brew", "install", "1password-cli"] in commands)
    check(
        ["nix", "build", "--impure", ".#darwinConfigurations.argus.system"] in commands
    )


def test_cmd_free_disk_space_runs_cleanup_in_ci(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Run all cleanup commands when CI env is enabled."""
    monkeypatch.setenv("CI", "true")
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

    check(ws._cmd_free_disk_space(force_local=False) == 0)
    check(["sudo", "rm", "-rf", "/Applications/Xcode-15.4.app"] in commands)
    check(any(cmd[:2] == ["xcrun", "simctl"] for cmd in commands))


def test_command_routing_for_new_and_legacy_aliases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Exercise Typer commands for grouped and legacy aliases."""
    output = tmp_path / "pr.md"

    monkeypatch.setattr(
        ws, "_cmd_build_darwin_config", lambda *, host: 11 if host == "argus" else 1
    )
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
    monkeypatch.setattr(ws, "_cmd_verify_artifacts", lambda *, workflow: 19)

    check(ws.main(["darwin", "build", "argus"]) == 11)
    check(ws.main(["darwin", "free", "--force-local"]) == 12)
    check(ws.main(["darwin", "install"]) == 13)
    check(ws.main(["flake", "prefetch"]) == 14)
    check(ws.main(["flake", "update"]) == 15)
    check(ws.main(["update-app"]) == 16)
    check(ws.main(["update-targets"]) == 17)
    check(
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

    check(ws.main(["build-darwin-config", "argus"]) == 11)
    check(ws.main(["free-disk-space", "--force-local"]) == 12)
    check(ws.main(["install-darwin-tools"]) == 13)
    check(ws.main(["prefetch-flake-inputs"]) == 14)
    check(ws.main(["nix-flake-update"]) == 15)
    check(ws.main(["smoke-check-update-app"]) == 16)
    check(ws.main(["list-update-targets"]) == 17)
    check(ws.main(["verify-artifacts"]) == 19)
    check(
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

    check(ws._cmd_verify_artifacts(workflow=Path("workflow.yml")) == 1)
    check("artifact contract mismatch" in capsys.readouterr().err)


def test_cmd_verify_artifacts_reports_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Render successful workflow artifact validation to stdout."""

    def _ok(*, workflow_path: Path) -> None:
        del workflow_path

    monkeypatch.setattr(ws, "validate_workflow_artifact_contracts", _ok)

    check(ws._cmd_verify_artifacts(workflow=Path("workflow.yml")) == 0)
    check("Verified workflow artifact contracts" in capsys.readouterr().out)


def test_generate_pr_body_skips_no_change_package_diffs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not render per-package section when all package diffs are empty."""
    output = tmp_path / "pr.md"
    (tmp_path / "flake.lock").write_text("{}\n", encoding="utf-8")

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
    monkeypatch.setattr(ws, "_run", _fake_run)
    monkeypatch.setattr(ws, "run_flake_lock_diff", lambda *_a: "flake diff")
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
    check(rc == 0)
    rendered = output.read_text(encoding="utf-8")
    check("flake diff" in rendered)
    check("Per-package sources.json changes" not in rendered)


def test_generate_pr_body_renders_package_diff_and_missing_current_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Render per-package section and handle missing working-tree file."""
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
    monkeypatch.setattr(ws, "_run", _fake_run)
    monkeypatch.setattr(ws, "run_flake_lock_diff", lambda *_a: "flake diff")
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
    check(rc == 0)
    rendered = output.read_text(encoding="utf-8")
    check("Per-package sources.json changes" in rendered)
    check("@@ diff" in rendered)


def test_generate_pr_body_renders_sources_header_once_for_multiple_diffs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Render sources section once when multiple package diffs are present."""
    output = tmp_path / "pr.md"
    (tmp_path / "flake.lock").write_text("{}\n", encoding="utf-8")
    (tmp_path / "packages").mkdir()

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
    monkeypatch.setattr(ws, "_run", _fake_run)
    monkeypatch.setattr(ws, "run_flake_lock_diff", lambda *_a: "flake diff")
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
    check(rc == 0)
    rendered = output.read_text(encoding="utf-8")
    check(rendered.count("### Per-package sources.json changes") == 1)
