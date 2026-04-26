"""Regression tests for the schema-first Zen session sync tool."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import lz4.block
import pytest
import yaml

from lib.tests._zen_tooling import load_zen_script_module, resolve_zen_script_path

ZEN_FOLDERS_PATH = resolve_zen_script_path("zentool")
ZENTOOL = load_zen_script_module("zentool", "zentool_script")


@pytest.fixture
def base_session() -> object:
    """Return a minimal reusable session payload."""
    return ZENTOOL.SessionState(
        tabs=[],
        groups=[],
        folders=[],
        spaces=[
            ZENTOOL.SessionSpace(
                uuid="ws-work",
                name="Work",
                icon="🏢",
                containerTabId=6,
            )
        ],
    )


def make_tab(
    *,
    sync_id: str,
    url: str,
    title: str,
    pinned: bool,
    workspace: str | None,
    group_id: str | None = None,
    essential: bool = False,
) -> object:
    """Build a compact tab record for tests."""
    return ZENTOOL.SessionTab(
        entries=[ZENTOOL.SessionEntry(url=url, title=title)],
        index=1,
        lastAccessed=1,
        hidden=False,
        pinned=pinned or essential,
        zenWorkspace=workspace,
        zenSyncId=sync_id,
        zenEssential=essential,
        userContextId=6 if workspace else 0,
        groupId=group_id,
        attributes={},
    )


def make_folder(
    *,
    folder_id: str,
    name: str,
    workspace_id: str,
    parent_id: str | None = None,
    prev_type: str = "start",
    prev_id: str | None = None,
) -> object:
    """Build a compact folder record for tests."""
    return ZENTOOL.SessionFolder(
        id=folder_id,
        name=name,
        workspaceId=workspace_id,
        parentId=parent_id,
        prevSiblingInfo=ZENTOOL.PrevSiblingInfo(type=prev_type, id=prev_id),
    )


def test_wrapper_script_parses_under_python3() -> None:
    """Catch syntax regressions before activation tries to execute the wrapper."""
    compile(
        ZEN_FOLDERS_PATH.read_text(encoding="utf-8"),
        str(ZEN_FOLDERS_PATH),
        "exec",
    )


def test_load_config_rejects_duplicate_keys(tmp_path: Path) -> None:
    """Duplicate YAML keys should fail fast."""
    config_path = tmp_path / "folders.yaml"
    config_path.write_text(
        "Work:\n  - AI:\n      - OpenAI: https://platform.openai.com\nWork: []\n",
        encoding="utf-8",
    )

    with pytest.raises(ZENTOOL.ZenFoldersError, match="duplicate key"):
        ZENTOOL.load_config(config_path)


def test_load_config_rejects_container_tab_id_in_public_schema(tmp_path: Path) -> None:
    """Container IDs should no longer appear in the user-facing schema."""
    config_path = tmp_path / "folders.yaml"
    config_path.write_text(
        "Work:\n  containerTabId: 6\n",
        encoding="utf-8",
    )

    with pytest.raises(ZENTOOL.ZenFoldersError, match="containerTabId"):
        ZENTOOL.load_config(config_path)


def test_load_config_accepts_workspace_map_with_unified_nodes(tmp_path: Path) -> None:
    """Top-level workspace keys should normalize into the canonical workspace model."""
    config_path = tmp_path / "folders.yaml"
    config_path.write_text(
        "Work:\n"
        "  - Gmail:\n"
        "      url: https://mail.google.com/mail/u/0/#inbox\n"
        "      role: essential\n"
        "  - AI / LLM:\n"
        "      - Anthropic: https://platform.claude.com\n"
        "      - OpenAI: https://platform.openai.com\n"
        "  - Scratch:\n"
        "      url: https://scratch.example.com\n"
        "      role: tab\n"
        "Home: []\n",
        encoding="utf-8",
    )

    config = ZENTOOL.load_config(config_path)

    assert [workspace.name for workspace in config.workspaces] == ["Work", "Home"]

    work = config.workspaces[0]
    assert work.essentials == [
        ZENTOOL.TabSpec(
            name="Gmail",
            url="https://mail.google.com/mail/u/0/#inbox",
        )
    ]
    assert [item.name for item in work.items] == ["AI / LLM"]
    assert [child.name for child in work.items[0].items] == ["Anthropic", "OpenAI"]
    assert work.tabs == [
        ZENTOOL.TabSpec(
            name="Scratch",
            url="https://scratch.example.com",
        )
    ]

    home = config.workspaces[1]
    assert home.items == []
    assert home.tabs == []


def test_load_config_accepts_workspace_metadata_tree_form(tmp_path: Path) -> None:
    """Workspace metadata objects should accept a `tree` plus unified nodes."""
    config_path = tmp_path / "folders.yaml"
    config_path.write_text(
        "Work:\n"
        '  icon: "🏢"\n'
        "  tree:\n"
        "    - Infrastructure:\n"
        "        collapsed: false\n"
        "        children:\n"
        "          - AWS: https://console.cloud.google.com\n"
        "    - Scratch:\n"
        "        url: https://scratch.example.com\n"
        "        role: tab\n",
        encoding="utf-8",
    )

    config = ZENTOOL.load_config(config_path)

    work = config.workspaces[0]
    assert work.name == "Work"
    assert work.icon == "🏢"
    assert len(work.items) == 1
    assert work.items[0].name == "Infrastructure"
    assert work.items[0].collapsed is False
    assert work.items[0].items == [
        ZENTOOL.ItemTabSpec(
            name="AWS",
            url="https://console.cloud.google.com",
        )
    ]
    assert work.tabs == [
        ZENTOOL.TabSpec(
            name="Scratch",
            url="https://scratch.example.com",
        )
    ]


def test_load_config_defaults_folder_collapsed_to_true(tmp_path: Path) -> None:
    """Folder shorthand should default collapsed folders to true."""
    config_path = tmp_path / "folders.yaml"
    config_path.write_text(
        "Work:\n  - AI / LLM:\n      - Anthropic: https://platform.claude.com\n",
        encoding="utf-8",
    )

    config = ZENTOOL.load_config(config_path)

    work = config.workspaces[0]
    assert len(work.items) == 1
    assert work.items[0].name == "AI / LLM"
    assert work.items[0].collapsed is True


def test_load_config_rejects_non_list_workspace_tree(tmp_path: Path) -> None:
    """Workspace trees should require a list of unified nodes."""
    config_path = tmp_path / "folders.yaml"
    config_path.write_text(
        "Work:\n  tree:\n    Gmail: https://mail.google.com/mail/u/0/#inbox\n",
        encoding="utf-8",
    )

    with pytest.raises(ZENTOOL.ZenFoldersError, match="tree must be a list"):
        ZENTOOL.load_config(config_path)


def test_build_desired_state_syncs_exact_subset(
    base_session: object,
) -> None:
    """Sync should preserve matches and remove undeclared managed items."""
    base_session.groups = [
        ZENTOOL.SessionGroup(id="f-ai", name="AI"),
        ZENTOOL.SessionGroup(id="f-old", name="Old"),
    ]
    base_session.folders = [
        make_folder(folder_id="f-ai", name="AI", workspace_id="ws-work"),
        make_folder(
            folder_id="f-old",
            name="Old",
            workspace_id="ws-work",
            prev_type="group",
            prev_id="f-ai",
        ),
    ]
    base_session.tabs = [
        make_tab(
            sync_id="ess-mail",
            url="https://mail.google.com/mail/u/0/",
            title="Mail",
            pinned=True,
            workspace="ws-work",
            essential=True,
        ),
        make_tab(
            sync_id="top-google",
            url="https://calendar.google.com/calendar/u/0/r",
            title="Calendar",
            pinned=True,
            workspace="ws-work",
        ),
        make_tab(
            sync_id="top-openai",
            url="https://platform.openai.com",
            title="OpenAI",
            pinned=True,
            workspace="ws-work",
        ),
        make_tab(
            sync_id="folder-anthropic",
            url="https://platform.claude.com",
            title="Anthropic",
            pinned=True,
            workspace="ws-work",
            group_id="f-ai",
        ),
        make_tab(
            sync_id="folder-old",
            url="https://old.example.com",
            title="Old",
            pinned=True,
            workspace="ws-work",
            group_id="f-old",
        ),
        make_tab(
            sync_id="tab-scratch",
            url="https://scratch.example.com",
            title="Scratch",
            pinned=False,
            workspace="ws-work",
        ),
    ]

    config = ZENTOOL.ZenConfig.model_validate({
        "workspaces": [
            {
                "name": "Work",
                "icon": "🏢",
                "essentials": [
                    {
                        "name": "Mail",
                        "url": "https://mail.google.com/mail/u/0/",
                    }
                ],
                "items": [
                    {
                        "type": "folder",
                        "name": "AI",
                        "items": [
                            {
                                "type": "tab",
                                "name": "Anthropic",
                                "url": "https://platform.claude.com",
                            },
                            {
                                "type": "tab",
                                "name": "OpenAI",
                                "url": "https://platform.openai.com",
                            },
                        ],
                    }
                ],
            }
        ],
    })

    desired = ZENTOOL.build_desired_state(base_session, config)

    assert [space.name for space in desired.spaces] == ["Work"]
    assert desired.spaces[0].container_tab_id == 6
    assert [folder.name for folder in desired.folders] == ["AI"]
    assert [group.name for group in desired.groups] == ["AI"]
    assert [ZENTOOL.active_url(tab) for tab in desired.tabs] == [
        "https://mail.google.com/mail/u/0/",
        "https://platform.claude.com",
        "https://platform.openai.com",
    ]

    by_url = {ZENTOOL.active_url(tab): tab for tab in desired.tabs}
    assert by_url["https://mail.google.com/mail/u/0/"].zen_sync_id == "ess-mail"
    assert by_url["https://platform.claude.com"].zen_sync_id == "folder-anthropic"
    assert by_url["https://platform.openai.com"].zen_sync_id == "top-openai"
    assert by_url["https://platform.openai.com"].group_id == "f-ai"
    assert by_url["https://platform.openai.com"].zen_static_label == "OpenAI"
    assert all(
        ZENTOOL.active_url(tab)
        not in {
            "https://calendar.google.com/calendar/u/0/r",
            "https://old.example.com",
            "https://scratch.example.com",
        }
        for tab in desired.tabs
    )


def test_workspace_scoped_essential_tabs_build_as_zen_essentials(
    base_session: object,
) -> None:
    """Workspace essentials should become real zenEssential tabs in the session."""
    config = ZENTOOL.ZenConfig.model_validate({
        "workspaces": [
            {
                "name": "Work",
                "essentials": [
                    {
                        "name": "Gmail",
                        "url": "https://mail.google.com/mail/u/0/#inbox",
                    },
                    {
                        "name": "Calendar",
                        "url": "https://calendar.google.com/calendar/u/0/r",
                    },
                ],
                "items": [
                    {
                        "type": "folder",
                        "name": "AI",
                        "items": [
                            {
                                "type": "tab",
                                "name": "OpenAI",
                                "url": "https://platform.openai.com",
                            }
                        ],
                    }
                ],
            }
        ]
    })

    desired = ZENTOOL.build_desired_state(base_session, config)

    essential_tabs = [tab for tab in desired.tabs if tab.zen_essential]
    assert [ZENTOOL.active_url(tab) for tab in essential_tabs] == [
        "https://mail.google.com/mail/u/0/#inbox",
        "https://calendar.google.com/calendar/u/0/r",
    ]
    assert all(tab.pinned is True for tab in essential_tabs)
    assert all(tab.group_id is None for tab in essential_tabs)
    assert all(tab.zen_workspace == "ws-work" for tab in essential_tabs)
    assert all(tab.user_context_id == 6 for tab in essential_tabs)


def test_build_desired_state_sets_parent_links_and_folder_order(
    base_session: object,
) -> None:
    """Nested folders should compile into parentId and prevSiblingInfo links."""
    config = ZENTOOL.ZenConfig.model_validate({
        "workspaces": [
            {
                "name": "Work",
                "items": [
                    {
                        "type": "tab",
                        "name": "Search",
                        "url": "https://google.com",
                    },
                    {
                        "type": "folder",
                        "name": "AI",
                        "items": [
                            {
                                "type": "tab",
                                "name": "OpenAI",
                                "url": "https://platform.openai.com",
                            },
                            {
                                "type": "folder",
                                "name": "Providers",
                                "items": [
                                    {
                                        "type": "tab",
                                        "name": "Anthropic",
                                        "url": "https://platform.claude.com",
                                    }
                                ],
                            },
                        ],
                    },
                ],
            }
        ]
    })

    desired = ZENTOOL.build_desired_state(base_session, config)

    tabs_by_name = {
        tab.zen_static_label: tab for tab in desired.tabs if tab.zen_static_label
    }
    folders_by_name = {folder.name: folder for folder in desired.folders}

    assert folders_by_name["AI"].parent_id is None
    assert folders_by_name["AI"].prev_sibling_info == ZENTOOL.PrevSiblingInfo(
        type="tab",
        id=tabs_by_name["Search"].zen_sync_id,
    )
    assert folders_by_name["Providers"].parent_id == folders_by_name["AI"].id
    assert folders_by_name["Providers"].prev_sibling_info == ZENTOOL.PrevSiblingInfo(
        type="tab",
        id=tabs_by_name["OpenAI"].zen_sync_id,
    )


def test_existing_expanded_folders_collapse_when_config_defaults_true(
    base_session: object,
) -> None:
    """Existing expanded folders should collapse when config omits collapsed metadata."""
    base_session.groups = [
        ZENTOOL.SessionGroup(id="f-infra", name="Infrastructure", collapsed=False),
    ]
    base_session.folders = [
        make_folder(
            folder_id="f-infra",
            name="Infrastructure",
            workspace_id="ws-work",
        )
    ]
    base_session.folders[0].collapsed = False
    base_session.tabs = [
        make_tab(
            sync_id="infra-aws",
            url="https://console.cloud.google.com",
            title="Google Cloud",
            pinned=True,
            workspace="ws-work",
            group_id="f-infra",
        )
    ]

    config = ZENTOOL.ZenConfig.model_validate({
        "workspaces": [
            {
                "name": "Work",
                "items": [
                    {
                        "type": "folder",
                        "name": "Infrastructure",
                        "items": [
                            {
                                "type": "tab",
                                "name": "Google Cloud",
                                "url": "https://console.cloud.google.com",
                            }
                        ],
                    }
                ],
            }
        ]
    })

    desired = ZENTOOL.build_desired_state(base_session, config)

    assert [folder.name for folder in desired.folders] == ["Infrastructure"]
    assert desired.folders[0].collapsed is True
    assert desired.groups[0].collapsed is True


def test_session_write_round_trips_workspace_essentials_and_collapsed_folders(
    tmp_path: Path,
    base_session: object,
) -> None:
    """Written session files should preserve essentials and collapsed folders."""
    config = ZENTOOL.ZenConfig.model_validate({
        "workspaces": [
            {
                "name": "Work",
                "essentials": [
                    {
                        "name": "Mail",
                        "url": "https://mail.google.com/mail/u/0/",
                    }
                ],
                "items": [
                    {
                        "type": "folder",
                        "name": "Infrastructure",
                        "items": [
                            {
                                "type": "tab",
                                "name": "Google Cloud",
                                "url": "https://console.cloud.google.com",
                            }
                        ],
                    }
                ],
            }
        ],
    })

    desired = ZENTOOL.build_desired_state(base_session, config)
    session_path = tmp_path / "roundtrip.jsonlz4"
    ZENTOOL.write_session(session_path, desired)
    loaded = ZENTOOL.read_session(session_path)

    essential_tabs = [
        tab for tab in loaded.tabs if tab.zen_essential and not tab.zen_is_empty
    ]
    assert [ZENTOOL.active_url(tab) for tab in essential_tabs] == [
        "https://mail.google.com/mail/u/0/",
    ]
    assert all(tab.zen_workspace == "ws-work" for tab in essential_tabs)
    assert all(tab.group_id is None for tab in essential_tabs)

    folders_by_name = {folder.name: folder for folder in loaded.folders}
    groups_by_name = {group.name: group for group in loaded.groups}
    assert folders_by_name["Infrastructure"].collapsed is True
    assert groups_by_name["Infrastructure"].collapsed is True


def test_export_config_round_trips_clean_schema(
    base_session: object,
) -> None:
    """Export should emit the same clean schema that apply consumes."""
    config = ZENTOOL.ZenConfig.model_validate({
        "workspaces": [
            {
                "name": "Work",
                "icon": "🏢",
                "essentials": [
                    {
                        "name": "Mail",
                        "url": "https://mail.google.com/mail/u/0/",
                    }
                ],
                "items": [
                    {
                        "type": "tab",
                        "name": "Search",
                        "url": "https://google.com",
                    },
                    {
                        "type": "folder",
                        "name": "AI",
                        "collapsed": False,
                        "items": [
                            {
                                "type": "tab",
                                "name": "OpenAI",
                                "url": "https://platform.openai.com",
                            }
                        ],
                    },
                ],
                "tabs": [
                    {
                        "name": "Scratch",
                        "url": "https://scratch.example.com",
                    }
                ],
            }
        ],
    })

    desired = ZENTOOL.build_desired_state(base_session, config)
    exported = ZENTOOL.export_config(desired)

    assert exported == config


def test_diff_session_reports_no_changes_for_compiled_state(
    base_session: object,
) -> None:
    """Diff should be empty when the session already matches config."""
    config = ZENTOOL.ZenConfig.model_validate({
        "workspaces": [
            {
                "name": "Work",
                "items": [
                    {
                        "type": "folder",
                        "name": "AI",
                        "items": [
                            {
                                "type": "tab",
                                "name": "OpenAI",
                                "url": "https://platform.openai.com",
                            }
                        ],
                    }
                ],
            }
        ]
    })
    desired = ZENTOOL.build_desired_state(base_session, config)

    assert not ZENTOOL.diff_session(desired, config)


def test_cmd_dump_emits_clean_schema(
    tmp_path: Path,
    base_session: object,
) -> None:
    """Dump should write the new schema instead of legacy dunder keys."""
    config = ZENTOOL.ZenConfig.model_validate({
        "workspaces": [
            {
                "name": "Work",
                "items": [
                    {
                        "type": "folder",
                        "name": "AI",
                        "items": [
                            {
                                "type": "tab",
                                "name": "OpenAI",
                                "url": "https://platform.openai.com",
                            }
                        ],
                    }
                ],
            }
        ]
    })
    session_path = tmp_path / "zen-sessions.jsonlz4"
    output_path = tmp_path / "dump.yaml"
    ZENTOOL.write_session(
        session_path,
        ZENTOOL.build_desired_state(base_session, config),
    )

    args = argparse.Namespace(profile=str(session_path), output=str(output_path))
    assert ZENTOOL.cmd_dump(args) == 0

    dumped = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    assert "Work" in dumped
    assert "__workspace__" not in dumped
    assert dumped["Work"][0] == {
        "AI": [
            {
                "OpenAI": "https://platform.openai.com",
            }
        ]
    }


def test_read_session_rejects_oversized_payload_header(tmp_path: Path) -> None:
    """Reject oversized uncompressed-size headers before decompression."""
    oversized = ZENTOOL.MAX_SESSION_UNCOMPRESSED_BYTES + 1
    path = tmp_path / "bad-session.jsonlz4"
    path.write_bytes(b"mozLz40\0" + oversized.to_bytes(4, "little"))

    with pytest.raises(ZENTOOL.SessionFormatError, match="Invalid"):
        ZENTOOL.read_session(path)


def test_read_session_normalizes_nullable_prev_sibling_info(tmp_path: Path) -> None:
    """Raw Zen payloads may use null prevSiblingInfo for top-level folders."""
    session = {
        "tabs": [],
        "groups": [
            {
                "id": "folder-1",
                "name": "AI",
                "color": "zen-workspace-color",
                "collapsed": True,
                "pinned": True,
                "saveOnWindowClose": True,
                "splitView": False,
            }
        ],
        "folders": [
            {
                "id": "folder-1",
                "name": "AI",
                "collapsed": True,
                "emptyTabIds": [],
                "parentId": None,
                "pinned": True,
                "prevSiblingInfo": None,
                "saveOnWindowClose": True,
                "splitViewGroup": False,
                "userIcon": "",
                "workspaceId": "ws-work",
            }
        ],
        "spaces": [
            {
                "uuid": "ws-work",
                "name": "Work",
                "containerTabId": 6,
                "icon": "🏢",
                "hasCollapsedPinnedTabs": False,
                "theme": {
                    "type": "gradient",
                    "gradientColors": [],
                    "opacity": 0.5,
                    "texture": 0,
                },
            }
        ],
        "splitViewData": [],
    }
    path = tmp_path / "nullable-prev-sibling.jsonlz4"
    ZENTOOL.write_session(path, ZENTOOL.SessionState.model_validate(session))

    loaded = ZENTOOL.read_session(path)

    assert loaded.folders[0].prev_sibling_info == ZENTOOL.PrevSiblingInfo(
        type="start",
        id=None,
    )


def test_cmd_inspect_raw_literal_preserves_nullable_prev_sibling_info(
    tmp_path: Path,
) -> None:
    """Literal raw mode should emit the decoded payload without normalization."""
    session = {
        "tabs": [],
        "groups": [
            {
                "id": "folder-1",
                "name": "AI",
                "color": "zen-workspace-color",
                "collapsed": True,
                "pinned": True,
                "saveOnWindowClose": True,
                "splitView": False,
            }
        ],
        "folders": [
            {
                "id": "folder-1",
                "name": "AI",
                "collapsed": True,
                "emptyTabIds": [],
                "parentId": None,
                "pinned": True,
                "prevSiblingInfo": None,
                "saveOnWindowClose": True,
                "splitViewGroup": False,
                "userIcon": "",
                "workspaceId": "ws-work",
            }
        ],
        "spaces": [
            {
                "uuid": "ws-work",
                "name": "Work",
                "containerTabId": 6,
                "icon": "🏢",
                "hasCollapsedPinnedTabs": False,
                "theme": {
                    "type": "gradient",
                    "gradientColors": [],
                    "opacity": 0.5,
                    "texture": 0,
                },
            }
        ],
        "splitViewData": [],
    }
    session_path = tmp_path / "literal-raw.jsonlz4"
    output_path = tmp_path / "literal-raw.json"

    payload = json.dumps(session, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    data = (
        ZENTOOL.SESSION_HEADER_PREFIX
        + len(payload).to_bytes(4, "little")
        + lz4.block.compress(payload, store_size=False)
    )
    session_path.write_bytes(data)

    args = argparse.Namespace(
        profile=str(session_path),
        output=str(output_path),
        literal=True,
    )
    assert ZENTOOL.cmd_inspect_raw(args) == 0

    dumped = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    assert dumped["folders"][0]["prevSiblingInfo"] is None


def test_resolve_profile_dir_supports_human_profile_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Visible names from profiles.ini should resolve to profile directories."""
    app_support = tmp_path / "zen"
    profiles_dir = app_support / "Profiles"
    twilight_dir = profiles_dir / "abc123.Default (twilight)"
    other_dir = profiles_dir / "xyz987.Default Profile"
    twilight_dir.mkdir(parents=True)
    other_dir.mkdir()

    profiles_ini = app_support / "profiles.ini"
    profiles_ini.write_text(
        "[Profile0]\n"
        "Name=Default Profile\n"
        "IsRelative=1\n"
        "Path=Profiles/xyz987.Default Profile\n\n"
        "[Profile1]\n"
        "Name=Default (twilight)\n"
        "IsRelative=1\n"
        "Path=Profiles/abc123.Default (twilight)\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(ZENTOOL, "ZEN_APPLICATION_SUPPORT", app_support)
    monkeypatch.setattr(ZENTOOL, "ZEN_PROFILES", profiles_dir)
    monkeypatch.setattr(ZENTOOL, "PROFILES_INI", profiles_ini)

    assert ZENTOOL.resolve_profile_dir("default (TWILIGHT)") == twilight_dir


def test_resolve_profile_dir_auto_detects_install_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auto-detection should prefer the install default from profiles.ini."""
    app_support = tmp_path / "zen"
    profiles_dir = app_support / "Profiles"
    twilight_dir = profiles_dir / "abc123.Default (twilight)"
    regular_dir = profiles_dir / "xyz987.Default Profile"
    twilight_dir.mkdir(parents=True)
    regular_dir.mkdir()

    profiles_ini = app_support / "profiles.ini"
    profiles_ini.write_text(
        "[Profile0]\n"
        "Name=Default Profile\n"
        "IsRelative=1\n"
        "Path=Profiles/xyz987.Default Profile\n"
        "Default=1\n\n"
        "[Profile1]\n"
        "Name=Default (twilight)\n"
        "IsRelative=1\n"
        "Path=Profiles/abc123.Default (twilight)\n\n"
        "[Install9EBD2AC824310766]\n"
        "Default=Profiles/abc123.Default (twilight)\n"
        "Locked=1\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(ZENTOOL, "ZEN_APPLICATION_SUPPORT", app_support)
    monkeypatch.setattr(ZENTOOL, "ZEN_PROFILES", profiles_dir)
    monkeypatch.setattr(ZENTOOL, "PROFILES_INI", profiles_ini)

    assert ZENTOOL.resolve_profile_dir(None) == twilight_dir


def test_build_parser_accepts_profile_after_subcommand() -> None:
    """Subcommands should accept a local profile flag in addition to the global one."""
    args = ZENTOOL.build_parser().parse_args([
        "apply",
        "--profile",
        "Default (twilight)",
        "--state",
        "--yes",
    ])

    assert args.command == "apply"
    assert args.profile == "Default (twilight)"
    assert args.state is True
    assert args.assets is False


def test_build_parser_accepts_dump_and_check_subcommands() -> None:
    """Dump and check should remain exposed as direct CLI subcommands."""
    dump_args = ZENTOOL.build_parser().parse_args([
        "dump",
        "--profile",
        "Default (twilight)",
        "--output",
        "dump.yaml",
    ])
    check_args = ZENTOOL.build_parser().parse_args([
        "check",
        "--profile",
        "Default (twilight)",
    ])

    assert dump_args.command == "dump"
    assert dump_args.profile == "Default (twilight)"
    assert dump_args.output == "dump.yaml"
    assert check_args.command == "check"
    assert check_args.profile == "Default (twilight)"


def test_main_dispatches_dump_and_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Main should route dump and check through their dedicated handlers."""
    monkeypatch.setattr(ZENTOOL, "cmd_dump", lambda _args: 17)
    monkeypatch.setattr(ZENTOOL, "cmd_check", lambda _args: 23)

    assert ZENTOOL.main(["dump"]) == 17
    assert ZENTOOL.main(["check"]) == 23


def test_zen_is_running_ignores_stale_lock_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A leftover lock file should not block reconciliation when not held."""
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    (profile_dir / ".parentlock").write_text("", encoding="utf-8")

    monkeypatch.setattr(ZENTOOL, "zen_profile_dir", lambda _profile: profile_dir)
    monkeypatch.setattr(ZENTOOL, "_lock_probe_state", lambda _path: False)

    assert ZENTOOL.zen_profile_lock_state(None) is False
    assert ZENTOOL.zen_is_running(None) is False


def test_require_zen_closed_reports_detection_details_when_runtime_is_uncertain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed runtime probes should explain why apply refuses to continue."""
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    (profile_dir / ".parentlock").write_text("", encoding="utf-8")

    monkeypatch.setattr(ZENTOOL, "zen_profile_dir", lambda _profile: profile_dir)
    monkeypatch.setattr(
        ZENTOOL,
        "session_file",
        lambda _profile: tmp_path / "zen-sessions.jsonlz4",
    )
    monkeypatch.setattr(ZENTOOL, "_lock_probe_state", lambda _path: None)
    monkeypatch.setattr(ZENTOOL, "zen_process_is_running", lambda: None)

    with pytest.raises(ZENTOOL.ZenFoldersError, match="Detection details"):
        ZENTOOL.require_zen_closed(None)


def test_session_check_detects_unknown_folder_reference(
    base_session: object,
) -> None:
    """The managed invariant checker should catch bad folder references."""
    base_session.tabs = [
        make_tab(
            sync_id="bad-tab",
            url="https://example.com",
            title="Example",
            pinned=True,
            workspace="ws-work",
            group_id="missing-folder",
        )
    ]

    errors = ZENTOOL.session_check(base_session)

    assert any("unknown folder" in error for error in errors)
