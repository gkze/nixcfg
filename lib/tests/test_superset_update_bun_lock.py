"""Pure-Python tests for the Superset bun lock update helper."""

import json
import os
import runpy
import stat
import subprocess
from pathlib import Path
from types import ModuleType

import pytest
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.ellipses import Ellipses
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.path import NixPath
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.import_utils import load_module_from_path
from lib.update.paths import REPO_ROOT, local_flake_url


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


def test_run_ignores_blank_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not append an empty diagnostic tail."""
    module = _load_module()

    def _raise_failure(*_args: object, **_kwargs: object) -> None:
        raise subprocess.CalledProcessError(7, ["nix", "hash", "path"], stderr="  \n")

    monkeypatch.setattr(module.subprocess, "run", _raise_failure)

    with pytest.raises(
        module.UpdateBunLockError,
        match=r"^command failed with exit code 7: nix hash path$",
    ):
        module._run(["nix", "hash", "path"])


def test_run_includes_stderr_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the nix diagnostic that the updater previously discarded."""
    module = _load_module()

    def _raise_failure(*_args: object, **_kwargs: object) -> None:
        raise subprocess.CalledProcessError(
            1,
            ["nix", "hash", "path"],
            stderr="noise\nerror: path '/src/apps/admin' does not exist",
        )

    monkeypatch.setattr(module.subprocess, "run", _raise_failure)

    with pytest.raises(
        module.UpdateBunLockError,
        match=r"does not exist",
    ):
        module._run(["nix", "hash", "path"])


def test_run_truncates_long_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bound a noisy nix log so the failure stays a quarantine excerpt."""
    module = _load_module()
    stderr = "HEAD-DROPPED\n" + ("x" * module._STDERR_TAIL)

    def _raise_failure(*_args: object, **_kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, ["nix", "hash", "path"], stderr=stderr)

    monkeypatch.setattr(module.subprocess, "run", _raise_failure)

    with pytest.raises(module.UpdateBunLockError) as excinfo:
        module._run(["nix", "hash", "path"])

    message = str(excinfo.value)
    tail = message.splitlines()[-1]
    assert "HEAD-DROPPED" not in message
    assert tail == "x" * module._STDERR_TAIL


def test_validate_workspace_parity_names_a_missing_pinned_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Name a workspace the pinned tree removed before calling nix hash."""
    module = _load_module()
    prepared_root = tmp_path / "prepared"
    upstream_root = tmp_path / "upstream"
    relative = Path("apps/admin")
    (prepared_root / relative).mkdir(parents=True)
    upstream_root.mkdir()

    def _unexpected_hash(*_args: object, **_kwargs: object) -> str:
        msg = "nix hash must not run when a workspace path is missing"
        raise AssertionError(msg)

    monkeypatch.setattr(
        module,
        "_workspace_paths_from_bun_nix",
        lambda _bun_nix: (prepared_root / relative,),
    )
    monkeypatch.setattr(module, "_run", _unexpected_hash)

    with pytest.raises(
        module.UpdateBunLockError,
        match=r"not in the pinned Superset tree: apps/admin",
    ):
        module._validate_workspace_parity(
            bun_nix=prepared_root / "bun.nix",
            prepared_root=prepared_root,
            upstream_root=upstream_root,
        )


