"""Regression tests for the zen-folders script."""

from __future__ import annotations

import argparse
import getpass
import importlib.machinery
import importlib.util
import sys
from typing import TYPE_CHECKING

import pytest
import yaml

from lib.update.paths import REPO_ROOT

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType


def _resolve_zen_folders_path() -> Path:
    preferred = REPO_ROOT / f"home/{getpass.getuser()}/bin/zen-folders"
    if preferred.is_file():
        return preferred

    candidates = sorted((REPO_ROOT / "home").glob("*/bin/zen-folders"))
    if len(candidates) == 1:
        return candidates[0]

    if candidates:
        candidate_paths = ", ".join(
            str(path.relative_to(REPO_ROOT)) for path in candidates
        )
        msg = (
            f"Unable to resolve zen-folders for user {getpass.getuser()!r}; "
            f"candidates: {candidate_paths}"
        )
        raise RuntimeError(msg)

    msg = "Unable to locate zen-folders under home/*/bin/zen-folders"
    raise RuntimeError(msg)


ZEN_FOLDERS_PATH = _resolve_zen_folders_path()


def _load_zen_folders_module() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader(
        "zen_folders_script",
        str(ZEN_FOLDERS_PATH),
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None or spec.loader is None:
        msg = "failed to load zen-folders module spec"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def zen_folders() -> ModuleType:
    """Load the zen-folders script as a module for direct function testing."""
    return _load_zen_folders_module()


def _base_session() -> dict:
    return {
        "tabs": [],
        "groups": [],
        "folders": [],
        "spaces": [{"name": "Work", "uuid": "ws1"}],
    }


def test_load_yaml_rejects_duplicate_keys(
    tmp_path: Path,
    zen_folders: ModuleType,
) -> None:
    """Duplicate keys in YAML should fail fast instead of silently overriding."""
    config_path = tmp_path / "zen-folders.yaml"
    config_path.write_text(
        "Work:\n"
        "  AI:\n"
        "    OpenAI: platform.openai.com\n"
        "  AI:\n"
        "    Anthropic: platform.claude.com\n",
        encoding="utf-8",
    )

    with pytest.raises(zen_folders.ZenFoldersError, match="duplicate key"):
        zen_folders.load_yaml(config_path)


def test_tab_entry_field_handles_non_numeric_index(zen_folders: ModuleType) -> None:
    """Non-integer tab index values should fall back to the latest entry."""
    tab = {
        "entries": [
            {"url": "https://example.com/first", "title": "First"},
            {"url": "https://example.com/second", "title": "Second"},
        ],
        "index": "2",
    }

    assert zen_folders.tab_url(tab) == "https://example.com/second"
    assert zen_folders.tab_title(tab) == "Second"


def test_compute_plan_rejects_duplicate_session_folder_names(
    zen_folders: ModuleType,
) -> None:
    """Ambiguous same-name folders in one workspace should be rejected."""
    session = _base_session()
    session["folders"] = [
        {"id": "f1", "name": "AI", "workspaceId": "ws1"},
        {"id": "f2", "name": "ai", "workspaceId": "ws1"},
    ]
    session["groups"] = [{"id": "f1"}, {"id": "f2"}]

    with pytest.raises(zen_folders.ZenFoldersError, match="duplicate folder names"):
        zen_folders.compute_plan(session, [], "ws1")


def test_apply_plan_handles_non_dict_rows_in_tabs_and_groups(
    zen_folders: ModuleType,
) -> None:
    """Malformed non-dict rows should not crash apply_plan reordering logic."""
    session = _base_session()
    session["groups"] = ["corrupt-group", {"id": "f1", "name": "AI"}]
    session["folders"] = [
        {
            "id": "f1",
            "name": "AI",
            "workspaceId": "ws1",
            "prevSiblingInfo": {"type": "start", "id": None},
            "emptyTabIds": [],
        },
    ]
    session["tabs"] = [
        "corrupt-tab",
        {
            "pinned": True,
            "zenWorkspace": "ws1",
            "zenSyncId": "t1",
            "groupId": "f1",
            "zenIsEmpty": False,
            "entries": [{"url": "https://platform.openai.com", "title": "OpenAI"}],
            "index": 1,
        },
        {
            "pinned": False,
            "entries": [{"url": "https://example.com", "title": "Example"}],
            "index": 1,
        },
    ]

    config_folders = [
        {"name": "AI", "tabs": [{"title": "OpenAI", "url": "openai.com"}]},
    ]
    plan = zen_folders.compute_plan(session, config_folders, "ws1")

    zen_folders.apply_plan(session, config_folders, "ws1", plan)

    assert any(not isinstance(group, dict) for group in session["groups"])
    assert any(not isinstance(tab, dict) for tab in session["tabs"])


def test_cmd_dump_disambiguates_duplicate_titles(
    tmp_path: Path,
    zen_folders: ModuleType,
) -> None:
    """Dump output should keep both tabs when titles collide."""
    session_path = tmp_path / "zen-sessions.jsonlz4"
    output_path = tmp_path / "dump.yaml"

    session = _base_session()
    session["groups"] = [{"id": "f1", "name": "AI"}]
    session["folders"] = [
        {
            "id": "f1",
            "name": "AI",
            "workspaceId": "ws1",
            "prevSiblingInfo": {"type": "start", "id": None},
            "emptyTabIds": [],
        },
    ]
    session["tabs"] = [
        {
            "pinned": True,
            "zenWorkspace": "ws1",
            "groupId": "f1",
            "zenIsEmpty": False,
            "entries": [{"url": "https://a.example.com", "title": "Dashboard"}],
            "index": 1,
        },
        {
            "pinned": True,
            "zenWorkspace": "ws1",
            "groupId": "f1",
            "zenIsEmpty": False,
            "entries": [{"url": "https://b.example.com", "title": "Dashboard"}],
            "index": 1,
        },
    ]

    zen_folders.write_session(session_path, session)
    args = argparse.Namespace(
        profile=str(session_path),
        workspace="Work",
        output=str(output_path),
    )

    assert zen_folders.cmd_dump(args) == 0

    dumped = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    tabs = dumped["Work"]["AI"]
    assert tabs["Dashboard"] == "https://a.example.com"
    assert tabs["Dashboard (2)"] == "https://b.example.com"


def test_cmd_dump_includes_non_default_workspace_metadata(
    tmp_path: Path,
    zen_folders: ModuleType,
) -> None:
    """Dump output should include richer workspace metadata when present."""
    session_path = tmp_path / "zen-sessions.jsonlz4"
    output_path = tmp_path / "dump.yaml"

    session = _base_session()
    session["spaces"] = [
        {
            "name": "Work",
            "uuid": "ws1",
            "icon": "🏢",
            "containerTabId": 6,
            "hasCollapsedPinnedTabs": True,
            "theme": {
                "type": "gradient",
                "gradientColors": ["#8caaee", "#99d1db"],
                "opacity": 0.7,
                "texture": 1,
            },
        }
    ]
    session["groups"] = [{"id": "f1", "name": "AI"}]
    session["folders"] = [
        {
            "id": "f1",
            "name": "AI",
            "workspaceId": "ws1",
            "prevSiblingInfo": {"type": "start", "id": None},
            "emptyTabIds": [],
        },
    ]
    session["tabs"] = [
        {
            "pinned": True,
            "zenWorkspace": "ws1",
            "groupId": "f1",
            "zenIsEmpty": False,
            "entries": [{"url": "https://platform.openai.com", "title": "OpenAI"}],
            "index": 1,
        },
    ]

    zen_folders.write_session(session_path, session)
    args = argparse.Namespace(
        profile=str(session_path),
        workspace="Work",
        output=str(output_path),
    )

    assert zen_folders.cmd_dump(args) == 0

    dumped = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    meta = dumped["Work"]["__workspace__"]
    assert meta == {
        "icon": "🏢",
        "containerTabId": 6,
        "hasCollapsedPinnedTabs": True,
        "theme": {
            "gradientColors": ["#8caaee", "#99d1db"],
            "opacity": 0.7,
            "texture": 1,
        },
    }


def test_read_session_rejects_oversized_payload_header(
    tmp_path: Path,
    zen_folders: ModuleType,
) -> None:
    """Reject oversized uncompressed-size headers before decompression."""
    oversized = zen_folders.MAX_SESSION_UNCOMPRESSED_BYTES + 1
    path = tmp_path / "bad-session.jsonlz4"
    path.write_bytes(b"mozLz40\0" + oversized.to_bytes(4, "little"))

    with pytest.raises(zen_folders.SessionFormatError, match="too large"):
        zen_folders.read_session(path)


def test_resolve_profile_dir_supports_human_profile_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zen_folders: ModuleType,
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

    monkeypatch.setattr(zen_folders, "ZEN_APPLICATION_SUPPORT", app_support)
    monkeypatch.setattr(zen_folders, "ZEN_PROFILES", profiles_dir)
    monkeypatch.setattr(zen_folders, "PROFILES_INI", profiles_ini)

    assert zen_folders.resolve_profile_dir("default (TWILIGHT)") == twilight_dir


def test_resolve_profile_dir_auto_detects_install_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zen_folders: ModuleType,
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

    monkeypatch.setattr(zen_folders, "ZEN_APPLICATION_SUPPORT", app_support)
    monkeypatch.setattr(zen_folders, "ZEN_PROFILES", profiles_dir)
    monkeypatch.setattr(zen_folders, "PROFILES_INI", profiles_ini)

    assert zen_folders.resolve_profile_dir(None) == twilight_dir


def test_zen_is_running_ignores_stale_lock_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zen_folders: ModuleType,
) -> None:
    """A leftover lock file should not block reconciliation when not held."""
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    (profile_dir / ".parentlock").write_text("", encoding="utf-8")

    monkeypatch.setattr(zen_folders, "zen_profile_dir", lambda _profile: profile_dir)
    monkeypatch.setattr(zen_folders, "_lock_probe_state", lambda _path: False)

    assert zen_folders.zen_profile_lock_state(None) is False
    assert zen_folders.zen_is_running(None) is False


