"""Tests for update UI state, rendering, and event consumption."""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest
from rich.console import Console
from rich.control import ControlType
from rich.live import Live
from rich.segment import Segment
from rich.text import Text

import lib.update.ui as ui_module
import lib.update.ui_consumer as ui_consumer_module
import lib.update.ui_render as ui_render_module
import lib.update.ui_state as ui_state_module
from lib.nix.models.sources import (
    HashCollection,
    HashEntry,
    HashType,
    SourceEntry,
    SourcesFile,
)
from lib.tests._assertions import expect_instance
from lib.update.artifacts import GeneratedArtifact
from lib.update.events import CommandResult, UpdateEvent, UpdateEventKind
from lib.update.ui_consumer import ConsumeEventsOptions, EventConsumer, consume_events
from lib.update.ui_render import Renderer
from lib.update.ui_state import (
    ItemMeta,
    ItemState,
    OperationKind,
    OperationState,
    apply_status,
    command_args_from_payload,
    hash_diff_lines,
    is_terminal_status,
    operation_for_command,
    operation_for_status,
)

HASH_A = "sha256-JnkqDwuC7lNsjafV+jOGfvs8K1xC8rk5CTOW+spjiCA="
HASH_B = "sha256-cvRBvHRuunNjF07c4GVHl5rRgoTn1qfI/HdJWtOV63M="
HASH_C = "sha256-DJUI4pMZ7wQTnyOiuDHALmZz7FZtrTbzRzCuNOShmWE="
HASH_D = "sha256-4TE4PIBEUDUalSRf8yPdc8fM7E7fRJsODG+1DgxhDEo="
HASH_E = "sha256-kjQj3f5Q9rV0pIBfHmTHvviIh1gcFuP4xGc8MEfMwd0="
HASH_F = "sha256-bjX1hF6IhFhRz9fNE2j7KnSxjQKf7bn74QRR48E0hVI="
HASH_G = "sha256-u9PE8j5SVfQ+SxG1A5f8A94slq8Nh5S08Y0dWxlI/kI="
TWO = 2

DEFAULT_OP_ORDER = (
    OperationKind.CHECK_VERSION,
    OperationKind.UPDATE_REF,
    OperationKind.REFRESH_LOCK,
    OperationKind.COMPUTE_HASH,
)


def test_ui_facade_reexports_focused_modules() -> None:
    """Public facade should continue exposing the focused UI APIs."""
    assert ui_module.consume_events is consume_events
    assert ui_module.ConsumeEventsOptions is ConsumeEventsOptions
    assert ui_module.EventConsumer is EventConsumer
    assert ui_module.Renderer is Renderer
    assert ui_module.ItemMeta is ItemMeta
    assert ui_module.ItemState is ItemState
    assert ui_module.OperationKind is OperationKind
    assert ui_module.OperationState is OperationState


def _hash_entry(
    hash_value: str,
    *,
    platform: str | None = None,
    git_dep: str | None = None,
    hash_type: HashType = "sha256",
) -> HashEntry:
    return HashEntry.create(
        hash_type=hash_type,
        hash_value=hash_value,
        platform=platform,
        git_dep=git_dep,
    )


def _source_entry(
    *,
    version: str | None = None,
    entries: list[HashEntry] | None = None,
    mapping: dict[str, str] | None = None,
) -> SourceEntry:
    if entries is not None:
        hashes = HashCollection(entries=entries)
    elif mapping is not None:
        hashes = HashCollection(mapping=mapping)
    else:
        hashes = HashCollection(mapping={"x86_64-linux": HASH_A})
    return SourceEntry(hashes=hashes, version=version)


def _meta_for(
    name: str = "demo",
    *,
    op_order: tuple[OperationKind, ...] = DEFAULT_OP_ORDER,
) -> dict[str, ItemMeta]:
    return {
        name: ItemMeta(name=name, origin="packages/demo", op_order=op_order),
    }


def _item(
    name: str = "demo",
    *,
    max_lines: int = 3,
    op_order: tuple[OperationKind, ...] = DEFAULT_OP_ORDER,
) -> ItemState:
    return ItemState.from_meta(
        _meta_for(name, op_order=op_order)[name],
        max_lines=max_lines,
    )


def test_ui_state_terminal_status_and_visibility() -> None:
    """Run this test case."""
    assert is_terminal_status("Updated: 1.0 -> 1.1")
    assert is_terminal_status("Up to date")
    assert is_terminal_status("still running", {"status": "updated"})
    assert not is_terminal_status("Checking demo (current: 1.0)")

    operation = OperationState(kind=OperationKind.CHECK_VERSION, label="Checking")
    assert not operation.visible()
    operation.message = "current 1.0"
    assert operation.visible()


def test_ui_state_from_meta_and_command_mappers() -> None:
    """Run this test case."""
    meta = ItemMeta(
        name="demo",
        origin="packages/demo",
        op_order=(OperationKind.CHECK_VERSION, OperationKind.COMPUTE_HASH),
    )
    item = ItemState.from_meta(meta, max_lines=2)

    assert item.name == "demo"
    assert item.operations[OperationKind.CHECK_VERSION].label == "Checking version"
    assert item.operations[OperationKind.COMPUTE_HASH].tail.maxlen == TWO

    assert command_args_from_payload(["nix", "build"]) == ["nix", "build"]
    assert command_args_from_payload(["nix", 1]) is None
    assert command_args_from_payload("nix build") is None

    assert operation_for_command(None) == OperationKind.COMPUTE_HASH
    assert operation_for_command([]) == OperationKind.COMPUTE_HASH
    assert operation_for_command(["flake-edit"]) == OperationKind.UPDATE_REF
    assert (
        operation_for_command(["nix", "flake", "lock", "--update-input", "demo"])
        == OperationKind.REFRESH_LOCK
    )
    assert operation_for_command(["echo", "ok"]) == OperationKind.COMPUTE_HASH


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        ("Checking demo (current: 1.0)", OperationKind.CHECK_VERSION),
        ("Update available: 1.0 -> 2.0", OperationKind.CHECK_VERSION),
        ("Up to date (version: 1.0)", OperationKind.CHECK_VERSION),
        ("Up to date (ref: main)", OperationKind.CHECK_VERSION),
        ("Updating ref: v1 -> v2", OperationKind.UPDATE_REF),
        ("Updating flake input 'demo'...", OperationKind.REFRESH_LOCK),
        ("Fetching hashes for all platforms", OperationKind.COMPUTE_HASH),
        ("Computing hash for demo.", OperationKind.COMPUTE_HASH),
        ("Build failed with exit status 1", OperationKind.COMPUTE_HASH),
        ("warning: retrying", OperationKind.COMPUTE_HASH),
        ("Up to date", OperationKind.COMPUTE_HASH),
    ],
)
def test_operation_for_status_matches_expected_kind(
    message: str,
    kind: OperationKind,
) -> None:
    """Run this test case."""
    assert operation_for_status(message) == kind


def test_operation_for_status_unknown_message_returns_none() -> None:
    """Run this test case."""
    assert operation_for_status("completely unrelated status") is None


def test_operation_for_status_prefers_typed_payload() -> None:
    """Use typed status payloads before message heuristics."""
    assert (
        operation_for_status(
            "custom status",
            {"operation": OperationKind.REFRESH_LOCK.value},
        )
        == OperationKind.REFRESH_LOCK
    )


def test_operation_for_status_invalid_typed_payload_falls_back_or_none() -> None:
    """Ignore malformed typed payloads and fall back to message parsing."""
    assert (
        operation_for_status("Update available: 1 -> 2", [])
        == OperationKind.CHECK_VERSION
    )
    assert (
        operation_for_status("Update available: 1 -> 2", {"operation": 1})
        == OperationKind.CHECK_VERSION
    )
    assert operation_for_status("custom status", {"operation": "not-real"}) is None


