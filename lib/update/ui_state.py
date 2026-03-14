"""State models and status mapping for update UI rendering."""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    from rich.spinner import Spinner

    from lib.nix.models.sources import SourceEntry
    from lib.update.events import CommandArgs

SummaryStatus = Literal["updated", "error", "no_change"]


def is_terminal_status(message: str) -> bool:
    """Return whether a status line represents terminal completion."""
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
type StatusMatcher = Callable[[str], Any]


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
    spinner: Spinner | None = field(default=None, repr=False)

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
        lambda m: (
            f"{cast('re.Match[str]', m).group(1)} -> {cast('re.Match[str]', m).group(2)}"
        ),
    ),
    StatusRule(
        _STATUS_UP_TO_DATE_VERSION.match,
        "no_change",
        lambda m: f"{cast('re.Match[str]', m).group(1)} (up to date)",
    ),
    StatusRule(
        _STATUS_UP_TO_DATE_REF.match,
        "no_change",
        lambda m: f"{cast('re.Match[str]', m).group(1)} (up to date)",
    ),
    StatusRule(
        _STATUS_LATEST_VERSION.match,
        "running",
        lambda m: cast("re.Match[str]", m).group(1),
    ),
    StatusRule(
        _STATUS_CHECKING.match,
        "running",
        lambda m: f"current {cast('re.Match[str]', m).group(1)}",
    ),
)

_UPDATE_REF_STATUS_RULES: tuple[StatusRule, ...] = (
    StatusRule(
        _STATUS_UPDATE_REF.match,
        "running",
        lambda m: (
            f"{cast('re.Match[str]', m).group(1)} -> {cast('re.Match[str]', m).group(2)}"
        ),
    ),
)

_REFRESH_LOCK_STATUS_RULES: tuple[StatusRule, ...] = (
    StatusRule(
        _STATUS_UPDATE_INPUT.match,
        "running",
        lambda m: cast("re.Match[str]", m).group(1),
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
        lambda m: cast("re.Match[str]", m).group(1),
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


def command_args_from_payload(payload: object) -> CommandArgs | None:
    """Parse command args payload from an update event."""
    if not isinstance(payload, list):
        return None
    args = [item for item in payload if isinstance(item, str)]
    if len(args) == len(payload):
        return args
    return None


def _operation_from_status_payload(payload: object) -> OperationKind | None:
    if not isinstance(payload, dict):
        return None
    operation = cast("dict[str, object]", payload).get("operation")
    if not isinstance(operation, str):
        return None
    try:
        return OperationKind(operation)
    except ValueError:
        return None


def operation_for_status(
    message: str,
    payload: object | None = None,
) -> OperationKind | None:
    """Map a status message to its UI operation group."""
    if payload is not None:
        operation = _operation_from_status_payload(payload)
        if operation is not None:
            return operation
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


def operation_for_command(args: list[str] | None) -> OperationKind:
    """Map command argv to the operation bucket that should render it."""
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


def apply_status(item: ItemState, message: str, payload: object | None = None) -> None:
    """Apply a status message update to the matching operation state."""
    kind = operation_for_status(message, payload)
    if kind is None:
        return
    operation = item.operations.get(kind)
    if operation is None:
        return
    item.last_operation = kind
    policy = _STATUS_POLICIES[kind]
    _apply_status_rules(
        operation,
        message,
        policy.rules,
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


def hash_diff_lines(
    old_entry: SourceEntry | None,
    new_entry: SourceEntry | None,
) -> list[str]:
    """Build per-hash diff lines for UI output."""
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
            lines.append(f"{label} :: <none> -> {new_hash}")
        elif new_hash is None:
            lines.append(f"{label} :: {old_hash} -> <removed>")
        else:
            lines.append(f"{label} :: {old_hash} -> {new_hash}")
    return lines


__all__ = [
    "ItemMeta",
    "ItemState",
    "OperationKind",
    "OperationState",
    "OperationStatus",
    "StatusRule",
    "SummaryStatus",
    "apply_status",
    "command_args_from_payload",
    "hash_diff_lines",
    "is_terminal_status",
    "operation_for_command",
]
