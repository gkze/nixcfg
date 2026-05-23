"""Focused pure-Python tests for zentool diff/apply asset flows."""

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
    """Load the zentool script for focused diff/apply tests."""
    return load_zen_script_module("zentool", "zentool_diff_apply")


def make_args(**overrides: object) -> SimpleNamespace:
    """Build a minimal namespace for diff/apply entrypoints."""
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
    """Minimal diff object exposing zentool's expected DeepDiff surface."""

    def __init__(self, text: str, *, truthy: bool = True) -> None:
        self.text = text
        self.truthy = truthy

    def pretty(self) -> str:
        return self.text

    def __bool__(self) -> bool:
        return self.truthy


def make_state_diff(
    zentool: ModuleType,
    text: str,
    *,
    truthy: bool = True,
) -> object:
    """Build one StateDiff test double with compact snapshots."""
    return zentool.StateDiff(
        current_snapshot={"Workspace": [{"Inbox": "https://old.example"}]},
        desired_snapshot={"Workspace": [{"Inbox": "https://new.example"}]},
        diff=FakeDiff(text, truthy=truthy),
    )


def test_human_plan_renders_nested_add_remove_and_change(
    zentool: ModuleType,
) -> None:
    """TTY plans should render scalar and nested authored-state deltas."""
    current = {
        "workspaces": {
            "Work": {
                "tree": [
                    {"name": "Old", "url": "https://old.example"},
                    {"nested": ["gone"]},
                ],
                "color": "blue",
            }
        },
        "containers": {"Town": {"color": "orange"}, "Old": {"color": "red"}},
    }
    desired = {
        "workspaces": {
            "Work": {
                "tree": [
                    {"name": "New", "url": "https://new.example"},
                    {"nested": ["kept", "added"]},
                ],
                "color": "green",
                "extra-key": {"enabled": True},
            },
            "Home": {"tree": []},
        },
        "containers": {"Town": {"color": "orange"}, "Personal": {"color": "blue"}},
    }

    plan = zentool._format_human_plan(current, desired)

    assert "Plan:" in plan
    assert '- container["Old"]' in plan
    assert '+ container["Personal"]' in plan
    assert '~ workspace["Work"].color = "blue" -> "green"' in plan
    assert 'workspace["Work"]["extra-key"]' in plan
    assert "gone" in plan
    assert "added" in plan


