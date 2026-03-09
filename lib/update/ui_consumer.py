"""Event consumer driving update UI rendering and summary collection."""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from typing import ClassVar

from lib.nix.models.sources import SourceEntry, SourcesFile
from lib.update.events import (
    CommandResult,
    UpdateEvent,
    UpdateEventKind,
    is_nix_build_command,
)
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
)


@dataclass(frozen=True)
class ConsumeEventsOptions:
    """Options controlling queued UI event consumption and rendering."""

    item_meta: dict[str, ItemMeta]
    max_lines: int
    is_tty: bool
    full_output: bool
    verbose: bool = False
    render_interval: float = 0.05
    build_failure_tail_lines: int = 20
    quiet: bool = False


class EventConsumer:
    """Process queued update events and collect summarized results."""

    _DETAIL_PRIORITY: ClassVar[dict[SummaryStatus, int]] = {
        "no_change": 0,
        "updated": 1,
        "error": 2,
    }

    def __init__(
        self,
        queue: asyncio.Queue[UpdateEvent | None],
        order: list[str],
        sources: SourcesFile,
        *,
        options: ConsumeEventsOptions,
    ) -> None:
        """Initialize consumer state, item map, and renderer."""
        self._queue = queue
        self._is_tty = options.is_tty
        self._render_interval = options.render_interval
        self.build_failure_tail_lines = options.build_failure_tail_lines
        self._sources = sources

        self.items: dict[str, ItemState] = {
            name: ItemState.from_meta(
                options.item_meta[name], max_lines=options.max_lines
            )
            for name in order
            if name in options.item_meta
        }
        self.updated = False
        self.errors = 0
        self.update_details: dict[str, SummaryStatus] = {}
        self.source_updates: dict[str, SourceEntry] = {}

        self.renderer = Renderer(
            self.items,
            order,
            is_tty=options.is_tty,
            full_output=options.full_output,
            verbose=options.verbose,
            render_interval=options.render_interval,
            quiet=options.quiet,
        )

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
        """Return the aggregate consumer result tuple."""
        return self.updated, self.errors, self.update_details, self.source_updates

    def _handle_status(self, event: UpdateEvent, item: ItemState) -> None:
        if event.message:
            apply_status(item, event.message)
            if is_terminal_status(event.message):
                if not self.renderer.is_tty:
                    self.renderer.log(event.source, event.message)
            elif self.renderer.verbose:
                self.renderer.log_line(event.source, event.message)

    def _handle_command_start(self, event: UpdateEvent, item: ItemState) -> None:
        args = command_args_from_payload(event.payload)
        op_kind = operation_for_command(args)
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
        op_kind = operation_for_command(result.args)
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
        """Handle a RESULT event. Return True to skip post-event render."""
        result = event.payload
        if result is not None:
            if isinstance(result, dict):
                result_map = {
                    key: value for key, value in result.items() if isinstance(key, str)
                }
                if len(result_map) != len(result):
                    return True
                return self._handle_ref_result(
                    event,
                    item,
                    result_map,
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
        current_payload = result_map.get("current")
        latest_payload = result_map.get("latest")
        if not isinstance(current_payload, str) or not isinstance(latest_payload, str):
            return True
        self._set_detail(event.source, "updated")
        check_op = item.operations.get(OperationKind.CHECK_VERSION)
        if check_op:
            check_op.status = "success"
            check_op.message = f"{current_payload} -> {latest_payload}"
            item.last_operation = OperationKind.CHECK_VERSION
        for op_kind in (OperationKind.UPDATE_REF, OperationKind.REFRESH_LOCK):
            op = item.operations.get(op_kind)
            if op and op.status == "running":
                op.status = "success"
        self.renderer.log(
            event.source, f"Updated: {current_payload} -> {latest_payload}"
        )
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
                check_op.message = f"{old_version} -> {new_version}"
            elif new_version:
                check_op.status = "success"
                if check_op.message is None:
                    check_op.message = new_version

        hash_op = item.operations.get(OperationKind.COMPUTE_HASH)
        if hash_op:
            hash_op.status = "success"
            hash_op.detail_lines = hash_diff_lines(old_entry, result)
            hash_op.message = None

        if old_version and new_version and old_version != new_version:
            self.renderer.log(event.source, f"Updated: {old_version} -> {new_version}")
            return

        old_hash = old_entry.hashes.primary_hash() if old_entry else None
        new_hash = result.hashes.primary_hash()
        if old_hash and new_hash and old_hash != new_hash:
            self.renderer.log(event.source, f"Updated: hash {old_hash} -> {new_hash}")
        else:
            self.renderer.log(event.source, "Updated")

    def _handle_error(self, event: UpdateEvent, item: ItemState) -> None:
        self.errors += 1
        self._set_detail(event.source, "error")
        full_message = event.message or "Unknown error"
        message_lines = full_message.splitlines()
        message = full_message
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
            log_message = full_message if not self.renderer.is_tty else message
            self.renderer.log_error(event.source, log_message)

    def _dispatch(self, event: UpdateEvent, item: ItemState) -> bool:
        """Dispatch an event to its kind handler."""
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
        """Consume events until sentinel and return aggregate results."""
        renderer = self.renderer

        async def _render_ticker() -> None:
            while True:
                await asyncio.sleep(self._render_interval)
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


async def consume_events(
    queue: asyncio.Queue[UpdateEvent | None],
    order: list[str],
    sources: SourcesFile,
    *,
    options: ConsumeEventsOptions,
) -> tuple[bool, int, dict[str, SummaryStatus], dict[str, SourceEntry]]:
    """Consume queued update events and return aggregate UI/update state."""
    consumer = EventConsumer(
        queue,
        order,
        sources,
        options=options,
    )
    return await consumer.run()


__all__ = ["ConsumeEventsOptions", "EventConsumer", "consume_events"]
