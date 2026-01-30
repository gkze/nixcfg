"""TUI rendering for update progress display."""

from __future__ import annotations

import os
import re
import select
import sys
import termios
import time
import tty
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from lib.events import EventKind, UpdateEvent

if TYPE_CHECKING:
    from rich.console import Console
    from rich.live import Live


DEFAULT_RENDER_INTERVAL = 0.05


# =============================================================================
# Terminal Utilities
# =============================================================================


def is_tty() -> bool:
    """Check if we're running in an interactive terminal."""
    term = os.environ.get("TERM", "")
    return sys.stdout.isatty() and term.lower() not in {"", "dumb"}


def read_cursor_row() -> int | None:
    """Read current cursor row position using ANSI escape sequence.

    Returns None if not in a TTY or if the query fails.
    """
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    fd = sys.stdin.fileno()
    try:
        original = termios.tcgetattr(fd)
    except termios.error:
        return None
    try:
        tty.setcbreak(fd)
        sys.stdout.write("\x1b[6n")
        sys.stdout.flush()
        response = ""
        start = time.monotonic()
        while time.monotonic() - start < 0.05:
            ready, _, _ = select.select([fd], [], [], 0.05)
            if not ready:
                continue
            response += os.read(fd, 32).decode(errors="ignore")
            if "R" in response:
                break
        match = re.search(r"\x1b\[(\d+);(\d+)R", response)
        if match:
            return int(match.group(1))
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, original)
    return None


class TerminalInfo:
    """Cached terminal information."""

    _console: Any = None

    @classmethod
    def _get_console(cls) -> Any:
        """Lazily initialize Rich Console."""
        if cls._console is None:
            from rich.console import Console

            cls._console = Console()
        return cls._console

    @classmethod
    def width(cls) -> int:
        return cls._get_console().width

    @classmethod
    def height(cls) -> int:
        return cls._get_console().height

    @classmethod
    def panel_height(cls) -> int:
        """Calculate available panel height for rendering."""
        override = os.environ.get("UPDATE_PANEL_HEIGHT")
        if override:
            try:
                return max(1, int(override))
            except ValueError:
                pass
        height = cls.height()
        row = read_cursor_row()
        if row is None:
            return max(1, height - 1)
        return max(1, height - row + 1)


def fit_to_width(text: str, width: int) -> str:
    """Truncate text to fit terminal width."""
    if width <= 0:
        return text
    # Reserve the last column to avoid line wrapping on some terminals.
    return text[: max(0, width - 1)]


# =============================================================================
# Source State Tracking
# =============================================================================


@dataclass
class SourceState:
    """State tracking for a single source during updates."""

    status: str = "pending"
    tail: deque[str] = field(default_factory=deque)
    active_commands: int = 0


# =============================================================================
# Renderer
# =============================================================================


class Renderer:
    """Rich-based live renderer for update progress.

    Uses Rich's Live display with SIGWINCH handling for clean resize behavior.
    """

    def __init__(
        self,
        states: dict[str, SourceState],
        order: list[str],
        *,
        is_tty_mode: bool,
        panel_height: int | None = None,
        quiet: bool = False,
    ) -> None:
        from rich.console import Console
        from rich.live import Live
        from rich.text import Text

        self.states = states
        self.order = order
        self.is_tty = is_tty_mode
        self.quiet = quiet
        self._initial_panel_height = panel_height
        self.last_render = 0.0
        self.needs_render = False

        # Rich components - only initialize for TTY mode
        self._console: Console | None = None
        self._live: Live | None = None
        if is_tty_mode and not quiet:
            self._console = Console(force_terminal=True)
            self._live = Live(
                Text(""),
                console=self._console,
                auto_refresh=False,
                transient=True,
            )
            self._live.start()

    def _build_display(self) -> Any:
        """Build the Rich renderable for current state."""
        from rich.console import Group
        from rich.text import Text

        if not self._console:
            return Text("")

        width = self._console.width
        height = self._console.height
        panel_height = self._initial_panel_height or max(1, height - 1)
        max_visible = min(panel_height, height - 1)

        lines: list[Text] = []
        for name in self.order:
            if len(lines) >= max_visible:
                break
            state = self.states[name]
            status = state.status or "pending"

            header = Text()
            header.append(name, style="bold")
            header.append(f": {status}")
            header.truncate(width - 1)
            lines.append(header)

            if len(lines) >= max_visible:
                break

            if state.active_commands > 0:
                for tail_line in state.tail:
                    if len(lines) >= max_visible:
                        break
                    detail = Text(f"  {tail_line}", style="dim")
                    detail.truncate(width - 1)
                    lines.append(detail)

        return Group(*lines)

    def log(self, source: str, message: str, *, stream: str | None = None) -> None:
        """Log a message to stdout when not in TTY mode."""
        if self.is_tty or self.quiet:
            return
        if stream:
            print(f"[{source}][{stream}] {message}")
        else:
            print(f"[{source}] {message}")

    def log_error(self, source: str, message: str) -> None:
        """Log an error message to stderr (always shown unless quiet)."""
        if self.is_tty or self.quiet:
            return
        print(f"[{source}] Error: {message}", file=sys.stderr)

    def request_render(self) -> None:
        """Mark that a render is needed."""
        if self.is_tty:
            self.needs_render = True

    def render_if_due(self, now: float) -> None:
        """Render if enough time has passed since last render."""
        if not self.is_tty or not self.needs_render:
            return
        if now - self.last_render >= DEFAULT_RENDER_INTERVAL:
            self.render()
            self.last_render = now
            self.needs_render = False

    def render(self) -> None:
        """Update the live display with current state."""
        if not self._live:
            return
        self._live.update(self._build_display(), refresh=True)

    def finalize(self) -> None:
        """Stop live display and print final status."""
        if self._live:
            self._live.stop()
            self._live = None
        if self.is_tty and not self.quiet:
            self._print_final_status()

    def _print_final_status(self) -> None:
        """Print final status summary after stopping live display."""
        from rich.console import Console
        from rich.text import Text

        console = Console()
        for name in self.order:
            state = self.states[name]
            status = state.status or "done"
            line = Text()
            line.append(name, style="bold")
            line.append(f": {status}")
            console.print(line)