def test_validate_workspace_parity_names_a_missing_prepared_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Name a generated workspace the prepared tree does not contain."""
    module = _load_module()
    prepared_root = tmp_path / "prepared"
    upstream_root = tmp_path / "upstream"
    relative = Path("apps/admin")
    prepared_root.mkdir()
    (upstream_root / relative).mkdir(parents=True)
    monkeypatch.setattr(
        module,
        "_workspace_paths_from_bun_nix",
        lambda _bun_nix: (prepared_root / relative,),
    )

    with pytest.raises(
        module.UpdateBunLockError,
        match=r"not in the prepared Superset tree: apps/admin",
    ):
        module._validate_workspace_parity(
            bun_nix=prepared_root / "bun.nix",
            prepared_root=prepared_root,
            upstream_root=upstream_root,
        )


def test_workspace_paths_from_bun_nix_evaluates_generated_local_paths(
    tmp_path: Path,
) -> None:
    """Use a tiny real Nix eval because applying bun.nix cannot be proven from its AST alone."""
    module = _load_module()
    workspace = tmp_path / "apps" / "desktop"
    workspace.mkdir(parents=True)
    bun_nix = tmp_path / "bun.nix"
    expression = FunctionDefinition(
        argument_set=[
            Identifier(name="copyPathToStore"),
            Identifier(name="fetchFromGitHub"),
            Identifier(name="fetchgit"),
            Identifier(name="fetchurl"),
            Ellipses(),
        ],
        output=AttributeSet(
            values=[
                Binding(
                    name="remote",
                    value=FunctionCall(
                        name=Identifier(name="fetchurl"),
                        argument=AttributeSet(
                            values=[
                                Binding(
                                    name="url",
                                    value=StringPrimitive(
                                        value="https://example.test/package.tgz"
                                    ),
                                )
                            ]
                        ),
                    ),
                ),
                Binding(
                    name="github",
                    value=FunctionCall(
                        name=Identifier(name="fetchFromGitHub"),
                        argument=AttributeSet(
                            values=[
                                Binding(
                                    name="owner",
                                    value=StringPrimitive(value="example"),
                                ),
                                Binding(
                                    name="repo",
                                    value=StringPrimitive(value="demo"),
                                ),
                            ]
                        ),
                    ),
                ),
                Binding(
                    name="git",
                    value=FunctionCall(
                        name=Identifier(name="fetchgit"),
                        argument=AttributeSet(
                            values=[
                                Binding(
                                    name="url",
                                    value=StringPrimitive(
                                        value="https://example.test/demo.git"
                                    ),
                                )
                            ]
                        ),
                    ),
                ),
                Binding(
                    name="workspace",
                    value=FunctionCall(
                        name=Identifier(name="copyPathToStore"),
                        argument=NixPath(path="./apps/desktop"),
                    ),
                ),
            ]
        ),
    )
    bun_nix.write_text(expression.rebuild() + "\n", encoding="utf-8")

    assert module._workspace_paths_from_bun_nix(bun_nix) == (workspace,)


@pytest.mark.parametrize(
    "output",
    [
        "not-json",
        "{}",
        "[]",
        '["/tmp/workspace", 1]',
    ],
)
def test_workspace_paths_from_bun_nix_rejects_invalid_evaluation_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    output: str,
) -> None:
    """Reject malformed or empty evaluator output instead of skipping parity checks."""
    module = _load_module()
    monkeypatch.setattr(module, "_run", lambda *_args, **_kwargs: output)

    with pytest.raises(
        module.UpdateBunLockError,
        match="could not resolve workspace paths from generated bun.nix",
    ):
        module._workspace_paths_from_bun_nix(tmp_path / "bun.nix")


def test_validate_workspace_parity_accepts_nar_equivalent_trees(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Ignore copy-time write bits while preserving Nix path hashing semantics."""
    module = _load_module()
    upstream_root = tmp_path / "upstream"
    prepared_root = tmp_path / "prepared"
    relative = Path("apps/desktop")
    for root in (upstream_root, prepared_root):
        workspace = root / relative
        workspace.mkdir(parents=True)
        executable = workspace / "run.sh"
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(stat.S_IRUSR | stat.S_IXUSR)
        (workspace / "run-link").symlink_to("run.sh")
    (prepared_root / relative / "run.sh").chmod(
        stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
    )
    monkeypatch.setattr(
        module,
        "_workspace_paths_from_bun_nix",
        lambda _bun_nix: (prepared_root / relative,),
    )

    module._validate_workspace_parity(
        bun_nix=prepared_root / "bun.nix",
        prepared_root=prepared_root,
        upstream_root=upstream_root,
    )


