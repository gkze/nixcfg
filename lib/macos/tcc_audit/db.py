"""Read-only adapter for the system TCC database.

The system database at ``/Library/Application Support/com.apple.TCC/TCC.db``
is world-readable sqlite and SIP-protected against writes; this adapter
therefore only ever opens it in read-only mode.

Rows this audit model does not interpret (unknown service keys, auth
values, or client kinds — possible future macOS additions) are returned
separately as :class:`UnrecognizedEntry` so audits stay observable about
what they skip instead of silently dropping it.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from lib.macos.tcc_audit.model import Auth, ClientKind, Service, TCCEntry

DEFAULT_DB = Path("/Library/Application Support/com.apple.TCC/TCC.db")

_READ_SQL = (
    "SELECT service, client_type, auth_value, client "
    "FROM access ORDER BY service, client"
)


@dataclass(frozen=True, slots=True)
class UnrecognizedEntry:
    """A TCC row this audit model does not interpret, and why.

    Every field holds the raw on-disk value rendered for display, because a
    non-conforming row can carry NULL or BLOB columns. ``reason`` names what the
    audit model could not interpret, so the report does not blame a generic
    "future macOS addition" for a row that failed for another reason.
    """

    service_key: str
    client_type: str
    auth_value: str
    client: str
    reason: str


@dataclass(frozen=True, slots=True)
class TccSnapshot:
    """Known entries plus rows this audit model does not interpret."""

    entries: list[TCCEntry]
    unrecognized: list[UnrecognizedEntry]


def _display(value: object) -> str:
    """Render a raw column value for a diagnostic, whatever its type is."""
    return value if isinstance(value, str) else repr(value)


def _is_integer_column(value: object) -> bool:
    """Return whether ``value`` is an integer column, excluding booleans."""
    return isinstance(value, int) and not isinstance(value, bool)


def _classify_row(
    service: object,
    kind: object,
    auth: object,
    client: object,
) -> TCCEntry | UnrecognizedEntry:
    """Classify one on-disk row, never raising on a non-conforming column."""
    if not (isinstance(service, str) and isinstance(client, str)):
        reason = "service and client must be text columns"
    elif not (_is_integer_column(kind) and _is_integer_column(auth)):
        reason = "client_type and auth_value must be integer columns"
    else:
        try:
            return TCCEntry(Service(service), ClientKind(kind), Auth(auth), client)
        except ValueError:
            reason = "unknown service, client kind, or auth value"
    return UnrecognizedEntry(
        service_key=_display(service),
        client_type=_display(kind),
        auth_value=_display(auth),
        client=_display(client),
        reason=reason,
    )


def read_snapshot(db_path: Path = DEFAULT_DB) -> TccSnapshot:
    """Read the system TCC database into known and unrecognized entries."""
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = connection.execute(_READ_SQL).fetchall()
    finally:
        connection.close()
    entries: list[TCCEntry] = []
    unrecognized: list[UnrecognizedEntry] = []
    for service, kind, auth, client in rows:
        classified = _classify_row(service, kind, auth, client)
        if isinstance(classified, TCCEntry):
            entries.append(classified)
        else:
            unrecognized.append(classified)
    return TccSnapshot(entries=entries, unrecognized=unrecognized)


def read_entries(db_path: Path = DEFAULT_DB) -> list[TCCEntry]:
    """Return known entries only; unrecognized rows are dropped.

    Prefer :func:`read_snapshot` when completeness matters.
    """
    return read_snapshot(db_path).entries
