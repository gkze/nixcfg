"""Regression tests for the zen-profile-sync script."""

from __future__ import annotations

import getpass
import importlib.machinery
import importlib.util
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from lib.update.paths import REPO_ROOT

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType


def _resolve_zen_profile_sync_path() -> Path:
    preferred = REPO_ROOT / f"home/{getpass.getuser()}/bin/zen-profile-sync"
    if preferred.is_file():
        return preferred

    candidates = sorted((REPO_ROOT / "home").glob("*/bin/zen-profile-sync"))
    if len(candidates) == 1:
        return candidates[0]

    if candidates:
        candidate_paths = ", ".join(
            str(path.relative_to(REPO_ROOT)) for path in candidates
        )
        msg = (
            f"Unable to resolve zen-profile-sync for user {getpass.getuser()!r}; "
            f"candidates: {candidate_paths}"
        )
        raise RuntimeError(msg)

    msg = "Unable to locate zen-profile-sync under home/*/bin/zen-profile-sync"
    raise RuntimeError(msg)


ZEN_PROFILE_SYNC_PATH = _resolve_zen_profile_sync_path()


def _load_zen_profile_sync_module() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader(
        "zen_profile_sync_script",
        str(ZEN_PROFILE_SYNC_PATH),
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None or spec.loader is None:
        msg = "failed to load zen-profile-sync module spec"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def zen_profile_sync() -> ModuleType:
    """Load the zen-profile-sync script as a module for direct function testing."""
    return _load_zen_profile_sync_module()


def test_sync_profile_applies_default_managed_files_when_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zen_profile_sync: ModuleType,
) -> None:
    """Default config-dir sources should sync and apply folders when Zen is closed."""
    config_dir = tmp_path / "config"
    chrome_dir = config_dir / "chrome"
    chrome_dir.mkdir(parents=True)
    chrome_file = chrome_dir / "userChrome.css"
    chrome_file.write_text("/* theme */\n", encoding="utf-8")
    user_js = config_dir / "user.js"
    user_js.write_text(
        "user_pref('toolkit.legacyUserProfileCustomizations.stylesheets', true);\n",
        encoding="utf-8",
    )
    folders_yaml = config_dir / "folders.yaml"
    folders_yaml.write_text("Work: {}\n", encoding="utf-8")
    profile_dir = tmp_path / "profile"

    apply_calls: list[tuple[str, str | None, Path]] = []
    monkeypatch.setattr(
        zen_profile_sync,
        "resolve_profile_dir",
        lambda folders_command, profile: profile_dir,
    )
    monkeypatch.setattr(zen_profile_sync, "zen_is_running", lambda *_args: False)
    monkeypatch.setattr(
        zen_profile_sync,
        "apply_folders",
        lambda folders_command, profile, folders_config: apply_calls.append((
            folders_command,
            profile,
            folders_config,
        )),
    )

    args = zen_profile_sync.build_parser().parse_args([
        "--config-dir",
        str(config_dir),
        "--profile",
        "Default (twilight)",
    ])
    assert zen_profile_sync.sync_profile(args) == 0

    profile_chrome_file = profile_dir / "chrome" / "userChrome.css"
    assert profile_chrome_file.is_symlink()
    assert profile_chrome_file.resolve() == chrome_file.resolve()
    profile_user_js = profile_dir / "user.js"
    assert profile_user_js.is_symlink()
    assert profile_user_js.resolve() == user_js.resolve()
    assert (profile_dir / zen_profile_sync.MANAGED_CHROME_MANIFEST).read_text(
        encoding="utf-8"
    ) == "userChrome.css\n"
    assert apply_calls == [
        (
            "zen-folders",
            "Default (twilight)",
            folders_yaml.resolve(),
        )
    ]


