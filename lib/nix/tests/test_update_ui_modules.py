"""Tests for update UI state, rendering, and event consumption."""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass, field
from typing import ClassVar

import pytest
from rich.text import Text

import lib.update.ui_consumer as ui_consumer_module
import lib.update.ui_render as ui_render_module
from lib.nix.models.sources import (
    HashCollection,
    HashEntry,
    HashType,
    SourceEntry,
    SourcesFile,
)
from lib.nix.tests._assertions import check, expect_instance
from lib.update.events import CommandResult, UpdateEvent, UpdateEventKind
from lib.update.ui_consumer import ConsumeEventsOptions, EventConsumer, consume_events
from lib.update.ui_render import Renderer
from lib.update.ui_state import (
    ItemMeta,
    ItemState,
    OperationKind,
    OperationState,
    SummaryStatus,
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
    check(is_terminal_status("Updated: 1.0 -> 1.1"))
    check(is_terminal_status("Up to date"))
    check(not is_terminal_status("Checking demo (current: 1.0)"))

    operation = OperationState(kind=OperationKind.CHECK_VERSION, label="Checking")
    check(not operation.visible())
    operation.message = "current 1.0"
    check(operation.visible())


def test_ui_state_from_meta_and_command_mappers() -> None:
    """Run this test case."""
    meta = ItemMeta(
        name="demo",
        origin="packages/demo",
        op_order=(OperationKind.CHECK_VERSION, OperationKind.COMPUTE_HASH),
    )
    item = ItemState.from_meta(meta, max_lines=2)

    check(item.name == "demo")
    check(item.operations[OperationKind.CHECK_VERSION].label == "Checking version")
    check(item.operations[OperationKind.COMPUTE_HASH].tail.maxlen == TWO)

    check(command_args_from_payload(["nix", "build"]) == ["nix", "build"])
    check(command_args_from_payload(["nix", 1]) is None)
    check(command_args_from_payload("nix build") is None)

    check(operation_for_command(None) == OperationKind.COMPUTE_HASH)
    check(operation_for_command([]) == OperationKind.COMPUTE_HASH)
    check(operation_for_command(["flake-edit"]) == OperationKind.UPDATE_REF)
    check(
        operation_for_command(["nix", "flake", "lock", "--update-input", "demo"])
        == OperationKind.REFRESH_LOCK
    )
    check(operation_for_command(["echo", "ok"]) == OperationKind.COMPUTE_HASH)


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
    check(operation_for_status(message) == kind)


def test_operation_for_status_unknown_message_returns_none() -> None:
    """Run this test case."""
    check(operation_for_status("completely unrelated status") is None)


def test_apply_status_rules_and_status_priority() -> None:
    """Run this test case."""
    item = _item()

    apply_status(item, "Latest version: 1.0.0")
    check_op = item.operations[OperationKind.CHECK_VERSION]
    check(check_op.status == "running")
    check(check_op.message == "1.0.0")

    apply_status(item, "Update available: 1.0.0 -> 1.1.0")
    check(check_op.status == "success")
    check(check_op.message == "1.0.0 -> 1.1.0")

    # Lower-priority running statuses should not overwrite terminal success.
    apply_status(item, "Latest version: 1.1.0")
    check(check_op.status == "success")
    check(check_op.message == "1.0.0 -> 1.1.0")

    apply_status(item, "Fetching hashes for all platforms")
    hash_op = item.operations[OperationKind.COMPUTE_HASH]
    check(hash_op.status == "running")
    check(hash_op.message == "all platforms")

    apply_status(item, "Up to date")
    check(hash_op.status == "no_change")
    check(hash_op.message is None)

    # Default message pass-through for unmatched compute-hash messages.
    item2 = _item()
    apply_status(item2, "warning: cache miss")
    hash_op2 = item2.operations[OperationKind.COMPUTE_HASH]
    check(hash_op2.status == "running")
    check(hash_op2.message == "warning: cache miss")


def test_apply_status_ignores_unknown_or_missing_operations() -> None:
    """Run this test case."""
    item = _item(op_order=(OperationKind.CHECK_VERSION,))
    check_op = item.operations[OperationKind.CHECK_VERSION]

    apply_status(item, "Computing hash for demo.")
    check(check_op.status == "pending")

    apply_status(item, "unrelated status")
    check(check_op.status == "pending")


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

    check(
        "aarch64-linux :: sha256-JnkqDwuC7lNsjafV+jOGfvs8K1xC8rk5CTOW+spjiCA= -> <removed>"
        in lines
    )
    check(
        "darwin :: <none> -> sha256-4TE4PIBEUDUalSRf8yPdc8fM7E7fRJsODG+1DgxhDEo="
        in lines
    )
    check(
        (
            "x86_64-linux :: "
            "sha256-DJUI4pMZ7wQTnyOiuDHALmZz7FZtrTbzRzCuNOShmWE= "
            "-> sha256-kjQj3f5Q9rV0pIBfHmTHvviIh1gcFuP4xGc8MEfMwd0="
        )
        in lines
    )
    check(all("unchanged" not in line for line in lines))


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
    check(
        (
            "sha256#2 :: sha256-cvRBvHRuunNjF07c4GVHl5rRgoTn1qfI/HdJWtOV63M= "
            "-> sha256-kjQj3f5Q9rV0pIBfHmTHvviIh1gcFuP4xGc8MEfMwd0="
        )
        in changed
    )
    check(
        (
            "sha256:tauri :: sha256-4TE4PIBEUDUalSRf8yPdc8fM7E7fRJsODG+1DgxhDEo= "
            "-> sha256-bjX1hF6IhFhRz9fNE2j7KnSxjQKf7bn74QRR48E0hVI="
        )
        in changed
    )
    check(
        (
            "aarch64-darwin :: <none> -> "
            "sha256-u9PE8j5SVfQ+SxG1A5f8A94slq8Nh5S08Y0dWxlI/kI="
        )
        in changed
    )

    added_only = hash_diff_lines(None, new_entry)
    check(any(line.startswith("sha256 :: <none>") for line in added_only))
    removed_only = hash_diff_lines(old_entry, None)
    check(any(line.endswith("-> <removed>") for line in removed_only))


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

    check(object.__getattribute__(renderer, "_live") is not None)
    check(_LiveStub.instances[0].started)

    renderer.render()
    check(_LiveStub.instances[0].updated)

    renderer.request_render()
    renderer.render_if_due(now=0.05)
    check(renderer.needs_render)

    renderer.render_if_due(now=1.0)
    check(not renderer.needs_render)
    check(renderer.last_render == 1.0)

    called: list[bool] = []
    monkeypatch.setattr(renderer, "_print_final_status", lambda: called.append(True))
    renderer.finalize()

    check(_LiveStub.instances[0].stopped)
    check(called == [True])
    check(object.__getattribute__(renderer, "_live") is None)


def test_renderer_formatting_symbols_and_spinner_updates() -> None:
    """Run this test case."""
    item = _item()
    renderer = Renderer({"demo": item}, ["demo"], is_tty=False, render_interval=0.1)

    running = item.operations[OperationKind.CHECK_VERSION]
    running.status = "running"
    running.message = "working"
    first_spinner = object.__getattribute__(renderer, "_render_operation")(running)
    check(running.spinner is not None)
    running.message = "still working"
    second_spinner = object.__getattribute__(renderer, "_render_operation")(running)
    check(first_spinner is second_spinner)

    running.status = "success"
    running.message = None
    success_line = object.__getattribute__(renderer, "_render_operation")(running)
    check(running.spinner is None)
    check(isinstance(success_line, Text))
    check(str(success_line).startswith("✓ "))

    no_change = item.operations[OperationKind.UPDATE_REF]
    no_change.status = "no_change"
    no_change.message = None
    no_change_line = object.__getattribute__(renderer, "_render_operation")(no_change)
    check(isinstance(no_change_line, Text))
    check(str(no_change_line).startswith("• "))

    error = item.operations[OperationKind.REFRESH_LOCK]
    error.status = "error"
    error.message = None
    error_line = object.__getattribute__(renderer, "_render_operation")(error)
    check(isinstance(error_line, Text))
    check(str(error_line).startswith("✗ "))


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
    check(full is not None)
    clipped = object.__getattribute__(renderer, "_build_display")(full_output=False)
    check(clipped is not None)

    object.__setattr__(renderer, "_console", None)
    no_console = object.__getattribute__(renderer, "_build_display")()
    check(isinstance(no_console, Text))


def test_renderer_detail_append_and_tty_logging_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    monkeypatch.setattr(ui_render_module, "Live", _LiveStub)

    item = _item()
    renderer = Renderer({"demo": item}, ["demo"], is_tty=True, render_interval=0.1)

    check(
        not object.__getattribute__(renderer, "_append_detail_line")("missing", "line")
    )
    check(not object.__getattribute__(renderer, "_append_detail_line")("demo", "line"))

    item.last_operation = OperationKind.CHECK_VERSION
    check(object.__getattribute__(renderer, "_append_detail_line")("demo", "line"))
    check(item.operations[OperationKind.CHECK_VERSION].detail_lines == ["line"])

    renderer.log("demo", "info")
    renderer.log_error("demo", "error")
    details = item.operations[OperationKind.CHECK_VERSION].detail_lines
    check(details[-2:] == ["info", "error"])

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
    verbose_renderer.render()

    captured = capsys.readouterr()
    check("[demo] line" in captured.out)
    check("[demo] info" in captured.out)
    check("[demo] ERROR: boom" in captured.err)

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
    check(capsys.readouterr() == ("", ""))


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

    check(captured["no_color"] is True)
    check(captured["highlight"] is False)
    check("renderable" in captured)


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
    check(consumer.update_details["demo"] == "updated")
    check(consumer.updated)

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
    check(("demo", "Latest version: 1.0.0") in renderer.line_logs)
    check(("demo", "Updated: 1.0 -> 1.1") not in renderer.logs)

    consumer_non_tty, _queue = _consumer(monkeypatch, is_tty=False, verbose=True)
    item_non_tty = consumer_non_tty.items["demo"]
    object.__getattribute__(consumer_non_tty, "_handle_status")(
        UpdateEvent.status("demo", "Updated: 1.0 -> 1.1"),
        item_non_tty,
    )
    non_tty_renderer = _renderer(consumer_non_tty)
    check(("demo", "Updated: 1.0 -> 1.1") in non_tty_renderer.logs)

    updated, errors, details, source_updates = consumer.result
    check(updated)
    check(errors == 0)
    check(details["demo"] == "updated")
    check(source_updates == {})


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
    check(item.active_command_op == OperationKind.UPDATE_REF)
    check(item.last_operation == OperationKind.UPDATE_REF)
    check(update_ref.active_commands == 1)
    check(list(update_ref.tail) == [])
    check(update_ref.detail_lines == [])
    renderer = _renderer(consumer)
    check(("demo", "$ flake-edit --set-ref") in renderer.line_logs)

    line_event = UpdateEvent(
        source="demo",
        kind=UpdateEventKind.LINE,
        message="same line",
        stream="stderr",
    )
    object.__getattribute__(consumer, "_handle_line")(line_event, item)
    object.__getattribute__(consumer, "_handle_line")(line_event, item)
    check(list(update_ref.tail) == ["[stderr] same line"])

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
    check(update_ref.status == "success")
    check(update_ref.active_commands == 0)
    check(item.active_command_op is None)

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
    check(hash_op.status == "error")
    check(
        hash_op.detail_lines
        == [
            "Output tail (last 2 lines):",
            "line-1",
            "line-2",
        ]
    )


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
    check(item.active_command_op is None)

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
    check(item.operations[OperationKind.CHECK_VERSION].status == "pending")

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
    check(("demo", "line") in renderer.line_logs)


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
    check(should_skip)

    should_skip = object.__getattribute__(consumer, "_handle_result")(
        UpdateEvent.result(
            "demo",
            payload={"current": "1.0.0", "latest": "2.0.0"},
        ),
        item,
    )
    check(not should_skip)
    check_op = item.operations[OperationKind.CHECK_VERSION]
    check(check_op.status == "success")
    check(check_op.message == "1.0.0 -> 2.0.0")
    check(item.operations[OperationKind.UPDATE_REF].status == "success")
    check(item.operations[OperationKind.REFRESH_LOCK].status == "success")
    renderer = _renderer(consumer)
    check(("demo", "Updated: 1.0.0 -> 2.0.0") in renderer.logs)


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
    check(consumer.source_updates["demo"] == changed)
    check(item.operations[OperationKind.CHECK_VERSION].message == "1.0.0 -> 2.0.0")
    renderer = _renderer(consumer)
    check(("demo", "Updated: 1.0.0 -> 2.0.0") in renderer.logs)

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
    check(
        (
            "demo",
            "Updated: hash sha256-JnkqDwuC7lNsjafV+jOGfvs8K1xC8rk5CTOW+spjiCA= "
            "-> sha256-DJUI4pMZ7wQTnyOiuDHALmZz7FZtrTbzRzCuNOShmWE=",
        )
        in hash_renderer.logs
    )

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
    check(("demo", "Updated") in generic_renderer.logs)


def test_event_consumer_result_none_and_other_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    consumer, _queue = _consumer(monkeypatch, is_tty=True)
    item = consumer.items["demo"]

    object.__getattribute__(consumer, "_handle_result")(
        UpdateEvent.result("demo", payload="updated"), item
    )
    check(consumer.update_details["demo"] == "updated")

    item.operations[OperationKind.CHECK_VERSION].status = "pending"
    object.__getattribute__(consumer, "_handle_result")(
        UpdateEvent.result("demo", payload=None), item
    )
    check(consumer.update_details["demo"] == "updated")

    consumer2, _queue = _consumer(monkeypatch, is_tty=True)
    item2 = consumer2.items["demo"]
    object.__getattribute__(consumer2, "_handle_result")(
        UpdateEvent.result("demo", payload=None), item2
    )
    check(consumer2.update_details["demo"] == "no_change")
    check(item2.operations[OperationKind.CHECK_VERSION].status == "no_change")


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
    check(consumer.errors == 1)
    check(consumer.update_details["demo"] == "error")
    check(hash_op.status == "error")
    check(hash_op.message == "boom")
    check(hash_op.active_commands == 0)
    check(list(hash_op.tail) == [])
    check(hash_op.detail_lines[-1] == "trace line")

    consumer_non_tty, _queue = _consumer(monkeypatch, is_tty=False)
    item_non_tty = consumer_non_tty.items["demo"]
    object.__getattribute__(consumer_non_tty, "_handle_error")(
        UpdateEvent.error("demo", "plain error"), item_non_tty
    )
    non_tty_renderer = _renderer(consumer_non_tty)
    check(("demo", "plain error") in non_tty_renderer.errors)


def test_event_consumer_dispatch_routes_and_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    consumer, _queue = _consumer(monkeypatch, is_tty=True, verbose=True)
    item = consumer.items["demo"]

    check(
        not object.__getattribute__(consumer, "_dispatch")(
            UpdateEvent.status("demo", "Checking demo (current: 1.0)"),
            item,
        )
    )
    check(
        not object.__getattribute__(consumer, "_dispatch")(
            UpdateEvent(
                source="demo",
                kind=UpdateEventKind.COMMAND_START,
                payload=["flake-edit"],
            ),
            item,
        )
    )
    check(
        not object.__getattribute__(consumer, "_dispatch")(
            UpdateEvent(
                source="demo",
                kind=UpdateEventKind.LINE,
                message="line",
                stream="stdout",
            ),
            item,
        )
    )
    check(
        not object.__getattribute__(consumer, "_dispatch")(
            UpdateEvent(
                source="demo",
                kind=UpdateEventKind.COMMAND_END,
                payload=CommandResult(
                    args=["flake-edit"], returncode=0, stdout="", stderr=""
                ),
            ),
            item,
        )
    )

    # Invalid ref-result payload causes skip-render path.
    monkeypatch.setattr(consumer, "_handle_result", lambda _event, _item: True)
    check(
        object.__getattribute__(consumer, "_dispatch")(
            UpdateEvent.result(
                "demo",
                payload={"current": "1", "latest": "2"},
            ),
            item,
        )
    )

    check(
        not object.__getattribute__(consumer, "_dispatch")(
            UpdateEvent.error("demo", "boom"), item
        )
    )
    check(
        not object.__getattribute__(consumer, "_dispatch")(
            UpdateEvent(source="demo", kind=UpdateEventKind.VALUE, payload="ignored"),
            item,
        )
    )


def test_event_consumer_run_non_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    consumer, queue = _consumer(monkeypatch, is_tty=False)

    async def _run() -> tuple[
        bool,
        int,
        dict[str, SummaryStatus],
        dict[str, SourceEntry],
    ]:
        await queue.put(UpdateEvent.status("demo", "Checking demo (current: 1.0)"))
        await queue.put(UpdateEvent.status("missing", "ignored"))
        await queue.put(
            UpdateEvent(source="demo", kind=UpdateEventKind.VALUE, payload="v")
        )
        await queue.put(None)
        return await consumer.run()

    updated, errors, details, source_updates = asyncio.run(_run())
    check(not updated)
    check(errors == 0)
    check(details == {})
    check(source_updates == {})
    renderer = _renderer(consumer)
    check(renderer.request_calls >= TWO)
    check(len(renderer.render_due_calls) >= TWO)
    check(renderer.finalized)


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

    async def _run_consumer() -> tuple[
        bool,
        int,
        dict[str, SummaryStatus],
        dict[str, SourceEntry],
    ]:
        run_task = asyncio.create_task(consumer.run())
        await queue.put(UpdateEvent.status("demo", "Checking demo (current: 1.0)"))
        await original_sleep(0)
        await queue.put(None)
        return await run_task

    _updated, _errors, _details, _source_updates = asyncio.run(_run_consumer())
    check(sleep_calls >= 1)
    renderer = _renderer(consumer)
    check(renderer.finalized)

    async def _run_wrapper() -> tuple[
        bool,
        int,
        dict[str, SummaryStatus],
        dict[str, SourceEntry],
    ]:
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
    check(result == (False, 0, {}, {}))
