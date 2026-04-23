"""Focused pure-Python tests for zentool's compilation helpers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from lib.tests._zen_tooling import load_zen_script_module

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType


@pytest.fixture(scope="module")
def zentool() -> ModuleType:
    """Load the zentool script for compilation-helper testing."""
    return load_zen_script_module("zentool", "zentool_compilation_helpers")


def make_entry(zentool: ModuleType, *, url: str, title: str = "") -> object:
    """Build one compact session history entry."""
    return zentool.SessionEntry(url=url, title=title)


def make_session_tab(
    zentool: ModuleType,
    *,
    url: str,
    sync_id: str,
    workspace_uuid: str | None,
    pinned: bool = False,
    essential: bool = False,
    empty: bool = False,
    folder_id: str | None = None,
    user_context_id: int = 0,
) -> object:
    """Build one compact existing session tab."""
    return zentool.SessionTab(
        entries=[] if empty else [make_entry(zentool, url=url, title=url)],
        index=1,
        pinned=pinned,
        zenWorkspace=workspace_uuid,
        zenSyncId=sync_id,
        zenEssential=essential,
        zenIsEmpty=empty,
        groupId=folder_id,
        userContextId=user_context_id,
    )


def make_compiled_tab(
    zentool: ModuleType,
    *,
    name: str,
    url: str,
    workspace_key: str | None,
    workspace_uuid: str | None,
    pinned: bool,
    essential: bool = False,
    folder_id: str | None = None,
    user_context_id: int = 0,
    sync_id: str = "",
    placeholder: bool = False,
    existing: object | None = None,
) -> object:
    """Build one compiled tab node."""
    if placeholder:
        spec = SimpleNamespace(name=name, url=url)
    else:
        spec_cls = zentool.ItemTabSpec if pinned else zentool.TabSpec
        spec = spec_cls(name=name, url=url)
    return zentool.CompiledTab(
        spec=spec,
        essential=essential,
        pinned=pinned,
        workspace_key=workspace_key,
        workspace_uuid=workspace_uuid,
        folder_id=folder_id,
        user_context_id=user_context_id,
        sync_id=sync_id,
        placeholder=placeholder,
        existing=existing,
    )


def make_compiled_folder(
    zentool: ModuleType,
    *,
    folder_id: str,
    name: str,
    workspace_key: str,
    workspace_uuid: str,
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


def test_compile_folder_children_only_assigns_parent_to_direct_tabs(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Only direct child tabs should inherit the compiled parent folder ID."""
    direct_tab = make_compiled_tab(
        zentool,
        name="Direct",
        url="https://direct.example",
        workspace_key="work",
        workspace_uuid="{ws}",
        pinned=True,
    )
    nested_tab = make_compiled_tab(
        zentool,
        name="Nested",
        url="https://nested.example",
        workspace_key="work",
        workspace_uuid="{ws}",
        pinned=True,
    )
    nested_folder = make_compiled_folder(
        zentool,
        folder_id="folder-child",
        name="Child",
        workspace_key="work",
        workspace_uuid="{ws}",
        items=[nested_tab],
    )
    calls: list[dict[str, object]] = []

    def fake_compile_items(specs: object, **kwargs: object) -> list[object]:
        calls.append({"specs": specs, **kwargs})
        return [direct_tab, nested_folder]

    monkeypatch.setattr(zentool, "compile_items", fake_compile_items)

    compiled = zentool.compile_folder_children(
        [zentool.ItemTabSpec(name="ignored", url="https://ignored.example")],
        workspace_key="work",
        workspace_uuid="{ws}",
        inherited_container_id=7,
        folder_id="folder-root",
        existing_folders={},
        existing_groups={},
        parent_path=("Parent",),
    )

    assert compiled == [direct_tab, nested_folder]
    assert direct_tab.folder_id == "folder-root"
    assert nested_tab.folder_id is None
    assert calls == [
        {
            "specs": [
                zentool.ItemTabSpec(name="ignored", url="https://ignored.example")
            ],
            "workspace_key": "work",
            "workspace_uuid": "{ws}",
            "inherited_container_id": 7,
            "existing_folders": {},
            "existing_groups": {},
            "parent_path": ("Parent",),
        }
    ]


