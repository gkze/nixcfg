"""TTY and non-TTY rendering for update progress events."""

import asyncio
import contextlib
import re
import sys
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, Literal, cast

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text
from rich.tree import Tree

from lib.nix.models.sources import SourceEntry, SourcesFile
from lib.update.events import (
    CommandArgs,
    CommandResult,
    UpdateEvent,
    UpdateEventKind,
    is_nix_build_command,
)

SummaryStatus = Literal["updated", "error", "no_change"]


def _is_terminal_status(message: str) -> bool:
    return message.startswith(
        (
            "Up to date",
            "Updated",
            "Update available",
            "Already at latest",
            "No updates needed",
        ),
    )


class OperationKind(StrEnum):
    """High-level operation phases shown in the renderer."""

    CHECK_VERSION = "check_version"
    UPDATE_REF = "update_ref"
    REFRESH_LOCK = "refresh_lock"
    COMPUTE_HASH = "compute_hash"


OperationStatus = Literal["pending", "running", "no_change", "success", "error"]
type StatusMatcher = Callable[[str], object]


@dataclass(frozen=True)
class StatusRule:
    """Declarative matcher for a status transition."""

    matcher: StatusMatcher
    status: OperationStatus
    formatter: Callable[[Any], str] | None = None
    clear_message: bool = False


_OPERATION_LABELS: dict[OperationKind, str] = {
    OperationKind.CHECK_VERSION: "Checking version",
    OperationKind.UPDATE_REF: "Updating ref",
    OperationKind.REFRESH_LOCK: "Refreshing lock",
    OperationKind.COMPUTE_HASH: "Computing hash",
}


@dataclass
class OperationState:
    """Mutable renderer state for a single operation phase."""

    kind: OperationKind
    label: str
    status: OperationStatus = "pending"
    message: str | None = None
    tail: deque[str] = field(default_factory=deque)
    detail_lines: list[str] = field(default_factory=list)
    active_commands: int = 0
    spinner: Any | None = field(default=None, repr=False)

    def visible(self) -> bool:
        """Return ``True`` when this operation should be rendered."""
        return (
            self.status != "pending"
            or self.message is not None
            or bool(self.detail_lines)
            or self.active_commands > 0
        )


@dataclass(frozen=True)
class ItemMeta:
    """Static metadata for one source item in the UI."""

    name: str
    origin: str
    op_order: tuple[OperationKind, ...]


@dataclass
class ItemState:
    """Runtime rendering state for one source item."""

    name: str
    origin: str
    op_order: tuple[OperationKind, ...]
    operations: dict[OperationKind, OperationState]
    last_operation: OperationKind | None = None
    active_command_op: OperationKind | None = None

    @classmethod
    def from_meta(cls, meta: ItemMeta, *, max_lines: int) -> ItemState:
        """Create initial item state from static metadata."""
        operations = {
            kind: OperationState(
                kind=kind,
                label=_OPERATION_LABELS[kind],
                tail=deque(maxlen=max_lines),
            )
            for kind in meta.op_order
        }
        return cls(
            name=meta.name,
            origin=meta.origin,
            op_order=meta.op_order,
            operations=operations,
        )


_STATUS_UPDATE_AVAILABLE = re.compile(r"Update available: (.+) -> (.+)")
_STATUS_UP_TO_DATE_VERSION = re.compile(r"Up to date \(version: (.+)\)")
_STATUS_UP_TO_DATE_REF = re.compile(r"Up to date \(ref: (.+)\)")
_STATUS_LATEST_VERSION = re.compile(r"Latest version: (.+)")
_STATUS_CHECKING = re.compile(r"Checking .*\(current: (.+)\)")
_STATUS_UPDATE_REF = re.compile(r"Updating ref: (.+) -> (.+)")
_STATUS_UPDATE_INPUT = re.compile(r"Updating flake input '([^']+)'\.*")
_STATUS_COMPUTING_HASH = re.compile(r"Computing hash for (.+)\.")

