"""Additional tests for update CLI orchestration helpers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    from lib.nix.models.flake_lock import FlakeLock, FlakeLockNode

from lib.nix.models.sources import SourceEntry, SourcesFile
from lib.tests._assertions import check
from lib.update.cli import (
    OutputOptions,
    ResolvedTargets,
    UpdateOptions,
    UpdateSummary,
    _build_item_meta,
    _build_run_plan,
    _build_update_options,
    _collect_flake_inputs_for_list,
    _collect_source_entries_for_list,
    _emit_summary,
    _execute_run_plan,
    _flake_source_string,
    _handle_list_targets_request,
    _handle_schema_request,
    _handle_validate_request,
    _is_tty,
    _ListRow,
    _load_pinned_versions,
    _load_sources_for_run,
    _merge_source_updates,
    _persist_source_updates,
    _resolve_full_output,
    _resolve_root_input_node,
    _resolve_runtime_config,
    _resolve_tty_settings,
    _row_sort_value,
    check_required_tools,
    run_update_command,
    run_updates,
)
from lib.update.events import UpdateEvent
from lib.update.paths import REPO_ROOT


def _run_async[T](awaitable: object) -> T:
    return asyncio.run(awaitable)  # type: ignore[arg-type]


def test_build_update_options_and_required_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Map json_output alias and detect missing required tools."""
    opts = _build_update_options({"source": "demo", "json_output": True, "check": True})
    check(opts.source == "demo")
    check(opts.json is True)
    check(opts.check is True)

    monkeypatch.setattr(
        "lib.update.cli.shutil.which",
        lambda tool: None if tool == "flake-edit" else "/bin/x",
    )
    monkeypatch.setattr(
        "lib.update.cli.UPDATERS",
        {"demo": type("_U", (), {"required_tools": ("nix",)})},
    )

    check(check_required_tools(needs_sources=False) == [])
    check(check_required_tools(source="demo") == [])
    check(check_required_tools(include_flake_edit=True) == ["flake-edit"])
    check(check_required_tools(source="unknown", needs_sources=True) == [])


