"""Renderer implementation for update UI output.

The live panel shows only what is in flight: items that have started and not
yet finished, followed by the run status line. Finished items are printed once,
above the panel, so the terminal scrollback is the record and the panel never
outgrows the screen. Rich's own refresh thread redraws the panel, so spinners
and elapsed times keep moving while a quiet command runs.
"""

import sys
import threading
from typing import TYPE_CHECKING, Self, cast

from rich.console import Console, ConsoleOptions, Group, RenderableType, RenderResult
from rich.control import Control, ControlType
from rich.live import Live
from rich.live_render import LiveRender, VerticalOverflowMethod
from rich.segment import Segment
from rich.spinner import Spinner
from rich.text import Text
from rich.tree import Tree

if TYPE_CHECKING:
    from collections.abc import Callable

    from lib.update.ui_state import ItemState, OperationState

type StatusLineSource = Callable[[], str | None]


class _ResizeAwareLiveRender(LiveRender):
    """Track rendered line widths so inline Live clearing survives width shrink."""

    def __init__(
        self,
        renderable: RenderableType,
        *,
        console: Console,
        vertical_overflow: VerticalOverflowMethod = "ellipsis",
    ) -> None:
        super().__init__(renderable, vertical_overflow=vertical_overflow)
        self._console = console
        self._line_lengths: tuple[int, ...] = ()

    def _visual_height(self) -> int:
        if self._shape is None:
            return 0
        width = max(1, self._console.width)
        return sum(
            1 if line_length == 0 else (line_length + width - 1) // width
            for line_length in self._line_lengths
        )

    def position_cursor(self) -> Control:
        """Clear the previous frame using the current terminal width."""
        height = self._visual_height()
        if height <= 0:
            return Control()
        return Control(
            ControlType.CARRIAGE_RETURN,
            (ControlType.ERASE_IN_LINE, 2),
            *(
                ((ControlType.CURSOR_UP, 1), (ControlType.ERASE_IN_LINE, 2))
                * (height - 1)
            ),
        )

    def restore_cursor(self) -> Control:
        """Clear the previous transient frame using the current terminal width."""
        height = self._visual_height()
        if height <= 0:
            return Control()
        return Control(
            ControlType.CARRIAGE_RETURN,
            *((ControlType.CURSOR_UP, 1), (ControlType.ERASE_IN_LINE, 2)) * height,
        )

    def __rich_console__(
        self,
        console: Console,
        options: ConsoleOptions,
    ) -> RenderResult:
        renderable = self.renderable
        style = console.get_style(self.style)
        lines = console.render_lines(renderable, options, style=style, pad=False)
        _, height = Segment.get_shape(lines)

        if height > options.size.height:
            if self.vertical_overflow == "crop":
                lines = lines[: options.size.height]
            elif self.vertical_overflow == "ellipsis":
                lines = lines[: (options.size.height - 1)]
                overflow_text = Text(
                    "...",
                    overflow="crop",
                    justify="center",
                    end="",
                    style="live.ellipsis",
                )
                lines.append(list(console.render(overflow_text)))

        self._shape = Segment.get_shape(lines)
        self._line_lengths = tuple(Segment.get_line_length(line) for line in lines)

        new_line = Segment.line()
        last_line = len(lines) - 1
        for index, line in enumerate(lines):
            yield from line
            if index != last_line:
                yield new_line


def _install_resize_aware_live_render(live: Live) -> Live:
    """Swap in a resize-aware LiveRender when Rich exposes the expected hooks."""
    if not all(
        hasattr(live, attr)
        for attr in ("_live_render", "console", "vertical_overflow", "get_renderable")
    ):
        return live

    # Rich clears transient output using the wrapped height from the previous
    # render. After a terminal width shrink that leaves stale rows behind, so
    # replace the private LiveRender with one that recomputes visual height
    # from the current console width before emitting cursor-clearing controls.
    object.__setattr__(
        live,
        "_live_render",
        _ResizeAwareLiveRender(
            live.get_renderable(),
            console=live.console,
            vertical_overflow=live.vertical_overflow,
        ),
    )
    return live


def _refresh_rate(render_interval: float) -> float:
    """Translate a render interval into Rich's refreshes-per-second unit."""
    return max(1.0, 1.0 / render_interval) if render_interval > 0 else 4.0


