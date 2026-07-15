"""Hold pipes open until macOS starts allocating reduced-capacity pipes."""

# ruff: noqa: INP001 -- standalone helper executed by a Nix derivation

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

MAX_PIPES = 400
PIPE_TARGET_CAPACITY = 64 * 1024
EXPECTED_ARGUMENT_COUNT = 2
ERROR_EXIT_STATUS = 2


def main() -> int:
    """Signal when XNU's pipe KVA high-water mark has been reached."""
    if len(sys.argv) != EXPECTED_ARGUMENT_COUNT:
        sys.stderr.write(f"usage: {sys.argv[0]} READY_FILE\n")
        return ERROR_EXIT_STATUS

    pipes: list[tuple[int, int]] = []
    payload = bytes(PIPE_TARGET_CAPACITY)

    for _ in range(MAX_PIPES):
        read_fd, write_fd = os.pipe()
        pipes.append((read_fd, write_fd))
        os.set_blocking(write_fd, False)

        try:
            written = os.write(write_fd, payload)
        except BlockingIOError:
            written = 0

        if written < len(payload):
            Path(sys.argv[1]).write_text(f"{written}\n", encoding="utf-8")
            while True:
                signal.pause()

    sys.stderr.write(
        f"failed to induce reduced pipe capacity after {MAX_PIPES} pipes\n"
    )
    return ERROR_EXIT_STATUS


if __name__ == "__main__":
    raise SystemExit(main())
