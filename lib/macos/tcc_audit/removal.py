"""Orchestration for pane-driven TCC row removal.

Removal is always: open the pane, confirm System Settings is showing that
pane, find the uniquely-named row, select it, press the pane's Remove button,
and let macOS ask the user for Touch ID. This module contains no AppleScript
and no TCC writes; the process object is duck-typed so tests can inject fakes
without a live AX session.
"""

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from lib.macos.tcc_audit import settings as settings_module
from lib.macos.tcc_audit.model import pane_label

if TYPE_CHECKING:
    from lib.macos.tcc_audit.accessibility import PaneRow
    from lib.macos.tcc_audit.model import Pane


class PaneProcess(Protocol):
    """The subset of ``SystemSettingsProcess`` removal needs."""

    def pane_title(self) -> str:
        """Return the visible pane's AX title, or an empty string."""

    def rows(self) -> list[PaneRow]:
        """Return the pane rows visible right now."""

    def select(self, row: PaneRow) -> bool:
        """Select a row; True when the selection stuck."""

    def button_named(self, name: str) -> object:
        """Return the named button element, or None."""

    def press(self, element: object) -> bool:
        """Press a button element; True on success."""


@dataclass(frozen=True, slots=True)
class Selection:
    """The row was found uniquely and selected; nothing removed yet."""

    row: PaneRow


@dataclass(frozen=True, slots=True)
class WrongPane:
    """System Settings is showing a pane other than the requested one.

    ``rows()`` walks the whole application AX tree rather than the visible
    pane, so acting while another pane is displayed could select and remove a
    row this command was never aimed at. Removal fails closed instead.
    """

    pane: Pane
    observed: str


@dataclass(frozen=True, slots=True)
class NotFound:
    """No row matched the name."""

    pane: Pane
    name: str


@dataclass(frozen=True, slots=True)
class Refused:
    """Multiple rows share the name; dead cannot be told from live."""

    pane: Pane
    name: str
    occurrences: int


@dataclass(frozen=True, slots=True)
class SelectFailed:
    """The row was found but the AX selection did not stick."""

    pane: Pane
    name: str


@dataclass(frozen=True, slots=True)
class RemoveButtonMissing:
    """No Remove button existed after selection (unexpected pane state)."""

    pane: Pane
    name: str


@dataclass(frozen=True, slots=True)
class PressFailed:
    """The Remove button was clicked but AX did not confirm the press."""

    pane: Pane
    name: str


@dataclass(frozen=True, slots=True)
class Pressed:
    """Remove was pressed; the user must complete the Touch ID prompt."""

    pane: Pane
    name: str


@dataclass(frozen=True, slots=True)
class Unverified:
    """The expected pane was lost after selection, so removal is unverified."""

    pane: Pane
    name: str
    observed: str
    pressed: bool


@dataclass(frozen=True, slots=True)
class Removed:
    """The row disappeared while the expected pane remained visible."""

    pane: Pane
    name: str


@dataclass(frozen=True, slots=True)
class StillPresent:
    """The row remained after the Remove press and auth window.

    The Touch ID prompt was likely cancelled or timed out.
    """

    pane: Pane
    name: str


RemovalOutcome = (
    Selection
    | WrongPane
    | NotFound
    | Refused
    | SelectFailed
    | RemoveButtonMissing
    | PressFailed
    | Pressed
    | Unverified
    | Removed
    | StillPresent
)


def select(
    process: PaneProcess,
    pane: Pane,
    name: str,
) -> Selection | WrongPane | NotFound | Refused | SelectFailed:
    """Find a uniquely-named row and select it in the pane.

    Pane identity is checked before any row is read or touched. The wait
    covers a System Settings window that has not repainted yet, and a title
    that still does not match means the rows on screen belong to another
    pane, so nothing here may be selected.
    """
    observed = settings_module.wait_for_pane(process, pane)
    if observed != pane_label(pane):
        return WrongPane(pane=pane, observed=observed)
    matches = [row for row in process.rows() if row.name == name]
    if not matches:
        return NotFound(pane=pane, name=name)
    if len(matches) > 1:
        return Refused(pane=pane, name=name, occurrences=len(matches))
    row = matches[0]
    if not process.select(row):
        return SelectFailed(pane=pane, name=name)
    return Selection(row=row)


def _post_selection_pane_problem(
    process: PaneProcess,
    pane: Pane,
    name: str,
    *,
    pressed: bool,
) -> Unverified | None:
    """Return an unverified outcome when the selected pane is no longer visible."""
    observed = process.pane_title()
    if observed == pane_label(pane):
        return None
    return Unverified(pane=pane, name=name, observed=observed, pressed=pressed)


def remove(
    process: PaneProcess,
    pane: Pane,
    name: str,
    *,
    dry_run: bool = False,
) -> RemovalOutcome:
    """Select the row and press Remove (unless dry-run).

    ``WrongPane`` comes from ``select`` before any row is touched. An
    ``Unverified`` result means the row was selected but the expected pane was
    lost before the dry-run selection could be confirmed or Remove could be
    pressed. Pressing Remove raises macOS's Touch ID/password prompt.
    """
    selection = select(process, pane, name)
    if not isinstance(selection, Selection):
        return selection
    if dry_run:
        problem = _post_selection_pane_problem(process, pane, name, pressed=False)
        if problem is not None:
            return problem
        return selection

    button = process.button_named("Remove")
    if button is None:
        return RemoveButtonMissing(pane=pane, name=name)
    problem = _post_selection_pane_problem(process, pane, name, pressed=False)
    if problem is not None:
        return problem
    if not process.press(button):
        return PressFailed(pane=pane, name=name)
    return Pressed(pane=pane, name=name)


def remove_and_verify(
    process: PaneProcess,
    pane: Pane,
    name: str,
    *,
    dry_run: bool = False,
    timeout: float = 180.0,
    interval: float = 0.5,
) -> RemovalOutcome:
    """Select, press Remove, and poll until the row actually disappears.

    The poll window covers the Touch ID/password prompt: the row stays
    listed until the user completes authentication, and remains if the
    prompt is cancelled. Every poll revalidates the pane before reading rows,
    and a disappearance counts only after a second pane check. A changed or
    lost pane therefore produces ``Unverified``, never a false ``Removed``.
    """
    outcome = remove(process, pane, name, dry_run=dry_run)
    if not isinstance(outcome, Pressed):
        return outcome
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(interval)
        problem = _post_selection_pane_problem(process, pane, name, pressed=True)
        if problem is not None:
            return problem
        if not any(row.name == name for row in process.rows()):
            problem = _post_selection_pane_problem(process, pane, name, pressed=True)
            if problem is not None:
                return problem
            return Removed(pane=pane, name=name)

    problem = _post_selection_pane_problem(process, pane, name, pressed=True)
    if problem is not None:
        return problem
    return StillPresent(pane=pane, name=name)
