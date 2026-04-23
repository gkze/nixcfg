"""Focused pure-Python tests for zentool models and tiny helpers."""

from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING

import pytest

from lib.tests._zen_tooling import load_zen_script_module

if TYPE_CHECKING:
    from types import ModuleType


@pytest.fixture(scope="module")
def zentool() -> ModuleType:
    """Load the zentool script for model and helper testing."""
    return load_zen_script_module("zentool", "zentool_models")


def make_session_tab(
    zentool: ModuleType,
    url: str,
    *,
    sync_id: str,
) -> object:
    """Build one minimal session tab with an active URL."""
    return zentool.SessionTab(
        entries=[zentool.SessionEntry(url=url, title="Tab")],
        zenSyncId=sync_id,
    )


def test_theme_spec_rejects_invalid_gradient_colors_opacity_and_texture(
    zentool: ModuleType,
) -> None:
    """ThemeSpec validators should reject blank colors and unsupported numbers."""
    with pytest.raises(
        ValueError, match="theme.gradientColors entries must be non-empty strings"
    ):
        zentool.ThemeSpec(gradientColors=["#123456", "   "])

    with pytest.raises(ValueError, match="theme.opacity must be between 0 and 1"):
        zentool.ThemeSpec(opacity=-0.1)

    with pytest.raises(ValueError, match="theme.opacity must be between 0 and 1"):
        zentool.ThemeSpec(opacity=1.1)

    with pytest.raises(ValueError, match="theme.texture must be >= 0"):
        zentool.ThemeSpec(texture=-1)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("name", "   ", id="blank-name"),
        pytest.param("url", "   ", id="blank-url"),
    ],
)
def test_tab_spec_rejects_blank_required_strings(
    zentool: ModuleType,
    field: str,
    value: str,
) -> None:
    """TabSpec should trim and reject empty required fields."""
    kwargs = {"name": "Inbox", "url": "https://mail.example.com"}
    kwargs[field] = value

    with pytest.raises(ValueError, match="tab fields must be non-empty strings"):
        zentool.TabSpec(**kwargs)


def test_folder_spec_rejects_blank_name_and_duplicate_child_folders(
    zentool: ModuleType,
) -> None:
    """FolderSpec should validate its own name and child-folder uniqueness."""
    with pytest.raises(ValueError, match="folder names must be non-empty strings"):
        zentool.FolderSpec(name="   ")

    with pytest.raises(ValueError, match="duplicate child folder name 'ALPHA'"):
        zentool.FolderSpec(
            name="Root",
            items=[
                zentool.FolderSpec(name="Alpha"),
                zentool.FolderSpec(name="ALPHA"),
            ],
        )


def test_workspace_spec_rejects_blank_name_blank_icon_and_duplicate_folders(
    zentool: ModuleType,
) -> None:
    """WorkspaceSpec should validate required metadata and top-level folders."""
    with pytest.raises(ValueError, match="workspace names must be non-empty strings"):
        zentool.WorkspaceSpec(name="   ")

    with pytest.raises(ValueError, match="workspace icons must be non-empty strings"):
        zentool.WorkspaceSpec(name="Work", icon="   ")

    with pytest.raises(ValueError, match="duplicate top-level folder name 'ALPHA'"):
        zentool.WorkspaceSpec(
            name="Work",
            items=[
                zentool.FolderSpec(name="Alpha"),
                zentool.FolderSpec(name="ALPHA"),
            ],
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("name", "   ", id="blank-name"),
        pytest.param("url", "   ", id="blank-url"),
    ],
)
def test_authored_leaf_node_rejects_blank_required_strings(
    zentool: ModuleType,
    field: str,
    value: str,
) -> None:
    """Authored leaf nodes should reject blank names and URLs."""
    kwargs = {"name": "Inbox", "url": "https://mail.example.com", "role": None}
    kwargs[field] = value

    with pytest.raises(ValueError, match="node fields must be non-empty strings"):
        zentool.AuthoredLeafNode(**kwargs)


def test_authored_folder_node_rejects_blank_name_and_duplicate_child_folders(
    zentool: ModuleType,
) -> None:
    """Authored folder nodes should validate names and same-name child folders."""
    with pytest.raises(ValueError, match="folder names must be non-empty strings"):
        zentool.AuthoredFolderNode(name="   ")

    with pytest.raises(ValueError, match="duplicate child folder name 'ALPHA'"):
        zentool.AuthoredFolderNode(
            name="Root",
            children=[
                zentool.AuthoredFolderNode(name="Alpha"),
                zentool.AuthoredFolderNode(name="ALPHA"),
            ],
        )


def test_zen_config_rejects_duplicate_workspace_names_casefolded(
    zentool: ModuleType,
) -> None:
    """ZenConfig should reject ambiguous workspace-name collisions."""
    with pytest.raises(ValueError, match="duplicate workspace name 'WORK'"):
        zentool.ZenConfig(
            workspaces=[
                zentool.WorkspaceSpec(name="Work"),
                zentool.WorkspaceSpec(name="WORK"),
            ]
        )


def test_session_state_normalizes_nullable_folder_links(
    zentool: ModuleType,
) -> None:
    """Live null sibling info should be normalized during model construction."""
    state = zentool.SessionState(
        folders=[
            zentool.SessionFolder(
                id="folder-a",
                name="Alpha",
                workspaceId="ws-1",
                prevSiblingInfo=None,
            ),
            zentool.SessionFolder(
                id="folder-b",
                name="Beta",
                workspaceId="ws-1",
                prevSiblingInfo=zentool.PrevSiblingInfo(type="group", id="group-1"),
            ),
        ]
    )

    assert state.folders[0].prevSiblingInfo == zentool.PrevSiblingInfo(
        type="start", id=None
    )
    assert state.folders[1].prevSiblingInfo == zentool.PrevSiblingInfo(
        type="group", id="group-1"
    )


def test_tab_pool_indexes_tabs_by_active_url_and_claims_in_order(
    zentool: ModuleType,
) -> None:
    """TabPool should preserve insertion order per URL and exhaust matches."""
    first = make_session_tab(zentool, "https://example.com", sync_id="tab-1")
    second = make_session_tab(zentool, "https://example.com", sync_id="tab-2")
    empty = zentool.SessionTab(entries=[], zenSyncId="tab-3")
    pool = zentool.TabPool([first, second, empty])

    assert pool.claim("https://example.com") is first
    assert pool.claim("https://example.com") is second
    assert pool.claim("https://example.com") is None
    assert pool.claim("") is empty
    assert pool.claim("https://missing.example") is None


def test_stdout_stderr_and_stdout_raw_write_expected_stream_output(
    capsys: pytest.CaptureFixture[str],
    zentool: ModuleType,
) -> None:
    """Tiny output helpers should preserve newline and raw-write behavior."""
    zentool._stdout("hello")
    zentool._stderr("problem")
    zentool._stdout_raw("tail")

    captured = capsys.readouterr()

    assert captured.out == "hello\ntail"
    assert captured.err == "problem\n"


def test_new_workspace_uuid_and_item_id_match_expected_formats(
    zentool: ModuleType,
) -> None:
    """Generated IDs should follow Zen's brace-wrapped and prefixed shapes."""
    workspace_uuid = zentool.new_workspace_uuid()
    item_id = zentool.new_item_id()

    assert workspace_uuid.startswith("{")
    assert workspace_uuid.endswith("}")
    assert str(uuid.UUID(workspace_uuid[1:-1])) == workspace_uuid[1:-1]

    assert re.fullmatch(r"zf-[0-9a-f]{32}", item_id)
