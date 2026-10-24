"""Behavior of the packaged runtime source boundary and checkout re-exec."""

import os
import stat
import sys
from pathlib import Path

import pytest

from lib.tests._update_workspace_helpers import init_update_workspace_repo
from lib.update import cli
from lib.update.runtime_source import (
    EXECUTION_SOURCE_ENV,
    REEXEC_ENV,
    UpdateSourceChangedError,
    _argv_runs_top_level_update,
    maybe_reexec_checkout_update,
    revalidate_runtime_source_snapshot,
    runtime_source_policy,
    runtime_source_relpaths,
    update_library_matches_checkout,
)

_RUNTIME_SOURCE_POLICY = """[tool.nixcfg.runtimeSource]
schemaVersion = 1
rootPaths = ["nixcfg.py", "pyproject.toml", "uv.lock"]
libraryExtensions = ["json", "py", "pyi", "yaml"]
libraryNames = ["py.typed"]
excludedLibraryPaths = ["tests"]
dynamicRoots = ["packages", "overlays"]
dynamicExtensions = ["py", "pyi"]
dynamicExcludedFileSuffixes = ["_test.py"]
"""


def _write_update_file(root: Path, relative_path: str, content: str) -> None:
    path = root / "lib" / "update" / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    policy_path = root / "pyproject.toml"
    if not policy_path.exists():
        policy_path.write_text(_RUNTIME_SOURCE_POLICY, encoding="utf-8")
    for root_file in (root / "nixcfg.py", root / "uv.lock"):
        if not root_file.exists():
            root_file.write_text("fixture runtime root\n", encoding="utf-8")
    for dynamic_root in (root / "packages", root / "overlays"):
        dynamic_root.mkdir(exist_ok=True)


def test_update_library_matches_checkout_same_and_equal_trees(tmp_path: Path) -> None:
    """Treat the current update tree and byte-identical copies as compatible."""
    repo_root = tmp_path / "repo"
    _write_update_file(repo_root, "cli.py", "VALUE = 1\n")

    assert update_library_matches_checkout(
        repo_root,
        runtime_source_root=repo_root,
    )

    _write_update_file(tmp_path / "runtime-root", "cli.py", "VALUE = 1\n")
    runtime_root = tmp_path / "runtime-root"

    assert update_library_matches_checkout(
        repo_root,
        runtime_source_root=runtime_root,
    )


def test_update_library_detects_missing_or_changed_checkout(tmp_path: Path) -> None:
    """Reject missing, differently shaped, or content-skewed update libraries."""
    repo_root = tmp_path / "repo"
    runtime_source_root = tmp_path / "runtime-root"
    runtime_root = runtime_source_root

    assert not update_library_matches_checkout(
        repo_root,
        runtime_source_root=runtime_root,
    )

    _write_update_file(repo_root, "cli.py", "VALUE = 1\n")
    _write_update_file(runtime_source_root, "other.py", "VALUE = 1\n")
    assert not update_library_matches_checkout(
        repo_root, runtime_source_root=runtime_root
    )

    (runtime_root / "lib" / "update" / "other.py").unlink()
    _write_update_file(runtime_source_root, "cli.py", "VALUE = 2\n")
    assert not update_library_matches_checkout(
        repo_root, runtime_source_root=runtime_root
    )