def test_zen_is_running_uses_process_fallback_when_lock_probe_is_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zen_folders: ModuleType,
) -> None:
    """If the lock cannot be probed, fall back to process inspection."""
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    (profile_dir / ".parentlock").write_text("", encoding="utf-8")

    monkeypatch.setattr(zen_folders, "zen_profile_dir", lambda _profile: profile_dir)
    monkeypatch.setattr(zen_folders, "_lock_probe_state", lambda _path: None)
    monkeypatch.setattr(zen_folders, "zen_process_is_running", lambda: False)
    assert zen_folders.zen_is_running(None) is False

    monkeypatch.setattr(zen_folders, "zen_process_is_running", lambda: True)
    assert zen_folders.zen_is_running(None) is True


def test_require_zen_closed_returns_runtime_warning_when_lock_probe_is_uncertain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zen_folders: ModuleType,
) -> None:
    """Successful fallback should preserve runtime-detection warnings for apply."""
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    (profile_dir / ".parentlock").write_text("", encoding="utf-8")

    monkeypatch.setattr(zen_folders, "zen_profile_dir", lambda _profile: profile_dir)
    monkeypatch.setattr(
        zen_folders,
        "session_file",
        lambda _profile: tmp_path / "zen-sessions.jsonlz4",
    )
    monkeypatch.setattr(zen_folders, "_lock_probe_state", lambda _path: None)
    monkeypatch.setattr(zen_folders, "zen_process_is_running", lambda: False)

    state = zen_folders.require_zen_closed(None)

    assert state.running is False
    assert any(
        "falling back to process inspection" in warning for warning in state.warnings
    )


