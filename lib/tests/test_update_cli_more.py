"""Additional tests for update CLI orchestration helpers."""

import asyncio
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, ClassVar, cast

import pytest
import typer

if TYPE_CHECKING:
    from lib.nix.models.flake_lock import FlakeLock

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry, SourcesFile
from lib.tests._run_updates_helpers import (
    configure_isolated_run,
    drain_events,
    make_run_plan,
)
from lib.tests._update_workspace_helpers import (
    init_update_workspace_repo as _init_update_workspace_repo,
)
from lib.update import cli_inventory as cli_inventory_module
from lib.update import crate2nix as update_crate2nix
from lib.update.artifacts import GeneratedArtifact
from lib.update.cli import (
    OutputOptions,
    ResolvedTargets,
    UpdateOptions,
    UpdateSummary,
    _argv_runs_top_level_update,
    _build_item_meta,
    _build_run_plan,
    _build_update_options,
    _emit_summary,
    _get_updaters,
    _handle_preflight_requests,
    _handle_schema_request,
    _is_tty,
    _load_sources_for_run,
    _maybe_reexec_checkout_update,
    _resolve_full_output,
    _resolve_runtime_config,
    _resolve_tty_settings,
    _split_trailing_target_options,
    _update_library_matches_checkout,
    check_required_tools,
    cli,
    run_update_command,
    run_updates,
)
from lib.update.cli_inventory import (
    _build_inventory_summary,
    _classify_updater_kind,
    _crate2nix_generated_artifact_paths,
    _generated_artifact_paths,
    _inventory_classification,
    _inventory_sort_value,
    _InventoryHandles,
    _InventoryRefTarget,
    _InventorySourceTarget,
    _InventoryTarget,
    _repo_relative_path,
    _source_hash_kinds,
    handle_list_targets_request,
)
from lib.update.cli_validation import handle_validate_request
from lib.update.flake import resolve_root_input_node
from lib.update.paths import REPO_ROOT
from lib.update.persistence import (
    IsolatedUpdateWorkspace,
    UpdateWorkspaceConflictError,
    UpdateWorkspaceError,
    UpdateWorkspaceUnexpectedPathsError,
    flatten_artifact_updates,
    merge_source_updates,
    persist_generated_artifacts,
    persist_materialized_updates,
    persist_source_updates,
    planned_update_paths,
)
from lib.update.planner import companion_source_name, source_backing_input_name
from lib.update.refs import FlakeInputRef
from lib.update.source_runner import UpdatePhaseResult
from lib.update.ui_state import OperationKind
from lib.update.updaters import (
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


def _write_update_file(root: Path, relative_path: str, content: str) -> None:
    path = root / "lib" / "update" / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_update_library_matches_checkout_same_and_equal_trees(tmp_path: Path) -> None:
    """Treat the current update tree and byte-identical copies as compatible."""
    repo_root = tmp_path / "repo"
    _write_update_file(repo_root, "cli.py", "VALUE = 1\n")

    assert _update_library_matches_checkout(
        repo_root,
        runtime_update_root=repo_root / "lib" / "update",
    )

    _write_update_file(tmp_path / "runtime-root", "cli.py", "VALUE = 1\n")
    runtime_root = tmp_path / "runtime-root" / "lib" / "update"

    assert _update_library_matches_checkout(repo_root, runtime_update_root=runtime_root)


def test_update_library_detects_missing_or_changed_checkout(tmp_path: Path) -> None:
    """Reject missing, differently shaped, or content-skewed update libraries."""
    repo_root = tmp_path / "repo"
    runtime_source_root = tmp_path / "runtime-root"
    runtime_root = runtime_source_root / "lib" / "update"

    assert not _update_library_matches_checkout(
        repo_root,
        runtime_update_root=runtime_root,
    )

    _write_update_file(repo_root, "cli.py", "VALUE = 1\n")
    _write_update_file(runtime_source_root, "other.py", "VALUE = 1\n")
    assert not _update_library_matches_checkout(
        repo_root, runtime_update_root=runtime_root
    )

    (runtime_root / "other.py").unlink()
    _write_update_file(runtime_source_root, "cli.py", "VALUE = 2\n")
    assert not _update_library_matches_checkout(
        repo_root, runtime_update_root=runtime_root
    )


def test_maybe_reexec_checkout_update_skips_non_update_or_matching(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Only top-level update commands with mismatched code trigger re-exec."""
    monkeypatch.setattr("lib.update.cli.sys.argv", ["nixcfg", "tree"])
    assert _maybe_reexec_checkout_update() is None

    monkeypatch.setattr("lib.update.cli.sys.argv", ["nixcfg", "update"])
    monkeypatch.setattr("lib.update.cli.get_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        "lib.update.cli._update_library_matches_checkout", lambda _: True
    )
    assert _maybe_reexec_checkout_update() is None


def test_maybe_reexec_checkout_update_reports_blockers(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Recursion and missing nix produce actionable errors instead of skewed imports."""
    monkeypatch.setattr("lib.update.cli.sys.argv", ["nixcfg", "update"])
    monkeypatch.setattr("lib.update.cli.get_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        "lib.update.cli._update_library_matches_checkout", lambda _: False
    )
    monkeypatch.setenv("NIXCFG_UPDATE_REEXECED_FROM_CHECKOUT", "1")

    assert _maybe_reexec_checkout_update() == 1
    assert "still differs" in capsys.readouterr().err

    monkeypatch.delenv("NIXCFG_UPDATE_REEXECED_FROM_CHECKOUT")
    monkeypatch.setattr("lib.update.cli.shutil.which", lambda _tool: None)

    assert _maybe_reexec_checkout_update() == 1
    assert "nix` was not found" in capsys.readouterr().err


def test_maybe_reexec_checkout_update_execs_checkout_flake(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Handoff uses the checkout flake and preserves the original update argv."""
    calls: dict[str, object] = {}

    def _execvpe(file: str, args: list[str], env: dict[str, str]) -> None:
        calls["file"] = file
        calls["args"] = args
        calls["env"] = env
        raise RuntimeError("exec called")

    monkeypatch.setattr(
        "lib.update.cli.sys.argv",
        ["nixcfg", "update", "--check", "t3code"],
    )
    monkeypatch.setattr("lib.update.cli.get_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        "lib.update.cli._update_library_matches_checkout", lambda _: False
    )
    monkeypatch.setattr("lib.update.cli.shutil.which", lambda _tool: "/bin/nix")
    monkeypatch.setattr(
        "lib.update.cli.os.chdir", lambda path: calls.setdefault("cwd", path)
    )
    monkeypatch.setattr("lib.update.cli.os.execvpe", _execvpe)

    with pytest.raises(RuntimeError, match="exec called"):
        _maybe_reexec_checkout_update()

    assert calls["cwd"] == tmp_path
    assert calls["file"] == "/bin/nix"
    assert calls["args"] == [
        "/bin/nix",
        "run",
        ".#nixcfg",
        "--",
        "update",
        "--check",
        "t3code",
    ]
    assert (
        cast("dict[str, str]", calls["env"])["NIXCFG_UPDATE_REEXECED_FROM_CHECKOUT"]
        == "1"
    )


def test_argv_runs_top_level_update() -> None:
    """Recognize only top-level nixcfg update invocations."""
    assert _argv_runs_top_level_update(["nixcfg", "update"])
    assert not _argv_runs_top_level_update(["nixcfg"])
    assert not _argv_runs_top_level_update(["nixcfg", "ci", "update"])


def test_build_update_options_and_required_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Map json_output alias and detect missing required tools."""
    assert UpdateOptions().target_names == ()
    assert UpdateOptions(targets=None).target_names == ()
    assert UpdateOptions(targets="single").target_names == ("single",)
    legacy_opts = UpdateOptions()
    object.__setattr__(legacy_opts, "targets", "legacy")
    assert legacy_opts.target_names == ("legacy",)

    opts = _build_update_options({"source": "demo", "json_output": True, "check": True})
    assert opts.source == "demo"
    assert opts.target_names == ("demo",)
    assert opts.json is True
    assert opts.check is True
    multi_opts = _build_update_options({"targets": ["one", "two"]})
    assert multi_opts.target_names == ("one", "two")

    monkeypatch.setattr(
        "lib.update.cli.shutil.which",
        lambda tool: None if tool in {"flake-edit", "uv"} else "/bin/x",
    )
    monkeypatch.setattr(
        "lib.update.cli.UPDATERS",
        {
            "demo": type("_U", (), {"required_tools": ("nix",)}),
            "source-only": type("_V", (), {"required_tools": ("nix",)}),
        },
    )
    monkeypatch.setattr(
        "lib.update.cli.get_flake_inputs_with_refs",
        lambda: [SimpleNamespace(name="demo")],
    )

    assert check_required_tools() == ["uv"]
    assert check_required_tools(needs_sources=False) == []
    assert check_required_tools(source="demo") == []
    assert check_required_tools(targets=("demo",)) == []
    assert check_required_tools(targets=("source-only",)) == []
    assert check_required_tools(source="demo", include_flake_edit=True) == [
        "flake-edit"
    ]
    assert check_required_tools(source="unknown", needs_sources=True) == []


def test_split_trailing_target_options_handles_click_variants() -> None:
    """Recover Typer options that Click leaves in the variadic target tuple."""
    assert _split_trailing_target_options(["one", "--", "--check", "two"]) == (
        ("one", "--check", "two"),
        {},
    )
    assert _split_trailing_target_options([
        "one",
        "--check",
        "--max-nix-builds",
        "3",
        "--render-interval=1.5",
        "--user-agent",
        "agent",
    ]) == (
        ("one",),
        {
            "check": True,
            "max_nix_builds": 3,
            "render_interval": 1.5,
            "user_agent": "agent",
        },
    )

    with pytest.raises(typer.BadParameter, match="does not take a value"):
        _split_trailing_target_options(["--check=true"])
    with pytest.raises(typer.BadParameter, match="expects an integer value"):
        _split_trailing_target_options(["--max-nix-builds", "many"])
    with pytest.raises(typer.BadParameter, match="expects a numeric value"):
        _split_trailing_target_options(["--render-interval", "soon"])
    with pytest.raises(typer.BadParameter, match="requires a value"):
        _split_trailing_target_options(["--max-nix-builds"])


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
    assert out.console is out.console
    assert out.err_console is out.err_console
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

    resolved_multi = ResolvedTargets.from_options(UpdateOptions(targets=("src", "inp")))
    assert resolved_multi.source_names == ["src"]
    assert [inp.name for inp in resolved_multi.ref_inputs] == ["inp"]

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
    merged = merge_source_updates(existing, source_updates, native_only=True)
    assert "src" in merged


def test_resolved_targets_expand_flake_input_to_backing_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selecting a flake input should also select sources backed by that input."""

    class _OpencodeUpdater(FlakeInputUpdater):
        pass

    class _ElectronUpdater(FlakeInputUpdater):
        input_name = "opencode"

    monkeypatch.setattr(
        "lib.update.cli.UPDATERS",
        {
            "opencode": _OpencodeUpdater,
            "opencode-desktop": _ElectronUpdater,
            "other": object,
        },
    )
    monkeypatch.setattr("lib.update.cli.get_flake_inputs_with_refs", list)

    resolved = ResolvedTargets.from_options(UpdateOptions(source="opencode"))
    assert resolved.ref_inputs == []
    assert resolved.source_names == [
        "opencode",
        "opencode-desktop",
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

    assert companion_source_name(_CodexV8Updater) == "codex"
    assert companion_source_name(None) is None

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
        cli_inventory_module,
        "build_update_inventory",
        lambda: inventory,
    )
    list_code = handle_list_targets_request(UpdateOptions(list_targets=True, json=True))
    assert list_code == 0
    list_payload = json.loads(capsys.readouterr().out)
    assert list_payload["schemaVersion"] == 1
    assert list_payload["kind"] == "nixcfg-update-inventory"
    assert [item["name"] for item in list_payload["targets"]] == ["a", "b", "i"]
    assert list_payload["summary"]["counts"]["sourceOnly"] == 1

    sorted_by_type_code = handle_list_targets_request(
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
        "lib.update.sources.load_all_sources",
        lambda: SourcesFile(entries={"a": SourceEntry(hashes={})}),
    )
    monkeypatch.setattr(
        "lib.update.sources.validate_source_discovery_consistency", lambda: None
    )
    validate_code = handle_validate_request(
        UpdateOptions(validate=True, json=True), OutputOptions(json_output=True)
    )
    assert validate_code == 0
    validate_payload = json.loads(capsys.readouterr().out)
    assert validate_payload["valid"] is True

    def _boom() -> None:
        msg = "nope"
        raise RuntimeError(msg)

    monkeypatch.setattr(
        "lib.update.sources.validate_source_discovery_consistency", _boom
    )
    validate_err = handle_validate_request(
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
        "lib.update.cli.handle_list_targets_request",
        lambda _opts: calls.append("list") or None,
    )
    monkeypatch.setattr(
        "lib.update.cli.handle_validate_request",
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
        "lib.update.cli.handle_list_targets_request",
        lambda _opts: calls.append("list") or 5,
    )
    assert _handle_preflight_requests(UpdateOptions(), OutputOptions()) == 5
    assert calls == ["sort", "schema", "list"]


def test_list_helpers_resolve_root_input_node() -> None:
    """Resolve root input nodes for direct, follows, and missing inputs."""

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
    direct_node, direct_follows = resolve_root_input_node(
        cast("FlakeLock", lock), "direct"
    )
    assert direct_node is lock.nodes["node-a"]
    assert direct_follows is None

    follows_node, follows_path = resolve_root_input_node(
        cast("FlakeLock", lock), "follows"
    )
    assert follows_node is lock.nodes["node-b"]
    assert follows_path == "wrapper/nixpkgs"

    missing_node, missing_path = resolve_root_input_node(
        cast("FlakeLock", lock), "missing"
    )
    assert missing_node is None
    assert missing_path is None

    unresolved_node, unresolved_path = resolve_root_input_node(
        cast("FlakeLock", lock), "unresolved"
    )
    assert unresolved_node is None
    assert unresolved_path == "wrapper/missing"


def test_inventory_helpers_and_sorting(  # noqa: PLR0915
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    assert source_backing_input_name("flake-hash", _FlakeHash) == "flake-hash"
    assert source_backing_input_name("explicit", _ExplicitInput) == "explicit-input"
    assert source_backing_input_name("deno", _Deno) == "deno"
    assert source_backing_input_name("fallback", None, entry_with_input) == "from-entry"
    assert source_backing_input_name("none", None) is None

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

    monkeypatch.setattr(
        cli_inventory_module,
        "updater_dir_for",
        lambda name: None if name == "missing" else REPO_ROOT / "packages" / name,
    )

    assert _generated_artifact_paths("deno", _Deno) == ("packages/deno/deno-deps.json",)
    assert _generated_artifact_paths("missing", _Deno) == ()
    assert _generated_artifact_paths("custom", _Custom) == ()
    assert _generated_artifact_paths("custom-artifact", _CustomArtifact) == (
        "packages/custom-artifact/generated.nix",
    )
    assert _generated_artifact_paths("uv-lock", _UvLock) == (
        "packages/uv-lock/uv.lock",
    )
    assert _generated_artifact_paths("custom-uv-lock", _CustomUvLock) == (
        "packages/custom-uv-lock/pinned.lock",
    )

    with monkeypatch.context() as patches:
        patches.setattr(
            cli_inventory_module,
            "updater_dir_for",
            lambda _name: (_ for _ in ()).throw(
                RuntimeError("Duplicate package directories")
            ),
        )
        assert _generated_artifact_paths("duplicate-name", _Custom) == ()

    with monkeypatch.context() as patches:
        patches.setattr(
            cli_inventory_module,
            "_repo_relative_path",
            lambda _path: None,
        )
        assert _generated_artifact_paths("custom-artifact", _CustomArtifact) == ()

    assert (
        _repo_relative_path(REPO_ROOT / "packages" / "demo" / "sources.json")
        == "packages/demo/sources.json"
    )
    assert (
        _repo_relative_path(Path("/tmp/outside/sources.json"))
        == "/tmp/outside/sources.json"
    )
    assert _repo_relative_path(None) is None

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


def test_generated_artifact_inventory_normalizes_sibling_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inventory paths should be contained, repo-relative, and free of ``..``."""

    class _SiblingArtifactUpdater(Updater):
        name = "primary"
        generated_artifact_files = ("../sibling/generated.lock",)

    monkeypatch.setattr(
        cli_inventory_module,
        "updater_dir_for",
        lambda _name: REPO_ROOT / "packages/primary",
    )

    paths = _generated_artifact_paths("primary", _SiblingArtifactUpdater)

    assert paths == ("packages/sibling/generated.lock",)
    assert all(".." not in Path(path).parts for path in paths)


def test_build_update_inventory_uses_logical_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    monkeypatch.setattr(
        cli_inventory_module,
        "load_all_sources",
        lambda: sources,
    )
    monkeypatch.setattr(
        cli_inventory_module,
        "package_file_map",
        lambda _filename: {
            "both": REPO_ROOT / "packages" / "both" / "sources.json",
            "desktop": REPO_ROOT / "packages" / "desktop" / "sources.json",
        },
    )
    monkeypatch.setattr(
        cli_inventory_module,
        "get_flake_inputs_with_refs",
        lambda: [
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
    )
    monkeypatch.setattr(cli_inventory_module, "load_flake_lock", _Lock)
    monkeypatch.setattr(
        cli_inventory_module,
        "UPDATERS",
        {
            "both": _BothUpdater,
            "desktop": _DesktopUpdater,
            "deno": _DenoUpdater,
        },
    )
    monkeypatch.setattr(
        cli_inventory_module,
        "sources_file_for",
        lambda name: REPO_ROOT / "packages" / name / "sources.json",
    )
    monkeypatch.setattr(
        cli_inventory_module,
        "updater_dir_for",
        lambda package_name: REPO_ROOT / "packages" / package_name,
    )

    targets = cli_inventory_module.build_update_inventory()
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


def test_generated_artifact_paths_include_crate2nix_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Surface crate2nix outputs alongside updater-declared generated artifacts."""

    class _DenoUpdater(DenoManifestUpdater):
        name = "demo"

    fake_module = SimpleNamespace(
        TARGETS={
            "demo": SimpleNamespace(
                artifact_paths=(
                    Path("packages/demo/Cargo.nix"),
                    Path("packages/demo/crate-hashes.json"),
                ),
            )
        }
    )
    monkeypatch.setattr(
        cli_inventory_module.importlib,
        "import_module",
        lambda name: fake_module if name == "lib.update.crate2nix" else None,
    )

    assert _crate2nix_generated_artifact_paths("demo") == (
        "packages/demo/Cargo.nix",
        "packages/demo/crate-hashes.json",
    )

    monkeypatch.setattr(
        cli_inventory_module,
        "updater_dir_for",
        lambda _name: REPO_ROOT / "packages" / "demo",
    )
    assert _generated_artifact_paths("demo", _DenoUpdater) == (
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
        cli_inventory_module.importlib,
        "import_module",
        lambda _name: (_ for _ in ()).throw(ImportError),
    )
    assert _crate2nix_generated_artifact_paths("demo") == ()

    fake_module = SimpleNamespace(
        TARGETS={
            "demo": SimpleNamespace(
                artifact_paths=(
                    Path("packages/demo/Cargo.nix"),
                    Path("packages/demo/crate-hashes.json"),
                ),
            )
        }
    )
    monkeypatch.setattr(
        cli_inventory_module.importlib,
        "import_module",
        lambda name: fake_module if name == "lib.update.crate2nix" else None,
    )
    monkeypatch.setattr(
        cli_inventory_module,
        "updater_dir_for",
        lambda _name: REPO_ROOT / "packages" / "demo",
    )
    monkeypatch.setattr(
        cli_inventory_module,
        "_repo_relative_path",
        lambda _path: None,
    )

    assert _generated_artifact_paths("demo", _DenoUpdater) == (
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
        "lib.update.sources.save_sources", lambda src: save_calls.append(src)
    )
    updates = {"a": SourceEntry(hashes={"x86_64-linux": "sha256-1"})}
    persist_source_updates(
        do_sources=resolved.do_sources,
        source_names=resolved.source_names,
        dry_run=resolved.dry_run,
        native_only=resolved.native_only,
        sources=source_file,
        source_updates=updates,
        details={"a": "updated"},
    )
    assert len(save_calls) == 1

    assert (
        persist_source_updates(
            do_sources=False,
            source_names=["a"],
            dry_run=False,
            native_only=False,
            sources=source_file,
            source_updates=updates,
            details={"a": "updated"},
        )
        == ()
    )
    assert (
        persist_source_updates(
            do_sources=True,
            source_names=["a"],
            dry_run=False,
            native_only=False,
            sources=source_file,
            source_updates=updates,
            details={"a": "no_change"},
        )
        == ()
    )
    monkeypatch.setattr(
        "lib.update.sources.save_source_updates",
        lambda updates, *, merge_existing: updates,
    )
    persist_source_updates(
        do_sources=True,
        source_names=["a"],
        dry_run=False,
        native_only=True,
        sources=source_file,
        source_updates=updates,
        details={"a": "updated"},
    )

    assert (
        persist_source_updates(
            do_sources=True,
            source_names=["a"],
            dry_run=True,
            native_only=False,
            sources=source_file,
            source_updates=updates,
            details={"a": "updated"},
        )
        == ()
    )
    assert len(save_calls) == 1


def test_persist_generated_artifacts_and_materialized_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persist generated artifacts before sources and respect dry-run."""
    artifact = GeneratedArtifact.text("artifacts/demo.txt", "hello\n")
    other_artifact = GeneratedArtifact.text("artifacts/other.txt", "other\n")
    assert flatten_artifact_updates(
        {"other": (other_artifact,), "demo": (artifact,)},
    ) == [artifact, other_artifact]

    saved_artifacts: list[list[GeneratedArtifact]] = []
    saved_sources: list[SourcesFile] = []
    monkeypatch.setattr(
        "lib.update.artifacts.save_generated_artifacts",
        lambda artifacts: saved_artifacts.append(list(artifacts)),
    )
    monkeypatch.setattr(
        "lib.update.sources.save_sources",
        lambda sources: saved_sources.append(sources),
    )

    persist_generated_artifacts(
        do_sources=True,
        source_names=["demo"],
        dry_run=False,
        artifact_updates={"demo": (artifact,)},
        details={"demo": "updated"},
    )
    assert saved_artifacts == [[artifact]]

    persist_materialized_updates(
        do_sources=True,
        source_names=["demo"],
        dry_run=False,
        native_only=False,
        sources=SourcesFile(entries={"demo": SourceEntry(hashes={})}),
        source_updates={"demo": SourceEntry(hashes={"x86_64-linux": "sha256-1"})},
        artifact_updates={"demo": (artifact,)},
        details={"demo": "updated"},
    )
    assert len(saved_artifacts) == 2
    assert len(saved_sources) == 1

    persist_generated_artifacts(
        do_sources=True,
        source_names=["demo"],
        dry_run=True,
        artifact_updates={"demo": (artifact,)},
        details={"demo": "updated"},
    )
    assert len(saved_artifacts) == 2

    persist_generated_artifacts(
        do_sources=False,
        source_names=["demo"],
        dry_run=False,
        artifact_updates={"demo": (artifact,)},
        details={"demo": "updated"},
    )
    assert len(saved_artifacts) == 2

    persist_generated_artifacts(
        do_sources=True,
        source_names=["demo"],
        dry_run=False,
        artifact_updates={"demo": (artifact,)},
        details={"demo": "error"},
    )
    assert len(saved_artifacts) == 2


def test_planned_update_paths_cover_new_sources_and_crate_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Capture every declared destination before an updater can mutate it."""
    package_dir = tmp_path / "packages" / "demo"
    package_dir.mkdir(parents=True)
    (package_dir / "updater.py").write_text("# updater\n", encoding="utf-8")
    target = update_crate2nix.Crate2NixTarget(
        name="demo",
        patched_src_installable="path:.#demo-crate2nix-src",
        cargo_nix=Path("packages/demo/Cargo.nix"),
        crate_hashes=Path("packages/demo/crate-hashes.json"),
        normalizer_path=Path("packages/demo/normalize_cargo_nix.py"),
        supported_platforms=("x86_64-linux",),
    )
    monkeypatch.setattr("lib.update.persistence.get_repo_root", lambda: tmp_path)
    monkeypatch.setitem(update_crate2nix.TARGETS, "demo", target)

    assert set(planned_update_paths(["demo"], {"demo": DenoManifestUpdater})) == {
        package_dir / "sources.json",
        package_dir / "deno-deps.json",
        package_dir / "Cargo.nix",
        package_dir / "crate-hashes.json",
    }


@pytest.mark.parametrize(
    ("owner_root", "shadow_root"),
    [("packages", "overlays"), ("overlays", "packages")],
)
def test_planned_update_paths_use_authoritative_source_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    owner_root: str,
    shadow_root: str,
) -> None:
    """Plan writes beside the unique source sidecar despite a shadow directory."""
    owner_dir = tmp_path / owner_root / "demo"
    shadow_dir = tmp_path / shadow_root / "demo"
    owner_dir.mkdir(parents=True)
    shadow_dir.mkdir(parents=True)
    (owner_dir / "sources.json").write_text("{}\n", encoding="utf-8")
    (owner_dir / "updater.py").write_text("# updater\n", encoding="utf-8")

    monkeypatch.setattr("lib.update.persistence.get_repo_root", lambda: tmp_path)

    assert set(planned_update_paths(["demo"], {"demo": DenoManifestUpdater})) == {
        owner_dir / "sources.json",
        owner_dir / "deno-deps.json",
    }


def test_planned_update_paths_use_updater_owner_for_generated_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep updater artifacts with their sidecar when source ownership is split."""
    source_dir = tmp_path / "packages" / "demo"
    updater_dir = tmp_path / "overlays" / "demo"
    source_dir.mkdir(parents=True)
    updater_dir.mkdir(parents=True)
    (source_dir / "sources.json").write_text("{}\n", encoding="utf-8")
    (updater_dir / "updater.py").write_text("# updater\n", encoding="utf-8")

    monkeypatch.setattr("lib.update.persistence.get_repo_root", lambda: tmp_path)

    assert set(planned_update_paths(["demo"], {"demo": DenoManifestUpdater})) == {
        source_dir / "sources.json",
        updater_dir / "deno-deps.json",
    }


def test_planned_update_paths_preserve_flat_sidecar_layout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Derive a missing flat source beside its flat updater sidecar."""
    updater_path = tmp_path / "overlays" / "demo.updater.py"
    updater_path.parent.mkdir(parents=True)
    updater_path.write_text("# updater\n", encoding="utf-8")

    monkeypatch.setattr("lib.update.persistence.get_repo_root", lambda: tmp_path)

    assert planned_update_paths(["demo"], {"demo": Updater}) == (
        (tmp_path / "overlays" / "demo.sources.json").resolve(),
    )


def test_planned_update_paths_use_unique_package_for_source_only_updater(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A source-only updater may create its sidecar in a unique package directory."""
    package_dir = tmp_path / "packages" / "demo"
    package_dir.mkdir(parents=True)

    monkeypatch.setattr("lib.update.persistence.get_repo_root", lambda: tmp_path)

    assert planned_update_paths(["demo"], {"demo": Updater}) == (
        package_dir / "sources.json",
    )


def test_planned_update_paths_reject_flat_relative_artifact_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Do not guess a directory for artifacts declared by a flat updater."""
    updater_path = tmp_path / "overlays" / "demo.updater.py"
    updater_path.parent.mkdir(parents=True)
    updater_path.write_text("# updater\n", encoding="utf-8")

    monkeypatch.setattr("lib.update.persistence.get_repo_root", lambda: tmp_path)

    with pytest.raises(RuntimeError, match="Flat updater sidecar"):
        planned_update_paths(["demo"], {"demo": DenoManifestUpdater})


def test_planned_update_paths_reject_artifacts_without_an_updater_sidecar(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Do not assign declared artifacts to a source-only package directory."""
    package_dir = tmp_path / "packages" / "demo"
    package_dir.mkdir(parents=True)
    (package_dir / "sources.json").write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr("lib.update.persistence.get_repo_root", lambda: tmp_path)

    with pytest.raises(RuntimeError, match="No updater sidecar owner"):
        planned_update_paths(["demo"], {"demo": DenoManifestUpdater})


def test_planned_update_paths_reject_sources_without_a_physical_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Fail before phases when a registered source has no writable sidecar owner."""
    monkeypatch.setattr("lib.update.persistence.get_repo_root", lambda: tmp_path)

    with pytest.raises(RuntimeError, match="No source sidecar owner found for: demo"):
        planned_update_paths(["demo"], {"demo": Updater})


def test_planned_update_paths_reject_source_symlinks_outside_repo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reject source destinations whose resolved target escapes the repository."""
    repo_root = tmp_path / "repo"
    package_dir = repo_root / "packages" / "demo"
    package_dir.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text("{}\n", encoding="utf-8")
    (package_dir / "sources.json").symlink_to(outside)

    monkeypatch.setattr("lib.update.persistence.get_repo_root", lambda: repo_root)

    with pytest.raises(RuntimeError, match="escapes repository root"):
        planned_update_paths(["demo"], {"demo": Updater})


def test_planned_update_paths_reject_artifacts_outside_repo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reject updater declarations that escape the transaction's repository."""

    class _EscapingUpdater(Updater):
        generated_artifact_files = ("../../../outside.txt",)

    package_dir = tmp_path / "packages" / "demo"
    package_dir.mkdir(parents=True)
    (package_dir / "updater.py").write_text("# updater\n", encoding="utf-8")

    monkeypatch.setattr("lib.update.persistence.get_repo_root", lambda: tmp_path)

    with pytest.raises(RuntimeError, match="escapes repository root"):
        planned_update_paths(["demo"], {"demo": _EscapingUpdater})


def test_run_plan_building(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Build an executable update run plan."""
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

    plan = _build_run_plan(UpdateOptions())
    assert plan is not None

    monkeypatch.setattr(
        "lib.update.cli._build_item_meta", lambda resolved, sources: ({}, [])
    )
    empty = _build_run_plan(UpdateOptions())
    assert empty is None


def test_top_level_entrypoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validate top-level command short circuits and status propagation."""
    cfg = SimpleNamespace(
        default_log_tail_lines=10,
        default_render_interval=0.1,
        default_subprocess_timeout=30,
    )
    monkeypatch.setattr("lib.update.cli._resolve_runtime_config", lambda _opts: cfg)
    monkeypatch.setattr(
        "lib.update.cli._handle_preflight_requests", lambda _opts, _out: 7
    )
    assert _run_async(run_updates(UpdateOptions())) == 7

    monkeypatch.setattr(
        "lib.update.cli._handle_preflight_requests", lambda _opts, _out: None
    )

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


def test_update_workspace_copies_and_promotes_explicit_changes(
    tmp_path: Path,
) -> None:
    """Run against an exact non-ignored source copy and promote allowed files."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    (live / "tracked.txt").write_text("working tree\n", encoding="utf-8")
    (live / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    (live / "ignored.txt").write_text("ignored\n", encoding="utf-8")
    original_cwd = Path.cwd()
    original_repo_root = os.environ.get("REPO_ROOT")

    with IsolatedUpdateWorkspace(live) as workspace:
        assert Path.cwd() == workspace.root
        assert os.environ["REPO_ROOT"] == str(workspace.root)
        assert (workspace.root / "tracked.txt").read_text() == "working tree\n"
        assert (workspace.root / "untracked.txt").read_text() == "untracked\n"
        assert not (workspace.root / "ignored.txt").exists()
        git = shutil.which("git")
        assert git is not None
        assert (
            subprocess.run(  # noqa: S603
                [git, "status", "--porcelain"],
                cwd=workspace.root,
                check=True,
                capture_output=True,
            ).stdout
            == b""
        )

        (workspace.root / "tracked.txt").write_text("updated\n", encoding="utf-8")
        (workspace.root / "tracked.txt").chmod(0o755)
        (workspace.root / "new.txt").write_text("new\n", encoding="utf-8")

        assert workspace.changed_paths() == (Path("new.txt"), Path("tracked.txt"))
        assert workspace.promote({"tracked.txt", "new.txt"}) == (
            Path("new.txt"),
            Path("tracked.txt"),
        )

    assert Path.cwd() == original_cwd
    assert os.environ.get("REPO_ROOT") == original_repo_root
    assert (live / "tracked.txt").read_text() == "updated\n"
    assert (live / "tracked.txt").stat().st_mode & 0o777 == 0o755
    assert (live / "new.txt").read_text() == "new\n"


def test_update_workspace_enforces_its_lifecycle(tmp_path: Path) -> None:
    """Reject access before entry, nested entry, and repeated promotion."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    workspace = IsolatedUpdateWorkspace(live)

    with pytest.raises(RuntimeError, match="not active"):
        _ = workspace.root

    with workspace:
        with pytest.raises(RuntimeError, match="entered more than once"):
            workspace.__enter__()
        (workspace.root / "tracked.txt").write_text("updated\n", encoding="utf-8")
        workspace.promote({"tracked.txt"})
        with pytest.raises(RuntimeError, match="already been committed"):
            workspace.promote({"tracked.txt"})


def test_update_workspace_commits_an_empty_change_set(tmp_path: Path) -> None:
    """A no-change update commits once without creating transaction state."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)

    with IsolatedUpdateWorkspace(live) as workspace:
        assert workspace.promote({"tracked.txt"}) == ()
        with pytest.raises(RuntimeError, match="already been committed"):
            workspace.promote({"tracked.txt"})

    assert not (live / ".git" / "nixcfg-update-transaction.json").exists()


def test_update_workspace_requires_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explain the required local dependency before creating any workspace."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    monkeypatch.setattr("lib.update.persistence.shutil.which", lambda _name: None)

    with (
        pytest.raises(UpdateWorkspaceError, match="Git is required"),
        IsolatedUpdateWorkspace(live),
    ):
        pytest.fail("a workspace must not start without Git")


@pytest.mark.parametrize("absolute", [False, True])
def test_update_workspace_rejects_escaping_source_symlinks(
    tmp_path: Path,
    *,
    absolute: bool,
) -> None:
    """Never recreate a source link that could write outside the sandbox."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    outside = tmp_path / "outside.txt"
    target = outside if absolute else Path("../outside.txt")
    link = live / "escape"
    link.symlink_to(target)
    original_cwd = Path.cwd()

    with (
        pytest.raises(UpdateWorkspaceError, match="symlink"),
        IsolatedUpdateWorkspace(live),
    ):
        pytest.fail("an escaping symlink must not enter the isolated source")

    assert Path.cwd() == original_cwd
    assert not outside.exists()
    link.unlink()
    with IsolatedUpdateWorkspace(live):
        pass


def test_update_workspace_rejects_gitlinks(tmp_path: Path) -> None:
    """Reject unsupported directory-like Git entries instead of copying loosely."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    git = shutil.which("git")
    assert git is not None
    revision = subprocess.run(  # noqa: S603
        [git, "rev-parse", "HEAD"],
        cwd=live,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (live / "vendor").mkdir()
    subprocess.run(  # noqa: S603
        [git, "update-index", "--add", "--cacheinfo", f"160000,{revision},vendor"],
        cwd=live,
        check=True,
    )

    with (
        pytest.raises(UpdateWorkspaceError, match="regular file or symlink"),
        IsolatedUpdateWorkspace(live),
    ):
        pytest.fail("a Git link must not be copied as an ordinary directory")


def test_update_workspace_never_follows_a_leaf_swapped_during_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a regular file changed to an escaping symlink after its lstat."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    original_open = os.open
    swapped = False

    def _swap_before_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if (
            not swapped
            and path == "tracked.txt"
            and dir_fd is not None
            and os.fstat(dir_fd).st_ino == live.stat().st_ino
        ):
            (live / "tracked.txt").unlink()
            (live / "tracked.txt").symlink_to(outside)
            swapped = True
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", _swap_before_open)

    with (
        pytest.raises(UpdateWorkspaceError, match="symlink"),
        IsolatedUpdateWorkspace(live),
    ):
        pytest.fail("a substituted link must never be read into the workspace")

    assert swapped
    assert outside.read_text(encoding="utf-8") == "outside\n"


@pytest.mark.parametrize("moment", ["opened", "read", "symlink"])
def test_update_workspace_retries_leaf_identity_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    moment: str,
) -> None:
    """Retry when a leaf changes at any descriptor-backed snapshot boundary."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    from lib.update import persistence as persistence_module

    original_fstat = os.fstat
    original_lstat = os.lstat
    original_read = persistence_module._read_source_view
    injected = False
    armed = False
    fstat_calls = 0

    def _different(metadata: os.stat_result) -> SimpleNamespace:
        return SimpleNamespace(
            st_dev=metadata.st_dev,
            st_ino=metadata.st_ino + 1,
            st_mode=metadata.st_mode,
            st_size=metadata.st_size,
            st_mtime_ns=metadata.st_mtime_ns,
            st_ctime_ns=metadata.st_ctime_ns,
        )

    def _fstat(descriptor: int) -> object:
        nonlocal armed, fstat_calls, injected
        metadata = original_fstat(descriptor)
        if (
            armed
            and moment != "symlink"
            and stat.S_ISREG(metadata.st_mode)
            and not injected
        ):
            fstat_calls += 1
            trigger = 1 if moment == "opened" else 2
            if fstat_calls == trigger:
                injected = True
                return _different(metadata)
        return metadata

    def _read(*args: object, **kwargs: object) -> object:
        nonlocal armed
        armed = True
        return original_read(*args, **kwargs)

    lstat_calls = 0

    def _lstat(*args: object, **kwargs: object) -> object:
        nonlocal injected, lstat_calls
        metadata = original_lstat(*args, **kwargs)
        if moment == "symlink" and stat.S_ISLNK(metadata.st_mode):
            lstat_calls += 1
            if lstat_calls == 2:
                injected = True
                return _different(metadata)
        return metadata

    if moment == "symlink":
        (live / "tracked.txt").unlink()
        (live / "tracked.txt").symlink_to(".root")
        monkeypatch.setattr(os, "lstat", _lstat)
    else:
        monkeypatch.setattr(os, "fstat", _fstat)
    monkeypatch.setattr(persistence_module, "_read_source_view", _read)

    with IsolatedUpdateWorkspace(live):
        pass

    assert injected


def test_update_workspace_propagates_snapshot_io_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not reinterpret a real source I/O failure as a concurrent edit."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    from lib.update import persistence as persistence_module

    original_open = os.open
    original_read = persistence_module._read_source_view
    armed = False

    def _read(*args: object, **kwargs: object) -> object:
        nonlocal armed
        armed = True
        return original_read(*args, **kwargs)

    def _fail_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if armed and path == "tracked.txt" and flags & os.O_NOFOLLOW:
            raise PermissionError(path)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(persistence_module, "_read_source_view", _read)
    monkeypatch.setattr(os, "open", _fail_open)

    with pytest.raises(PermissionError), IsolatedUpdateWorkspace(live):
        pytest.fail("a real read failure must propagate")


def test_update_workspace_retries_a_disappearing_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry a symlink removed between lstat and readlink without following it."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    tracked = live / "tracked.txt"
    tracked.unlink()
    tracked.symlink_to(".root")
    original_readlink = os.readlink
    removed = False

    def _remove_before_readlink(*args: object, **kwargs: object) -> object:
        nonlocal removed
        if not removed and args[0] == "tracked.txt":
            tracked.unlink()
            removed = True
        return original_readlink(*args, **kwargs)

    monkeypatch.setattr(os, "readlink", _remove_before_readlink)

    with IsolatedUpdateWorkspace(live) as workspace:
        assert not (workspace.root / "tracked.txt").exists()
    assert removed


def test_update_workspace_rejects_a_source_that_never_stabilizes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not validate a composite source view assembled across live edits."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    from lib.update import persistence as persistence_module

    original_read = persistence_module._read_source_view
    calls = 0

    def _read_then_move(*args: object, **kwargs: object) -> object:
        nonlocal calls
        view = original_read(*args, **kwargs)
        calls += 1
        (live / "tracked.txt").write_text(
            "moving-a\n" if calls % 2 == 0 else "moving-b\n",
            encoding="utf-8",
        )
        return view

    monkeypatch.setattr(persistence_module, "_read_source_view", _read_then_move)

    with (
        pytest.raises(UpdateWorkspaceError, match="stable snapshot"),
        IsolatedUpdateWorkspace(live),
    ):
        pytest.fail("a moving source must not become the validation baseline")


def test_update_workspace_owns_flake_cache_switching(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Load flake state from the active root on entry and restored root on exit."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    from lib.update import flake as update_flake
    from lib.update import paths as update_paths

    monkeypatch.setenv("REPO_ROOT", os.fspath(live))
    update_paths._clear_root_cache()
    update_flake.invalidate_flake_lock()
    monkeypatch.setattr(
        update_flake.FlakeLock,
        "from_file",
        lambda path: SimpleNamespace(path=path),
    )

    assert update_flake.load_flake_lock().path == live / "flake.lock"
    with IsolatedUpdateWorkspace(live) as workspace:
        assert update_flake.load_flake_lock().path == workspace.root / "flake.lock"
    assert update_flake.load_flake_lock().path == live / "flake.lock"
    update_flake.invalidate_flake_lock()
    update_paths._clear_root_cache()


def test_update_workspace_cleans_temporary_file_after_install_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leave neither a partial output nor an atomic-write temporary on failure."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)

    with IsolatedUpdateWorkspace(live) as workspace:
        (workspace.root / "tracked.txt").write_text("update\n", encoding="utf-8")
        from lib.update import persistence as persistence_module

        def _fail_exchange(*_args: object, **_kwargs: object) -> None:
            raise OSError("exchange failed")

        monkeypatch.setattr(persistence_module, "_rename_exchange", _fail_exchange)
        with pytest.raises(OSError, match="exchange failed"):
            workspace.promote({"tracked.txt"})

    assert (live / "tracked.txt").read_text(encoding="utf-8") == "committed\n"
    assert list(live.glob(".tracked.txt.nixcfg-*")) == []


def test_update_workspace_copies_a_deleted_tracked_file_as_absent(
    tmp_path: Path,
) -> None:
    """Use the exact dirty working-tree view, including staged or unstaged deletes."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    (live / "tracked.txt").unlink()

    with IsolatedUpdateWorkspace(live) as workspace:
        assert not (workspace.root / "tracked.txt").exists()
        git = shutil.which("git")
        assert git is not None
        assert (
            subprocess.run(  # noqa: S603
                [git, "status", "--porcelain"],
                cwd=workspace.root,
                check=True,
                capture_output=True,
            ).stdout
            == b""
        )


@pytest.mark.parametrize("parent_state", ["missing", "file"])
def test_update_workspace_handles_a_deleted_tracked_parent(
    tmp_path: Path,
    parent_state: str,
) -> None:
    """Represent a missing parent as deletion and reject a non-directory parent."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    shutil.rmtree(live / "nested")
    if parent_state == "file":
        (live / "nested").write_text("not a directory\n", encoding="utf-8")
        with (
            pytest.raises(UpdateWorkspaceError, match="non-directory"),
            IsolatedUpdateWorkspace(live),
        ):
            pytest.fail("a tracked path beneath a file must not be copied")
    else:
        with IsolatedUpdateWorkspace(live) as workspace:
            assert not (workspace.root / "nested/tracked.txt").exists()


def test_update_workspace_restores_context_after_setup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Release every resource when activation fails after switching roots."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    original_cwd = Path.cwd()
    monkeypatch.setenv("REPO_ROOT", "previous-root")
    from lib.update import persistence as persistence_module

    original_clear = persistence_module.update_paths._clear_root_cache
    calls = 0

    def _fail_once() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("cache failure")
        original_clear()

    monkeypatch.setattr(
        persistence_module.update_paths,
        "_clear_root_cache",
        _fail_once,
    )

    with (
        pytest.raises(RuntimeError, match="cache failure"),
        IsolatedUpdateWorkspace(live),
    ):
        pytest.fail("workspace activation unexpectedly succeeded")

    assert Path.cwd() == original_cwd
    assert os.environ["REPO_ROOT"] == "previous-root"
    monkeypatch.setattr(
        persistence_module.update_paths,
        "_clear_root_cache",
        original_clear,
    )
    with IsolatedUpdateWorkspace(live):
        pass


def test_update_workspace_rejects_unexpected_paths(tmp_path: Path) -> None:
    """Refuse every isolated change outside the caller's explicit allowlist."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)

    with IsolatedUpdateWorkspace(live) as workspace:
        (workspace.root / "tracked.txt").write_text("allowed\n", encoding="utf-8")
        (workspace.root / "surprise.txt").write_text("surprise\n", encoding="utf-8")

        with pytest.raises(UpdateWorkspaceUnexpectedPathsError) as exc_info:
            workspace.promote({"tracked.txt"})

    assert exc_info.value.paths == (Path("surprise.txt"),)
    assert (live / "tracked.txt").read_text() == "committed\n"
    assert not (live / "surprise.txt").exists()


def test_update_workspace_rejects_generated_escaping_symlink(tmp_path: Path) -> None:
    """Do not promote a newly generated link outside the repository."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)

    with IsolatedUpdateWorkspace(live) as workspace:
        (workspace.root / "escape").symlink_to("../outside.txt")
        with pytest.raises(UpdateWorkspaceError, match="symlink escapes"):
            workspace.promote({"escape"})

    assert not (live / "escape").exists()
    assert not (tmp_path / "outside.txt").exists()


@pytest.mark.parametrize("existing", [True, False])
def test_update_workspace_preserves_external_edits(
    tmp_path: Path,
    *,
    existing: bool,
) -> None:
    """Detect changed and newly created live outputs before promotion."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    relative = Path("tracked.txt" if existing else "new.txt")

    with IsolatedUpdateWorkspace(live) as workspace:
        (workspace.root / relative).write_text("update\n", encoding="utf-8")
        (live / relative).write_text("external\n", encoding="utf-8")

        with pytest.raises(UpdateWorkspaceConflictError) as exc_info:
            workspace.promote({relative})

    assert exc_info.value.paths == (relative,)
    assert (live / relative).read_text() == "external\n"


def test_update_workspace_preserves_external_deletion(tmp_path: Path) -> None:
    """Treat deleting a captured live output as an external edit."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)

    with IsolatedUpdateWorkspace(live) as workspace:
        (workspace.root / "tracked.txt").write_text("update\n", encoding="utf-8")
        (live / "tracked.txt").unlink()

        with pytest.raises(UpdateWorkspaceConflictError) as exc_info:
            workspace.promote({"tracked.txt"})

    assert exc_info.value.paths == (Path("tracked.txt"),)
    assert not (live / "tracked.txt").exists()


def test_update_workspace_preserves_external_untracked_deletion(tmp_path: Path) -> None:
    """Detect a captured untracked source disappearing during the update."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    untracked = live / "untracked.txt"
    untracked.write_text("start\n", encoding="utf-8")

    with IsolatedUpdateWorkspace(live) as workspace:
        (workspace.root / "tracked.txt").write_text("update\n", encoding="utf-8")
        untracked.unlink()

        with pytest.raises(UpdateWorkspaceConflictError) as exc_info:
            workspace.promote({"tracked.txt"})

    assert exc_info.value.paths == (Path("untracked.txt"),)
    assert not untracked.exists()


def test_update_workspace_preserves_external_non_file_edit(tmp_path: Path) -> None:
    """Report a captured file replaced by a directory without aborting inspection."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)

    with IsolatedUpdateWorkspace(live) as workspace:
        (workspace.root / "tracked.txt").write_text("update\n", encoding="utf-8")
        (live / "tracked.txt").unlink()
        (live / "tracked.txt").mkdir()

        with pytest.raises(UpdateWorkspaceConflictError) as exc_info:
            workspace.promote({"tracked.txt"})

    assert exc_info.value.paths == (Path("tracked.txt"),)
    assert (live / "tracked.txt").is_dir()


def test_update_workspace_rechecks_each_output_before_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve an edit made after the full-source freshness check."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)

    with IsolatedUpdateWorkspace(live) as workspace:
        (workspace.root / "tracked.txt").write_text("update\n", encoding="utf-8")
        from lib.update import persistence as persistence_module

        original_promote = persistence_module._promote_path

        def _edit_then_promote(*args: object, **kwargs: object) -> object:
            (live / "tracked.txt").write_text("external\n", encoding="utf-8")
            return original_promote(*args, **kwargs)

        monkeypatch.setattr(
            persistence_module,
            "_promote_path",
            _edit_then_promote,
        )

        with pytest.raises(UpdateWorkspaceConflictError) as exc_info:
            workspace.promote({"tracked.txt"})

    assert exc_info.value.paths == (Path("tracked.txt"),)
    assert (live / "tracked.txt").read_text(encoding="utf-8") == "external\n"


def test_update_workspace_preserves_an_edit_after_candidate_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject the candidate if its path-visible state changes before verification."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)

    with IsolatedUpdateWorkspace(live) as workspace:
        (workspace.root / "tracked.txt").write_text("update\n", encoding="utf-8")
        from lib.update import persistence as persistence_module

        original_exchange = persistence_module._rename_exchange
        edited = False

        def _exchange_then_edit(
            source: str,
            destination: str,
            *,
            descriptor: int,
        ) -> None:
            nonlocal edited
            original_exchange(source, destination, descriptor=descriptor)
            if edited:
                return
            edited = True
            opened = os.open(source, os.O_WRONLY | os.O_TRUNC, dir_fd=descriptor)
            try:
                os.write(opened, b"external\n")
            finally:
                os.close(opened)

        monkeypatch.setattr(
            persistence_module,
            "_rename_exchange",
            _exchange_then_edit,
        )

        with pytest.raises(UpdateWorkspaceConflictError) as exc_info:
            workspace.promote({"tracked.txt"})

    assert exc_info.value.paths == (Path("tracked.txt"),)
    assert (live / "tracked.txt").read_text(encoding="utf-8") == "external\n"
    retained = list(live.glob(".tracked.txt.nixcfg-transaction-*"))
    assert len(retained) == 1
    assert retained[0].read_text(encoding="utf-8") == "committed\n"


def test_update_workspace_cleans_candidate_after_creation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed candidate write must leave the original and no hidden residue."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)

    with IsolatedUpdateWorkspace(live) as workspace:
        (workspace.root / "tracked.txt").write_text("update\n", encoding="utf-8")

        def _fail_fchmod(_descriptor: int, _mode: int) -> None:
            raise OSError("candidate write failed")

        monkeypatch.setattr(os, "fchmod", _fail_fchmod)
        with pytest.raises(OSError, match="candidate write failed"):
            workspace.promote({"tracked.txt"})

    assert (live / "tracked.txt").read_text(encoding="utf-8") == "committed\n"
    assert list(live.glob(".tracked.txt.nixcfg-*")) == []


def test_update_workspace_cleans_temporary_journal_after_fsync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A journal write failure leaves neither live changes nor temporary metadata."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)

    with IsolatedUpdateWorkspace(live) as workspace:
        (workspace.root / "tracked.txt").write_text("update\n", encoding="utf-8")

        def _fail_fsync(_descriptor: int) -> None:
            raise OSError("journal fsync failed")

        monkeypatch.setattr(os, "fsync", _fail_fsync)
        with pytest.raises(OSError, match="journal fsync failed"):
            workspace.promote({"tracked.txt"})

    assert (live / "tracked.txt").read_text(encoding="utf-8") == "committed\n"
    assert list((live / ".git").glob("*nixcfg-update-transaction*")) == []


def test_update_workspace_preserves_candidate_changed_before_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not install or discard a prepared candidate changed by another writer."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)

    with IsolatedUpdateWorkspace(live) as workspace:
        (workspace.root / "tracked.txt").write_text("candidate\n", encoding="utf-8")
        from lib.update import persistence as persistence_module

        original_promote = persistence_module._promote_path

        def _edit_then_promote(*args: object, **kwargs: object) -> object:
            retained = cast("str", kwargs["retained"])
            (live / retained).write_text("external\n", encoding="utf-8")
            return original_promote(*args, **kwargs)

        monkeypatch.setattr(persistence_module, "_promote_path", _edit_then_promote)
        with pytest.raises(UpdateWorkspaceConflictError) as exc_info:
            workspace.promote({"tracked.txt"})

    assert exc_info.value.paths == (Path("tracked.txt"),)
    assert (live / "tracked.txt").read_text(encoding="utf-8") == "committed\n"
    retained = list(live.glob(".tracked.txt.nixcfg-transaction-*"))
    assert len(retained) == 1
    assert retained[0].read_text(encoding="utf-8") == "external\n"
    assert (live / ".git" / "nixcfg-update-transaction.json").exists()


@pytest.mark.parametrize("existing", [False, True])
def test_update_workspace_preserves_creation_during_atomic_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    existing: bool,
) -> None:
    """Never replace a file created after retaining the original live inode."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    relative = Path("tracked.txt" if existing else "new.txt")
    output = live / relative

    with IsolatedUpdateWorkspace(live) as workspace:
        (workspace.root / relative).write_text("candidate\n", encoding="utf-8")
        from lib.update import persistence as persistence_module

        original_rename = persistence_module._rename_no_replace
        original_exchange = persistence_module._rename_exchange
        raced = False

        def _create_before_rename(
            source: str,
            destination: str,
            *,
            source_descriptor: int,
            destination_descriptor: int,
        ) -> None:
            nonlocal raced
            if destination == relative.name:
                descriptor = os.open(
                    destination,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o644,
                    dir_fd=destination_descriptor,
                )
                try:
                    os.write(descriptor, b"external\n")
                finally:
                    os.close(descriptor)
                raced = True
            original_rename(
                source,
                destination,
                source_descriptor=source_descriptor,
                destination_descriptor=destination_descriptor,
            )

        def _edit_before_exchange(
            source: str,
            destination: str,
            *,
            descriptor: int,
        ) -> None:
            nonlocal raced
            if not raced:
                opened = os.open(source, os.O_WRONLY | os.O_TRUNC, dir_fd=descriptor)
                try:
                    os.write(opened, b"external\n")
                finally:
                    os.close(opened)
                raced = True
            original_exchange(source, destination, descriptor=descriptor)

        monkeypatch.setattr(
            persistence_module,
            "_rename_exchange" if existing else "_rename_no_replace",
            _edit_before_exchange if existing else _create_before_rename,
        )

        with pytest.raises(UpdateWorkspaceConflictError) as exc_info:
            workspace.promote({relative})

    assert raced
    assert exc_info.value.paths == (relative,)
    assert output.read_text(encoding="utf-8") == "external\n"
    retained = list(live.glob(f".{relative.name}.nixcfg-transaction-*"))
    assert len(retained) == 1
    assert retained[0].read_text(encoding="utf-8") == "candidate\n"


@pytest.mark.parametrize("boundary", ["retain", "restore"])
def test_update_workspace_preserves_an_original_changed_after_retention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    """Preserve an edit to the retained inode before validation or restoration."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)

    with IsolatedUpdateWorkspace(live) as workspace:
        (workspace.root / "tracked.txt").write_text("candidate\n", encoding="utf-8")
        from lib.update import persistence as persistence_module

        original_exchange = persistence_module._rename_exchange
        edited = False

        def _exchange_then_edit(
            source: str,
            destination: str,
            *,
            descriptor: int,
        ) -> None:
            nonlocal edited
            original_exchange(source, destination, descriptor=descriptor)
            if edited:
                return
            edited = True
            edits = [(destination, b"external-backup\n")]
            if boundary == "restore":
                edits.append((source, b"external-live\n"))
            for leaf, content in edits:
                opened = os.open(
                    leaf,
                    os.O_WRONLY | os.O_TRUNC,
                    dir_fd=descriptor,
                )
                try:
                    os.write(opened, content)
                finally:
                    os.close(opened)

        monkeypatch.setattr(
            persistence_module,
            "_rename_exchange",
            _exchange_then_edit,
        )

        with pytest.raises(UpdateWorkspaceConflictError) as exc_info:
            workspace.promote({"tracked.txt"})

    assert exc_info.value.paths == (Path("tracked.txt"),)
    expected = "external-backup\n" if boundary == "retain" else "external-live\n"
    assert (live / "tracked.txt").read_text(encoding="utf-8") == expected
    retained = list(live.glob(".tracked.txt.nixcfg-transaction-*"))
    assert len(retained) == 1
    retained_expected = "candidate\n" if boundary == "retain" else "external-backup\n"
    assert retained[0].read_text(encoding="utf-8") == retained_expected


def test_update_workspace_rejects_parent_redirected_during_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not follow an output parent replaced by an escaping link mid-promotion."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    (live / "generated").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    with IsolatedUpdateWorkspace(live) as workspace:
        output = workspace.root / "generated" / "output.txt"
        output.parent.mkdir()
        output.write_text("update\n", encoding="utf-8")
        from lib.update import persistence as persistence_module

        original_promote = persistence_module._promote_path

        def _redirect_then_promote(*args: object, **kwargs: object) -> object:
            retained = cast("str", kwargs["retained"])
            (live / "generated" / retained).unlink()
            (live / "generated").rmdir()
            (live / "generated").symlink_to(outside)
            return original_promote(*args, **kwargs)

        monkeypatch.setattr(
            persistence_module,
            "_promote_path",
            _redirect_then_promote,
        )

        with pytest.raises(UpdateWorkspaceConflictError) as exc_info:
            workspace.promote({"generated/output.txt"})

    assert exc_info.value.paths == (Path("generated/output.txt"),)
    assert not (outside / "output.txt").exists()


def test_update_workspace_rejects_output_with_missing_live_parent(
    tmp_path: Path,
) -> None:
    """Promotion is leaf-only so a crash cannot strand journal-owned directories."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)

    with IsolatedUpdateWorkspace(live) as workspace:
        output = workspace.root / "generated" / "output.txt"
        output.parent.mkdir()
        output.write_text("update\n", encoding="utf-8")
        with pytest.raises(UpdateWorkspaceConflictError) as exc_info:
            workspace.promote({"generated/output.txt"})

    assert exc_info.value.paths == (Path("generated/output.txt"),)
    assert not (live / "generated").exists()
    assert not (live / ".git" / "nixcfg-update-transaction.json").exists()


def test_update_workspace_rejects_unrelated_live_source_drift(tmp_path: Path) -> None:
    """Promotion requires the complete validated source view to remain current."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    unrelated = live / "unrelated.txt"
    unrelated.write_text("start\n", encoding="utf-8")
    with IsolatedUpdateWorkspace(live) as workspace:
        (workspace.root / "tracked.txt").write_text("update\n", encoding="utf-8")
        unrelated.write_text("external\n", encoding="utf-8")

        with pytest.raises(UpdateWorkspaceConflictError) as exc_info:
            workspace.promote({"tracked.txt"})

    assert exc_info.value.paths == (Path("unrelated.txt"),)
    assert (live / "tracked.txt").read_text() == "committed\n"
    assert unrelated.read_text() == "external\n"


def test_update_workspace_rolls_back_partial_promotion(tmp_path: Path) -> None:
    """Restore promoted files if a later allowed output cannot be installed."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    blocked_parent = live / "z-output"
    blocked_parent.mkdir()
    blocked_parent.chmod(0o500)

    try:
        with IsolatedUpdateWorkspace(live) as workspace:
            (workspace.root / "tracked.txt").write_text("update\n", encoding="utf-8")
            nested = workspace.root / "z-output" / "new.txt"
            nested.parent.mkdir()
            nested.write_text("new\n", encoding="utf-8")

            with pytest.raises(PermissionError):
                workspace.promote({"tracked.txt", "z-output/new.txt"})
    finally:
        blocked_parent.chmod(0o700)

    assert (live / "tracked.txt").read_text() == "committed\n"
    assert not (blocked_parent / "new.txt").exists()


def test_update_workspace_excludes_concurrent_promoters(tmp_path: Path) -> None:
    """Serialize update workspaces for one repository through a Git-local lock."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)

    with (
        IsolatedUpdateWorkspace(live),
        pytest.raises(UpdateWorkspaceError, match="already running"),
        IsolatedUpdateWorkspace(live),
    ):
        pytest.fail("a second workspace must not become active")


def test_update_workspace_promotes_deletion_and_symlink(tmp_path: Path) -> None:
    """Preserve filesystem semantics for removed files and symbolic links."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    symlink = live / "link.txt"
    symlink.symlink_to("tracked.txt")

    with IsolatedUpdateWorkspace(live) as workspace:
        (workspace.root / "tracked.txt").unlink()
        workspace_link = workspace.root / "link.txt"
        assert workspace_link.readlink() == Path("tracked.txt")
        workspace_link.unlink()
        workspace_link.symlink_to("replacement.txt")

        workspace.promote({"tracked.txt", "link.txt"})

    assert not (live / "tracked.txt").exists()
    assert symlink.readlink() == Path("replacement.txt")


@pytest.mark.parametrize(
    "invalid",
    [Path(), Path("../escape"), Path(".git/config"), Path("/absolute")],
)
def test_update_workspace_rejects_unsafe_allowed_paths(
    tmp_path: Path,
    invalid: Path,
) -> None:
    """Keep promotion allowlists repository-relative and outside Git metadata."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)

    with (
        IsolatedUpdateWorkspace(live) as workspace,
        pytest.raises(ValueError, match="repository-relative"),
    ):
        workspace.promote({invalid})


def test_update_workspace_verifies_complete_final_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detect a direct live edit racing promotion and roll back owned outputs."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    unrelated = live / "unrelated.txt"
    unrelated.write_text("start\n", encoding="utf-8")

    with IsolatedUpdateWorkspace(live) as workspace:
        (workspace.root / "tracked.txt").write_text("update\n", encoding="utf-8")
        from lib.update import persistence as persistence_module

        original_conflicts = persistence_module._source_conflicts
        calls = 0

        def _edit_before_final_check(
            *args: object,
            **kwargs: object,
        ) -> tuple[Path, ...]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return original_conflicts(*args, **kwargs)
            (live / "tracked.txt").write_text("external\n", encoding="utf-8")
            return ()

        monkeypatch.setattr(
            persistence_module,
            "_source_conflicts",
            _edit_before_final_check,
        )

        with pytest.raises(UpdateWorkspaceConflictError) as exc_info:
            workspace.promote({"tracked.txt"})

    assert exc_info.value.paths == (Path("tracked.txt"),)
    assert (live / "tracked.txt").read_text() == "external\n"


def test_update_workspace_reports_rollback_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve and report an output externally edited before rollback."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)

    with IsolatedUpdateWorkspace(live) as workspace:
        (workspace.root / "tracked.txt").write_text("update\n", encoding="utf-8")
        (workspace.root / "z-output.txt").write_text("new\n", encoding="utf-8")
        from lib.update import persistence as persistence_module

        original_promote = persistence_module._promote_path

        def _promote_then_race(
            root_descriptor: int,
            path: Path,
            *args: object,
            **kwargs: object,
        ) -> object:
            if path == Path("z-output.txt"):
                raise PermissionError(path)
            record = original_promote(root_descriptor, path, *args, **kwargs)
            if path == Path("tracked.txt"):
                (live / path).write_text("external\n", encoding="utf-8")
            return record

        monkeypatch.setattr(
            persistence_module,
            "_promote_path",
            _promote_then_race,
        )

        with pytest.raises(UpdateWorkspaceConflictError) as exc_info:
            workspace.promote({"tracked.txt", "z-output.txt"})

    assert exc_info.value.paths == (Path("tracked.txt"),)
    assert (live / "tracked.txt").read_text() == "external\n"
    assert not (live / "z-output.txt").exists()


def test_update_workspace_rolls_back_a_promoted_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restore a deleted output when promotion of a later output fails."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)

    with IsolatedUpdateWorkspace(live) as workspace:
        (workspace.root / "tracked.txt").unlink()
        (workspace.root / "z-output.txt").write_text("new\n", encoding="utf-8")
        from lib.update import persistence as persistence_module

        original_promote = persistence_module._promote_path

        def _fail_later(
            root_descriptor: int,
            path: Path,
            *args: object,
            **kwargs: object,
        ) -> object:
            if path == Path("z-output.txt"):
                raise PermissionError(path)
            return original_promote(root_descriptor, path, *args, **kwargs)

        monkeypatch.setattr(persistence_module, "_promote_path", _fail_later)

        with pytest.raises(PermissionError):
            workspace.promote({"tracked.txt", "z-output.txt"})

    assert (live / "tracked.txt").read_text(encoding="utf-8") == "committed\n"
    assert not (live / "z-output.txt").exists()


def test_update_workspace_continues_rollback_past_non_file_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restore other outputs after one output changes type during rollback."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    (live / "a-created").mkdir()

    with IsolatedUpdateWorkspace(live) as workspace:
        nested = workspace.root / "a-created" / "output.txt"
        nested.parent.mkdir()
        nested.write_text("a\n", encoding="utf-8")
        (workspace.root / "b-output.txt").write_text("b\n", encoding="utf-8")
        (workspace.root / "z-output.txt").write_text("z\n", encoding="utf-8")
        from lib.update import persistence as persistence_module

        original_promote = persistence_module._promote_path

        def _replace_then_fail(
            root_descriptor: int,
            path: Path,
            *args: object,
            **kwargs: object,
        ) -> object:
            if path == Path("z-output.txt"):
                conflicted = live / "b-output.txt"
                conflicted.unlink()
                conflicted.mkdir()
                raise PermissionError(path)
            return original_promote(root_descriptor, path, *args, **kwargs)

        monkeypatch.setattr(
            persistence_module,
            "_promote_path",
            _replace_then_fail,
        )

        with pytest.raises(UpdateWorkspaceConflictError) as exc_info:
            workspace.promote({
                "a-created/output.txt",
                "b-output.txt",
                "z-output.txt",
            })

    assert exc_info.value.paths == (Path("b-output.txt"),)
    assert (live / "a-created").is_dir()
    assert not (live / "a-created" / "output.txt").exists()
    assert (live / "b-output.txt").is_dir()
    assert not (live / "z-output.txt").exists()


def test_update_workspace_merges_validation_and_rollback_conflicts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report both the validation race and a separately conflicted rollback."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)

    with IsolatedUpdateWorkspace(live) as workspace:
        (workspace.root / "tracked.txt").write_text("update\n", encoding="utf-8")
        from lib.update import persistence as persistence_module

        original_conflicts = persistence_module._source_conflicts
        calls = 0

        def _conflict_after_promotion(
            *args: object,
            **kwargs: object,
        ) -> tuple[Path, ...]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return original_conflicts(*args, **kwargs)
            (live / "tracked.txt").write_text("external\n", encoding="utf-8")
            return (Path("unrelated.txt"),)

        monkeypatch.setattr(
            persistence_module,
            "_source_conflicts",
            _conflict_after_promotion,
        )

        with pytest.raises(UpdateWorkspaceConflictError) as exc_info:
            workspace.promote({"tracked.txt"})

    assert set(exc_info.value.paths) == {
        Path("tracked.txt"),
        Path("unrelated.txt"),
    }
    assert (live / "tracked.txt").read_text(encoding="utf-8") == "external\n"


def test_run_updates_promotes_only_after_isolated_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Validate a mutating run in a disposable root before touching live files."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    output = live / "tracked.txt"
    execution_roots: list[Path] = []
    plan = make_run_plan()

    async def _execute_result(*_args: object) -> SimpleNamespace:
        isolated_root = Path.cwd()
        execution_roots.append(isolated_root)
        isolated_output = isolated_root / "tracked.txt"
        isolated_output.write_text("validated update\n", encoding="utf-8")
        assert output.read_text(encoding="utf-8") == "committed\n"
        return SimpleNamespace(
            summary=UpdateSummary(updated=["demo"]),
            had_errors=False,
            written_paths=(isolated_output,),
        )

    configure_isolated_run(
        monkeypatch,
        root=live,
        plan=plan,
        execute_result=_execute_result,
        planned_paths=("tracked.txt",),
    )

    assert _run_async(run_updates(UpdateOptions())) == 0
    assert execution_roots
    assert execution_roots[0] != live
    assert output.read_text(encoding="utf-8") == "validated update\n"


def test_run_updates_check_validates_the_candidate_in_isolation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Hash and validate prospective refs without changing the live checkout."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    live_flake_lock = live / "flake.lock"
    live_flake_lock.write_text("current input\n", encoding="utf-8")
    ref = FlakeInputRef("demo", "owner", "repo", "v1", "github")
    plan = make_run_plan(
        source_names=("demo",),
        ref_inputs=(ref,),
        dry_run=True,
        do_input_refresh=True,
        show_phase_headers=True,
    )
    events: list[str] = []

    async def _run_ref_phase(*, dry_run: bool, **_kwargs: object) -> UpdatePhaseResult:
        assert dry_run is False
        assert Path.cwd() != live
        (Path.cwd() / "flake.lock").write_text("candidate input\n", encoding="utf-8")
        events.append("ref")
        return UpdatePhaseResult(details={"demo": "updated"})

    async def _run_sources_phase(context: object) -> UpdatePhaseResult:
        assert context.update_input is True
        assert context.dry_run is False
        assert (Path.cwd() / "flake.lock").read_text(encoding="utf-8") == (
            "candidate input\n"
        )
        live_flake_lock.write_text("concurrent input\n", encoding="utf-8")
        events.append("hash")
        return UpdatePhaseResult(details={"demo": "updated"})

    def _persist(**kwargs: object) -> tuple[Path, ...]:
        assert kwargs["dry_run"] is False
        events.append("persist")
        return ()

    def _validate(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        assert (Path.cwd() / "flake.lock").read_text(encoding="utf-8") == (
            "candidate input\n"
        )
        events.append("validate")
        return ()

    monkeypatch.setattr("lib.update.cli.get_repo_root", lambda: live)
    monkeypatch.setattr("lib.update.cli._build_run_plan", lambda *_args: plan)
    monkeypatch.setattr("lib.update.cli.consume_events", drain_events)
    monkeypatch.setattr("lib.update.source_runner.run_ref_phase", _run_ref_phase)
    monkeypatch.setattr(
        "lib.update.source_runner.run_sources_phase", _run_sources_phase
    )
    monkeypatch.setattr("lib.update.persistence.persist_materialized_updates", _persist)
    monkeypatch.setattr(
        "lib.update.derivation_validation.validate_derivations", _validate
    )
    monkeypatch.setattr(
        "lib.update.persistence.planned_update_paths", lambda *_args: ()
    )

    assert _run_async(run_updates(UpdateOptions(check=True))) == 0
    assert events == ["ref", "hash", "persist", "validate"]
    rendered = capsys.readouterr().out
    assert "Phase 1: flake input refs" in rendered
    assert "Phase 2: sources.json updates" in rendered
    assert live_flake_lock.read_text(encoding="utf-8") == "concurrent input\n"


def test_run_updates_ref_only_skips_the_source_phase(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A ref-only plan must complete without invoking source update machinery."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    ref = FlakeInputRef("demo", "owner", "repo", "v1", "github")
    plan = make_run_plan(ref_inputs=(ref,), dry_run=True)

    async def _run_ref_phase(**_kwargs: object) -> UpdatePhaseResult:
        return UpdatePhaseResult(details={"demo": "no_change"})

    async def _unexpected_source_phase(_context: object) -> UpdatePhaseResult:
        raise AssertionError("ref-only plans must skip the source phase")

    monkeypatch.setattr("lib.update.cli.get_repo_root", lambda: live)
    monkeypatch.setattr("lib.update.cli._build_run_plan", lambda _opts: plan)
    monkeypatch.setattr("lib.update.cli.consume_events", drain_events)
    monkeypatch.setattr("lib.update.source_runner.run_ref_phase", _run_ref_phase)
    monkeypatch.setattr(
        "lib.update.source_runner.run_sources_phase",
        _unexpected_source_phase,
    )
    monkeypatch.setattr(
        "lib.update.persistence.persist_materialized_updates",
        lambda **_kwargs: (),
    )
    monkeypatch.setattr(
        "lib.update.derivation_validation.validate_derivations",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        "lib.update.persistence.planned_update_paths",
        lambda *_args: (),
    )

    assert _run_async(run_updates(UpdateOptions(check=True))) == 0


def test_run_updates_source_input_refresh_declares_flake_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Allow a source-backed input refresh to promote its lock-file update."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    live_flake_lock = live / "flake.lock"
    live_flake_lock.write_text("current input\n", encoding="utf-8")
    plan = make_run_plan(source_names=("demo",), do_input_refresh=True)

    async def _execute_result(*_args: object) -> SimpleNamespace:
        candidate = Path.cwd() / "flake.lock"
        candidate.write_text("refreshed input\n", encoding="utf-8")
        return SimpleNamespace(
            summary=UpdateSummary(updated=["demo"]),
            had_errors=False,
            written_paths=(),
        )

    class _InputUpdater:
        input_name = "demo"
        additional_input_names: tuple[str, ...] = ()

        @staticmethod
        def get_generated_artifact_files() -> tuple[str, ...]:
            return ()

    configure_isolated_run(
        monkeypatch,
        root=live,
        plan=plan,
        execute_result=_execute_result,
        updaters={"demo": _InputUpdater},
    )

    assert _run_async(run_updates(UpdateOptions())) == 0
    assert live_flake_lock.read_text(encoding="utf-8") == "refreshed input\n"


def test_run_updates_does_not_declare_lock_for_source_without_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A source updater without input refresh authority cannot write flake.lock."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    lock = live / "flake.lock"
    lock.write_text("current\n", encoding="utf-8")
    plan = make_run_plan(source_names=("demo",), do_input_refresh=True)

    class _SourceUpdater:
        additional_input_names: tuple[str, ...] = ()

        @staticmethod
        def get_generated_artifact_files() -> tuple[str, ...]:
            return ()

    async def _execute_result(*_args: object) -> SimpleNamespace:
        candidate = Path.cwd() / "flake.lock"
        candidate.write_text("unauthorized\n", encoding="utf-8")
        return SimpleNamespace(
            summary=UpdateSummary(updated=["demo"]),
            had_errors=False,
            written_paths=(candidate,),
        )

    configure_isolated_run(
        monkeypatch,
        root=live,
        plan=plan,
        execute_result=_execute_result,
        updaters={"demo": _SourceUpdater},
    )

    assert _run_async(run_updates(UpdateOptions())) == 1
    assert lock.read_text(encoding="utf-8") == "current\n"
    assert "unexpected paths: flake.lock" in capsys.readouterr().err


@pytest.mark.parametrize(
    (
        "candidate_path",
        "candidate_content",
        "reported_path",
        "planned_paths",
        "had_errors",
        "check",
        "diagnostics",
        "expected_live_content",
    ),
    [
        pytest.param(
            "tracked.txt",
            "unreported\n",
            None,
            ("tracked.txt",),
            False,
            False,
            ("unexpected paths: tracked.txt",),
            "committed\n",
            id="unreported-declared-write",
        ),
        pytest.param(
            "surprise.txt",
            "surprise\n",
            None,
            (),
            False,
            True,
            ("unexpected paths: surprise.txt", "Failed: workspace"),
            None,
            id="check-undeclared-candidate",
        ),
        pytest.param(
            "surprise.txt",
            "surprise\n",
            "candidate",
            (),
            False,
            False,
            ("unexpected paths: surprise.txt",),
            None,
            id="reported-undeclared-write",
        ),
        pytest.param(
            "tracked.txt",
            "failed update\n",
            "candidate",
            ("tracked.txt",),
            True,
            False,
            (),
            "committed\n",
            id="failed-execution",
        ),
        pytest.param(
            None,
            None,
            "outside",
            (),
            False,
            False,
            ("escapes isolated workspace", "Failed: workspace"),
            None,
            id="reported-path-escapes-isolation",
        ),
    ],
)
def test_run_updates_enforces_isolated_output_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    candidate_path: str | None,
    candidate_content: str | None,
    reported_path: str | None,
    planned_paths: tuple[str, ...],
    had_errors: bool,
    check: bool,
    diagnostics: tuple[str, ...],
    expected_live_content: str | None,
) -> None:
    """Promote only successful, reported writes inside the declared boundary."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    candidate: Path | None = None

    async def _execute_result(*_args: object) -> SimpleNamespace:
        nonlocal candidate
        if candidate_path is not None:
            candidate = Path.cwd() / candidate_path
            candidate.write_text(candidate_content or "", encoding="utf-8")
        if reported_path == "candidate":
            assert candidate is not None
            written_paths = (candidate,)
        elif reported_path == "outside":
            written_paths = (tmp_path / "outside.txt",)
        else:
            written_paths = ()
        return SimpleNamespace(
            summary=UpdateSummary(
                errors=["demo"] if had_errors else [],
                updated=[] if had_errors else ["demo"],
            ),
            had_errors=had_errors,
            written_paths=written_paths,
        )

    configure_isolated_run(
        monkeypatch,
        root=live,
        plan=make_run_plan(dry_run=check),
        execute_result=_execute_result,
        planned_paths=planned_paths,
    )

    assert _run_async(run_updates(UpdateOptions(check=check))) == 1
    if candidate_path is not None:
        live_candidate = live / candidate_path
        if expected_live_content is None:
            assert not live_candidate.exists()
        else:
            assert live_candidate.read_text(encoding="utf-8") == expected_live_content
    rendered = capsys.readouterr().err
    assert all(diagnostic in rendered for diagnostic in diagnostics)


def test_run_updates_reports_workspace_lock_conflict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Return the lock conflict itself in machine-readable output."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    plan = make_run_plan()
    monkeypatch.setattr("lib.update.cli.get_repo_root", lambda: live)
    monkeypatch.setattr("lib.update.cli._build_run_plan", lambda *_args: plan)

    with IsolatedUpdateWorkspace(live):
        assert _run_async(run_updates(UpdateOptions(json=True))) == 1

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["errors"] == ["workspace"]
    assert "already running" in payload["error"]
    assert captured.err == ""


def test_run_updates_emits_once_after_workspace_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Report phase and cleanup failures in one machine-readable result."""
    plan = make_run_plan()

    class _FailingCleanupWorkspace:
        def __init__(self, _root: Path) -> None:
            self.root = tmp_path

        def __enter__(self) -> _FailingCleanupWorkspace:
            return self

        def __exit__(self, *_exc_info: object) -> None:
            msg = "workspace cleanup failed"
            raise UpdateWorkspaceError(msg)

    async def _execute_result(*_args: object) -> SimpleNamespace:
        summary = UpdateSummary()
        summary.accumulate({"demo": "error"})
        return SimpleNamespace(
            summary=summary,
            had_errors=True,
            written_paths=(),
        )

    monkeypatch.setattr(
        "lib.update.persistence.IsolatedUpdateWorkspace",
        _FailingCleanupWorkspace,
    )
    monkeypatch.setattr("lib.update.cli._build_run_plan", lambda *_args: plan)
    monkeypatch.setattr("lib.update.cli._execute_run_plan_result", _execute_result)

    assert _run_async(run_updates(UpdateOptions(json=True))) == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["errors"] == ["demo", "workspace"]
    assert payload["error"] == "workspace cleanup failed"


def test_run_updates_emits_plan_failure_once_after_workspace_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Wait for cleanup before reporting a target-planning failure."""

    class _FailingCleanupWorkspace:
        def __init__(self, root: Path) -> None:
            self.root = root

        def __enter__(self) -> _FailingCleanupWorkspace:
            return self

        def __exit__(self, *_exc_info: object) -> None:
            msg = "workspace cleanup failed"
            raise UpdateWorkspaceError(msg)

    monkeypatch.setattr(
        "lib.update.persistence.IsolatedUpdateWorkspace",
        _FailingCleanupWorkspace,
    )
    monkeypatch.setattr("lib.update.cli.get_repo_root", lambda: tmp_path)
    monkeypatch.setattr("lib.update.cli._get_updaters", dict)
    monkeypatch.setattr("lib.update.cli.get_flake_inputs_with_refs", list)

    options = UpdateOptions(targets=("missing",), json=True)
    assert _run_async(run_updates(options)) == 1

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "updated": [],
        "errors": ["missing", "workspace"],
        "noChange": [],
        "success": False,
        "error": "workspace cleanup failed",
        "planError": "Unknown source or input 'missing'",
        "unknownTargets": ["missing"],
        "availableTargets": [],
    }


def test_run_updates_emits_empty_result_only_after_workspace_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Do not emit even a no-op summary until the workspace has closed."""

    class _ObservedWorkspace:
        def __init__(self, root: Path) -> None:
            self.root = root

        def __enter__(self) -> _ObservedWorkspace:
            return self

        def __exit__(self, *_exc_info: object) -> None:
            assert capsys.readouterr().out == ""

    monkeypatch.setattr(
        "lib.update.persistence.IsolatedUpdateWorkspace",
        _ObservedWorkspace,
    )
    monkeypatch.setattr("lib.update.cli.get_repo_root", lambda: tmp_path)
    monkeypatch.setattr("lib.update.cli._build_run_plan", lambda _opts: None)

    assert _run_async(run_updates(UpdateOptions(json=True))) == 0
    assert json.loads(capsys.readouterr().out) == {
        "updated": [],
        "errors": [],
        "noChange": [],
        "success": True,
    }


def test_update_workspace_reports_repository_lock_conflict(
    tmp_path: Path,
) -> None:
    """Serialize separate processes through the repository's common Git lock."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    from lib.update import persistence as persistence_module

    lock = persistence_module.FileLock(
        persistence_module._git_common_dir(live) / "nixcfg-update.lock"
    )
    lock.acquire(timeout=0)
    try:
        with (
            pytest.raises(UpdateWorkspaceError, match="already running"),
            IsolatedUpdateWorkspace(live),
        ):
            pytest.fail("repository lock was ignored")
    finally:
        lock.release()


@pytest.mark.parametrize(
    ("targets", "message"),
    [
        (("missing",), "Unknown source or input 'missing'"),
        (
            ("missing-a", "missing-b"),
            "Unknown sources or inputs: missing-a, missing-b",
        ),
    ],
)
def test_run_updates_reports_unknown_targets_as_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    targets: tuple[str, ...],
    message: str,
) -> None:
    """Return target-planning failures as one machine-readable summary."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    monkeypatch.setattr("lib.update.cli.get_repo_root", lambda: live)
    monkeypatch.setattr("lib.update.cli._get_updaters", dict)
    monkeypatch.setattr(
        "lib.update.cli.get_flake_inputs_with_refs",
        lambda: [FlakeInputRef("known", "owner", "repo", "v1", "github")],
    )

    assert _run_async(run_updates(UpdateOptions(targets=targets, json=True))) == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "updated": [],
        "errors": list(targets),
        "noChange": [],
        "success": False,
        "error": message,
        "unknownTargets": list(targets),
        "availableTargets": ["known"],
    }


def test_run_updates_reports_unknown_target_after_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Preserve the human-facing unknown-target diagnostic after teardown."""
    live = tmp_path / "live"
    _init_update_workspace_repo(live)
    monkeypatch.setattr("lib.update.cli.get_repo_root", lambda: live)
    monkeypatch.setattr("lib.update.cli._get_updaters", dict)
    monkeypatch.setattr(
        "lib.update.cli.get_flake_inputs_with_refs",
        lambda: [FlakeInputRef("known", "owner", "repo", "v1", "github")],
    )

    assert _run_async(run_updates(UpdateOptions(targets=("missing",)))) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Error: Unknown source or input 'missing'" in captured.err
    assert "Available: known" in captured.err