class Renderer:
    """Render update progress to TTY and collect non-TTY details."""

    def __init__(
        self,
        items: dict[str, ItemState],
        order: list[str],
        *,
        is_tty: bool,
        render_interval: float,
        **kwargs: object,
    ) -> None:
        """Initialize renderer state and optional live TTY panel."""
        full_output_obj = kwargs.pop("full_output", False)
        if not isinstance(full_output_obj, bool):
            msg = "full_output must be a boolean"
            raise TypeError(msg)

        verbose_obj = kwargs.pop("verbose", False)
        if not isinstance(verbose_obj, bool):
            msg = "verbose must be a boolean"
            raise TypeError(msg)

        panel_height_obj = kwargs.pop("panel_height", None)
        if panel_height_obj is not None and not isinstance(panel_height_obj, int):
            msg = "panel_height must be an integer"
            raise TypeError(msg)

        quiet_obj = kwargs.pop("quiet", False)
        if not isinstance(quiet_obj, bool):
            msg = "quiet must be a boolean"
            raise TypeError(msg)

        status_line_obj = kwargs.pop("status_line", None)
        status_line: StatusLineSource | None = None
        if status_line_obj is not None:
            if not callable(status_line_obj):
                msg = "status_line must be callable"
                raise TypeError(msg)
            status_line = cast("StatusLineSource", status_line_obj)

        if kwargs:
            unknown = ", ".join(sorted(kwargs))
            msg = f"Unexpected keyword argument(s): {unknown}"
            raise TypeError(msg)

        self.items = items
        self.order = order
        self.is_tty = is_tty
        self.full_output = full_output_obj
        self.verbose = verbose_obj
        self.quiet = quiet_obj
        self._initial_panel_height = panel_height_obj
        self.render_interval = render_interval
        self._status_line = status_line
        # Serializes item mutation on the event loop against Rich's refresh thread.
        self.lock = threading.RLock()
        self.completed: set[str] = set()

        self._console: Console | None = None
        self._live: Live | None = None
        if is_tty and not self.quiet:
            self._console = Console(force_terminal=True)
            self._live = _install_resize_aware_live_render(
                Live(
                    get_renderable=self._build_display,
                    console=self._console,
                    auto_refresh=True,
                    refresh_per_second=_refresh_rate(render_interval),
                    transient=True,
                )
            )
            self._live.start()

    def _build_item_tree(self, name: str) -> Tree:
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
        return tree

    def _compact_lines(
        self,
        renderable: RenderableType,
        *,
        width: int,
        max_visible: int,
    ) -> RenderableType:
        if self._console is None:
            return Text("")
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

    def _is_started(self, name: str) -> bool:
        item = self.items[name]
        return any(operation.visible() for operation in item.operations.values())

    def _panel_names(self, *, full_output: bool) -> list[str]:
        """Return items shown in the panel: started and unfinished, or all."""
        return [
            name
            for name in self.order
            if name not in self.completed and (full_output or self._is_started(name))
        ]

    def _status_text(self) -> Text | None:
        if self._status_line is None:
            return None
        status = self._status_line()
        if not status:
            return None
        return Text(status, style="bold")

    def _build_display(
        self,
        *,
        full_output: bool | None = None,
    ) -> RenderableType:
        if not self._console:
            return Text("")

        width = self._console.width
        height = self._console.height
        panel_height = self._initial_panel_height or max(1, height - 1)
        max_visible = min(panel_height, height - 1)
        if full_output is None:
            full_output = self.full_output

        with self.lock:
            trees = [
                self._build_item_tree(name)
                for name in self._panel_names(full_output=full_output)
            ]
            status = self._status_text()

        parts: list[RenderableType] = list(trees)
        if status is not None:
            parts.append(status)
        renderable: RenderableType = Group(*parts) if parts else Text("")
        if full_output:
            return renderable

        return self._compact_lines(renderable, width=width, max_visible=max_visible)

    def item_completed(self, name: str) -> None:
        """Print a finished item once, above the live panel, and retire it."""
        with self.lock:
            if name in self.completed or name not in self.items:
                return
            self.completed.add(name)
            if self._console is None:
                return
            tree = self._build_item_tree(name)
        self._console.print(tree)

    def log_line(self, source: str, message: str) -> None:
        """Print a build log line in verbose non-TTY mode."""
        if not self.is_tty and self.verbose and not self.quiet:
            sys.stdout.write(f"[{source}] {message}\n")
            sys.stdout.flush()

    def _append_detail_line(self, source: str, message: str) -> bool:
        item = self.items.get(source)
        if item is None or item.last_operation is None:
            return False
        operation = item.operations.get(item.last_operation)
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
            sys.stdout.flush()

    def log_error(self, source: str, message: str) -> None:
        """Record an error message for a source item."""
        if self.is_tty:
            self._append_detail_line(source, message)
        elif not self.quiet:
            lines = message.splitlines() or [message]
            first_line = lines[0]
            sys.stderr.write(f"[{source}] ERROR: {first_line}\n")
            for line in lines[1:]:
                sys.stderr.write(f"[{source}]       {line}\n")

    def finalize(self) -> None:
        """Print any item that never finished, then stop live rendering."""
        if self._live is None:
            return
        with self.lock:
            unfinished = [
                name
                for name in self.order
                if name not in self.completed and self._is_started(name)
            ]
        for name in unfinished:
            self.item_completed(name)
        self._live.stop()
        self._live = None


class ValidationDisplay:
    """Transient live block for the synchronous validation phases."""

    def __init__(
        self,
        *,
        status_line: StatusLineSource,
        tail: Callable[[], tuple[str, ...]],
        render_interval: float,
    ) -> None:
        """Prepare a live block driven by run status and validation output."""
        self._status_line = status_line
        self._tail = tail
        self._console = Console(force_terminal=True)
        self._live = Live(
            get_renderable=self._build,
            console=self._console,
            auto_refresh=True,
            refresh_per_second=_refresh_rate(render_interval),
            transient=True,
        )

    def _build(self) -> RenderableType:
        parts: list[RenderableType] = []
        status = self._status_line()
        if status:
            parts.append(Text(status, style="bold"))
        parts.extend(Text(f"> {line}", style="dim") for line in self._tail())
        return Group(*parts) if parts else Text("")

    def __enter__(self) -> Self:
        """Start refreshing the block."""
        self._live.start()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        """Stop and clear the block."""
        self._live.stop()


__all__ = ["Renderer", "ValidationDisplay"]