_OP_STATUS_PRIORITY = {
    "pending": 0,
    "running": 1,
    "no_change": 2,
    "success": 3,
    "error": 4,
}


def _msg_equals(expected: str) -> StatusMatcher:
    return lambda message: message == expected


def _msg_startswith(prefix: str) -> StatusMatcher:
    return lambda message: message.startswith(prefix)


_CHECK_VERSION_STATUS_RULES: tuple[StatusRule, ...] = (
    StatusRule(
        _STATUS_UPDATE_AVAILABLE.match,
        "success",
        lambda m: f"{m.group(1)} → {m.group(2)}",
    ),
    StatusRule(
        _STATUS_UP_TO_DATE_VERSION.match,
        "no_change",
        lambda m: f"{m.group(1)} (up to date)",
    ),
    StatusRule(
        _STATUS_UP_TO_DATE_REF.match,
        "no_change",
        lambda m: f"{m.group(1)} (up to date)",
    ),
    StatusRule(
        _STATUS_LATEST_VERSION.match,
        "running",
        lambda m: m.group(1),
    ),
    StatusRule(
        _STATUS_CHECKING.match,
        "running",
        lambda m: f"current {m.group(1)}",
    ),
)

_UPDATE_REF_STATUS_RULES: tuple[StatusRule, ...] = (
    StatusRule(
        _STATUS_UPDATE_REF.match,
        "running",
        lambda m: f"{m.group(1)} → {m.group(2)}",
    ),
)

_REFRESH_LOCK_STATUS_RULES: tuple[StatusRule, ...] = (
    StatusRule(
        _STATUS_UPDATE_INPUT.match,
        "running",
        lambda m: m.group(1),
    ),
)

_COMPUTE_HASH_STATUS_RULES: tuple[StatusRule, ...] = (
    StatusRule(_msg_equals("Up to date"), "no_change", None, clear_message=True),
    StatusRule(
        _msg_startswith("Fetching hashes"),
        "running",
        lambda _m: "all platforms",
    ),
    StatusRule(
        _STATUS_COMPUTING_HASH.match,
        "running",
        lambda m: m.group(1),
    ),
)


@dataclass(frozen=True)
class _StatusPolicy:
    rules: tuple[StatusRule, ...]
    default_status: OperationStatus
    pass_message: bool


_STATUS_POLICIES: dict[OperationKind, _StatusPolicy] = {
    OperationKind.CHECK_VERSION: _StatusPolicy(
        _CHECK_VERSION_STATUS_RULES,
        default_status="running",
        pass_message=False,
    ),
    OperationKind.UPDATE_REF: _StatusPolicy(
        _UPDATE_REF_STATUS_RULES,
        default_status="running",
        pass_message=False,
    ),
    OperationKind.REFRESH_LOCK: _StatusPolicy(
        _REFRESH_LOCK_STATUS_RULES,
        default_status="running",
        pass_message=False,
    ),
    OperationKind.COMPUTE_HASH: _StatusPolicy(
        _COMPUTE_HASH_STATUS_RULES,
        default_status="running",
        pass_message=True,
    ),
}


def _command_args_from_payload(payload: object) -> CommandArgs | None:
    if isinstance(payload, list) and all(isinstance(item, str) for item in payload):
        return cast("CommandArgs", payload)
    return None


def _operation_for_status(message: str) -> OperationKind | None:
    lowered = message.lower()
    if lowered.startswith(
        (
            "checking ",
            "fetching latest",
            "latest version",
            "update available",
            "up to date (version",
            "up to date (ref",
        ),
    ):
        return OperationKind.CHECK_VERSION
    if lowered.startswith("updating ref"):
        return OperationKind.UPDATE_REF
    if lowered.startswith("updating flake input"):
        return OperationKind.REFRESH_LOCK
    if (
        lowered.startswith(
            (
                "fetching hashes",
                "computing hash",
                "build failed",
                "warning:",
            ),
        )
        or message == "Up to date"
    ):
        return OperationKind.COMPUTE_HASH
    return None


