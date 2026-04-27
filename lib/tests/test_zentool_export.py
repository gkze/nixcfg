"""Focused pure-Python tests for zentool export and dump seams."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from lib.tests._zen_tooling import load_zen_script_module

if TYPE_CHECKING:
    from types import ModuleType


@pytest.fixture(scope="module")
def zentool() -> ModuleType:
    """Load the zentool script for export/dump helper testing."""
    return load_zen_script_module("zentool", "zentool_export_helpers")


def make_entry(zentool: ModuleType, *, url: str, title: str = "") -> object:
    """Build one compact session history entry."""
    return zentool.SessionEntry(url=url, title=title)


def make_tab(
    zentool: ModuleType,
    *,
    name: str,
    url: str,
    workspace: str | None,
    sync_id: str,
    pinned: bool = False,
    essential: bool = False,
    empty: bool = False,
    folder_id: str | None = None,
) -> object:
    """Build one compact session tab record."""
    return zentool.SessionTab(
        entries=[] if empty else [make_entry(zentool, url=url, title=name)],
        index=1,
        pinned=pinned,
        zenEssential=essential,
        zenIsEmpty=empty,
        zenWorkspace=workspace,
        zenSyncId=sync_id,
        zenStaticLabel=name,
        groupId=folder_id,
    )


def make_folder(
    zentool: ModuleType,
    *,
    folder_id: str,
    name: str,
    workspace_id: str,
    parent_id: str | None = None,
    prev_type: str = "start",
    prev_id: str | None = None,
    collapsed: bool = True,
) -> object:
    """Build one compact session folder record."""
    return zentool.SessionFolder(
        id=folder_id,
        name=name,
        workspaceId=workspace_id,
        parentId=parent_id,
        collapsed=collapsed,
        prevSiblingInfo=zentool.PrevSiblingInfo(type=prev_type, id=prev_id),
    )


def make_space(
    zentool: ModuleType,
    *,
    uuid: str,
    name: str,
    icon: str | None = None,
    collapsed_pinned: bool = False,
    theme: object | None = None,
) -> object:
    """Build one compact session workspace record."""
    return zentool.SessionSpace(
        uuid=uuid,
        name=name,
        icon=icon,
        hasCollapsedPinnedTabs=collapsed_pinned,
        theme=theme or zentool.SessionTheme(),
    )


def test_top_level_selection_helpers_filter_by_role_workspace_and_placeholder(
    zentool: ModuleType,
) -> None:
    """Selection helpers should keep only the intended export-visible tabs."""
    session = zentool.SessionState(
        tabs=[
            make_tab(
                zentool,
                name="Essential",
                url="https://essential.example",
                workspace="ws-1",
                sync_id="essential-1",
                pinned=True,
                essential=True,
            ),
            make_tab(
                zentool,
                name="Pinned",
                url="https://pinned.example",
                workspace="ws-1",
                sync_id="pinned-1",
                pinned=True,
            ),
            make_tab(
                zentool,
                name="Workspace Tab",
                url="https://tab.example",
                workspace="ws-1",
                sync_id="tab-1",
            ),
            make_tab(
                zentool,
                name="Nested",
                url="https://nested.example",
                workspace="ws-1",
                sync_id="nested-1",
                pinned=True,
                folder_id="folder-1",
            ),
            make_tab(
                zentool,
                name="Placeholder",
                url="about:blank",
                workspace="ws-1",
                sync_id="empty-1",
                pinned=True,
                empty=True,
            ),
            make_tab(
                zentool,
                name="Other Workspace",
                url="https://other.example",
                workspace="ws-2",
                sync_id="other-1",
                pinned=True,
                essential=True,
            ),
        ]
    )

    assert [tab.zen_sync_id for tab in zentool.top_level_essentials(session)] == [
        "essential-1",
        "other-1",
    ]
    assert [
        tab.zen_sync_id for tab in zentool.top_level_pinned_tabs(session, "ws-1")
    ] == [
        "pinned-1",
    ]
    assert [
        tab.zen_sync_id for tab in zentool.direct_folder_tabs(session, "folder-1")
    ] == [
        "nested-1",
    ]
    assert [
        tab.zen_sync_id for tab in zentool.top_level_workspace_tabs(session, "ws-1")
    ] == [
        "tab-1",
    ]


def test_insert_folder_nodes_uses_prev_sibling_links_and_fallback_order(
    zentool: ModuleType,
) -> None:
    """Folder insertion should respect sibling metadata and append unresolved folders."""
    base_nodes = [
        ("tab-a", zentool.ItemTabSpec(name="A", url="https://a.example")),
        ("tab-b", zentool.ItemTabSpec(name="B", url="https://b.example")),
    ]
    folders = [
        make_folder(
            zentool,
            folder_id="folder-start",
            name="Start",
            workspace_id="ws-1",
            prev_type="start",
        ),
        make_folder(
            zentool,
            folder_id="folder-after-a",
            name="After A",
            workspace_id="ws-1",
            prev_type="tab",
            prev_id="tab-a",
        ),
        make_folder(
            zentool,
            folder_id="folder-unresolved",
            name="Fallback",
            workspace_id="ws-1",
            prev_type="group",
            prev_id="missing",
        ),
    ]

    inserted = zentool.insert_folder_nodes(
        base_nodes,
        folders,
        lambda folder: zentool.FolderSpec(name=folder.name, collapsed=folder.collapsed),
    )

    assert [item.name for item in inserted] == [
        "Start",
        "A",
        "After A",
        "B",
        "Fallback",
    ]


def test_build_folder_tree_recurses_and_preserves_mixed_item_order(
    zentool: ModuleType,
) -> None:
    """Tree reconstruction should preserve folder nesting and pinned-tab ordering."""
    session = zentool.SessionState(
        tabs=[
            make_tab(
                zentool,
                name="Pinned Root",
                url="https://root.example",
                workspace="ws-1",
                sync_id="root-tab",
                pinned=True,
            ),
            make_tab(
                zentool,
                name="Nested Tab",
                url="https://nested.example",
                workspace="ws-1",
                sync_id="nested-tab",
                pinned=True,
                folder_id="folder-parent",
            ),
        ],
        folders=[
            make_folder(
                zentool,
                folder_id="folder-parent",
                name="Parent",
                workspace_id="ws-1",
                prev_type="tab",
                prev_id="root-tab",
                collapsed=False,
            ),
            make_folder(
                zentool,
                folder_id="folder-child",
                name="Child",
                workspace_id="ws-1",
                parent_id="folder-parent",
                prev_type="start",
            ),
        ],
    )
    folders_by_parent = {
        None: [session.folders[0]],
        "folder-parent": [session.folders[1]],
    }

    items = zentool.build_folder_tree(
        session,
        workspace_uuid="ws-1",
        parent_id=None,
        folders_by_parent=folders_by_parent,
    )

    assert items == [
        zentool.ItemTabSpec(name="Pinned Root", url="https://root.example"),
        zentool.FolderSpec(
            name="Parent",
            collapsed=False,
            items=[
                zentool.FolderSpec(name="Child"),
                zentool.ItemTabSpec(name="Nested Tab", url="https://nested.example"),
            ],
        ),
    ]


def test_export_config_builds_workspace_specs_from_session_state(
    zentool: ModuleType,
) -> None:
    """Export should reconstruct workspace metadata, tree items, and workspace tabs."""
    session = zentool.SessionState(
        spaces=[
            make_space(
                zentool,
                uuid="ws-1",
                name="Work",
                icon="briefcase",
                collapsed_pinned=True,
                theme=zentool.SessionTheme(
                    gradientColors=["#111111", "#222222"],
                    opacity=0.75,
                ),
            ),
            make_space(zentool, uuid="ws-2", name="Play"),
        ],
        tabs=[
            make_tab(
                zentool,
                name="Inbox",
                url="https://mail.example",
                workspace="ws-1",
                sync_id="essential-inbox",
                pinned=True,
                essential=True,
            ),
            make_tab(
                zentool,
                name="Docs",
                url="https://docs.example",
                workspace="ws-1",
                sync_id="pinned-docs",
                pinned=True,
            ),
            make_tab(
                zentool,
                name="Chat",
                url="https://chat.example",
                workspace="ws-1",
                sync_id="nested-chat",
                pinned=True,
                folder_id="folder-ai",
            ),
            make_tab(
                zentool,
                name="Board",
                url="https://board.example",
                workspace="ws-1",
                sync_id="tab-board",
            ),
            make_tab(
                zentool,
                name="Games",
                url="https://games.example",
                workspace="ws-2",
                sync_id="play-tab",
            ),
        ],
        folders=[
            make_folder(
                zentool,
                folder_id="folder-ai",
                name="AI",
                workspace_id="ws-1",
                prev_type="tab",
                prev_id="pinned-docs",
                collapsed=False,
            )
        ],
    )

    config = zentool.export_config(session)

    assert config == zentool.ZenConfig(
        workspaces=[
            zentool.WorkspaceSpec(
                name="Work",
                icon="briefcase",
                hasCollapsedPinnedTabs=True,
                theme=zentool.ThemeSpec(
                    gradientColors=["#111111", "#222222"],
                    opacity=0.75,
                ),
                essentials=[zentool.TabSpec(name="Inbox", url="https://mail.example")],
                items=[
                    zentool.ItemTabSpec(name="Docs", url="https://docs.example"),
                    zentool.FolderSpec(
                        name="AI",
                        collapsed=False,
                        items=[
                            zentool.ItemTabSpec(
                                name="Chat",
                                url="https://chat.example",
                            )
                        ],
                    ),
                ],
                tabs=[zentool.TabSpec(name="Board", url="https://board.example")],
            ),
            zentool.WorkspaceSpec(
                name="Play",
                theme=zentool.ThemeSpec(),
                tabs=[zentool.TabSpec(name="Games", url="https://games.example")],
            ),
        ]
    )


def test_serialization_helpers_emit_compact_expected_shapes(
    zentool: ModuleType,
) -> None:
    """Helper serializers should omit defaults and preserve authored forms."""
    theme = zentool.ThemeSpec(
        gradientColors=["#abc", "#def"],
        opacity=0.75,
        texture=2,
    )
    item_tab = zentool.ItemTabSpec(name="Pinned", url="https://pinned.example")
    folder = zentool.FolderSpec(
        name="AI",
        collapsed=False,
        items=[item_tab],
    )
    config = zentool.ZenConfig(
        workspaces=[
            zentool.WorkspaceSpec(
                name="Work",
                icon="briefcase",
                hasCollapsedPinnedTabs=True,
                theme=theme,
                essentials=[zentool.TabSpec(name="Inbox", url="https://mail.example")],
                items=[folder],
                tabs=[zentool.TabSpec(name="Board", url="https://board.example")],
            )
        ]
    )

    assert zentool._theme_to_dict(zentool.ThemeSpec()) == {}
    assert zentool._theme_to_dict(theme) == {
        "gradientColors": ["#abc", "#def"],
        "opacity": 0.75,
        "texture": 2,
    }
    assert zentool._tab_spec_to_dict(
        config.workspaces[0].tabs[0], include_type=False
    ) == {
        "name": "Board",
        "url": "https://board.example",
    }
    assert zentool._item_to_dict(item_tab) == {
        "type": "tab",
        "name": "Pinned",
        "url": "https://pinned.example",
    }
    assert zentool._item_to_dict(folder) == {
        "type": "folder",
        "name": "AI",
        "collapsed": False,
        "items": [{"type": "tab", "name": "Pinned", "url": "https://pinned.example"}],
    }
    assert zentool._authored_leaf_to_dict(
        config.workspaces[0].essentials[0], role="essential"
    ) == {"Inbox": {"url": "https://mail.example", "role": "essential"}}
    assert zentool._authored_folder_to_dict(folder) == {
        "AI": {
            "collapsed": False,
            "children": [{"Pinned": "https://pinned.example"}],
        }
    }
    assert zentool._item_to_authored_dict(item_tab) == {
        "Pinned": "https://pinned.example"
    }
    assert zentool.config_to_dict(config) == {
        "Work": {
            "icon": "briefcase",
            "hasCollapsedPinnedTabs": True,
            "theme": {
                "gradientColors": ["#abc", "#def"],
                "opacity": 0.75,
                "texture": 2,
            },
            "tree": [
                {"Inbox": {"url": "https://mail.example", "role": "essential"}},
                {
                    "AI": {
                        "collapsed": False,
                        "children": [{"Pinned": "https://pinned.example"}],
                    }
                },
                {"Board": {"url": "https://board.example", "role": "tab"}},
            ],
        }
    }


def test_snapshot_and_yaml_formatters_render_exported_shape(
    zentool: ModuleType,
) -> None:
    """Snapshot and YAML helpers should expose the exported authored config shape."""
    session = zentool.SessionState(
        spaces=[make_space(zentool, uuid="ws-1", name="Work")],
        tabs=[
            make_tab(
                zentool,
                name="Inbox",
                url="https://mail.example",
                workspace="ws-1",
                sync_id="essential-inbox",
                pinned=True,
                essential=True,
            ),
            make_tab(
                zentool,
                name="Board",
                url="https://board.example",
                workspace="ws-1",
                sync_id="tab-board",
            ),
        ],
    )
    expected = {
        "Work": [
            {"Inbox": {"url": "https://mail.example", "role": "essential"}},
            {"Board": {"url": "https://board.example", "role": "tab"}},
        ]
    }

    config = zentool.export_config(session)

    assert zentool.snapshot(session) == expected
    assert zentool.format_config_yaml(config) == zentool.format_snapshot_yaml(session)
    assert zentool.format_snapshot_yaml(session) == (
        "Work:\n"
        "- Inbox:\n"
        "    url: https://mail.example\n"
        "    role: essential\n"
        "- Board:\n"
        "    url: https://board.example\n"
        "    role: tab\n"
    )


def test_cmd_dump_prints_or_writes_snapshot_yaml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Dump should stream YAML to stdout or write it to the requested file."""
    session = zentool.SessionState(
        spaces=[make_space(zentool, uuid="ws-1", name="Work")]
    )
    monkeypatch.setattr(
        zentool, "load_session", lambda _profile: (Path("/tmp/session"), session)
    )
    monkeypatch.setattr(
        zentool,
        "load_containers",
        lambda _profile: (Path("/tmp/containers.json"), zentool.ContainerState()),
    )

    stdout_chunks: list[str] = []
    stdout_lines: list[str] = []
    monkeypatch.setattr(zentool, "_stdout_raw", stdout_chunks.append)
    monkeypatch.setattr(zentool, "_stdout", stdout_lines.append)

    assert zentool.cmd_dump(SimpleNamespace(profile="default", output=None)) == 0
    assert stdout_chunks == ["Work: []\n"]
    assert stdout_lines == []

    written = tmp_path / "dump.yaml"
    stdout_chunks.clear()
    assert (
        zentool.cmd_dump(SimpleNamespace(profile="default", output=str(written))) == 0
    )
    assert written.read_text(encoding="utf-8") == "Work: []\n"
    assert stdout_chunks == []
    assert stdout_lines == [f"Written to {written}"]