def test_validate_workspace_parity_rejects_prepared_workspace_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Fail before promotion when preparation changes a bun.nix workspace subtree."""
    module = _load_module()
    upstream_root = tmp_path / "upstream"
    prepared_root = tmp_path / "prepared"
    relative = Path("apps/desktop")
    for root, content in ((upstream_root, "raw\n"), (prepared_root, "prepared\n")):
        workspace = root / relative
        workspace.mkdir(parents=True)
        (workspace / "package.json").write_text(content, encoding="utf-8")
    monkeypatch.setattr(
        module,
        "_workspace_paths_from_bun_nix",
        lambda _bun_nix: (prepared_root / relative,),
    )

    with pytest.raises(
        module.UpdateBunLockError,
        match=r"prepared workspace differs from locked upstream source: apps/desktop",
    ):
        module._validate_workspace_parity(
            bun_nix=prepared_root / "bun.nix",
            prepared_root=prepared_root,
            upstream_root=upstream_root,
        )


def test_validate_workspace_parity_rejects_paths_outside_prepared_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Do not let a generated local package escape the source tree being checked."""
    module = _load_module()
    prepared_root = tmp_path / "prepared"
    prepared_root.mkdir()
    monkeypatch.setattr(
        module,
        "_workspace_paths_from_bun_nix",
        lambda _bun_nix: (tmp_path / "outside",),
    )

    with pytest.raises(
        module.UpdateBunLockError,
        match="bun.nix referenced a workspace outside the prepared source",
    ):
        module._validate_workspace_parity(
            bun_nix=prepared_root / "bun.nix",
            prepared_root=prepared_root,
            upstream_root=tmp_path / "upstream",
        )