@pytest.mark.parametrize(
    ("payload", "expected_status", "expected_message"),
    [
        ({"status": "checking_current", "detail": "1.0"}, "running", "current 1.0"),
        ({"status": "pinned_version", "detail": "2.0"}, "running", "2.0"),
        ({"status": "latest_version", "detail": "3.0"}, "running", "3.0"),
        (
            {"status": "update_available", "detail": {"current": "1", "latest": "2"}},
            "success",
            "1 -> 2",
        ),
        (
            {"status": "up_to_date", "detail": {"scope": "version", "value": "1.0"}},
            "no_change",
            "1.0 (up to date)",
        ),
        (
            {"status": "up_to_date", "detail": {"scope": "hash"}},
            "no_change",
            None,
        ),
        (
            {"status": "updating_ref", "detail": {"current": "a", "latest": "b"}},
            "running",
            "a -> b",
        ),
        ({"status": "refresh_lock", "detail": "demo"}, "running", "demo"),
        ({"status": "fetching_hashes"}, "running", "all platforms"),
        ({"status": "computing_hash", "detail": "linux"}, "running", "linux"),
    ],
)
def test_apply_status_prefers_structured_status_payloads(
    payload: dict[str, object],
    expected_status: str,
    expected_message: str | None,
) -> None:
    """Apply structured payloads without parsing message text."""
    item = _item()
    apply_status(
        item,
        "structured status",
        {"operation": OperationKind.COMPUTE_HASH.value, **payload},
    )
    op = item.operations[OperationKind.COMPUTE_HASH]
    assert op.status == expected_status
    assert op.message == expected_message


def test_apply_status_handles_artifact_materialization_payloads() -> None:
    """Keep a dedicated artifact phase stable when typed payloads are used."""
    item = _item(
        op_order=(
            OperationKind.CHECK_VERSION,
            OperationKind.MATERIALIZE_ARTIFACTS,
            OperationKind.COMPUTE_HASH,
        )
    )

    apply_status(
        item,
        "Refreshing crate2nix artifacts...",
        {
            "operation": OperationKind.MATERIALIZE_ARTIFACTS.value,
            "status": "computing_hash",
            "detail": "crate2nix artifacts",
        },
    )
    artifact_op = item.operations[OperationKind.MATERIALIZE_ARTIFACTS]
    assert artifact_op.status == "running"
    assert artifact_op.message == "crate2nix artifacts"

    apply_status(
        item,
        "Prepared crate2nix artifacts",
        {
            "operation": OperationKind.MATERIALIZE_ARTIFACTS.value,
            "status": "updated",
            "detail": "crate2nix artifacts",
        },
    )
    assert artifact_op.status == "success"
    assert artifact_op.message == "crate2nix artifacts"

    item2 = _item(op_order=(OperationKind.MATERIALIZE_ARTIFACTS,))
    apply_status(
        item2,
        "crate2nix artifacts up to date",
        {
            "operation": OperationKind.MATERIALIZE_ARTIFACTS.value,
            "status": "up_to_date",
            "detail": {"scope": "artifacts", "value": "crate2nix artifacts"},
        },
    )
    same_op = item2.operations[OperationKind.MATERIALIZE_ARTIFACTS]
    assert same_op.status == "no_change"
    assert same_op.message == "crate2nix artifacts (up to date)"


def test_apply_status_legacy_fallbacks_cover_remaining_message_patterns() -> None:
    """Keep legacy message parsing behavior for unsupported payloads."""
    item = _item()
    apply_status(item, "Checking demo (current: 1.0)")
    assert item.operations[OperationKind.CHECK_VERSION].message == "current 1.0"

    apply_status(item, "Updating ref: old -> new")
    assert item.operations[OperationKind.UPDATE_REF].message == "old -> new"

    apply_status(item, "Updating flake input 'demo'...")
    assert item.operations[OperationKind.REFRESH_LOCK].message == "demo"

    apply_status(item, "Computing hash for linux...")
    assert item.operations[OperationKind.COMPUTE_HASH].message == "linux"

    apply_status(
        item,
        "custom hash note",
        {"operation": OperationKind.COMPUTE_HASH.value},
    )
    assert item.operations[OperationKind.COMPUTE_HASH].message == "custom hash note"

    apply_status(item, "Up to date (version: 2.0)")
    assert item.operations[OperationKind.CHECK_VERSION].message == "2.0 (up to date)"

    apply_status(item, "Up to date (ref: main)")
    assert item.operations[OperationKind.CHECK_VERSION].message == "main (up to date)"

    apply_status(item, "Computing hash for archive.")
    assert item.operations[OperationKind.COMPUTE_HASH].message == "archive"

    item2 = _item()
    apply_status(
        item2,
        "no parser match",
        {"operation": OperationKind.CHECK_VERSION.value},
    )
    assert item2.operations[OperationKind.CHECK_VERSION].status == "running"


def test_apply_status_rules_and_status_priority() -> None:
    """Run this test case."""
    item = _item()

    apply_status(item, "Latest version: 1.0.0")
    check_op = item.operations[OperationKind.CHECK_VERSION]
    assert check_op.status == "running"
    assert check_op.message == "1.0.0"

    apply_status(item, "Update available: 1.0.0 -> 1.1.0")
    assert check_op.status == "success"
    assert check_op.message == "1.0.0 -> 1.1.0"

    # Lower-priority running statuses should not overwrite terminal success.
    apply_status(item, "Latest version: 1.1.0")
    assert check_op.status == "success"
    assert check_op.message == "1.0.0 -> 1.1.0"

    apply_status(item, "Fetching hashes for all platforms")
    hash_op = item.operations[OperationKind.COMPUTE_HASH]
    assert hash_op.status == "running"
    assert hash_op.message == "all platforms"

    apply_status(item, "Up to date")
    assert hash_op.status == "no_change"
    assert hash_op.message is None

    # Default message pass-through for unmatched compute-hash messages.
    item2 = _item()
    apply_status(item2, "warning: cache miss")
    hash_op2 = item2.operations[OperationKind.COMPUTE_HASH]
    assert hash_op2.status == "running"
    assert hash_op2.message == "warning: cache miss"


def test_ui_status_helper_edge_cases_cover_non_string_details() -> None:
    """Keep helper return values stable for malformed or non-string detail payloads."""
    assert ui_state_module._up_to_date_status_update("demo") is None
    assert ui_state_module._up_to_date_status_update({
        "scope": "artifacts",
        "value": ["crate2nix"],
    }) == ui_state_module.StatusUpdate("no_change", clear_message=True)
    assert ui_state_module._updated_status_update({
        "value": "crate2nix artifacts"
    }) == ui_state_module.StatusUpdate("success", "crate2nix artifacts")
    assert ui_state_module._updated_status_update({
        "value": ["bad"]
    }) == ui_state_module.StatusUpdate("success")
    assert ui_state_module._updated_status_update([
        "bad"
    ]) == ui_state_module.StatusUpdate("success")


def test_payload_status_update_rejects_malformed_structured_details() -> None:
    """Ignore structured payload variants that do not match the expected shape."""
    assert (
        ui_state_module._payload_status_update({
            "status": "update_available",
            "detail": {"current": 1, "latest": "2"},
        })
        is None
    )
    assert (
        ui_state_module._payload_status_update({
            "status": "up_to_date",
            "detail": {"scope": "version", "value": 1},
        })
        is None
    )
    assert (
        ui_state_module._payload_status_update({
            "status": "updating_ref",
            "detail": {"current": "a", "latest": 2},
        })
        is None
    )


def test_apply_status_ignores_unknown_or_missing_operations() -> None:
    """Run this test case."""
    item = _item(op_order=(OperationKind.CHECK_VERSION,))
    check_op = item.operations[OperationKind.CHECK_VERSION]

    apply_status(item, "Computing hash for demo.")
    assert check_op.status == "pending"

    apply_status(item, "unrelated status")
    assert check_op.status == "pending"


