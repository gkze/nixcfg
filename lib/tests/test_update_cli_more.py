"""Additional tests for update CLI orchestration helpers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, ClassVar, cast

import pytest
import typer

if TYPE_CHECKING:
    from lib.nix.models.flake_lock import FlakeLock, FlakeLockNode
    from lib.update.events import UpdateEvent

import lib.update.cli as update_cli_module
from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry, SourcesFile
from lib.update import cli_inventory as cli_inventory_module
from lib.update import cli_validation as cli_validation_module
from lib.update.artifacts import GeneratedArtifact
from lib.update.cli import (
    OutputOptions,
    ResolvedTargets,
    UpdateOptions,
    UpdateSummary,
    _build_item_meta,
    _build_run_plan,
    _build_update_inventory,
    _build_update_options,
    _emit_summary,
    _execute_run_plan,
    _get_updaters,
    _handle_list_targets_request,
    _handle_preflight_requests,
    _handle_schema_request,
    _handle_validate_request,
    _is_tty,
    _load_pinned_versions,
    _load_sources_for_run,
    _merge_source_updates,
    _persist_generated_artifacts,
    _persist_materialized_updates,
    _persist_source_updates,
    _resolve_full_output,
    _resolve_runtime_config,
    _resolve_tty_settings,
    check_required_tools,
    cli,
    run_update_command,
    run_updates,
)
from lib.update.cli_inventory import (
    _build_inventory_summary,
    _classify_updater_kind,
    _collect_flake_inputs_for_list,
    _collect_source_entries_for_list,
    _crate2nix_generated_artifact_paths,
    _flake_source_string,
    _generated_artifact_paths,
    _inventory_classification,
    _inventory_sort_value,
    _InventoryHandles,
    _InventoryRefTarget,
    _InventorySourceTarget,
    _InventoryTarget,
    _ListRow,
    _repo_relative_path,
    _row_sort_value,
    _source_backing_input_name,
    _source_hash_kinds,
)
from lib.update.paths import REPO_ROOT
from lib.update.refs import FlakeInputRef
from lib.update.ui_state import OperationKind
from lib.update.updaters.base import (
    ChecksumProvidedUpdater,
    DenoManifestUpdater,
    DownloadHashUpdater,
    FlakeInputHashUpdater,
    FlakeInputMetadataUpdater,
    FlakeInputUpdater,
    HashEntryUpdater,
    Updater,
    UvLockUpdater,
)
from lib.update.updaters.platform_api import PlatformAPIUpdater


def _run_async[T](awaitable: object) -> T:
    return asyncio.run(awaitable)  # type: ignore[arg-type]


def test_build_update_options_and_required_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Map json_output alias and detect missing required tools."""
    opts = _build_update_options({"source": "demo", "json_output": True, "check": True})
    assert opts.source == "demo"
    assert opts.json is True
    assert opts.check is True

    monkeypatch.setattr(
        "lib.update.cli.shutil.which",
        lambda tool: None if tool in {"flake-edit", "uv"} else "/bin/x",
    )
    monkeypatch.setattr(
        "lib.update.cli.UPDATERS",
        {"demo": type("_U", (), {"required_tools": ("nix",)})},
    )

    assert check_required_tools() == ["uv"]
    assert check_required_tools(needs_sources=False) == []
    assert check_required_tools(source="demo") == []
    assert check_required_tools(source="demo", include_flake_edit=True) == [
        "flake-edit"
    ]
    assert check_required_tools(source="unknown", needs_sources=True) == []


