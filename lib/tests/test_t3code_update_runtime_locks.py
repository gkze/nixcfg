"""Pure-Python tests for the T3 Code runtime lock update helper."""

from __future__ import annotations

import runpy
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

from lib.import_utils import load_module_from_path
from lib.update.paths import REPO_ROOT


def _load_module() -> ModuleType:
    return load_module_from_path(
        REPO_ROOT / "packages/t3code-desktop/update_runtime_locks.py",
        "t3code_update_runtime_locks_dedicated_test",
    )


def _write_repo_layout(repo_root: Path) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "flake.nix").write_text("{}\n", encoding="utf-8")
    (repo_root / "packages" / "t3code").mkdir(parents=True)
    (repo_root / "packages" / "t3code-desktop").mkdir(parents=True)


def test_ensure_repo_root_requires_nixcfg_root(tmp_path: Path) -> None:
    """Reject directories that are not the nixcfg repository root."""
    module = _load_module()

    with pytest.raises(
        module.UpdateRuntimeLocksError,
        match="run this script from the nixcfg repository root",
    ):
        module._ensure_repo_root(tmp_path)


def test_ensure_repo_root_accepts_expected_layout(tmp_path: Path) -> None:
    """Accept a root containing flake.nix and both T3 package directories."""
    module = _load_module()
    _write_repo_layout(tmp_path)

    module._ensure_repo_root(tmp_path)


def test_run_translates_missing_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report missing executables as user-facing update errors."""
    module = _load_module()

    def _raise_missing(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError("missing")

    monkeypatch.setattr(module.subprocess, "run", _raise_missing)

    with pytest.raises(
        module.UpdateRuntimeLocksError,
        match="missing required executable: bun",
    ):
        module._run(["bun", "install"])


def test_run_invokes_subprocess(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Pass command, cwd, and environment through to subprocess.run."""
    module = _load_module()
    calls: list[tuple[list[str], Path, dict[str, str]]] = []
    env = {"DEMO": "1"}

    def _record_run(
        command: list[str],
        *,
        check: bool,
        cwd: Path,
        env: dict[str, str],
    ) -> None:
        assert check is True
        calls.append((command, cwd, env))

    monkeypatch.setattr(module.subprocess, "run", _record_run)

    module._run(["bun", "install"], cwd=tmp_path, env=env)

    assert calls == [(["bun", "install"], tmp_path, env)]


