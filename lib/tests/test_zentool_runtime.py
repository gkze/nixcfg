"""Focused pure-Python tests for zentool runtime and profile helpers."""

from __future__ import annotations

import configparser
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lib.tests._zen_tooling import load_zen_script_module

if TYPE_CHECKING:
    from types import ModuleType


@pytest.fixture(scope="module")
def zentool() -> ModuleType:
    """Load the zentool script for direct runtime-helper testing."""
    return load_zen_script_module("zentool", "zentool_runtime_helpers")


def write_profiles_ini(path: Path, content: str) -> None:
    """Write a small profiles.ini fixture file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")


def test_load_profiles_ini_parses_valid_ini(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """The helper should return a parser when profiles.ini is readable."""
    profiles_ini = tmp_path / "profiles.ini"
    write_profiles_ini(
        profiles_ini,
        """
        [Profile0]
        Name = Default (twilight)
        Path = Profiles/default
        Default = 1
        """,
    )
    monkeypatch.setattr(zentool, "PROFILES_INI", profiles_ini)

    parser = zentool._load_profiles_ini()

    assert isinstance(parser, configparser.RawConfigParser)
    assert parser.get("Profile0", "Name") == "Default (twilight)"


def test_load_profiles_ini_returns_none_for_parse_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Unreadable ini syntax should be treated as absent config."""
    profiles_ini = tmp_path / "profiles.ini"
    write_profiles_ini(profiles_ini, "[")
    monkeypatch.setattr(zentool, "PROFILES_INI", profiles_ini)

    assert zentool._load_profiles_ini() is None


def test_profiles_from_ini_resolves_relative_and_absolute_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Profile parsing should normalize relative paths and preserve defaults."""
    app_support = tmp_path / "Library" / "Application Support" / "zen"
    profiles_ini = app_support / "profiles.ini"
    absolute = tmp_path / "absolute-profile"
    write_profiles_ini(
        profiles_ini,
        f"""
        [General]
        StartWithLastProfile = 1

        [Profile0]
        Name = Default (twilight)
        Path = Profiles/default
        Default = 1

        [Profile1]
        Name = Work
        Path = {absolute}

        [Profile2]
        Name = MissingPath
        Path =
        """,
    )
    monkeypatch.setattr(zentool, "ZEN_APPLICATION_SUPPORT", app_support)
    monkeypatch.setattr(zentool, "PROFILES_INI", profiles_ini)

    assert zentool._profiles_from_ini() == [
        ("Default (twilight)", app_support / "Profiles/default", True),
        ("Work", absolute, False),
    ]


def test_default_profile_dir_prefers_install_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Install-level defaults should win before profile flags or directory fallback."""
    app_support = tmp_path / "app-support"
    profiles_ini = app_support / "profiles.ini"
    write_profiles_ini(
        profiles_ini,
        """
        [Install123]
        Default = Profiles/install-default

        [Profile0]
        Name = Default (twilight)
        Path = Profiles/profile-default
        Default = 1
        """,
    )
    monkeypatch.setattr(zentool, "ZEN_APPLICATION_SUPPORT", app_support)
    monkeypatch.setattr(zentool, "PROFILES_INI", profiles_ini)

    assert zentool._default_profile_dir() == app_support / "Profiles/install-default"