def test_set_operation_status_keeps_message_when_none() -> None:
    """Leave the existing message untouched when no new message is provided."""
    operation = OperationState(kind=OperationKind.CHECK_VERSION, label="Checking")
    operation.message = "existing"
    ui_state_module._set_operation_status(operation, "running", message=None)
    assert operation.message == "existing"


def test_hash_diff_lines_covers_mapping_changes() -> None:
    """Run this test case."""
    old_entry = _source_entry(
        mapping={
            "aarch64-linux": HASH_A,
            "unchanged": HASH_B,
            "x86_64-linux": HASH_C,
        },
    )
    new_entry = _source_entry(
        mapping={
            "darwin": HASH_D,
            "unchanged": HASH_B,
            "x86_64-linux": HASH_E,
        },
    )

    lines = hash_diff_lines(old_entry, new_entry)

    assert (
        "aarch64-linux :: sha256-JnkqDwuC7lNsjafV+jOGfvs8K1xC8rk5CTOW+spjiCA= -> <removed>"
        in lines
    )
    assert (
        "darwin :: <none> -> sha256-4TE4PIBEUDUalSRf8yPdc8fM7E7fRJsODG+1DgxhDEo="
        in lines
    )
    assert (
        "x86_64-linux :: "
        "sha256-DJUI4pMZ7wQTnyOiuDHALmZz7FZtrTbzRzCuNOShmWE= "
        "-> sha256-kjQj3f5Q9rV0pIBfHmTHvviIh1gcFuP4xGc8MEfMwd0="
    ) in lines
    assert all("unchanged" not in line for line in lines)


def test_hash_diff_lines_covers_entry_keys_duplicates_and_none_inputs() -> None:
    """Run this test case."""
    old_entry = _source_entry(
        entries=[
            _hash_entry(HASH_A),
            _hash_entry(HASH_B),
            _hash_entry(HASH_C, platform="x86_64-linux"),
            _hash_entry(HASH_D, git_dep="tauri"),
        ],
    )
    new_entry = _source_entry(
        entries=[
            _hash_entry(HASH_A),
            _hash_entry(HASH_E),
            _hash_entry(HASH_C, platform="x86_64-linux"),
            _hash_entry(HASH_F, git_dep="tauri"),
            _hash_entry(HASH_G, platform="aarch64-darwin"),
        ],
    )

    changed = hash_diff_lines(old_entry, new_entry)
    assert (
        "sha256#2 :: sha256-cvRBvHRuunNjF07c4GVHl5rRgoTn1qfI/HdJWtOV63M= "
        "-> sha256-kjQj3f5Q9rV0pIBfHmTHvviIh1gcFuP4xGc8MEfMwd0="
    ) in changed
    assert (
        "sha256:tauri :: sha256-4TE4PIBEUDUalSRf8yPdc8fM7E7fRJsODG+1DgxhDEo= "
        "-> sha256-bjX1hF6IhFhRz9fNE2j7KnSxjQKf7bn74QRR48E0hVI="
    ) in changed
    assert (
        "aarch64-darwin :: <none> -> sha256-u9PE8j5SVfQ+SxG1A5f8A94slq8Nh5S08Y0dWxlI/kI="
    ) in changed
    added_only = hash_diff_lines(None, new_entry)
    assert any(line.startswith("sha256 :: <none>") for line in added_only)
    removed_only = hash_diff_lines(old_entry, None)
    assert any(line.endswith("-> <removed>") for line in removed_only)


class _LiveStub:
    instances: ClassVar[list[_LiveStub]] = []

    def __init__(
        self,
        _renderable: object,
        *,
        console: object,
        auto_refresh: bool,
        transient: bool,
    ) -> None:
        self.console = console
        self.auto_refresh = auto_refresh
        self.transient = transient
        self.started = False
        self.stopped = False
        self.updated: list[tuple[object, bool]] = []
        self.__class__.instances.append(self)

    def start(self) -> None:
        """Run this test case."""
        self.started = True

    def stop(self) -> None:
        """Run this test case."""
        self.stopped = True

    def update(self, renderable: object, *, refresh: bool) -> None:
        """Run this test case."""
        self.updated.append((renderable, refresh))


def test_install_resize_aware_live_render_replaces_rich_live_render() -> None:
    """Wire the resize-aware renderer into the Rich Live instance used at runtime."""
    console = Console(file=io.StringIO(), force_terminal=True, width=12, height=4)
    live = Live(Text("abcdefghij"), console=console, auto_refresh=False, transient=True)

    assert not isinstance(live._live_render, ui_render_module._ResizeAwareLiveRender)

    patched = ui_render_module._install_resize_aware_live_render(live)

    assert patched is live
    assert isinstance(live._live_render, ui_render_module._ResizeAwareLiveRender)
    assert live._live_render._console is console


def test_resize_aware_live_render_returns_empty_controls_before_first_render() -> None:
    """Before any render, cursor-clearing controls should be empty."""
    console = Console(file=io.StringIO(), force_terminal=True, width=12, height=4)
    render = ui_render_module._ResizeAwareLiveRender(Text("abc"), console=console)

    assert render.position_cursor().segment.control == []
    assert render.restore_cursor().segment.control == []


def test_resize_aware_live_render_adds_ellipsis_for_vertical_overflow() -> None:
    """Mirror Rich's ellipsis overflow mode while tracking wrapped line widths."""
    console = Console(file=io.StringIO(), force_terminal=True, width=12, height=2)
    render = ui_render_module._ResizeAwareLiveRender(
        Text("one\ntwo\nthree"),
        console=console,
        vertical_overflow="ellipsis",
    )

    segments = list(render.__rich_console__(console, console.options))

    assert render.last_render_height == 2
    assert any("..." in segment.text for segment in segments)
    assert any(segment.text == "\n" for segment in segments)


def test_resize_aware_live_render_crops_vertical_overflow_without_ellipsis() -> None:
    """Mirror Rich's crop overflow mode while preserving tracked line widths."""
    console = Console(file=io.StringIO(), force_terminal=True, width=12, height=2)
    render = ui_render_module._ResizeAwareLiveRender(
        Text("one\ntwo\nthree"),
        console=console,
        vertical_overflow="crop",
    )

    segments = list(render.__rich_console__(console, console.options))

    assert render.last_render_height == 2
    assert not any(segment.text == "..." for segment in segments)
    assert any(segment.text == "\n" for segment in segments)


def test_resize_aware_live_render_preserves_visible_overflow() -> None:
    """Allow visible overflow while still tracking the full rendered height."""
    console = Console(file=io.StringIO(), force_terminal=True, width=12, height=2)
    render = ui_render_module._ResizeAwareLiveRender(
        Text("one\ntwo\nthree"),
        console=console,
        vertical_overflow="visible",
    )

    segments = list(render.__rich_console__(console, console.options))

    assert render.last_render_height == 3
    assert not any("..." in segment.text for segment in segments)
    assert sum(segment.text == "\n" for segment in segments) == 2


def test_resize_aware_live_render_clears_wrapped_rows_after_width_shrink() -> None:
    """Use current console width when erasing a previously rendered frame."""
    console = Console(file=io.StringIO(), force_terminal=True, width=12, height=4)
    render = ui_render_module._ResizeAwareLiveRender(
        Text("abcdefghij"), console=console
    )

    _ = list(render.__rich_console__(console, console.options))
    assert render.last_render_height == 1

    console._width = 4
    position = render.position_cursor()
    restore = render.restore_cursor()

    assert position.segment.control == [
        (ControlType.CARRIAGE_RETURN,),
        (ControlType.ERASE_IN_LINE, 2),
        (ControlType.CURSOR_UP, 1),
        (ControlType.ERASE_IN_LINE, 2),
        (ControlType.CURSOR_UP, 1),
        (ControlType.ERASE_IN_LINE, 2),
    ]
    assert restore.segment.control == [
        (ControlType.CARRIAGE_RETURN,),
        (ControlType.CURSOR_UP, 1),
        (ControlType.ERASE_IN_LINE, 2),
        (ControlType.CURSOR_UP, 1),
        (ControlType.ERASE_IN_LINE, 2),
        (ControlType.CURSOR_UP, 1),
        (ControlType.ERASE_IN_LINE, 2),
    ]


