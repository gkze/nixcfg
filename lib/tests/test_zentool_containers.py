"""Focused tests for declarative zentool container management."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from lib.tests._zen_tooling import load_zen_script_module

if TYPE_CHECKING:
    from types import ModuleType


@pytest.fixture(scope="module")
def zentool() -> ModuleType:
    """Load the zentool script for container-helper testing."""
    return load_zen_script_module("zentool", "zentool_container_helpers")


def test_load_config_accepts_explicit_container_root(
    tmp_path: Path,
    zentool: ModuleType,
) -> None:
    """The explicit root schema should declare containers and workspaces together."""
    config_path = tmp_path / "folders.yaml"
    config_path.write_text(
        """
containers:
  Town:
    name: Town
    icon: briefcase
    color: orange
  Personal:
    icon: fingerprint
    color: blue
workspaces:
  Work:
    icon: "🏢"
    container: Town
    tree:
      - Gmail:
          url: https://mail.google.com/mail/u/0/#inbox
          role: essential
  Home:
    icon: "🏡"
    container: Personal
    tree: []
""",
        encoding="utf-8",
    )

    config = zentool.load_config(config_path)

    assert config.manage_containers is True
    assert [container.key for container in config.containers] == ["Town", "Personal"]
    assert config.containers[1].name == "Personal"
    assert [
        (workspace.name, workspace.container) for workspace in config.workspaces
    ] == [
        ("Work", "Town"),
        ("Home", "Personal"),
    ]


def test_load_config_rejects_unknown_managed_container_ref(
    tmp_path: Path,
    zentool: ModuleType,
) -> None:
    """Managed workspace container refs should point at declared containers."""
    config_path = tmp_path / "folders.yaml"
    config_path.write_text(
        """
containers:
  Town: {}
workspaces:
  Home:
    container: Personal
    tree: []