def test_default_profile_dir_falls_back_to_first_profile_then_single_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Profile and directory fallback should stay deterministic when ini lacks defaults."""
    profiles_dir = tmp_path / "Profiles"
    lone_profile = profiles_dir / "solo"
    lone_profile.mkdir(parents=True)
    monkeypatch.setattr(zentool, "ZEN_PROFILES", profiles_dir)
    monkeypatch.setattr(
        zentool,
        "_load_profiles_ini",
        lambda: None,
    )
    monkeypatch.setattr(
        zentool,
        "_profiles_from_ini",
        lambda: [
            ("First", tmp_path / "from-ini", False),
            ("Second", tmp_path / "two", False),
        ],
    )

    assert zentool._default_profile_dir() == tmp_path / "from-ini"

    monkeypatch.setattr(zentool, "_profiles_from_ini", list)
    assert zentool._default_profile_dir() == lone_profile


def test_available_profile_hints_prefers_names_then_directory_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Hints should use profile names when available, then fall back to directories."""
    monkeypatch.setattr(
        zentool,
        "_profiles_from_ini",
        lambda: [
            ("Default (twilight)", tmp_path / "p1", True),
            ("Work", tmp_path / "p2", False),
        ],
    )

    assert zentool._available_profile_hints() == "Default (twilight), Work"

    profiles_dir = tmp_path / "Profiles"
    (profiles_dir / "beta").mkdir(parents=True)
    (profiles_dir / "alpha").mkdir()
    monkeypatch.setattr(zentool, "ZEN_PROFILES", profiles_dir)
    monkeypatch.setattr(
        zentool, "_profiles_from_ini", lambda: [("", tmp_path / "p1", False)]
    )

    assert zentool._available_profile_hints() == "alpha, beta"


def test_available_profile_hints_reports_none_when_no_profiles_exist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """The missing-profile message should stay explicit."""
    monkeypatch.setattr(zentool, "ZEN_PROFILES", tmp_path / "missing-profiles")
    monkeypatch.setattr(zentool, "_profiles_from_ini", list)

    assert zentool._available_profile_hints() == "<none found>"


def test_resolve_profile_dir_supports_auto_detect_path_directory_and_name_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Profile resolution should accept each supported selector form."""
    auto_detected = tmp_path / "auto"
    direct_dir = tmp_path / "direct"
    direct_dir.mkdir()
    session_path = direct_dir / zentool.SESSION_FILENAME
    session_path.write_text("session", encoding="utf-8")
    profiles_dir = tmp_path / "Profiles"
    named_dir = profiles_dir / "named-dir"
    named_dir.mkdir(parents=True)

    monkeypatch.setattr(zentool, "_default_profile_dir", lambda: auto_detected)
    monkeypatch.setattr(zentool, "ZEN_PROFILES", profiles_dir)
    monkeypatch.setattr(
        zentool,
        "_profiles_from_ini",
        lambda: [("Work", tmp_path / "named-profile", False)],
    )

    assert zentool.resolve_profile_dir(None) == auto_detected
    assert zentool.resolve_profile_dir(str(session_path)) == direct_dir
    assert zentool.resolve_profile_dir(str(direct_dir)) == direct_dir
    assert zentool.resolve_profile_dir("named-dir") == named_dir
    assert zentool.resolve_profile_dir("work") == tmp_path / "named-profile"
    assert zentool.zen_profile_dir("work") == tmp_path / "named-profile"


def test_resolve_profile_dir_reports_missing_profiles_with_hints(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Unknown profile names should surface the available-hints message."""
    missing = Path("unknown-profile")
    monkeypatch.setattr(
        zentool, "_available_profile_hints", lambda: "Default (twilight)"
    )
    monkeypatch.setattr(zentool, "_profiles_from_ini", list)
    monkeypatch.setattr(zentool, "ZEN_PROFILES", Path("/definitely-missing"))

    with pytest.raises(
        zentool.ZenFoldersError, match=r"Available: Default \(twilight\)"
    ):
        zentool.resolve_profile_dir(str(missing))


