"""Tests for GitHub Desktop's packaged Info.plist validator."""

from __future__ import annotations

import plistlib
from pathlib import Path
from types import ModuleType

import pytest

from lib.tests._updater_helpers import load_repo_module


def _load_module() -> ModuleType:
    return load_repo_module(
        "overlays/github-desktop/validate_info_plist.py",
        "github_desktop_info_plist_test",
    )


def _write_plist(path: Path, payload: object) -> None:
    with path.open("wb") as handle:
        plistlib.dump(payload, handle)


def _valid_payload() -> dict[str, object]:
    return {
        "CFBundleIdentifier": "com.github.GitHubClient",
        "CFBundleURLTypes": [
            {"CFBundleURLSchemes": ["github-mac", "x-github-client"]},
            {"CFBundleURLSchemes": ["x-github-desktop-auth"]},
        ],
    }


def test_validate_info_plist_accepts_expected_bundle_metadata(tmp_path: Path) -> None:
    """The validator should accept the real app identifier and URL schemes."""
    module = _load_module()
    plist_path = tmp_path / "Info.plist"
    _write_plist(plist_path, _valid_payload())

    module.validate_info_plist(plist_path)
    module.main([str(plist_path)])


def test_validate_info_plist_rejects_wrong_bundle_identifier(tmp_path: Path) -> None:
    """A copied bundle with the wrong identifier should fail before install ends."""
    module = _load_module()
    plist_path = tmp_path / "Info.plist"
    payload = _valid_payload()
    payload["CFBundleIdentifier"] = "com.github.Electron"
    _write_plist(plist_path, payload)

    with pytest.raises(module.InfoPlistValidationError, match="CFBundleIdentifier"):
        module.validate_info_plist(plist_path)


def test_validate_info_plist_rejects_missing_url_scheme(tmp_path: Path) -> None:
    """The installed app needs every GitHub Desktop protocol handler."""
    module = _load_module()
    plist_path = tmp_path / "Info.plist"
    payload = _valid_payload()
    payload["CFBundleURLTypes"] = [{"CFBundleURLSchemes": ["github-mac"]}]
    _write_plist(plist_path, payload)

    with pytest.raises(module.InfoPlistValidationError, match="x-github-client"):
        module.validate_info_plist(plist_path)
    with pytest.raises(SystemExit, match="x-github-client"):
        module.main([str(plist_path)])


def test_validate_info_plist_rejects_non_dictionary_payload(tmp_path: Path) -> None:
    """The packaged plist must decode to a dictionary."""
    module = _load_module()
    plist_path = tmp_path / "Info.plist"
    _write_plist(plist_path, ["not", "a", "dict"])

    with pytest.raises(module.InfoPlistValidationError, match="dictionary"):
        module.validate_info_plist(plist_path)
