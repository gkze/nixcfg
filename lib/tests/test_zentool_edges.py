"""Focused pure-Python tests for zentool edge-helper branches."""

from __future__ import annotations

import errno
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from lib.tests._zen_tooling import load_zen_script_module

if TYPE_CHECKING:
    from types import ModuleType


@pytest.fixture(scope="module")
def zentool() -> ModuleType:
    """Load the zentool script for narrow edge-helper tests."""
    return load_zen_script_module("zentool", "zentool_edge_helpers")


def test_load_profiles_ini_and_profiles_from_ini_handle_missing_ini(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Absent ``profiles.ini`` should behave like no configured profiles."""
    profiles_ini = tmp_path / "missing" / "profiles.ini"
    monkeypatch.setattr(zentool, "PROFILES_INI", profiles_ini)

    assert zentool._load_profiles_ini() is None
    assert zentool._profiles_from_ini() == []


@pytest.mark.parametrize("directory_count", [0, 2])
def test_default_profile_dir_returns_none_without_unique_fallback_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
    directory_count: int,
) -> None:
    """Directory fallback should only pick a unique profile directory."""
    profiles_dir = tmp_path / "Profiles"
    monkeypatch.setattr(zentool, "ZEN_PROFILES", profiles_dir)
    monkeypatch.setattr(zentool, "_load_profiles_ini", lambda: None)
    monkeypatch.setattr(zentool, "_profiles_from_ini", list)

    if directory_count:
        profiles_dir.mkdir()
        for index in range(directory_count):
            (profiles_dir / f"profile-{index}").mkdir()

    assert zentool._default_profile_dir() is None


def test_resolve_profile_dir_reports_auto_detect_non_directory_and_absolute_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Profile resolution should preserve its less common error messages."""
    monkeypatch.setattr(zentool, "_default_profile_dir", lambda: None)

    with pytest.raises(
        zentool.ZenFoldersError, match="Unable to auto-detect a Zen profile"
    ):
        zentool.resolve_profile_dir("   ")

    profiles_dir = tmp_path / "Profiles"
    profiles_dir.mkdir()
    monkeypatch.setattr(zentool, "ZEN_PROFILES", profiles_dir)

    not_dir = profiles_dir / "named-profile"
    not_dir.write_text("x\n", encoding="utf-8")
    with pytest.raises(
        zentool.ZenFoldersError, match="Profile path is not a directory"
    ):
        zentool.resolve_profile_dir("named-profile")

    missing = tmp_path / "missing-profile"
    with pytest.raises(zentool.ZenFoldersError, match=rf"Profile not found: {missing}"):
        zentool.resolve_profile_dir(str(missing))


def test_resolve_profile_dir_matches_firefox_style_directory_display_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Human profile selectors should work without profiles.ini."""
    profiles_dir = tmp_path / "Profiles"
    profile_dir = profiles_dir / "vb4m4ab8.Default (twilight)"
    profile_dir.mkdir(parents=True)
    monkeypatch.setattr(zentool, "ZEN_PROFILES", profiles_dir)
    monkeypatch.setattr(zentool, "_profiles_from_ini", list)

    assert zentool.resolve_profile_dir("Default (twilight)") == profile_dir


def test_profile_display_name_without_firefox_prefix_uses_directory_name(
    zentool: ModuleType,
) -> None:
    """Unprefixed profile directories should keep their full directory name."""
    assert (
        zentool._profile_display_name_from_directory(Path("plain-profile"))
        == "plain-profile"
    )


def test_resolve_profile_dir_rejects_ambiguous_directory_display_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Display-name fallback should not pick an arbitrary profile."""
    profiles_dir = tmp_path / "Profiles"
    (profiles_dir / "aaaaaaaa.Default").mkdir(parents=True)
    (profiles_dir / "bbbbbbbb.Default").mkdir()
    monkeypatch.setattr(zentool, "ZEN_PROFILES", profiles_dir)
    monkeypatch.setattr(zentool, "_profiles_from_ini", list)

    with pytest.raises(zentool.ZenFoldersError, match="Profile 'Default' is ambiguous"):
        zentool.resolve_profile_dir("Default")


def test_session_file_rejects_non_file_session_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Derived session paths must exist as regular files."""
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    session_dir = profile_dir / zentool.SESSION_FILENAME
    session_dir.mkdir()
    monkeypatch.setattr(zentool, "zen_profile_dir", lambda _profile: profile_dir)

    with pytest.raises(zentool.ZenFoldersError, match="Session path is not a file"):
        zentool.session_file("default")


@pytest.mark.parametrize(
    ("decompress_error", "payload"),
    [
        pytest.param(RuntimeError("bad lz4"), None, id="decompress-error"),
        pytest.param(None, b"{not-json", id="json-error"),
    ],
)
def test_read_session_wraps_decode_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
    *,
    decompress_error: RuntimeError | None,
    payload: bytes | None,
) -> None:
    """Decode failures should stay behind the user-facing session error."""
    session_path = tmp_path / zentool.SESSION_FILENAME
    session_path.write_bytes(
        zentool.SESSION_HEADER_PREFIX + (8).to_bytes(4, "little") + b"raw"
    )

    def fake_decompress(_data: bytes, _size: int) -> bytes:
        if decompress_error is not None:
            raise decompress_error
        assert payload is not None
        return payload

    monkeypatch.setattr(zentool.lz4.block, "decompress", fake_decompress)

    with pytest.raises(
        zentool.SessionFormatError, match="Unable to decode session file"
    ):
        zentool.read_session(session_path)


def test_lock_probe_state_covers_platform_and_lock_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """The lock probe should preserve unknown, active, and inactive outcomes."""

    class FakeFcntl:
        LOCK_EX = 1
        LOCK_NB = 2
        LOCK_UN = 4

        def __init__(self) -> None:
            self.mode = "success"
            self.calls: list[tuple[int, int]] = []

        def lockf(self, fd: int, flags: int) -> None:
            self.calls.append((fd, flags))
            if flags == self.LOCK_UN:
                return
            if self.mode == "busy":
                raise OSError(errno.EAGAIN, "busy")
            if self.mode == "error":
                raise OSError(errno.EPERM, "nope")

    path = Path("/tmp/fake-lock")
    fake_fcntl = FakeFcntl()
    closed: list[int] = []

    monkeypatch.setattr(zentool, "fcntl", None)
    assert zentool._lock_probe_state(path) is None

    monkeypatch.setattr(zentool, "fcntl", fake_fcntl)

    def raise_open(_path: Path, _flags: int) -> int:
        raise OSError("missing")

    monkeypatch.setattr(zentool.os, "open", raise_open)
    assert zentool._lock_probe_state(path) is None

    monkeypatch.setattr(zentool.os, "open", lambda _path, _flags: 11)
    monkeypatch.setattr(zentool.os, "close", closed.append)

    fake_fcntl.mode = "busy"
    assert zentool._lock_probe_state(path) is True

    fake_fcntl.mode = "error"
    assert zentool._lock_probe_state(path) is None

    fake_fcntl.mode = "success"
    assert zentool._lock_probe_state(path) is False
    assert closed == [11, 11, 11]
    assert fake_fcntl.calls[-2:] == [
        (11, fake_fcntl.LOCK_EX | fake_fcntl.LOCK_NB),
        (11, fake_fcntl.LOCK_UN),
    ]


def test_zen_profile_lock_state_returns_false_without_lock_files(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Profiles with no lock files should report a definite unlocked state."""
    monkeypatch.setattr(zentool, "zen_lock_paths", lambda _profile: [])

    assert zentool.zen_profile_lock_state(None) is False


def test_zen_is_running_wraps_runtime_inspection(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """The thin running wrapper should forward the runtime state bool."""
    monkeypatch.setattr(
        zentool,
        "inspect_zen_runtime",
        lambda _profile: zentool.ZenRuntimeState(running=True, warnings=("fallback",)),
    )

    assert zentool.zen_is_running("default") is True


def test_print_runtime_warnings_emits_each_warning(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Runtime warnings should be rendered one line at a time to stderr."""
    lines: list[str] = []
    monkeypatch.setattr(zentool, "_stderr", lines.append)

    zentool._print_runtime_warnings(
        zentool.ZenRuntimeState(
            running=False, warnings=("first warning", "second warning")
        )
    )

    assert lines == ["Warning: first warning", "Warning: second warning"]


def test_backup_session_uses_suffix_when_first_backup_name_collides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Backup naming should add a numeric suffix after a timestamp collision."""
    session_path = tmp_path / zentool.SESSION_FILENAME
    session_path.write_text("session\n", encoding="utf-8")
    monkeypatch.setattr(zentool.time, "strftime", lambda _fmt: "20260422-120000")
    monkeypatch.setattr(zentool.time, "time_ns", lambda: 42)

    first_backup = (
        tmp_path / f"{zentool.SESSION_FILENAME}.20260422-120000-000000042.bak"
    )
    first_backup.write_text("existing\n", encoding="utf-8")

    backup = zentool.backup_session(session_path)

    assert (
        backup
        == tmp_path / f"{zentool.SESSION_FILENAME}.20260422-120000-000000042.1.bak"
    )
    assert backup.read_text(encoding="utf-8") == "session\n"


def test_subprocess_run_covers_capture_non_capture_and_errors(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Subprocess execution should preserve stdout and wrap failures."""
    calls: list[tuple[list[str], bool, bool]] = []

    def fake_run(
        command: list[str], *, check: bool, capture_output: bool, text: bool
    ) -> SimpleNamespace:
        calls.append((command, check, capture_output))
        assert text is True
        return SimpleNamespace(stdout="captured\n")

    monkeypatch.setattr(zentool.subprocess, "run", fake_run)

    assert zentool.subprocess_run(["/bin/echo", "ok"], capture=True) == "captured\n"
    assert zentool.subprocess_run(["/bin/echo", "ok"], capture=False) == ""
    assert calls == [
        (["/bin/echo", "ok"], True, True),
        (["/bin/echo", "ok"], True, False),
    ]

    def raise_subprocess_error(
        command: list[str], *, check: bool, capture_output: bool, text: bool
    ) -> SimpleNamespace:
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(zentool.subprocess, "run", raise_subprocess_error)

    with pytest.raises(
        zentool.ZenFoldersError, match=r"Failed to run command /bin/echo ok"
    ):
        zentool.subprocess_run(["/bin/echo", "ok"], capture=True)


def test_cmd_validate_loads_config_and_reports_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Validation should stay a thin wrapper around config loading."""
    config_path = tmp_path / "folders.yaml"
    seen: list[Path] = []
    lines: list[str] = []
    monkeypatch.setattr(
        zentool, "load_config", lambda path: seen.append(path) or object()
    )
    monkeypatch.setattr(zentool, "_stdout", lines.append)

    assert zentool.cmd_validate(SimpleNamespace(config=str(config_path))) == 0
    assert seen == [config_path]
    assert lines == ["Config is valid."]