# =============================================================================
# Output Options
# =============================================================================


@dataclass
class OutputOptions:
    """Control output format and verbosity."""

    json_output: bool = False
    quiet: bool = False
    _console: Any = field(default=None, repr=False)
    _err_console: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        from rich.console import Console

        self._console = Console()
        self._err_console = Console(stderr=True)

    def print(
        self, message: str, *, style: str | None = None, stderr: bool = False
    ) -> None:
        """Print message unless in quiet or json mode."""
        if not self.quiet and not self.json_output:
            console = self._err_console if stderr else self._console
            console.print(message, style=style)

    def print_error(self, message: str) -> None:
        """Print error message (always shown unless json mode)."""
        if not self.json_output:
            self._err_console.print(message, style="red")


# =============================================================================
# Event Processing
# =============================================================================


def process_event(
    event: UpdateEvent,
    state: SourceState,
    renderer: Renderer,
) -> tuple[str | None, bool]:
    """Process an event and update state.

    Returns (update_type, is_error) where update_type is "updated", "error", or None.
    """
    update_type: str | None = None
    is_error = False

    match event.kind:
        case EventKind.STATUS:
            state.status = event.message or state.status
            if event.message:
                renderer.log(event.source, event.message)

        case EventKind.COMMAND_START:
            state.active_commands += 1
            if event.message:
                state.status = event.message
                renderer.log(event.source, event.message)
            if state.active_commands == 1:
                state.tail.clear()

        case EventKind.LINE:
            label = event.stream or "stdout"
            message = event.message or ""
            line_text = f"[{label}] {message}" if label else message
            if state.active_commands > 0:
                if not state.tail or state.tail[-1] != line_text:
                    state.tail.append(line_text)
            renderer.log(event.source, message, stream=label)

        case EventKind.COMMAND_END:
            state.active_commands = max(0, state.active_commands - 1)
            if state.active_commands == 0:
                state.tail.clear()
            from lib.events import CommandResult

            result = event.payload
            if isinstance(result, CommandResult):
                renderer.log(
                    event.source, f"command finished (exit {result.returncode})"
                )

        case EventKind.RESULT:
            result = event.payload
            if result is not None:
                update_type = "updated"
                # Handle various result types for status display
                if isinstance(result, dict):
                    current_ref = result.get("current", "?")
                    latest_ref = result.get("latest", "?")
                    state.status = f"Updated :: {current_ref} => {latest_ref}"
                else:
                    state.status = "Updated."
            else:
                update_type = "no_change"
                if not state.status:
                    state.status = "No updates needed."

        case EventKind.ERROR:
            is_error = True
            update_type = "error"
            message = event.message or "Unknown error"
            state.status = f"Error: {message}"
            state.active_commands = 0
            state.tail.clear()
            renderer.log_error(event.source, message)

    return update_type, is_error
