"""Focused pure-Python tests for zentool listing, check, and diff helpers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from lib.tests._zen_tooling import load_zen_script_module

if TYPE_CHECKING:
    from types import ModuleType


@pytest.fixture(scope="module")
def zentool() -> ModuleType:
    """Load the zentool script for listing/check helper tests."""
    return load_zen_script_module("zentool", "zentool_listing_helpers")


def make_entry(zentool: ModuleType, *, url: str, title: str = "") -> object:
    """Build one compact session history entry."""
    return zentool.SessionEntry(url=url, title=title)


def make_tab(
    zentool: ModuleType,
    *,
    url: str,
    title: str = "",
    sync_id: str,
    pinned: bool = False,
    essential: bool = False,
    workspace: str | None = None,
    folder_id: str | None = None,
    static_label: str | None = None,
    empty: bool = False,
) -> object:
    """Build one compact session tab."""
    return zentool.SessionTab(
        entries=[] if empty else [make_entry(zentool, url=url, title=title)],
        zenSyncId=sync_id,
        pinned=pinned,
        zenEssential=essential,
        zenWorkspace=workspace,
        groupId=folder_id,
        zenStaticLabel=static_label,
        zenIsEmpty=empty,
    )


def make_folder(
    zentool: ModuleType,
    *,
    folder_id: str,
    name: str,
    workspace_id: str,
    parent_id: str | None = None,
    collapsed: bool = True,
) -> object:
    """Build one compact folder record."""
    return zentool.SessionFolder(
        id=folder_id,
        name=name,
        workspaceId=workspace_id,
        parentId=parent_id,
        collapsed=collapsed,
    )


def make_group(zentool: ModuleType, *, group_id: str, name: str = "") -> object:
    """Build one compact matching folder group record."""
    return zentool.SessionGroup(id=group_id, name=name or group_id)


def make_space(zentool: ModuleType, *, uuid: str, name: str) -> object:
    """Build one compact workspace record."""
    return zentool.SessionSpace(uuid=uuid, name=name)


def make_session(
    zentool: ModuleType,
    *,
    tabs: list[object] | None = None,
    groups: list[object] | None = None,
    folders: list[object] | None = None,
    spaces: list[object] | None = None,
) -> object:
    """Build one compact session state fixture."""
    return zentool.SessionState(
        tabs=list(tabs or []),
        groups=list(groups or []),
        folders=list(folders or []),
        spaces=list(spaces or []),
    )


def test_diff_session_uses_desired_state_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Diffing should compare the current snapshot to the built desired snapshot."""
    current = make_session(zentool)
    desired = make_session(
        zentool,
        spaces=[make_space(zentool, uuid="{ws}", name="Work")],
    )
    config = zentool.ZenConfig()
    calls: list[object] = []

    monkeypatch.setattr(
        zentool,
        "build_desired_state",
        lambda session, built_config, _plan=None: (
            desired if session is current and built_config is config else None
        ),
    )

    def fake_snapshot(session: object, _containers: object = None) -> dict[str, int]:
        calls.append(session)
        return {"space_count": len(session.spaces)}

    monkeypatch.setattr(zentool, "snapshot", fake_snapshot)

    diff = zentool.diff_session(current, config)

    assert calls == [desired, current]
    assert diff["values_changed"]["root['space_count']"] == {
        "old_value": 0,
        "new_value": 1,
    }


def test_iter_folder_records_builds_workspace_prefixed_paths(
    zentool: ModuleType,
) -> None:
    """Folder iteration should use session order and reconstruct nested paths."""
    session = make_session(
        zentool,
        spaces=[make_space(zentool, uuid="{ws}", name="Workspace")],
        folders=[
            make_folder(
                zentool,
                folder_id="child",
                name="Child",
                workspace_id="{ws}",
                parent_id="parent",
            ),
            make_folder(
                zentool,
                folder_id="parent",
                name="Parent",
                workspace_id="{ws}",
                collapsed=False,
            ),
            make_folder(
                zentool,
                folder_id="orphan",
                name="Orphan",
                workspace_id="missing-workspace",
            ),
        ],
    )

    assert list(zentool.iter_folder_records(session)) == [
        ("Workspace / Parent / Child", session.folders[0]),
        ("Workspace / Parent", session.folders[1]),
        ("missing-workspace / Orphan", session.folders[2]),
    ]


def test_session_check_accepts_valid_managed_structure(zentool: ModuleType) -> None:
    """A structurally coherent managed subset should report no errors."""
    session = make_session(
        zentool,
        groups=[make_group(zentool, group_id="folder-1")],
        folders=[
            make_folder(
                zentool,
                folder_id="folder-1",
                name="Research",
                workspace_id="{ws}",
            )
        ],
        tabs=[
            make_tab(
                zentool,
                url="https://essential.example",
                title="Essential",
                sync_id="essential",
                pinned=True,
                essential=True,
            ),
            make_tab(
                zentool,
                url="https://folder.example",
                title="Folder",
                sync_id="folder-tab",
                pinned=True,
                workspace="{ws}",
                folder_id="folder-1",
            ),
        ],
    )

    assert zentool.session_check(session) == []


