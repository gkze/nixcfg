"""Regression tests for zentool asset operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from lib.tests._zen_tooling import load_zen_script_module, resolve_zen_script_path

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType


ZENTOOL_PATH = resolve_zen_script_path("zentool")


@pytest.fixture(scope="module")
def zentool() -> ModuleType:
    """Load the zentool script as a module for direct function testing."""
    return load_zen_script_module("zentool", "zentool_script")


def test_apply_assets_uses_default_managed_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Default asset-dir sources should sync managed chrome and user.js."""
    asset_dir = tmp_path / "config"
    chrome_dir = asset_dir / "chrome"
    chrome_dir.mkdir(parents=True)
    chrome_file = chrome_dir / "userChrome.css"
    chrome_file.write_text("/* theme */\n", encoding="utf-8")
    user_js = asset_dir / "user.js"
    user_js.write_text(
        "user_pref('toolkit.legacyUserProfileCustomizations.stylesheets', true);\n",
        encoding="utf-8",
    )
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()

    monkeypatch.setattr(zentool, "zen_profile_dir", lambda _profile: profile_dir)

    args = zentool.build_parser().parse_args([
        "--profile",
        "Default (twilight)",
        "apply",
        "--assets",
        "--asset-dir",
        str(asset_dir),
        "--yes",
    ])
    assert zentool.cmd_apply(args) == 0

    profile_chrome_file = profile_dir / "chrome" / "userChrome.css"
    assert profile_chrome_file.is_symlink()
    assert profile_chrome_file.resolve() == chrome_file.resolve()
    profile_user_js = profile_dir / "user.js"
    assert profile_user_js.is_symlink()
    assert profile_user_js.resolve() == user_js.resolve()
    assert (profile_dir / zentool.MANAGED_CHROME_MANIFEST).read_text(
        encoding="utf-8"
    ) == "userChrome.css\n"


def test_apply_assets_preserves_unmanaged_user_js_and_cleans_stale_chrome_links(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Asset apply should clean stale managed chrome links and keep unrelated user.js."""
    asset_dir = tmp_path / "config"
    asset_dir.mkdir()

    profile_dir = tmp_path / "profile"
    stale_target = tmp_path / "old.css"
    stale_target.write_text("stale\n", encoding="utf-8")
    stale_link = profile_dir / "chrome" / "stale.css"
    stale_link.parent.mkdir(parents=True)
    stale_link.symlink_to(stale_target)
    (profile_dir / zentool.MANAGED_CHROME_MANIFEST).write_text(
        "stale.css\n",
        encoding="utf-8",
    )

    unmanaged_user_js_target = tmp_path / "custom-user.js"
    unmanaged_user_js_target.write_text("custom\n", encoding="utf-8")
    unmanaged_user_js = profile_dir / "user.js"
    unmanaged_user_js.symlink_to(unmanaged_user_js_target)

    monkeypatch.setattr(zentool, "zen_profile_dir", lambda _profile: profile_dir)

    args = zentool.build_parser().parse_args([
        "apply",
        "--assets",
        "--asset-dir",
        str(asset_dir),
        "--yes",
    ])
    assert zentool.cmd_apply(args) == 0

    assert not stale_link.exists()
    assert not (profile_dir / zentool.MANAGED_CHROME_MANIFEST).exists()
    assert unmanaged_user_js.is_symlink()
    assert unmanaged_user_js.resolve() == unmanaged_user_js_target.resolve()


def test_apply_assets_removes_previously_managed_explicit_user_js_when_omitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Omitting user.js later should clean up a previously managed explicit source."""
    asset_dir = tmp_path / "config"
    asset_dir.mkdir()
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()

    previous_source = tmp_path / "managed-user.js"
    previous_source.write_text("managed\n", encoding="utf-8")
    managed_user_js = profile_dir / "user.js"
    managed_user_js.symlink_to(previous_source)
    (profile_dir / zentool.MANAGED_USER_JS_MANIFEST).write_text(
        f"{previous_source}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(zentool, "zen_profile_dir", lambda _profile: profile_dir)

    args = zentool.build_parser().parse_args([
        "apply",
        "--assets",
        "--asset-dir",
        str(asset_dir),
        "--yes",
    ])
    assert zentool.cmd_apply(args) == 0

    assert not managed_user_js.exists()
    assert not managed_user_js.is_symlink()
    assert not (profile_dir / zentool.MANAGED_USER_JS_MANIFEST).exists()


def test_main_rejects_missing_explicit_chrome_source(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    zentool: ModuleType,
) -> None:
    """An explicit bad chrome source path should fail immediately."""
    rc = zentool.main([
        "apply",
        "--assets",
        "--chrome-source",
        str(tmp_path / "missing"),
        "--yes",
    ])

    assert rc == 1
    assert "chrome source directory not found" in capsys.readouterr().err
