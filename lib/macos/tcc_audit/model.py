"""Domain vocabulary for TCC auditing.

All types are frozen and hashable so verdicts can be compared, collected,
and diffed without defensive copying.
"""

from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


class Service(StrEnum):
    """TCC service keys backing the System Settings privacy panes."""

    FULL_DISK_ACCESS = "kTCCServiceSystemPolicyAllFiles"
    ACCESSIBILITY = "kTCCServiceAccessibility"
    SCREEN_RECORDING = "kTCCServiceScreenCapture"
    INPUT_MONITORING = "kTCCServiceListenEvent"
    POST_EVENT = "kTCCServicePostEvent"
    DEVELOPER_TOOL = "kTCCServiceDeveloperTool"


class Pane(StrEnum):
    """System Settings deep-link suffixes for the privacy panes."""

    FULL_DISK_ACCESS = "Privacy_AllFiles"
    ACCESSIBILITY = "Privacy_Accessibility"
    SCREEN_RECORDING = "Privacy_ScreenCapture"
    INPUT_MONITORING = "Privacy_ListenEvent"
    POST_EVENT = "Privacy_PostEvent"
    DEVELOPER_TOOL = "Privacy_DeveloperTool"


# Every service this CLI can classify must also be addressable in the panes:
# a service without a pane would be counted as actionable by `audit` and then
# silently skipped by `clean`, which is the one place the two must agree.
PANE_BY_SERVICE: Final[Mapping[Service, Pane]] = {
    Service.FULL_DISK_ACCESS: Pane.FULL_DISK_ACCESS,
    Service.ACCESSIBILITY: Pane.ACCESSIBILITY,
    Service.SCREEN_RECORDING: Pane.SCREEN_RECORDING,
    Service.INPUT_MONITORING: Pane.INPUT_MONITORING,
    Service.POST_EVENT: Pane.POST_EVENT,
    Service.DEVELOPER_TOOL: Pane.DEVELOPER_TOOL,
}

SERVICE_PAGE_LABELS: Final[Mapping[Service, str]] = {
    Service.FULL_DISK_ACCESS: "Full Disk Access",
    Service.ACCESSIBILITY: "Accessibility",
    Service.SCREEN_RECORDING: "Screen Recording",
    Service.INPUT_MONITORING: "Input Monitoring",
    Service.POST_EVENT: "Send Keystrokes",
    Service.DEVELOPER_TOOL: "Developer Tools",
}


def page_label(service: Service) -> str:
    """Human label for the System Settings page backing this service."""
    return SERVICE_PAGE_LABELS.get(service, service.value)


# System Settings titles each privacy pane with the same human label the audit
# report shows, so the Pane -> AX title map is derived from the single label
# table instead of restating it as a second copy that can drift.
PANE_LABELS: Final[Mapping[Pane, str]] = {
    pane: SERVICE_PAGE_LABELS[service] for service, pane in PANE_BY_SERVICE.items()
}


def pane_label(pane: Pane) -> str:
    """AX title System Settings shows for this privacy pane."""
    return PANE_LABELS[pane]


class Auth(IntEnum):
    """Authorization state recorded in the ``access.auth_value`` column."""

    DENIED = 0
    ALLOWED = 2


class ClientKind(IntEnum):
    """How the TCC row identifies its client."""

    BUNDLE_ID = 0
    PATH = 1


@dataclass(frozen=True, slots=True)
class TCCEntry:
    """One row of the system ``access`` table."""

    service: Service
    kind: ClientKind
    auth: Auth
    client: str


@dataclass(frozen=True, slots=True)
class Bundle:
    """An installed application bundle resolved from a bundle identifier."""

    bundle_id: str
    name: str
    path: Path