def test_run_translates_called_process_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report subprocess failures with the exit code and command line."""
    module = _load_module()

    def _raise_failure(*_args: object, **_kwargs: object) -> None:
        raise subprocess.CalledProcessError(23, ["bun", "install"])

    monkeypatch.setattr(module.subprocess, "run", _raise_failure)

    with pytest.raises(
        module.UpdateRuntimeLocksError,
        match=r"command failed with exit code 23: bun install",
    ):
        module._run(["bun", "install"])


def test_refresh_lock_renders_manifest_and_runs_bun(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Refresh a package lock with the rendered runtime manifest."""
    module = _load_module()
    repo_root = tmp_path / "repo"
    _write_repo_layout(repo_root)
    lock_file = repo_root / "packages" / "t3code-desktop" / "bun.lock"
    lock_file.write_text("original lock\n", encoding="utf-8")
    calls: list[tuple[list[str], Path | None, dict[str, str] | None]] = []

    def _run(
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        calls.append((command, cwd, env))

    monkeypatch.setattr(module, "_run", _run)

    module._refresh_lock(
        repo_root,
        lock_file,
        electron_builder_version="26.8.1",
        commit_hash="abc123",
    )

    render_command, render_cwd, render_env = calls[0]
    assert render_command[:4] == [
        module.sys.executable,
        str(
            repo_root / "packages" / "t3code-desktop" / "render_runtime_package_json.py"
        ),
        str(module.UPSTREAM_SRC),
        "--output",
    ]
    assert render_command[4].endswith("/package.json")
    assert render_command[5:] == [
        "--electron-builder-version",
        "26.8.1",
        "--commit-hash",
        "abc123",
    ]
    assert render_cwd is None
    assert render_env is None

    bun_command, bun_cwd, bun_env = calls[1]
    assert bun_command == [
        module.BUN,
        "install",
        "--lockfile-only",
        "--ignore-scripts",
        "--no-progress",
    ]
    assert bun_cwd is not None
    assert bun_cwd.name.startswith("t3code-runtime-lock.")
    assert bun_env is not None
    assert Path(bun_env["HOME"]).name.startswith("t3code-bun-lock-home.")
    assert lock_file.read_text(encoding="utf-8") == "original lock\n"


def test_render_runtime_manifest_keeps_optional_flags_optional(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Do not pass desktop-only manifest flags for the standalone package."""
    module = _load_module()
    repo_root = tmp_path / "repo"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    calls: list[list[str]] = []

    monkeypatch.setattr(
        module,
        "_run",
        lambda command: calls.append(command),
    )

    module._render_runtime_manifest(repo_root, workspace)

    assert calls == [
        [
            module.sys.executable,
            str(
                repo_root
                / "packages"
                / "t3code-desktop"
                / "render_runtime_package_json.py"
            ),
            str(module.UPSTREAM_SRC),
            "--output",
            str(workspace / "package.json"),
        ]
    ]


def test_main_happy_path_refreshes_both_lockfiles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Refresh both runtime lockfiles from the repository root."""
    module = _load_module()
    repo_root = tmp_path / "repo"
    _write_repo_layout(repo_root)
    refreshed: list[tuple[Path, str | None, str | None]] = []

    monkeypatch.setattr(module.Path, "cwd", classmethod(lambda cls: repo_root))
    monkeypatch.setattr(
        module,
        "_refresh_lock",
        lambda _repo_root, lock_file, **kwargs: refreshed.append((
            lock_file,
            kwargs.get("electron_builder_version"),
            kwargs.get("commit_hash"),
        )),
    )

    assert module.main() == 0
    assert refreshed == [
        (repo_root / "packages" / "t3code" / "bun.lock", None, None),
        (
            repo_root / "packages" / "t3code-desktop" / "bun.lock",
            module.ELECTRON_BUILDER_VERSION,
            module.T3CODE_COMMIT_HASH,
        ),
    ]


def test_main_failure_path_returns_one_and_writes_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Return a failing exit code when the update helper raises a user error."""
    module = _load_module()
    repo_root = tmp_path / "repo"
    _write_repo_layout(repo_root)

    monkeypatch.setattr(module.Path, "cwd", classmethod(lambda cls: repo_root))
    monkeypatch.setattr(
        module,
        "_refresh_lock",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            module.UpdateRuntimeLocksError("refresh failed")
        ),
    )

    assert module.main() == 1
    assert capsys.readouterr().err == "refresh failed\n"


def test_script_main_guard_exits_with_main_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Execute the file as a script so the __main__ guard runs."""
    script_path = REPO_ROOT / "packages/t3code-desktop/update_runtime_locks.py"
    repo_root = tmp_path / "repo"
    _write_repo_layout(repo_root)
    t3_lock = repo_root / "packages" / "t3code" / "bun.lock"
    desktop_lock = repo_root / "packages" / "t3code-desktop" / "bun.lock"
    t3_lock.write_text("t3 lock\n", encoding="utf-8")
    desktop_lock.write_text("desktop lock\n", encoding="utf-8")

    monkeypatch.setattr(Path, "cwd", classmethod(lambda cls: repo_root))
    monkeypatch.setattr("subprocess.run", lambda *_args, **_kwargs: None)

    with pytest.raises(SystemExit, match="0") as excinfo:
        runpy.run_path(str(script_path), run_name="__main__")

    assert excinfo.value.code == 0
    assert t3_lock.read_text(encoding="utf-8") == "t3 lock\n"
    assert desktop_lock.read_text(encoding="utf-8") == "desktop lock\n"
