"""Native Accessibility bridge for reading and driving System Settings.

Requires the responsible host application to hold the Accessibility
permission (``kTCCServiceAccessibility``). Every operation here targets the
live System Settings application through the AX C API; nothing touches the
TCC database. The Touch ID/password prompt macOS raises for TCC
modifications is a separate trusted process and is never automated.
"""

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from AppKit import NSRunningApplication  # ty: ignore[unresolved-import]

# pyobjc is Darwin-only and resolves framework symbols dynamically. ty reports
# the module unresolved on Linux and each member unresolved on Darwin, so keep
# every pyobjc import on the single line that carries its suppression; the
# isort splits stop these from being merged into one parenthesized import.
from ApplicationServices import AXUIElementCopyAttributeValue  # ty: ignore[unresolved-import]

# isort: split
from ApplicationServices import AXUIElementCreateApplication  # ty: ignore[unresolved-import]

# isort: split
from ApplicationServices import AXUIElementPerformAction  # ty: ignore[unresolved-import]

# isort: split
from ApplicationServices import AXUIElementSetAttributeValue  # ty: ignore[unresolved-import]

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ApplicationServices import AXUIElement  # ty: ignore[unresolved-import]

SYSTEM_SETTINGS_BUNDLE_ID = "com.apple.systempreferences"

_ROLE = "AXRole"
_CHILDREN = "AXChildren"
_VALUE = "AXValue"
_SELECTED = "AXSelected"
_PRESS = "AXPress"
_MAX_WALK_DEPTH = 14


def _copy_attribute(element: AXUIElement, attribute: str) -> tuple[bool, object]:
    """Read one AX attribute, normalizing pyobjc's return shape."""
    result = AXUIElementCopyAttributeValue(element, attribute, None)
    if isinstance(result, tuple) and result:
        err = result[0]
        value = result[1] if len(result) > 1 else None
        return err == 0, value
    return False, None


def set_attribute(element: AXUIElement, attribute: str, value: object) -> bool:
    """Write one AX attribute; True when the system confirms the write."""
    return AXUIElementSetAttributeValue(element, attribute, value) == 0


def perform_action(element: AXUIElement, action: str) -> bool:
    """Perform a named AX action; True when the system confirms it."""
    return AXUIElementPerformAction(element, action) == 0


def attr(element: AXUIElement, attribute: str, default: object = None) -> object:
    """Read one AX attribute with a default for unreadable attributes."""
    ok, value = _copy_attribute(element, attribute)
    return value if ok else default


def walk(
    element: AXUIElement,
    max_depth: int = _MAX_WALK_DEPTH,
) -> Iterator[tuple[AXUIElement, str, int]]:
    """Yield ``(element, role, depth)`` for the AX subtree, depth-first."""
    stack: list[tuple[AXUIElement, int]] = [(element, 0)]
    while stack:
        current, depth = stack.pop()
        # AXRole is always a string on a live element; a missing one reads as "".
        role = str(attr(current, _ROLE, "") or "")
        yield current, role, depth
        if depth < max_depth:
            ok, kids = _copy_attribute(current, _CHILDREN)
            # AXChildren is a CFArray; anything else means a malformed element.
            if ok and isinstance(kids, list | tuple) and kids:
                stack.extend((child, depth + 1) for child in reversed(list(kids)))


def _row_parts(element: AXUIElement) -> tuple[str, bool | None]:
    """Extract (display name, toggle state) from an AXRow subtree.

    Returns an empty name and ``None`` toggle for rows without a toggle
    (sidebar rows and window chrome).
    """
    name = ""
    allowed: bool | None = None
    has_toggle = False
    for current, role, _depth in walk(element, max_depth=4):
        if role == "AXStaticText" and not name:
            name = str(attr(current, _VALUE, "") or "")
        if role in {"AXCheckBox", "AXSwitch"}:
            has_toggle = True
            raw = attr(current, _VALUE, None)
            allowed = None if raw is None else bool(raw)
    if not has_toggle:
        return "", None
    return name, allowed


@dataclass(frozen=True, slots=True)
class PaneRow:
    """One toggle-backed app row in a System Settings privacy pane."""

    name: str
    allowed: bool | None
    selected: bool
    element: AXUIElement


class SystemSettingsProcess:
    """The running System Settings application, addressed through AX."""

    def __init__(self, element: AXUIElement, pid: int) -> None:
        """Wrap an AX application element for the given pid."""
        self._element = element
        self.pid = pid

    @classmethod
    def attach(cls) -> SystemSettingsProcess | None:
        """Return the running System Settings, or None if it is not open."""
        apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(
            SYSTEM_SETTINGS_BUNDLE_ID,
        )
        if not apps:
            return None
        pid = apps[0].processIdentifier()
        return cls(
            element=AXUIElementCreateApplication(pid),
            pid=pid,
        )

    def rows(self) -> list[PaneRow]:
        """Return every toggle-backed app row in the frontmost privacy pane.

        Rows are identified by a checkbox/toggle descendant; sidebar rows
        and window chrome carry no toggle and are excluded.
        """
        found: list[PaneRow] = []
        for element, role, _depth in walk(self._element):
            if role != "AXRow":
                continue
            name, allowed = _row_parts(element)
            if allowed is None and not name:
                continue
            found.append(
                PaneRow(
                    name=name,
                    allowed=allowed,
                    selected=bool(attr(element, _SELECTED, default=False)),
                    element=element,
                ),
            )
        return found

    def select(self, row: PaneRow) -> bool:
        """Select a row and verify the selection stuck."""
        if not set_attribute(row.element, _SELECTED, value=True):
            return False
        time.sleep(0.3)
        return bool(attr(row.element, _SELECTED, default=False))

    def button_named(self, name: str) -> AXUIElement | None:
        """Return the AXButton whose title matches, or None."""
        for element, role, _depth in walk(self._element):
            if role != "AXButton":
                continue
            if attr(element, "AXTitle", "") == name:
                return element
        return None

    def press(self, element: AXUIElement) -> bool:
        """Perform the AX press action on an element."""
        return perform_action(element, _PRESS)

    def pane_title(self) -> str:
        """Return the visible pane title, or an empty string."""
        return str(attr(self._element, "AXTitle", "") or "")