@pytest.mark.parametrize("runtime_path", ["nixcfg.py", "pyproject.toml", "uv.lock"])
def test_update_library_detects_runtime_root_input_changes(
    runtime_path: str,
    tmp_path: Path,
) -> None:
    """Entrypoint, project metadata, and lock changes must all force a handoff."""
    repo_root = tmp_path / "repo"
    runtime_source_root = tmp_path / "runtime-root"
    _write_update_file(repo_root, "cli.py", "VALUE = 1\n")
    _write_update_file(runtime_source_root, "cli.py", "VALUE = 1\n")
    for root in (repo_root, runtime_source_root):
        (root / "nixcfg.py").write_text("VALUE = 1\n", encoding="utf-8")
        (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")

    changed_path = repo_root / runtime_path
    changed_path.write_text(
        changed_path.read_text(encoding="utf-8") + "# changed\n",
        encoding="utf-8",
    )

    assert not update_library_matches_checkout(
        repo_root,
        runtime_source_root=runtime_source_root,
    )


def test_update_library_detects_packaged_policy_data_changes(tmp_path: Path) -> None:
    """Policy-only edits must trigger the checkout-matching runtime handoff."""
    repo_root = tmp_path / "repo"
    runtime_source_root = tmp_path / "runtime-root"
    _write_update_file(repo_root, "cli.py", "VALUE = 1\n")
    _write_update_file(runtime_source_root, "cli.py", "VALUE = 1\n")
    repo_policy = repo_root / "lib" / "system-policy.json"
    runtime_policy = runtime_source_root / "lib" / "system-policy.json"
    repo_policy.write_text('{"schemaVersion": 1}\n', encoding="utf-8")
    runtime_policy.write_text('{"schemaVersion": 2}\n', encoding="utf-8")

    assert not update_library_matches_checkout(
        repo_root,
        runtime_source_root=runtime_source_root,
    )

    runtime_policy.write_bytes(repo_policy.read_bytes())
    assert update_library_matches_checkout(
        repo_root,
        runtime_source_root=runtime_source_root,
    )


def test_update_library_detects_dynamic_updater_changes(tmp_path: Path) -> None:
    """Live updater code must never run against a silently stale installed core."""
    repo_root = tmp_path / "repo"
    runtime_source_root = tmp_path / "runtime-root"
    for root in (repo_root, runtime_source_root):
        _write_update_file(root, "cli.py", "VALUE = 1\n")
        updater = root / "packages" / "demo" / "updater.py"
        updater.parent.mkdir(parents=True)
        updater.write_text("VALUE = 1\n", encoding="utf-8")
        (updater.parent / "updater_test.py").write_text(
            "TEST_VALUE = 1\n",
            encoding="utf-8",
        )

    assert update_library_matches_checkout(
        repo_root,
        runtime_source_root=runtime_source_root,
    )

    (repo_root / "packages" / "demo" / "updater.py").write_text(
        "VALUE = 2\n",
        encoding="utf-8",
    )
    assert not update_library_matches_checkout(
        repo_root,
        runtime_source_root=runtime_source_root,
    )

    (repo_root / "packages" / "demo" / "updater.py").write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )
    (repo_root / "packages" / "demo" / "updater_test.py").write_text(
        "TEST_VALUE = 2\n",
        encoding="utf-8",
    )
    assert update_library_matches_checkout(
        repo_root,
        runtime_source_root=runtime_source_root,
    )


def test_runtime_source_relpaths_excludes_non_runtime_files(tmp_path: Path) -> None:
    """Apply every policy filter before comparing packaged runtime sources."""
    _write_update_file(tmp_path, "cli.py", "VALUE = 1\n")
    excluded_library_file = tmp_path / "lib" / "tests" / "helper.py"
    excluded_library_file.parent.mkdir(parents=True)
    excluded_library_file.write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "lib" / "update" / "notes.txt").write_text(
        "not runtime source\n",
        encoding="utf-8",
    )
    package_dir = tmp_path / "packages" / "demo"
    package_dir.mkdir(parents=True)
    (package_dir / "updater.py").write_text("VALUE = 1\n", encoding="utf-8")
    (package_dir / "updater_test.py").write_text(
        "TEST_VALUE = 1\n",
        encoding="utf-8",
    )
    (package_dir / "README.md").write_text("ignored\n", encoding="utf-8")

    assert runtime_source_relpaths(
        tmp_path,
        runtime_source_policy(tmp_path),
    ) == {
        Path("nixcfg.py"),
        Path("pyproject.toml"),
        Path("uv.lock"),
        Path("lib/update/cli.py"),
        Path("packages/demo/updater.py"),
    }


