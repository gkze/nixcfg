"""Pure-Python tests for the Superset bun lock update helper."""

from __future__ import annotations

import os
import runpy
import stat
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

from lib.import_utils import load_module_from_path
from lib.update.paths import REPO_ROOT


def _load_module() -> ModuleType:
    return load_module_from_path(
        REPO_ROOT / "packages/superset/update_bun_lock.py",
        "superset_update_bun_lock_dedicated_test",
    )


def test_ensure_repo_root_requires_nixcfg_root(tmp_path: Path) -> None:
    """Reject directories that are not the nixcfg repository root."""
    module = _load_module()

    with pytest.raises(
        module.UpdateBunLockError,
        match="run this script from the nixcfg repository root",
    ):
        module._ensure_repo_root(tmp_path)


def test_ensure_repo_root_accepts_expected_layout(tmp_path: Path) -> None:
    """Accept a root containing flake.nix and packages/superset."""
    module = _load_module()
    (tmp_path / "flake.nix").write_text("{}\n", encoding="utf-8")
    (tmp_path / "packages" / "superset").mkdir(parents=True)

    module._ensure_repo_root(tmp_path)


def test_make_user_writable_updates_tree_but_skips_symlinks(tmp_path: Path) -> None:
    """Add user-write bits to directories and files without touching symlink targets."""
    module = _load_module()
    root = tmp_path / "tree"
    nested = root / "nested"
    nested.mkdir(parents=True)
    locked_file = nested / "bun.lock"
    locked_file.write_text("lock\n", encoding="utf-8")
    external_target = tmp_path / "external-target"
    external_target.write_text("outside\n", encoding="utf-8")
    symlink_path = root / "linked"
    symlink_path.symlink_to(external_target)

    root.chmod(stat.S_IRUSR | stat.S_IXUSR)
    nested.chmod(stat.S_IRUSR | stat.S_IXUSR)
    locked_file.chmod(stat.S_IRUSR)
    external_target.chmod(stat.S_IRUSR)

    module._make_user_writable(root)

    assert root.stat().st_mode & stat.S_IWUSR
    assert nested.stat().st_mode & stat.S_IWUSR
    assert locked_file.stat().st_mode & stat.S_IWUSR
    assert symlink_path.is_symlink()
    assert not (external_target.stat().st_mode & stat.S_IWUSR)


def test_run_translates_missing_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report missing executables as user-facing update errors."""
    module = _load_module()

    def _raise_missing(*_args, **_kwargs) -> None:
        raise FileNotFoundError("missing")

    monkeypatch.setattr(module.subprocess, "run", _raise_missing)

    with pytest.raises(
        module.UpdateBunLockError,
        match="missing required executable: bun",
    ):
        module._run(["bun", "install"])


def test_run_translates_called_process_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report subprocess failures with the exit code and command line."""
    module = _load_module()

    def _raise_failure(*_args, **_kwargs) -> None:
        raise subprocess.CalledProcessError(23, ["nix", "run", "demo"])

    monkeypatch.setattr(module.subprocess, "run", _raise_failure)

    with pytest.raises(
        module.UpdateBunLockError,
        match=r"command failed with exit code 23: nix run demo",
    ):
        module._run(["nix", "run", "demo"])