def test_require_zen_closed_reports_detection_details_when_runtime_is_uncertain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zen_folders: ModuleType,
) -> None:
    """Failed runtime probes should explain why apply refuses to continue."""
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    (profile_dir / ".parentlock").write_text("", encoding="utf-8")

    monkeypatch.setattr(zen_folders, "zen_profile_dir", lambda _profile: profile_dir)
    monkeypatch.setattr(
        zen_folders,
        "session_file",
        lambda _profile: tmp_path / "zen-sessions.jsonlz4",
    )
    monkeypatch.setattr(zen_folders, "_lock_probe_state", lambda _path: None)
    monkeypatch.setattr(zen_folders, "zen_process_is_running", lambda: None)

    with pytest.raises(zen_folders.ZenFoldersError, match="Detection details"):
        zen_folders.require_zen_closed(None)


def test_parse_config_supports_workspace_metadata(zen_folders: ModuleType) -> None:
    """Workspace metadata should parse cleanly without becoming a folder."""
    raw = {
        "Work": {
            "__workspace__": {
                "icon": "🏢",
                "containerTabId": 6,
                "hasCollapsedPinnedTabs": True,
                "theme": {
                    "gradientColors": ["#8caaee", "#99d1db"],
                    "opacity": 0.7,
                    "texture": 1,
                },
            },
            "AI": {"OpenAI": "platform.openai.com"},
        }
    }

    workspace_spec, folders = zen_folders.parse_config(raw)

    assert workspace_spec == zen_folders.WorkspaceSpec(
        name="Work",
        icon="🏢",
        container_tab_id=6,
        has_collapsed_pinned_tabs=True,
        theme=zen_folders.WorkspaceThemeSpec(
            gradient_colors=("#8caaee", "#99d1db"),
            opacity=0.7,
            texture=1,
        ),
    )
    assert folders == [
        {
            "name": "AI",
            "tabs": [{"title": "OpenAI", "url": "platform.openai.com"}],
        }
    ]


