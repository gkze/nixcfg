"""SQLite storage for immutable candidate files and operational projections.

DBOS owns execution history in this same database. These tables contain domain
data and diagnostics, never a second workflow status or retry ledger.
"""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from typing import TYPE_CHECKING

from dbos import DBOSClient
from pydantic import TypeAdapter

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

type Snapshot = dict[str, tuple[bytes, int, bool] | None]

DATABASE_FILE = "run.sqlite"


class RunStore:
    """Short SQLite transactions; no connection is shared across threads."""

    def __init__(self, directory: Path, *, readonly: bool = False) -> None:
        """Create the application tables alongside DBOS's system tables."""
        self.path = directory.resolve() / DATABASE_FILE
        self.readonly = readonly
        if readonly:
            return
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS update_metadata (
                    name TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS update_content (
                    digest TEXT PRIMARY KEY, content BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS update_events (
                    sequence INTEGER PRIMARY KEY, record TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS update_snapshots (
                    digest TEXT PRIMARY KEY, manifest TEXT NOT NULL
                );
            """)
        self.path.chmod(0o600)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Commit complete writes and close promptly, including on failure."""
        connection = sqlite3.connect(
            f"{self.path.as_uri()}?mode=ro" if self.readonly else str(self.path),
            timeout=30,
            uri=self.readonly,
        )
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def write(self, name: str, value: object) -> None:
        """Replace one projection atomically."""
        with self.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO update_metadata VALUES (?, ?)",
                (name, json.dumps(value, sort_keys=True)),
            )

    def read(self, name: str) -> object:
        """Read metadata, distinguishing a missing record from corrupt JSON."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM update_metadata WHERE name = ?", (name,)
            ).fetchone()
        if row is None:
            msg = f"No {name} record in {self.path}"
            raise FileNotFoundError(msg)
        return json.loads(row[0])

    def append(self, record: object) -> None:
        """Append a redacted diagnostic event in one transaction."""
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO update_events(record) VALUES (?)", (json.dumps(record),)
            )

    def events(self) -> list[dict[str, object]]:
        """Return structured diagnostics in emission order."""
        with self.connect() as connection:
            return [
                TypeAdapter(dict[str, object]).validate_json(row[0])
                for row in connection.execute(
                    "SELECT record FROM update_events ORDER BY sequence"
                )
            ]

    def execution_status(self) -> str | None:
        """Read DBOS's authority without starting workers or loading pickled data."""
        try:
            self.read("request")
        except FileNotFoundError:
            # Library callers can use diagnostics without starting DBOS.
            return None
        client = DBOSClient(
            system_database_url=f"sqlite:///{self.path.as_uri()}?mode=ro&uri=true",
            retry_connection_errors=False,
        )
        try:
            rows = client.list_workflows(
                workflow_ids=["update"], load_input=False, load_output=False
            )
            return rows[0].status if rows else None
        finally:
            client.destroy()

    def save_snapshot(self, files: Mapping[str, tuple[bytes, int, bool] | None]) -> str:
        """Commit a complete tree and its deduplicated contents together."""
        manifest: dict[str, tuple[str, int, bool] | None] = {}
        with self.connect() as connection:
            for path, state in sorted(files.items()):
                if state is None:
                    manifest[path] = None
                    continue
                content, mode, symlink = state
                digest = hashlib.sha256(content).hexdigest()
                connection.execute(
                    "INSERT OR IGNORE INTO update_content VALUES (?, ?)",
                    (digest, content),
                )
                manifest[path] = (digest, mode, symlink)
            payload = json.dumps(manifest, sort_keys=True)
            identity = hashlib.sha256(payload.encode()).hexdigest()
            connection.execute(
                "INSERT OR IGNORE INTO update_snapshots VALUES (?, ?)",
                (identity, payload),
            )
        return identity

    def load_snapshot(self, identity: str) -> Snapshot:
        """Read and verify every file in a checkpoint before writing any file."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT manifest FROM update_snapshots WHERE digest = ?", (identity,)
            ).fetchone()
            if row is None or hashlib.sha256(row[0].encode()).hexdigest() != identity:
                msg = f"Missing or corrupt candidate snapshot: {identity}"
                raise ValueError(msg)
            manifest = TypeAdapter(
                dict[str, tuple[str, int, bool] | None]
            ).validate_json(row[0])
            files: Snapshot = {}
            for path, state in manifest.items():
                if state is None:
                    files[path] = None
                    continue
                digest, mode, symlink = state
                content_row = connection.execute(
                    "SELECT content FROM update_content WHERE digest = ?", (digest,)
                ).fetchone()
                if (
                    content_row is None
                    or hashlib.sha256(content_row[0]).hexdigest() != digest
                ):
                    msg = f"Missing or corrupt candidate content: {digest}"
                    raise ValueError(msg)
                files[path] = (bytes(content_row[0]), mode, symlink)
        return files