def test_plan_helpers_cover_empty_roots_and_tty_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Plan and preview formatters should handle empty and tty/non-tty output."""
    assert zentool._format_plan_path(()) == "root"
    assert zentool._format_plan_path(("custom", "child", 2, "not-key!")) == (
        'custom.child[2]["not-key!"]'
    )
    assert zentool._render_plan_block(None, "+") == ["      + null", "      + ..."]
    assert zentool._render_plan_block({}, "+") == ["      + {}"]
    monkeypatch.setattr(zentool.yaml, "safe_dump", lambda *_args, **_kwargs: "")
    assert zentool._render_plan_block({"ignored": True}, "+") == ["      + <empty>"]
    assert zentool._format_human_plan({}, {}) == ""
    assert (
        zentool._format_human_plan(
            {"workspaces": {"Work": {"tree": []}}},
            {"workspaces": {"Work": {"tree": []}}},
        )
        == ""
    )

    lines: list[str] = []
    counters = zentool._PlanChangeCounter()
    zentool._collect_plan_changes(
        {"removed": "old", "changed": {"nested": "old"}, "items": ["same", "gone"]},
        {
            "changed": {"nested": ["new"]},
            "items": ["same", "added"],
            "inserted": "new",
        },
        ("workspace", "Work"),
        lines,
        counters,
    )
    rendered_lines = "\n".join(lines)
    assert 'workspace["Work"].removed = "old"' in rendered_lines
    assert 'workspace["Work"].changed.nested' in rendered_lines
    assert 'workspace["Work"].items[1] = "gone" -> "added"' in rendered_lines
    assert 'workspace["Work"].inserted = "new"' in rendered_lines

    list_lines: list[str] = []
    list_counters = zentool._PlanChangeCounter()
    zentool._collect_plan_changes(
        ["same", "removed"],
        ["same"],
        ("workspace", "Work", "delete-only"),
        list_lines,
        list_counters,
    )
    zentool._collect_plan_changes(
        ["same"],
        ["same", "inserted"],
        ("workspace", "Work", "insert-only"),
        list_lines,
        list_counters,
    )
    zentool._collect_plan_changes(
        ["old", "extra"],
        ["new"],
        ("workspace", "Work", "replace-with-extra-current"),
        list_lines,
        list_counters,
    )
    rendered_list_lines = "\n".join(list_lines)
    assert 'workspace["Work"]["delete-only"][1] = "removed"' in rendered_list_lines
    assert 'workspace["Work"]["insert-only"][1] = "inserted"' in rendered_list_lines
    assert (
        'workspace["Work"]["replace-with-extra-current"][1] = "extra"'
        in rendered_list_lines
    )

    monkeypatch.setattr(zentool, "_stdout_is_tty", lambda: False)
    assert (
        zentool._format_state_diff_text("machine", {}, {"Workspace": []}) == "machine"
    )
    assert zentool._format_asset_diff_text(["plain asset line"]) == "plain asset line"

    monkeypatch.setattr(zentool, "_stdout_is_tty", lambda: True)
    assert (
        zentool._format_asset_diff_text(["plain asset line"])
        == "Assets:\n  ~ plain asset line"
    )

    with monkeypatch.context() as plan_patch:
        plan_patch.setattr(
            zentool,
            "_normal_plan_roots",
            lambda snapshot: {
                "container": [] if snapshot["kind"] == "current" else {},
                "workspace": {},
            },
        )
        assert (
            zentool._format_human_plan({"kind": "current"}, {"kind": "desired"}) == ""
        )


def test_print_apply_preview_omits_empty_asset_diff(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Preview printing should skip empty formatted asset sections."""
    stdout: list[str] = []
    monkeypatch.setattr(zentool, "_stdout", lambda text="": stdout.append(text))
    monkeypatch.setattr(zentool, "_stdout_is_tty", lambda: True)
    preview = zentool.ApplyPreview(
        state_plan=None,
        asset_lines=["create asset chrome/new.css"],
    )

    zentool.print_apply_preview(preview)

    assert stdout == ["Assets:\n  + chrome/new.css"]

    stdout.clear()
    preview.asset_lines = ["asset"]
    monkeypatch.setattr(zentool, "_format_asset_diff_text", lambda _lines: None)
    zentool.print_apply_preview(preview)
    assert stdout == []


