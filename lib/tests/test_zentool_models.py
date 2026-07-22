"""Focused pure-Python tests for zentool models and tiny helpers."""

from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING

import pytest

from lib.tests._zen_tooling import load_zentool_module, make_session_entry
from lib.tests._zen_tooling import make_session_tab as make_zentool_tab

if TYPE_CHECKING:
    from types import ModuleType


@pytest.fixture(scope="module")
def zentool() -> ModuleType:
    """Load the zentool script for model and helper testing."""
    return load_zentool_module("zentool_models")


def make_session_tab(
    zentool: ModuleType,
    url: str,
    *,
    sync_id: str,
    static_label: str | None = None,
) -> object:
    """Build one minimal session tab with an active URL."""
    return make_zentool_tab(
        zentool,
        entries=[make_session_entry(zentool, url=url, title="Tab")],
        sync_id=sync_id,
        static_label=static_label,
        hidden=False,
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

    assert state.folders[0].prev_sibling_info == zentool.PrevSiblingInfo(
        type="start", id=None
    )
    assert state.folders[1].prev_sibling_info == zentool.PrevSiblingInfo(
        type="group", id="group-1"
    )


def test_session_models_preserve_only_json_shaped_opaque_fields(
    zentool: ModuleType,
) -> None:
    """Opaque Zen fields should be preserved but still runtime-validated as JSON."""
    state = zentool.SessionState.model_validate({
        "tabs": [
            {
                "zenSyncId": "tab-1",
                "searchMode": {"kind": "url", "enabled": True},
                "attributes": {"nested": ["value", 1, None]},
                "opaqueTabField": {"ok": [False]},
            }
        ],
        "opaqueRootField": {"count": 1},
    })

    assert state.model_extra == {"opaqueRootField": {"count": 1}}
    assert state.tabs[0].model_extra == {"opaqueTabField": {"ok": [False]}}

    with pytest.raises(zentool.ValidationError):
        zentool.SessionState.model_validate({"opaqueRootField": object()})

    with pytest.raises(zentool.ValidationError):
        zentool.SessionTab.model_validate({
            "zenSyncId": "tab-1",
            "attributes": {"notJson": object()},
        })


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


def test_tab_pool_claims_by_label_with_origin_restriction(
    zentool: ModuleType,
) -> None:
    """Label claims should trim, casefold, and honor same-origin-only mode."""
    cross_origin = make_session_tab(
        zentool,
        "https://sso.example/park",
        sync_id="tab-cross",
        static_label="  GitHub  ",
    )
    same_origin = make_session_tab(
        zentool,
        "https://github.com/notifications",
        sync_id="tab-same",
        static_label="GitHub",
    )
    solo = make_session_tab(
        zentool,
        "https://solo.example/home",
        sync_id="tab-solo",
        static_label="Solo",
    )
    pool = zentool.TabPool([cross_origin, same_origin, solo])

    assert (
        pool.claim_by_label(
            "github",
            url="https://github.com/coder",
            same_origin_only=True,
        )
        is same_origin
    )
    assert (
        pool.claim_by_label(" GitHub ", url="https://github.com/coder") is cross_origin
    )
    assert pool.claim_by_label("GitHub", url="https://github.com/coder") is None
    assert (
        pool.claim_by_label(
            "Solo",
            url="https://nomatch.example/",
            same_origin_only=True,
        )
        is None
    )
    assert pool.claim_by_label("Solo", url="about:blank", same_origin_only=True) is None
    assert pool.claim_by_label("Solo", url="https://nomatch.example/") is solo
    assert pool.claim_by_label("   ", url="https://github.com/coder") is None
    assert pool.claim_by_label("missing", url="https://github.com/coder") is None


def test_tab_pool_claims_by_origin_with_scheme_and_label_guards(
    zentool: ModuleType,
) -> None:
    """Origin claims should walk managed tabs FIFO and skip unmanaged or non-HTTP tabs."""
    unmanaged = make_session_tab(
        zentool,
        "https://app.example/adhoc",
        sync_id="tab-adhoc",
    )
    first = make_session_tab(
        zentool,
        "https://app.example/a",
        sync_id="tab-a",
        static_label="A",
    )
    second = make_session_tab(
        zentool,
        "https://app.example/b",
        sync_id="tab-b",
        static_label="B",
    )
    non_http = make_session_tab(
        zentool,
        "about:blank",
        sync_id="tab-about",
        static_label="Blank",
    )
    pool = zentool.TabPool([unmanaged, first, second, non_http])

    assert pool.claim_by_origin("https://app.example/zzz") is first
    assert pool.claim_by_origin("https://app.example/zzz") is second
    assert pool.claim_by_origin("https://app.example/zzz") is None
    assert pool.claim_by_origin("about:blank") is None
    assert pool.claim_by_origin("https://other.example/") is None


def test_tab_pool_claims_are_exclusive_across_indexes(
    zentool: ModuleType,
) -> None:
    """A tab claimed through one index should be invisible to the others."""
    by_url = make_session_tab(
        zentool,
        "https://app.example/page",
        sync_id="tab-url",
        static_label="App",
    )
    by_label = make_session_tab(
        zentool,
        "https://docs.example/home",
        sync_id="tab-label",
        static_label="Docs",
    )
    pool = zentool.TabPool([by_url, by_label])

    assert pool.claim("https://app.example/page") is by_url
    assert pool.claim_by_label("App", url="https://app.example/page") is None
    assert pool.claim_by_origin("https://app.example/page") is None

    assert pool.claim_by_label("docs", url="https://elsewhere.example/") is by_label
    assert pool.claim("https://docs.example/home") is None
    assert pool.claim_by_origin("https://docs.example/home") is None


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