def test_tty_resolution_and_output_options(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Resolve tty modes and respect quiet/json output behavior."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert _is_tty(force_tty=True, no_tty=False, zellij_guard=False) is True
    assert _is_tty(force_tty=False, no_tty=True, zellij_guard=False) is False

    monkeypatch.setenv("ZELLIJ", "1")
    assert _is_tty(force_tty=False, no_tty=False, zellij_guard=True) is False

    monkeypatch.delenv("ZELLIJ", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    assert _is_tty(force_tty=False, no_tty=False, zellij_guard=False) is False

    monkeypatch.setenv("UPDATE_LOG_FULL", "1")
    assert _resolve_full_output() is True
    assert _resolve_full_output(full_output=False) is False

    out = OutputOptions(json_output=False, quiet=False)
    out.print("hello")
    out.print_error("bad")
    printed = capsys.readouterr()
    assert "hello" in printed.out
    assert "bad" in printed.err

    quiet_out = OutputOptions(json_output=True, quiet=True)
    quiet_out.print("hidden")
    quiet_out.print_error("also hidden")
    hidden = capsys.readouterr()
    assert hidden.out == ""
    assert hidden.err == ""


def test_update_summary_and_emit_summary(capsys: pytest.CaptureFixture[str]) -> None:
    """Accumulate status priorities and print human/json summaries."""
    summary = UpdateSummary()
    summary.accumulate({"a": "no_change", "b": "updated"})
    summary.accumulate({"a": "error"})
    assert summary.updated == ["b"]
    assert summary.errors == ["a"]
    assert summary.to_dict()["success"] is False

    code = _emit_summary(
        summary, had_errors=True, out=OutputOptions(json_output=True), dry_run=False
    )
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["errors"] == ["a"]

    summary_no_updates = UpdateSummary(updated=[], errors=[], no_change=[])
    code_no_updates = _emit_summary(
        summary_no_updates,
        had_errors=False,
        out=OutputOptions(json_output=False, quiet=False),
        dry_run=True,
    )
    assert code_no_updates == 0
    assert "No updates available" in capsys.readouterr().out


def test_resolved_targets_and_item_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve source/input selections and derive UI item metadata."""

    class _SrcUpdater:
        shows_materialize_artifacts_phase = True

    monkeypatch.setattr("lib.update.cli.UPDATERS", {"src": _SrcUpdater})
    monkeypatch.setattr(
        "lib.update.cli.get_flake_inputs_with_refs",
        lambda: [SimpleNamespace(name="inp", owner="o", repo="r", ref="v1")],
    )
    resolved = ResolvedTargets.from_options(UpdateOptions(source="src", no_refs=True))
    assert resolved.source_names == ["src"]
    assert resolved.ref_inputs == []

    sources = SourcesFile(entries={"src": SourceEntry(hashes={}, input="inp")})
    meta, order = _build_item_meta(resolved, sources)
    assert "src" in meta
    assert meta["src"].op_order == (
        OperationKind.CHECK_VERSION,
        OperationKind.REFRESH_LOCK,
        OperationKind.MATERIALIZE_ARTIFACTS,
        OperationKind.COMPUTE_HASH,
    )
    assert order == sorted(order)

    source_updates = {"src": SourceEntry(hashes={"x86_64-linux": "sha256-1"})}
    existing = {"src": SourceEntry(hashes={"aarch64-darwin": "sha256-2"})}
    merged = _merge_source_updates(existing, source_updates, native_only=True)
    assert "src" in merged


def test_resolved_targets_expand_flake_input_to_backing_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selecting a flake input should also select sources backed by that input."""

    class _OpencodeUpdater(FlakeInputUpdater):
        pass

    class _DesktopUpdater(FlakeInputUpdater):
        input_name = "opencode"

    class _ElectronUpdater(FlakeInputUpdater):
        input_name = "opencode"

    monkeypatch.setattr(
        "lib.update.cli.UPDATERS",
        {
            "opencode": _OpencodeUpdater,
            "opencode-desktop": _DesktopUpdater,
            "opencode-desktop-electron": _ElectronUpdater,
            "other": object,
        },
    )
    monkeypatch.setattr("lib.update.cli.get_flake_inputs_with_refs", list)

    resolved = ResolvedTargets.from_options(UpdateOptions(source="opencode"))
    assert resolved.ref_inputs == []
    assert resolved.source_names == [
        "opencode",
        "opencode-desktop",
        "opencode-desktop-electron",
    ]

    resolved_no_refs = ResolvedTargets.from_options(
        UpdateOptions(source="opencode", no_refs=True)
    )
    assert resolved_no_refs.ref_inputs == []
    assert resolved_no_refs.source_names == resolved.source_names


def test_resolved_targets_expand_primary_source_to_companion_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selecting a primary source should also select its managed companions."""

    class _CodexUpdater(FlakeInputUpdater):
        pass

    class _CodexV8Updater(HashEntryUpdater):
        companion_of = "codex"

    class _CodexOtherUpdater(HashEntryUpdater):
        companion_of = "codex"

    monkeypatch.setattr(
        "lib.update.cli.UPDATERS",
        {
            "codex": _CodexUpdater,
            "codex-v8": _CodexV8Updater,
            "codex-other": _CodexOtherUpdater,
        },
    )
    monkeypatch.setattr("lib.update.cli.get_flake_inputs_with_refs", list)

    resolved = ResolvedTargets.from_options(UpdateOptions(source="codex", no_refs=True))

    assert resolved.ref_inputs == []
    assert resolved.source_names == ["codex", "codex-other", "codex-v8"]

    direct_companion = ResolvedTargets.from_options(
        UpdateOptions(source="codex-v8", no_refs=True)
    )

    assert direct_companion.ref_inputs == []
    assert direct_companion.source_names == ["codex", "codex-v8"]


def test_preflight_handlers_schema_list_validate(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Handle schema/list/validate preflight paths before runtime execution."""
    assert _handle_schema_request(UpdateOptions(schema=False)) is None
    schema_code = _handle_schema_request(UpdateOptions(schema=True))
    assert schema_code == 0
    assert "$defs" in capsys.readouterr().out

    inventory = [
        _InventoryTarget(
            name="i",
            handles=_InventoryHandles(
                ref_update=True,
                input_refresh=False,
                source_update=False,
                artifact_write=False,
            ),
            classification="refOnly",
            backing_input="i",
            ref_target=_InventoryRefTarget(
                input_name="i",
                source_type="github",
                owner="o",
                repo="r",
                selector="v1",
                locked_rev="deadbeef",
            ),
            source_target=None,
            generated_artifacts=(),
        ),
        _InventoryTarget(
            name="a",
            handles=_InventoryHandles(
                ref_update=False,
                input_refresh=False,
                source_update=True,
                artifact_write=False,
            ),
            classification="sourceOnly",
            backing_input=None,
            ref_target=None,
            source_target=_InventorySourceTarget(
                path="packages/a/sources.json",
                version="1.0.0",
                commit=None,
                hash_kinds=("sha256",),
                updater_kind="download",
                updater_class="AUpdater",
            ),
            generated_artifacts=(),
        ),
        _InventoryTarget(
            name="b",
            handles=_InventoryHandles(
                ref_update=False,
                input_refresh=True,
                source_update=True,
                artifact_write=False,
            ),
            classification="sourceWithInputRefresh",
            backing_input="b-input",
            ref_target=None,
            source_target=_InventorySourceTarget(
                path="packages/b/sources.json",
                version="2.0.0",
                commit=None,
                hash_kinds=("sha256",),
                updater_kind="custom-hash",
                updater_class="BUpdater",
            ),
            generated_artifacts=(),
        ),
    ]
    monkeypatch.setattr(
        "lib.update.cli_inventory.build_update_inventory",
        lambda *, dependencies: inventory,
    )
    list_code = _handle_list_targets_request(
        UpdateOptions(list_targets=True, json=True)
    )
    assert list_code == 0
    list_payload = json.loads(capsys.readouterr().out)
    assert list_payload["schemaVersion"] == 1
    assert list_payload["kind"] == "nixcfg-update-inventory"
    assert [item["name"] for item in list_payload["targets"]] == ["a", "b", "i"]
    assert list_payload["summary"]["counts"]["sourceOnly"] == 1

    sorted_by_type_code = _handle_list_targets_request(
        UpdateOptions(list_targets=True, json=True, sort_by="type")
    )
    assert sorted_by_type_code == 0
    sorted_by_type_payload = json.loads(capsys.readouterr().out)
    assert [item["name"] for item in sorted_by_type_payload["targets"]] == [
        "i",
        "a",
        "b",
    ]

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
    assert validate_code == 0
    validate_payload = json.loads(capsys.readouterr().out)
    assert validate_payload["valid"] is True

    def _boom() -> None:
        msg = "nope"
        raise RuntimeError(msg)

    monkeypatch.setattr("lib.update.cli.validate_source_discovery_consistency", _boom)
    validate_err = _handle_validate_request(
        UpdateOptions(validate=True, json=True), OutputOptions(json_output=True)
    )
    assert validate_err == 1
    err_payload = json.loads(capsys.readouterr().out)
    assert err_payload["valid"] is False


def test_handle_preflight_requests_checks_schema_list_then_validate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run preflight handlers in order and stop at the first non-None result."""
    calls: list[str] = []

    monkeypatch.setattr(
        "lib.update.cli.validate_list_sort_option",
        lambda _opts, _out: calls.append("sort") or None,
    )
    monkeypatch.setattr(
        "lib.update.cli._handle_schema_request",
        lambda _opts: calls.append("schema") or None,
    )
    monkeypatch.setattr(
        "lib.update.cli._handle_list_targets_request",
        lambda _opts: calls.append("list") or None,
    )
    monkeypatch.setattr(
        "lib.update.cli._handle_validate_request",
        lambda _opts, _out: calls.append("validate") or 9,
    )

    assert _handle_preflight_requests(UpdateOptions(), OutputOptions()) == 9
    assert calls == ["sort", "schema", "list", "validate"]

    calls.clear()
    monkeypatch.setattr(
        "lib.update.cli._handle_schema_request",
        lambda _opts: calls.append("schema") or 4,
    )
    assert _handle_preflight_requests(UpdateOptions(), OutputOptions()) == 4
    assert calls == ["sort", "schema"]

    calls.clear()
    monkeypatch.setattr(
        "lib.update.cli._handle_schema_request",
        lambda _opts: calls.append("schema") or None,
    )
    monkeypatch.setattr(
        "lib.update.cli._handle_list_targets_request",
        lambda _opts: calls.append("list") or 5,
    )
    assert _handle_preflight_requests(UpdateOptions(), OutputOptions()) == 5
    assert calls == ["sort", "schema", "list"]


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
                "wrapper": SimpleNamespace(inputs={"nixpkgs": "node-b"}),
                "node-b": SimpleNamespace(original=None, locked=None, inputs=None),
            }

    lock = _Lock()
    direct_node, direct_follows = update_cli_module.resolve_root_input_node(
        cast("FlakeLock", lock), "direct"
    )
    assert direct_node is lock.nodes["node-a"]
    assert direct_follows is None

    follows_node, follows_path = update_cli_module.resolve_root_input_node(
        cast("FlakeLock", lock), "follows"
    )
    assert follows_node is lock.nodes["node-b"]
    assert follows_path == "wrapper/nixpkgs"

    missing_node, missing_path = update_cli_module.resolve_root_input_node(
        cast("FlakeLock", lock), "missing"
    )
    assert missing_node is None
    assert missing_path is None

    unresolved_node, unresolved_path = update_cli_module.resolve_root_input_node(
        cast("FlakeLock", lock), "unresolved"
    )
    assert unresolved_node is None
    assert unresolved_path == "wrapper/missing"

    github_node = SimpleNamespace(
        original=SimpleNamespace(
            type="github", owner="owner", repo="repo", url=None, path=None
        ),
        locked=None,
    )
    assert (
        _flake_source_string(cast("FlakeLockNode", github_node), None)
        == "github:owner/repo"
    )
    url_node = SimpleNamespace(
        original=SimpleNamespace(
            type="git", owner=None, repo=None, url="https://x", path=None
        ),
        locked=None,
    )
    assert (
        _flake_source_string(cast("FlakeLockNode", url_node), None) == "git:https://x"
    )

    path_node = SimpleNamespace(
        original=SimpleNamespace(
            type="path", owner=None, repo=None, url=None, path="./local"
        ),
        locked=None,
    )
    assert (
        _flake_source_string(cast("FlakeLockNode", path_node), None) == "path:./local"
    )

    unknown_node = SimpleNamespace(
        original=SimpleNamespace(type=None, owner=None, repo=None, url=None, path=None),
        locked=None,
    )
    assert (
        _flake_source_string(cast("FlakeLockNode", unknown_node), "dep/nixpkgs")
        == "follows:dep/nixpkgs"
    )
    url_only_node = SimpleNamespace(
        original=SimpleNamespace(type=None, owner=None, repo=None, url=None, path=None),
        locked=SimpleNamespace(
            type=None, owner=None, repo=None, url="https://plain", path=None
        ),
    )
    assert (
        _flake_source_string(cast("FlakeLockNode", url_only_node), None)
        == "https://plain"
    )
    path_only_node = SimpleNamespace(
        original=SimpleNamespace(type=None, owner=None, repo=None, url=None, path=None),
        locked=SimpleNamespace(
            type=None, owner=None, repo=None, url=None, path="./plain"
        ),
    )
    assert (
        _flake_source_string(cast("FlakeLockNode", path_only_node), None) == "./plain"
    )

    type_only_node = SimpleNamespace(
        original=SimpleNamespace(type=None, owner=None, repo=None, url=None, path=None),
        locked=SimpleNamespace(
            type="tarball", owner=None, repo=None, url=None, path=None
        ),
    )
    assert (
        _flake_source_string(cast("FlakeLockNode", type_only_node), None) == "tarball"
    )
    assert _flake_source_string(None, None) == "<unknown>"


def test_collect_flake_inputs_for_list() -> None:
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

    rows = _collect_flake_inputs_for_list(
        load_lock=_Lock,
        resolve_root_input_node=update_cli_module.resolve_root_input_node,
        flake_source_string=_flake_source_string,
        get_flake_input_version=lambda node: (
            "unknown"
            if node is not None and getattr(node.locked, "rev", None) == "rev4"
            else "inferred-version"
            if node is not None
            else "unknown"
        ),
    )
    by_name = {row.name: row for row in rows}

    assert by_name["with-ref"].item_type == "flake"
    assert by_name["with-ref"].ref == "v1"
    assert by_name["with-ref"].rev == "rev1"
    assert by_name["with-selector"].ref == "selector-ref"
    assert by_name["with-inferred"].ref == "inferred-version"
    assert by_name["unknown-inferred"].ref is None
    assert by_name["missing-node"].ref is None


def test_collect_source_entries_for_list() -> None:
    """Collect table rows for sources.json entries and source paths."""
    rows = _collect_source_entries_for_list(
        load_sources=lambda: SourcesFile(
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
        source_path_map=lambda _filename: {
            "inside": REPO_ROOT / "packages" / "inside" / "sources.json",
            "outside": Path("/tmp/outside/sources.json"),
            "no-url": REPO_ROOT / "overlays" / "no-url" / "sources.json",
        },
    )
    by_name = {row.name: row for row in rows}

    assert by_name["inside"].item_type == "sources.json"
    assert by_name["inside"].source == "https://example.com/inside.tgz"
    assert by_name["inside"].ref == "1.0.0"
    assert by_name["inside"].rev is None
    assert (
        by_name["outside"].source == "https://example.com/outside-linux.tgz (+1 more)"
    )
    assert by_name["outside"].rev == "d" * 40
    assert by_name["no-url"].source == "<none>"


def test_row_sort_value_variants() -> None:
    """Sort key helper should return the selected column value."""
    row = _ListRow(
        name="demo",
        item_type="flake",
        source="https://example.com/demo.tgz",
        ref="v1.2.3",
        rev="a" * 40,
    )
    assert _row_sort_value(row, "name") == "demo"
    assert _row_sort_value(row, "type") == "flake"
    assert _row_sort_value(row, "source") == "https://example.com/demo.tgz"
    assert _row_sort_value(row, "ref") == "v1.2.3"
    assert _row_sort_value(row, "rev") == "a" * 40


def test_inventory_helpers_and_sorting() -> None:  # noqa: PLR0915
    """Cover inventory helper branches, labels, and sort aliases."""

    class _FlakeHash(FlakeInputHashUpdater):
        name = "flake-hash"
        hash_type = "vendorHash"

    class _Deno(DenoManifestUpdater):
        name = "deno"

    class _Download(DownloadHashUpdater):
        name = "download"
        PLATFORMS: ClassVar[dict[str, str]] = {
            "x86_64-linux": "https://example.com/pkg.tgz"
        }

    class _Checksum(ChecksumProvidedUpdater):
        name = "checksum"
        PLATFORMS: ClassVar[dict[str, str]] = {"x86_64-linux": "linux"}

    class _Platform(PlatformAPIUpdater):
        name = "platform"
        PLATFORMS: ClassVar[dict[str, str]] = {"x86_64-linux": "linux"}

    class _HashEntry(HashEntryUpdater):
        name = "hash-entry"

    class _ExplicitInput(HashEntryUpdater):
        name = "explicit"
        input_name = "explicit-input"

    class _Custom(Updater):
        name = "custom"

    class _CustomArtifact(Updater):
        name = "custom-artifact"
        generated_artifact_files = ("generated.nix",)

    class _UvLock(UvLockUpdater):
        name = "uv-lock"

    class _CustomUvLock(UvLockUpdater):
        name = "custom-uv-lock"
        lock_file = "pinned.lock"

    def _handles(
        *,
        ref_update: bool,
        input_refresh: bool,
        source_update: bool,
        artifact_write: bool,
    ) -> _InventoryHandles:
        return _InventoryHandles(
            ref_update=ref_update,
            input_refresh=input_refresh,
            source_update=source_update,
            artifact_write=artifact_write,
        )

    entry_with_input = SourceEntry(hashes={}, input="from-entry")
    assert _source_backing_input_name("flake-hash", _FlakeHash) == "flake-hash"
    assert _source_backing_input_name("explicit", _ExplicitInput) == "explicit-input"
    assert _source_backing_input_name("deno", _Deno) == "deno"
    assert (
        _source_backing_input_name("fallback", None, entry_with_input) == "from-entry"
    )
    assert _source_backing_input_name("none", None) is None

    entry_hashes = SourceEntry(
        hashes=HashCollection(entries=[HashEntry.create("vendorHash", "sha256-abc=")])
    )
    mapping_hashes = SourceEntry(hashes={"x86_64-linux": "sha256-def="})
    empty_hashes = SourceEntry(hashes=HashCollection())
    assert _source_hash_kinds(entry_hashes) == ("vendorHash",)
    assert _source_hash_kinds(mapping_hashes) == ("sha256",)
    assert _source_hash_kinds(empty_hashes) == ()
    assert _source_hash_kinds(None) == ()

    assert _classify_updater_kind(_Deno) == "deno-manifest"
    assert _classify_updater_kind(_FlakeHash) == "flake-input-hash"
    assert _classify_updater_kind(_Platform) == "platform-api"
    assert _classify_updater_kind(_Checksum) == "checksum-api"
    assert _classify_updater_kind(_Download) == "download"
    assert _classify_updater_kind(_HashEntry) == "custom-hash"
    assert _classify_updater_kind(_Custom) == "custom-hash"

    def repo_relative_path(path: Path | None) -> str | None:
        return _repo_relative_path(
            path,
            repo_root=lambda: Path(REPO_ROOT),
        )

    assert _generated_artifact_paths(
        "deno",
        _Deno,
        package_dir_for=lambda name: (
            None if name == "missing" else REPO_ROOT / "packages" / name
        ),
        repo_relative_path=repo_relative_path,
    ) == ("packages/deno/deno-deps.json",)
    assert (
        _generated_artifact_paths(
            "missing",
            _Deno,
            package_dir_for=lambda name: (
                None if name == "missing" else REPO_ROOT / "packages" / name
            ),
            repo_relative_path=repo_relative_path,
        )
        == ()
    )
    assert (
        _generated_artifact_paths(
            "custom",
            _Custom,
            package_dir_for=lambda name: (
                None if name == "missing" else REPO_ROOT / "packages" / name
            ),
            repo_relative_path=repo_relative_path,
        )
        == ()
    )
    assert (
        _generated_artifact_paths(
            "duplicate-name",
            _Custom,
            package_dir_for=lambda _name: (_ for _ in ()).throw(
                RuntimeError("Duplicate package directories")
            ),
            repo_relative_path=repo_relative_path,
        )
        == ()
    )
    assert _generated_artifact_paths(
        "custom-artifact",
        _CustomArtifact,
        package_dir_for=lambda name: (
            None if name == "missing" else REPO_ROOT / "packages" / name
        ),
        repo_relative_path=repo_relative_path,
    ) == ("packages/custom-artifact/generated.nix",)
    assert (
        _generated_artifact_paths(
            "custom-artifact",
            _CustomArtifact,
            package_dir_for=lambda name: (
                None if name == "missing" else REPO_ROOT / "packages" / name
            ),
            repo_relative_path=lambda _path: None,
        )
        == ()
    )
    assert _generated_artifact_paths(
        "uv-lock",
        _UvLock,
        package_dir_for=lambda name: (
            None if name == "missing" else REPO_ROOT / "packages" / name
        ),
        repo_relative_path=repo_relative_path,
    ) == ("packages/uv-lock/uv.lock",)
    assert _generated_artifact_paths(
        "custom-uv-lock",
        _CustomUvLock,
        package_dir_for=lambda name: (
            None if name == "missing" else REPO_ROOT / "packages" / name
        ),
        repo_relative_path=repo_relative_path,
    ) == ("packages/custom-uv-lock/pinned.lock",)

    assert (
        repo_relative_path(REPO_ROOT / "packages" / "demo" / "sources.json")
        == "packages/demo/sources.json"
    )
    assert (
        repo_relative_path(Path("/tmp/outside/sources.json"))
        == "/tmp/outside/sources.json"
    )
    assert repo_relative_path(None) is None

    assert (
        _inventory_classification(
            _handles(
                ref_update=True,
                input_refresh=True,
                source_update=True,
                artifact_write=False,
            )
        )
        == "refAndSourceWithInputRefresh"
    )
    assert (
        _inventory_classification(
            _handles(
                ref_update=False,
                input_refresh=True,
                source_update=True,
                artifact_write=False,
            )
        )
        == "sourceWithInputRefresh"
    )
    assert (
        _inventory_classification(
            _handles(
                ref_update=True,
                input_refresh=False,
                source_update=True,
                artifact_write=False,
            )
        )
        == "refAndSource"
    )
    assert (
        _inventory_classification(
            _handles(
                ref_update=False,
                input_refresh=False,
                source_update=True,
                artifact_write=False,
            )
        )
        == "sourceOnly"
    )
    assert (
        _inventory_classification(
            _handles(
                ref_update=True,
                input_refresh=False,
                source_update=False,
                artifact_write=False,
            )
        )
        == "refOnly"
    )
    assert (
        _inventory_classification(
            _handles(
                ref_update=False,
                input_refresh=False,
                source_update=False,
                artifact_write=False,
            )
        )
        == "unclassified"
    )
    target = _InventoryTarget(
        name="demo",
        handles=_handles(
            ref_update=True,
            input_refresh=True,
            source_update=True,
            artifact_write=True,
        ),
        classification="refAndSourceWithInputRefresh",
        backing_input="shared-input",
        ref_target=_InventoryRefTarget(
            input_name="shared-input",
            source_type="github",
            owner="o",
            repo="r",
            selector="v1.2.3",
            locked_rev="deadbeef",
        ),
        source_target=_InventorySourceTarget(
            path="packages/demo/sources.json",
            version="1.2.3",
            commit="a" * 40,
            hash_kinds=("sha256", "vendorHash"),
            updater_kind="deno-manifest",
            updater_class="DemoUpdater",
        ),
        generated_artifacts=("packages/demo/deno-deps.json",),
    )
    assert target.handles.touch_labels() == ("ref", "lock", "sources", "art")
    assert target.selector_value() == "v1.2.3"
    assert target.revision_value() == "deadbeef"
    assert target.source_value() == "shared-input"
    assert target.write_labels() == ("flake.lock", "sources.json", "deno-deps.json")
    assert target.classification_label() == "ref+source+input"
    target_dict = target.to_dict()
    assert target_dict["backingInput"] == "shared-input"
    assert target_dict["generatedArtifacts"] == ["packages/demo/deno-deps.json"]

    source_only_target = _InventoryTarget(
        name="source-only",
        handles=_handles(
            ref_update=False,
            input_refresh=False,
            source_update=True,
            artifact_write=False,
        ),
        classification="sourceOnly",
        backing_input=None,
        ref_target=None,
        source_target=_InventorySourceTarget(
            path=None,
            version="2.0.0",
            commit="b" * 40,
            hash_kinds=(),
            updater_kind="custom-hash",
            updater_class="SourceOnlyUpdater",
        ),
        generated_artifacts=(),
    )
    assert source_only_target.handles.touch_labels() == ("sources",)
    assert source_only_target.selector_value() == "2.0.0"
    assert source_only_target.revision_value() == "b" * 40
    assert source_only_target.write_labels() == ("sources.json",)

    path_source_target = _InventoryTarget(
        name="path-source",
        handles=_handles(
            ref_update=False,
            input_refresh=False,
            source_update=True,
            artifact_write=False,
        ),
        classification="sourceOnly",
        backing_input=None,
        ref_target=None,
        source_target=_InventorySourceTarget(
            path="packages/path-source/sources.json",
            version="3.0.0",
            commit=None,
            hash_kinds=(),
            updater_kind="download",
            updater_class="PathSourceUpdater",
        ),
        generated_artifacts=(),
    )
    assert path_source_target.source_value() == "packages/path-source/sources.json"

    ref_only_target = _InventoryTarget(
        name="ref-only",
        handles=_handles(
            ref_update=True,
            input_refresh=False,
            source_update=False,
            artifact_write=False,
        ),
        classification="refOnly",
        backing_input=None,
        ref_target=_InventoryRefTarget(
            input_name="ref-only",
            source_type="github",
            owner="o",
            repo="r",
            selector="v9.9.9",
            locked_rev=None,
        ),
        source_target=None,
        generated_artifacts=(),
    )
    assert ref_only_target.source_value() == "github:o/r"
    assert ref_only_target.classification_label() == "ref"
    weird_target = _InventoryTarget(
        name="weird",
        handles=_handles(
            ref_update=False,
            input_refresh=False,
            source_update=False,
            artifact_write=False,
        ),
        classification="unclassified",
        backing_input=None,
        ref_target=None,
        source_target=None,
        generated_artifacts=(),
    )
    assert weird_target.handles.touch_labels() == ()
    assert weird_target.selector_value() is None
    assert weird_target.revision_value() is None
    assert weird_target.source_value() == ""
    assert weird_target.write_labels() == ()
    assert weird_target.classification_label() == "unclassified"
    assert weird_target.to_dict()["refTarget"] is None
    assert weird_target.to_dict()["sourceTarget"] is None

    counts = _build_inventory_summary([
        target,
        source_only_target,
        ref_only_target,
        weird_target,
    ])
    assert counts["totalTargets"] == 4
    counts_map = counts["counts"]
    if not isinstance(counts_map, dict):
        raise AssertionError
    assert counts_map["refOnly"] == 1
    assert counts_map["sourceOnly"] == 1
    assert counts_map["refAndSource"] == 0
    assert counts_map["refAndSourceWithInputRefresh"] == 1
    assert counts_map["unclassified"] == 1

    assert _inventory_sort_value(target, "name") == "demo"
    assert _inventory_sort_value(target, "type") == "refAndSourceWithInputRefresh"
    assert (
        _inventory_sort_value(target, "classification")
        == "refAndSourceWithInputRefresh"
    )
    assert _inventory_sort_value(target, "source") == "shared-input"
    assert _inventory_sort_value(target, "input") == "shared-input"
    assert _inventory_sort_value(target, "ref") == "v1.2.3"
    assert _inventory_sort_value(target, "version") == "v1.2.3"
    assert _inventory_sort_value(target, "rev") == "deadbeef"
    assert _inventory_sort_value(target, "commit") == "deadbeef"
    assert _inventory_sort_value(target, "touches") == "ref,lock,sources,art"
    assert (
        _inventory_sort_value(target, "writes")
        == "flake.lock,sources.json,deno-deps.json"
    )


def test_build_update_inventory_uses_logical_targets() -> None:
    """Build logical inventory entries from updater/ref metadata."""

    class _BothUpdater(FlakeInputHashUpdater):
        name = "both"
        hash_type = "vendorHash"

    class _DesktopUpdater(FlakeInputUpdater, HashEntryUpdater):
        name = "desktop"
        input_name = "shared-input"

    class _DenoUpdater(DenoManifestUpdater):
        name = "deno"

    sources = SourcesFile(
        entries={
            "both": SourceEntry(
                version="v1.0.0",
                hashes=[HashEntry.create("vendorHash", "sha256-ghi=")],
            ),
            "desktop": SourceEntry(
                hashes=[HashEntry.create("sha256", "sha256-jkl=")],
                commit="b" * 40,
            ),
            "deno": SourceEntry(
                version="v9.9.9",
                hashes={"x86_64-linux": "sha256-mno="},
            ),
        }
    )

    class _Lock:
        def __init__(self) -> None:
            self.root_node = SimpleNamespace(
                inputs={"both": "node-both", "ref-only": "node-ref"}
            )
            self.nodes = {
                "node-both": SimpleNamespace(locked=SimpleNamespace(rev="rev-both")),
            }

        def _resolve_target_node_name(self, input_name: str) -> str | None:
            _ = input_name
            return None

    def repo_relative_path(path: Path | None) -> str | None:
        return _repo_relative_path(
            path,
            repo_root=lambda: Path(REPO_ROOT),
        )

    targets = cli_inventory_module.build_update_inventory(
        dependencies=cli_inventory_module.InventoryDependencies(
            load_sources=lambda: sources,
            source_path_map=lambda _filename: {
                "both": REPO_ROOT / "packages" / "both" / "sources.json",
                "desktop": REPO_ROOT / "packages" / "desktop" / "sources.json",
            },
            list_ref_inputs=lambda: [
                FlakeInputRef(
                    name="both",
                    owner="o",
                    repo="r",
                    ref="v1.0.0",
                    input_type="github",
                ),
                FlakeInputRef(
                    name="ref-only",
                    owner="o",
                    repo="r",
                    ref="v3.0.0",
                    input_type="github",
                ),
            ],
            load_lock=_Lock,
            get_updaters=lambda: {
                "both": _BothUpdater,
                "desktop": _DesktopUpdater,
                "deno": _DenoUpdater,
            },
            source_file_for=lambda name: REPO_ROOT / "packages" / name / "sources.json",
            resolve_root_input_node=update_cli_module.resolve_root_input_node,
            source_backing_input_name=_source_backing_input_name,
            generated_artifact_paths=lambda name, updater_cls: (
                _generated_artifact_paths(
                    name,
                    updater_cls,
                    package_dir_for=lambda package_name: (
                        REPO_ROOT / "packages" / package_name
                    ),
                    repo_relative_path=repo_relative_path,
                )
            ),
            source_hash_kinds=_source_hash_kinds,
            classify_updater_kind=_classify_updater_kind,
            repo_relative_path=repo_relative_path,
        )
    )
    by_name = {target.name: target for target in targets}

    assert [target.name for target in targets] == [
        "both",
        "deno",
        "desktop",
        "ref-only",
    ]
    assert by_name["both"].classification == "refAndSourceWithInputRefresh"
    assert by_name["both"].backing_input == "both"
    assert by_name["both"].ref_target is not None
    assert by_name["both"].ref_target.locked_rev == "rev-both"
    assert by_name["both"].source_target is not None
    assert by_name["both"].source_target.path == "packages/both/sources.json"
    assert by_name["both"].source_target.updater_kind == "flake-input-hash"

    assert by_name["desktop"].classification == "sourceWithInputRefresh"
    assert by_name["desktop"].backing_input == "shared-input"
    assert by_name["desktop"].ref_target is None
    assert by_name["desktop"].source_target is not None
    assert by_name["desktop"].source_target.commit == "b" * 40
    assert by_name["desktop"].source_target.updater_kind == "custom-hash"

    assert by_name["deno"].classification == "sourceWithInputRefresh"
    assert by_name["deno"].generated_artifacts == ("packages/deno/deno-deps.json",)
    assert by_name["deno"].source_target is not None
    assert by_name["deno"].source_target.path == "packages/deno/sources.json"
    assert by_name["deno"].source_target.hash_kinds == ("sha256",)
    assert by_name["deno"].source_target.updater_kind == "deno-manifest"

    assert by_name["ref-only"].classification == "refOnly"
    assert by_name["ref-only"].backing_input == "ref-only"
    assert by_name["ref-only"].source_target is None
    assert by_name["ref-only"].ref_target is not None
    assert by_name["ref-only"].ref_target.locked_rev is None


def test_build_update_inventory_wrapper_builds_dependency_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Build the inventory through the shared dependency-bundle API."""
    captured: dict[str, object] = {}

    def _fake_build_update_inventory(*, dependencies: object) -> list[object]:
        captured["dependencies"] = dependencies
        return ["wrapped"]

    monkeypatch.setattr(
        "lib.update.cli.build_update_inventory",
        _fake_build_update_inventory,
    )

    assert _build_update_inventory() == ["wrapped"]
    monkeypatch.setattr(
        "lib.update.cli.package_dir_for",
        lambda name: REPO_ROOT / "packages" / name,
    )

    class _DenoUpdater(DenoManifestUpdater):
        name = "demo"

    dependencies = captured["dependencies"]
    assert isinstance(dependencies, cli_inventory_module.InventoryDependencies)
    assert dependencies.load_sources is update_cli_module.load_all_sources
    assert dependencies.source_path_map is update_cli_module.package_file_map
    assert dependencies.list_ref_inputs is update_cli_module.get_flake_inputs_with_refs
    assert dependencies.load_lock is update_cli_module.load_flake_lock
    assert dependencies.get_updaters is update_cli_module._get_updaters
    assert dependencies.source_file_for is update_cli_module.sources_file_for
    assert (
        dependencies.resolve_root_input_node
        is update_cli_module.resolve_root_input_node
    )
    assert (
        dependencies.source_backing_input_name
        is cli_inventory_module._source_backing_input_name
    )
    assert dependencies.source_hash_kinds is cli_inventory_module._source_hash_kinds
    assert (
        dependencies.classify_updater_kind
        is cli_inventory_module._classify_updater_kind
    )
    assert (
        dependencies.repo_relative_path(
            REPO_ROOT / "packages" / "demo" / "sources.json"
        )
        == "packages/demo/sources.json"
    )
    assert (
        dependencies.repo_relative_path(Path("/tmp/outside/sources.json"))
        == "/tmp/outside/sources.json"
    )
    assert dependencies.generated_artifact_paths("demo", _DenoUpdater) == (
        "packages/demo/deno-deps.json",
    )


def test_generated_artifact_paths_include_crate2nix_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Surface crate2nix outputs alongside updater-declared generated artifacts."""

    class _DenoUpdater(DenoManifestUpdater):
        name = "demo"

    fake_module = SimpleNamespace(
        TARGETS={
            "demo": SimpleNamespace(
                cargo_nix=Path("packages/demo/Cargo.nix"),
                crate_hashes=Path("packages/demo/crate-hashes.json"),
            )
        }
    )
    monkeypatch.setattr(
        "lib.update.cli_inventory.importlib.import_module",
        lambda name: fake_module if name == "lib.update.crate2nix" else None,
    )

    assert _crate2nix_generated_artifact_paths("demo") == (
        "packages/demo/Cargo.nix",
        "packages/demo/crate-hashes.json",
    )

    assert _generated_artifact_paths(
        "demo",
        _DenoUpdater,
        package_dir_for=lambda _name: REPO_ROOT / "packages" / "demo",
        repo_relative_path=lambda path: (
            None if path is None else str(path.relative_to(REPO_ROOT))
        ),
    ) == (
        "packages/demo/deno-deps.json",
        "packages/demo/Cargo.nix",
        "packages/demo/crate-hashes.json",
    )


def test_generated_artifact_paths_fall_back_when_manifest_or_crate2nix_import_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep crate2nix-only outputs when manifest resolution or import loading fails."""

    class _DenoUpdater(DenoManifestUpdater):
        name = "demo"

    monkeypatch.setattr(
        "lib.update.cli_inventory.importlib.import_module",
        lambda _name: (_ for _ in ()).throw(ImportError),
    )
    assert _crate2nix_generated_artifact_paths("demo") == ()

    fake_module = SimpleNamespace(
        TARGETS={
            "demo": SimpleNamespace(
                cargo_nix=Path("packages/demo/Cargo.nix"),
                crate_hashes=Path("packages/demo/crate-hashes.json"),
            )
        }
    )
    monkeypatch.setattr(
        "lib.update.cli_inventory.importlib.import_module",
        lambda name: fake_module if name == "lib.update.crate2nix" else None,
    )

    assert _generated_artifact_paths(
        "demo",
        _DenoUpdater,
        package_dir_for=lambda _name: REPO_ROOT / "packages" / "demo",
        repo_relative_path=lambda _path: None,
    ) == (
        "packages/demo/Cargo.nix",
        "packages/demo/crate-hashes.json",
    )


def test_build_item_meta_appends_materialize_artifacts_when_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Schedule artifact materialization in both mixed and source-only flows."""

    class _ArtifactOnlyUpdater:
        emits_crate2nix_artifacts = True
        shows_materialize_artifacts_phase = True

    class _BothUpdater(FlakeInputMetadataUpdater):
        name = "both-src"
        emits_crate2nix_artifacts = True
        shows_materialize_artifacts_phase = True
        input_name = "both-input"

    class _MetadataUpdater(FlakeInputMetadataUpdater):
        name = "meta-src"
        emits_crate2nix_artifacts = True
        shows_materialize_artifacts_phase = True
        input_name = "flake-src"

    monkeypatch.setattr(
        "lib.update.cli.UPDATERS",
        {
            "artifact-src": _ArtifactOnlyUpdater,
            "both-src": _BothUpdater,
            "meta-src": _MetadataUpdater,
        },
    )

    resolved = ResolvedTargets(
        all_source_names={"artifact-src", "both-src", "meta-src"},
        all_ref_inputs=[
            FlakeInputRef(
                name="both-src",
                owner="owner",
                repo="repo",
                ref="v1.0.0",
                input_type="github",
            )
        ],
        all_ref_names={"both-src"},
        all_known_names={"artifact-src", "both-src", "meta-src"},
        do_refs=True,
        do_sources=True,
        do_input_refresh=True,
        dry_run=False,
        native_only=False,
        ref_inputs=[
            FlakeInputRef(
                name="both-src",
                owner="owner",
                repo="repo",
                ref="v1.0.0",
                input_type="github",
            )
        ],
        source_names=["artifact-src", "both-src", "meta-src"],
    )
    sources = SourcesFile(
        entries={
            "both-src": SourceEntry(hashes={}, input="both-input"),
            "meta-src": SourceEntry(hashes={}, input="flake-src"),
        }
    )

    meta, _order = _build_item_meta(resolved, sources)

    assert meta["artifact-src"].op_order == (
        OperationKind.CHECK_VERSION,
        OperationKind.MATERIALIZE_ARTIFACTS,
        OperationKind.COMPUTE_HASH,
    )
    assert meta["both-src"].op_order == (
        OperationKind.CHECK_VERSION,
        OperationKind.UPDATE_REF,
        OperationKind.REFRESH_LOCK,
        OperationKind.MATERIALIZE_ARTIFACTS,
        OperationKind.COMPUTE_HASH,
    )
    assert meta["meta-src"].op_order == (
        OperationKind.CHECK_VERSION,
        OperationKind.REFRESH_LOCK,
        OperationKind.MATERIALIZE_ARTIFACTS,
        OperationKind.COMPUTE_HASH,
    )


def test_handle_validate_request_wrapper_builds_dependency_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forward validation through the shared dependency-bundle API."""
    captured: dict[str, object] = {}

    def _fake_handle_validate_request(
        opts: UpdateOptions,
        out: OutputOptions,
        *,
        dependencies: object,
    ) -> int:
        captured["opts"] = opts
        captured["out"] = out
        captured["dependencies"] = dependencies
        return 7

    monkeypatch.setattr(
        "lib.update.cli.handle_validate_request",
        _fake_handle_validate_request,
    )

    opts = UpdateOptions(validate=True)
    out = OutputOptions(json_output=True)

    assert _handle_validate_request(opts, out) == 7

    dependencies = captured["dependencies"]
    assert captured["opts"] is opts
    assert captured["out"] is out
    assert isinstance(dependencies, cli_validation_module.ValidationDependencies)
    assert dependencies.load_sources is update_cli_module.load_all_sources
    assert (
        dependencies.validate_source_discovery_consistency
        is update_cli_module.validate_source_discovery_consistency
    )


def test_runtime_config_and_tty_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve runtime config and evaluate header display toggles."""
    captured: dict[str, object] = {}

    def _resolve_config(**kwargs: object) -> object:
        captured.update(kwargs)
        return SimpleNamespace(default_log_tail_lines=10, default_render_interval=0.1)

    monkeypatch.setattr("lib.update.cli.resolve_config", _resolve_config)
    cfg = _resolve_runtime_config(UpdateOptions(http_timeout=3, retries=2))
    assert cfg.default_log_tail_lines == 10
    assert captured["http_timeout"] == 3
    assert captured["retries"] == 2

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
    assert tty_enabled is False
    assert show_headers is True


def test_get_updaters_falls_back_to_lazy_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Load the registry explicitly when the local alias is empty."""
    from lib.update import updaters as updater_module

    updater_module.UPDATERS.clear()
    monkeypatch.setattr("lib.update.cli.UPDATERS", updater_module.UPDATERS)
    monkeypatch.setattr(
        "lib.update.cli.ensure_updaters_loaded",
        lambda: {"demo": cast("type[object]", object)},
    )
    assert _get_updaters() == {"demo": object}


def test_sort_option_requires_list(capsys: pytest.CaptureFixture[str]) -> None:
    """Reject --sort/-o usage when --list is not enabled."""
    exit_code = _run_async(run_updates(UpdateOptions(sort_by="rev", json=True)))
    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is False
    assert "--sort/-o" in payload["error"]


def test_sort_option_requires_list_non_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Emit stderr validation error for --sort/-o without --list."""
    exit_code = _run_async(run_updates(UpdateOptions(sort_by="rev", json=False)))
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "--sort/-o" in captured.err


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
    assert _load_sources_for_run(resolved) is source_file
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
    assert _load_sources_for_run(resolved_none).entries == {}

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
    assert len(save_calls) == 1


def test_persist_generated_artifacts_and_materialized_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persist generated artifacts before sources and respect dry-run."""
    resolved = ResolvedTargets(
        all_source_names={"demo"},
        all_ref_inputs=[],
        all_ref_names=set(),
        all_known_names={"demo"},
        do_refs=False,
        do_sources=True,
        do_input_refresh=False,
        dry_run=False,
        native_only=False,
        ref_inputs=[],
        source_names=["demo"],
    )
    artifact = GeneratedArtifact.text("artifacts/demo.txt", "hello\n")

    saved_artifacts: list[list[GeneratedArtifact]] = []
    saved_sources: list[SourcesFile] = []
    monkeypatch.setattr(
        "lib.update.cli.save_generated_artifacts",
        lambda artifacts: saved_artifacts.append(list(artifacts)),
    )
    monkeypatch.setattr(
        "lib.update.cli.save_sources",
        lambda sources: saved_sources.append(sources),
    )

    _persist_generated_artifacts(
        resolved=resolved,
        artifact_updates={"demo": (artifact,)},
        details={"demo": "updated"},
    )
    assert saved_artifacts == [[artifact]]

    _persist_materialized_updates(
        resolved=resolved,
        sources=SourcesFile(entries={"demo": SourceEntry(hashes={})}),
        source_updates={"demo": SourceEntry(hashes={"x86_64-linux": "sha256-1"})},
        artifact_updates={"demo": (artifact,)},
        details={"demo": "updated"},
    )
    assert len(saved_artifacts) == 2
    assert len(saved_sources) == 1

    dry_run_resolved = ResolvedTargets(
        all_source_names={"demo"},
        all_ref_inputs=[],
        all_ref_names=set(),
        all_known_names={"demo"},
        do_refs=False,
        do_sources=True,
        do_input_refresh=False,
        dry_run=True,
        native_only=False,
        ref_inputs=[],
        source_names=["demo"],
    )
    _persist_generated_artifacts(
        resolved=dry_run_resolved,
        artifact_updates={"demo": (artifact,)},
        details={"demo": "updated"},
    )
    assert len(saved_artifacts) == 2

    skipped_resolved = ResolvedTargets(
        all_source_names={"demo"},
        all_ref_inputs=[],
        all_ref_names=set(),
        all_known_names={"demo"},
        do_refs=False,
        do_sources=False,
        do_input_refresh=False,
        dry_run=False,
        native_only=False,
        ref_inputs=[],
        source_names=["demo"],
    )
    _persist_generated_artifacts(
        resolved=skipped_resolved,
        artifact_updates={"demo": (artifact,)},
        details={"demo": "updated"},
    )
    assert len(saved_artifacts) == 2

    _persist_generated_artifacts(
        resolved=resolved,
        artifact_updates={"demo": (artifact,)},
        details={"demo": "error"},
    )
    assert len(saved_artifacts) == 2


def test_load_pinned_versions_and_run_plan_building(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Load pinned versions and build executable run plans."""
    out = OutputOptions(json_output=False, quiet=True)
    assert _load_pinned_versions(UpdateOptions(), out) == {}

    pinned_path = "/tmp/pinned.json"
    monkeypatch.setattr(
        "lib.update.cli.load_pinned_versions",
        lambda _path: {"a": SimpleNamespace(version="1", metadata={})},
    )
    loaded = _load_pinned_versions(UpdateOptions(pinned_versions=pinned_path), out)
    assert "a" in loaded

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
    assert not isinstance(plan, int)

    unknown = _build_run_plan(UpdateOptions(source="unknown"), OutputOptions())
    assert unknown == 1

    monkeypatch.setattr(
        "lib.update.cli._build_item_meta", lambda resolved, sources: ({}, [])
    )
    empty = _build_run_plan(UpdateOptions(), OutputOptions())
    assert empty == 0


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
    ) -> SimpleNamespace:
        _ = options
        while await queue.get() is not None:
            pass
        return SimpleNamespace(
            updated=True,
            errors=0,
            details={"src": "updated"},
            source_updates={
                "src": SourceEntry(
                    hashes={
                        "x86_64-linux": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
                    }
                )
            },
            artifact_updates={},
        )

    async def _run_ref_phase(**_kwargs: object) -> None:
        return None

    async def _run_sources_phase(*, context: object) -> None:
        _ = context

    monkeypatch.setattr("lib.update.cli.consume_events", _consume)
    monkeypatch.setattr("lib.update.cli._run_ref_phase", _run_ref_phase)
    monkeypatch.setattr("lib.update.cli._run_sources_phase", _run_sources_phase)
    monkeypatch.setattr(
        "lib.update.cli._persist_materialized_updates", lambda **_kwargs: None
    )

    cfg = SimpleNamespace(default_log_tail_lines=10, default_render_interval=0.1)
    exit_code = _run_async(
        _execute_run_plan(UpdateOptions(), OutputOptions(), cfg, plan)
    )
    assert exit_code == 0

    # run_updates preflight short-circuit
    monkeypatch.setattr("lib.update.cli._resolve_runtime_config", lambda _opts: cfg)
    monkeypatch.setattr(
        "lib.update.cli._handle_preflight_requests", lambda _opts, _out: 7
    )
    assert _run_async(run_updates(UpdateOptions())) == 7

    monkeypatch.setattr(
        "lib.update.cli._handle_preflight_requests", lambda _opts, _out: None
    )
    monkeypatch.setattr("lib.update.cli._build_run_plan", lambda _opts, _out: 3)
    assert _run_async(run_updates(UpdateOptions())) == 3

    monkeypatch.setattr("lib.update.cli._build_run_plan", lambda _opts, _out: plan)
    monkeypatch.setattr(
        "lib.update.cli._execute_run_plan",
        lambda _opts, _out, _cfg, _plan: asyncio.sleep(0, result=0),
    )
    assert _run_async(run_updates(UpdateOptions())) == 0

    # run_update_command tool checks and execution
    monkeypatch.setattr(
        "lib.update.cli.check_required_tools", lambda **_kwargs: ["nix"]
    )
    assert run_update_command() == 1

    monkeypatch.setattr("lib.update.cli.check_required_tools", lambda **_kwargs: [])
    monkeypatch.setattr(
        "lib.update.cli.run_updates", lambda _opts: asyncio.sleep(0, result=5)
    )
    assert run_update_command(list_targets=True) == 5


def test_cli_callback_raises_typer_exit_with_run_update_command_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expose the imperative command result through Typer's exit wrapper."""
    monkeypatch.setattr("lib.update.cli.run_update_command", lambda **_kwargs: 7)

    with pytest.raises(typer.Exit) as exc_info:
        cli()

    assert exc_info.value.exit_code == 7


def test_run_update_command_rejects_invalid_option_inputs() -> None:
    """Reject mixed invocation styles and non-UpdateOptions objects."""
    with pytest.raises(
        TypeError,
        match="run_update_command accepts either UpdateOptions or keyword overrides",
    ):
        run_update_command(UpdateOptions(), list_targets=True)

    with pytest.raises(TypeError, match="Expected UpdateOptions, got"):
        run_update_command(cast("UpdateOptions", object()))