def test_renderer_lifecycle_and_render_if_due(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    monkeypatch.setattr(ui_render_module, "Live", _LiveStub)
    _LiveStub.instances.clear()

    item = _item()
    op = item.operations[OperationKind.CHECK_VERSION]
    op.status = "running"
    op.message = "current 1.0"

    renderer = Renderer(
        {"demo": item},
        ["demo"],
        is_tty=True,
        render_interval=0.1,
    )

    assert object.__getattribute__(renderer, "_live") is not None
    assert _LiveStub.instances[0].started

    renderer.render()
    assert _LiveStub.instances[0].updated

    renderer.request_render()
    renderer.render_if_due(now=0.05)
    assert renderer.needs_render

    renderer.render_if_due(now=1.0)
    assert not renderer.needs_render
    assert renderer.last_render == 1.0

    called: list[bool] = []
    monkeypatch.setattr(renderer, "_print_final_status", lambda: called.append(True))
    renderer.finalize()

    assert _LiveStub.instances[0].stopped
    assert called == [True]
    assert object.__getattribute__(renderer, "_live") is None


def test_renderer_formatting_symbols_and_spinner_updates() -> None:
    """Run this test case."""
    item = _item()
    renderer = Renderer({"demo": item}, ["demo"], is_tty=False, render_interval=0.1)

    running = item.operations[OperationKind.CHECK_VERSION]
    running.status = "running"
    running.message = "working"
    first_spinner = object.__getattribute__(renderer, "_render_operation")(running)
    assert running.spinner is not None
    running.message = "still working"
    second_spinner = object.__getattribute__(renderer, "_render_operation")(running)
    assert first_spinner is second_spinner

    running.status = "success"
    running.message = None
    success_line = object.__getattribute__(renderer, "_render_operation")(running)
    assert running.spinner is None
    assert isinstance(success_line, Text)
    assert str(success_line).startswith("✓ ")

    no_change = item.operations[OperationKind.UPDATE_REF]
    no_change.status = "no_change"
    no_change.message = None
    no_change_line = object.__getattribute__(renderer, "_render_operation")(no_change)
    assert isinstance(no_change_line, Text)
    assert str(no_change_line).startswith("• ")

    error = item.operations[OperationKind.REFRESH_LOCK]
    error.status = "error"
    error.message = None
    error_line = object.__getattribute__(renderer, "_render_operation")(error)
    assert isinstance(error_line, Text)
    assert str(error_line).startswith("✗ ")


def test_renderer_build_display_with_and_without_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    monkeypatch.setattr(ui_render_module, "Live", _LiveStub)

    item = _item(max_lines=2)
    op = item.operations[OperationKind.COMPUTE_HASH]
    op.status = "running"
    op.message = "all platforms"
    op.active_commands = 1
    op.tail.append("[stdout] line-1")
    op.detail_lines.append("detail line")
    item.last_operation = OperationKind.COMPUTE_HASH

    renderer = Renderer(
        {"demo": item},
        ["demo"],
        is_tty=True,
        full_output=False,
        render_interval=0.1,
    )

    full = object.__getattribute__(renderer, "_build_display")(full_output=True)
    assert full is not None
    clipped = object.__getattribute__(renderer, "_build_display")(full_output=False)
    assert clipped is not None

    object.__setattr__(renderer, "_console", None)
    no_console = object.__getattribute__(renderer, "_build_display")()
    assert isinstance(no_console, Text)


def test_renderer_detail_append_and_tty_logging_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    monkeypatch.setattr(ui_render_module, "Live", _LiveStub)

    item = _item()
    renderer = Renderer({"demo": item}, ["demo"], is_tty=True, render_interval=0.1)

    assert not object.__getattribute__(renderer, "_append_detail_line")(
        "missing", "line"
    )
    assert not object.__getattribute__(renderer, "_append_detail_line")("demo", "line")

    item.last_operation = OperationKind.CHECK_VERSION
    assert object.__getattribute__(renderer, "_append_detail_line")("demo", "line")
    assert item.operations[OperationKind.CHECK_VERSION].detail_lines == ["line"]

    renderer.log("demo", "info")
    renderer.log_error("demo", "error")
    details = item.operations[OperationKind.CHECK_VERSION].detail_lines
    assert details[-2:] == ["info", "error"]

    renderer.finalize()


def test_renderer_non_tty_output_and_quiet(capsys: pytest.CaptureFixture[str]) -> None:
    """Run this test case."""
    item = _item()
    verbose_renderer = Renderer(
        {"demo": item},
        ["demo"],
        is_tty=False,
        verbose=True,
        render_interval=0.1,
    )
    verbose_renderer.log_line("demo", "line")
    verbose_renderer.log("demo", "info")
    verbose_renderer.log_error("demo", "boom")
    verbose_renderer.log_error("demo", "multi\ndetail")
    verbose_renderer.render()

    captured = capsys.readouterr()
    assert "[demo] line" in captured.out
    assert "[demo] info" in captured.out
    assert "[demo] ERROR: boom" in captured.err
    assert "[demo] ERROR: multi" in captured.err
    assert "[demo]       detail" in captured.err

    quiet_renderer = Renderer(
        {"demo": item},
        ["demo"],
        is_tty=False,
        verbose=True,
        render_interval=0.1,
        quiet=True,
    )
    quiet_renderer.log_line("demo", "line")
    quiet_renderer.log("demo", "info")
    quiet_renderer.log_error("demo", "boom")
    assert capsys.readouterr() == ("", "")


def test_renderer_private_branch_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    monkeypatch.setattr(ui_render_module, "Live", _LiveStub)
    _LiveStub.instances.clear()

    item = _item()
    renderer = Renderer(
        {"demo": item},
        ["demo"],
        is_tty=True,
        render_interval=0.1,
        quiet=True,
    )

    # _compact_lines with no console returns empty text.
    compact = object.__getattribute__(renderer, "_compact_lines")(
        Text("x"),
        width=10,
        max_visible=1,
    )
    assert isinstance(compact, Text)

    # _append_detail_line returns False when operation lookup fails.
    item.last_operation = OperationKind.CHECK_VERSION
    item.operations.pop(OperationKind.CHECK_VERSION)
    assert not object.__getattribute__(renderer, "_append_detail_line")("demo", "x")

    # render_if_due early-return when needs_render is false.
    renderer.render_if_due(now=1.0)
    assert not renderer.needs_render

    # request_render false branch for non-tty renderer.
    non_tty_renderer = Renderer(
        {"demo": _item()},
        ["demo"],
        is_tty=False,
        render_interval=0.1,
    )
    non_tty_renderer.request_render()
    assert not non_tty_renderer.needs_render

    # _compact_lines branch where segment.text is empty.
    active_renderer = Renderer(
        {"demo": _item()},
        ["demo"],
        is_tty=True,
        render_interval=0.1,
    )
    console = object.__getattribute__(active_renderer, "_console")
    assert console is not None
    monkeypatch.setattr(
        console,
        "render_lines",
        lambda _renderable, options: [[Segment("", None), Segment("x", None)]],
    )
    compact_active = object.__getattribute__(active_renderer, "_compact_lines")(
        Text("x"),
        width=10,
        max_visible=1,
    )
    assert compact_active is not None
    active_renderer.finalize()

    # finalize with quiet=True should not print final status.
    called: list[bool] = []
    monkeypatch.setattr(renderer, "_print_final_status", lambda: called.append(True))
    renderer.finalize()
    assert called == []


def test_renderer_print_final_status_uses_stdout_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    captured: dict[str, object] = {}

    class _ConsoleCapture:
        def __init__(self, *, no_color: bool, highlight: bool) -> None:
            captured["no_color"] = no_color
            captured["highlight"] = highlight

        def print(self, renderable: object) -> None:
            """Run this test case."""
            captured["renderable"] = renderable

    class _StdoutCapture(io.StringIO):
        def isatty(self) -> bool:
            """Run this test case."""
            return False

    monkeypatch.setattr(ui_render_module, "Console", _ConsoleCapture)
    monkeypatch.setattr(ui_render_module.sys, "stdout", _StdoutCapture())

    item = _item()
    renderer = Renderer({"demo": item}, ["demo"], is_tty=False, render_interval=0.1)
    object.__getattribute__(renderer, "_print_final_status")()

    assert captured["no_color"] is True
    assert captured["highlight"] is False
    assert "renderable" in captured


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"full_output": "yes"}, "full_output must be a boolean"),
        ({"verbose": "yes"}, "verbose must be a boolean"),
        ({"panel_height": "10"}, "panel_height must be an integer"),
        ({"quiet": "yes"}, "quiet must be a boolean"),
        ({"extra": True}, "Unexpected keyword argument"),
    ],
)
def test_renderer_init_validates_kwargs(
    kwargs: dict[str, object],
    message: str,
) -> None:
    """Run this test case."""
    with pytest.raises(TypeError, match=message):
        _ = Renderer(
            {"demo": _item()},
            ["demo"],
            is_tty=False,
            render_interval=0.1,
            **kwargs,
        )