def test_update_library_compares_declared_runtime_directories(
    tmp_path: Path,
) -> None:
    """Directory root paths must match the recursive fileset packaged by Nix."""
    repo_root = tmp_path / "repo"
    runtime_source_root = tmp_path / "runtime-root"
    policy = _RUNTIME_SOURCE_POLICY.replace(
        'rootPaths = ["nixcfg.py", "pyproject.toml", "uv.lock"]',
        'rootPaths = ["nixcfg.py", "pyproject.toml", "uv.lock", "runtime-data"]',
    )
    for root in (repo_root, runtime_source_root):
        _write_update_file(root, "cli.py", "VALUE = 1\n")
        (root / "pyproject.toml").write_text(policy, encoding="utf-8")
        runtime_file = root / "runtime-data" / "behavior.py"
        runtime_file.parent.mkdir()
        runtime_file.write_text("VALUE = 1\n", encoding="utf-8")

    assert update_library_matches_checkout(
        repo_root,
        runtime_source_root=runtime_source_root,
    )

    (repo_root / "runtime-data" / "behavior.py").write_text(
        "VALUE = 2\n",
        encoding="utf-8",
    )
    assert not update_library_matches_checkout(
        repo_root,
        runtime_source_root=runtime_source_root,
    )

    (repo_root / "runtime-data" / "behavior.py").unlink()
    assert not update_library_matches_checkout(
        repo_root,
        runtime_source_root=runtime_source_root,
    )


def test_update_library_compares_file_valued_dynamic_roots(tmp_path: Path) -> None:
    """A dynamic file root must match the fileFilter semantics used by Nix."""
    repo_root = tmp_path / "repo"
    runtime_source_root = tmp_path / "runtime-root"
    policy = _RUNTIME_SOURCE_POLICY.replace(
        'dynamicRoots = ["packages", "overlays"]',
        'dynamicRoots = ["dynamic.py"]',
    )
    for root in (repo_root, runtime_source_root):
        _write_update_file(root, "cli.py", "VALUE = 1\n")
        (root / "pyproject.toml").write_text(policy, encoding="utf-8")
        (root / "dynamic.py").write_text("VALUE = 1\n", encoding="utf-8")

    assert update_library_matches_checkout(
        repo_root,
        runtime_source_root=runtime_source_root,
    )

    (repo_root / "dynamic.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert not update_library_matches_checkout(
        repo_root,
        runtime_source_root=runtime_source_root,
    )

    (repo_root / "dynamic.py").unlink()
    assert not update_library_matches_checkout(
        repo_root,
        runtime_source_root=runtime_source_root,
    )


@pytest.mark.parametrize("root_path", ["missing.py", "../outside", "/outside"])
def test_update_library_rejects_invalid_declared_runtime_roots(
    root_path: str,
    tmp_path: Path,
) -> None:
    """Missing or checkout-escaping source roots must force a safe re-exec."""
    repo_root = tmp_path / "repo"
    runtime_source_root = tmp_path / "runtime-root"
    policy = _RUNTIME_SOURCE_POLICY.replace(
        'rootPaths = ["nixcfg.py", "pyproject.toml", "uv.lock"]',
        f'rootPaths = ["{root_path}"]',
    )
    for root in (repo_root, runtime_source_root):
        _write_update_file(root, "cli.py", "VALUE = 1\n")
        (root / "pyproject.toml").write_text(policy, encoding="utf-8")

    assert not update_library_matches_checkout(
        repo_root,
        runtime_source_root=runtime_source_root,
    )