def test_build_workspace_compilation_reuses_existing_space_and_builds_tabs(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Workspace compilation should reuse the space UUID/container and normalize metadata."""
    compile_calls: list[dict[str, object]] = []
    compiled_item = make_compiled_tab(
        zentool,
        name="Pinned",
        url="https://pinned.example",
        workspace_key="work",
        workspace_uuid="{ws-existing}",
        pinned=True,
        user_context_id=42,
    )

    def fake_compile_items(specs: object, **kwargs: object) -> list[object]:
        compile_calls.append({"specs": specs, **kwargs})
        return [compiled_item]

    monkeypatch.setattr(zentool, "compile_items", fake_compile_items)

    spec = zentool.WorkspaceSpec(
        name="Work",
        icon="briefcase",
        hasCollapsedPinnedTabs=True,
        theme=zentool.ThemeSpec(
            gradientColors=["#111", "#222"], opacity=0.75, texture=2
        ),
        items=[zentool.ItemTabSpec(name="Pinned", url="https://pinned.example")],
        tabs=[zentool.TabSpec(name="Regular", url="https://regular.example")],
    )
    existing_space = zentool.SessionSpace(
        uuid="{ws-existing}",
        name="Old Name",
        containerTabId=42,
        icon="old",
    )

    compiled = zentool.build_workspace_compilation(
        spec,
        existing_space=existing_space,
        existing_folders={},
        existing_groups={},
    )

    assert compiled.key == "work"
    assert compiled.space is not existing_space
    assert compiled.space.uuid == "{ws-existing}"
    assert compiled.space.name == "Work"
    assert compiled.space.icon == "briefcase"
    assert compiled.space.hasCollapsedPinnedTabs is True
    assert compiled.space.containerTabId == 42
    assert compiled.space.theme.model_dump() == {
        "type": "gradient",
        "gradientColors": ["#111", "#222"],
        "opacity": 0.75,
        "texture": 2,
    }
    assert compiled.items == [compiled_item]
    assert len(compiled.tabs) == 1
    assert compiled.tabs[0].spec == zentool.TabSpec(
        name="Regular",
        url="https://regular.example",
    )
    assert compiled.tabs[0].pinned is False
    assert compiled.tabs[0].essential is False
    assert compiled.tabs[0].workspace_key == "work"
    assert compiled.tabs[0].workspace_uuid == "{ws-existing}"
    assert compiled.tabs[0].user_context_id == 42
    assert compile_calls == [
        {
            "specs": spec.items,
            "workspace_key": "work",
            "workspace_uuid": "{ws-existing}",
            "inherited_container_id": 42,
            "existing_folders": {},
            "existing_groups": {},
        }
    ]


def test_gather_compiled_tabs_preserves_workspace_local_then_global_order(
    zentool: ModuleType,
) -> None:
    """Gathering should emit workspace essentials, pinned items, tabs, then global essentials."""
    ws1_pinned = make_compiled_tab(
        zentool,
        name="Pinned 1",
        url="https://pinned-1.example",
        workspace_key="work",
        workspace_uuid="{ws1}",
        pinned=True,
        sync_id="pinned-1",
    )
    ws1_nested = make_compiled_tab(
        zentool,
        name="Nested",
        url="https://nested.example",
        workspace_key="work",
        workspace_uuid="{ws1}",
        pinned=True,
        sync_id="nested-1",
    )
    ws1_placeholder = make_compiled_tab(
        zentool,
        name="",
        url="",
        workspace_key="work",
        workspace_uuid="{ws1}",
        pinned=True,
        folder_id="folder-empty",
        sync_id="placeholder-1",
        placeholder=True,
    )
    ws1_folder = make_compiled_folder(
        zentool,
        folder_id="folder-1",
        name="Folder",
        workspace_key="work",
        workspace_uuid="{ws1}",
        items=[ws1_nested],
    )
    ws1_folder.placeholder_tab = ws1_placeholder
    ws1_regular = make_compiled_tab(
        zentool,
        name="Regular 1",
        url="https://regular-1.example",
        workspace_key="work",
        workspace_uuid="{ws1}",
        pinned=False,
        sync_id="regular-1",
    )
    ws2_regular = make_compiled_tab(
        zentool,
        name="Regular 2",
        url="https://regular-2.example",
        workspace_key="play",
        workspace_uuid="{ws2}",
        pinned=False,
        sync_id="regular-2",
    )
    ws1 = zentool.WorkspaceCompilation(
        spec=zentool.WorkspaceSpec(name="Work"),
        key="work",
        space=zentool.SessionSpace(uuid="{ws1}", name="Work"),
        items=[ws1_pinned, ws1_folder],
        tabs=[ws1_regular],
    )
    ws2 = zentool.WorkspaceCompilation(
        spec=zentool.WorkspaceSpec(name="Play"),
        key="play",
        space=zentool.SessionSpace(uuid="{ws2}", name="Play"),
        items=[],
        tabs=[ws2_regular],
    )
    essentials = [
        make_compiled_tab(
            zentool,
            name="Work Essential",
            url="https://essential-work.example",
            workspace_key="work",
            workspace_uuid="{ws1}",
            pinned=True,
            essential=True,
            sync_id="essential-work",
        ),
        make_compiled_tab(
            zentool,
            name="Global Essential",
            url="https://essential-global.example",
            workspace_key=None,
            workspace_uuid=None,
            pinned=True,
            essential=True,
            sync_id="essential-global",
        ),
    ]

    assert zentool.gather_workspace_pinned_tabs(ws1.items) == [
        ws1_pinned,
        ws1_placeholder,
        ws1_nested,
    ]
    assert [folder.id for folder in zentool.iter_compiled_folders(ws1.items)] == [
        "folder-1"
    ]
    assert zentool.gather_compiled_tabs(essentials, [ws1, ws2]) == [
        essentials[0],
        ws1_pinned,
        ws1_placeholder,
        ws1_nested,
        ws1_regular,
        ws2_regular,
        essentials[1],
    ]


def test_match_tabs_to_existing_claims_tabs_and_assigns_sync_ids(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Matching should reuse exact-URL tabs and allocate IDs for unmatched or placeholder tabs."""
    next_ids: Iterator[str] = iter([
        "zf-new-essential",
        "zf-new-placeholder",
        "zf-new-regular",
    ])
    monkeypatch.setattr(zentool, "new_item_id", lambda: next(next_ids))

    workspace = zentool.WorkspaceCompilation(
        spec=zentool.WorkspaceSpec(name="Work"),
        key="work",
        space=zentool.SessionSpace(uuid="{ws}", name="Work", containerTabId=5),
        items=[],
        tabs=[],
    )
    matched_pinned = make_compiled_tab(
        zentool,
        name="Pinned",
        url="https://pinned.example",
        workspace_key="work",
        workspace_uuid="{ws}",
        pinned=True,
    )
    unmatched_placeholder = make_compiled_tab(
        zentool,
        name="",
        url="",
        workspace_key="work",
        workspace_uuid="{ws}",
        pinned=True,
        folder_id="folder-empty",
        placeholder=True,
    )
    unmatched_regular = make_compiled_tab(
        zentool,
        name="Regular",
        url="https://regular-new.example",
        workspace_key="work",
        workspace_uuid="{ws}",
        pinned=False,
    )
    workspace.items = [matched_pinned, unmatched_placeholder]
    workspace.tabs = [unmatched_regular]
    matched_essential = make_compiled_tab(
        zentool,
        name="Essential",
        url="https://essential.example",
        workspace_key="work",
        workspace_uuid="{ws}",
        pinned=True,
        essential=True,
    )
    unmatched_essential = make_compiled_tab(
        zentool,
        name="Missing Essential",
        url="https://missing.example",
        workspace_key=None,
        workspace_uuid=None,
        pinned=True,
        essential=True,
    )
    session = zentool.SessionState(
        tabs=[
            make_session_tab(
                zentool,
                url="https://essential.example",
                sync_id="sync-essential",
                workspace_uuid="{ws}",
                pinned=True,
                essential=True,
                user_context_id=11,
            ),
            make_session_tab(
                zentool,
                url="https://pinned.example",
                sync_id="sync-pinned",
                workspace_uuid="{ws}",
                pinned=True,
                user_context_id=12,
            ),
            make_session_tab(
                zentool,
                url="https://regular-old.example",
                sync_id="sync-regular-old",
                workspace_uuid="{ws}",
                user_context_id=13,
            ),
        ]
    )

    zentool.match_tabs_to_existing(
        session=session,
        essentials=[matched_essential, unmatched_essential],
        workspaces=[workspace],
    )

    assert matched_essential.existing is session.tabs[0]
    assert matched_essential.sync_id == "sync-essential"
    assert matched_essential.user_context_id == 11
    assert unmatched_essential.existing is None
    assert unmatched_essential.sync_id == "zf-new-essential"
    assert matched_pinned.existing is session.tabs[1]
    assert matched_pinned.sync_id == "sync-pinned"
    assert matched_pinned.user_context_id == 12
    assert unmatched_placeholder.existing is None
    assert unmatched_placeholder.sync_id == "zf-new-placeholder"
    assert unmatched_regular.existing is None
    assert unmatched_regular.sync_id == "zf-new-regular"


def test_prepare_folder_placeholders_uses_direct_tab_count_and_reuses_sync_ids(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Only folders without direct tabs should get placeholders, including nested empty folders."""
    next_ids: Iterator[str] = iter(["zf-generated-nested", "zf-generated-child"])
    monkeypatch.setattr(zentool, "new_item_id", lambda: next(next_ids))
    monkeypatch.setattr(
        zentool,
        "TabSpec",
        lambda *, name, url: SimpleNamespace(name=name, url=url),
    )

    nested_empty = make_compiled_folder(
        zentool,
        folder_id="folder-nested",
        name="Nested Empty",
        workspace_key="work",
        workspace_uuid="{ws}",
    )
    empty_root = make_compiled_folder(
        zentool,
        folder_id="folder-empty",
        name="Empty Root",
        workspace_key="work",
        workspace_uuid="{ws}",
        items=[nested_empty],
        placeholder_sync_id="sync-empty-root",
    )
    direct_root = make_compiled_folder(
        zentool,
        folder_id="folder-direct",
        name="Direct Root",
        workspace_key="work",
        workspace_uuid="{ws}",
        items=[
            make_compiled_tab(
                zentool,
                name="Direct",
                url="https://direct.example",
                workspace_key="work",
                workspace_uuid="{ws}",
                pinned=True,
            ),
            make_compiled_folder(
                zentool,
                folder_id="folder-child",
                name="Child",
                workspace_key="work",
                workspace_uuid="{ws}",
            ),
        ],
    )
    workspace = zentool.WorkspaceCompilation(
        spec=zentool.WorkspaceSpec(name="Work"),
        key="work",
        space=zentool.SessionSpace(uuid="{ws}", name="Work", containerTabId=55),
        items=[empty_root, direct_root],
        tabs=[],
    )

    assert zentool.direct_tab_count(empty_root.items) == 0
    assert zentool.direct_tab_count(direct_root.items) == 1

    zentool.prepare_folder_placeholders([workspace])

    assert [folder.id for folder in zentool.iter_compiled_folders(workspace.items)] == [
        "folder-empty",
        "folder-nested",
        "folder-direct",
        "folder-child",
    ]
    assert empty_root.placeholder_tab is not None
    assert empty_root.placeholder_tab.sync_id == "sync-empty-root"
    assert empty_root.placeholder_tab.folder_id == "folder-empty"
    assert empty_root.placeholder_tab.user_context_id == 55
    assert nested_empty.placeholder_tab is not None
    assert nested_empty.placeholder_tab.sync_id == "zf-generated-nested"
    assert direct_root.placeholder_tab is None
    assert direct_root.items[1].placeholder_tab is not None
    assert direct_root.items[1].placeholder_tab.sync_id == "zf-generated-child"


def test_assign_folder_links_and_build_folder_group_update_existing_records(
    zentool: ModuleType,
) -> None:
    """Folder link assignment and builders should preserve IDs while normalizing metadata."""
    leading_tab = make_compiled_tab(
        zentool,
        name="Pinned",
        url="https://pinned.example",
        workspace_key="work",
        workspace_uuid="{ws}",
        pinned=True,
        sync_id="sync-leading",
    )
    nested_folder = make_compiled_folder(
        zentool,
        folder_id="folder-nested",
        name="Nested",
        workspace_key="work",
        workspace_uuid="{ws}",
    )
    existing_folder = zentool.SessionFolder(
        id="folder-existing",
        name="Old Name",
        workspaceId="{ws}",
        userIcon="star",
    )
    existing_group = zentool.SessionGroup(id="folder-existing", name="Old Group")
    primary_folder = make_compiled_folder(
        zentool,
        folder_id="folder-existing",
        name="Primary",
        workspace_key="work",
        workspace_uuid="{ws}",
        items=[nested_folder],
        existing=existing_folder,
        group_existing=existing_group,
        collapsed=False,
    )
    primary_folder.placeholder_tab = make_compiled_tab(
        zentool,
        name="",
        url="",
        workspace_key="work",
        workspace_uuid="{ws}",
        pinned=True,
        folder_id="folder-existing",
        sync_id="sync-placeholder",
        placeholder=True,
    )
    trailing_folder = make_compiled_folder(
        zentool,
        folder_id="folder-trailing",
        name="Trailing",
        workspace_key="work",
        workspace_uuid="{ws}",
    )

    zentool.assign_folder_links(
        [leading_tab, primary_folder, trailing_folder], parent_id=None
    )

    assert primary_folder.existing is existing_folder
    assert primary_folder.existing.parentId is None
    assert primary_folder.existing.prevSiblingInfo == zentool.PrevSiblingInfo(
        type="tab",
        id="sync-leading",
    )
    assert nested_folder.existing is not None
    assert nested_folder.existing.parentId == "folder-existing"
    assert nested_folder.existing.prevSiblingInfo == zentool.PrevSiblingInfo(
        type="start",
        id=None,
    )
    assert trailing_folder.existing is not None
    assert trailing_folder.existing.prevSiblingInfo == zentool.PrevSiblingInfo(
        type="group",
        id="folder-existing",
    )

    built_group = zentool.build_group(primary_folder)
    built_folder = zentool.build_folder(primary_folder)

    assert built_group is not existing_group
    assert built_group.id == "folder-existing"
    assert built_group.name == "Primary"
    assert built_group.collapsed is False
    assert built_group.color == zentool.GROUP_COLOR
    assert built_group.pinned is True
    assert built_folder is not existing_folder
    assert built_folder.id == "folder-existing"
    assert built_folder.name == "Primary"
    assert built_folder.workspaceId == "{ws}"
    assert built_folder.collapsed is False
    assert built_folder.userIcon == "star"
    assert built_folder.emptyTabIds == ["sync-placeholder"]
    assert built_folder.prevSiblingInfo == zentool.PrevSiblingInfo(
        type="tab",
        id="sync-leading",
    )


def test_build_desired_tabs_builds_regular_and_placeholder_tabs(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Desired-tab building should route normal and placeholder nodes to the right builders."""
    regular = make_compiled_tab(
        zentool,
        name="Regular",
        url="https://regular.example",
        workspace_key="work",
        workspace_uuid="{ws}",
        pinned=False,
        sync_id="sync-regular",
    )
    placeholder = make_compiled_tab(
        zentool,
        name="",
        url="",
        workspace_key="work",
        workspace_uuid="{ws}",
        pinned=True,
        folder_id="folder-1",
        user_context_id=23,
        sync_id="sync-placeholder",
        placeholder=True,
    )
    workspace = zentool.WorkspaceCompilation(
        spec=zentool.WorkspaceSpec(name="Work"),
        key="work",
        space=zentool.SessionSpace(uuid="{ws}", name="Work"),
        items=[placeholder],
        tabs=[regular],
    )
    built_regular = zentool.SessionTab(zenSyncId="built-regular")
    built_placeholder = zentool.SessionTab(zenSyncId="built-placeholder")
    build_tab_calls: list[
        tuple[object, object, str, bool, bool, str | None, str | None, int]
    ] = []
    placeholder_calls: list[tuple[str, str, str, int]] = []

    monkeypatch.setattr(
        zentool,
        "build_tab",
        lambda spec, *, existing, sync_id, pinned, essential, workspace_uuid, folder_id, user_context_id: (
            build_tab_calls.append((
                spec,
                existing,
                sync_id,
                pinned,
                essential,
                workspace_uuid,
                folder_id,
                user_context_id,
            ))
            or built_regular
        ),
    )
    monkeypatch.setattr(
        zentool,
        "build_placeholder_tab",
        lambda *, sync_id, workspace_uuid, folder_id, user_context_id: (
            placeholder_calls.append((
                sync_id,
                workspace_uuid,
                folder_id,
                user_context_id,
            ))
            or built_placeholder
        ),
    )

    tabs = zentool.build_desired_tabs([], [workspace])

    assert tabs == [built_placeholder, built_regular]
    assert placeholder_calls == [("sync-placeholder", "{ws}", "folder-1", 23)]
    assert build_tab_calls == [
        (
            regular.spec,
            None,
            "sync-regular",
            False,
            False,
            "{ws}",
            None,
            0,
        )
    ]


def test_build_desired_tabs_rejects_placeholder_without_workspace_folder(
    zentool: ModuleType,
) -> None:
    """Placeholder tabs must stay attached to a concrete workspace folder."""
    invalid = make_compiled_tab(
        zentool,
        name="",
        url="",
        workspace_key=None,
        workspace_uuid=None,
        pinned=True,
        placeholder=True,
        sync_id="bad-placeholder",
    )
    workspace = zentool.WorkspaceCompilation(
        spec=zentool.WorkspaceSpec(name="Work"),
        key="work",
        space=zentool.SessionSpace(uuid="{ws}", name="Work"),
        items=[invalid],
        tabs=[],
    )

    with pytest.raises(
        zentool.ZenFoldersError,
        match="placeholder tabs must belong to a workspace folder",
    ):
        zentool.build_desired_tabs([], [workspace])


def test_build_desired_state_sets_split_view_data_when_no_extra_exists(
    zentool: ModuleType,
) -> None:
    """Empty sessions should still gain the managed split-view payload."""
    result = zentool.build_desired_state(zentool.SessionState(), zentool.ZenConfig())

    assert result.tabs == []
    assert result.groups == []
    assert result.folders == []
    assert result.spaces == []
    assert result.model_extra == {"splitViewData": []}


def test_build_desired_state_overwrites_split_view_data_and_preserves_other_extra(
    zentool: ModuleType,
) -> None:
    """Managed split-view data should be replaced without dropping unrelated extras."""
    existing = zentool.SessionState.model_validate({
        "tabs": [],
        "groups": [],
        "folders": [],
        "spaces": [],
        "keep": {"marker": True},
        "splitViewData": ["stale"],
    })

    result = zentool.build_desired_state(existing, zentool.ZenConfig())

    assert result.model_extra == {
        "keep": {"marker": True},
        "splitViewData": [],
    }
