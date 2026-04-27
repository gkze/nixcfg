"""Focused pure-Python tests for zentool authored workspace handling."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lib.tests._zen_tooling import load_zen_script_module

if TYPE_CHECKING:
    from types import ModuleType


@pytest.fixture(scope="module")
def zentool() -> ModuleType:
    """Load the zentool script for authored-workspace tests."""
    return load_zen_script_module("zentool", "zentool_workspaces")


def test_authored_workspace_validate_name_trims_and_rejects_blank(
    zentool: ModuleType,
) -> None:
    """Workspace names should be trimmed and required."""
    workspace = zentool.AuthoredWorkspace(name="  Work  ")

    assert workspace.name == "Work"

    with pytest.raises(ValueError, match="workspace names must be non-empty strings"):
        zentool.AuthoredWorkspace.validate_name("   ")


def test_authored_workspace_validate_icon_handles_none_trim_and_blank(
    zentool: ModuleType,
) -> None:
    """Optional icons should preserve ``None`` and trim non-empty strings."""
    assert zentool.AuthoredWorkspace.validate_icon(None) is None
    assert zentool.AuthoredWorkspace.validate_icon("  browser  ") == "browser"

    with pytest.raises(ValueError, match="workspace icons must be non-empty strings"):
        zentool.AuthoredWorkspace.validate_icon("   ")


def test_authored_workspace_rejects_duplicate_top_level_folder_names_casefolded(
    zentool: ModuleType,
) -> None:
    """Top-level authored folders should be unique case-insensitively."""
    alpha = zentool.AuthoredFolderNode(name="Alpha", children=[])
    leaf = zentool.AuthoredLeafNode(name="Alpha", url="https://example.com", role=None)

    zentool.AuthoredWorkspace(name="Work", tree=[alpha, leaf])

    with pytest.raises(ValueError, match="duplicate top-level folder name 'ALPHA'"):
        zentool.AuthoredWorkspace(
            name="Work",
            tree=[alpha, zentool.AuthoredFolderNode(name="ALPHA", children=[])],
        )


def test_coerce_authored_workspace_accepts_list_shorthand(
    zentool: ModuleType,
) -> None:
    """List-form workspaces should become authored workspace models."""
    workspace = zentool._coerce_authored_workspace(
        "Work",
        [{"Inbox": "https://mail.example.com"}],
    )

    assert workspace == zentool.AuthoredWorkspace(
        name="Work",
        tree=[
            zentool.AuthoredLeafNode(
                name="Inbox",
                url="https://mail.example.com",
                role=None,
            )
        ],
    )


def test_coerce_authored_workspace_accepts_explicit_tree_mapping(
    zentool: ModuleType,
) -> None:
    """Explicit workspace mappings should preserve metadata and tree entries."""
    workspace = zentool._coerce_authored_workspace(
        "Work",
        {
            "icon": "  briefcase  ",
            "hasCollapsedPinnedTabs": True,
            "theme": {"gradientColors": ["#123456"], "opacity": 0.75, "texture": 2},
            "tree": [
                {"Docs": {"url": "https://docs.example.com", "role": "essential"}},
                {"Scratch": {"url": "https://scratch.example.com", "role": "tab"}},
            ],
        },
    )

    assert workspace == zentool.AuthoredWorkspace(
        name="Work",
        icon="briefcase",
        hasCollapsedPinnedTabs=True,
        theme=zentool.ThemeSpec(
            gradientColors=["#123456"],
            opacity=0.75,
            texture=2,
        ),
        tree=[
            zentool.AuthoredLeafNode(
                name="Docs",
                url="https://docs.example.com",
                role="essential",
            ),
            zentool.AuthoredLeafNode(
                name="Scratch",
                url="https://scratch.example.com",
                role="tab",
            ),
        ],
    )


def test_coerce_authored_workspace_accepts_implicit_tree_mapping(
    zentool: ModuleType,
) -> None:
    """Implicit child mappings should be treated as tree entries."""
    workspace = zentool._coerce_authored_workspace(
        "Work",
        {
            "icon": "globe",
            "Pinned": [{"Inbox": "https://mail.example.com"}],
            "Notes": {"url": "https://notes.example.com", "role": "tab"},
        },
    )

    assert workspace.tree == [
        zentool.AuthoredFolderNode(
            name="Pinned",
            children=[
                zentool.AuthoredLeafNode(
                    name="Inbox",
                    url="https://mail.example.com",
                    role=None,
                )
            ],
            collapsed=True,
        ),
        zentool.AuthoredLeafNode(
            name="Notes",
            url="https://notes.example.com",
            role="tab",
        ),
    ]


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        pytest.param(
            "bad", "workspace 'Work' must be a list or mapping", id="non-list-mapping"
        ),
        pytest.param(
            {"tree": {}}, "workspace 'Work' tree must be a list", id="tree-not-list"
        ),
        pytest.param(
            {"theme": []},
            "workspace 'Work' theme must be a mapping",
            id="theme-not-mapping",
        ),
        pytest.param(
            {"hasCollapsedPinnedTabs": 1},
            "workspace 'Work' hasCollapsedPinnedTabs must be a boolean",
            id="collapsed-pinned-not-bool",
        ),
    ],
)
def test_coerce_authored_workspace_rejects_invalid_shapes(
    raw: object,
    match: str,
    zentool: ModuleType,
) -> None:
    """Workspace coercion should fail fast on unsupported authored shapes."""
    with pytest.raises(zentool.ZenFoldersError, match=match):
        zentool._coerce_authored_workspace("Work", raw)


def test_authored_workspace_to_spec_partitions_tree_by_role(
    zentool: ModuleType,
) -> None:
    """Authored workspaces should split essentials, pinned items, and loose tabs."""
    spec = zentool._authored_workspace_to_spec(
        "Work",
        {
            "icon": "briefcase",
            "hasCollapsedPinnedTabs": True,
            "tree": [
                {"Docs": {"url": "https://docs.example.com", "role": "essential"}},
                {"Pinned": [{"Inbox": "https://mail.example.com"}]},
                {"Scratch": {"url": "https://scratch.example.com", "role": "tab"}},
                {"News": "https://news.example.com"},
            ],
        },
    )

    assert spec == zentool.WorkspaceSpec(
        name="Work",
        icon="briefcase",
        hasCollapsedPinnedTabs=True,
        essentials=[zentool.TabSpec(name="Docs", url="https://docs.example.com")],
        items=[
            zentool.FolderSpec(
                name="Pinned",
                items=[
                    zentool.ItemTabSpec(name="Inbox", url="https://mail.example.com")
                ],
            ),
            zentool.ItemTabSpec(name="News", url="https://news.example.com"),
        ],
        tabs=[zentool.TabSpec(name="Scratch", url="https://scratch.example.com")],
    )


def test_load_config_returns_empty_config_when_yaml_is_null(
    tmp_path: Path,
    zentool: ModuleType,
) -> None:
    """Empty YAML documents should normalize to an empty config."""
    config_path = tmp_path / "folders.yaml"
    config_path.write_text("null\n", encoding="utf-8")

    assert zentool.load_config(config_path) == zentool.ZenConfig()


def test_load_config_rejects_non_mapping_root(
    tmp_path: Path, zentool: ModuleType
) -> None:
    """The config root must remain a workspace-name mapping."""
    config_path = tmp_path / "folders.yaml"
    config_path.write_text("[]\n", encoding="utf-8")

    with pytest.raises(zentool.ZenFoldersError, match="Config root must be a mapping"):
        zentool.load_config(config_path)


def test_load_config_wraps_invalid_workspace_conversion(
    tmp_path: Path,
    zentool: ModuleType,
) -> None:
    """Workspace conversion failures should be reported with config-path context."""
    config_path = tmp_path / "folders.yaml"
    config_path.write_text("Work: 3\n", encoding="utf-8")

    with pytest.raises(
        zentool.ZenFoldersError,
        match=rf"Invalid config file {config_path}: workspace 'Work' must be a list or mapping",
    ):
        zentool.load_config(config_path)


def test_load_config_wraps_invalid_workspace_mapping_contents(
    tmp_path: Path,
    zentool: ModuleType,
) -> None:
    """Invalid authored workspace mappings should be wrapped by ``load_config``."""
    config_path = tmp_path / "folders.yaml"
    config_path.write_text("Work:\n  tree: {}\n", encoding="utf-8")

    with pytest.raises(
        zentool.ZenFoldersError,
        match=rf"Invalid config file {config_path}: workspace 'Work' tree must be a list",
    ):
        zentool.load_config(config_path)
