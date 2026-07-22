"""Focused pure-Python tests for zentool compilation and matching helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from lib.tests._zen_tooling import load_zentool_module

if TYPE_CHECKING:
    from types import ModuleType


@pytest.fixture(scope="module")
def zentool() -> ModuleType:
    """Load the zentool script for compilation and matching tests."""
    return load_zentool_module("zentool_matching_helpers")


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
    static_label: str | None = None,
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
        zenStaticLabel=static_label,
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
    )


def make_placeholder_tab(
    zentool: ModuleType,
    *,
    workspace_key: str | None = "work",
    workspace_uuid: str | None = "{ws}",
    folder_id: str | None = None,
    user_context_id: int = 0,
    sync_id: str = "",
) -> object:
    """Build one compiled empty-folder placeholder tab node."""
    return zentool.CompiledTab(
        spec=None,
        essential=False,
        pinned=True,
        workspace_key=workspace_key,
        workspace_uuid=workspace_uuid,
        folder_id=folder_id,
        user_context_id=user_context_id,
        sync_id=sync_id,
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

    zentool.prepare_folder_placeholders([workspace])

    assert empty_root.placeholder_tab is not None
    assert empty_root.placeholder_tab.spec is None
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
    broken_placeholder = make_placeholder_tab(
        zentool,
        workspace_uuid=None,
        folder_id=None,
        sync_id="broken",
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
    primary_folder.placeholder_tab = make_placeholder_tab(
        zentool,
        folder_id="folder-primary",
        sync_id="placeholder-primary",
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


def make_matching_workspace(
    zentool: ModuleType,
    *,
    items: list[object],
    tabs: list[object] | None = None,
) -> object:
    """Build one compiled workspace shell for matching-pass tests."""
    return zentool.WorkspaceCompilation(
        spec=zentool.WorkspaceSpec(name="Work"),
        key="work",
        space=zentool.SessionSpace(uuid="{ws}", name="Work"),
        items=items,
        tabs=list(tabs or []),
    )


def test_match_tabs_to_existing_reclaims_drifted_tabs_by_label_then_origin(
    zentool: ModuleType,
) -> None:
    """Navigated tabs should be reclaimed by declared label, then origin."""
    label_target = make_compiled_tab(
        zentool,
        name="GitHub",
        url="https://github.example/coder",
    )
    origin_target = make_compiled_tab(
        zentool,
        name="Rippling",
        url="https://rippling.example/dashboard",
    )
    fresh_target = make_compiled_tab(
        zentool, name="Fresh", url="https://fresh.example/"
    )
    workspace = make_matching_workspace(
        zentool,
        items=[label_target, origin_target, fresh_target],
    )
    drifted_label = make_session_tab(
        zentool,
        url="https://sso.example/park",
        sync_id="sync-github",
        pinned=True,
        user_context_id=21,
        static_label="GitHub",
    )
    drifted_origin = make_session_tab(
        zentool,
        url="https://rippling.example/settings/profile",
        sync_id="sync-rippling",
        pinned=True,
        user_context_id=22,
        static_label="Rippling Payroll",
    )
    session = zentool.SessionState(tabs=[drifted_label, drifted_origin])

    zentool.match_tabs_to_existing(
        session=session,
        essentials=[],
        workspaces=[workspace],
    )

    assert label_target.existing is session.tabs[0]
    assert label_target.sync_id == "sync-github"
    assert label_target.user_context_id == 21
    assert origin_target.existing is session.tabs[1]
    assert origin_target.sync_id == "sync-rippling"
    assert origin_target.user_context_id == 22
    assert fresh_target.existing is None
    assert fresh_target.sync_id.startswith("zf-")


def test_match_tabs_to_existing_completes_exact_urls_before_label_claims(
    zentool: ModuleType,
) -> None:
    """An exact-URL match must win over an earlier compiled tab's label claim."""
    alpha = make_compiled_tab(zentool, name="Alpha", url="https://alpha.example/")
    beta = make_compiled_tab(zentool, name="Beta", url="https://beta.example/")
    stolen_candidate = make_session_tab(
        zentool,
        url="https://beta.example/",
        sync_id="sync-beta",
        pinned=True,
        static_label="Alpha",
    )
    drifted_alpha = make_session_tab(
        zentool,
        url="https://sso.example/alpha-park",
        sync_id="sync-alpha",
        pinned=True,
        static_label="Alpha",
    )
    session = zentool.SessionState(tabs=[stolen_candidate, drifted_alpha])
    workspace = make_matching_workspace(zentool, items=[alpha, beta])

    zentool.match_tabs_to_existing(
        session=session,
        essentials=[],
        workspaces=[workspace],
    )

    assert beta.existing is session.tabs[0]
    assert beta.sync_id == "sync-beta"
    assert alpha.existing is session.tabs[1]
    assert alpha.sync_id == "sync-alpha"


