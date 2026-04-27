"""Focused pure-Python tests for zentool I/O and asset helpers."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import lz4.block
import pytest

from lib.tests._zen_tooling import load_zen_script_module

if TYPE_CHECKING:
    from types import ModuleType


@pytest.fixture(scope="module")
def zentool() -> ModuleType:
    """Load zentool for isolated I/O and asset helper testing."""
    return load_zen_script_module("zentool", "zentool_io_assets")


def _minimal_session(zentool: ModuleType) -> object:
    return zentool.SessionState(
        tabs=[],
        groups=[],
        folders=[],
        spaces=[zentool.SessionSpace(uuid="ws-1", name="Work")],
    )


def _make_args(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "profile": None,
        "asset_dir": "/tmp/assets",
        "chrome_source": None,
        "user_js_source": None,
        "state": False,
        "assets": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _encode_session_bytes(zentool: ModuleType, payload: object) -> bytes:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    header = zentool.SESSION_HEADER_PREFIX + len(raw).to_bytes(4, "little")
    return header + lz4.block.compress(raw, store_size=False)


def test_selected_scope_defaults_and_explicit_flags(zentool: ModuleType) -> None:
    """Scope resolution should default to both and respect explicit flags."""
    assert zentool._selected_scope(_make_args()) == (True, True)
    assert zentool._selected_scope(_make_args(state=True)) == (True, False)
    assert zentool._selected_scope(_make_args(assets=True)) == (False, True)
    assert zentool._selected_scope(_make_args(state=True, assets=True)) == (True, True)


def test_symlink_target_matching_and_managed_source_reading(
    tmp_path: Path,
    zentool: ModuleType,
) -> None:
    """Symlink/marshal helpers should distinguish matches, mismatches, and blank manifests."""
    source = tmp_path / "source.js"
    source.write_text("demo\n", encoding="utf-8")
    other = tmp_path / "other.js"
    other.write_text("other\n", encoding="utf-8")
    destination = tmp_path / "user.js"
    destination.write_text("plain file\n", encoding="utf-8")

    assert zentool._symlink_target_matches(destination, source) is False
    destination.unlink()
    destination.symlink_to(source)
    assert zentool._symlink_target_matches(destination, source) is True
    assert zentool._symlink_target_matches(destination, other) is False

    manifest = tmp_path / "manifest.txt"
    assert zentool._read_managed_source(manifest) is None
    manifest.write_text("\n", encoding="utf-8")
    assert zentool._read_managed_source(manifest) is None
    manifest.write_text(f"{source}\n", encoding="utf-8")
    assert zentool._read_managed_source(manifest) == source


def test_link_managed_file_handles_remove_replace_and_invalid_destination(
    tmp_path: Path,
    zentool: ModuleType,
) -> None:
    """Managed file linking should remove prior managed links and reject directories."""
    source = tmp_path / "source.js"
    source.write_text("demo\n", encoding="utf-8")
    previous = tmp_path / "previous.js"
    previous.write_text("old\n", encoding="utf-8")
    destination = tmp_path / "user.js"
    manifest = tmp_path / "user-manifest.txt"

    destination.symlink_to(previous)
    manifest.write_text(f"{previous}\n", encoding="utf-8")
    zentool.link_managed_file(None, destination, manifest_path=manifest)
    assert not destination.exists()
    assert not manifest.exists()

    destination.write_text("plain\n", encoding="utf-8")
    zentool.link_managed_file(source, destination, manifest_path=manifest)
    assert destination.is_symlink()
    assert destination.resolve() == source.resolve()
    assert manifest.read_text(encoding="utf-8") == f"{source}\n"

    destination.unlink()
    destination.mkdir()
    with pytest.raises(zentool.ZenFoldersError, match="Destination path is not a file"):
        zentool.link_managed_file(source, destination, manifest_path=manifest)


def test_cleanup_and_prune_managed_chrome_tree(
    tmp_path: Path, zentool: ModuleType
) -> None:
    """Chrome cleanup should remove recorded symlinks and empty directories only."""
    chrome_dir = tmp_path / "chrome"
    stale_target = tmp_path / "stale.css"
    stale_target.write_text("stale\n", encoding="utf-8")
    stale_link = chrome_dir / "nested" / "stale.css"
    stale_link.parent.mkdir(parents=True)
    stale_link.symlink_to(stale_target)
    keeper = chrome_dir / "keep.txt"
    keeper.parent.mkdir(parents=True, exist_ok=True)
    keeper.write_text("keep\n", encoding="utf-8")
    manifest = tmp_path / "manifest.txt"
    manifest.write_text("nested/stale.css\n\nmissing.css\n", encoding="utf-8")

    zentool.cleanup_managed_chrome_symlinks(chrome_dir, manifest)
    assert not stale_link.exists()
    assert keeper.exists()

    zentool.prune_empty_chrome_dirs(chrome_dir)
    assert not (chrome_dir / "nested").exists()
    assert chrome_dir.exists()
    zentool.prune_empty_chrome_dirs(tmp_path / "missing-chrome")


def test_iter_source_files_and_sync_chrome_tree(
    tmp_path: Path,
    zentool: ModuleType,
) -> None:
    """Chrome sync should sort source files, create symlinks, and write the manifest."""
    source_dir = tmp_path / "source"
    (source_dir / "b").mkdir(parents=True)
    (source_dir / "a").mkdir(parents=True)
    file_b = source_dir / "b" / "theme.css"
    file_a = source_dir / "a" / "userChrome.css"
    file_b.write_text("b\n", encoding="utf-8")
    file_a.write_text("a\n", encoding="utf-8")

    assert zentool._iter_source_files(source_dir) == [file_a, file_b]

    profile_chrome_dir = tmp_path / "profile" / "chrome"
    manifest = tmp_path / "chrome-manifest.txt"
    zentool.sync_chrome_tree(source_dir, profile_chrome_dir, manifest)

    assert (profile_chrome_dir / "a" / "userChrome.css").is_symlink()
    assert (profile_chrome_dir / "b" / "theme.css").is_symlink()
    assert manifest.read_text(encoding="utf-8") == "a/userChrome.css\nb/theme.css\n"

    (source_dir / "conflict").mkdir(exist_ok=True)
    (source_dir / "conflict" / "x.css").write_text("x\n", encoding="utf-8")
    (profile_chrome_dir / "conflict" / "x.css").mkdir(parents=True)
    with pytest.raises(
        zentool.ZenFoldersError, match="Chrome destination path is not a file"
    ):
        zentool.sync_chrome_tree(source_dir, profile_chrome_dir, manifest)


def test_load_asset_sources_prefers_explicit_and_env_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Asset source resolution should honor explicit arguments and env overrides."""
    asset_dir = tmp_path / "assets"
    default_chrome = asset_dir / "chrome"
    default_chrome.mkdir(parents=True)
    default_user_js = asset_dir / "user.js"
    default_user_js.write_text("user\n", encoding="utf-8")
    env_chrome = tmp_path / "env-chrome"
    env_chrome.mkdir()
    explicit_user_js = tmp_path / "explicit-user.js"
    explicit_user_js.write_text("explicit\n", encoding="utf-8")

    monkeypatch.setenv(zentool.DEFAULT_CHROME_SOURCE_ENV, str(env_chrome))
    config_dir, chrome_source, user_js_source = zentool._load_asset_sources(
        _make_args(asset_dir=str(asset_dir), user_js_source=str(explicit_user_js))
    )

    assert config_dir == asset_dir.resolve()
    assert chrome_source == env_chrome.resolve()
    assert user_js_source == explicit_user_js.resolve()