def test_asset_diff_lines_reports_create_update_remove_and_user_js_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Asset diff should describe create, update, remove, and user.js drift."""
    profile_dir = tmp_path / "profile"
    profile_chrome_dir = profile_dir / "chrome"
    profile_chrome_dir.mkdir(parents=True)

    chrome_source = tmp_path / "assets" / "chrome"
    (chrome_source / "nested").mkdir(parents=True)
    create_source = chrome_source / "create.css"
    create_source.write_text("create\n", encoding="utf-8")
    update_source = chrome_source / "nested" / "update.css"
    update_source.write_text("update\n", encoding="utf-8")

    wrong_target = tmp_path / "wrong.css"
    wrong_target.write_text("wrong\n", encoding="utf-8")
    update_destination = profile_chrome_dir / "nested" / "update.css"
    update_destination.parent.mkdir(parents=True)
    update_destination.symlink_to(wrong_target)

    manifest_path = profile_dir / zentool.MANAGED_CHROME_MANIFEST
    manifest_path.write_text("nested/update.css\nstale.css\n", encoding="utf-8")

    user_js_source = tmp_path / "assets" / "user.js"
    user_js_source.parent.mkdir(parents=True, exist_ok=True)
    user_js_source.write_text("source\n", encoding="utf-8")
    wrong_user_js = tmp_path / "wrong-user.js"
    wrong_user_js.write_text("wrong\n", encoding="utf-8")
    user_js_destination = profile_dir / "user.js"
    user_js_destination.symlink_to(wrong_user_js)

    user_js_manifest_path = profile_dir / zentool.MANAGED_USER_JS_MANIFEST

    monkeypatch.setattr(
        zentool,
        "_resolve_asset_targets",
        lambda _args: zentool.AssetTargets(
            profile_dir=profile_dir,
            chrome_source=chrome_source,
            user_js_source=user_js_source,
            profile_chrome_dir=profile_chrome_dir,
            chrome_manifest_path=manifest_path,
            user_js_manifest_path=user_js_manifest_path,
        ),
    )

    assert zentool._asset_diff_lines(make_args()) == [
        f"create asset chrome/create.css -> {create_source}",
        f"update asset chrome/nested/update.css -> {update_source}",
        "remove asset chrome/stale.css",
        f"update asset user.js -> {user_js_source}",
    ]


def test_asset_diff_lines_keeps_symlinked_chrome_sources_stable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Chrome diffs should match the symlink target that apply writes."""
    profile_dir = tmp_path / "profile"
    profile_chrome_dir = profile_dir / "chrome"
    profile_chrome_dir.mkdir(parents=True)

    realized_theme = tmp_path / "theme-store" / "userChrome.css"
    realized_theme.parent.mkdir()
    realized_theme.write_text("theme\n", encoding="utf-8")

    chrome_source = tmp_path / "assets" / "chrome"
    chrome_source.mkdir(parents=True)
    managed_source = chrome_source / "userChrome.css"
    managed_source.symlink_to(realized_theme)
    (profile_chrome_dir / "userChrome.css").symlink_to(managed_source)

    manifest_path = profile_dir / zentool.MANAGED_CHROME_MANIFEST
    manifest_path.write_text("userChrome.css\n", encoding="utf-8")
    user_js_manifest_path = profile_dir / zentool.MANAGED_USER_JS_MANIFEST

    monkeypatch.setattr(
        zentool,
        "_resolve_asset_targets",
        lambda _args: zentool.AssetTargets(
            profile_dir=profile_dir,
            chrome_source=chrome_source,
            user_js_source=None,
            profile_chrome_dir=profile_chrome_dir,
            chrome_manifest_path=manifest_path,
            user_js_manifest_path=user_js_manifest_path,
        ),
    )

    assert zentool._asset_diff_lines(make_args()) == []


def test_asset_diff_lines_reports_managed_user_js_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Asset diff should remove user.js only when the managed link still matches."""
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    profile_chrome_dir = profile_dir / "chrome"
    manifest_path = profile_dir / zentool.MANAGED_CHROME_MANIFEST
    user_js_manifest_path = profile_dir / zentool.MANAGED_USER_JS_MANIFEST

    previous_source = tmp_path / "managed-user.js"
    previous_source.write_text("managed\n", encoding="utf-8")
    user_js_manifest_path.write_text(f"{previous_source}\n", encoding="utf-8")
    (profile_dir / "user.js").symlink_to(previous_source)

    monkeypatch.setattr(
        zentool,
        "_resolve_asset_targets",
        lambda _args: zentool.AssetTargets(
            profile_dir=profile_dir,
            chrome_source=None,
            user_js_source=None,
            profile_chrome_dir=profile_chrome_dir,
            chrome_manifest_path=manifest_path,
            user_js_manifest_path=user_js_manifest_path,
        ),
    )

    assert zentool._asset_diff_lines(make_args()) == ["remove asset user.js"]


def test_apply_assets_syncs_sources_and_reports_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Asset apply should clean old links, sync sources, and link user.js."""
    profile_dir = tmp_path / "profile"
    profile_chrome_dir = profile_dir / "chrome"
    manifest_path = profile_dir / zentool.MANAGED_CHROME_MANIFEST
    user_js_manifest_path = profile_dir / zentool.MANAGED_USER_JS_MANIFEST
    chrome_source = tmp_path / "assets" / "chrome"
    user_js_source = tmp_path / "assets" / "user.js"
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        zentool,
        "_resolve_asset_targets",
        lambda _args: zentool.AssetTargets(
            profile_dir=profile_dir,
            chrome_source=chrome_source,
            user_js_source=user_js_source,
            profile_chrome_dir=profile_chrome_dir,
            chrome_manifest_path=manifest_path,
            user_js_manifest_path=user_js_manifest_path,
        ),
    )
    monkeypatch.setattr(zentool, "_asset_diff_lines", lambda _args: ["changed"])
    monkeypatch.setattr(
        zentool,
        "cleanup_managed_chrome_symlinks",
        lambda chrome_dir, manifest: calls.append(("cleanup", (chrome_dir, manifest))),
    )
    monkeypatch.setattr(
        zentool,
        "prune_empty_chrome_dirs",
        lambda chrome_dir: calls.append(("prune", chrome_dir)),
    )
    monkeypatch.setattr(
        zentool,
        "sync_chrome_tree",
        lambda source, chrome_dir, manifest: calls.append((
            "sync",
            (source, chrome_dir, manifest),
        )),
    )
    monkeypatch.setattr(
        zentool,
        "link_managed_file",
        lambda source, destination, *, manifest_path: calls.append((
            "link",
            (source, destination, manifest_path),
        )),
    )

    assert zentool._apply_assets(make_args()) is True
    assert profile_chrome_dir.is_dir()
    assert calls == [
        ("cleanup", (profile_chrome_dir, manifest_path)),
        ("prune", profile_chrome_dir),
        ("sync", (chrome_source, profile_chrome_dir, manifest_path)),
        ("link", (user_js_source, profile_dir / "user.js", user_js_manifest_path)),
    ]