def _operation_for_command(args: list[str] | None) -> OperationKind:
    if not args:
        return OperationKind.COMPUTE_HASH
    if args[0] == "flake-edit":
        return OperationKind.UPDATE_REF
    if args[:3] == ["nix", "flake", "lock"] and "--update-input" in args:
        return OperationKind.REFRESH_LOCK
    return OperationKind.COMPUTE_HASH


def _set_operation_status(
    operation: OperationState,
    status: OperationStatus,
    *,
    message: str | None = None,
    clear_message: bool = False,
) -> None:
    if _OP_STATUS_PRIORITY[status] < _OP_STATUS_PRIORITY[operation.status]:
        return
    operation.status = status
    if status != "running":
        operation.spinner = None
    if clear_message:
        operation.message = None
    elif message is not None:
        operation.message = message


def _apply_status_rules(
    operation: OperationState,
    message: str,
    rules: tuple[StatusRule, ...],
    *,
    default_status: OperationStatus = "running",
    default_message: str | None = None,
) -> None:
    for rule in rules:
        match = rule.matcher(message)
        if match:
            formatted = rule.formatter(match) if rule.formatter else None
            _set_operation_status(
                operation,
                rule.status,
                message=formatted,
                clear_message=rule.clear_message,
            )
            return
    _set_operation_status(operation, default_status, message=default_message)


def _apply_status(item: ItemState, message: str) -> None:
    kind = _operation_for_status(message)
    if kind is None:
        return
    operation = item.operations.get(kind)
    if operation is None:
        return
    item.last_operation = kind
    policy = _STATUS_POLICIES[kind]
    rules = policy.rules
    _apply_status_rules(
        operation,
        message,
        rules,
        default_status=policy.default_status,
        default_message=message if policy.pass_message else None,
    )


def _hash_label_map(entry: SourceEntry | None) -> dict[str, str]:
    if entry is None:
        return {}
    hashes = entry.hashes
    if hashes.mapping:
        return dict(hashes.mapping)
    mapping: dict[str, str] = {}
    if hashes.entries:
        for idx, item in enumerate(hashes.entries, start=1):
            if item.git_dep:
                key = f"{item.hash_type}:{item.git_dep}"
            elif item.platform:
                key = item.platform
            else:
                key = item.hash_type
            if key in mapping:
                key = f"{key}#{idx}"
            mapping[key] = item.hash
    return mapping


def _hash_diff_lines(
    old_entry: SourceEntry | None,
    new_entry: SourceEntry | None,
) -> list[str]:
    old_map = _hash_label_map(old_entry)
    new_map = _hash_label_map(new_entry)
    labels = sorted(set(old_map) | set(new_map))
    lines: list[str] = []
    for label in labels:
        old_hash = old_map.get(label)
        new_hash = new_map.get(label)
        if old_hash == new_hash:
            continue
        if old_hash is None:
            lines.append(f"{label} :: <none> → {new_hash}")
        elif new_hash is None:
            lines.append(f"{label} :: {old_hash} → <removed>")
        else:
            lines.append(f"{label} :: {old_hash} → {new_hash}")
    return lines