""",
        encoding="utf-8",
    )

    with pytest.raises(zentool.ZenFoldersError, match="unknown container"):
        zentool.load_config(config_path)


def test_container_and_workspace_coercion_handles_optional_root_shapes(
    zentool: ModuleType,
) -> None:
    """Authored container/workspace root coercion should validate edge shapes."""
    assert zentool._coerce_container_spec("Town", None) == zentool.ContainerSpec(
        key="Town"
    )
    assert zentool._coerce_container_spec("Town", "Team") == zentool.ContainerSpec(
        key="Town",
        name="Team",
    )
    assert zentool._coerce_container_specs(None) == []
    assert zentool._coerce_workspace_specs(None) == []

    with pytest.raises(zentool.ZenFoldersError, match="mapping, string, or null"):
        zentool._coerce_container_spec("Town", 1)

    with pytest.raises(zentool.ZenFoldersError, match="containers must be a mapping"):
        zentool._coerce_container_specs([])

    with pytest.raises(zentool.ZenFoldersError, match="workspaces must be a mapping"):
        zentool._coerce_workspace_specs([])

    with pytest.raises(zentool.ValidationError, match="workspace containers"):
        zentool._coerce_workspace_specs({"Work": {"container": "   ", "tree": []}})


def test_build_desired_containers_reifies_existing_workspace_context(
    zentool: ModuleType,
) -> None:
    """Missing identities should reuse existing workspace context IDs when possible."""
    containers = zentool.ContainerState.model_validate({
        "version": 5,
        "lastUserContextId": 5,
        "identities": [
            {
                "icon": "fingerprint",
                "color": "blue",
                "l10nId": "user-context-personal",
                "public": True,
                "userContextId": 1,
            },
            {
                "public": False,
                "icon": "",
                "color": "",
                "name": "userContextIdInternal.thumbnail",
                "accessKey": "",
                "userContextId": 5,
            },
        ],
    })
    session = zentool.SessionState(
        spaces=[
            zentool.SessionSpace(uuid="ws-work", name="Work", containerTabId=6),
            zentool.SessionSpace(uuid="ws-home", name="Home", containerTabId=0),
        ],
        tabs=[
            zentool.SessionTab(
                entries=[
                    zentool.SessionEntry(
                        url="https://mail.google.com/mail/u/0/#inbox",
                        title="Gmail",
                    )
                ],
                index=1,
                pinned=True,
                zenWorkspace="ws-home",
                zenSyncId="home-gmail",
                zenEssential=True,
                userContextId=0,
            )
        ],
    )
    config = zentool.ZenConfig(
        manage_containers=True,
        containers=[
            zentool.ContainerSpec(key="Town", icon="briefcase", color="orange"),
            zentool.ContainerSpec(key="Personal", icon="fingerprint", color="blue"),
        ],
        workspaces=[
            zentool.WorkspaceSpec(
                name="Work",
                container="Town",
                essentials=[
                    zentool.TabSpec(
                        name="Gmail",
                        url="https://mail.google.com/mail/u/0/#inbox",
                    )
                ],
            ),
            zentool.WorkspaceSpec(
                name="Home",
                container="Personal",
                essentials=[
                    zentool.TabSpec(
                        name="Gmail",
                        url="https://mail.google.com/mail/u/0/#inbox",
                    )
                ],
            ),
        ],
    )

    plan = zentool.build_desired_containers(containers, config, session)
    desired = zentool.build_desired_state(session, config, plan)

    assert plan.context_ids_by_key == {"town": 6, "personal": 1}
    assert [
        (identity.name, identity.user_context_id)
        for identity in plan.state.identities
        if identity.name == "Town"
    ] == [("Town", 6)]
    assert plan.state.last_user_context_id == 6
    assert [(space.name, space.container_tab_id) for space in desired.spaces] == [
        ("Work", 6),
        ("Home", 1),
    ]
    assert [tab.user_context_id for tab in desired.tabs] == [6, 1]


def test_build_desired_containers_prunes_unmanaged_custom_identities(
    zentool: ModuleType,
) -> None:
    """Explicit container management should remove custom public identities."""
    containers = zentool.ContainerState.model_validate({
        "lastUserContextId": 9,
        "identities": [
            {
                "icon": "briefcase",
                "color": "orange",
                "name": "Town",
                "public": True,
                "userContextId": 6,
            },
            {
                "icon": "circle",
                "color": "purple",
                "name": "Old",
                "public": True,
                "userContextId": 8,
            },
        ],
    })
    config = zentool.ZenConfig(
        manage_containers=True,
        containers=[
            zentool.ContainerSpec(key="Town", icon="briefcase", color="orange")
        ],
    )

    plan = zentool.build_desired_containers(
        containers,
        config,
        zentool.SessionState(),
    )

    assert [identity.name for identity in plan.state.identities] == ["Town"]
    assert plan.removed_context_ids == {8}


def test_managed_workspace_without_container_drops_pruned_existing_context(
    zentool: ModuleType,
) -> None:
    """Managed configs should not preserve references to undeclared containers."""
    containers = zentool.ContainerState.model_validate({
        "lastUserContextId": 8,
        "identities": [
            {
                "icon": "briefcase",
                "color": "orange",
                "name": "Town",
                "public": True,
                "userContextId": 6,
            },
            {
                "icon": "circle",
                "color": "purple",
                "name": "Old",
                "public": True,
                "userContextId": 8,
            },
        ],
    })
    session = zentool.SessionState(
        spaces=[zentool.SessionSpace(uuid="ws-home", name="Home", containerTabId=8)],
        tabs=[
            zentool.SessionTab(
                entries=[
                    zentool.SessionEntry(url="https://example.com", title="Example")
                ],
                pinned=True,
                zenSyncId="home-example",
                zenWorkspace="ws-home",
                zenEssential=True,
                userContextId=8,
            )
        ],
    )
    config = zentool.ZenConfig(
        manage_containers=True,
        containers=[
            zentool.ContainerSpec(key="Town", icon="briefcase", color="orange")
        ],
        workspaces=[
            zentool.WorkspaceSpec(
                name="Home",
                essentials=[
                    zentool.TabSpec(
                        name="Example",
                        url="https://example.com",
                    )
                ],
            )
        ],
    )

    plan = zentool.build_desired_containers(containers, config, session)
    desired = zentool.build_desired_state(session, config, plan)

    assert plan.removed_context_ids == {8}
    assert [(space.name, space.container_tab_id) for space in desired.spaces] == [
        ("Home", 0)
    ]
    assert [tab.user_context_id for tab in desired.tabs] == [0]


def test_prune_cookie_contexts_deletes_only_removed_context_rows(
    tmp_path: Path,
    zentool: ModuleType,
) -> None:
    """Cookie pruning should match exact originAttributes context tokens."""
    cookies = tmp_path / "cookies.sqlite"
    with sqlite3.connect(cookies) as connection:
        connection.execute(
            "CREATE TABLE moz_cookies (id INTEGER PRIMARY KEY, originAttributes TEXT)"
        )
        connection.executemany(
            "INSERT INTO moz_cookies(originAttributes) VALUES (?)",
            [
                ("",),
                ("^userContextId=8",),
                ("^userContextId=8&partitionKey=%28https%2Cexample.com%29",),
                ("^userContextId=80",),
                ("^partitionKey=x&userContextId=8",),
            ],
        )

    assert zentool.prune_cookie_contexts(cookies, {8}) == 3

    with sqlite3.connect(cookies) as connection:
        remaining = [
            row[0]
            for row in connection.execute(
                "SELECT originAttributes FROM moz_cookies ORDER BY id"
            )
        ]
    assert remaining == ["", "^userContextId=80"]


def test_prune_cookie_contexts_handles_noop_and_sqlite_failures(
    tmp_path: Path,
    zentool: ModuleType,
) -> None:
    """Cookie pruning should no-op cheaply and wrap SQLite errors."""
    missing = tmp_path / "missing.sqlite"
    assert zentool.prune_cookie_contexts(missing, {8}) == 0

    cookies = tmp_path / "cookies.sqlite"
    cookies.write_text("not sqlite\n", encoding="utf-8")

    with pytest.raises(zentool.ZenFoldersError, match="Unable to prune cookies"):
        zentool.prune_cookie_contexts(cookies, {8})


def test_container_id_helpers_cover_fresh_and_default_resolution(
    zentool: ModuleType,
) -> None:
    """Container ID helpers should cover built-ins, defaults, and fresh IDs."""
    builtin = zentool.ContainerIdentity(userContextId=1, public=True)
    assert zentool.builtin_container_name(builtin) is None
    assert zentool.identity_display_name(builtin) == "1"

    assert zentool._next_context_id({2}, 1) == 3
    special_id = next(iter(zentool.SPECIAL_CONTEXT_IDS))
    assert zentool._next_context_id(set(), special_id - 1) == special_id + 1

    used_ids = {1}
    context_id = zentool._resolve_custom_context_id(
        zentool.ContainerSpec(key="Town"),
        custom_by_key={},
        preferred_ids={},
        matched_existing_ids=set(),
        used_ids=used_ids,
        last_user_context_id=1,
    )
    assert context_id == 2
    assert used_ids == {1, 2}

    workspace = zentool.WorkspaceSpec(name="Work")
    existing_space = zentool.SessionSpace(
        uuid="ws-work",
        name="Work",
        containerTabId=6,
    )
    assert (
        zentool.workspace_container_id(
            workspace,
            None,
            existing_space,
            manage_containers=True,
        )
        == 0
    )
    default_workspace = zentool.WorkspaceSpec(name="Work", container="default")
    assert (
        zentool.workspace_container_id(
            default_workspace,
            None,
            existing_space,
            manage_containers=False,
        )
        == 0
    )
    with pytest.raises(zentool.ZenFoldersError, match="require a container plan"):
        zentool.workspace_container_id(
            zentool.WorkspaceSpec(name="Work", container="Town"),
            None,
            existing_space,
            manage_containers=True,
        )
    assert (
        zentool.workspace_container_id(
            zentool.WorkspaceSpec(name="Work", container="Town"),
            None,
            existing_space,
            manage_containers=False,
        )
        == 6
    )


def test_read_containers_wraps_payload_validation_failures(
    tmp_path: Path,
    zentool: ModuleType,
) -> None:
    """Invalid ``containers.json`` shapes should become user-facing errors."""
    containers_path = tmp_path / zentool.CONTAINERS_FILENAME
    containers_path.write_text(
        """
{
  "identities": [
    {"userContextId": 6},
    {"userContextId": 6}
  ]
}
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(zentool.ZenFoldersError, match="Invalid containers file"):
        zentool.read_containers(containers_path)