@dataclass
class _RendererStub:
    """Simple test renderer used for EventConsumer unit tests."""

    items: dict[str, ItemState]
    order: list[str]
    is_tty: bool
    full_output: bool
    verbose: bool
    render_interval: float
    quiet: bool
    line_logs: list[tuple[str, str]] = field(default_factory=list)
    logs: list[tuple[str, str]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    request_calls: int = 0
    render_due_calls: list[float] = field(default_factory=list)
    finalized: bool = False

    def log_line(self, source: str, message: str) -> None:
        """Run this test case."""
        self.line_logs.append((source, message))

    def log(self, source: str, message: str) -> None:
        """Run this test case."""
        self.logs.append((source, message))

    def log_error(self, source: str, message: str) -> None:
        """Run this test case."""
        self.errors.append((source, message))

    def request_render(self) -> None:
        """Run this test case."""
        self.request_calls += 1

    def render_if_due(self, now: float) -> None:
        """Run this test case."""
        self.render_due_calls.append(now)

    def finalize(self) -> None:
        """Run this test case."""
        self.finalized = True


def _consumer(
    monkeypatch: pytest.MonkeyPatch,
    *,
    is_tty: bool = True,
    verbose: bool = False,
    op_order: tuple[OperationKind, ...] = DEFAULT_OP_ORDER,
    sources: SourcesFile | None = None,
) -> tuple[EventConsumer, asyncio.Queue[UpdateEvent | None]]:
    monkeypatch.setattr(ui_consumer_module, "Renderer", _RendererStub)
    queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
    if sources is None:
        sources = SourcesFile(entries={"demo": _source_entry(version="1.0.0")})

    consumer = EventConsumer(
        queue,
        ["demo"],
        sources,
        options=ConsumeEventsOptions(
            item_meta=_meta_for("demo", op_order=op_order),
            max_lines=3,
            is_tty=is_tty,
            full_output=False,
            verbose=verbose,
            render_interval=0.0,
            build_failure_tail_lines=2,
            quiet=False,
        ),
    )
    return consumer, queue


def _renderer(consumer: EventConsumer) -> _RendererStub:
    renderer = consumer.renderer
    return expect_instance(renderer, _RendererStub)


def test_event_consumer_detail_priority_and_status_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    consumer, _queue = _consumer(monkeypatch, is_tty=True, verbose=True)
    item = consumer.items["demo"]

    object.__getattribute__(consumer, "_set_detail")("demo", "no_change")
    object.__getattribute__(consumer, "_set_detail")("demo", "updated")
    object.__getattribute__(consumer, "_set_detail")("demo", "no_change")
    assert consumer.update_details["demo"] == "updated"
    assert consumer.updated

    object.__getattribute__(consumer, "_handle_status")(
        UpdateEvent.status("demo", "Latest version: 1.0.0"), item
    )
    object.__getattribute__(consumer, "_handle_status")(
        UpdateEvent.status("demo", "Updated: 1.0 -> 1.1"), item
    )
    object.__getattribute__(consumer, "_handle_status")(
        UpdateEvent(source="demo", kind=UpdateEventKind.STATUS, message=None),
        item,
    )

    renderer = _renderer(consumer)
    assert ("demo", "Latest version: 1.0.0") in renderer.line_logs
    assert ("demo", "Updated: 1.0 -> 1.1") not in renderer.logs

    consumer_non_tty, _queue = _consumer(monkeypatch, is_tty=False, verbose=True)
    item_non_tty = consumer_non_tty.items["demo"]
    object.__getattribute__(consumer_non_tty, "_handle_status")(
        UpdateEvent.status("demo", "Updated: 1.0 -> 1.1"),
        item_non_tty,
    )
    non_tty_renderer = _renderer(consumer_non_tty)
    assert ("demo", "Updated: 1.0 -> 1.1") in non_tty_renderer.logs

    result = consumer.result
    assert result.updated
    assert result.errors == 0
    assert result.details["demo"] == "updated"
    assert result.source_updates == {}
    assert result.artifact_updates == {}


def test_event_consumer_command_start_line_and_end_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    consumer, _queue = _consumer(monkeypatch, is_tty=True, verbose=True)
    item = consumer.items["demo"]

    update_ref = item.operations[OperationKind.UPDATE_REF]
    update_ref.tail.append("old-tail")
    update_ref.detail_lines.append("old-detail")

    object.__getattribute__(consumer, "_handle_command_start")(
        UpdateEvent(
            source="demo",
            kind=UpdateEventKind.COMMAND_START,
            message="flake-edit --set-ref",
            payload=["flake-edit", "--set-ref"],
        ),
        item,
    )
    assert item.active_command_op == OperationKind.UPDATE_REF
    assert item.last_operation == OperationKind.UPDATE_REF
    assert update_ref.active_commands == 1
    assert list(update_ref.tail) == []
    assert update_ref.detail_lines == []
    renderer = _renderer(consumer)
    assert ("demo", "$ flake-edit --set-ref") in renderer.line_logs

    line_event = UpdateEvent(
        source="demo",
        kind=UpdateEventKind.LINE,
        message="same line",
        stream="stderr",
    )
    object.__getattribute__(consumer, "_handle_line")(line_event, item)
    object.__getattribute__(consumer, "_handle_line")(line_event, item)
    assert list(update_ref.tail) == ["[stderr] same line"]

    object.__getattribute__(consumer, "_handle_command_end")(
        UpdateEvent(
            source="demo",
            kind=UpdateEventKind.COMMAND_END,
            payload="not-a-command-result",
        ),
        item,
    )

    object.__getattribute__(consumer, "_handle_command_end")(
        UpdateEvent(
            source="demo",
            kind=UpdateEventKind.COMMAND_END,
            payload=CommandResult(
                args=["flake-edit", "--set-ref"],
                returncode=0,
                stdout="",
                stderr="",
            ),
        ),
        item,
    )
    assert update_ref.status == "success"
    assert update_ref.active_commands == 0
    assert item.active_command_op is None

    hash_op = item.operations[OperationKind.COMPUTE_HASH]
    hash_op.active_commands = 1
    item.active_command_op = OperationKind.COMPUTE_HASH
    object.__getattribute__(consumer, "_handle_command_end")(
        UpdateEvent(
            source="demo",
            kind=UpdateEventKind.COMMAND_END,
            payload=CommandResult(
                args=["nix", "build", ".#demo"],
                returncode=1,
                stdout="",
                stderr="",
                tail_lines=("line-1", "line-2"),
            ),
        ),
        item,
    )
    assert hash_op.status == "error"
    assert hash_op.detail_lines == [
        "Command failed (exit 1): nix build '.#demo'",
        "Output tail (last 2 lines):",
        "line-1",
        "line-2",
    ]


def test_event_consumer_command_failure_includes_output_tails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    consumer, _queue = _consumer(monkeypatch, is_tty=False)
    item = consumer.items["demo"]
    hash_op = item.operations[OperationKind.COMPUTE_HASH]
    hash_op.active_commands = 1
    item.active_command_op = OperationKind.COMPUTE_HASH

    object.__getattribute__(consumer, "_handle_command_end")(
        UpdateEvent(
            source="demo",
            kind=UpdateEventKind.COMMAND_END,
            payload=CommandResult(
                args=["cmd", "--flag", "value"],
                returncode=2,
                stdout="\n".join(f"out-{index}" for index in range(12)),
                stderr="\n".join(f"err-{index}" for index in range(11)),
                tail_lines=("stream-tail",),
            ),
        ),
        item,
    )

    expected_details = [
        "Command failed (exit 2): cmd --flag value",
        "stdout (last 10 lines):",
        *(f"out-{index}" for index in range(2, 12)),
        "stderr (last 10 lines):",
        *(f"err-{index}" for index in range(1, 11)),
    ]
    assert hash_op.status == "error"
    assert hash_op.detail_lines == expected_details
    assert _renderer(consumer).errors == [("demo", "\n".join(expected_details))]


def test_event_consumer_command_branches_and_source_result_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    consumer, _queue = _consumer(monkeypatch, is_tty=True)
    item = consumer.items["demo"]

    update_ref = item.operations[OperationKind.UPDATE_REF]
    update_ref.active_commands = 1
    update_ref.tail.append("keep-tail")
    update_ref.detail_lines.append("keep-detail")
    object.__getattribute__(consumer, "_handle_command_start")(
        UpdateEvent(
            source="demo",
            kind=UpdateEventKind.COMMAND_START,
            payload=["flake-edit", "--set-ref"],
        ),
        item,
    )
    assert update_ref.active_commands == 2
    assert list(update_ref.tail) == ["keep-tail"]

    # active_commands > 0 on end should not clear active op
    item.active_command_op = OperationKind.UPDATE_REF
    object.__getattribute__(consumer, "_handle_command_end")(
        UpdateEvent(
            source="demo",
            kind=UpdateEventKind.COMMAND_END,
            payload=CommandResult(
                args=["flake-edit", "--set-ref"],
                returncode=0,
                stdout="",
                stderr="",
            ),
        ),
        item,
    )
    assert update_ref.active_commands == 1
    assert item.active_command_op == OperationKind.UPDATE_REF

    # allow_failure skips error status path
    hash_op = item.operations[OperationKind.COMPUTE_HASH]
    hash_op.status = "running"
    object.__getattribute__(consumer, "_handle_command_end")(
        UpdateEvent(
            source="demo",
            kind=UpdateEventKind.COMMAND_END,
            payload=CommandResult(
                args=["nix", "build", ".#demo"],
                returncode=1,
                stdout="",
                stderr="",
                allow_failure=True,
            ),
        ),
        item,
    )
    assert hash_op.status == "running"

    # RESULT with SourceEntry payload routes through _handle_result source path.
    source_result = _source_entry(version="2.0.0", mapping={"x86_64-linux": HASH_B})
    should_skip = object.__getattribute__(consumer, "_handle_result")(
        UpdateEvent.result("demo", payload=source_result),
        item,
    )
    assert not should_skip
    assert consumer.source_updates["demo"].version == "2.0.0"


def test_event_consumer_command_handlers_when_operation_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    consumer, _queue = _consumer(
        monkeypatch,
        op_order=(OperationKind.CHECK_VERSION,),
    )
    item = consumer.items["demo"]

    object.__getattribute__(consumer, "_handle_command_start")(
        UpdateEvent(
            source="demo",
            kind=UpdateEventKind.COMMAND_START,
            payload=["flake-edit"],
        ),
        item,
    )
    assert item.active_command_op is None

    object.__getattribute__(consumer, "_handle_command_end")(
        UpdateEvent(
            source="demo",
            kind=UpdateEventKind.COMMAND_END,
            payload=CommandResult(
                args=["flake-edit"],
                returncode=0,
                stdout="",
                stderr="",
            ),
        ),
        item,
    )
    assert item.operations[OperationKind.CHECK_VERSION].status == "pending"

    object.__getattribute__(consumer, "_handle_line")(
        UpdateEvent(
            source="demo",
            kind=UpdateEventKind.LINE,
            message="line",
            stream="stdout",
        ),
        item,
    )
    renderer = _renderer(consumer)
    assert ("demo", "line") in renderer.line_logs


def test_event_consumer_result_ref_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    consumer, _queue = _consumer(monkeypatch, is_tty=True)
    item = consumer.items["demo"]
    item.operations[OperationKind.UPDATE_REF].status = "running"
    item.operations[OperationKind.REFRESH_LOCK].status = "running"

    # Invalid payload shape returns early and requests skip-render.
    should_skip = object.__getattribute__(consumer, "_handle_ref_result")(
        UpdateEvent.result("demo", payload={"current": "1", "latest": "2"}),
        item,
        {"current": 1, "latest": "2"},
    )
    assert should_skip

    should_skip = object.__getattribute__(consumer, "_handle_result")(
        UpdateEvent.result(
            "demo",
            payload={"current": "1.0.0", "latest": "2.0.0"},
        ),
        item,
    )
    assert not should_skip
    check_op = item.operations[OperationKind.CHECK_VERSION]
    assert check_op.status == "success"
    assert check_op.message == "1.0.0 -> 2.0.0"
    assert item.operations[OperationKind.UPDATE_REF].status == "success"
    assert item.operations[OperationKind.REFRESH_LOCK].status == "success"
    renderer = _renderer(consumer)
    assert ("demo", "Updated: 1.0.0 -> 2.0.0") in renderer.logs


def test_event_consumer_source_result_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    # Version changed path.
    sources = SourcesFile(entries={"demo": _source_entry(version="1.0.0")})
    consumer, _queue = _consumer(monkeypatch, sources=sources, is_tty=True)
    item = consumer.items["demo"]
    changed = _source_entry(version="2.0.0", mapping={"x86_64-linux": HASH_B})
    object.__getattribute__(consumer, "_handle_source_result")(
        UpdateEvent.result("demo", payload=changed), item, changed
    )
    assert consumer.source_updates["demo"] == changed
    assert item.operations[OperationKind.CHECK_VERSION].message == "1.0.0 -> 2.0.0"
    renderer = _renderer(consumer)
    assert ("demo", "Updated: 1.0.0 -> 2.0.0") in renderer.logs

    # Hash-only changed path.
    sources = SourcesFile(entries={"demo": _source_entry(version="1.0.0")})
    consumer_hash, _queue = _consumer(monkeypatch, sources=sources, is_tty=True)
    item_hash = consumer_hash.items["demo"]
    hash_only = _source_entry(version="1.0.0", mapping={"x86_64-linux": HASH_C})
    object.__getattribute__(consumer_hash, "_handle_source_result")(
        UpdateEvent.result("demo", payload=hash_only),
        item_hash,
        hash_only,
    )
    hash_renderer = _renderer(consumer_hash)
    assert (
        "demo",
        "Updated: hash sha256-JnkqDwuC7lNsjafV+jOGfvs8K1xC8rk5CTOW+spjiCA= "
        "-> sha256-DJUI4pMZ7wQTnyOiuDHALmZz7FZtrTbzRzCuNOShmWE=",
    ) in hash_renderer.logs
    # Generic updated path with no old hash to compare.
    no_old = SourcesFile(entries={"demo": _source_entry(version=None, mapping={})})
    consumer_generic, _queue = _consumer(monkeypatch, sources=no_old, is_tty=True)
    item_generic = consumer_generic.items["demo"]
    generic = _source_entry(version="1.0.0", mapping={"x86_64-linux": HASH_A})
    object.__getattribute__(consumer_generic, "_handle_source_result")(
        UpdateEvent.result("demo", payload=generic),
        item_generic,
        generic,
    )
    generic_renderer = _renderer(consumer_generic)
    assert ("demo", "Updated") in generic_renderer.logs


def test_event_consumer_artifact_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Track changed, staged, and batched generated artifact updates."""
    monkeypatch.setattr(
        GeneratedArtifact,
        "resolved_path",
        lambda self, *, repo_root=tmp_path: tmp_path / Path(self.path).name,
    )
    monkeypatch.setattr(
        GeneratedArtifact,
        "repo_relative_path",
        lambda self, *, repo_root=tmp_path: Path(self.path).name,
    )

    artifact_path = tmp_path / "demo.txt"
    artifact_path.write_text("old\n", encoding="utf-8")

    consumer, _queue = _consumer(monkeypatch, is_tty=True)
    item = consumer.items["demo"]
    changed = GeneratedArtifact.text("demo.txt", "new\n")
    object.__getattribute__(consumer, "_handle_artifact")(
        UpdateEvent.artifact("demo", changed),
        item,
    )

    result = consumer.result
    assert result.updated
    assert result.details["demo"] == "updated"
    assert result.artifact_updates["demo"] == (changed,)
    renderer = _renderer(consumer)
    assert ("demo", f"Updated artifact: {artifact_path.name}") in renderer.logs

    staged_same = GeneratedArtifact.text("demo.txt", "new\n")
    staged_changed = GeneratedArtifact.text("demo.txt", "newer\n")
    assert not object.__getattribute__(consumer, "_artifact_changed")(staged_same)
    assert object.__getattribute__(consumer, "_artifact_changed")(staged_changed)

    consumer_same, _queue = _consumer(monkeypatch, is_tty=True)
    item_same = consumer_same.items["demo"]
    same = GeneratedArtifact.text("demo.txt", "old\n")
    object.__getattribute__(consumer_same, "_handle_artifact")(
        UpdateEvent.artifact("demo", same),
        item_same,
    )
    same_result = consumer_same.result
    assert not same_result.updated
    assert same_result.details == {}
    assert same_result.artifact_updates == {}
    same_renderer = _renderer(consumer_same)
    assert ("demo", f"Updated artifact: {artifact_path.name}") not in same_renderer.logs

    consumer_pair, _queue = _consumer(monkeypatch, is_tty=True)
    item_pair = consumer_pair.items["demo"]
    pair_artifacts = [
        GeneratedArtifact.text(f"pair-{index}.txt", f"new-{index}\n")
        for index in range(2)
    ]
    for index in range(2):
        (tmp_path / f"pair-{index}.txt").write_text("old\n", encoding="utf-8")

    object.__getattribute__(consumer_pair, "_handle_artifact")(
        UpdateEvent.artifact("demo", pair_artifacts),
        item_pair,
    )
    pair_renderer = _renderer(consumer_pair)
    assert ("demo", "Updated 2 artifacts: pair-0.txt, pair-1.txt") in pair_renderer.logs

    consumer_many, _queue = _consumer(monkeypatch, is_tty=True)
    item_many = consumer_many.items["demo"]
    many_artifacts = [
        GeneratedArtifact.text(f"artifact-{index}.txt", f"new-{index}\n")
        for index in range(4)
    ]
    for index in range(4):
        (tmp_path / f"artifact-{index}.txt").write_text("old\n", encoding="utf-8")

    assert not object.__getattribute__(consumer_many, "_dispatch")(
        UpdateEvent.artifact("demo", many_artifacts),
        item_many,
    )
    many_renderer = _renderer(consumer_many)
    assert (
        "demo",
        "Updated 4 artifacts: artifact-0.txt, artifact-1.txt, artifact-2.txt, ...",
    ) in many_renderer.logs


def test_event_consumer_result_none_and_other_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    consumer, _queue = _consumer(monkeypatch, is_tty=True)
    item = consumer.items["demo"]

    object.__getattribute__(consumer, "_handle_result")(
        UpdateEvent.result("demo", payload="updated"), item
    )
    assert consumer.update_details["demo"] == "updated"

    item.operations[OperationKind.CHECK_VERSION].status = "pending"
    object.__getattribute__(consumer, "_handle_result")(
        UpdateEvent.result("demo", payload=None), item
    )
    assert consumer.update_details["demo"] == "updated"

    consumer2, _queue = _consumer(monkeypatch, is_tty=True)
    item2 = consumer2.items["demo"]
    object.__getattribute__(consumer2, "_handle_result")(
        UpdateEvent.result("demo", payload=None), item2
    )
    assert consumer2.update_details["demo"] == "no_change"
    assert item2.operations[OperationKind.CHECK_VERSION].status == "no_change"


def test_event_consumer_error_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    consumer, _queue = _consumer(monkeypatch, is_tty=True)
    item = consumer.items["demo"]
    hash_op = item.operations[OperationKind.COMPUTE_HASH]
    hash_op.active_commands = 1
    hash_op.tail.append("tail")
    item.active_command_op = OperationKind.COMPUTE_HASH

    object.__getattribute__(consumer, "_handle_error")(
        UpdateEvent.error("demo", "boom\ntrace line"),
        item,
    )
    assert consumer.errors == 1
    assert consumer.update_details["demo"] == "error"
    assert hash_op.status == "error"
    assert hash_op.message == "boom"
    assert hash_op.active_commands == 0
    assert list(hash_op.tail) == []
    assert hash_op.detail_lines[-1] == "trace line"

    consumer_non_tty, _queue = _consumer(monkeypatch, is_tty=False)
    item_non_tty = consumer_non_tty.items["demo"]
    object.__getattribute__(consumer_non_tty, "_handle_error")(
        UpdateEvent.error("demo", "plain error"), item_non_tty
    )
    non_tty_renderer = _renderer(consumer_non_tty)
    assert ("demo", "plain error") in non_tty_renderer.errors


def test_event_consumer_dispatch_routes_and_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    consumer, _queue = _consumer(monkeypatch, is_tty=True, verbose=True)
    item = consumer.items["demo"]

    assert not object.__getattribute__(consumer, "_dispatch")(
        UpdateEvent.status("demo", "Checking demo (current: 1.0)"),
        item,
    )
    assert not object.__getattribute__(consumer, "_dispatch")(
        UpdateEvent(
            source="demo",
            kind=UpdateEventKind.COMMAND_START,
            payload=["flake-edit"],
        ),
        item,
    )
    assert not object.__getattribute__(consumer, "_dispatch")(
        UpdateEvent(
            source="demo",
            kind=UpdateEventKind.LINE,
            message="line",
            stream="stdout",
        ),
        item,
    )
    assert not object.__getattribute__(consumer, "_dispatch")(
        UpdateEvent(
            source="demo",
            kind=UpdateEventKind.COMMAND_END,
            payload=CommandResult(
                args=["flake-edit"], returncode=0, stdout="", stderr=""
            ),
        ),
        item,
    )
    # Invalid ref-result payload causes skip-render path.
    monkeypatch.setattr(consumer, "_handle_result", lambda _event, _item: True)
    assert object.__getattribute__(consumer, "_dispatch")(
        UpdateEvent.result(
            "demo",
            payload={"current": "1", "latest": "2"},
        ),
        item,
    )
    assert not object.__getattribute__(consumer, "_dispatch")(
        UpdateEvent.error("demo", "boom"), item
    )
    assert not object.__getattribute__(consumer, "_dispatch")(
        UpdateEvent(source="demo", kind=UpdateEventKind.VALUE, payload="ignored"),
        item,
    )


def test_event_consumer_run_non_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    consumer, queue = _consumer(monkeypatch, is_tty=False)

    async def _run() -> ui_consumer_module.ConsumeEventsResult:
        await queue.put(UpdateEvent.status("demo", "Checking demo (current: 1.0)"))
        await queue.put(UpdateEvent.status("missing", "ignored"))
        await queue.put(
            UpdateEvent(source="demo", kind=UpdateEventKind.VALUE, payload="v")
        )
        await queue.put(None)
        return await consumer.run()

    result = asyncio.run(_run())
    assert not result.updated
    assert result.errors == 0
    assert result.details == {}
    assert result.source_updates == {}
    assert result.artifact_updates == {}
    renderer = _renderer(consumer)
    assert renderer.request_calls >= TWO
    assert len(renderer.render_due_calls) >= TWO
    assert renderer.finalized


def test_event_consumer_run_tty_ticker_and_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    consumer, queue = _consumer(monkeypatch, is_tty=True)

    original_sleep = asyncio.sleep
    sleep_calls = 0

    async def _patched_sleep(_delay: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        await original_sleep(0)

    monkeypatch.setattr(ui_consumer_module.asyncio, "sleep", _patched_sleep)

    async def _run_consumer() -> ui_consumer_module.ConsumeEventsResult:
        run_task = asyncio.create_task(consumer.run())
        await queue.put(UpdateEvent.status("demo", "Checking demo (current: 1.0)"))
        await original_sleep(0)
        await queue.put(None)
        return await run_task

    result = asyncio.run(_run_consumer())
    assert not result.updated
    assert result.errors == 0
    assert result.source_updates == {}
    assert result.artifact_updates == {}
    assert sleep_calls >= 1
    renderer = _renderer(consumer)
    assert renderer.finalized

    async def _run_wrapper() -> ui_consumer_module.ConsumeEventsResult:
        wrapped_queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
        await wrapped_queue.put(None)
        return await consume_events(
            wrapped_queue,
            ["demo"],
            SourcesFile(entries={"demo": _source_entry(version="1.0.0")}),
            options=ConsumeEventsOptions(
                item_meta=_meta_for("demo"),
                max_lines=3,
                is_tty=False,
                full_output=False,
                render_interval=0.0,
                build_failure_tail_lines=2,
            ),
        )

    result = asyncio.run(_run_wrapper())
    assert not result.updated
    assert result.errors == 0
    assert result.details == {}
    assert result.source_updates == {}
    assert result.artifact_updates == {}


def test_event_consumer_run_skip_render_and_result_map_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    consumer, queue = _consumer(monkeypatch, is_tty=False)
    item = consumer.items["demo"]

    # _handle_result should skip when result dict has non-string keys.
    bad_payload = cast("Any", {"current": "1", 1: "x"})
    should_skip = object.__getattribute__(consumer, "_handle_result")(
        UpdateEvent.result("demo", payload=bad_payload),
        item,
    )
    assert should_skip

    # If check status is not pending, RESULT none should not overwrite it.
    check_op = item.operations[OperationKind.CHECK_VERSION]
    check_op.status = "success"
    object.__getattribute__(consumer, "_handle_result")(
        UpdateEvent.result("demo", payload=None),
        item,
    )
    assert check_op.status == "success"

    # run loop skip-render path: _dispatch returns True, request_render not called.
    monkeypatch.setattr(consumer, "_dispatch", lambda _event, _item: True)

    async def _run() -> None:
        await queue.put(UpdateEvent.status("demo", "ignored"))
        await queue.put(None)
        _ = await consumer.run()

    asyncio.run(_run())
    renderer = _renderer(consumer)
    assert renderer.request_calls == 0


def test_event_consumer_internal_branch_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    consumer, _queue = _consumer(monkeypatch, is_tty=True)
    item = consumer.items["demo"]

    # _dispatch RESULT false path should not skip render.
    should_skip = object.__getattribute__(consumer, "_dispatch")(
        UpdateEvent.result("demo", payload="updated"),
        item,
    )
    assert not should_skip

    # _handle_ref_result branch when check_op is absent and update/ref ops not running.
    source_only_consumer, _queue = _consumer(
        monkeypatch,
        op_order=(OperationKind.COMPUTE_HASH,),
    )
    source_item = source_only_consumer.items["demo"]
    assert not object.__getattribute__(source_only_consumer, "_handle_ref_result")(
        UpdateEvent.result("demo", payload={"current": "1", "latest": "2"}),
        source_item,
        {"current": "1", "latest": "2"},
    )
    # _handle_source_result branch variants.
    check_consumer, _queue = _consumer(monkeypatch, is_tty=True)
    check_item = check_consumer.items["demo"]
    check_op = check_item.operations[OperationKind.CHECK_VERSION]
    check_op.message = "already-set"
    no_version_entry = _source_entry(version=None, mapping={"x86_64-linux": HASH_A})
    object.__getattribute__(check_consumer, "_handle_source_result")(
        UpdateEvent.result("demo", payload=no_version_entry),
        check_item,
        no_version_entry,
    )
    assert check_op.message == "already-set"

    no_hash_consumer, _queue = _consumer(
        monkeypatch,
        op_order=(OperationKind.CHECK_VERSION,),
    )
    no_hash_item = no_hash_consumer.items["demo"]
    object.__getattribute__(no_hash_consumer, "_handle_source_result")(
        UpdateEvent.result("demo", payload=_source_entry(version="2.0.0")),
        no_hash_item,
        _source_entry(version="2.0.0"),
    )

    no_check_consumer, _queue = _consumer(
        monkeypatch,
        op_order=(OperationKind.COMPUTE_HASH,),
    )
    no_check_item = no_check_consumer.items["demo"]
    object.__getattribute__(no_check_consumer, "_handle_source_result")(
        UpdateEvent.result("demo", payload=_source_entry(version="2.0.0")),
        no_check_item,
        _source_entry(version="2.0.0"),
    )

    same_version_consumer, _queue = _consumer(monkeypatch, is_tty=True)
    same_version_item = same_version_consumer.items["demo"]
    same_check = same_version_item.operations[OperationKind.CHECK_VERSION]
    same_check.message = "already-set"
    same_version_result = _source_entry(
        version="1.0.0", mapping={"x86_64-linux": HASH_A}
    )
    object.__getattribute__(same_version_consumer, "_handle_source_result")(
        UpdateEvent.result("demo", payload=same_version_result),
        same_version_item,
        same_version_result,
    )
    assert same_check.message == "already-set"

    # _handle_command_end branch without tail lines for nix build failure.
    hash_op = item.operations[OperationKind.COMPUTE_HASH]
    hash_op.status = "running"
    object.__getattribute__(consumer, "_handle_command_end")(
        UpdateEvent(
            source="demo",
            kind=UpdateEventKind.COMMAND_END,
            payload=CommandResult(
                args=["nix", "build", ".#demo"],
                returncode=1,
                stdout="",
                stderr="",
                tail_lines=(),
            ),
        ),
        item,
    )
    assert hash_op.status == "error"


def test_event_consumer_ticker_requests_render(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    consumer, queue = _consumer(monkeypatch, is_tty=True)
    renderer = _renderer(consumer)

    original_sleep = asyncio.sleep
    ticked = asyncio.Event()

    async def _patched_sleep(_delay: float) -> None:
        ticked.set()
        await original_sleep(0)

    monkeypatch.setattr(ui_consumer_module.asyncio, "sleep", _patched_sleep)

    async def _run() -> None:
        task = asyncio.create_task(consumer.run())
        await ticked.wait()
        await original_sleep(0)
        await queue.put(None)
        _ = await task

    asyncio.run(_run())
    assert renderer.request_calls >= 1
    assert len(renderer.render_due_calls) >= 1


def test_event_consumer_error_empty_splitlines_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    consumer, _queue = _consumer(monkeypatch, is_tty=True)
    item = consumer.items["demo"]
    item.last_operation = OperationKind.CHECK_VERSION

    class _Msg:
        def __bool__(self) -> bool:
            return True

        def splitlines(self) -> list[str]:
            return []

    object.__getattribute__(consumer, "_handle_error")(
        UpdateEvent(
            source="demo", kind=UpdateEventKind.ERROR, message=cast("Any", _Msg())
        ),
        item,
    )
