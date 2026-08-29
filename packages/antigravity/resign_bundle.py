"""Re-sign and validate every native code object in Antigravity."""

import argparse
import os
import plistlib
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

type CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[bytes]]
type BundleInventory = tuple[tuple[Path, ...], tuple[Path, ...]]

EXPECTED_MACHOS = (
    "Contents/Frameworks/Antigravity Helper (GPU).app/Contents/MacOS/Antigravity Helper (GPU)",
    "Contents/Frameworks/Antigravity Helper (Plugin).app/Contents/MacOS/"
    "Antigravity Helper (Plugin)",
    "Contents/Frameworks/Antigravity Helper (Renderer).app/Contents/MacOS/"
    "Antigravity Helper (Renderer)",
    "Contents/Frameworks/Antigravity Helper.app/Contents/MacOS/Antigravity Helper",
    "Contents/Frameworks/Electron Framework.framework/Versions/A/Electron Framework",
    "Contents/Frameworks/Electron Framework.framework/Versions/A/Helpers/chrome_crashpad_handler",
    "Contents/Frameworks/Electron Framework.framework/Versions/A/Libraries/libEGL.dylib",
    "Contents/Frameworks/Electron Framework.framework/Versions/A/Libraries/libGLESv2.dylib",
    "Contents/Frameworks/Electron Framework.framework/Versions/A/Libraries/libffmpeg.dylib",
    "Contents/Frameworks/Electron Framework.framework/Versions/A/Libraries/libvk_swiftshader.dylib",
    "Contents/Frameworks/Mantle.framework/Versions/A/Mantle",
    "Contents/Frameworks/ReactiveObjC.framework/Versions/A/ReactiveObjC",
    "Contents/Frameworks/Squirrel.framework/Versions/A/Resources/ShipIt",
    "Contents/Frameworks/Squirrel.framework/Versions/A/Squirrel",
    "Contents/MacOS/Antigravity",
    "Contents/Resources/bin/language_server",
    "Contents/Resources/bin/webm_encoder",
)
EXPECTED_NESTED_BUNDLES = (
    "Contents/Frameworks/Antigravity Helper (GPU).app",
    "Contents/Frameworks/Antigravity Helper (Plugin).app",
    "Contents/Frameworks/Antigravity Helper (Renderer).app",
    "Contents/Frameworks/Antigravity Helper.app",
    "Contents/Frameworks/Electron Framework.framework",
    "Contents/Frameworks/Mantle.framework",
    "Contents/Frameworks/ReactiveObjC.framework",
    "Contents/Frameworks/Squirrel.framework",
)

