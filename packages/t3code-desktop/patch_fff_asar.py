"""Redirect fff-node's native library lookup out of the Electron ASAR archive.

``fff-node`` resolves its dylib next to its own ``package.json`` and hands that
path to ``ffi-rs``, which calls ``dlopen``. Inside a packaged Electron app the
package lives in ``app.asar``, and ``dlopen`` cannot read the archive, so the
lookup has to point at the ``app.asar.unpacked`` copy that electron-builder
writes for native files.

Upstream carries the same fix as a pnpm patch; this package installs its runtime
dependencies with Bun, which does not apply those patches, so the rewrite runs
here instead. A plain text replacement is used rather than a diff: the packaged
files are third-party build output, and the guard below makes a layout change
fail loudly instead of silently shipping an icon-less, dlopen-broken app.

The seam is matched by shape, not by file: upstream ships both an ESM and a CJS
bundle and the packaged app loads the CJS one, so every named entry point that
carries the lookup gets the same rewrite.
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Final

SEAM: Final = re.compile(r"^(?P<indent>[ \t]*)return binaryPath;$", re.MULTILINE)
REPLACEMENT: Final = (
    "{indent}// ffi-rs dlopen needs the real file outside Electron's ASAR archive.\n"
    "{indent}// The desktop packager unpacks dylibs alongside native Node addons.\n"
    '{indent}return binaryPath.replace(/\\.asar\\//, ".asar.unpacked/");'
)


class FffPatchError(RuntimeError):
    """The expected fff-node lookup code was not found exactly once."""


def patch_source(text: str) -> str:
    """Return *text* with the ASAR-aware dylib lookup applied."""
    matches = list(SEAM.finditer(text))
    if len(matches) != 1:
        msg = f"expected exactly one fff-node dylib lookup, found {len(matches)}"
        raise FffPatchError(msg)
    match = matches[0]
    return (
        text[: match.start()]
        + REPLACEMENT.format(indent=match.group("indent"))
        + text[match.end() :]
    )


def patch_file(path: Path) -> None:
    """Rewrite *path* in place with the ASAR-aware lookup."""
    path.write_text(
        patch_source(path.read_text(encoding="utf-8")),
        encoding="utf-8",
    )


def main() -> int:
    """Patch every named ``fff-node`` entry point that upstream shipped."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        type=Path,
        nargs="+",
        help="fff-node entry points to patch; an absent entry point is skipped.",
    )
    args = parser.parse_args()
    patched = 0
    for path in args.paths:
        if not path.is_file():
            continue
        try:
            patch_file(path)
        except FffPatchError as exc:
            sys.stderr.write(f"{path}: {exc}\n")
            return 1
        patched += 1
    if patched == 0:
        sys.stderr.write(
            "no fff-node entry point found among: "
            + ", ".join(str(path) for path in args.paths)
            + "\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
