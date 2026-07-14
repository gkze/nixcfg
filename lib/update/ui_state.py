"""State models and status mapping for update UI rendering."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, assert_never

from lib.update.events import StatusInfo, StatusKind, StatusPayload

if TYPE_CHECKING:
    from rich.spinner import Spinner

    from lib.nix.models.sources import SourceEntry
    from lib.update.events import CommandArgs

SummaryStatus = Literal["updated", "error", "no_change"]

_TERMINAL_STATUS_KINDS = frozenset({
    StatusKind.UPDATE_AVAILABLE,
    StatusKind.UPDATED,
})


def is_terminal_status(message: str, payload: object | None = None) -> bool:
    """Return whether a status line represents terminal completion."""
    _ = message
    return (
        isinstance(payload, StatusPayload)
        and payload.info is not None
        and payload.info.kind in _TERMINAL_STATUS_KINDS
    )


class OperationKind(StrEnum):
    """High-level operation phases shown in the renderer."""

    CHECK_VERSION = "check_version"
    UPDATE_REF = "update_ref"
    REFRESH_LOCK = "refresh_lock"
    MATERIALIZE_ARTIFACTS = "materialize_artifacts"
    COMPUTE_HASH = "compute_hash"


OperationStatus = Literal["pending", "running", "no_change", "success", "error"]


_OPERATION_LABELS: dict[OperationKind, str] = {
    OperationKind.CHECK_VERSION: "Checking version",
    OperationKind.UPDATE_REF: "Updating ref",
    OperationKind.REFRESH_LOCK: "Refreshing lock",
    OperationKind.MATERIALIZE_ARTIFACTS: "Refreshing artifacts",
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


_OP_STATUS_PRIORITY = {
    "pending": 0,
    "running": 1,
    "no_change": 2,
    "success": 3,
    "error": 4,
}


@dataclass(frozen=True)
class StatusUpdate:
    status: OperationStatus
    message: str | None = None
    clear_message: bool = False


def _up_to_date_update(info: StatusInfo) -> StatusUpdate | None:
    if info.scope == "hash":
        return StatusUpdate("no_change", clear_message=True)
    if info.value is not None:
        return StatusUpdate("no_change", f"{info.value} (up to date)")
    if info.scope == "artifacts":
        return StatusUpdate("no_change", clear_message=True)
    return None


def _status_update(info: StatusInfo, message: str | None) -> StatusUpdate | None:
    """Map typed status intent onto a renderer status transition."""
    match info.kind:
        case StatusKind.CHECKING_CURRENT:
            update = StatusUpdate("running", f"current {info.value}")
        case (
            StatusKind.PINNED_VERSION
            | StatusKind.LATEST_VERSION
            | StatusKind.REFRESH_LOCK
            | StatusKind.COMPUTING_HASH
        ):
            update = StatusUpdate("running", info.value)
        case StatusKind.UPDATE_AVAILABLE:
            update = StatusUpdate("success", f"{info.current} -> {info.latest}")
        case StatusKind.UP_TO_DATE:
            update = _up_to_date_update(info)
        case StatusKind.UPDATED:
            update = StatusUpdate("success", info.value)
        case StatusKind.UPDATING_REF:
            update = StatusUpdate("running", f"{info.current} -> {info.latest}")
        case StatusKind.FETCHING_HASHES:
            update = StatusUpdate("running", "all platforms")
        case (
            StatusKind.UNSUPPORTED_PLATFORM
            | StatusKind.SKIPPED
            | StatusKind.PRESERVED_HASH
            | StatusKind.PRESERVED_DRV_HASH
            | StatusKind.PRESERVED_ARTIFACT
            | StatusKind.PARTIAL_HASHES
            | StatusKind.RETRY
        ):
            update = StatusUpdate("running", message)
        case _ as unreachable:  # pragma: no cover -- StatusKind match is exhaustive
            assert_never(unreachable)
    return update


def command_args_from_payload(payload: object) -> CommandArgs | None:
    """Parse command args payload from an update event."""
    if not isinstance(payload, list):
        return None
    args = [item for item in payload if isinstance(item, str)]
    if len(args) == len(payload):
        return args
    return None


def _operation_kind(operation: str | None) -> OperationKind | None:
    if operation is None:
        return None
    try:
        return OperationKind(operation)
    except ValueError:
        return None


def operation_for_status(
    message: str,
    payload: object | None = None,
) -> OperationKind | None:
    """Map a status event payload to its UI operation group."""
    _ = message
    if not isinstance(payload, StatusPayload):
        return None
    return _operation_kind(payload.operation)


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
    update: StatusUpdate,
) -> None:
    _set_operation_status(
        operation,
        update.status,
        message=update.message,
        clear_message=update.clear_message,
    )


def apply_status(item: ItemState, message: str, payload: object | None = None) -> None:
    """Apply a status message update to the matching operation state."""
    if not isinstance(payload, StatusPayload):
        return
    kind = _operation_kind(payload.operation)
    if kind is None:
        return
    operation = item.operations.get(kind)
    if operation is None:
        return
    item.last_operation = kind
    update = (
        StatusUpdate("running", message)
        if payload.info is None
        else _status_update(payload.info, message)
    )
    if update is None:
        return
    _apply_status_rules(operation, update)


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
    "SummaryStatus",
    "apply_status",
    "command_args_from_payload",
    "hash_diff_lines",
    "is_terminal_status",
    "operation_for_command",
]