_SOURCE_MAIN_ENTITLEMENTS = {
    "com.apple.security.automation.apple-events": True,
    "com.apple.security.cs.allow-jit": True,
    "com.apple.security.device.audio-input": True,
    "com.apple.security.device.camera": True,
}
_MAIN_ENTITLEMENTS = {
    **_SOURCE_MAIN_ENTITLEMENTS,
    "com.apple.security.cs.disable-library-validation": True,
}
_JIT_ENTITLEMENTS = {"com.apple.security.cs.allow-jit": True}
_HELPER_ENTITLEMENTS = {
    **_JIT_ENTITLEMENTS,
    "com.apple.security.cs.disable-library-validation": True,
}
_PLUGIN_ENTITLEMENTS = {
    **_JIT_ENTITLEMENTS,
    "com.apple.security.cs.allow-unsigned-executable-memory": True,
    "com.apple.security.cs.disable-library-validation": True,
}
EXPECTED_ENTITLEMENTS = {
    ".": _MAIN_ENTITLEMENTS,
    "Contents/MacOS/Antigravity": _MAIN_ENTITLEMENTS,
    "Contents/Frameworks/Antigravity Helper (GPU).app": _HELPER_ENTITLEMENTS,
    (
        "Contents/Frameworks/Antigravity Helper (GPU).app/Contents/MacOS/"
        "Antigravity Helper (GPU)"
    ): _HELPER_ENTITLEMENTS,
    "Contents/Frameworks/Antigravity Helper (Plugin).app": _PLUGIN_ENTITLEMENTS,
    (
        "Contents/Frameworks/Antigravity Helper (Plugin).app/Contents/MacOS/"
        "Antigravity Helper (Plugin)"
    ): _PLUGIN_ENTITLEMENTS,
    "Contents/Frameworks/Antigravity Helper (Renderer).app": _HELPER_ENTITLEMENTS,
    (
        "Contents/Frameworks/Antigravity Helper (Renderer).app/Contents/MacOS/"
        "Antigravity Helper (Renderer)"
    ): _HELPER_ENTITLEMENTS,
    "Contents/Frameworks/Antigravity Helper.app": _HELPER_ENTITLEMENTS,
    (
        "Contents/Frameworks/Antigravity Helper.app/Contents/MacOS/Antigravity Helper"
    ): _HELPER_ENTITLEMENTS,
    (
        "Contents/Frameworks/Electron Framework.framework/Versions/A/Helpers/"
        "chrome_crashpad_handler"
    ): _JIT_ENTITLEMENTS,
    "Contents/Frameworks/Squirrel.framework/Versions/A/Resources/ShipIt": _JIT_ENTITLEMENTS,
    "Contents/Resources/bin/language_server": _JIT_ENTITLEMENTS,
    "Contents/Resources/bin/webm_encoder": _JIT_ENTITLEMENTS,
}
SOURCE_ENTITLEMENTS = {
    **EXPECTED_ENTITLEMENTS,
    ".": _SOURCE_MAIN_ENTITLEMENTS,
    "Contents/MacOS/Antigravity": _SOURCE_MAIN_ENTITLEMENTS,
    "Contents/Frameworks/Antigravity Helper (GPU).app": _JIT_ENTITLEMENTS,
    (
        "Contents/Frameworks/Antigravity Helper (GPU).app/Contents/MacOS/"
        "Antigravity Helper (GPU)"
    ): _JIT_ENTITLEMENTS,
    "Contents/Frameworks/Antigravity Helper (Renderer).app": _JIT_ENTITLEMENTS,
    (
        "Contents/Frameworks/Antigravity Helper (Renderer).app/Contents/MacOS/"
        "Antigravity Helper (Renderer)"
    ): _JIT_ENTITLEMENTS,
    "Contents/Frameworks/Antigravity Helper.app": _JIT_ENTITLEMENTS,
    (
        "Contents/Frameworks/Antigravity Helper.app/Contents/MacOS/Antigravity Helper"
    ): _JIT_ENTITLEMENTS,
}
_MAIN_ENTITLEMENT_LABELS = frozenset({".", "Contents/MacOS/Antigravity"})
_HELPER_ENTITLEMENT_LABELS = frozenset({
    "Contents/Frameworks/Antigravity Helper (GPU).app",
    "Contents/Frameworks/Antigravity Helper (GPU).app/Contents/MacOS/"
    "Antigravity Helper (GPU)",
    "Contents/Frameworks/Antigravity Helper (Renderer).app",
    "Contents/Frameworks/Antigravity Helper (Renderer).app/Contents/MacOS/"
    "Antigravity Helper (Renderer)",
    "Contents/Frameworks/Antigravity Helper.app",
    "Contents/Frameworks/Antigravity Helper.app/Contents/MacOS/Antigravity Helper",
})

_MACHO_MAGICS = frozenset(
    bytes.fromhex(magic)
    for magic in (
        "feedface",
        "feedfacf",
        "cefaedfe",
        "cffaedfe",
        "cafebabe",
        "bebafeca",
        "cafebabf",
        "bfbafeca",
    )
)
_CODE_BUNDLE_SUFFIXES = frozenset({
    ".app",
    ".appex",
    ".bundle",
    ".framework",
    ".plugin",
    ".xpc",
})


class SigningError(ValueError):
    """The Antigravity native-code signing contract was not met."""


def _default_runner(arguments: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603
        arguments,
        check=False,
        capture_output=True,
    )