def test_sync_profile_skips_folder_apply_when_running_and_preserves_unmanaged_user_js(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    zen_profile_sync: ModuleType,
) -> None:
    """Running Zen should skip apply, clean stale chrome links, and keep unrelated user.js."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "folders.yaml").write_text("Work: {}\n", encoding="utf-8")

    profile_dir = tmp_path / "profile"
    stale_target = tmp_path / "old.css"
    stale_target.write_text("stale\n", encoding="utf-8")
    stale_link = profile_dir / "chrome" / "stale.css"
    stale_link.parent.mkdir(parents=True)
    stale_link.symlink_to(stale_target)
    (profile_dir / zen_profile_sync.MANAGED_CHROME_MANIFEST).write_text(
        "stale.css\n",
        encoding="utf-8",
    )

    unmanaged_user_js_target = tmp_path / "custom-user.js"
    unmanaged_user_js_target.write_text("custom\n", encoding="utf-8")
    unmanaged_user_js = profile_dir / "user.js"
    unmanaged_user_js.symlink_to(unmanaged_user_js_target)

    apply_calls: list[tuple[str, str | None, Path]] = []
    monkeypatch.setattr(
        zen_profile_sync,
        "resolve_profile_dir",
        lambda folders_command, profile: profile_dir,
    )
    monkeypatch.setattr(zen_profile_sync, "zen_is_running", lambda *_args: True)
    monkeypatch.setattr(
        zen_profile_sync,
        "apply_folders",
        lambda folders_command, profile, folders_config: apply_calls.append((
            folders_command,
            profile,
            folders_config,
        )),
    )

    args = zen_profile_sync.build_parser().parse_args(["--config-dir", str(config_dir)])
    assert zen_profile_sync.sync_profile(args) == 0

    assert not stale_link.exists()
    assert not (profile_dir / zen_profile_sync.MANAGED_CHROME_MANIFEST).exists()
    assert unmanaged_user_js.is_symlink()
    assert unmanaged_user_js.resolve() == unmanaged_user_js_target.resolve()
    assert apply_calls == []
    assert "Zen is running; synced chrome files and prefs" in capsys.readouterr().err


def test_sync_profile_removes_previously_managed_explicit_user_js_when_omitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zen_profile_sync: ModuleType,
) -> None:
    """Omitting user.js later should clean up a previously managed explicit source."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()

    previous_source = tmp_path / "managed-user.js"
    previous_source.write_text("managed\n", encoding="utf-8")
    managed_user_js = profile_dir / "user.js"
    managed_user_js.symlink_to(previous_source)
    (profile_dir / zen_profile_sync.MANAGED_USER_JS_MANIFEST).write_text(
        f"{previous_source}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        zen_profile_sync,
        "resolve_profile_dir",
        lambda folders_command, profile: profile_dir,
    )

    args = zen_profile_sync.build_parser().parse_args([
        "--config-dir",
        str(config_dir),
        "--no-apply-folders",
    ])
    assert zen_profile_sync.sync_profile(args) == 0

    assert not managed_user_js.exists()
    assert not managed_user_js.is_symlink()
    assert not (profile_dir / zen_profile_sync.MANAGED_USER_JS_MANIFEST).exists()


def test_main_rejects_missing_explicit_chrome_source(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    zen_profile_sync: ModuleType,
) -> None:
    """An explicit bad chrome source path should fail immediately."""
    rc = zen_profile_sync.main([
        "--chrome-source",
        str(tmp_path / "missing"),
        "--no-apply-folders",
    ])

    assert rc == 1
    assert "chrome source directory not found" in capsys.readouterr().err


def test_resolve_profile_dir_raises_for_explicit_profile_failure(
    monkeypatch: pytest.MonkeyPatch,
    zen_profile_sync: ModuleType,
) -> None:
    """Explicit profile lookup failures should surface the zen-folders error."""
    monkeypatch.setattr(
        zen_profile_sync,
        "_run_zen_folders_capture",
        lambda _command: subprocess.CompletedProcess(
            args=["zen-folders", "--profile", "bad", "profile-path"],
            returncode=1,
            stdout="",
            stderr="Profile 'bad' not found.",
        ),
    )

    with pytest.raises(
        zen_profile_sync.ZenProfileSyncError,
        match="Profile 'bad' not found",
    ):
        zen_profile_sync.resolve_profile_dir("zen-folders", "bad")


def test_resolve_profile_dir_returns_none_for_auto_detect_miss(
    monkeypatch: pytest.MonkeyPatch,
    zen_profile_sync: ModuleType,
) -> None:
    """Auto-detect misses should still behave like the first-run no-profile case."""
    monkeypatch.setattr(
        zen_profile_sync,
        "_run_zen_folders_capture",
        lambda _command: subprocess.CompletedProcess(
            args=["zen-folders", "profile-path"],
            returncode=1,
            stdout="",
            stderr=(
                "Unable to auto-detect a Zen profile. Pass --profile with a profile "
                "name, directory, or session file path."
            ),
        ),
    )

    assert zen_profile_sync.resolve_profile_dir("zen-folders", None) is None


def test_resolve_profile_dir_raises_for_unexpected_auto_detect_failure(
    monkeypatch: pytest.MonkeyPatch,
    zen_profile_sync: ModuleType,
) -> None:
    """Unexpected auto-detect failures should surface instead of being ignored."""
    monkeypatch.setattr(
        zen_profile_sync,
        "_run_zen_folders_capture",
        lambda _command: subprocess.CompletedProcess(
            args=["zen-folders", "profile-path"],
            returncode=1,
            stdout="",
            stderr="profiles.ini parse failed",
        ),
    )

    with pytest.raises(
        zen_profile_sync.ZenProfileSyncError,
        match="profiles.ini parse failed",
    ):
        zen_profile_sync.resolve_profile_dir("zen-folders", None)