def test_session_check_collects_folder_and_tab_errors(zentool: ModuleType) -> None:
    """Session checking should aggregate each structural failure branch once."""
    session = make_session(
        zentool,
        groups=[
            make_group(zentool, group_id="dup-a"),
            make_group(zentool, group_id="dup-b"),
            make_group(zentool, group_id="bad-parent"),
        ],
        folders=[
            make_folder(
                zentool,
                folder_id="missing-group",
                name="Lonely",
                workspace_id="{ws}",
            ),
            make_folder(
                zentool,
                folder_id="dup-a",
                name="Dup",
                workspace_id="{ws}",
            ),
            make_folder(
                zentool,
                folder_id="dup-b",
                name="Dup",
                workspace_id="{ws}",
            ),
            make_folder(
                zentool,
                folder_id="bad-parent",
                name="Child",
                workspace_id="{ws}",
                parent_id="missing-parent",
            ),
        ],
        tabs=[
            make_tab(
                zentool,
                url="https://unknown-folder.example",
                sync_id="unknown-folder",
                pinned=True,
                folder_id="missing-folder",
            ),
            make_tab(
                zentool,
                url="https://essential-unpinned.example",
                sync_id="essential-unpinned",
                essential=True,
            ),
            make_tab(
                zentool,
                url="https://skipped-empty.example",
                sync_id="empty",
                essential=True,
                folder_id="missing-folder",
                empty=True,
            ),
        ],
    )

    assert zentool.session_check(session) == [
        "Folder 'Lonely' has no matching group",
        "Duplicate folder name in one container: 'Dup'",
        "Folder 'Child' has unknown parentId 'missing-parent'",
        "Tab 'https://unknown-folder.example' references unknown folder 'missing-folder'",
        "Essential tab 'https://essential-unpinned.example' is not pinned",
    ]


def test_cmd_list_prints_empty_and_populated_folder_views(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Folder listing should print the empty message and numbered rows."""
    lines: list[str] = []
    monkeypatch.setattr(zentool, "_stdout", lambda message="": lines.append(message))
    monkeypatch.setattr(
        zentool,
        "load_session",
        lambda _profile: ("/tmp/session", make_session(zentool)),
    )

    assert zentool.cmd_list(SimpleNamespace(profile="default")) == 0
    assert lines == ["No folders found."]

    lines.clear()
    session = make_session(
        zentool,
        spaces=[make_space(zentool, uuid="{ws}", name="Work")],
        folders=[
            make_folder(
                zentool,
                folder_id="alpha",
                name="Alpha",
                workspace_id="{ws}",
                collapsed=False,
            ),
            make_folder(
                zentool,
                folder_id="beta",
                name="Beta",
                workspace_id="{ws}",
                parent_id="alpha",
            ),
        ],
    )
    monkeypatch.setattr(
        zentool,
        "load_session",
        lambda _profile: ("/tmp/session", session),
    )

    assert zentool.cmd_list(SimpleNamespace(profile="default")) == 0
    assert lines == [
        " 1. Work / Alpha  [collapsed=False]",
        " 2. Work / Alpha / Beta  [collapsed=True]",
    ]


def test_cmd_tabs_prints_essentials_workspace_sections_and_folder_tabs(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Tab listing should group essentials, pinned tabs, folder tabs, and tabs."""
    lines: list[str] = []
    session = make_session(
        zentool,
        spaces=[make_space(zentool, uuid="{work}", name="Work")],
        folders=[
            make_folder(
                zentool,
                folder_id="folder-1",
                name="Research",
                workspace_id="{work}",
            )
        ],
        tabs=[
            make_tab(
                zentool,
                url="https://essential.example",
                sync_id="essential",
                pinned=True,
                essential=True,
                static_label="Essential Tab",
            ),
            make_tab(
                zentool,
                url="https://pinned.example",
                sync_id="pinned",
                pinned=True,
                workspace="{work}",
                static_label="Pinned Tab",
            ),
            make_tab(
                zentool,
                url="https://folder.example",
                sync_id="folder-tab",
                pinned=True,
                workspace="{work}",
                folder_id="folder-1",
                static_label="Folder Tab",
            ),
            make_tab(
                zentool,
                url="https://normal.example",
                sync_id="normal",
                workspace="{work}",
                static_label="Normal Tab",
            ),
            make_tab(
                zentool,
                url="https://skipped-empty.example",
                sync_id="empty",
                pinned=True,
                workspace="{work}",
                static_label="Skipped",
                empty=True,
            ),
        ],
    )
    monkeypatch.setattr(zentool, "_stdout", lambda message="": lines.append(message))
    monkeypatch.setattr(
        zentool,
        "load_session",
        lambda _profile: ("/tmp/session", session),
    )

    assert zentool.cmd_tabs(SimpleNamespace(profile="default")) == 0
    assert lines == [
        "Essentials:",
        "  Essential Tab -> https://essential.example",
        "",
        "Workspace: Work",
        "  Pinned:",
        "    Pinned Tab -> https://pinned.example",
        "  Folder: Research",
        "    Folder Tab -> https://folder.example",
        "  Tabs:",
        "    Normal Tab -> https://normal.example",
    ]


def test_cmd_inspect_folder_and_tab_wrappers_delegate(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """The inspect wrappers should stay thin aliases for list and tabs."""
    args = SimpleNamespace(profile="default")
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        zentool,
        "cmd_list",
        lambda forwarded_args: calls.append(("list", forwarded_args)) or 17,
    )
    monkeypatch.setattr(
        zentool,
        "cmd_tabs",
        lambda forwarded_args: calls.append(("tabs", forwarded_args)) or 23,
    )

    assert zentool.cmd_inspect_folders(args) == 17
    assert zentool.cmd_inspect_tabs(args) == 23
    assert calls == [("list", args), ("tabs", args)]