def _run_checked(
    runner: CommandRunner,
    arguments: list[str],
) -> subprocess.CompletedProcess[bytes]:
    completed = runner(arguments)
    if completed.returncode != 0:
        detail = completed.stderr.decode(errors="replace").strip()
        msg = f"Antigravity signing command failed ({' '.join(arguments)}): {detail}"
        raise SigningError(msg)
    return completed


def _relative_label(app: Path, candidate: Path) -> str:
    return "." if candidate == app else candidate.relative_to(app).as_posix()


def _is_macho(candidate: Path) -> bool:
    with candidate.open("rb") as handle:
        return handle.read(4) in _MACHO_MAGICS


def _sorted_regular_files(app: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            (
                candidate
                for candidate in app.rglob("*")
                if candidate.is_file() and not candidate.is_symlink()
            ),
            key=lambda candidate: os.fsencode(candidate.relative_to(app).as_posix()),
        )
    )


def _sorted_code_bundles(app: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            (
                candidate
                for candidate in app.rglob("*")
                if candidate.is_dir()
                and not candidate.is_symlink()
                and candidate.suffix in _CODE_BUNDLE_SUFFIXES
            ),
            key=lambda candidate: (
                -len(candidate.relative_to(app).parts),
                os.fsencode(candidate.relative_to(app).as_posix()),
            ),
        )
    )


def discover_inventory(app: Path) -> BundleInventory:
    """Discover and pin every native leaf and nested signed bundle."""
    if not app.is_dir():
        msg = f"Antigravity app is not a directory: {app}"
        raise SigningError(msg)

    machos = tuple(
        candidate for candidate in _sorted_regular_files(app) if _is_macho(candidate)
    )
    macho_labels = tuple(_relative_label(app, candidate) for candidate in machos)
    if macho_labels != EXPECTED_MACHOS:
        msg = f"unexpected Antigravity Mach-O inventory: {macho_labels!r}"
        raise SigningError(msg)

    bundles = _sorted_code_bundles(app)
    bundle_labels = tuple(_relative_label(app, candidate) for candidate in bundles)
    if bundle_labels != EXPECTED_NESTED_BUNDLES:
        msg = f"unexpected Antigravity nested-bundle inventory: {bundle_labels!r}"
        raise SigningError(msg)
    return machos, bundles


def _read_entitlements(
    app: Path,
    candidate: Path,
    *,
    runner: CommandRunner,
) -> dict[str, object]:
    completed = _run_checked(
        runner,
        [
            "/usr/bin/codesign",
            "-d",
            "--entitlements",
            ":-",
            str(candidate),
        ],
    )
    if not completed.stdout:
        return {}
    try:
        entitlements = plistlib.loads(completed.stdout)
    except plistlib.InvalidFileException as error:
        label = _relative_label(app, candidate)
        msg = f"invalid Antigravity entitlements for {label}"
        raise SigningError(msg) from error
    if not isinstance(entitlements, dict):
        label = _relative_label(app, candidate)
        msg = f"invalid Antigravity entitlements for {label}"
        raise SigningError(msg)
    return entitlements


def _validate_entitlements(
    app: Path,
    candidate: Path,
    *,
    runner: CommandRunner,
    expected_inventory: Mapping[str, Mapping[str, object]] = EXPECTED_ENTITLEMENTS,
) -> None:
    label = _relative_label(app, candidate)
    actual = _read_entitlements(app, candidate, runner=runner)
    expected = expected_inventory.get(label, {})
    if actual != expected:
        msg = f"unexpected Antigravity entitlements for {label}: {actual!r}"
        raise SigningError(msg)