def test_update_library_rejects_invalid_or_different_runtime_policy(
    tmp_path: Path,
) -> None:
    """Fail closed when either packaged policy cannot define the same boundary."""
    repo_root = tmp_path / "repo"
    runtime_source_root = tmp_path / "runtime-root"
    for root in (repo_root, runtime_source_root):
        _write_update_file(root, "cli.py", "VALUE = 1\n")

    runtime_policy = runtime_source_root / "pyproject.toml"
    runtime_policy.write_text("[", encoding="utf-8")
    assert not update_library_matches_checkout(
        repo_root,
        runtime_source_root=runtime_source_root,
    )

    runtime_policy.write_text(
        _RUNTIME_SOURCE_POLICY.replace(
            'libraryNames = ["py.typed"]',
            'libraryNames = ["py.typed", "runtime.marker"]',
        ),
        encoding="utf-8",
    )
    assert not update_library_matches_checkout(
        repo_root,
        runtime_source_root=runtime_source_root,
    )


def test_update_library_uses_packaged_runtime_source_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The installed wrapper's source boundary must drive the skew comparison."""
    repo_root = tmp_path / "repo"
    runtime_source_root = tmp_path / "runtime-root"
    for root in (repo_root, runtime_source_root):
        _write_update_file(root, "cli.py", "VALUE = 1\n")
        (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    monkeypatch.setenv("NIXCFG_UPDATE_EXECUTION_SOURCE", str(runtime_source_root))

    assert update_library_matches_checkout(repo_root)

    (repo_root / "uv.lock").write_text("version = 2\n", encoding="utf-8")
    assert not update_library_matches_checkout(repo_root)


@pytest.mark.parametrize(
    ("policy", "error_type", "message"),
    [
        (
            _RUNTIME_SOURCE_POLICY.replace("schemaVersion = 1", "schemaVersion = 2"),
            RuntimeError,
            "schema",
        ),
        (
            _RUNTIME_SOURCE_POLICY.replace("schemaVersion = 1", "schemaVersion = true"),
            RuntimeError,
            "schema",
        ),
        (
            _RUNTIME_SOURCE_POLICY.replace(
                'rootPaths = ["nixcfg.py", "pyproject.toml", "uv.lock"]',
                'rootPaths = "nixcfg.py"',
            ),
            TypeError,
            "rootPaths",
        ),
        (
            '[tool.nixcfg]\nruntimeSource = "invalid"\n',
            TypeError,
            "policy table",
        ),
        (
            _RUNTIME_SOURCE_POLICY.replace(
                'dynamicRoots = ["packages", "overlays"]',
                'dynamicRoots = ["../outside"]',
            ),
            TypeError,
            "dynamicRoots",
        ),
        (
            _RUNTIME_SOURCE_POLICY.replace(
                'excludedLibraryPaths = ["tests"]',
                'excludedLibraryPaths = ["/outside"]',
            ),
            TypeError,
            "excludedLibraryPaths",
        ),
    ],
)
def test_runtime_source_policy_rejects_invalid_metadata(
    policy: str,
    error_type: type[Exception],
    message: str,
    tmp_path: Path,
) -> None:
    """Malformed source policy must fail closed before a skew comparison."""
    (tmp_path / "pyproject.toml").write_text(policy, encoding="utf-8")

    with pytest.raises(error_type, match=message):
        runtime_source_policy(tmp_path)


def _equal_runtime_trees(tmp_path: Path) -> tuple[Path, Path]:
    repo_root = tmp_path / "repo"
    runtime_source_root = tmp_path / "runtime-root"
    for root in (repo_root, runtime_source_root):
        _write_update_file(root, "cli.py", "VALUE = 1\n")
    return repo_root, runtime_source_root


def test_update_library_compares_symlink_identity_not_target_bytes(
    tmp_path: Path,
) -> None:
    """Equal file bytes must not conceal a changed packaged symlink contract."""
    repo_root, runtime_source_root = _equal_runtime_trees(tmp_path)
    for root in (repo_root, runtime_source_root):
        update_dir = root / "lib" / "update"
        (update_dir / "target.py").write_text("VALUE = 1\n", encoding="utf-8")
        (update_dir / "same_target.py").write_text("VALUE = 1\n", encoding="utf-8")
        (update_dir / "link.py").symlink_to("target.py")
    assert update_library_matches_checkout(
        repo_root, runtime_source_root=runtime_source_root
    )

    runtime_link = runtime_source_root / "lib" / "update" / "link.py"
    runtime_link.unlink()
    runtime_link.symlink_to("same_target.py")
    assert not update_library_matches_checkout(
        repo_root, runtime_source_root=runtime_source_root
    )

    runtime_link.unlink()
    runtime_link.write_text("VALUE = 1\n", encoding="utf-8")
    assert not update_library_matches_checkout(
        repo_root, runtime_source_root=runtime_source_root
    )


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
def test_update_library_fails_closed_when_a_runtime_file_is_unreadable(
    tmp_path: Path,
) -> None:
    """A file that cannot be compared is source skew, not a crash."""
    repo_root, runtime_source_root = _equal_runtime_trees(tmp_path)
    unreadable = runtime_source_root / "lib" / "update" / "cli.py"
    unreadable.chmod(0)
    try:
        assert not update_library_matches_checkout(
            repo_root, runtime_source_root=runtime_source_root
        )
    finally:
        unreadable.chmod(stat.S_IRUSR | stat.S_IWUSR)


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["nixcfg", "update"], True),
        (["nixcfg", "--nix-option", "builders = x", "update"], True),
        (["nixcfg", "-N", "builders = x", "update"], True),
        (["nixcfg", "--nix-option=builders = x", "update"], True),
        (["nixcfg", "-Nbuilders = x", "update"], True),
        (
            [
                "nixcfg",
                "--nix-option=builders = x",
                "-N",
                "cores = 2",
                "update",
            ],
            True,
        ),
        (["nixcfg", "--nix-option"], False),
        (["nixcfg", "-N"], False),
        (["nixcfg", "--nix-option", "update"], False),
        (["nixcfg", "-N", "update"], False),
        (["nixcfg", "--unknown", "value", "update"], False),
        (["nixcfg", "ci", "update"], False),
    ],
)
def test_top_level_update_detection_skips_only_global_nix_options(
    argv: list[str], expected: bool
) -> None:
    """Consume supported global option values without mistaking them for commands."""
    assert _argv_runs_top_level_update(argv) is expected


