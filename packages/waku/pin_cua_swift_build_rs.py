"""Pin Cua's vendored SwiftPM build scripts to the host Xcode toolchain.

Why this exists: ``apple-cf``, ``apple-metal`` and ``screencapturekit`` build
Swift helper packages from their ``build.rs`` by spawning ``swift build``. Those
packages need a Swift 6 toolchain and a macOS 15 SDK; nixpkgs' Darwin SDK
(apple-sdk-14.4, Swift 5.10) cannot compile them, and SwiftPM's own
``sandbox-exec`` cannot nest inside the Nix Darwin sandbox. The ``cua-driver-sdk``
``preBuild`` therefore discovers the host Xcode and exports ``CUA_XCODE_*``;
this codemod makes each reviewed ``swift`` spawn use exactly that toolchain,
SDK and home, and passes ``--disable-sandbox`` to its ``swift build``.

Workaround limits: the build stays impure on the host Xcode, and only the three
reviewed crates below are rewritten, each with exact anchor counts. A new or
renamed vendored crate that spawns ``swift`` fails the build instead of
silently building against the Nix SDK.

Intended lifetime: remove this codemod and the ``CUA_XCODE_*`` exports once
nixpkgs ships a Darwin Swift 6 toolchain and macOS 15 SDK that SwiftPM can use
inside the Nix sandbox, or once these crates stop building Swift from
``build.rs``. Re-review the counts whenever ``cua-driver.lock`` changes.
"""

import re
from typing import TYPE_CHECKING

from lib.codemods.errors import CodemodError
from lib.codemods.source_patch import SourcePatch, apply_patches, run_source_tree_cli

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

_SWIFT_SPAWN = 'Command::new("swift")'
_SWIFT_BUILD_ARGUMENT = '"build",'
_SANDBOX_DISABLED_BUILD_ARGUMENT = '"build", "--disable-sandbox",'
# Environment the spawned swift sees, each value taken from a CUA_XCODE_* export.
_PINNED_SPAWN_ENVIRONMENT = (
    ("DEVELOPER_DIR", "CUA_XCODE_DEVELOPER_DIR"),
    ("SDKROOT", "CUA_XCODE_SDKROOT"),
    ("HOME", "CUA_XCODE_HOME"),
    ("CFFIXED_USER_HOME", "CUA_XCODE_HOME"),
)
# Reviewed crate -> (swift spawns, ``"build",`` arguments) in its build.rs.
SWIFT_BUILD_CRATES = {
    "apple-cf": (1, 1),
    "apple-metal": (1, 1),
    "screencapturekit": (1, 1),
}


def _required_env(name: str) -> str:
    return f'std::env::var("{name}").expect("{name}")'


PINNED_SWIFT_SPAWN = f"Command::new({_required_env('CUA_XCODE_SWIFT')})" + "".join(
    f'.env("{variable}", {_required_env(source)})'
    for variable, source in _PINNED_SPAWN_ENVIRONMENT
)


def _crate_directory(vendor_dir: Path, crate: str) -> str:
    pattern = re.compile(rf"{re.escape(crate)}-[0-9][^/]*")
    matches = sorted(
        entry.name
        for entry in vendor_dir.iterdir()
        if entry.is_dir() and pattern.fullmatch(entry.name)
    )
    if len(matches) != 1:
        msg = f"expected one vendored {crate} crate in {vendor_dir}, found {matches}"
        raise CodemodError(msg)
    return matches[0]


def _patches(crate_directory: str, spawns: int, builds: int) -> tuple[SourcePatch, ...]:
    build_rs = f"{crate_directory}/build.rs"
    return (
        SourcePatch(build_rs, _SWIFT_SPAWN, PINNED_SWIFT_SPAWN, spawns),
        SourcePatch(
            build_rs,
            _SWIFT_BUILD_ARGUMENT,
            _SANDBOX_DISABLED_BUILD_ARGUMENT,
            builds,
        ),
    )


def pin_vendor_dir(vendor_dir: Path) -> None:
    """Rewrite every reviewed swift spawn; reject unreviewed swift spawners."""
    reviewed = {
        _crate_directory(vendor_dir, crate): counts
        for crate, counts in SWIFT_BUILD_CRATES.items()
    }
    unreviewed = sorted(
        build_rs.parent.name
        for build_rs in vendor_dir.glob("*/build.rs")
        if build_rs.parent.name not in reviewed
        and _SWIFT_SPAWN in build_rs.read_text(encoding="utf-8")
    )
    if unreviewed:
        msg = f"unreviewed vendored crates spawn swift: {', '.join(unreviewed)}"
        raise CodemodError(msg)
    apply_patches(
        vendor_dir,
        (
            patch
            for directory, (spawns, builds) in reviewed.items()
            for patch in _patches(directory, spawns, builds)
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Pin the vendored swift build scripts in one cargo vendor directory."""
    return run_source_tree_cli(argv, description=__doc__, patch_tree=pin_vendor_dir)


if __name__ == "__main__":  # pragma: no cover -- packaged CLI guard
    raise SystemExit(main())