def _validate_signature_details(label: str, details: str) -> None:
    team_identifiers = [
        line for line in details.splitlines() if line.startswith("TeamIdentifier=")
    ]
    if team_identifiers != ["TeamIdentifier=not set"]:
        msg = f"Antigravity signature for {label} has a Team ID: {team_identifiers!r}"
        raise SigningError(msg)

    signatures = [
        line for line in details.splitlines() if line.startswith("Signature=")
    ]
    if signatures != ["Signature=adhoc"]:
        msg = f"Antigravity signature for {label} is not exactly ad hoc: {signatures!r}"
        raise SigningError(msg)

    code_directories = [
        line for line in details.splitlines() if line.startswith("CodeDirectory ")
    ]
    if len(code_directories) != 1:
        msg = (
            f"Antigravity signature for {label} has "
            f"{len(code_directories)} CodeDirectory lines"
        )
        raise SigningError(msg)
    flags_match = re.search(r"flags=[^(]+\(([^)]*)\)", code_directories[0])
    flags = set(flags_match.group(1).split(",")) if flags_match else set()
    missing_flags = {"adhoc", "runtime"} - flags
    if missing_flags:
        msg = (
            f"Antigravity signature for {label} lacks required flags: "
            f"{sorted(missing_flags)!r}"
        )
        raise SigningError(msg)


def _validate_candidate(
    app: Path,
    candidate: Path,
    *,
    runner: CommandRunner,
) -> None:
    _run_checked(
        runner,
        ["/usr/bin/codesign", "--verify", "--strict", str(candidate)],
    )
    details = _run_checked(
        runner,
        ["/usr/bin/codesign", "-d", "--verbose=4", str(candidate)],
    ).stderr.decode(errors="replace")
    label = _relative_label(app, candidate)
    _validate_signature_details(label, details)
    _validate_entitlements(app, candidate, runner=runner)


def validate_bundle(
    app: Path,
    *,
    runner: CommandRunner = _default_runner,
) -> None:
    """Validate exact inventory, uniform identity, runtime, and entitlements."""
    machos, bundles = discover_inventory(app)
    for candidate in (*machos, *bundles, app):
        _validate_candidate(app, candidate, runner=runner)
    _run_checked(
        runner,
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
    )


def resign_bundle(
    app: Path,
    *,
    runner: CommandRunner = _default_runner,
) -> None:
    """Re-sign all native leaves and bundles from the deepest level outward."""
    machos, bundles = discover_inventory(app)
    candidates = (*machos, *bundles, app)
    for candidate in candidates:
        _validate_entitlements(
            app,
            candidate,
            runner=runner,
            expected_inventory=SOURCE_ENTITLEMENTS,
        )
    with (
        tempfile.NamedTemporaryFile(
            prefix="antigravity-main-entitlements-",
            suffix=".plist",
        ) as main_entitlement_file,
        tempfile.NamedTemporaryFile(
            prefix="antigravity-helper-entitlements-",
            suffix=".plist",
        ) as helper_entitlement_file,
    ):
        plistlib.dump(_MAIN_ENTITLEMENTS, main_entitlement_file)
        main_entitlement_file.flush()
        plistlib.dump(_HELPER_ENTITLEMENTS, helper_entitlement_file)
        helper_entitlement_file.flush()
        for candidate in candidates:
            label = _relative_label(app, candidate)
            arguments = [
                "/usr/bin/codesign",
                "--force",
                "--timestamp=none",
                "--sign",
                "-",
            ]
            if label in _MAIN_ENTITLEMENT_LABELS | _HELPER_ENTITLEMENT_LABELS:
                entitlement_file = (
                    main_entitlement_file
                    if label in _MAIN_ENTITLEMENT_LABELS
                    else helper_entitlement_file
                )
                arguments.extend([
                    "--preserve-metadata=identifier,flags,runtime",
                    "--entitlements",
                    entitlement_file.name,
                ])
            else:
                arguments.append(
                    "--preserve-metadata=identifier,entitlements,flags,runtime"
                )
            arguments.append(str(candidate))
            _run_checked(runner, arguments)
        validate_bundle(app, runner=runner)


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: CommandRunner = _default_runner,
) -> int:
    """Re-sign or validate one extracted Antigravity application bundle."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("app", type=Path)
    arguments = parser.parse_args(argv)
    try:
        if arguments.check:
            validate_bundle(arguments.app, runner=runner)
            sys.stdout.write("verified Antigravity signatures\n")
        else:
            resign_bundle(arguments.app, runner=runner)
            sys.stdout.write("re-signed Antigravity native code\n")
    except (OSError, SigningError) as error:
        sys.stderr.write(f"error: {error}\n")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover -- package-build entry point
    raise SystemExit(main())
