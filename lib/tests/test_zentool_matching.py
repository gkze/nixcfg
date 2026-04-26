"""Focused pure-Python tests for zentool compilation and matching helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from lib.tests._zen_tooling import load_zen_script_module

if TYPE_CHECKING:
    from types import ModuleType


@pytest.fixture(scope="module")
def zentool() -> ModuleType:
    """Load the zentool script for compilation and matching tests."""
    return load_zen_script_module("zentool", "zentool_matching_helpers")


def make_entry(zentool: ModuleType, *, url: str, title: str = "") -> object:
    """Build one compact session-history entry."""
    return zentool.SessionEntry(url=url, title=title or url)


def make_session_tab(
    zentool: ModuleType,
    *,
    url: str,
    sync_id: str,
    pinned: bool = False,
    essential: bool = False,
    workspace_uuid: str | None = "{ws}",
    folder_id: str | None = None,
    user_context_id: int = 0,
    empty: bool = False,
) -> object:
    """Build one compact session tab for matching tests."""
    return zentool.SessionTab(
        entries=[] if empty else [make_entry(zentool, url=url)],
        pinned=pinned,
        zenEssential=essential,
        zenWorkspace=workspace_uuid,
        zenSyncId=sync_id,
        userContextId=user_context_id,
        groupId=folder_id,
        zenIsEmpty=empty,
    )


def make_compiled_tab(
    zentool: ModuleType,
    *,
    name: str,
    url: str,
    workspace_key: str = "work",
    workspace_uuid: str | None = "{ws}",
    folder_id: str | None = None,
    pinned: bool = True,
    essential: bool = False,
    user_context_id: int = 0,
    sync_id: str = "",
    placeholder: bool = False,
) -> object:
    """Build one compiled tab node."""
    spec_cls = zentool.ItemTabSpec if pinned and not essential else zentool.TabSpec
    return zentool.CompiledTab(
        spec=spec_cls(name=name, url=url),
        essential=essential,
        pinned=pinned,
        workspace_key=workspace_key,
        workspace_uuid=workspace_uuid,
        folder_id=folder_id,
        user_context_id=user_context_id,
        sync_id=sync_id,
        placeholder=placeholder,
    )


def make_compiled_folder(
    zentool: ModuleType,
    *,
    folder_id: str,
    name: str,
    workspace_key: str = "work",
    workspace_uuid: str = "{ws}",
    items: list[object] | None = None,
    existing: object | None = None,
    group_existing: object | None = None,
    placeholder_sync_id: str | None = None,
    collapsed: bool = True,
) -> object:
    """Build one compiled folder node."""
    return zentool.CompiledFolder(
        spec=zentool.FolderSpec(name=name, collapsed=collapsed, items=[]),
        workspace_key=workspace_key,
        workspace_uuid=workspace_uuid,
        id=folder_id,
        existing=existing,
        group_existing=group_existing,
        placeholder_sync_id=placeholder_sync_id,
        items=list(items or []),
    )


def test_compile_items_and_build_workspace_compilation_reuse_existing_state(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Compilation should reuse matching folders and preserve workspace container context."""
    monkeypatch.setattr(zentool, "new_item_id", lambda: "generated-folder")

    existing_root = zentool.SessionFolder(
        id="folder-root",
        name="Projects",
        workspaceId="{space}",
        emptyTabIds=["placeholder-root"],
    )
    existing_nested = zentool.SessionFolder(
        id="folder-child",
        name="Backend",
        workspaceId="{space}",
        parentId="folder-root",
    )
    existing_space = zentool.SessionSpace(
        uuid="{space}",
        name="Old Name",
        containerTabId=7,
    )
    existing_groups = {
        "folder-root": zentool.SessionGroup(id="folder-root", name="Old Group")
    }
    existing_folders = {
        ("work", ("projects",)): existing_root,
        ("work", ("projects", "backend")): existing_nested,
    }
    spec = zentool.WorkspaceSpec(
        name="Work",
        icon="briefcase",
        hasCollapsedPinnedTabs=True,
        theme=zentool.ThemeSpec(
            gradientColors=["#111111", "#222222"],
            opacity=0.75,
            texture=2,
        ),
        items=[
            zentool.ItemTabSpec(name="Pinned Top", url="https://top.example"),
            zentool.FolderSpec(
                name="Projects",
                collapsed=False,
                items=[
                    zentool.ItemTabSpec(
                        name="Pinned Child", url="https://child.example"
                    ),
                    zentool.FolderSpec(
                        name="Backend",
                        items=[
                            zentool.ItemTabSpec(
                                name="Pinned Nested",
                                url="https://nested.example",
                            )
                        ],
                    ),
                ],
            ),
        ],
        tabs=[zentool.TabSpec(name="Regular", url="https://regular.example")],
    )

    compiled = zentool.build_workspace_compilation(
        spec,
        existing_space=existing_space,
        existing_folders=existing_folders,
        existing_groups=existing_groups,
    )

    assert compiled.key == "work"
    assert compiled.space is not existing_space
    assert compiled.space.uuid == "{space}"
    assert compiled.space.name == "Work"
    assert compiled.space.icon == "briefcase"
    assert compiled.space.has_collapsed_pinned_tabs is True
    assert compiled.space.theme == zentool.SessionTheme(
        gradientColors=["#111111", "#222222"],
        opacity=0.75,
        texture=2,
    )

    top_tab = compiled.items[0]
    root_folder = compiled.items[1]
    assert isinstance(top_tab, zentool.CompiledTab)
    assert top_tab.folder_id is None
    assert top_tab.user_context_id == 7

    assert isinstance(root_folder, zentool.CompiledFolder)
    assert root_folder.id == "folder-root"
    assert root_folder.existing == existing_root
    assert root_folder.group_existing == existing_groups["folder-root"]
    assert root_folder.placeholder_sync_id == "placeholder-root"
    assert root_folder.spec.collapsed is False

    child_tab = root_folder.items[0]
    nested_folder = root_folder.items[1]
    assert isinstance(child_tab, zentool.CompiledTab)
    assert child_tab.folder_id == "folder-root"
    assert child_tab.user_context_id == 7

    assert isinstance(nested_folder, zentool.CompiledFolder)
    assert nested_folder.id == "folder-child"
    assert nested_folder.existing == existing_nested
    assert isinstance(nested_folder.items[0], zentool.CompiledTab)
    assert nested_folder.items[0].folder_id == "folder-child"

    assert compiled.tabs == [
        zentool.CompiledTab(
            spec=zentool.TabSpec(name="Regular", url="https://regular.example"),
            essential=False,
            pinned=False,
            workspace_key="work",
            workspace_uuid="{space}",
            folder_id=None,
            user_context_id=7,
        )
    ]