def test_validate_workspace_parity_rejects_symlink_escape(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reject lexical children whose resolved path escapes through a symlink."""
    module = _load_module()
    prepared_root = tmp_path / "prepared"
    prepared_root.mkdir()
    outside = tmp_path / "outside"
    (outside / "desktop").mkdir(parents=True)
    (prepared_root / "apps").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(
        module,
        "_workspace_paths_from_bun_nix",
        lambda _bun_nix: (prepared_root / "apps/desktop",),
    )

    with pytest.raises(
        module.UpdateBunLockError,
        match="bun.nix referenced a workspace outside the prepared source",
    ):
        module._validate_workspace_parity(
            bun_nix=prepared_root / "bun.nix",
            prepared_root=prepared_root,
            upstream_root=tmp_path / "upstream",
        )


def test_validate_workspace_parity_rejects_incomplete_hash_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Require one NAR hash for each prepared and locked workspace path."""
    module = _load_module()
    prepared_root = tmp_path / "prepared"
    upstream_root = tmp_path / "upstream"
    relative = Path("apps/desktop")
    (prepared_root / relative).mkdir(parents=True)
    (upstream_root / relative).mkdir(parents=True)
    monkeypatch.setattr(
        module,
        "_workspace_paths_from_bun_nix",
        lambda _bun_nix: (prepared_root / relative,),
    )
    monkeypatch.setattr(module, "_run", lambda *_args, **_kwargs: "one-hash\n")

    with pytest.raises(
        module.UpdateBunLockError,
        match="nix hash path returned an unexpected number of workspace hashes",
    ):
        module._validate_workspace_parity(
            bun_nix=prepared_root / "bun.nix",
            prepared_root=prepared_root,
            upstream_root=upstream_root,
        )


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

    copied_trees: list[tuple[Path, Path, bool]] = []
    copied_files: list[tuple[Path, Path]] = []
    run_calls: list[tuple[list[str], Path | None]] = []
    parity_calls: list[tuple[Path, Path, Path]] = []
    writable_roots: list[Path] = []

    def _copytree(src: Path, dst: Path, *, symlinks: bool) -> None:
        copied_trees.append((src, dst, symlinks))
        dst.mkdir(parents=True, exist_ok=True)
        (dst / "bun.lock").write_text("generated lock\n", encoding="utf-8")
        (dst / "bun.nix").write_text("generated nix\n", encoding="utf-8")

    def _copy2(src: Path, dst: Path) -> None:
        copied_files.append((src, dst))

    def _run(command: list[str], *, cwd: Path | None = None, **_kwargs: object) -> str:
        run_calls.append((command, cwd))
        return ""

    def _validate_workspace_parity(
        *, bun_nix: Path, prepared_root: Path, upstream_root: Path
    ) -> None:
        parity_calls.append((bun_nix, prepared_root, upstream_root))

    monkeypatch.setattr(module.Path, "cwd", classmethod(lambda cls: repo_root))
    monkeypatch.setattr(module.tempfile, "TemporaryDirectory", _TemporaryDirectory)
    monkeypatch.setattr(module.shutil, "copytree", _copytree)
    monkeypatch.setattr(module.shutil, "copy2", _copy2)
    monkeypatch.setattr(module, "_make_user_writable", writable_roots.append)
    monkeypatch.setattr(module, "_run", _run)
    monkeypatch.setattr(
        module, "_validate_workspace_parity", _validate_workspace_parity
    )

    assert module.main() == 0

    prepared_root = temp_root / "prepared"
    upstream_snapshot = temp_root / "upstream"
    assert copied_trees == [
        (module.UPSTREAM_SRC, upstream_snapshot, True),
        (upstream_snapshot, prepared_root, True),
    ]
    assert writable_roots == [prepared_root]
    assert run_calls == [
        (
            [
                "nix",
                "run",
                f"{local_flake_url(repo_root)}#nixcfg",
                "--",
                "ci",
                "pipeline",
                "bun-lock",
                "prepare",
                "--workspace-root",
                str(prepared_root),
                "--lock-file",
                str(prepared_root / "bun.lock"),
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
                str(prepared_root / "bun.nix"),
            ],
            prepared_root,
        ),
    ]
    assert copied_files == [
        (prepared_root / "bun.lock", output_dir / "bun.lock"),
        (prepared_root / "bun.nix", output_dir / "bun.nix"),
    ]
    assert parity_calls == [
        (prepared_root / "bun.nix", prepared_root, upstream_snapshot),
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

    def _copytree(_src: Path, dst: Path, *, symlinks: bool) -> None:
        assert symlinks is True
        dst.mkdir(parents=True, exist_ok=True)
        (dst / "bun.lock").write_text("generated lock\n", encoding="utf-8")
        (dst / "bun.nix").write_text("generated nix\n", encoding="utf-8")
        (dst / "apps" / "desktop").mkdir(parents=True)

    def _copy2(src: Path, dst: Path) -> None:
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setattr(Path, "cwd", classmethod(lambda cls: repo_root))
    monkeypatch.setattr("tempfile.TemporaryDirectory", _TemporaryDirectory)
    monkeypatch.setattr("shutil.copytree", _copytree)
    monkeypatch.setattr("shutil.copy2", _copy2)

    def _subprocess_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        if command[:2] == ["nix", "eval"]:
            env = kwargs.get("env")
            if not isinstance(env, dict):
                msg = "nix eval must receive the generated bun.nix path"
                raise AssertionError(msg)
            bun_nix = Path(str(env["NIXCFG_SUPERSET_BUN_NIX"]))
            stdout = json.dumps([str(bun_nix.parent / "apps" / "desktop")])
        elif command[:3] == ["nix", "hash", "path"]:
            stdout = "sha256-workspace\nsha256-workspace\n"
        else:
            stdout = ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("subprocess.run", _subprocess_run)

    with pytest.raises(SystemExit, match="0") as excinfo:
        runpy.run_path(
            str(script_path),
            run_name="__main__",
        )

    assert excinfo.value.code == 0
    assert (output_dir / "bun.lock").read_text(encoding="utf-8") == "generated lock\n"
    assert (output_dir / "bun.nix").read_text(encoding="utf-8") == "generated nix\n"