def test_plan_workspace_metadata_updates_warns_on_container_mismatch(
    zen_folders: ModuleType,
) -> None:
    """Existing workspace metadata should diff cleanly and warn on container drift."""
    session = _base_session()
    session["spaces"] = [
        {
            "name": "Work",
            "uuid": "ws1",
            "icon": "🏢",
            "containerTabId": 6,
            "hasCollapsedPinnedTabs": False,
            "theme": zen_folders.WorkspaceThemeSpec().to_session_dict(),
        }
    ]
    spec = zen_folders.WorkspaceSpec(
        name="Work",
        icon="💼",
        container_tab_id=7,
        has_collapsed_pinned_tabs=True,
        theme=zen_folders.WorkspaceThemeSpec(
            gradient_colors=("#8caaee", "#99d1db"),
            opacity=0.7,
            texture=1,
        ),
    )

    plan = zen_folders.Plan()
    zen_folders.plan_workspace_metadata_updates(plan, session, "ws1", spec)

    assert "icon: 🏢 -> 💼" in plan.workspace_updates
    assert "hasCollapsedPinnedTabs: False -> True" in plan.workspace_updates
    assert any(item.startswith("theme: ") for item in plan.workspace_updates)
    assert any("does not rewrite container IDs" in warning for warning in plan.warnings)


def test_apply_workspace_metadata_updates_safe_fields(
    zen_folders: ModuleType,
) -> None:
    """Applying workspace metadata should touch safe fields but not container IDs."""
    session = _base_session()
    session["spaces"] = [
        {
            "name": "Work",
            "uuid": "ws1",
            "icon": "🏢",
            "containerTabId": 6,
            "hasCollapsedPinnedTabs": False,
            "theme": zen_folders.WorkspaceThemeSpec().to_session_dict(),
        }
    ]
    spec = zen_folders.WorkspaceSpec(
        name="Work",
        icon="💼",
        container_tab_id=9,
        has_collapsed_pinned_tabs=True,
        theme=zen_folders.WorkspaceThemeSpec(
            gradient_colors=("#8caaee",),
            opacity=0.7,
            texture=1,
        ),
    )

    zen_folders.apply_workspace_metadata(session, "ws1", spec)

    space = session["spaces"][0]
    assert space["icon"] == "💼"
    assert space["containerTabId"] == 6
    assert space["hasCollapsedPinnedTabs"] is True
    assert space["theme"] == {
        "type": "gradient",
        "gradientColors": ["#8caaee"],
        "opacity": 0.7,
        "texture": 1,
    }


def test_reconcile_workspace_applies_metadata_before_folder_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
    zen_folders: ModuleType,
) -> None:
    """Workspace metadata should be applied before tab and folder mutations."""
    calls: list[str] = []

    monkeypatch.setattr(
        zen_folders,
        "apply_workspace_metadata",
        lambda *_args: calls.append("metadata"),
    )
    monkeypatch.setattr(zen_folders, "apply_plan", lambda *_args: calls.append("plan"))

    zen_folders.reconcile_workspace(
        session={},
        config_folders=[],
        workspace_uuid="ws1",
        workspace_spec=zen_folders.WorkspaceSpec(name="Work"),
        plan=zen_folders.Plan(),
    )

    assert calls == ["metadata", "plan"]


def test_ensure_workspace_creation_preserves_workspace_container_for_new_tabs(
    zen_folders: ModuleType,
) -> None:
    """Missing workspaces should be creatable with container metadata intact."""
    session = {"tabs": [], "groups": [], "folders": [], "spaces": []}
    workspace_spec = zen_folders.WorkspaceSpec(
        name="Work",
        icon="🏢",
        container_tab_id=6,
        has_collapsed_pinned_tabs=True,
        theme=zen_folders.WorkspaceThemeSpec(
            gradient_colors=("#8caaee", "#99d1db"),
            opacity=0.7,
            texture=1,
        ),
    )

    workspace_uuid, created = zen_folders.ensure_workspace(session, workspace_spec)

    assert created == workspace_spec
    assert session["spaces"][0]["name"] == "Work"
    assert session["spaces"][0]["icon"] == "🏢"
    assert session["spaces"][0]["containerTabId"] == 6
    assert session["spaces"][0]["hasCollapsedPinnedTabs"] is True
    assert session["spaces"][0]["theme"] == {
        "type": "gradient",
        "gradientColors": ["#8caaee", "#99d1db"],
        "opacity": 0.7,
        "texture": 1,
    }

    existing_ids: set[str] = set()
    zen_folders._create_folder(session, workspace_uuid, "AI", existing_ids)

    placeholder = next(tab for tab in session["tabs"] if tab.get("zenIsEmpty"))
    assert placeholder["userContextId"] == 6

    folder_id = session["folders"][0]["id"]
    context = zen_folders._AssignTabContext(
        session=session,
        all_pinned=zen_folders.pinned_tabs(session, workspace_uuid),
        workspace_uuid=workspace_uuid,
        existing_ids=existing_ids,
        claimed_tab_indices=set(),
    )
    created_tabs = zen_folders._assign_tabs_for_folder(
        context,
        folder_id,
        [{"title": "OpenAI", "url": "platform.openai.com"}],
    )

    assert created_tabs[0]["userContextId"] == 6