def test_tty_resolution_and_output_options(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Resolve tty modes and respect quiet/json output behavior."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setenv("TERM", "xterm-256color")
    check(_is_tty(force_tty=True, no_tty=False, zellij_guard=False) is True)
    check(_is_tty(force_tty=False, no_tty=True, zellij_guard=False) is False)

    monkeypatch.setenv("ZELLIJ", "1")
    check(_is_tty(force_tty=False, no_tty=False, zellij_guard=True) is False)

    monkeypatch.delenv("ZELLIJ", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    check(_is_tty(force_tty=False, no_tty=False, zellij_guard=False) is False)

    monkeypatch.setenv("UPDATE_LOG_FULL", "1")
    check(_resolve_full_output() is True)
    check(_resolve_full_output(full_output=False) is False)

    out = OutputOptions(json_output=False, quiet=False)
    out.print("hello")
    out.print_error("bad")
    printed = capsys.readouterr()
    check("hello" in printed.out)
    check("bad" in printed.err)

    quiet_out = OutputOptions(json_output=True, quiet=True)
    quiet_out.print("hidden")
    quiet_out.print_error("also hidden")
    hidden = capsys.readouterr()
    check(hidden.out == "")
    check(hidden.err == "")


def test_update_summary_and_emit_summary(capsys: pytest.CaptureFixture[str]) -> None:
    """Accumulate status priorities and print human/json summaries."""
    summary = UpdateSummary()
    summary.accumulate({"a": "no_change", "b": "updated"})
    summary.accumulate({"a": "error"})
    check(summary.updated == ["b"])
    check(summary.errors == ["a"])
    check(summary.to_dict()["success"] is False)

    code = _emit_summary(
        summary, had_errors=True, out=OutputOptions(json_output=True), dry_run=False
    )
    check(code == 1)
    payload = json.loads(capsys.readouterr().out)
    check(payload["errors"] == ["a"])

    summary_no_updates = UpdateSummary(updated=[], errors=[], no_change=[])
    code_no_updates = _emit_summary(
        summary_no_updates,
        had_errors=False,
        out=OutputOptions(json_output=False, quiet=False),
        dry_run=True,
    )
    check(code_no_updates == 0)
    check("No updates available" in capsys.readouterr().out)


def test_resolved_targets_and_item_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve source/input selections and derive UI item metadata."""
    monkeypatch.setattr("lib.update.cli.UPDATERS", {"src": object})
    monkeypatch.setattr(
        "lib.update.cli.get_flake_inputs_with_refs",
        lambda: [SimpleNamespace(name="inp", owner="o", repo="r", ref="v1")],
    )
    resolved = ResolvedTargets.from_options(UpdateOptions(source="src", no_refs=True))
    check(resolved.source_names == ["src"])
    check(resolved.ref_inputs == [])

    sources = SourcesFile(entries={"src": SourceEntry(hashes={}, input="inp")})
    meta, order = _build_item_meta(resolved, sources)
    check("src" in meta)
    check(order == sorted(order))

    source_updates = {"src": SourceEntry(hashes={"x86_64-linux": "sha256-1"})}
    existing = {"src": SourceEntry(hashes={"aarch64-darwin": "sha256-2"})}
    merged = _merge_source_updates(existing, source_updates, native_only=True)
    check("src" in merged)


def test_preflight_handlers_schema_list_validate(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Handle schema/list/validate preflight paths before runtime execution."""
    check(_handle_schema_request(UpdateOptions(schema=False)) is None)
    schema_code = _handle_schema_request(UpdateOptions(schema=True))
    check(schema_code == 0)
    check("$defs" in capsys.readouterr().out)

    monkeypatch.setattr(
        "lib.update.cli._collect_flake_inputs_for_list",
        lambda: [
            SimpleNamespace(
                name="i",
                item_type="flake",
                source="github:o/r",
                ref="v1",
                rev="deadbeef",
            )
        ],
    )
    monkeypatch.setattr(
        "lib.update.cli._collect_source_entries_for_list",
        lambda: [
            SimpleNamespace(
                name="a",
                item_type="sources.json",
                source="https://example.com/a.tar.gz",
                ref="1.0.0",
                rev=None,
            ),
            SimpleNamespace(
                name="b",
                item_type="sources.json",
                source="https://example.com/b.tar.gz",
                ref="2.0.0",
                rev=None,
            ),
        ],
    )
    list_code = _handle_list_targets_request(
        UpdateOptions(list_targets=True, json=True)
    )
    check(list_code == 0)
    list_payload = json.loads(capsys.readouterr().out)
    check([item["name"] for item in list_payload["rows"]] == ["a", "b", "i"])

    sorted_by_type_code = _handle_list_targets_request(
        UpdateOptions(list_targets=True, json=True, sort_by="type")
    )
    check(sorted_by_type_code == 0)
    sorted_by_type_payload = json.loads(capsys.readouterr().out)
    check([item["name"] for item in sorted_by_type_payload["rows"]] == ["i", "a", "b"])

    monkeypatch.setattr(
        "lib.update.cli.load_all_sources",
        lambda: SourcesFile(entries={"a": SourceEntry(hashes={})}),
    )
    monkeypatch.setattr(
        "lib.update.cli.validate_source_discovery_consistency", lambda: None
    )
    validate_code = _handle_validate_request(
        UpdateOptions(validate=True, json=True), OutputOptions(json_output=True)
    )
    check(validate_code == 0)
    validate_payload = json.loads(capsys.readouterr().out)
    check(validate_payload["valid"] is True)

    def _boom() -> None:
        msg = "nope"
        raise RuntimeError(msg)

    monkeypatch.setattr("lib.update.cli.validate_source_discovery_consistency", _boom)
    validate_err = _handle_validate_request(
        UpdateOptions(validate=True, json=True), OutputOptions(json_output=True)
    )
    check(validate_err == 1)
    err_payload = json.loads(capsys.readouterr().out)
    check(err_payload["valid"] is False)


def test_list_helpers_resolve_root_and_source_string() -> None:
    """Resolve root input nodes and render input source strings."""

    class _Lock:
        def __init__(self) -> None:
            self.root_node = SimpleNamespace(
                inputs={
                    "direct": "node-a",
                    "follows": ["wrapper", "nixpkgs"],
                    "unresolved": ["wrapper", "missing"],
                }
            )
            self.nodes = {
                "node-a": SimpleNamespace(original=None, locked=None, inputs=None),
                "node-b": SimpleNamespace(original=None, locked=None, inputs=None),
            }

        def _resolve_target_node_name(self, input_name: str) -> str | None:
            if input_name == "follows":
                return "node-b"
            return None

    lock = _Lock()
    direct_node, direct_follows = _resolve_root_input_node(
        cast("FlakeLock", lock), "direct"
    )
    check(direct_node is lock.nodes["node-a"])
    check(direct_follows is None)

    follows_node, follows_path = _resolve_root_input_node(
        cast("FlakeLock", lock), "follows"
    )
    check(follows_node is lock.nodes["node-b"])
    check(follows_path == "wrapper/nixpkgs")

    missing_node, missing_path = _resolve_root_input_node(
        cast("FlakeLock", lock), "missing"
    )
    check(missing_node is None)
    check(missing_path is None)

    unresolved_node, unresolved_path = _resolve_root_input_node(
        cast("FlakeLock", lock), "unresolved"
    )
    check(unresolved_node is None)
    check(unresolved_path == "wrapper/missing")

    github_node = SimpleNamespace(
        original=SimpleNamespace(
            type="github", owner="owner", repo="repo", url=None, path=None
        ),
        locked=None,
    )
    check(
        _flake_source_string(cast("FlakeLockNode", github_node), None)
        == "github:owner/repo"
    )

    url_node = SimpleNamespace(
        original=SimpleNamespace(
            type="git", owner=None, repo=None, url="https://x", path=None
        ),
        locked=None,
    )
    check(
        _flake_source_string(cast("FlakeLockNode", url_node), None) == "git:https://x"
    )

    path_node = SimpleNamespace(
        original=SimpleNamespace(
            type="path", owner=None, repo=None, url=None, path="./local"
        ),
        locked=None,
    )
    check(
        _flake_source_string(cast("FlakeLockNode", path_node), None) == "path:./local"
    )

    unknown_node = SimpleNamespace(
        original=SimpleNamespace(type=None, owner=None, repo=None, url=None, path=None),
        locked=None,
    )
    check(
        _flake_source_string(cast("FlakeLockNode", unknown_node), "dep/nixpkgs")
        == "follows:dep/nixpkgs"
    )

    url_only_node = SimpleNamespace(
        original=SimpleNamespace(type=None, owner=None, repo=None, url=None, path=None),
        locked=SimpleNamespace(
            type=None, owner=None, repo=None, url="https://plain", path=None
        ),
    )
    check(
        _flake_source_string(cast("FlakeLockNode", url_only_node), None)
        == "https://plain"
    )

    path_only_node = SimpleNamespace(
        original=SimpleNamespace(type=None, owner=None, repo=None, url=None, path=None),
        locked=SimpleNamespace(
            type=None, owner=None, repo=None, url=None, path="./plain"
        ),
    )
    check(
        _flake_source_string(cast("FlakeLockNode", path_only_node), None) == "./plain"
    )

    type_only_node = SimpleNamespace(
        original=SimpleNamespace(type=None, owner=None, repo=None, url=None, path=None),
        locked=SimpleNamespace(
            type="tarball", owner=None, repo=None, url=None, path=None
        ),
    )
    check(
        _flake_source_string(cast("FlakeLockNode", type_only_node), None) == "tarball"
    )
    check(_flake_source_string(None, None) == "<unknown>")


def test_collect_flake_inputs_for_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """Collect table rows for flake inputs with ref fallback behavior."""

    class _Lock:
        def __init__(self) -> None:
            self.root_node = SimpleNamespace(
                inputs={
                    "with-ref": "node-ref",
                    "with-selector": "node-selector",
                    "with-inferred": "node-inferred",
                    "unknown-inferred": "node-unknown",
                    "missing-node": "node-missing",
                }
            )
            self.nodes = {
                "node-ref": SimpleNamespace(
                    original=SimpleNamespace(
                        type="github",
                        owner="o",
                        repo="r",
                        ref="v1",
                        rev=None,
                        url=None,
                        path=None,
                    ),
                    locked=SimpleNamespace(
                        rev="rev1",
                        type="github",
                        owner="o",
                        repo="r",
                        url=None,
                        path=None,
                    ),
                ),
                "node-selector": SimpleNamespace(
                    original=SimpleNamespace(
                        type="github",
                        owner="o",
                        repo="r",
                        ref=None,
                        rev="selector-ref",
                        url=None,
                        path=None,
                    ),
                    locked=SimpleNamespace(
                        rev="rev2",
                        type="github",
                        owner="o",
                        repo="r",
                        url=None,
                        path=None,
                    ),
                ),
                "node-inferred": SimpleNamespace(
                    original=SimpleNamespace(
                        type="github",
                        owner="o",
                        repo="r",
                        ref=None,
                        rev=123,
                        url=None,
                        path=None,
                    ),
                    locked=SimpleNamespace(
                        rev="rev3",
                        type="github",
                        owner="o",
                        repo="r",
                        url=None,
                        path=None,
                    ),
                ),
                "node-unknown": SimpleNamespace(
                    original=SimpleNamespace(
                        type="github",
                        owner="o",
                        repo="r",
                        ref=None,
                        rev=456,
                        url=None,
                        path=None,
                    ),
                    locked=SimpleNamespace(
                        rev="rev4",
                        type="github",
                        owner="o",
                        repo="r",
                        url=None,
                        path=None,
                    ),
                ),
            }

        def _resolve_target_node_name(self, input_name: str) -> str | None:
            _ = input_name
            return None

    monkeypatch.setattr("lib.update.cli.load_flake_lock", _Lock)
    monkeypatch.setattr(
        "lib.update.cli.get_flake_input_version",
        lambda node: (
            "unknown"
            if node is not None and getattr(node.locked, "rev", None) == "rev4"
            else "inferred-version"
            if node is not None
            else "unknown"
        ),
    )

    rows = _collect_flake_inputs_for_list()
    by_name = {row.name: row for row in rows}

    check(by_name["with-ref"].item_type == "flake")
    check(by_name["with-ref"].ref == "v1")
    check(by_name["with-ref"].rev == "rev1")
    check(by_name["with-selector"].ref == "selector-ref")
    check(by_name["with-inferred"].ref == "inferred-version")
    check(by_name["unknown-inferred"].ref is None)
    check(by_name["missing-node"].ref is None)


def test_collect_source_entries_for_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """Collect table rows for sources.json entries and source paths."""
    monkeypatch.setattr(
        "lib.update.cli.load_all_sources",
        lambda: SourcesFile(
            entries={
                "inside": SourceEntry(
                    version="1.0.0",
                    hashes={},
                    urls={"x86_64-linux": "https://example.com/inside.tgz"},
                ),
                "outside": SourceEntry(
                    version="2.0.0",
                    hashes={},
                    commit="d" * 40,
                    urls={
                        "x86_64-linux": "https://example.com/outside-linux.tgz",
                        "aarch64-darwin": "https://example.com/outside-macos.tgz",
                    },
                ),
                "no-url": SourceEntry(version="3.0.0", hashes={}),
            }
        ),
    )
    monkeypatch.setattr(
        "lib.update.cli.package_file_map",
        lambda _filename: {
            "inside": REPO_ROOT / "packages" / "inside" / "sources.json",
            "outside": Path("/tmp/outside/sources.json"),
            "no-url": REPO_ROOT / "overlays" / "no-url" / "sources.json",
        },
    )

    rows = _collect_source_entries_for_list()
    by_name = {row.name: row for row in rows}

    check(by_name["inside"].item_type == "sources.json")
    check(by_name["inside"].source == "https://example.com/inside.tgz")
    check(by_name["inside"].ref == "1.0.0")
    check(by_name["inside"].rev is None)
    check(
        by_name["outside"].source == "https://example.com/outside-linux.tgz (+1 more)"
    )
    check(by_name["outside"].rev == "d" * 40)
    check(by_name["no-url"].source == "<none>")


def test_row_sort_value_variants() -> None:
    """Sort key helper should return the selected column value."""
    row = _ListRow(
        name="demo",
        item_type="flake",
        source="https://example.com/demo.tgz",
        ref="v1.2.3",
        rev="a" * 40,
    )
    check(_row_sort_value(row, "name") == "demo")
    check(_row_sort_value(row, "type") == "flake")
    check(_row_sort_value(row, "source") == "https://example.com/demo.tgz")
    check(_row_sort_value(row, "ref") == "v1.2.3")
    check(_row_sort_value(row, "rev") == "a" * 40)


def test_runtime_config_and_tty_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve runtime config and evaluate header display toggles."""
    captured: dict[str, object] = {}

    def _resolve_config(**kwargs: object) -> object:
        captured.update(kwargs)
        return SimpleNamespace(default_log_tail_lines=10, default_render_interval=0.1)

    monkeypatch.setattr("lib.update.cli.resolve_config", _resolve_config)
    cfg = _resolve_runtime_config(UpdateOptions(http_timeout=3, retries=2))
    check(cfg.default_log_tail_lines == 10)
    check(captured["http_timeout"] == 3)
    check(captured["retries"] == 2)

    resolved = ResolvedTargets(
        all_source_names=set(),
        all_ref_inputs=[],
        all_ref_names=set(),
        all_known_names=set(),
        do_refs=True,
        do_sources=True,
        do_input_refresh=True,
        dry_run=False,
        native_only=False,
        ref_inputs=[SimpleNamespace(name="inp", owner="o", repo="r", ref="v1")],
        source_names=["src"],
    )

    monkeypatch.setattr("lib.update.cli._is_tty", lambda **_kwargs: False)
    tty_enabled, show_headers = _resolve_tty_settings(
        UpdateOptions(json=False, quiet=False), resolved
    )
    check(tty_enabled is False)
    check(show_headers is True)


def test_sort_option_requires_list(capsys: pytest.CaptureFixture[str]) -> None:
    """Reject --sort/-o usage when --list is not enabled."""
    exit_code = _run_async(run_updates(UpdateOptions(sort_by="rev", json=True)))
    check(exit_code == 1)
    payload = json.loads(capsys.readouterr().out)
    check(payload["success"] is False)
    check("--sort/-o" in payload["error"])


def test_sort_option_requires_list_non_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Emit stderr validation error for --sort/-o without --list."""
    exit_code = _run_async(run_updates(UpdateOptions(sort_by="rev", json=False)))
    check(exit_code == 1)
    captured = capsys.readouterr()
    check("--sort/-o" in captured.err)


def test_load_sources_and_persist_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Load sources only when needed and persist changed updates."""
    source_file = SourcesFile(entries={"a": SourceEntry(hashes={})})
    monkeypatch.setattr("lib.update.cli.load_all_sources", lambda: source_file)

    resolved = ResolvedTargets(
        all_source_names={"a"},
        all_ref_inputs=[],
        all_ref_names=set(),
        all_known_names={"a"},
        do_refs=False,
        do_sources=True,
        do_input_refresh=True,
        dry_run=False,
        native_only=False,
        ref_inputs=[],
        source_names=["a"],
    )
    check(_load_sources_for_run(resolved) is source_file)
    resolved_none = ResolvedTargets(
        all_source_names=set(),
        all_ref_inputs=[],
        all_ref_names=set(),
        all_known_names=set(),
        do_refs=False,
        do_sources=False,
        do_input_refresh=False,
        dry_run=False,
        native_only=False,
        ref_inputs=[],
        source_names=[],
    )
    check(_load_sources_for_run(resolved_none).entries == {})

    save_calls: list[SourcesFile] = []
    monkeypatch.setattr(
        "lib.update.cli.save_sources", lambda src: save_calls.append(src)
    )
    updates = {"a": SourceEntry(hashes={"x86_64-linux": "sha256-1"})}
    _persist_source_updates(
        resolved=resolved,
        sources=source_file,
        source_updates=updates,
        details={"a": "updated"},
    )
    check(len(save_calls) == 1)


def test_load_pinned_versions_and_run_plan_building(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Load pinned versions and build executable run plans."""
    out = OutputOptions(json_output=False, quiet=True)
    check(_load_pinned_versions(UpdateOptions(), out) == {})

    pinned_path = "/tmp/pinned.json"
    monkeypatch.setattr(
        "lib.update.cli.load_pinned_versions",
        lambda _path: {"a": SimpleNamespace(version="1", metadata={})},
    )
    loaded = _load_pinned_versions(UpdateOptions(pinned_versions=pinned_path), out)
    check("a" in loaded)

    monkeypatch.setattr("lib.update.cli.UPDATERS", {"src": object})
    monkeypatch.setattr(
        "lib.update.cli.get_flake_inputs_with_refs",
        lambda: [SimpleNamespace(name="inp", owner="o", repo="r", ref="v1")],
    )
    monkeypatch.setattr(
        "lib.update.cli._resolve_tty_settings", lambda opts, resolved: (False, False)
    )
    monkeypatch.setattr(
        "lib.update.cli._load_sources_for_run", lambda resolved: SourcesFile(entries={})
    )
    monkeypatch.setattr(
        "lib.update.cli._build_item_meta",
        lambda resolved, sources: (
            {"src": SimpleNamespace(name="src", origin="x", op_order=())},
            ["src"],
        ),
    )

    plan = _build_run_plan(UpdateOptions(), OutputOptions())
    check(not isinstance(plan, int))

    unknown = _build_run_plan(UpdateOptions(source="unknown"), OutputOptions())
    check(unknown == 1)

    monkeypatch.setattr(
        "lib.update.cli._build_item_meta", lambda resolved, sources: ({}, [])
    )
    empty = _build_run_plan(UpdateOptions(), OutputOptions())
    check(empty == 0)


def test_execute_run_plan_and_top_level_entrypoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execute run plan phases and validate top-level command behavior."""
    resolved = ResolvedTargets(
        all_source_names={"src"},
        all_ref_inputs=[SimpleNamespace(name="inp", owner="o", repo="r", ref="v1")],
        all_ref_names={"inp"},
        all_known_names={"src", "inp"},
        do_refs=True,
        do_sources=True,
        do_input_refresh=True,
        dry_run=False,
        native_only=False,
        ref_inputs=[SimpleNamespace(name="inp", owner="o", repo="r", ref="v1")],
        source_names=["src"],
    )
    plan = SimpleNamespace(
        resolved=resolved,
        tty_enabled=False,
        show_phase_headers=True,
        sources=SourcesFile(entries={"src": SourceEntry(hashes={})}),
        item_meta={"src": SimpleNamespace(name="src", origin="x", op_order=())},
        order=["src"],
    )

    async def _consume(
        queue: asyncio.Queue[UpdateEvent | None],
        _order: list[str],
        _sources: SourcesFile,
        *,
        options: object,
    ) -> tuple[list[str], int, dict[str, str], dict[str, SourceEntry]]:
        _ = options
        while await queue.get() is not None:
            pass
        return (
            ["src"],
            0,
            {"src": "updated"},
            {
                "src": SourceEntry(
                    hashes={
                        "x86_64-linux": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
                    }
                )
            },
        )

    async def _run_ref_phase(**_kwargs: object) -> None:
        return None

    async def _run_sources_phase(*, context: object) -> None:
        _ = context

    monkeypatch.setattr("lib.update.cli.consume_events", _consume)
    monkeypatch.setattr("lib.update.cli._run_ref_phase", _run_ref_phase)
    monkeypatch.setattr("lib.update.cli._run_sources_phase", _run_sources_phase)
    monkeypatch.setattr(
        "lib.update.cli._persist_source_updates", lambda **_kwargs: None
    )

    cfg = SimpleNamespace(default_log_tail_lines=10, default_render_interval=0.1)
    exit_code = _run_async(
        _execute_run_plan(UpdateOptions(), OutputOptions(), cfg, plan)
    )
    check(exit_code == 0)

    # run_updates preflight short-circuit
    monkeypatch.setattr("lib.update.cli._resolve_runtime_config", lambda _opts: cfg)
    monkeypatch.setattr(
        "lib.update.cli._handle_preflight_requests", lambda _opts, _out: 7
    )
    check(_run_async(run_updates(UpdateOptions())) == 7)

    monkeypatch.setattr(
        "lib.update.cli._handle_preflight_requests", lambda _opts, _out: None
    )
    monkeypatch.setattr("lib.update.cli._build_run_plan", lambda _opts, _out: 3)
    check(_run_async(run_updates(UpdateOptions())) == 3)

    monkeypatch.setattr("lib.update.cli._build_run_plan", lambda _opts, _out: plan)
    monkeypatch.setattr(
        "lib.update.cli._execute_run_plan",
        lambda _opts, _out, _cfg, _plan: asyncio.sleep(0, result=0),
    )
    check(_run_async(run_updates(UpdateOptions())) == 0)

    # run_update_command tool checks and execution
    monkeypatch.setattr(
        "lib.update.cli.check_required_tools", lambda **_kwargs: ["nix"]
    )
    check(run_update_command() == 1)

    monkeypatch.setattr("lib.update.cli.check_required_tools", lambda **_kwargs: [])
    monkeypatch.setattr(
        "lib.update.cli.run_updates", lambda _opts: asyncio.sleep(0, result=5)
    )
    check(run_update_command(list_targets=True) == 5)
