"""Focused finishing tests for remaining zentool diff/export/list branches."""

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
    """Load the zentool script for branch-finishing tests."""
    return load_zen_script_module("zentool", "zentool_finishing")


def make_args(**overrides: object) -> SimpleNamespace:
    """Build a minimal namespace for diff/apply helpers."""
    values: dict[str, object] = {
        "profile": None,
        "config": "/tmp/folders.yaml",
        "asset_dir": "/tmp/assets",
        "chrome_source": None,
        "user_js_source": None,
        "state": False,
        "assets": False,
        "yes": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeDiff:
    """Minimal DeepDiff-like test double with configurable truthiness."""

    def __init__(self, text: str, *, truthy: bool = True) -> None:
        self.text = text
        self.truthy = truthy

    def pretty(self) -> str:
        return self.text

    def __bool__(self) -> bool:
        return self.truthy


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
    folder_id: str | None = None,
) -> object:
    """Build one compact session tab record."""
    return zentool.SessionTab(
        entries=[make_entry(zentool, url=url, title=name)],
        index=1,
        pinned=pinned,
        zenWorkspace=workspace,
        zenSyncId=sync_id,
        zenStaticLabel=name,
        groupId=folder_id,
    )


def test_serialization_helpers_cover_remaining_default_skips(
    zentool: ModuleType,
) -> None:
    """Serializer helpers should cover non-default type and default-skipping branches."""
    theme = zentool.ThemeSpec.model_construct(type="image")
    empty_folder = zentool.FolderSpec(name="Collapsed")
    tab = zentool.TabSpec(name="Inbox", url="https://mail.example")
    config = zentool.ZenConfig(
        manage_containers=True,
        containers=[
            zentool.ContainerSpec(
                key="Town",
                icon="briefcase",
                color="orange",
                public=False,
                accessKey="T",
            )
        ],
        workspaces=[zentool.WorkspaceSpec(name="Work")],
    )

    assert zentool._theme_to_dict(theme) == {"type": "image"}
    assert zentool._item_to_dict(empty_folder) == {
        "type": "folder",
        "name": "Collapsed",
    }
    assert zentool._authored_leaf_to_dict(tab) == {"Inbox": "https://mail.example"}
    assert zentool._authored_folder_to_dict(empty_folder) == {"Collapsed": []}
    assert zentool.config_to_dict(config) == {
        "containers": {
            "Town": {
                "name": "Town",
                "icon": "briefcase",
                "color": "orange",
                "public": False,
                "accessKey": "T",
            }
        },
        "workspaces": {"Work": []},
    }
    assert zentool.config_to_dict(
        zentool.ZenConfig(
            manage_containers=True,
            workspaces=[zentool.WorkspaceSpec(name="Work")],
        )
    ) == {"workspaces": {"Work": []}}