@pytest.mark.parametrize("option", ["--nix-option", "-N"])
def test_revalidation_detects_skew_after_global_nix_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, option: str
) -> None:
    """Source-race revalidation recognizes update after either global option form."""
    monkeypatch.setattr(
        sys, "argv", ["nixcfg", option, "builders = x", "update", "--check"]
    )
    monkeypatch.delenv(EXECUTION_SOURCE_ENV, raising=False)
    monkeypatch.setattr(
        "lib.update.runtime_source.update_library_matches_checkout", lambda _root: False
    )

    with pytest.raises(UpdateSourceChangedError, match="retry `nixcfg update`"):
        revalidate_runtime_source_snapshot(tmp_path)


def test_revalidation_is_a_no_op_outside_update_runs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Library callers without an update argv or packaged source skip the check."""
    monkeypatch.setattr(sys, "argv", ["nixcfg", "ci", "update"])
    monkeypatch.delenv(EXECUTION_SOURCE_ENV, raising=False)

    revalidate_runtime_source_snapshot(tmp_path / "missing")


def test_revalidation_accepts_matching_and_rejects_changed_snapshots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The captured workspace must still match the packaged runtime source."""
    workspace_root, runtime_source_root = _equal_runtime_trees(tmp_path)
    monkeypatch.setattr(sys, "argv", ["nixcfg"])
    monkeypatch.setenv(EXECUTION_SOURCE_ENV, str(runtime_source_root))

    revalidate_runtime_source_snapshot(workspace_root)

    _write_update_file(workspace_root, "cli.py", "VALUE = 2\n")
    with pytest.raises(UpdateSourceChangedError, match="retry `nixcfg update`"):
        revalidate_runtime_source_snapshot(workspace_root)