def test_container_file_helpers_cover_paths_and_io_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Container path helpers should validate file shape and wrap filesystem errors."""
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    monkeypatch.setattr(zentool, "zen_profile_dir", lambda _profile: profile_dir)

    assert zentool.cookies_file("Default") == profile_dir / zentool.COOKIES_FILENAME

    containers_dir = profile_dir / zentool.CONTAINERS_FILENAME
    containers_dir.mkdir()
    with pytest.raises(zentool.ZenFoldersError, match="Container path is not a file"):
        zentool.read_containers(containers_dir)

    containers_path = tmp_path / "containers.json"
    containers_path.write_text("[]\n", encoding="utf-8")
    with pytest.raises(zentool.ZenFoldersError, match="payload root must be an object"):
        zentool.read_containers(containers_path)

    containers_path.write_text("{bad json\n", encoding="utf-8")
    with pytest.raises(zentool.ZenFoldersError, match="Unable to read containers file"):
        zentool.read_containers(containers_path)

    output_path = tmp_path / "out-containers.json"
    zentool.write_containers(
        output_path,
        zentool.ContainerState(
            identities=[zentool.ContainerIdentity(userContextId=6, name="Town")]
        ),
    )
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["identities"][0]["name"] == "Town"

    def raise_oserror(self: Path, text: str, *, encoding: str) -> int:
        _ = (self, text, encoding)
        raise OSError("permission denied")

    monkeypatch.setattr(type(output_path), "write_text", raise_oserror)
    with pytest.raises(
        zentool.ZenFoldersError, match="Unable to write containers file"
    ):
        zentool.write_containers(output_path, zentool.ContainerState())


def test_session_check_reports_unknown_workspace_and_tab_containers(
    zentool: ModuleType,
) -> None:
    """Structural checks should catch session refs to missing identities."""
    session = zentool.SessionState(
        spaces=[zentool.SessionSpace(uuid="ws-home", name="Home", containerTabId=7)],
        tabs=[
            zentool.SessionTab(
                entries=[zentool.SessionEntry(url="https://example.com", title="")],
                zenSyncId="tab",
                zenWorkspace="ws-home",
                userContextId=7,
            )
        ],
    )
    containers = zentool.ContainerState(identities=[])

    assert zentool.session_check(session, containers) == [
        "Workspace 'Home' references unknown container 7",
        "Tab 'https://example.com' references unknown container 7",
    ]


def test_apply_state_plan_writes_containers_and_prunes_removed_cookie_contexts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Applying a managed container change should back up and prune removed jars."""
    session_path = tmp_path / zentool.SESSION_FILENAME
    containers_path = tmp_path / zentool.CONTAINERS_FILENAME
    cookies_path = tmp_path / zentool.COOKIES_FILENAME
    session_path.write_bytes(b"session")
    containers_path.write_text("{}\n", encoding="utf-8")
    with sqlite3.connect(cookies_path) as connection:
        connection.execute(
            "CREATE TABLE moz_cookies (id INTEGER PRIMARY KEY, originAttributes TEXT)"
        )
        connection.executemany(
            "INSERT INTO moz_cookies(originAttributes) VALUES (?)",
            [("^userContextId=8",), ("",)],
        )

    stdout: list[str] = []
    container_writes: list[tuple[Path, object]] = []
    session_writes: list[tuple[Path, object]] = []
    monkeypatch.setattr(zentool, "_stdout", stdout.append)
    monkeypatch.setattr(
        zentool,
        "backup_session",
        lambda path: path.with_name(f"{path.name}.bak"),
    )
    monkeypatch.setattr(
        zentool,
        "backup_file",
        lambda path: path.with_name(f"{path.name}.bak"),
    )
    monkeypatch.setattr(zentool, "cookies_file", lambda _profile: cookies_path)
    monkeypatch.setattr(
        zentool,
        "write_containers",
        lambda path, state: container_writes.append((path, state)),
    )
    monkeypatch.setattr(
        zentool,
        "write_session",
        lambda path, state: session_writes.append((path, state)),
    )
    desired_state = zentool.SessionState()
    desired_containers = zentool.ContainerState()
    plan = zentool.StateApplyPlan(
        session_path=session_path,
        containers_path=containers_path,
        desired_state=desired_state,
        desired_containers=desired_containers,
        diff_text="state diff",
        containers_changed=True,
        removed_context_ids={8},
    )

    zentool.apply_state_plan(SimpleNamespace(profile="Default"), plan)

    assert container_writes == [(containers_path, desired_containers)]
    assert session_writes == [(session_path, desired_state)]
    assert stdout == [
        f"Backup: {session_path.name}.bak",
        f"Backup: {containers_path.name}.bak",
        f"Backup: {cookies_path.name}.bak",
        "Pruned 1 cookie(s) for removed containers.",
        "Applied state successfully.",
    ]
    with sqlite3.connect(cookies_path) as connection:
        remaining = [
            row[0]
            for row in connection.execute(
                "SELECT originAttributes FROM moz_cookies ORDER BY id"
            )
        ]
    assert remaining == [""]