def test_read_write_load_and_backup_session_cover_error_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Session I/O helpers should round-trip and report malformed data clearly."""
    session = _minimal_session(zentool)
    session_path = tmp_path / zentool.SESSION_FILENAME
    zentool.write_session(session_path, session)
    assert zentool.read_session(session_path) == session

    backup = zentool.backup_session(session_path)
    assert backup.exists()
    assert backup.read_bytes() == session_path.read_bytes()

    def _bad_read_bytes() -> bytes:
        msg = "boom"
        raise OSError(msg)

    bad_path = tmp_path / "bad.jsonlz4"
    original_read_bytes = Path.read_bytes

    def _patched_read_bytes(self: Path) -> bytes:
        if self == bad_path:
            return _bad_read_bytes()
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _patched_read_bytes)
    with pytest.raises(zentool.ZenFoldersError, match="Unable to read session file"):
        zentool.read_session(bad_path)

    tiny = tmp_path / "tiny.jsonlz4"
    tiny.write_bytes(b"tiny")
    with pytest.raises(zentool.SessionFormatError, match="too small"):
        zentool.read_session(tiny)

    wrong_prefix = tmp_path / "wrong.jsonlz4"
    wrong_prefix.write_bytes(b"notmozLz40\x00\x00")
    with pytest.raises(zentool.SessionFormatError, match="Not a Mozilla LZ4 file"):
        zentool.read_session(wrong_prefix)

    invalid_size = tmp_path / "invalid-size.jsonlz4"
    invalid_size.write_bytes(zentool.SESSION_HEADER_PREFIX + (0).to_bytes(4, "little"))
    with pytest.raises(zentool.SessionFormatError, match="Invalid uncompressed size"):
        zentool.read_session(invalid_size)

    bad_payload = tmp_path / "bad-payload.jsonlz4"
    bad_payload.write_bytes(_encode_session_bytes(zentool, ["not", "an", "object"]))
    with pytest.raises(
        zentool.SessionFormatError, match="Session payload root must be an object"
    ):
        zentool.read_session(bad_payload)

    monkeypatch.setattr(zentool, "session_file", lambda _profile: session_path)
    loaded_path, loaded_session = zentool.load_session("profile")
    assert loaded_path == session_path
    assert loaded_session == session

    def _bad_copy2(_src: Path, _dst: Path) -> None:
        msg = "copy failed"
        raise OSError(msg)

    monkeypatch.setattr(zentool.shutil, "copy2", _bad_copy2)
    with pytest.raises(zentool.ZenFoldersError, match="Failed to create backup"):
        zentool.backup_session(session_path)


def test_write_session_reports_write_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Writing the session should translate filesystem errors into ZentoolError."""
    session = _minimal_session(zentool)
    target = tmp_path / zentool.SESSION_FILENAME

    def _bad_write_bytes(_data: bytes) -> int:
        msg = "write failed"
        raise OSError(msg)

    original_write_bytes = Path.write_bytes

    def _patched_write_bytes(self: Path, data: bytes) -> int:
        if self == target:
            return _bad_write_bytes(data)
        return original_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", _patched_write_bytes)
    with pytest.raises(zentool.ZenFoldersError, match="Unable to write session file"):
        zentool.write_session(target, session)