class Renderer:
    """Render update progress to TTY and collect non-TTY details."""

    def __init__(  # noqa: PLR0913
        self,
        items: dict[str, ItemState],
        order: list[str],
        *,
        is_tty: bool,
        full_output: bool = False,
        verbose: bool = False,
        panel_height: int | None = None,
        render_interval: float,
        quiet: bool = False,
    ) -> None:
        """Initialize renderer state and optional live TTY panel."""
        self.items = items
        self.order = order
        self.is_tty = is_tty
        self.full_output = full_output
        self.verbose = verbose
        self.quiet = quiet
        self._initial_panel_height = panel_height
        self.render_interval = render_interval
        self.last_render = 0.0
        self.needs_render = False

        self._console: Any = None
        self._live: Any = None
        if is_tty and not quiet:
            self._console = Console(force_terminal=True)
            self._live = Live(
                Text(""),
                console=self._console,
                auto_refresh=False,
                transient=True,
            )
            self._live.start()

    def _format_operation_text(self, operation: OperationState) -> str:
        text = f"{operation.label}..."
        message = operation.message
        if operation.status == "success" and not message:
            message = "done"
        elif operation.status == "no_change" and not message:
            message = "no change"
        elif operation.status == "error" and not message:
            message = "failed"
        if message:
            return f"{text} {message}"
        return text

    def _render_operation(self, operation: OperationState) -> RenderableType:

        text = self._format_operation_text(operation)
        if operation.status == "running":
            if operation.spinner is None:
                operation.spinner = Spinner("dots", text, style="cyan")
            else:
                operation.spinner.text = text
            return operation.spinner

        operation.spinner = None

        symbol = "•"
        style = None
        if operation.status == "success":
            symbol = "✓"
            style = "green"
        elif operation.status == "no_change":
            symbol = "•"
            style = "yellow"
        elif operation.status == "error":
            symbol = "✗"
            style = "red"

        line = Text()
        line.append(symbol, style=style)
        line.append(" ")
        line.append(text, style=style)
        return line

    def _build_display(self, *, full_output: bool | None = None) -> RenderableType:  # noqa: C901

        if not self._console:
            return Text("")

        width = self._console.width
        height = self._console.height
        panel_height = self._initial_panel_height or max(1, height - 1)
        max_visible = min(panel_height, height - 1)
        if full_output is None:
            full_output = self.full_output

        trees: list[Any] = []
        for name in self.order:
            item = self.items[name]
            header = Text()
            header.append(name, style="bold")
            header.append(" ")
            header.append(item.origin, style="dim")
            tree = Tree(header, guide_style="dim")

            operations = [
                item.operations[kind]
                for kind in item.op_order
                if item.operations[kind].visible()
            ]
            for operation in operations:
                op_node = tree.add(self._render_operation(operation))
                for detail in operation.detail_lines:
                    op_node.add(Text(detail))
                if operation.active_commands > 0:
                    for tail_line in operation.tail:
                        op_node.add(Text(f"> {tail_line}", style="dim"))

            trees.append(tree)

        renderable: Any = Group(*trees) if trees else Text("")
        if full_output:
            return renderable

        options = self._console.options.update(width=width)
        rendered_lines = self._console.render_lines(renderable, options=options)
        lines: list[Text] = []
        for line in rendered_lines[:max_visible]:
            text = Text()
            for segment in line:
                if segment.text:
                    text.append(segment.text, style=segment.style)
            text.truncate(width - 1)
            lines.append(text)

        return Group(*lines)

    def log_line(self, source: str, message: str) -> None:
        """Print a build log line in verbose non-TTY mode."""
        if not self.is_tty and self.verbose and not self.quiet:
            sys.stdout.write(f"[{source}] {message}\n")

    def _append_detail_line(self, source: str, message: str) -> bool:
        item = self.items.get(source)
        if item is None or item.last_operation is None:
            return False
        operation = item.operations.get(cast("OperationKind", item.last_operation))
        if operation is None:
            return False
        operation.detail_lines.append(message)
        return True

    def log(self, source: str, message: str) -> None:
        """Record an informational message for a source item."""
        if self.is_tty:
            self._append_detail_line(source, message)
        elif not self.quiet:
            sys.stdout.write(f"[{source}] {message}\n")

    def log_error(self, source: str, message: str) -> None:
        """Record an error message for a source item."""
        if self.is_tty:
            self._append_detail_line(source, message)
        elif not self.quiet:
            sys.stderr.write(f"[{source}] ERROR: {message}\n")

    def request_render(self) -> None:
        """Mark the live panel as needing refresh."""
        if self.is_tty:
            self.needs_render = True

    def render_if_due(self, now: float) -> None:
        """Render when the configured interval has elapsed."""
        if not self.is_tty or not self.needs_render:
            return
        if now - self.last_render >= self.render_interval:
            self.render()
            self.last_render = now
            self.needs_render = False

    def finalize(self) -> None:
        """Stop live rendering and print final status when enabled."""
        if self._live:
            self._live.stop()
            self._live = None
        if self.is_tty and not self.quiet:
            self._print_final_status()

    def _print_final_status(self) -> None:
        """Render the final full output snapshot to stdout."""
        _no_color = not sys.stdout.isatty()
        console = Console(no_color=_no_color, highlight=not _no_color)
        console.print(self._build_display(full_output=True))

    def render(self) -> None:
        """Force one live panel render."""
        if not self._live:
            return
        self._live.update(self._build_display(), refresh=True)