def test_apply_state_plan_skips_missing_cookie_backup_and_zero_prune_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Apply should not report cookie backups/pruning when no cookie rows change."""
    session_path = tmp_path / zentool.SESSION_FILENAME
    containers_path = tmp_path / zentool.CONTAINERS_FILENAME
    cookies_path = tmp_path / zentool.COOKIES_FILENAME
    session_path.write_bytes(b"session")
    containers_path.write_text("{}\n", encoding="utf-8")

    stdout: list[str] = []
    backups: list[Path] = []
    monkeypatch.setattr(zentool, "_stdout", stdout.append)
    monkeypatch.setattr(
        zentool,
        "backup_session",
        lambda path: path.with_name(f"{path.name}.bak"),
    )

    def fake_backup_file(path: Path) -> Path:
        backups.append(path)
        return path.with_name(f"{path.name}.bak")

    monkeypatch.setattr(zentool, "backup_file", fake_backup_file)
    monkeypatch.setattr(zentool, "cookies_file", lambda _profile: cookies_path)
    monkeypatch.setattr(zentool, "write_containers", lambda _path, _state: None)
    monkeypatch.setattr(zentool, "write_session", lambda _path, _state: None)
    plan = zentool.StateApplyPlan(
        session_path=session_path,
        containers_path=containers_path,
        desired_state=zentool.SessionState(),
        desired_containers=zentool.ContainerState(),
        diff_text="state diff",
        containers_changed=True,
        removed_context_ids={8},
    )

    zentool.apply_state_plan(SimpleNamespace(profile="Default"), plan)

    assert backups == [containers_path]
    assert stdout == [
        f"Backup: {session_path.name}.bak",
        f"Backup: {containers_path.name}.bak",
        "Applied state successfully.",
    ]

    cookies_path.touch()
    stdout.clear()
    backups.clear()
    monkeypatch.setattr(zentool, "prune_cookie_contexts", lambda _path, _ids: 0)

    zentool.apply_state_plan(SimpleNamespace(profile="Default"), plan)

    assert backups == [containers_path, cookies_path]
    assert "Pruned" not in "\n".join(stdout)


def test_build_state_apply_plan_reports_container_only_diffs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Container-only identity changes should still make apply actionable."""
    session = zentool.SessionState(
        spaces=[zentool.SessionSpace(uuid="ws-work", name="Work", containerTabId=6)]
    )
    containers = zentool.ContainerState.model_validate({
        "lastUserContextId": 8,
        "identities": [
            {
                "icon": "briefcase",
                "color": "orange",
                "name": "Town",
                "public": True,
                "userContextId": 6,
            },
            {
                "icon": "circle",
                "color": "purple",
                "name": "Old",
                "public": True,
                "userContextId": 8,
            },
        ],
    })
    config = zentool.ZenConfig(
        manage_containers=True,
        containers=[
            zentool.ContainerSpec(key="Town", icon="briefcase", color="orange")
        ],
        workspaces=[zentool.WorkspaceSpec(name="Work", container="Town")],
    )

    monkeypatch.setattr(
        zentool,
        "require_zen_closed",
        lambda _profile: zentool.ZenRuntimeState(running=False),
    )
    monkeypatch.setattr(zentool, "_print_runtime_warnings", lambda _runtime: None)
    monkeypatch.setattr(
        zentool,
        "load_session",
        lambda _profile: (tmp_path / zentool.SESSION_FILENAME, session),
    )
    monkeypatch.setattr(
        zentool,
        "load_containers",
        lambda _profile: (tmp_path / zentool.CONTAINERS_FILENAME, containers),
    )
    monkeypatch.setattr(zentool, "load_config", lambda _path: config)

    plan = zentool.build_state_apply_plan(
        SimpleNamespace(profile="Default", config=str(tmp_path / "folders.yaml"))
    )

    assert plan.containers_changed is True
    assert plan.removed_context_ids == {8}
    assert plan.diff_text is not None
    assert "identities" in plan.diff_text