def test_cmd_tabs_omits_empty_sections_for_sparse_workspaces(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Tab listing should skip empty essentials, pinned, folder, and tab sections."""
    lines: list[str] = []
    session = zentool.SessionState(
        spaces=[
            zentool.SessionSpace(uuid="ws-1", name="Work"),
            zentool.SessionSpace(uuid="ws-2", name="Play"),
        ],
        folders=[
            zentool.SessionFolder(
                id="folder-1", name="Empty Folder", workspaceId="ws-1"
            )
        ],
    )

    monkeypatch.setattr(zentool, "_stdout", lambda message="": lines.append(message))
    monkeypatch.setattr(
        zentool, "load_session", lambda _profile: (Path("session"), session)
    )

    assert zentool.cmd_tabs(SimpleNamespace(profile="default")) == 0
    assert lines == ["", "Workspace: Work", "", "Workspace: Play"]


def test_asset_diff_lines_returns_empty_for_already_matching_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Asset diff should stay empty when managed symlinks already match sources."""
    profile_dir = tmp_path / "profile"
    profile_chrome_dir = profile_dir / "chrome"
    profile_chrome_dir.mkdir(parents=True)

    chrome_source = tmp_path / "assets" / "chrome"
    chrome_source.mkdir(parents=True)
    source_css = chrome_source / "theme.css"
    source_css.write_text("body {}\n", encoding="utf-8")
    (profile_chrome_dir / "theme.css").symlink_to(source_css.resolve())

    manifest_path = profile_dir / zentool.MANAGED_CHROME_MANIFEST
    manifest_path.write_text("theme.css\n", encoding="utf-8")

    user_js_source = tmp_path / "assets" / "user.js"
    user_js_source.write_text("pref\n", encoding="utf-8")
    (profile_dir / "user.js").symlink_to(user_js_source)
    user_js_manifest_path = profile_dir / zentool.MANAGED_USER_JS_MANIFEST
    user_js_manifest_path.write_text(f"{user_js_source}\n", encoding="utf-8")

    monkeypatch.setattr(
        zentool,
        "_resolve_asset_targets",
        lambda _args: (
            profile_dir,
            chrome_source,
            user_js_source,
            profile_chrome_dir,
            manifest_path,
            user_js_manifest_path,
        ),
    )

    assert zentool._asset_diff_lines(make_args()) == []


def test_cmd_diff_reports_no_changes_for_state_only_scope(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """State-only diff should skip asset inspection and print the no-op message."""
    stdout: list[str] = []

    monkeypatch.setattr(
        zentool, "load_session", lambda _profile: (Path("session"), object())
    )
    monkeypatch.setattr(
        zentool,
        "load_containers",
        lambda _profile: (Path("containers.json"), zentool.ContainerState()),
    )
    monkeypatch.setattr(zentool, "load_config", lambda _path: zentool.ZenConfig())
    monkeypatch.setattr(
        zentool, "diff_session", lambda _session, _config, _containers: None
    )
    monkeypatch.setattr(
        zentool,
        "_asset_diff_lines",
        lambda _args: pytest.fail("asset diff should not run for state-only scope"),
    )
    monkeypatch.setattr(zentool, "_stdout", lambda message="": stdout.append(message))

    assert zentool.cmd_diff(make_args(state=True, assets=False)) == 0
    assert stdout == ["No changes needed."]


def test_cmd_diff_reports_no_changes_for_asset_only_scope(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Asset-only diff should skip state loading and print the no-op message."""
    stdout: list[str] = []

    monkeypatch.setattr(
        zentool,
        "load_session",
        lambda _profile: pytest.fail(
            "state loading should not run for asset-only scope"
        ),
    )
    monkeypatch.setattr(
        zentool,
        "load_config",
        lambda _path: pytest.fail("config loading should not run for asset-only scope"),
    )
    monkeypatch.setattr(zentool, "_asset_diff_lines", lambda _args: [])
    monkeypatch.setattr(zentool, "_stdout", lambda message="": stdout.append(message))

    assert zentool.cmd_diff(make_args(state=False, assets=True)) == 0
    assert stdout == ["No changes needed."]


def test_cmd_apply_reports_no_changes_for_state_only_without_diff(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """State-only apply should short-circuit when the structural diff is empty."""
    stdout: list[str] = []
    session = object()
    desired_state = object()

    monkeypatch.setattr(zentool, "require_zen_closed", lambda _profile: object())
    monkeypatch.setattr(zentool, "_print_runtime_warnings", lambda _runtime: None)
    monkeypatch.setattr(
        zentool, "load_session", lambda _profile: (Path("session"), session)
    )
    monkeypatch.setattr(
        zentool,
        "load_containers",
        lambda _profile: (Path("containers.json"), zentool.ContainerState()),
    )
    monkeypatch.setattr(zentool, "load_config", lambda _path: zentool.ZenConfig())
    monkeypatch.setattr(
        zentool,
        "build_desired_state",
        lambda _session, _config, _plan: desired_state,
    )
    monkeypatch.setattr(
        zentool,
        "snapshot",
        lambda value, _containers=None: {
            "kind": "session" if value is session else "desired"
        },
    )
    monkeypatch.setattr(
        zentool, "DeepDiff", lambda *_args, **_kwargs: FakeDiff("", truthy=False)
    )
    monkeypatch.setattr(
        zentool,
        "_asset_diff_lines",
        lambda _args: pytest.fail("asset diff should not run for state-only scope"),
    )
    monkeypatch.setattr(zentool, "_stdout", lambda message="": stdout.append(message))

    assert zentool.cmd_apply(make_args(state=True, assets=False, yes=False)) == 0
    assert stdout == ["No changes needed."]


def test_cmd_apply_aborts_on_state_only_prompt_interrupt(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """State-only apply should use the scoped prompt and abort on input interruption."""
    stdout: list[str] = []
    prompts: list[str] = []
    session = object()
    desired_state = object()

    monkeypatch.setattr(zentool, "require_zen_closed", lambda _profile: object())
    monkeypatch.setattr(zentool, "_print_runtime_warnings", lambda _runtime: None)
    monkeypatch.setattr(
        zentool, "load_session", lambda _profile: (Path("session"), session)
    )
    monkeypatch.setattr(
        zentool,
        "load_containers",
        lambda _profile: (Path("containers.json"), zentool.ContainerState()),
    )
    monkeypatch.setattr(zentool, "load_config", lambda _path: zentool.ZenConfig())
    monkeypatch.setattr(
        zentool,
        "build_desired_state",
        lambda _session, _config, _plan: desired_state,
    )
    monkeypatch.setattr(
        zentool,
        "snapshot",
        lambda value, _containers=None: {
            "kind": "session" if value is session else "desired"
        },
    )
    monkeypatch.setattr(
        zentool, "DeepDiff", lambda *_args, **_kwargs: FakeDiff("state diff")
    )
    monkeypatch.setattr(zentool, "_stdout", lambda message="": stdout.append(message))

    def raise_eof(prompt: str) -> str:
        prompts.append(prompt)
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)

    assert zentool.cmd_apply(make_args(state=True, assets=False, yes=False)) == 1
    assert prompts == ["Apply state changes? [y/N] "]
    assert stdout == ["state diff", "\nAborted."]


def test_cmd_apply_aborts_on_state_only_prompt_rejection(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """State-only apply should stop before writing when the user declines."""
    stdout: list[str] = []
    session = object()
    desired_state = object()

    monkeypatch.setattr(zentool, "require_zen_closed", lambda _profile: object())
    monkeypatch.setattr(zentool, "_print_runtime_warnings", lambda _runtime: None)
    monkeypatch.setattr(
        zentool, "load_session", lambda _profile: (Path("session"), session)
    )
    monkeypatch.setattr(
        zentool,
        "load_containers",
        lambda _profile: (Path("containers.json"), zentool.ContainerState()),
    )
    monkeypatch.setattr(zentool, "load_config", lambda _path: zentool.ZenConfig())
    monkeypatch.setattr(
        zentool,
        "build_desired_state",
        lambda _session, _config, _plan: desired_state,
    )
    monkeypatch.setattr(
        zentool,
        "snapshot",
        lambda value, _containers=None: {
            "kind": "session" if value is session else "desired"
        },
    )
    monkeypatch.setattr(
        zentool, "DeepDiff", lambda *_args, **_kwargs: FakeDiff("state diff")
    )
    monkeypatch.setattr(zentool, "_stdout", lambda message="": stdout.append(message))
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    monkeypatch.setattr(
        zentool,
        "write_session",
        lambda *_args: pytest.fail("state should not be written after rejection"),
    )

    assert zentool.cmd_apply(make_args(state=True, assets=False, yes=False)) == 1
    assert stdout == ["state diff", "Aborted."]


def test_cmd_apply_applies_state_only_without_running_asset_sync(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """State-only apply should write the session and skip asset application."""
    stdout: list[str] = []
    session = object()
    desired_state = object()
    session_path = Path("/tmp/session.jsonlz4")
    backup_path = Path("/tmp/session.jsonlz4.bak")
    writes: list[tuple[Path, object]] = []

    monkeypatch.setattr(zentool, "require_zen_closed", lambda _profile: object())
    monkeypatch.setattr(zentool, "_print_runtime_warnings", lambda _runtime: None)
    monkeypatch.setattr(
        zentool, "load_session", lambda _profile: (session_path, session)
    )
    monkeypatch.setattr(
        zentool,
        "load_containers",
        lambda _profile: (Path("containers.json"), zentool.ContainerState()),
    )
    monkeypatch.setattr(zentool, "load_config", lambda _path: zentool.ZenConfig())
    monkeypatch.setattr(
        zentool,
        "build_desired_state",
        lambda _session, _config, _plan: desired_state,
    )
    monkeypatch.setattr(
        zentool,
        "snapshot",
        lambda value, _containers=None: {
            "kind": "session" if value is session else "desired"
        },
    )
    monkeypatch.setattr(
        zentool, "DeepDiff", lambda *_args, **_kwargs: FakeDiff("state diff")
    )
    monkeypatch.setattr(zentool, "backup_session", lambda _path: backup_path)
    monkeypatch.setattr(
        zentool, "write_session", lambda path, state: writes.append((path, state))
    )
    monkeypatch.setattr(
        zentool,
        "_apply_assets",
        lambda _args: pytest.fail(
            "asset application should not run for state-only scope"
        ),
    )
    monkeypatch.setattr(zentool, "_stdout", lambda message="": stdout.append(message))

    assert zentool.cmd_apply(make_args(state=True, assets=False, yes=True)) == 0
    assert writes == [(session_path, desired_state)]
    assert stdout == [
        "state diff",
        f"Backup: {backup_path.name}",
        "Applied state successfully.",
    ]
