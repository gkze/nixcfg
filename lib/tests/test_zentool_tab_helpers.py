"""Focused pure-Python tests for zentool tab and folder helper seams."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from lib.tests._zen_tooling import load_zen_script_module

if TYPE_CHECKING:
    from types import ModuleType


@pytest.fixture(scope="module")
def zentool() -> ModuleType:
    """Load the zentool script for direct helper testing."""
    return load_zen_script_module("zentool", "zentool_tab_helpers")


def make_tab(
    zentool: ModuleType,
    *,
    entries: list[object] | None = None,
    index: int = 1,
    sync_id: str = "sync-1",
    static_label: str | None = None,
    last_accessed: int = 123,
    pinned: bool = False,
    essential: bool = False,
    workspace: str | None = "{ws}",
    folder_id: str | None = None,
    user_context_id: int = 0,
    attributes: dict[str, object] | None = None,
) -> object:
    """Build a compact session tab for helper tests."""
    return zentool.SessionTab(
        entries=list(entries or []),
        index=index,
        lastAccessed=last_accessed,
        hidden=True,
        pinned=pinned,
        zenWorkspace=workspace,
        zenSyncId=sync_id,
        zenEssential=essential,
        zenStaticLabel=static_label,
        userContextId=user_context_id,
        groupId=folder_id,
        attributes=dict(attributes or {}),
    )


def make_entry(zentool: ModuleType, *, url: str, title: str = "") -> object:
    """Build one session history entry."""
    return zentool.SessionEntry(url=url, title=title)


def make_folder(
    zentool: ModuleType,
    *,
    folder_id: str,
    name: str,
    workspace_id: str,
    parent_id: str | None = None,
) -> object:
    """Build one session folder record."""
    return zentool.SessionFolder(
        id=folder_id,
        name=name,
        workspaceId=workspace_id,
        parentId=parent_id,
    )


def make_space(zentool: ModuleType, *, uuid: str, name: str) -> object:
    """Build one session space record."""
    return zentool.SessionSpace(uuid=uuid, name=name)


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        (0, 0),
        (1, 0),
        (2, 1),
        (99, 1),
    ],
)
def test_active_index_clamps_to_available_entries(
    zentool: ModuleType,
    *,
    index: int,
    expected: int,
) -> None:
    """Active index should stay inside the history-entry bounds."""
    tab = make_tab(
        zentool,
        entries=[
            make_entry(zentool, url="https://one.example", title="One"),
            make_entry(zentool, url="https://two.example", title="Two"),
        ],
        index=index,
    )

    assert zentool.active_index(tab) == expected


def test_active_entry_helpers_fall_back_for_empty_tabs(zentool: ModuleType) -> None:
    """Empty tabs should expose no active entry, URL, or title."""
    tab = make_tab(zentool, entries=[], index=5)

    assert zentool.active_index(tab) == 0
    assert zentool.active_entry(tab) is None
    assert zentool.active_url(tab) == ""
    assert zentool.active_title(tab) == ""


def test_display_name_prefers_static_label_then_title_then_url(
    zentool: ModuleType,
) -> None:
    """Display names should follow zentool's declared-label precedence."""
    static = make_tab(
        zentool,
        entries=[
            make_entry(zentool, url="https://url.example", title="  Live Title  ")
        ],
        static_label="  Pinned Label  ",
    )
    titled = make_tab(
        zentool,
        entries=[
            make_entry(zentool, url="https://url.example/two", title="  Live Title  ")
        ],
        static_label="   ",
    )
    url_only = make_tab(
        zentool,
        entries=[make_entry(zentool, url="https://url.example/three", title="   ")],
        static_label=None,
    )

    assert zentool.display_name(static) == "Pinned Label"
    assert zentool.display_name(titled) == "Live Title"
    assert zentool.display_name(url_only) == "https://url.example/three"


def test_tab_to_spec_uses_display_name_and_active_url(zentool: ModuleType) -> None:
    """Tab specs should reflect the active entry and display-name policy."""
    tab = make_tab(
        zentool,
        entries=[make_entry(zentool, url="https://example.com", title="Window Title")],
        static_label="Pinned Name",
    )

    assert zentool.tab_to_spec(tab) == zentool.TabSpec(
        name="Pinned Name",
        url="https://example.com",
    )


def test_clone_tab_returns_deep_copy_when_existing_tab_is_present(
    zentool: ModuleType,
) -> None:
    """Cloning should isolate nested entry and attribute state."""
    original = make_tab(
        zentool,
        entries=[make_entry(zentool, url="https://example.com", title="Original")],
        attributes={"nested": {"count": 1}},
    )

    cloned = zentool.clone_tab(original)
    cloned.entries[0].title = "Updated"
    cloned.attributes["nested"]["count"] = 2

    assert cloned is not original
    assert original.entries[0].title == "Original"
    assert original.attributes["nested"]["count"] == 1


def test_clone_tab_without_existing_returns_fresh_shell(zentool: ModuleType) -> None:
    """A missing existing tab should produce a minimal default shell."""
    cloned = zentool.clone_tab(None)

    assert cloned == zentool.SessionTab(zenSyncId="")


def test_reset_active_entry_replaces_history_with_single_target_entry(
    zentool: ModuleType,
) -> None:
    """Resetting should replace history and restore one-based active indexing."""
    tab = make_tab(
        zentool,
        entries=[
            make_entry(zentool, url="https://old.example/one", title="One"),
            make_entry(zentool, url="https://old.example/two", title="Two"),
        ],
        index=2,
    )

    zentool.reset_active_entry(tab, name="Pinned", url="https://new.example")

    assert tab.entries == [
        zentool.SessionEntry(url="https://new.example", title="Pinned")
    ]
    assert tab.index == 1


