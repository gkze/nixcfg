"""Regression tests for the zen-folders script."""

# ruff: noqa: S101

from __future__ import annotations

import argparse
import getpass
import importlib.machinery
import importlib.util
from typing import TYPE_CHECKING

import pytest
import yaml

from lib.update.paths import REPO_ROOT

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

ZEN_FOLDERS_PATH = REPO_ROOT / f"home/{getpass.getuser()}/bin/zen-folders"


def _load_zen_folders_module() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader(
        "zen_folders_script",
        str(ZEN_FOLDERS_PATH),
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None or spec.loader is None:
        msg = "failed to load zen-folders module spec"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def zen_folders() -> ModuleType:
    """Load the zen-folders script as a module for direct function testing."""
    return _load_zen_folders_module()


def _base_session() -> dict:
    return {
        "tabs": [],
        "groups": [],
        "folders": [],
        "spaces": [{"name": "Work", "uuid": "ws1"}],
    }


def test_load_yaml_rejects_duplicate_keys(
    tmp_path: Path,
    zen_folders: ModuleType,
) -> None:
    """Duplicate keys in YAML should fail fast instead of silently overriding."""
    config_path = tmp_path / "zen-folders.yaml"
    config_path.write_text(
        "Work:\n"
        "  AI:\n"
        "    OpenAI: platform.openai.com\n"
        "  AI:\n"
        "    Anthropic: platform.claude.com\n",
        encoding="utf-8",
    )

    with pytest.raises(zen_folders.ZenFoldersError, match="duplicate key"):
        zen_folders.load_yaml(config_path)


def test_tab_entry_field_handles_non_numeric_index(zen_folders: ModuleType) -> None:
    """Non-integer tab index values should fall back to the latest entry."""
    tab = {
        "entries": [
            {"url": "https://example.com/first", "title": "First"},
            {"url": "https://example.com/second", "title": "Second"},
        ],
        "index": "2",
    }

    assert zen_folders.tab_url(tab) == "https://example.com/second"
    assert zen_folders.tab_title(tab) == "Second"


def test_compute_plan_rejects_duplicate_session_folder_names(
    zen_folders: ModuleType,
) -> None:
    """Ambiguous same-name folders in one workspace should be rejected."""
    session = _base_session()
    session["folders"] = [
        {"id": "f1", "name": "AI", "workspaceId": "ws1"},
        {"id": "f2", "name": "ai", "workspaceId": "ws1"},
    ]
    session["groups"] = [{"id": "f1"}, {"id": "f2"}]

    with pytest.raises(zen_folders.ZenFoldersError, match="duplicate folder names"):
        zen_folders.compute_plan(session, [], "ws1")


def test_apply_plan_handles_non_dict_rows_in_tabs_and_groups(
    zen_folders: ModuleType,
) -> None:
    """Malformed non-dict rows should not crash apply_plan reordering logic."""
    session = _base_session()
    session["groups"] = ["corrupt-group", {"id": "f1", "name": "AI"}]
    session["folders"] = [
        {
            "id": "f1",
            "name": "AI",
            "workspaceId": "ws1",
            "prevSiblingInfo": {"type": "start", "id": None},
            "emptyTabIds": [],
        },
    ]
    session["tabs"] = [
        "corrupt-tab",
        {
            "pinned": True,
            "zenWorkspace": "ws1",
            "zenSyncId": "t1",
            "groupId": "f1",
            "zenIsEmpty": False,
            "entries": [{"url": "https://platform.openai.com", "title": "OpenAI"}],
            "index": 1,
        },
        {
            "pinned": False,
            "entries": [{"url": "https://example.com", "title": "Example"}],
            "index": 1,
        },
    ]

    config_folders = [
        {"name": "AI", "tabs": [{"title": "OpenAI", "url": "openai.com"}]},
    ]
    plan = zen_folders.compute_plan(session, config_folders, "ws1")

    zen_folders.apply_plan(session, config_folders, "ws1", plan)

    assert any(not isinstance(group, dict) for group in session["groups"])
    assert any(not isinstance(tab, dict) for tab in session["tabs"])


def test_cmd_dump_disambiguates_duplicate_titles(
    tmp_path: Path,
    zen_folders: ModuleType,
) -> None:
    """Dump output should keep both tabs when titles collide."""
    session_path = tmp_path / "zen-sessions.jsonlz4"
    output_path = tmp_path / "dump.yaml"

    session = _base_session()
    session["groups"] = [{"id": "f1", "name": "AI"}]
    session["folders"] = [
        {
            "id": "f1",
            "name": "AI",
            "workspaceId": "ws1",
            "prevSiblingInfo": {"type": "start", "id": None},
            "emptyTabIds": [],
        },
    ]
    session["tabs"] = [
        {
            "pinned": True,
            "zenWorkspace": "ws1",
            "groupId": "f1",
            "zenIsEmpty": False,
            "entries": [{"url": "https://a.example.com", "title": "Dashboard"}],
            "index": 1,
        },
        {
            "pinned": True,
            "zenWorkspace": "ws1",
            "groupId": "f1",
            "zenIsEmpty": False,
            "entries": [{"url": "https://b.example.com", "title": "Dashboard"}],
            "index": 1,
        },
    ]

    zen_folders.write_session(session_path, session)
    args = argparse.Namespace(
        profile=str(session_path),
        workspace="Work",
        output=str(output_path),
    )

    assert zen_folders.cmd_dump(args) == 0

    dumped = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    tabs = dumped["Work"]["AI"]
    assert tabs["Dashboard"] == "https://a.example.com"
    assert tabs["Dashboard (2)"] == "https://b.example.com"


def test_read_session_rejects_oversized_payload_header(
    tmp_path: Path,
    zen_folders: ModuleType,
) -> None:
    """Reject oversized uncompressed-size headers before decompression."""
    oversized = zen_folders.MAX_SESSION_UNCOMPRESSED_BYTES + 1
    path = tmp_path / "bad-session.jsonlz4"
    path.write_bytes(b"mozLz40\0" + oversized.to_bytes(4, "little"))

    with pytest.raises(zen_folders.SessionFormatError, match="too large"):
        zen_folders.read_session(path)
