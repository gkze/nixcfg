"""Focused pure-Python tests for zentool tab and folder helper seams."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from lib.tests._zen_tooling import load_zentool_module
from lib.tests._zen_tooling import make_session_entry as make_entry
from lib.tests._zen_tooling import make_session_folder as make_folder
from lib.tests._zen_tooling import make_session_space as make_space
from lib.tests._zen_tooling import make_session_tab as make_tab

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from types import ModuleType, TracebackType


@pytest.fixture(scope="module")
def zentool() -> ModuleType:
    """Load the zentool script for direct helper testing."""
    return load_zentool_module("zentool_tab_helpers")


def make_entry_with_extra(zentool: ModuleType, *, url: str, title: str = "") -> object:
    """Build one session history entry with opaque runtime data."""
    return make_entry(
        zentool,
        url=url,
        title=title,
        structuredCloneState="session-data",
    )


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
            make_entry_with_extra(
                zentool,
                url="https://example.com",
                title="Existing Title",
            )
        ],
        static_label="Old Label",
        last_accessed=456,
        attributes={"nested": ["value"]},
        pinned_icon="page-icon",
        has_static_icon=True,
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
    assert built.zen_sync_id == "sync-new"
    assert built.pinned is True
    assert built.hidden is False
    assert built.zen_workspace == "{workspace}"
    assert built.zen_essential is True
    assert built.group_id == "folder-1"
    assert built.zen_is_empty is False
    assert built.zen_static_label == "Pinned Label"
    assert built.zen_pinned_icon == "page-icon"
    assert built.zen_has_static_icon is True
    assert built.user_context_id == 7
    assert built.zen_default_user_context_id == "true"
    assert built.last_accessed == 456
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
    assert built.zen_static_label == "Folder Tab"
    assert built.zen_pinned_icon is None
    assert built.zen_has_static_icon is False
    assert built.zen_is_empty is False
    assert built.last_accessed == 1234500
    assert built.zen_default_user_context_id is None

    assert placeholder.entries == [zentool.SessionEntry(url="", title="Folder Tab")]
    assert placeholder.zen_static_label is None
    assert placeholder.zen_pinned_icon is None
    assert placeholder.zen_has_static_icon is False
    assert placeholder.zen_is_empty is True


def test_build_tab_resolves_favicon_for_new_tab(
    zentool: ModuleType,
) -> None:
    """New tabs should get an image cache when a resolver can find one."""
    spec = zentool.ItemTabSpec(name="Example", url="https://example.com")

    built = zentool.build_tab(
        spec,
        existing=None,
        sync_id="sync-new",
        pinned=True,
        essential=False,
        workspace_uuid="{workspace}",
        folder_id=None,
        user_context_id=0,
        favicon_resolver=lambda url: f"data:image/png;base64,{url}",
    )

    assert built.model_extra["image"] == "data:image/png;base64,https://example.com"


def test_build_tab_preserves_same_origin_image_when_url_is_canonicalized(
    zentool: ModuleType,
) -> None:
    """Canonicalizing a URL within one origin should keep the old image cache."""
    existing = make_tab(
        zentool,
        entries=[make_entry(zentool, url="https://example.com/dashboard", title="Old")],
        image="data:image/png;base64,old",
    )
    spec = zentool.ItemTabSpec(name="Example", url="https://example.com")

    built = zentool.build_tab(
        spec,
        existing=existing,
        sync_id="sync-new",
        pinned=True,
        essential=False,
        workspace_uuid="{workspace}",
        folder_id=None,
        user_context_id=0,
        favicon_resolver=lambda _url: pytest.fail("same-origin image should be kept"),
    )

    assert built.entries == [
        zentool.SessionEntry(url="https://example.com", title="Example")
    ]
    assert built.model_extra["image"] == "data:image/png;base64,old"


def test_build_tab_replaces_cross_origin_image_when_url_changes(
    zentool: ModuleType,
) -> None:
    """Cross-origin URL resets should not keep a stale image cache."""
    existing = make_tab(
        zentool,
        entries=[make_entry(zentool, url="https://old.example", title="Old")],
        image="data:image/png;base64,old",
    )
    spec = zentool.ItemTabSpec(name="Example", url="https://new.example")

    built = zentool.build_tab(
        spec,
        existing=existing,
        sync_id="sync-new",
        pinned=True,
        essential=False,
        workspace_uuid="{workspace}",
        folder_id=None,
        user_context_id=0,
        favicon_resolver=lambda _url: "data:image/png;base64,new",
    )

    assert built.model_extra["image"] == "data:image/png;base64,new"


def test_resolve_missing_tab_images_updates_only_missing_nonempty_tabs(
    zentool: ModuleType,
) -> None:
    """Favicon repair should leave existing images and empty sentinels alone."""
    missing = make_tab(
        zentool,
        entries=[make_entry(zentool, url="https://missing.example", title="Missing")],
    )
    existing = make_tab(
        zentool,
        entries=[make_entry(zentool, url="https://existing.example", title="Existing")],
        image="data:image/png;base64,existing",
    )
    empty = zentool.build_placeholder_tab(
        sync_id="empty",
        workspace_uuid="{workspace}",
        folder_id="folder",
        user_context_id=0,
    )
    session = zentool.SessionState(tabs=[missing, existing, empty])
    seen: list[str] = []

    count = zentool.resolve_missing_tab_images(
        session,
        lambda url: seen.append(url) or "data:image/png;base64,missing",
    )

    assert count == 1
    assert seen == ["https://missing.example"]
    assert missing.model_extra["image"] == "data:image/png;base64,missing"
    assert existing.model_extra["image"] == "data:image/png;base64,existing"
    assert empty.model_extra is None or "image" not in empty.model_extra


def test_resolve_missing_tab_images_uses_worker_pool_and_logs(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Favicon repair should dedupe URLs before concurrent resolution."""
    first = make_tab(
        zentool,
        entries=[make_entry(zentool, url="https://same.example", title="First")],
    )
    second = make_tab(
        zentool,
        entries=[make_entry(zentool, url="https://same.example", title="Second")],
    )
    third = make_tab(
        zentool,
        entries=[make_entry(zentool, url="https://other.example", title="Third")],
    )
    session = zentool.SessionState(tabs=[first, second, third])
    seen: list[str] = []
    worker_counts: list[int] = []
    logs: list[str] = []

    class FakeExecutor:
        def __init__(self, *, max_workers: int) -> None:
            worker_counts.append(max_workers)

        def __enter__(self) -> FakeExecutor:
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> bool:
            _ = exc_type, exc, traceback
            return False

        def map(
            self,
            func: Callable[[str], str | None],
            urls: Iterable[str],
        ) -> list[str | None]:
            return [func(url) for url in urls]

    monkeypatch.setattr(
        zentool.concurrent.futures,
        "ThreadPoolExecutor",
        FakeExecutor,
    )

    count = zentool.resolve_missing_tab_images(
        session,
        lambda url: seen.append(url) or f"data:image/png;base64,{url}",
        log=logs.append,
        verbose=True,
    )

    assert count == 3
    assert worker_counts == [2]
    assert seen == ["https://same.example", "https://other.example"]
    assert logs == [
        "Resolving favicons for 3 tab(s) across 2 URL(s)...",
        "Resolved favicon for https://same.example",
        "Resolved favicon for https://other.example",
        "Resolved favicon images for 3 of 3 tab(s).",
    ]
    assert first.model_extra["image"] == "data:image/png;base64,https://same.example"
    assert second.model_extra["image"] == "data:image/png;base64,https://same.example"
    assert third.model_extra["image"] == "data:image/png;base64,https://other.example"


