"""Tests for shared recover command helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lib.recover import _common as rc


def test_files_equal_requires_existing_identical_files(tmp_path: Path) -> None:
    """Compare files by bytes and reject missing inputs."""
    left = tmp_path / "left.txt"
    right = tmp_path / "right.txt"

    assert rc.files_equal(left, right) is False

    left.write_bytes(b"same")
    right.write_bytes(b"same")
    assert rc.files_equal(left, right) is True

    right.write_bytes(b"different")
    assert rc.files_equal(left, right) is False


def test_stage_paths_returns_early_when_no_paths(tmp_path: Path) -> None:
    """Skip git lookup entirely when there is nothing to stage."""
    rc.stage_paths(tmp_path, ())


def test_stage_paths_rejects_missing_git(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Raise a clear error when git cannot be found."""
    monkeypatch.setattr("lib.recover._common.shutil.which", lambda _name: None)

    with pytest.raises(RuntimeError, match="git not found on PATH"):
        rc.stage_paths(tmp_path, ("flake.lock",))


def test_stage_paths_runs_git_add_and_surfaces_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Run git add -A and propagate stderr or stdout failures."""
    seen: dict[str, object] = {}

    def _run_success(
        args: list[str],
        *,
        cwd: Path,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        seen["args"] = args
        seen["cwd"] = cwd
        seen["check"] = check
        seen["capture_output"] = capture_output
        seen["text"] = text
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(
        "lib.recover._common.shutil.which", lambda _name: "/usr/bin/git"
    )
    monkeypatch.setattr(subprocess, "run", _run_success)

    rc.stage_paths(tmp_path, ("flake.lock", "packages/demo/sources.json"))

    assert seen["args"] == [
        "/usr/bin/git",
        "add",
        "-A",
        "--",
        "flake.lock",
        "packages/demo/sources.json",
    ]
    assert seen["cwd"] == tmp_path
    assert seen["check"] is False
    assert seen["capture_output"] is True
    assert seen["text"] is True

    def _run_stderr(
        _args: list[str],
        *,
        cwd: Path,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, check, capture_output, text
        return subprocess.CompletedProcess(
            args=["git"], returncode=1, stdout="", stderr="fatal: bad path\n"
        )

    monkeypatch.setattr(subprocess, "run", _run_stderr)
    with pytest.raises(RuntimeError, match="fatal: bad path"):
        rc.stage_paths(tmp_path, ("flake.lock",))

    def _run_stdout(
        _args: list[str],
        *,
        cwd: Path,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, check, capture_output, text
        return subprocess.CompletedProcess(
            args=["git"], returncode=1, stdout="needs attention\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", _run_stdout)
    with pytest.raises(RuntimeError, match="needs attention"):
        rc.stage_paths(tmp_path, ("flake.lock",))
