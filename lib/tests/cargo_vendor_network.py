"""Exercise the built upstream Cargo vendor utility against local HTTP failures.

Run by the utility's Nix passthru test using its own Python dependencies. This
checks the installed patch without importing Nix or network clients in pytest.
"""

# ruff: noqa: PT009, PT027 -- standalone test uses the utility's stdlib-only runner

import hashlib
import runpy
import socket
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import FunctionType
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Iterator

_ARCHIVE = b"complete archive contents\n" * 100
_UTILITY = Path(sys.argv.pop())


@dataclass
class _ServerState:
    mode: str
    requests: int = 0


@contextmanager
def _serve(mode: str) -> Iterator[tuple[str, _ServerState]]:
    state = _ServerState(mode)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            pass

        def do_GET(self) -> None:
            state.requests += 1
            if state.mode == "missing":
                self.send_error(404)
                return
            if state.mode == "throttled" and state.requests == 1:
                self.send_response(429)
                self.send_header("Retry-After", "0")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(_ARCHIVE)))
            self.end_headers()
            interrupted = state.mode == "always-stall" or (
                state.requests == 1 and state.mode in {"stall", "truncate"}
            )
            if interrupted:
                self.wfile.write(_ARCHIVE[:1024])
                self.wfile.flush()
                if state.mode == "truncate":
                    self.connection.shutdown(socket.SHUT_RDWR)
                else:
                    time.sleep(0.3)
                self.close_connection = True
                return
            self.wfile.write(_ARCHIVE)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/archive", state
        finally:
            server.shutdown()
            thread.join()


class CargoVendorNetworkTests(unittest.TestCase):
    """Validate bounded recovery using the installed utility and HTTP transport."""

    def setUp(self) -> None:
        module = runpy.run_path(str(_UTILITY))
        self.download = cast("FunctionType", module["download_file_with_checksum"])
        self.download.__globals__["DOWNLOAD_TIMEOUT"] = (0.2, 0.1)
        self.create_session = cast("FunctionType", module["create_http_session"])

    def test_interrupted_bodies_restart_and_preserve_checksum(self) -> None:
        for mode in ("success", "stall", "truncate", "throttled"):
            with (
                self.subTest(mode=mode),
                tempfile.TemporaryDirectory() as directory,
                _serve(mode) as (url, state),
                self.create_session() as session,
            ):
                destination = Path(directory) / "archive"
                digest = self.download(session, url, destination)
                self.assertEqual(destination.read_bytes(), _ARCHIVE)
                self.assertEqual(digest, hashlib.sha256(_ARCHIVE).hexdigest())
                self.assertEqual(state.requests, 1 if mode == "success" else 2)

    def test_exhausted_body_retries_remove_partial_archive(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            _serve("always-stall") as (url, state),
            self.create_session() as session,
        ):
            destination = Path(directory) / "archive"
            with self.assertRaisesRegex(Exception, "Read timed out"):
                self.download(session, url, destination)
            self.assertEqual(state.requests, 3)
            self.assertFalse(destination.exists())

    def test_missing_archive_is_not_retried(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            _serve("missing") as (url, state),
            self.create_session() as session,
        ):
            destination = Path(directory) / "archive"
            with self.assertRaisesRegex(Exception, "Status code: 404"):
                self.download(session, url, destination)
            self.assertEqual(state.requests, 1)
            self.assertFalse(destination.exists())

    def test_cargo_lock_checksum_still_rejects_wrong_content(self) -> None:
        download_tarball = cast(
            "FunctionType", self.download.__globals__["download_tarball"]
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            _serve("success") as (url, state),
            self.create_session() as session,
        ):
            destination = Path(directory)
            (destination / "tarballs").mkdir()
            download_tarball.__globals__["get_download_url_for_tarball"] = (
                lambda _package: url
            )
            with self.assertRaisesRegex(Exception, "Hash mismatch"):
                download_tarball(
                    session,
                    {"name": "demo", "version": "1", "checksum": "0" * 64},
                    destination,
                )
            self.assertEqual(state.requests, 1)


if __name__ == "__main__":
    unittest.main()