def test_resolve_favicon_data_url_uses_discovered_icons_before_fallback(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Favicon resolution should try page-declared icons, then origin fallback."""
    fetched: list[str] = []
    monkeypatch.setattr(
        zentool,
        "_discover_favicon_urls",
        lambda _url: ["https://example.com/icon.svg"],
    )

    def fake_fetch(url: str) -> str | None:
        fetched.append(url)
        return "data:image/svg+xml;base64,icon" if url.endswith("icon.svg") else None

    monkeypatch.setattr(zentool, "_fetch_image_data_url", fake_fetch)

    assert zentool.resolve_favicon_data_url("https://example.com/path") == (
        "data:image/svg+xml;base64,icon"
    )
    assert fetched == ["https://example.com/icon.svg"]


def test_discover_favicon_urls_scans_large_pages_and_icon_relations(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Discovery should scan HTML beyond image limits and accept icon-like rels."""
    calls: list[tuple[str, int]] = []

    def fake_read_url_bytes(
        url: str,
        *,
        timeout: float,
        max_bytes: int,
    ) -> tuple[bytes, str]:
        del timeout
        calls.append((url, max_bytes))
        return (
            b'<link rel="apple-touch-icon" href="/apple.png">'
            b'<link rel="shortcut icon" href="/favicon.ico">',
            "text/html",
        )

    monkeypatch.setattr(zentool, "_read_url_bytes", fake_read_url_bytes)

    assert zentool._discover_favicon_urls("https://example.com/path") == [
        "https://example.com/apple.png",
        "https://example.com/favicon.ico",
    ]
    assert calls == [("https://example.com/path", zentool.MAX_FAVICON_DISCOVERY_BYTES)]
    assert zentool.MAX_FAVICON_DISCOVERY_BYTES > zentool.MAX_FAVICON_BYTES


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
