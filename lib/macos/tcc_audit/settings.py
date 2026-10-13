"""System Settings integration: pane deep-links and pane readiness.

Pane opening goes through ``NSWorkspace`` so no shell is involved. The
Touch ID/password prompt that a TCC modification raises is deliberately not
detected here: the row poll in ``removal.remove_and_verify`` observes the
outcome that prompt gates, which needs no SecurityAgent window list.
"""

import time
from typing import TYPE_CHECKING, Protocol

# pyobjc resolves framework symbols dynamically, so static analysis cannot see
# these members; each suppression is scoped to its own import line.
from AppKit import (
    NSRunningApplication,  # ty: ignore[unresolved-import]
    NSWorkspace,  # ty: ignore[unresolved-import]
)
from Foundation import NSURL  # ty: ignore[unresolved-import]

from lib.macos.tcc_audit.model import pane_label

if TYPE_CHECKING:
    from collections.abc import Callable

    from lib.macos.tcc_audit.model import Pane

PANE_URL_TEMPLATE = "x-apple.systempreferences:com.apple.preference.security?{suffix}"
SYSTEM_SETTINGS_APP_NAME = "System Settings"


def open_pane(pane: Pane) -> bool:
    """Open a privacy pane in System Settings via NSWorkspace."""
    url = NSURL.URLWithString_(PANE_URL_TEMPLATE.format(suffix=pane.value))
    return bool(NSWorkspace.sharedWorkspace().openURL_(url))


def system_settings_pid() -> int | None:
    """Return the running System Settings pid, or None."""
    apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(
        "com.apple.systempreferences",
    )
    return apps[0].processIdentifier() if apps else None


def ensure_running(pane: Pane, timeout: float = 10.0) -> bool:
    """Open the pane and wait for System Settings to be running."""
    if system_settings_pid() is not None:
        return open_pane(pane)
    if not open_pane(pane):
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if system_settings_pid() is not None:
            return True
        time.sleep(0.25)
    return False


class PaneTitleSource(Protocol):
    """The pane-title read :func:`wait_for_pane` needs.

    ``SystemSettingsProcess`` satisfies this port structurally, which keeps
    this module free of an accessibility import.
    """

    def pane_title(self) -> str:
        """Return the visible pane's AX title, or an empty string."""


def wait_for_pane(
    process: PaneTitleSource,
    pane: Pane,
    timeout: float = 10.0,
    interval: float = 0.25,
) -> str:
    """Retry reading the visible pane title until it is the requested pane.

    ``ensure_running`` only proves NSWorkspace accepted the deep-link URL, so
    the first read after it can still see the previously open pane while
    System Settings repaints. Returns the last observed title: the caller
    compares it against ``pane_label(pane)`` and must fail closed on a
    mismatch instead of acting on another pane's rows.
    """
    observed = ""

    def shows_requested_pane() -> bool:
        nonlocal observed
        observed = process.pane_title()
        return observed == pane_label(pane)

    _poll(shows_requested_pane, timeout, interval)
    return observed


def _poll(
    predicate: Callable[[], bool],
    timeout: float,
    interval: float,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()