def test_session_file_prefers_explicit_file_and_validates_profile_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Session lookup should accept explicit files and validate derived targets."""
    explicit = tmp_path / "explicit.jsonlz4"
    explicit.write_text("session", encoding="utf-8")
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    session = profile_dir / zentool.SESSION_FILENAME
    session.write_text("session", encoding="utf-8")
    monkeypatch.setattr(zentool, "zen_profile_dir", lambda _profile: profile_dir)

    assert zentool.session_file(str(explicit)) == explicit
    assert zentool.session_file("ignored") == session

    session.unlink()
    with pytest.raises(zentool.ZenFoldersError, match="Session file not found"):
        zentool.session_file("ignored")


def test_zen_lock_paths_returns_only_existing_lock_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Lock discovery should filter to the lock files that exist now."""
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    existing = profile_dir / ".parentlock"
    existing.write_text("", encoding="utf-8")
    monkeypatch.setattr(zentool, "zen_profile_dir", lambda _profile: profile_dir)

    assert zentool.zen_lock_paths(None) == [existing]


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        ([True], True),
        ([None], None),
        ([False, None], None),
        ([False, False], False),
    ],
)
def test_zen_profile_lock_state_combines_probe_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
    *,
    states: list[bool | None],
    expected: bool | None,
) -> None:
    """Lock-state aggregation should preserve definite and uncertain results."""
    lock_paths = [tmp_path / f"lock-{index}" for index in range(len(states))]
    for path in lock_paths:
        path.write_text("", encoding="utf-8")
    states_by_path = dict(zip(lock_paths, states, strict=True))
    monkeypatch.setattr(zentool, "zen_lock_paths", lambda _profile: lock_paths)
    monkeypatch.setattr(zentool, "_lock_probe_state", lambda path: states_by_path[path])

    assert zentool.zen_profile_lock_state(None) is expected


def test_zen_process_is_running_reports_true_false_and_unknown(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Process detection should scan ps output and tolerate subprocess failures."""
    monkeypatch.setattr(
        zentool,
        "subprocess_run",
        lambda _command, *, capture: "/Applications/Zen.app/Contents/MacOS/zen\n",
    )
    assert zentool.zen_process_is_running() is True

    monkeypatch.setattr(
        zentool, "subprocess_run", lambda _command, *, capture: "/usr/bin/python\n"
    )
    assert zentool.zen_process_is_running() is False

    def raise_run(_command: object, *, capture: bool) -> str:
        raise zentool.ZenFoldersError("ps failed")

    monkeypatch.setattr(zentool, "subprocess_run", raise_run)
    assert zentool.zen_process_is_running() is None


def test_inspect_zen_runtime_uses_lock_state_then_process_then_lock_presence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Runtime inspection should keep the narrowest definite signal available."""
    lock_path = tmp_path / ".parentlock"
    lock_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(zentool, "zen_lock_paths", lambda _profile: [lock_path])
    monkeypatch.setattr(zentool, "zen_profile_lock_state", lambda _profile: True)
    assert zentool.inspect_zen_runtime(None) == zentool.ZenRuntimeState(running=True)

    monkeypatch.setattr(zentool, "zen_profile_lock_state", lambda _profile: None)
    monkeypatch.setattr(zentool, "zen_process_is_running", lambda: False)
    assert zentool.inspect_zen_runtime(None) == zentool.ZenRuntimeState(
        running=False,
        warnings=(
            "Could not confirm whether the profile lock is active; falling back to process inspection.",
        ),
    )

    monkeypatch.setattr(zentool, "zen_process_is_running", lambda: None)
    assert zentool.inspect_zen_runtime(None) == zentool.ZenRuntimeState(
        running=True,
        warnings=(
            "Could not confirm whether the profile lock is active; falling back to process inspection.",
            "Could not inspect Zen processes either; treating existing profile lock files as active.",
        ),
    )


def test_require_zen_closed_returns_state_or_raises_with_detection_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """The guard should pass through safe state and explain uncertain failures."""
    session = tmp_path / zentool.SESSION_FILENAME
    session.write_text("session", encoding="utf-8")
    monkeypatch.setattr(zentool, "session_file", lambda _profile: session)

    safe = zentool.ZenRuntimeState(running=False)
    monkeypatch.setattr(zentool, "inspect_zen_runtime", lambda _profile: safe)
    assert zentool.require_zen_closed(None) == safe

    running = zentool.ZenRuntimeState(running=True, warnings=("process fallback used",))
    monkeypatch.setattr(zentool, "inspect_zen_runtime", lambda _profile: running)

    with pytest.raises(
        zentool.ZenFoldersError, match="Detection details: process fallback used"
    ):
        zentool.require_zen_closed(None)
