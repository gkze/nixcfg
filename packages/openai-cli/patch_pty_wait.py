"""Drain openai-cli's final PTY write before an exit is a missing marker.

``Terminal.wait`` reads the PTY and then treats child exit as proof that a
marker will never arrive. On Darwin those two observations land in one turn:
the read that copies ``NEXT_COMMAND`` is the same read that notices the shell
has exited, so the waiter raises even though the marker is already buffered.
The list-activation check then fails while its own error tail shows the marker.

Both supported harnesses keep that shape. The rewrite reads the final write
before the assertion. A marker that never arrives still fails.
"""

from typing import TYPE_CHECKING

from lib.codemods.errors import CodemodError
from lib.codemods.source_patch import (
    SourcePatch,
    apply_patches,
    require_absent,
    run_source_tree_cli,
    select_variant,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

HARNESS_PATH = "scripts/image_picker_harness.py"
_SENTINEL = "Drain the final PTY write before treating child exit as a missing marker."
# One delayed write plus two consecutive empty slices. Two empty slices stop
# the drain, so a marker already copied by the current read is not waited on.
_FINAL_PTY_READS = 4
_FINAL_PTY_SLICE = "0.02"


def _patched_wait(*, exited: str, exit_status: str) -> str:
    """Return ``wait`` that checks the buffer again after child exit."""
    return (
        "    def wait(self, marker, timeout=12, after=0):\n"
        "        deadline = time.monotonic()+timeout\n"
        "        def seen():\n"
        "            return marker in ANSI.sub("
        "'', self.raw[after:].decode('utf-8', 'replace'))\n"
        "        while not seen():\n"
        "            self.read()\n"
        f"            exited = {exited}\n"
        "            if exited and not seen():\n"
        "                # Drain the final PTY write before treating "
        "child exit as a missing marker.\n"
        "                quiet = False\n"
        f"                for _ in range({_FINAL_PTY_READS}):\n"
        "                    pending = len(self.raw)\n"
        f"                    self.read({_FINAL_PTY_SLICE})\n"
        "                    if len(self.raw) == pending:\n"
        "                        if quiet:\n"
        "                            break\n"
        "                        quiet = True\n"
        "                        continue\n"
        "                    quiet = False\n"
        "            if seen():\n"
        "                break\n"
        "            if exited or time.monotonic() > deadline:\n"
        "                raise AssertionError("
        f"f'missing {{marker!r}}; exit={exit_status}; tail={{self.text()[-1800:]}}')\n"
    )


def _wait_patch(*, exited: str, exit_status: str, old: str) -> SourcePatch:
    """Declare the one exact replacement of one upstream ``wait`` method."""
    return SourcePatch(
        HARNESS_PATH,
        old,
        _patched_wait(exited=exited, exit_status=exit_status),
    )


# 1.51.4 records liveness on Terminal.running(). 1.40.0 polls the child.
_LAYOUTS = {
    "running": (
        _wait_patch(
            exited="not self.running()",
            exit_status="{self.child.returncode}",
            old=(
                "    def wait(self, marker, timeout=12, after=0):\n"
                "        deadline = time.monotonic()+timeout\n"
                "        while marker not in ANSI.sub("
                "'', self.raw[after:].decode('utf-8', 'replace')):\n"
                "            self.read()\n"
                "            if not self.running() or time.monotonic() > deadline:\n"
                "                raise AssertionError("
                "f'missing {marker!r}; exit={self.child.returncode}; "
                "tail={self.text()[-1800:]}')\n"
            ),
        ),
    ),
    "poll": (
        _wait_patch(
            exited="self.child.poll() is not None",
            exit_status="{self.child.poll()}",
            old=(
                "    def wait(self, marker, timeout=12, after=0):\n"
                "        deadline = time.monotonic()+timeout\n"
                "        while marker not in ANSI.sub("
                "'', self.raw[after:].decode('utf-8', 'replace')):\n"
                "            self.read()\n"
                "            if self.child.poll() is not None or time.monotonic() > deadline:\n"
                "                raise AssertionError("
                "f'missing {marker!r}; exit={self.child.poll()}; "
                "tail={self.text()[-1800:]}')\n"
            ),
        ),
    ),
}


def harness_layout(source: str) -> str:
    """Return the one supported ``wait`` layout present in ``source``."""
    matching = [
        name
        for name, patches in _LAYOUTS.items()
        if all(source.count(patch.old) == patch.expected_count for patch in patches)
    ]
    if len(matching) != 1:
        found = ", ".join(matching) or "none"
        msg = (
            "expected exactly one openai-cli PTY wait layout "
            f"({', '.join(_LAYOUTS)}), found {found}"
        )
        raise CodemodError(msg)
    return matching[0]


def patch_tree(source_root: Path) -> None:
    """Rewrite the one matching PTY waiter, or fail before writing."""
    require_absent(
        source_root,
        (HARNESS_PATH,),
        _SENTINEL,
        message="openai-cli PTY wait drain was already applied",
    )
    _layout, patches = select_variant(source_root, _LAYOUTS)
    apply_patches(source_root, patches)


def main(argv: Sequence[str] | None = None) -> int:
    """Apply the PTY exit drain to one unpacked openai-cli tree."""
    return run_source_tree_cli(argv, description=__doc__, patch_tree=patch_tree)


if __name__ == "__main__":  # pragma: no cover -- packaged CLI guard
    raise SystemExit(main())