class EventConsumer:
    """Processes queued update events, driving the renderer and collecting results."""

    _DETAIL_PRIORITY: ClassVar[dict[SummaryStatus, int]] = {
        "no_change": 0,
        "updated": 1,
        "error": 2,
    }

    def __init__(  # noqa: PLR0913
        self,
        queue: asyncio.Queue[UpdateEvent | None],
        order: list[str],
        sources: SourcesFile,
        *,
        item_meta: dict[str, ItemMeta],
        max_lines: int,
        is_tty: bool,
        full_output: bool,
        verbose: bool = False,
        render_interval: float,
        build_failure_tail_lines: int,
        quiet: bool = False,
    ) -> None:
        """Initialize consumer state, item map, and renderer."""
        self._queue = queue
        self._order = order
        self._sources = sources
        self._is_tty = is_tty
        self._render_interval = render_interval
        self.build_failure_tail_lines = build_failure_tail_lines

        self.items: dict[str, ItemState] = {
            name: ItemState.from_meta(item_meta[name], max_lines=max_lines)
            for name in order
            if name in item_meta
        }
        self.updated = False
        self.errors = 0
        self.update_details: dict[str, SummaryStatus] = {}
        self.source_updates: dict[str, SourceEntry] = {}

        self.renderer = Renderer(
            self.items,
            order,
            is_tty=is_tty,
            full_output=full_output,
            verbose=verbose,
            render_interval=render_interval,
            quiet=quiet,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_detail(self, name: str, status: SummaryStatus) -> None:
        current = self.update_details.get(name)
        if (
            current is None
            or self._DETAIL_PRIORITY[status] > self._DETAIL_PRIORITY[current]
        ):
            self.update_details[name] = status
        if status == "updated":
            self.updated = True

    @property
    def result(
        self,
    ) -> tuple[bool, int, dict[str, SummaryStatus], dict[str, SourceEntry]]:
        """Return the aggregate ``(updated, errors, update_details, source_updates)`` tuple."""
        return self.updated, self.errors, self.update_details, self.source_updates

    # ------------------------------------------------------------------
    # Per-event-kind handlers
    # ------------------------------------------------------------------

    def _handle_status(self, event: UpdateEvent, item: ItemState) -> None:
        if event.message:
            _apply_status(item, event.message)
            if _is_terminal_status(event.message):
                self.renderer.log(event.source, event.message)
            elif self.renderer.verbose:
                self.renderer.log_line(event.source, event.message)

    def _handle_command_start(self, event: UpdateEvent, item: ItemState) -> None:
        args = _command_args_from_payload(event.payload)
        op_kind = _operation_for_command(args)
        operation = item.operations.get(op_kind)
        if operation:
            item.last_operation = op_kind
            item.active_command_op = op_kind
            operation.status = "running"
            operation.active_commands += 1
            if operation.active_commands == 1:
                operation.tail.clear()
                operation.detail_lines.clear()
        if event.message and self.renderer.verbose:
            self.renderer.log_line(event.source, f"$ {event.message}")

    def _handle_line(self, event: UpdateEvent, item: ItemState) -> None:
        label = event.stream or "stdout"
        message = event.message or ""
        line_text = f"[{label}] {message}" if label else message
        op_kind = item.active_command_op or item.last_operation
        if op_kind is None:
            op_kind = OperationKind.COMPUTE_HASH
        operation = item.operations.get(op_kind)
        if (
            operation
            and operation.active_commands > 0
            and (not operation.tail or operation.tail[-1] != line_text)
        ):
            operation.tail.append(line_text)
        self.renderer.log_line(event.source, message)

    def _handle_command_end(self, event: UpdateEvent, item: ItemState) -> None:
        result = event.payload
        if not isinstance(result, CommandResult):
            return
        op_kind = _operation_for_command(result.args)
        operation = item.operations.get(op_kind)
        if not operation:
            return
        operation.active_commands = max(0, operation.active_commands - 1)
        if operation.active_commands == 0:
            operation.tail.clear()
            if item.active_command_op == op_kind:
                item.active_command_op = None
        if result.returncode != 0 and not result.allow_failure:
            operation.status = "error"
            if is_nix_build_command(result.args) and result.tail_lines:
                operation.detail_lines = [
                    f"Output tail (last {self.build_failure_tail_lines} lines):",
                    *result.tail_lines,
                ]
        elif (
            op_kind in (OperationKind.UPDATE_REF, OperationKind.REFRESH_LOCK)
            and operation.status != "error"
        ):
            operation.status = "success"

    def _handle_result(self, event: UpdateEvent, item: ItemState) -> bool:
        """Handle a RESULT event.  Returns True when the loop should ``continue``."""
        result = event.payload
        if result is not None:
            if isinstance(result, dict):
                return self._handle_ref_result(
                    event, item, cast("dict[str, object]", result)
                )
            if isinstance(result, SourceEntry):
                self._handle_source_result(event, item, result)
            else:
                self._set_detail(event.source, "updated")
        else:
            self._set_detail(event.source, "no_change")
            check_op = item.operations.get(OperationKind.CHECK_VERSION)
            if check_op and check_op.status == "pending":
                check_op.status = "no_change"
        return False

    def _handle_ref_result(
        self,
        event: UpdateEvent,
        item: ItemState,
        result_map: dict[str, object],
    ) -> bool:
        """Handle a RESULT whose payload is a ref-update dict.

        Returns True when the main loop should ``continue`` (skip remaining
        processing for this event).
        """
        current_payload = result_map.get("current")
        latest_payload = result_map.get("latest")
        if not isinstance(current_payload, str) or not isinstance(latest_payload, str):
            return True
        self._set_detail(event.source, "updated")
        current_ref = current_payload
        latest_ref = latest_payload
        check_op = item.operations.get(OperationKind.CHECK_VERSION)
        if check_op:
            check_op.status = "success"
            check_op.message = f"{current_ref} → {latest_ref}"
            item.last_operation = OperationKind.CHECK_VERSION
        for op_kind in (OperationKind.UPDATE_REF, OperationKind.REFRESH_LOCK):
            op = item.operations.get(op_kind)
            if op and op.status == "running":
                op.status = "success"
        self.renderer.log(event.source, f"Updated: {current_ref} -> {latest_ref}")
        return False

    def _handle_source_result(
        self,
        event: UpdateEvent,
        item: ItemState,
        result: SourceEntry,
    ) -> None:
        old_entry = self._sources.entries.get(event.source)
        old_version = old_entry.version if old_entry else None
        new_version = result.version
        self.source_updates[event.source] = result
        self._set_detail(event.source, "updated")

        check_op = item.operations.get(OperationKind.CHECK_VERSION)
        if check_op:
            if old_version and new_version and old_version != new_version:
                check_op.status = "success"
                check_op.message = f"{old_version} → {new_version}"
            elif new_version:
                check_op.status = "success"
                if check_op.message is None:
                    check_op.message = new_version

        hash_op = item.operations.get(OperationKind.COMPUTE_HASH)
        if hash_op:
            hash_op.status = "success"
            hash_op.detail_lines = _hash_diff_lines(old_entry, result)
            hash_op.message = None

        if old_version and new_version and old_version != new_version:
            self.renderer.log(
                event.source,
                f"Updated: {old_version} -> {new_version}",
            )
        else:
            old_hash = old_entry.hashes.primary_hash() if old_entry else None
            new_hash = result.hashes.primary_hash()
            if old_hash and new_hash and old_hash != new_hash:
                self.renderer.log(
                    event.source,
                    f"Updated: hash {old_hash} -> {new_hash}",
                )
            else:
                self.renderer.log(event.source, "Updated")

    def _handle_error(self, event: UpdateEvent, item: ItemState) -> None:
        self.errors += 1
        self._set_detail(event.source, "error")
        message = event.message or "Unknown error"
        message_lines = message.splitlines()
        if message_lines:
            message = message_lines[0]
        error_op: OperationState | None = None
        if item.active_command_op:
            error_op = item.operations.get(item.active_command_op)
        if error_op is None and item.last_operation:
            error_op = item.operations.get(item.last_operation)
        if error_op:
            error_op.status = "error"
            error_op.message = message
            error_op.active_commands = 0
            error_op.tail.clear()
            if len(message_lines) > 1:
                error_op.detail_lines.extend(message_lines[1:])
        if not error_op or not self.renderer.is_tty:
            self.renderer.log_error(event.source, message)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _dispatch(self, event: UpdateEvent, item: ItemState) -> bool:
        """Dispatch *event* to the appropriate handler.

        Returns ``True`` when the caller should skip post-event rendering
        (mirrors the ``continue`` that the RESULT handler may trigger).
        """
        kind = event.kind
        if kind is UpdateEventKind.STATUS:
            self._handle_status(event, item)
        elif kind is UpdateEventKind.COMMAND_START:
            self._handle_command_start(event, item)
        elif kind is UpdateEventKind.LINE:
            self._handle_line(event, item)
        elif kind is UpdateEventKind.COMMAND_END:
            self._handle_command_end(event, item)
        elif kind is UpdateEventKind.RESULT:
            if self._handle_result(event, item):
                return True
        elif kind is UpdateEventKind.ERROR:
            self._handle_error(event, item)
        return False

    async def run(
        self,
    ) -> tuple[bool, int, dict[str, SummaryStatus], dict[str, SourceEntry]]:
        """Consume events until a ``None`` sentinel, then return results."""
        render_interval = self._render_interval
        renderer = self.renderer

        async def _render_ticker() -> None:
            while True:
                await asyncio.sleep(render_interval)
                renderer.request_render()
                renderer.render_if_due(time.monotonic())

        ticker = asyncio.create_task(_render_ticker()) if self._is_tty else None
        try:
            while True:
                event = await self._queue.get()
                if event is None:
                    break
                item = self.items.get(event.source)
                if item is None:
                    continue

                if self._dispatch(event, item):
                    continue

                renderer.request_render()
                renderer.render_if_due(time.monotonic())
        finally:
            if ticker is not None:
                ticker.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await ticker
            renderer.finalize()

        return self.result


async def consume_events(  # noqa: PLR0913
    queue: asyncio.Queue[UpdateEvent | None],
    order: list[str],
    sources: SourcesFile,
    *,
    item_meta: dict[str, ItemMeta],
    max_lines: int,
    is_tty: bool,
    full_output: bool,
    verbose: bool = False,
    render_interval: float,
    build_failure_tail_lines: int,
    quiet: bool = False,
) -> tuple[bool, int, dict[str, SummaryStatus], dict[str, SourceEntry]]:
    """Consume queued update events and return aggregate UI/update state."""
    consumer = EventConsumer(
        queue,
        order,
        sources,
        item_meta=item_meta,
        max_lines=max_lines,
        is_tty=is_tty,
        full_output=full_output,
        verbose=verbose,
        render_interval=render_interval,
        build_failure_tail_lines=build_failure_tail_lines,
        quiet=quiet,
    )
    return await consumer.run()