def test_apply_assets_removes_manifest_when_chrome_source_is_omitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Asset apply should clear the chrome manifest when managed chrome is disabled."""
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    profile_chrome_dir = profile_dir / "chrome"
    manifest_path = profile_dir / zentool.MANAGED_CHROME_MANIFEST
    manifest_path.write_text("old.css\n", encoding="utf-8")
    user_js_manifest_path = profile_dir / zentool.MANAGED_USER_JS_MANIFEST
    calls: list[str] = []

    monkeypatch.setattr(
        zentool,
        "_resolve_asset_targets",
        lambda _args: zentool.AssetTargets(
            profile_dir=profile_dir,
            chrome_source=None,
            user_js_source=None,
            profile_chrome_dir=profile_chrome_dir,
            chrome_manifest_path=manifest_path,
            user_js_manifest_path=user_js_manifest_path,
        ),
    )
    monkeypatch.setattr(zentool, "_asset_diff_lines", lambda _args: [])
    monkeypatch.setattr(
        zentool,
        "cleanup_managed_chrome_symlinks",
        lambda *_args: calls.append("cleanup"),
    )
    monkeypatch.setattr(
        zentool,
        "prune_empty_chrome_dirs",
        lambda *_args: calls.append("prune"),
    )
    monkeypatch.setattr(
        zentool,
        "sync_chrome_tree",
        lambda *_args: calls.append("sync"),
    )
    monkeypatch.setattr(
        zentool,
        "link_managed_file",
        lambda *_args, **_kwargs: calls.append("link"),
    )

    assert zentool._apply_assets(make_args()) is False
    assert not manifest_path.exists()
    assert calls == ["cleanup", "prune", "link"]


def test_cmd_diff_prints_no_changes_when_state_and_assets_match(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Diff should emit the no-op message when both selected scopes are clean."""
    stdout: list[str] = []

    monkeypatch.setattr(
        zentool, "load_session", lambda _profile: (Path("session"), object())
    )
    monkeypatch.setattr(
        zentool,
        "load_containers",
        lambda _profile: (Path("containers.json"), object()),
    )
    monkeypatch.setattr(zentool, "load_config", lambda _path: object())
    monkeypatch.setattr(
        zentool,
        "build_state_diff",
        lambda _session, _config, _containers: make_state_diff(
            zentool,
            "",
            truthy=False,
        ),
    )
    monkeypatch.setattr(zentool, "_asset_diff_lines", lambda _args: [])
    monkeypatch.setattr(zentool, "_stdout", lambda message="": stdout.append(message))

    assert zentool.cmd_diff(make_args()) == 0
    assert stdout == ["No changes needed."]