def test_match_tabs_to_existing_prefers_same_origin_for_duplicate_labels(
    zentool: ModuleType,
) -> None:
    """Duplicate declared names should pair by origin, not authored order."""
    mail_first = make_compiled_tab(
        zentool,
        name="Mail",
        url="https://mail.example/u/0",
    )
    mail_second = make_compiled_tab(
        zentool,
        name="Mail",
        url="https://mail.other.example/inbox",
    )
    other_live = make_session_tab(
        zentool,
        url="https://mail.other.example/thread/7",
        sync_id="sync-other",
        pinned=True,
        static_label="Mail",
    )
    first_live = make_session_tab(
        zentool,
        url="https://mail.example/u/0/settings",
        sync_id="sync-first",
        pinned=True,
        static_label="Mail",
    )
    session = zentool.SessionState(tabs=[other_live, first_live])
    workspace = make_matching_workspace(zentool, items=[mail_first, mail_second])

    zentool.match_tabs_to_existing(
        session=session,
        essentials=[],
        workspaces=[workspace],
    )

    assert mail_first.existing is session.tabs[1]
    assert mail_first.sync_id == "sync-first"
    assert mail_second.existing is session.tabs[0]
    assert mail_second.sync_id == "sync-other"


def test_match_tabs_to_existing_gives_same_origin_label_claims_priority(
    zentool: ModuleType,
) -> None:
    """A cross-origin label fallback must not steal a later tab's same-origin match."""
    mail_personal = make_compiled_tab(
        zentool,
        name="Mail",
        url="https://mail.example/u/0",
    )
    mail_work = make_compiled_tab(
        zentool,
        name="Mail",
        url="https://mail.other.example/inbox",
    )
    work_live = make_session_tab(
        zentool,
        url="https://mail.other.example/thread/7",
        sync_id="sync-work",
        pinned=True,
        user_context_id=7,
        static_label="Mail",
    )
    session = zentool.SessionState(tabs=[work_live])
    workspace = make_matching_workspace(zentool, items=[mail_personal, mail_work])

    zentool.match_tabs_to_existing(
        session=session,
        essentials=[],
        workspaces=[workspace],
    )

    assert mail_work.existing is session.tabs[0]
    assert mail_work.sync_id == "sync-work"
    assert mail_work.user_context_id == 7
    assert mail_personal.existing is None
    assert mail_personal.sync_id.startswith("zf-")


def test_match_tabs_to_existing_label_claims_win_over_origin_claims(
    zentool: ModuleType,
) -> None:
    """A label match must outrank another compiled tab's origin-only claim."""
    renamed = make_compiled_tab(zentool, name="Foo", url="https://y.example/")
    same_origin = make_compiled_tab(zentool, name="Bar", url="https://x.example/")
    live = make_session_tab(
        zentool,
        url="https://x.example/deep",
        sync_id="sync-foo",
        pinned=True,
        static_label="Foo",
    )
    session = zentool.SessionState(tabs=[live])
    workspace = make_matching_workspace(zentool, items=[renamed, same_origin])

    zentool.match_tabs_to_existing(
        session=session,
        essentials=[],
        workspaces=[workspace],
    )

    assert renamed.existing is session.tabs[0]
    assert renamed.sync_id == "sync-foo"
    assert same_origin.existing is None
    assert same_origin.sync_id.startswith("zf-")


def test_match_tabs_to_existing_skips_placeholders_and_unknown_pools(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Placeholders keep their IDs and unknown essential pools allocate fresh IDs."""
    next_ids = iter(["zf-ghost"])
    monkeypatch.setattr(zentool, "new_item_id", lambda: next(next_ids))

    placeholder = make_placeholder_tab(
        zentool,
        folder_id="folder-empty",
        sync_id="placeholder-keep",
    )
    ghost_essential = make_compiled_tab(
        zentool,
        name="Ghost",
        url="https://ghost.example/",
        workspace_key="ghost",
        workspace_uuid="{ghost}",
        essential=True,
    )
    blank_live = make_session_tab(
        zentool,
        url="",
        sync_id="sync-blank",
        pinned=True,
    )
    session = zentool.SessionState(tabs=[blank_live])
    workspace = make_matching_workspace(zentool, items=[placeholder])

    zentool.match_tabs_to_existing(
        session=session,
        essentials=[ghost_essential],
        workspaces=[workspace],
    )

    assert placeholder.existing is None
    assert placeholder.sync_id == "placeholder-keep"
    assert ghost_essential.existing is None
    assert ghost_essential.sync_id == "zf-ghost"


def test_build_desired_state_gives_subfolder_only_folders_placeholders(
    zentool: ModuleType,
) -> None:
    """An authored folder whose only children are subfolders gets a placeholder."""
    result = zentool.build_desired_state(
        zentool.SessionState(),
        zentool.ZenConfig(
            workspaces=[
                zentool.WorkspaceSpec(
                    name="Work",
                    items=[
                        zentool.FolderSpec(
                            name="Outer",
                            items=[zentool.FolderSpec(name="Inner", items=[])],
                        )
                    ],
                )
            ]
        ),
    )

    folders_by_name = {folder.name: folder for folder in result.folders}
    assert set(folders_by_name) == {"Outer", "Inner"}
    assert folders_by_name["Inner"].parent_id == folders_by_name["Outer"].id

    placeholders_by_folder = {tab.group_id: tab for tab in result.tabs}
    assert len(result.tabs) == 2
    assert all(tab.zen_is_empty for tab in result.tabs)
    for folder in folders_by_name.values():
        placeholder = placeholders_by_folder[folder.id]
        assert folder.empty_tab_ids == [placeholder.zen_sync_id]
