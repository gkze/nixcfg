"""Focused finishing tests for remaining zentool profile/config branches."""

from __future__ import annotations

import configparser
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lib.tests._zen_tooling import load_zen_script_module

if TYPE_CHECKING:
    from types import ModuleType


@pytest.fixture(scope="module")
def zentool() -> ModuleType:
    """Load the zentool script for profile/config finishing tests."""
    return load_zen_script_module("zentool", "zentool_profile_finishing")


def test_load_config_returns_valid_workspace_spec(
    tmp_path: Path,
    zentool: ModuleType,
) -> None:
    """Valid authored workspace mappings should return a parsed config model."""
    config_path = tmp_path / "folders.yaml"
    config_path.write_text(
        "Work:\n  - Inbox: https://mail.example.com\n", encoding="utf-8"
    )

    assert zentool.load_config(config_path) == zentool.ZenConfig(
        workspaces=[
            zentool.WorkspaceSpec(
                name="Work",
                items=[
                    zentool.ItemTabSpec(
                        name="Inbox",
                        url="https://mail.example.com",
                    )
                ],
            )
        ]
    )


def test_default_profile_dir_skips_non_install_and_blank_install_before_default_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Install parsing should fall through until a profile-marked default is found."""
    parser = configparser.RawConfigParser()
    parser.add_section("Profile0")
    parser.set("Profile0", "Path", "Profiles/ignored")
    parser.add_section("Install0")
    parser.set("Install0", "Default", "   ")
    parser.add_section("Other")

    fallback = tmp_path / "Profiles/default-profile"
    monkeypatch.setattr(zentool, "_load_profiles_ini", lambda: parser)
    monkeypatch.setattr(
        zentool,
        "_profiles_from_ini",
        lambda: [
            ("Default", fallback, True),
            ("Other", tmp_path / "Profiles/other", False),
        ],
    )

    assert zentool._default_profile_dir() == fallback


def test_available_profile_hints_reports_none_for_existing_empty_profiles_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Existing profile roots without names or directories should stay explicit."""
    profiles_dir = tmp_path / "Profiles"
    profiles_dir.mkdir()
    (profiles_dir / "notes.txt").write_text("not a profile\n", encoding="utf-8")
    monkeypatch.setattr(zentool, "ZEN_PROFILES", profiles_dir)
    monkeypatch.setattr(
        zentool, "_profiles_from_ini", lambda: [("", tmp_path / "unused", False)]
    )

    assert zentool._available_profile_hints() == "<none found>"


def test_resolve_profile_dir_rejects_existing_file_and_matches_later_named_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Resolution should reject existing files and keep scanning named-profile entries."""
    not_dir = tmp_path / "not-a-directory"
    os.mkfifo(not_dir)

    with pytest.raises(
        zentool.ZenFoldersError, match=rf"Profile path is not a directory: {not_dir}"
    ):
        zentool.resolve_profile_dir(str(not_dir))

    monkeypatch.setattr(zentool, "ZEN_PROFILES", tmp_path / "Profiles")
    monkeypatch.setattr(
        zentool,
        "_profiles_from_ini",
        lambda: [
            ("Alpha", tmp_path / "alpha-profile", False),
            ("Work", tmp_path / "work-profile", False),
        ],
    )

    assert zentool.resolve_profile_dir("work") == tmp_path / "work-profile"


def test_session_file_accepts_none_and_uses_profile_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Missing explicit profile args should still resolve through the profile helper."""
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    session = profile_dir / zentool.SESSION_FILENAME
    session.write_text("session", encoding="utf-8")
    seen: list[str | None] = []
    monkeypatch.setattr(
        zentool, "zen_profile_dir", lambda profile: seen.append(profile) or profile_dir
    )

    assert zentool.session_file(None) == session
    assert seen == [None]


def test_require_zen_closed_running_without_warnings_omits_detection_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Running-profile failures without warnings should not append detection details."""
    session = tmp_path / zentool.SESSION_FILENAME
    session.write_text("session", encoding="utf-8")
    monkeypatch.setattr(zentool, "session_file", lambda _profile: session)
    monkeypatch.setattr(
        zentool,
        "inspect_zen_runtime",
        lambda _profile: zentool.ZenRuntimeState(running=True),
    )

    with pytest.raises(
        zentool.ZenFoldersError, match=r"Zen is running\. Quit it first\."
    ) as exc:
        zentool.require_zen_closed(None)

    assert "Detection details:" not in str(exc.value)


def test_build_desired_state_initializes_split_view_when_model_extra_is_none(
    zentool: ModuleType,
) -> None:
    """Desired-state assembly should recreate extras when a copied session lacks them."""
    existing = zentool.SessionState.model_construct(
        tabs=[], groups=[], folders=[], spaces=[]
    )
    existing.__pydantic_extra__ = None

    result = zentool.build_desired_state(existing, zentool.ZenConfig())

    assert existing.model_extra is None
    assert result.model_extra == {"splitViewData": []}