def test_build_tab_reuses_active_entry_when_url_already_matches(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Build should preserve the active entry when the URL is already correct."""
    monkeypatch.setattr(zentool.time, "time", lambda: 999.0)
    existing = make_tab(
        zentool,
        entries=[
            make_entry(zentool, url="https://example.com", title="Existing Title")
        ],
        static_label="Old Label",
        last_accessed=456,
        attributes={"nested": ["value"]},
    )
    original_attributes = existing.attributes
    spec = zentool.TabSpec(name="Pinned Label", url="https://example.com")

    built = zentool.build_tab(
        spec,
        existing=existing,
        sync_id="sync-new",
        pinned=False,
        essential=True,
        workspace_uuid="{workspace}",
        folder_id="folder-1",
        user_context_id=7,
    )

    assert built is not existing
    assert built.entries == existing.entries
    assert built.index == existing.index
    assert built.zenSyncId == "sync-new"
    assert built.pinned is True
    assert built.hidden is False
    assert built.zenWorkspace == "{workspace}"
    assert built.zenEssential is True
    assert built.groupId == "folder-1"
    assert built.zenIsEmpty is False
    assert built.zenStaticLabel == "Pinned Label"
    assert built.userContextId == 7
    assert built.zenDefaultUserContextId == "true"
    assert built.lastAccessed == 456
    assert built.attributes == {"nested": ["value"]}
    assert built.attributes is not original_attributes


def test_build_tab_resets_entry_for_mismatched_url_and_placeholder(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Build should rewrite history for URL changes and placeholder tabs."""
    monkeypatch.setattr(zentool.time, "time", lambda: 1234.5)
    existing = make_tab(
        zentool,
        entries=[make_entry(zentool, url="https://old.example", title="Old")],
        index=9,
        sync_id="old-sync",
        last_accessed=0,
    )
    spec = zentool.ItemTabSpec(name="Folder Tab", url="https://new.example")

    built = zentool.build_tab(
        spec,
        existing=existing,
        sync_id="sync-updated",
        pinned=True,
        essential=False,
        workspace_uuid="{workspace}",
        folder_id="folder-2",
        user_context_id=0,
    )
    placeholder = zentool.build_tab(
        spec,
        existing=existing,
        sync_id="sync-placeholder",
        pinned=True,
        essential=False,
        workspace_uuid="{workspace}",
        folder_id="folder-2",
        user_context_id=0,
        placeholder=True,
    )

    assert built.entries == [
        zentool.SessionEntry(url="https://new.example", title="Folder Tab")
    ]
    assert built.index == 1
    assert built.zenStaticLabel == "Folder Tab"
    assert built.zenIsEmpty is False
    assert built.lastAccessed == 1234500
    assert built.zenDefaultUserContextId is None

    assert placeholder.entries == [zentool.SessionEntry(url="", title="Folder Tab")]
    assert placeholder.zenStaticLabel is None
    assert placeholder.zenIsEmpty is True


def test_build_placeholder_tab_creates_empty_folder_sentinel(
    zentool: ModuleType,
) -> None:
    """Placeholder-tab builder should match the empty-folder shape."""
    tab = zentool.build_placeholder_tab(
        sync_id="sync-empty",
        workspace_uuid="{workspace}",
        folder_id="folder-empty",
        user_context_id=4,
    )

    assert tab == zentool.SessionTab(
        entries=[],
        index=1,
        lastAccessed=0,
        hidden=False,
        pinned=True,
        zenWorkspace="{workspace}",
        zenSyncId="sync-empty",
        zenEssential=False,
        zenDefaultUserContextId="true",
        zenPinnedIcon=None,
        zenIsEmpty=True,
        zenStaticLabel=None,
        zenHasStaticIcon=False,
        zenGlanceId=None,
        zenIsGlance=False,
        zenLiveFolderItemId=None,
        searchMode=None,
        userContextId=4,
        groupId="folder-empty",
        attributes={},
    )


def test_folder_lookup_by_path_indexes_casefolded_workspace_paths(
    zentool: ModuleType,
) -> None:
    """Folder lookup should resolve nested paths within each workspace."""
    root = make_folder(
        zentool,
        folder_id="folder-root",
        name="AI",
        workspace_id="ws-1",
    )
    child = make_folder(
        zentool,
        folder_id="folder-child",
        name="Agents",
        workspace_id="ws-1",
        parent_id="folder-root",
    )
    orphan = make_folder(
        zentool,
        folder_id="folder-orphan",
        name="Ignored",
        workspace_id="missing-workspace",
    )
    session = zentool.SessionState(
        folders=[root, child, orphan],
        spaces=[make_space(zentool, uuid="ws-1", name="Work")],
    )

    lookup = zentool.folder_lookup_by_path(
        session,
        spaces_by_uuid={"ws-1": session.spaces[0]},
    )

    assert lookup[("work", ("ai",))] is root
    assert lookup[("work", ("ai", "agents"))] is child
    assert all(folder is not orphan for folder in lookup.values())


def test_folder_lookup_by_path_rejects_duplicate_casefolded_paths(
    zentool: ModuleType,
) -> None:
    """Case-insensitive duplicate folder paths should fail fast."""
    session = zentool.SessionState(
        folders=[
            make_folder(
                zentool,
                folder_id="folder-1",
                name="AI",
                workspace_id="ws-1",
            ),
            make_folder(
                zentool,
                folder_id="folder-2",
                name="ai",
                workspace_id="ws-1",
            ),
        ],
        spaces=[make_space(zentool, uuid="ws-1", name="Work")],
    )

    with pytest.raises(zentool.ZenFoldersError, match="Duplicate folder path"):
        zentool.folder_lookup_by_path(
            session, spaces_by_uuid={"ws-1": session.spaces[0]}
        )