def _use_repo_root(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """Point real repository discovery at ``root`` for this test."""
    monkeypatch.setenv("REPO_ROOT", str(root))


def test_reexec_skips_non_update_commands_and_matching_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Only a top-level update with skewed runtime code hands off."""
    repo_root, runtime_source_root = _equal_runtime_trees(tmp_path)
    monkeypatch.setenv(EXECUTION_SOURCE_ENV, str(tmp_path / "absent"))
    monkeypatch.setattr(sys, "argv", ["nixcfg", "ci", "update"])
    assert maybe_reexec_checkout_update() is None
    monkeypatch.setattr(sys, "argv", ["nixcfg"])
    assert maybe_reexec_checkout_update() is None

    monkeypatch.setattr(sys, "argv", ["nixcfg", "update"])
    monkeypatch.setenv(EXECUTION_SOURCE_ENV, str(runtime_source_root))
    _use_repo_root(monkeypatch, repo_root)
    assert maybe_reexec_checkout_update() is None


def test_reexec_reports_recursion_and_missing_nix(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Recursion and missing nix produce actionable errors instead of skewed imports."""
    repo_root, _runtime_source_root = _equal_runtime_trees(tmp_path)
    monkeypatch.setattr(sys, "argv", ["nixcfg", "update"])
    monkeypatch.setenv(EXECUTION_SOURCE_ENV, str(tmp_path / "absent"))
    _use_repo_root(monkeypatch, repo_root)
    monkeypatch.setenv(REEXEC_ENV, "1")

    assert maybe_reexec_checkout_update() == 1
    assert "still differs" in capsys.readouterr().err

    monkeypatch.delenv(REEXEC_ENV)
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))

    assert maybe_reexec_checkout_update() == 1
    assert "nix` was not found" in capsys.readouterr().err


_FAKE_NIX = """#!/bin/sh
snapshot="${2#path:}"
snapshot="${snapshot%#nixcfg}"
printf '%s\n' "$@" > "$NIX_LOG/args"
printf '%s\n' "$NIXCFG_UPDATE_REEXECED_FROM_CHECKOUT" "$REPO_ROOT" "$(pwd -P)" \
    > "$NIX_LOG/env"
cat "$snapshot/tracked.txt" > "$NIX_LOG/snapshot"
exit 23
"""


@pytest.mark.parametrize(
    "global_args",
    [
        (),
        ("--nix-option", "builders = ssh-ng://builder"),
        ("-N", "builders = ssh-ng://builder"),
    ],
)
def test_update_command_hands_off_to_a_checkout_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    global_args: tuple[str, ...],
) -> None:
    """A skewed runtime re-runs identical global and update argv from a snapshot."""
    repo_root = tmp_path / "repo"
    init_update_workspace_repo(repo_root, tracked_files={"tracked.txt": "live\n"})
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_nix = bin_dir / "nix"
    fake_nix.write_text(_FAKE_NIX, encoding="utf-8")
    fake_nix.chmod(stat.S_IRWXU)
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("NIX_LOG", str(log_dir))
    monkeypatch.setenv(EXECUTION_SOURCE_ENV, str(tmp_path / "absent"))
    monkeypatch.delenv(REEXEC_ENV, raising=False)
    monkeypatch.setattr(
        sys, "argv", ["nixcfg", *global_args, "update", "--check", "t3code"]
    )
    _use_repo_root(monkeypatch, repo_root)

    assert cli.run_update_command() == 23

    args = (log_dir / "args").read_text(encoding="utf-8").splitlines()
    assert args[:2] == ["run", args[1]]
    assert args[1].startswith("path:")
    assert args[1].endswith("#nixcfg")
    assert args[2:] == ["--", *global_args, "update", "--check", "t3code"]
    assert (log_dir / "snapshot").read_text(encoding="utf-8") == "live\n"
    reexeced, repo_env, cwd = (log_dir / "env").read_text(encoding="utf-8").splitlines()
    assert reexeced == "1"
    assert Path(repo_env).resolve() == repo_root.resolve()
    assert Path(cwd).resolve() == repo_root.resolve()