def test_cmd_diff_prints_state_and_asset_sections_together(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Diff should join state and asset output with one blank line between sections."""
    stdout: list[str] = []

    monkeypatch.setattr(
        zentool, "load_session", lambda _profile: (Path("session"), object())
    )
    monkeypatch.setattr(
        zentool,
        "load_containers",
        lambda _profile: (Path("containers.json"), object()),
    )
    monkeypatch.setattr(zentool, "load_config", lambda _path: object())
    monkeypatch.setattr(
        zentool,
        "build_state_diff",
        lambda _session, _config, _containers: make_state_diff(
            zentool,
            "state diff",
        ),
    )
    monkeypatch.setattr(
        zentool,
        "_asset_diff_lines",
        lambda _args: ["asset one", "asset two"],
    )
    monkeypatch.setattr(zentool, "_stdout", lambda message="": stdout.append(message))

    assert zentool.cmd_diff(make_args()) == 0
    assert stdout == ["state diff\n\nasset one\nasset two"]


def test_state_diff_text_renders_human_plan_on_tty(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """TTY state diffs should render as a concise Terraform-style plan."""
    monkeypatch.setattr(zentool, "_stdout_is_tty", lambda: True)

    current = {
        "Workspace": {
            "icon": "old",
            "tree": [{"Inbox": "https://old.example"}],
        }
    }
    desired = {
        "Workspace": {
            "icon": "new",
            "tree": [
                {"Inbox": "https://new.example"},
                {"Docs": "https://docs.example"},
            ],
        }
    }

    assert zentool._format_state_diff_text("machine diff", current, desired) == (
        "Plan: 1 to add, 2 to change, 0 to destroy.\n"
        "\n"
        '  ~ workspace["Workspace"].icon = "old" -> "new"\n'
        '  ~ workspace["Workspace"].tree[0].Inbox = '
        '"https://old.example" -> "https://new.example"\n'
        '  + workspace["Workspace"].tree[1]\n'
        "      + Docs: https://docs.example"
    )


def test_asset_diff_text_renders_human_plan_on_tty(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """TTY asset diffs should use the same add/change/remove markers."""
    monkeypatch.setattr(zentool, "_stdout_is_tty", lambda: True)

    assert zentool._format_asset_diff_text([
        "create asset chrome/userChrome.css -> /nix/store/theme/userChrome.css",
        "update asset user.js -> /nix/store/zen/user.js",
        "remove asset chrome/stale.css",
    ]) == (
        "Assets:\n"
        "  + chrome/userChrome.css -> /nix/store/theme/userChrome.css\n"
        "  ~ user.js -> /nix/store/zen/user.js\n"
        "  - chrome/stale.css"
    )


def test_cmd_apply_prints_no_changes_when_nothing_is_pending(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Apply should short-circuit before prompting when no state or asset changes exist."""
    stdout: list[str] = []

    monkeypatch.setattr(zentool, "_asset_diff_lines", lambda _args: [])
    monkeypatch.setattr(zentool, "_stdout", lambda message="": stdout.append(message))

    assert zentool.cmd_apply(make_args(state=False, assets=True, yes=False)) == 0
    assert stdout == ["No changes needed."]


def test_cmd_apply_aborts_when_asset_confirmation_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Apply should use the asset-only prompt and stop when the user declines."""
    stdout: list[str] = []
    prompts: list[str] = []

    monkeypatch.setattr(zentool, "_asset_diff_lines", lambda _args: ["asset change"])
    monkeypatch.setattr(zentool, "_stdout", lambda message="": stdout.append(message))
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: prompts.append(prompt) or "n",
    )
    monkeypatch.setattr(
        zentool,
        "_apply_assets",
        lambda _args: pytest.fail("assets should not be applied after rejection"),
    )

    assert zentool.cmd_apply(make_args(state=False, assets=True, yes=False)) == 1
    assert prompts == ["Apply asset changes? [y/N] "]
    assert stdout == ["asset change", "Aborted."]


def test_cmd_apply_applies_state_and_assets_after_showing_both_diffs(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Apply should write state, apply assets, and report both success messages."""
    stdout: list[str] = []
    session = object()
    desired_state = object()
    containers = object()
    desired_containers = object()
    config = SimpleNamespace(manage_containers=False)
    container_plan = SimpleNamespace(
        state=desired_containers,
        removed_context_ids=set(),
    )
    session_path = Path("/tmp/session.jsonlz4")
    containers_path = Path("/tmp/containers.json")
    backup_path = Path("/tmp/session.jsonlz4.bak")
    runtime = object()
    diff = FakeDiff("state diff")

    monkeypatch.setattr(zentool, "require_zen_closed", lambda _profile: runtime)
    monkeypatch.setattr(zentool, "_print_runtime_warnings", lambda _state: None)
    monkeypatch.setattr(
        zentool, "load_session", lambda _profile: (session_path, session)
    )
    monkeypatch.setattr(
        zentool,
        "load_containers",
        lambda _profile: (containers_path, containers),
    )
    monkeypatch.setattr(zentool, "load_config", lambda _path: config)
    monkeypatch.setattr(
        zentool,
        "build_desired_containers",
        lambda _containers, _config, _session: container_plan,
    )
    monkeypatch.setattr(
        zentool,
        "build_desired_state",
        lambda existing, _config, _plan: desired_state if existing is session else None,
    )
    monkeypatch.setattr(
        zentool,
        "snapshot",
        lambda value, _containers=None: {
            "kind": "session" if value is session else "desired"
        },
    )
    monkeypatch.setattr(zentool, "DeepDiff", lambda old, new, **_kwargs: diff)
    monkeypatch.setattr(zentool, "_asset_diff_lines", lambda _args: ["asset change"])
    monkeypatch.setattr(zentool, "backup_session", lambda _path: backup_path)
    written: list[tuple[Path, object]] = []
    monkeypatch.setattr(
        zentool,
        "write_session",
        lambda path, state: written.append((path, state)),
    )
    applied_assets: list[SimpleNamespace] = []
    monkeypatch.setattr(
        zentool,
        "_apply_assets",
        lambda args: applied_assets.append(args) or True,
    )
    monkeypatch.setattr(zentool, "_stdout", lambda message="": stdout.append(message))

    args = make_args(state=True, assets=True, yes=True)
    assert zentool.cmd_apply(args) == 0

    assert written == [(session_path, desired_state)]
    assert applied_assets == [args]
    assert stdout == [
        "state diff",
        "",
        "asset change",
        f"Backup: {backup_path.name}",
        "Applied state successfully.",
        "Applied assets successfully.",
    ]


def test_cmd_apply_accepts_combined_prompt_before_applying_changes(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Combined state+asset applies should proceed when the user answers yes."""
    stdout: list[str] = []
    prompts: list[str] = []
    session = object()
    desired_state = object()
    containers = object()
    desired_containers = object()
    config = SimpleNamespace(manage_containers=False)
    container_plan = SimpleNamespace(
        state=desired_containers,
        removed_context_ids=set(),
    )
    session_path = Path("/tmp/session.jsonlz4")
    containers_path = Path("/tmp/containers.json")
    backup_path = Path("/tmp/session.jsonlz4.bak")

    monkeypatch.setattr(zentool, "require_zen_closed", lambda _profile: object())
    monkeypatch.setattr(zentool, "_print_runtime_warnings", lambda _state: None)
    monkeypatch.setattr(
        zentool, "load_session", lambda _profile: (session_path, session)
    )
    monkeypatch.setattr(
        zentool,
        "load_containers",
        lambda _profile: (containers_path, containers),
    )
    monkeypatch.setattr(zentool, "load_config", lambda _path: config)
    monkeypatch.setattr(
        zentool,
        "build_desired_containers",
        lambda _containers, _config, _session: container_plan,
    )
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
    monkeypatch.setattr(zentool, "_asset_diff_lines", lambda _args: ["asset change"])
    monkeypatch.setattr(zentool, "backup_session", lambda _path: backup_path)
    monkeypatch.setattr(zentool, "write_session", lambda *_args: None)
    monkeypatch.setattr(zentool, "_apply_assets", lambda _args: True)
    monkeypatch.setattr(zentool, "_stdout", lambda message="": stdout.append(message))
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: prompts.append(prompt) or "yes",
    )

    args = make_args(state=True, assets=True, yes=False)
    assert zentool.cmd_apply(args) == 0
    assert prompts == ["Apply changes? [y/N] "]
    assert stdout == [
        "state diff",
        "",
        "asset change",
        f"Backup: {backup_path.name}",
        "Applied state successfully.",
        "Applied assets successfully.",
    ]
