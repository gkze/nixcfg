"""Deterministic triage of TCC entries into typed verdicts.

Every piece of judgment from the manual audit is encoded here as data and
rules: bundle-id identity (via the installed-bundle index and a
LaunchServices registration lookup), pane visibility, display-name
ambiguity, and a policy keep-list. ``classify`` performs no I/O beyond
what its inputs carry, so verdicts are fully reproducible.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from lib.macos.tcc_audit.model import ClientKind, Service, TCCEntry

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from lib.macos.tcc_audit.bundles import BundleIndex
    from lib.macos.tcc_audit.db import UnrecognizedEntry

SYSTEM_CLIENT_PREFIXES: tuple[str, ...] = (
    "/System/",
    "/usr/libexec/",
    "/Library/Apple/",
)
SYSTEM_BUNDLE_PREFIXES: tuple[str, ...] = ("com.apple.",)

DEFAULT_KEEP_PREFIXES: tuple[str, ...] = ("com.rippling.", "com.sentinelone.")
DEFAULT_KEEP_BUNDLE_IDS: frozenset[str] = frozenset({"com.openai.sky.CUAService"})


class Registered(Protocol):
    """LaunchServices registration lookup used to resolve edge cases."""

    def __call__(self, bundle_id: str) -> bool:
        """Return whether LaunchServices still registers this bundle id."""
        ...


@dataclass(frozen=True, slots=True)
class SystemEntry:
    """An Apple-provided entry; never a cleanup candidate."""

    entry: TCCEntry


@dataclass(frozen=True, slots=True)
class Live:
    """A resolvable entry backed by an installed app or existing binary."""

    entry: TCCEntry
    name: str


@dataclass(frozen=True, slots=True)
class HiddenOrphan:
    """An unregistered bundle id: hidden from panes and inert.

    The grant cannot be exercised without the original signed binary, and
    the System Settings UI does not list the row, so pane removal is
    impossible. Purging requires Recovery OS database surgery.
    """

    entry: TCCEntry


@dataclass(frozen=True, slots=True)
class DeadPath:
    """A path client whose binary no longer exists.

    Removable through the pane when the display name is unique.
    """

    entry: TCCEntry
    name: str


@dataclass(frozen=True, slots=True)
class NameAmbiguous:
    """A dead row whose display name is shared with other rows.

    The pane cannot distinguish dead from live, so automated targeting is
    refused.
    """

    entry: TCCEntry
    name: str
    occurrences: int


@dataclass(frozen=True, slots=True)
class PolicyKept:
    """A stale entry protected by policy (company-managed, known-keep)."""

    entry: TCCEntry
    reason: str


@dataclass(frozen=True, slots=True)
class UnrecognizedService:
    """A TCC row this model does not interpret, with the row that failed."""

    entry: UnrecognizedEntry


Verdict = (
    SystemEntry
    | Live
    | HiddenOrphan
    | DeadPath
    | NameAmbiguous
    | PolicyKept
    | UnrecognizedService
)


def _is_system(entry: TCCEntry) -> bool:
    if entry.kind is ClientKind.PATH:
        return entry.client.startswith(SYSTEM_CLIENT_PREFIXES)
    return entry.client.startswith(SYSTEM_BUNDLE_PREFIXES)


def _display_name(
    entry: TCCEntry,
    index: BundleIndex,
    registered: Registered,
) -> str | None:
    """Return the pane display name, or None when the row is hidden."""
    if entry.kind is ClientKind.PATH:
        tail = entry.client.rstrip("/").rsplit("/", 1)[-1]
        return tail or entry.client
    carrier = index.carrier(entry.client)
    if carrier is not None:
        return carrier.name
    if registered(entry.client):
        return entry.client
    return None


def _is_stale(
    entry: TCCEntry,
    index: BundleIndex,
    registered: Registered,
) -> bool:
    if entry.kind is ClientKind.PATH:
        return not Path(entry.client).exists()
    return index.carrier(entry.client) is None and not registered(entry.client)


def _kept_reason(
    entry: TCCEntry,
    keep_prefixes: Sequence[str],
    keep_bundle_ids: frozenset[str],
) -> str | None:
    if any(entry.client.startswith(prefix) for prefix in keep_prefixes):
        return "company-managed identity"
    if entry.client in keep_bundle_ids:
        return "known-keep identity"
    return None


class Auditor:
    """Classify TCC entries into typed verdicts under a fixed policy."""

    def __init__(
        self,
        index: BundleIndex,
        registered: Registered,
        *,
        keep_prefixes: Sequence[str] = DEFAULT_KEEP_PREFIXES,
        keep_bundle_ids: frozenset[str] = DEFAULT_KEEP_BUNDLE_IDS,
    ) -> None:
        """Hold the identity sources and the removal policy."""
        self._index = index
        self._registered = registered
        self._keep_prefixes = tuple(keep_prefixes)
        self._keep_bundle_ids = keep_bundle_ids

    def classify(
        self,
        entries: Sequence[TCCEntry],
        unrecognized: Sequence[UnrecognizedEntry] = (),
    ) -> list[Verdict]:
        """Return one verdict per entry plus unrecognized-entry verdicts."""
        counts = self._count_visible_names(entries)
        verdicts: list[Verdict] = [UnrecognizedService(entry=u) for u in unrecognized]
        verdicts.extend(self._classify_one(entry, counts) for entry in entries)
        return verdicts

    def actionable(self, verdicts: Sequence[Verdict]) -> list[DeadPath]:
        """Return removal candidates: dead paths with unambiguous names."""
        return [v for v in verdicts if isinstance(v, DeadPath)]

    def _count_visible_names(
        self,
        entries: Sequence[TCCEntry],
    ) -> dict[tuple[Service, str], int]:
        counts: dict[tuple[Service, str], int] = {}
        for entry in entries:
            if _is_system(entry):
                continue
            name = _display_name(entry, self._index, self._registered)
            if name is not None:
                counts[(entry.service, name)] = counts.get((entry.service, name), 0) + 1
        return counts

    def _classify_one(
        self,
        entry: TCCEntry,
        counts: Mapping[tuple[Service, str], int],
    ) -> Verdict:
        if _is_system(entry):
            return SystemEntry(entry=entry)
        stale = _is_stale(entry, self._index, self._registered)
        if stale:
            reason = _kept_reason(entry, self._keep_prefixes, self._keep_bundle_ids)
            if reason is not None:
                return PolicyKept(entry=entry, reason=reason)
        name = _display_name(entry, self._index, self._registered)
        if entry.kind is ClientKind.PATH:
            if not stale:
                return Live(entry=entry, name=name or "")
            occurrences = counts.get((entry.service, name or ""), 0)
            if occurrences > 1:
                return NameAmbiguous(
                    entry=entry, name=name or "", occurrences=occurrences
                )
            return DeadPath(entry=entry, name=name or "")
        if stale:
            return HiddenOrphan(entry=entry)
        return Live(entry=entry, name=name or entry.client)