def test_main_happy_path_runs_prepare_and_copies_outputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Refresh both artifacts and copy them back into packages/superset."""
    module = _load_module()
    repo_root = tmp_path / "repo"
    output_dir = repo_root / "packages" / "superset"
    output_dir.mkdir(parents=True)
    (repo_root / "flake.nix").write_text("{}\n", encoding="utf-8")

    temp_root = tmp_path / "temp-work"
    temp_root.mkdir()

    class _TemporaryDirectory:
        def __init__(self, *, prefix: str) -> None:
            self.prefix = prefix

        def __enter__(self) -> str:
            return os.fspath(temp_root)

        def __exit__(self, *_args: object) -> bool:
            return False

    copied_trees: list[tuple[Path, Path, bool, bool]] = []
    copied_files: list[tuple[Path, Path]] = []
    run_calls: list[tuple[list[str], Path | None]] = []
    writable_roots: list[Path] = []

    def _copytree(src: Path, dst: Path, *, dirs_exist_ok: bool, symlinks: bool) -> None:
        copied_trees.append((src, dst, dirs_exist_ok, symlinks))
        (dst / "bun.lock").write_text("generated lock\n", encoding="utf-8")
        (dst / "bun.nix").write_text("generated nix\n", encoding="utf-8")

    def _copy2(src: Path, dst: Path) -> None:
        copied_files.append((src, dst))

    def _run(command: list[str], *, cwd: Path | None = None) -> None:
        run_calls.append((command, cwd))

    monkeypatch.setattr(module.Path, "cwd", classmethod(lambda cls: repo_root))
    monkeypatch.setattr(module.tempfile, "TemporaryDirectory", _TemporaryDirectory)
    monkeypatch.setattr(module.shutil, "copytree", _copytree)
    monkeypatch.setattr(module.shutil, "copy2", _copy2)
    monkeypatch.setattr(module, "_make_user_writable", writable_roots.append)
    monkeypatch.setattr(module, "_run", _run)

    assert module.main() == 0

    assert copied_trees == [(module.UPSTREAM_SRC, temp_root, True, True)]
    assert writable_roots == [temp_root]
    assert run_calls == [
        (
            [
                "nix",
                "run",
                f"path:{repo_root}#nixcfg",
                "--",
                "ci",
                "workflow",
                "prepare-bun-lock",
                "--workspace-root",
                str(temp_root),
                "--lock-file",
                str(temp_root / "bun.lock"),
                "--bun-executable",
                module.BUN,
            ],
            None,
        ),
        (
            [
                "nix",
                "run",
                f"{module.BUN2NIX_FLAKE}#bun2nix",
                "--",
                "--lock-file",
                "bun.lock",
                "--copy-prefix",
                "./",
                "--output-file",
                str(temp_root / "bun.nix"),
            ],
            temp_root,
        ),
    ]
    assert copied_files == [
        (temp_root / "bun.lock", output_dir / "bun.lock"),
        (temp_root / "bun.nix", output_dir / "bun.nix"),
    ]


def test_main_failure_path_returns_one_and_writes_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Return a failing exit code when the update helper raises a user error."""
    module = _load_module()
    repo_root = tmp_path / "repo"
    output_dir = repo_root / "packages" / "superset"
    output_dir.mkdir(parents=True)
    (repo_root / "flake.nix").write_text("{}\n", encoding="utf-8")
    temp_root = tmp_path / "temp-work"
    temp_root.mkdir()

    class _TemporaryDirectory:
        def __init__(self, *, prefix: str) -> None:
            self.prefix = prefix

        def __enter__(self) -> str:
            return os.fspath(temp_root)

        def __exit__(self, *_args: object) -> bool:
            return False

    monkeypatch.setattr(module.Path, "cwd", classmethod(lambda cls: repo_root))
    monkeypatch.setattr(module.tempfile, "TemporaryDirectory", _TemporaryDirectory)
    monkeypatch.setattr(module.shutil, "copytree", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "_make_user_writable", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        module,
        "_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            module.UpdateBunLockError("prepare step failed")
        ),
    )

    assert module.main() == 1
    assert capsys.readouterr().err == "prepare step failed\n"


def test_script_main_guard_exits_with_main_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Execute the file as a script so the __main__ guard runs."""
    script_path = (
        Path(__file__).resolve().parents[2] / "packages/superset/update_bun_lock.py"
    )
    repo_root = tmp_path / "repo"
    output_dir = repo_root / "packages" / "superset"
    output_dir.mkdir(parents=True)
    (repo_root / "flake.nix").write_text("{}\n", encoding="utf-8")
    temp_root = tmp_path / "temp-work"
    temp_root.mkdir()

    class _TemporaryDirectory:
        def __init__(self, *, prefix: str) -> None:
            self.prefix = prefix

        def __enter__(self) -> str:
            return os.fspath(temp_root)

        def __exit__(self, *_args: object) -> bool:
            return False

    def _copytree(
        _src: Path, dst: Path, *, dirs_exist_ok: bool, symlinks: bool
    ) -> None:
        assert dirs_exist_ok is True
        assert symlinks is True
        (dst / "bun.lock").write_text("generated lock\n", encoding="utf-8")
        (dst / "bun.nix").write_text("generated nix\n", encoding="utf-8")

    def _copy2(src: Path, dst: Path) -> None:
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setattr(Path, "cwd", classmethod(lambda cls: repo_root))
    monkeypatch.setattr("tempfile.TemporaryDirectory", _TemporaryDirectory)
    monkeypatch.setattr("shutil.copytree", _copytree)
    monkeypatch.setattr("shutil.copy2", _copy2)
    monkeypatch.setattr("subprocess.run", lambda *_args, **_kwargs: None)

    with pytest.raises(SystemExit, match="0") as excinfo:
        runpy.run_path(
            str(script_path),
            run_name="__main__",
        )

    assert excinfo.value.code == 0
    assert (output_dir / "bun.lock").read_text(encoding="utf-8") == "generated lock\n"
    assert (output_dir / "bun.nix").read_text(encoding="utf-8") == "generated nix\n"