def test_prepare_match_and_build_desired_tabs_cover_placeholder_and_ordering(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Matching should reuse existing tabs and keep placeholder tabs deterministic."""
    next_ids = iter(["placeholder-empty", "placeholder-nested", "fresh-unmatched"])
    monkeypatch.setattr(zentool, "new_item_id", lambda: next(next_ids))

    top_pinned = make_compiled_tab(
        zentool,
        name="Pinned Top",
        url="https://pinned.example",
        user_context_id=55,
    )
    direct_tab = make_compiled_tab(
        zentool,
        name="Pinned Child",
        url="https://child.example",
        folder_id="folder-direct",
        user_context_id=55,
    )
    unmatched_tab = make_compiled_tab(
        zentool,
        name="Pinned Fresh",
        url="https://fresh.example",
        folder_id="folder-direct",
        user_context_id=55,
    )
    nested_empty = make_compiled_folder(
        zentool,
        folder_id="folder-nested",
        name="Nested Empty",
    )
    empty_root = make_compiled_folder(
        zentool,
        folder_id="folder-empty",
        name="Empty Root",
    )
    direct_root = make_compiled_folder(
        zentool,
        folder_id="folder-direct",
        name="Direct Root",
        items=[direct_tab, unmatched_tab, nested_empty],
    )
    regular_tab = make_compiled_tab(
        zentool,
        name="Regular",
        url="https://regular.example",
        pinned=False,
        user_context_id=55,
    )
    workspace = zentool.WorkspaceCompilation(
        spec=zentool.WorkspaceSpec(name="Work"),
        key="work",
        space=zentool.SessionSpace(uuid="{ws}", name="Work", containerTabId=55),
        items=[top_pinned, empty_root, direct_root],
        tabs=[regular_tab],
    )
    essentials = [
        make_compiled_tab(
            zentool,
            name="Essential",
            url="https://essential.example",
            pinned=True,
            essential=True,
            user_context_id=55,
        ),
        make_compiled_tab(
            zentool,
            name="Global Essential",
            url="https://global.example",
            workspace_key=None,
            workspace_uuid=None,
            pinned=True,
            essential=True,
        ),
    ]

    assert zentool.direct_tab_count(empty_root.items) == 0
    assert zentool.direct_tab_count(direct_root.items) == 2

    original_tab_spec = zentool.TabSpec
    monkeypatch.setattr(
        zentool,
        "TabSpec",
        lambda *, name, url: original_tab_spec.model_construct(name=name, url=url),
    )
    zentool.prepare_folder_placeholders([workspace])

    assert empty_root.placeholder_tab is not None
    assert empty_root.placeholder_tab.sync_id == "placeholder-empty"
    assert empty_root.placeholder_tab.folder_id == "folder-empty"
    assert direct_root.placeholder_tab is None
    assert nested_empty.placeholder_tab is not None
    assert nested_empty.placeholder_tab.sync_id == "placeholder-nested"

    session = zentool.SessionState(
        tabs=[
            make_session_tab(
                zentool,
                url="https://essential.example",
                sync_id="sync-essential",
                pinned=True,
                essential=True,
                user_context_id=1,
            ),
            make_session_tab(
                zentool,
                url="https://global.example",
                sync_id="sync-global",
                pinned=True,
                essential=True,
                workspace_uuid=None,
                user_context_id=2,
            ),
            make_session_tab(
                zentool,
                url="https://pinned.example",
                sync_id="sync-pinned",
                pinned=True,
                user_context_id=3,
            ),
            make_session_tab(
                zentool,
                url="https://child.example",
                sync_id="sync-child",
                pinned=True,
                folder_id="folder-direct",
                user_context_id=4,
            ),
            make_session_tab(
                zentool,
                url="https://regular.example",
                sync_id="sync-regular",
                pinned=False,
                user_context_id=5,
            ),
            make_session_tab(
                zentool,
                url="https://ignored-empty.example",
                sync_id="ignored-empty",
                pinned=True,
                empty=True,
            ),
        ]
    )

    zentool.match_tabs_to_existing(
        session=session,
        essentials=essentials,
        workspaces=[workspace],
    )

    assert essentials[0].sync_id == "sync-essential"
    assert essentials[0].user_context_id == 1
    assert essentials[1].sync_id == "sync-global"
    assert essentials[1].user_context_id == 2

    assert top_pinned.sync_id == "sync-pinned"
    assert top_pinned.user_context_id == 3
    assert direct_tab.sync_id == "sync-child"
    assert direct_tab.user_context_id == 4
    assert unmatched_tab.sync_id == "fresh-unmatched"
    assert empty_root.placeholder_tab.sync_id == "placeholder-empty"
    assert nested_empty.placeholder_tab.sync_id == "placeholder-nested"
    assert regular_tab.sync_id == "sync-regular"
    assert regular_tab.user_context_id == 5

    pinned_tabs = zentool.gather_workspace_pinned_tabs(workspace.items)
    assert pinned_tabs == [
        top_pinned,
        empty_root.placeholder_tab,
        direct_tab,
        unmatched_tab,
        nested_empty.placeholder_tab,
    ]
    assert zentool.gather_compiled_tabs(essentials, [workspace]) == [
        essentials[0],
        top_pinned,
        empty_root.placeholder_tab,
        direct_tab,
        unmatched_tab,
        nested_empty.placeholder_tab,
        regular_tab,
        essentials[1],
    ]

    desired = zentool.build_desired_tabs(essentials, [workspace])

    assert [tab.zen_sync_id for tab in desired] == [
        "sync-essential",
        "sync-pinned",
        "placeholder-empty",
        "sync-child",
        "fresh-unmatched",
        "placeholder-nested",
        "sync-regular",
        "sync-global",
    ]
    assert [tab.group_id for tab in desired] == [
        None,
        None,
        "folder-empty",
        "folder-direct",
        "folder-direct",
        "folder-nested",
        None,
        None,
    ]
    assert [tab.zen_is_empty for tab in desired] == [
        False,
        False,
        True,
        False,
        False,
        True,
        False,
        False,
    ]


def test_build_desired_tabs_rejects_placeholder_without_workspace_folder(
    zentool: ModuleType,
) -> None:
    """Placeholder tabs must belong to a concrete workspace folder."""
    broken_placeholder = make_compiled_tab(
        zentool,
        name="Placeholder",
        url="https://placeholder.example",
        workspace_uuid=None,
        folder_id=None,
        sync_id="broken",
        placeholder=True,
    )
    workspace = zentool.WorkspaceCompilation(
        spec=zentool.WorkspaceSpec(name="Work"),
        key="work",
        space=zentool.SessionSpace(uuid="{ws}", name="Work"),
        items=[broken_placeholder],
        tabs=[],
    )

    with pytest.raises(zentool.ZenFoldersError, match="placeholder tabs must belong"):
        zentool.build_desired_tabs([], [workspace])


def test_assign_folder_links_and_build_records_cover_existing_and_new_nodes(
    zentool: ModuleType,
) -> None:
    """Folder linking should set sibling metadata before record construction."""
    leading_tab = make_compiled_tab(
        zentool,
        name="Leading",
        url="https://leading.example",
        sync_id="tab-1",
    )
    existing_folder = zentool.SessionFolder(
        id="folder-primary",
        name="Primary",
        workspaceId="{ws}",
        userIcon="custom-icon",
    )
    existing_group = zentool.SessionGroup(id="folder-primary", name="Old Group")
    nested_folder = make_compiled_folder(
        zentool,
        folder_id="folder-nested",
        name="Nested",
    )
    primary_folder = make_compiled_folder(
        zentool,
        folder_id="folder-primary",
        name="Primary",
        existing=existing_folder,
        group_existing=existing_group,
        items=[nested_folder],
        collapsed=False,
    )
    primary_folder.placeholder_tab = make_compiled_tab(
        zentool,
        name="Placeholder",
        url="https://placeholder.example",
        folder_id="folder-primary",
        sync_id="placeholder-primary",
        placeholder=True,
    )
    trailing_folder = make_compiled_folder(
        zentool,
        folder_id="folder-trailing",
        name="Trailing",
    )

    zentool.assign_folder_links(
        [leading_tab, primary_folder, trailing_folder],
        parent_id=None,
    )

    assert primary_folder.existing.parent_id is None
    assert primary_folder.existing.prev_sibling_info == zentool.PrevSiblingInfo(
        type="tab",
        id="tab-1",
    )
    assert nested_folder.existing.parent_id == "folder-primary"
    assert nested_folder.existing.prev_sibling_info == zentool.PrevSiblingInfo(
        type="start",
        id=None,
    )
    assert trailing_folder.existing.parent_id is None
    assert trailing_folder.existing.prev_sibling_info == zentool.PrevSiblingInfo(
        type="group",
        id="folder-primary",
    )

    built_group = zentool.build_group(primary_folder)
    built_folder = zentool.build_folder(primary_folder)

    assert built_group.id == "folder-primary"
    assert built_group.name == "Primary"
    assert built_group.collapsed is False
    assert built_group.color == zentool.GROUP_COLOR
    assert built_group.pinned is True

    assert built_folder.id == "folder-primary"
    assert built_folder.workspace_id == "{ws}"
    assert built_folder.collapsed is False
    assert built_folder.user_icon == "custom-icon"
    assert built_folder.empty_tab_ids == ["placeholder-primary"]
    assert built_folder.prev_sibling_info == zentool.PrevSiblingInfo(
        type="tab",
        id="tab-1",
    )


def test_build_desired_state_sets_split_view_data_for_plain_and_extra_states(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Desired-state assembly should normalize split view extras in both cases."""
    monkeypatch.setattr(zentool, "new_workspace_uuid", lambda: "{generated-space}")
    monkeypatch.setattr(zentool, "new_item_id", lambda: "generated-item")

    plain_result = zentool.build_desired_state(
        zentool.SessionState(),
        zentool.ZenConfig(workspaces=[]),
    )
    original_tab_spec = zentool.TabSpec
    monkeypatch.setattr(
        zentool,
        "TabSpec",
        lambda *, name, url: original_tab_spec.model_construct(name=name, url=url),
    )
    extra_result = zentool.build_desired_state(
        zentool.SessionState.model_validate({
            "tabs": [],
            "groups": [],
            "folders": [],
            "spaces": [],
            "note": "keep",
        }),
        zentool.ZenConfig(
            workspaces=[
                zentool.WorkspaceSpec(
                    name="Work",
                    items=[zentool.FolderSpec(name="Empty Folder", items=[])],
                    tabs=[
                        zentool.TabSpec(name="Regular", url="https://regular.example")
                    ],
                    essentials=[
                        zentool.TabSpec(
                            name="Essential",
                            url="https://essential.example",
                        )
                    ],
                )
            ]
        ),
    )

    assert plain_result.model_extra == {"splitViewData": []}
    assert extra_result.model_extra == {"note": "keep", "splitViewData": []}
    assert [space.uuid for space in extra_result.spaces] == ["{generated-space}"]
    assert [folder.name for folder in extra_result.folders] == ["Empty Folder"]
    assert [group.name for group in extra_result.groups] == ["Empty Folder"]
    assert [tab.zen_sync_id for tab in extra_result.tabs] == [
        "generated-item",
        "generated-item",
        "generated-item",
    ]
    assert [tab.zen_is_empty for tab in extra_result.tabs] == [False, True, False]
