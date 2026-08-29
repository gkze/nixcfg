"""Validate Nix-owned Paseo code-signing evidence."""

import argparse
import os
import plistlib
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

type CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[bytes]]

EXPECTED_ENTITLEMENTS = {
    "com.apple.security.cs.allow-jit": True,
    "com.apple.security.cs.allow-unsigned-executable-memory": True,
    "com.apple.security.cs.disable-library-validation": True,
    "com.apple.security.device.audio-input": True,
}
EXPECTED_NONEXECUTABLE_COUNT = 97
EXPECTED_FRAMEWORKS = (
    "Contents/Frameworks/Electron Framework.framework",
    "Contents/Frameworks/Mantle.framework",
    "Contents/Frameworks/ReactiveObjC.framework",
    "Contents/Frameworks/Squirrel.framework",
)
EXPECTED_HELPERS = (
    "Contents/Frameworks/Paseo Helper (GPU).app",
    "Contents/Frameworks/Paseo Helper (Plugin).app",
    "Contents/Frameworks/Paseo Helper (Renderer).app",
    "Contents/Frameworks/Paseo Helper.app",
)
EXPECTED_ENTITLED_MACHOS = (
    "Contents/Frameworks/Paseo Helper (GPU).app/Contents/MacOS/Paseo Helper (GPU)",
    "Contents/Frameworks/Paseo Helper (Plugin).app/Contents/MacOS/Paseo Helper (Plugin)",
    "Contents/Frameworks/Paseo Helper (Renderer).app/Contents/MacOS/Paseo Helper (Renderer)",
    "Contents/Frameworks/Paseo Helper.app/Contents/MacOS/Paseo Helper",
    "Contents/MacOS/Paseo",
)
EXPECTED_EXECUTABLE_MACHOS = (
    "Contents/Frameworks/Electron Framework.framework/Versions/A/Helpers/chrome_crashpad_handler",
    "Contents/Frameworks/Paseo Helper (GPU).app/Contents/MacOS/Paseo Helper (GPU)",
    "Contents/Frameworks/Paseo Helper (Plugin).app/Contents/MacOS/Paseo Helper (Plugin)",
    "Contents/Frameworks/Paseo Helper (Renderer).app/Contents/MacOS/Paseo Helper (Renderer)",
    "Contents/Frameworks/Paseo Helper.app/Contents/MacOS/Paseo Helper",
    "Contents/Frameworks/Squirrel.framework/Versions/A/Resources/ShipIt",
    "Contents/MacOS/Paseo",
    "Contents/Resources/app.asar.unpacked/node_modules/@esbuild/darwin-arm64/bin/esbuild",
    "Contents/Resources/app.asar.unpacked/node_modules/node-pty/build/Release/spawn-helper",
)
EXPECTED_EXECUTABLE_COUNT = len(EXPECTED_EXECUTABLE_MACHOS)
EXPECTED_MACHO_COUNT = EXPECTED_EXECUTABLE_COUNT + EXPECTED_NONEXECUTABLE_COUNT
EXPECTED_ENTITLED_MACHO_COUNT = len(EXPECTED_ENTITLED_MACHOS)
EXPECTED_UNENTITLED_MACHO_COUNT = EXPECTED_MACHO_COUNT - EXPECTED_ENTITLED_MACHO_COUNT


def validate_signature_evidence(
    *,
    label: str,
    entitlements_payload: bytes,
    details: str,
    require_entitlements: bool,
    strict_verified: bool,
) -> None:
    """Validate one strict ad hoc hardened-runtime signature report."""
    if not strict_verified:
        msg = f"Paseo signature for {label} did not pass strict verification"
        raise ValueError(msg)

    if entitlements_payload:
        try:
            entitlements = plistlib.loads(entitlements_payload)
        except plistlib.InvalidFileException as error:
            msg = f"invalid Paseo entitlements for {label}"
            raise ValueError(msg) from error
        if not require_entitlements:
            msg = f"Paseo code {label} unexpectedly has entitlements"
            raise ValueError(msg)
        if entitlements != EXPECTED_ENTITLEMENTS:
            msg = f"unexpected Paseo entitlements for {label}: {entitlements!r}"
            raise ValueError(msg)
    elif require_entitlements:
        msg = f"Paseo entitled code {label} lacks required entitlements"
        raise ValueError(msg)

    signatures = [
        line for line in details.splitlines() if line.startswith("Signature=")
    ]
    if signatures != ["Signature=adhoc"]:
        msg = f"Paseo signature for {label} is not exactly ad hoc: {signatures!r}"
        raise ValueError(msg)

    team_identifiers = [
        line for line in details.splitlines() if line.startswith("TeamIdentifier=")
    ]
    if team_identifiers != ["TeamIdentifier=not set"]:
        msg = f"Paseo signature for {label} has a Team ID: {team_identifiers!r}"
        raise ValueError(msg)

    code_directories = [
        line for line in details.splitlines() if line.startswith("CodeDirectory ")
    ]
    if len(code_directories) != 1:
        msg = (
            f"Paseo signature for {label} has "
            f"{len(code_directories)} CodeDirectory lines"
        )
        raise ValueError(msg)
    flags_match = re.search(r"flags=[^(]+\(([^)]*)\)", code_directories[0])
    flags = set(flags_match.group(1).split(",")) if flags_match else set()
    missing_flags = {"adhoc", "runtime"} - flags
    if missing_flags:
        msg = (
            f"Paseo signature for {label} lacks required flags: "
            f"{sorted(missing_flags)!r}"
        )
        raise ValueError(msg)


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
        msg = f"Paseo signature command failed ({' '.join(arguments)}): {detail}"
        raise ValueError(msg)
    return completed


def _validate_path_signature(
    path: Path,
    *,
    require_entitlements: bool,
    runner: CommandRunner,
) -> None:
    strict = _run_checked(
        runner,
        ["/usr/bin/codesign", "--verify", "--strict", str(path)],
    )
    details = _run_checked(
        runner,
        ["/usr/bin/codesign", "-d", "--verbose=4", str(path)],
    )
    entitlements = _run_checked(
        runner,
        ["/usr/bin/codesign", "-d", "--entitlements", ":-", str(path)],
    )
    validate_signature_evidence(
        label=str(path),
        entitlements_payload=entitlements.stdout,
        details=details.stderr.decode(errors="replace"),
        require_entitlements=require_entitlements,
        strict_verified=strict.returncode == 0,
    )


def _relative_inventory(app: Path, pattern: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            (
                candidate.relative_to(app).as_posix()
                for candidate in app.rglob(pattern)
                if candidate.is_dir() and not candidate.is_symlink()
            ),
            key=os.fsencode,
        )
    )


def validate_bundle(
    app: Path,
    *,
    runner: CommandRunner = _default_runner,
) -> None:
    """Validate Paseo's complete reviewed Mach-O and nested-bundle inventory."""
    if not app.is_dir():
        msg = f"Paseo app is not a directory: {app}"
        raise ValueError(msg)

    machos: list[tuple[Path, bool]] = []
    candidates = sorted(
        (
            candidate
            for candidate in app.rglob("*")
            if candidate.is_file() and not candidate.is_symlink()
        ),
        key=lambda candidate: os.fsencode(candidate.relative_to(app).as_posix()),
    )
    for candidate in candidates:
        description = _run_checked(
            runner,
            ["/usr/bin/file", "-b", str(candidate)],
        ).stdout.decode(errors="replace")
        if "Mach-O" in description:
            machos.append((candidate, "executable" in description))

    executable_count = sum(is_executable for _, is_executable in machos)
    nonexecutable_count = len(machos) - executable_count
    actual_counts = (len(machos), executable_count, nonexecutable_count)
    expected_counts = (
        EXPECTED_MACHO_COUNT,
        EXPECTED_EXECUTABLE_COUNT,
        EXPECTED_NONEXECUTABLE_COUNT,
    )
    if actual_counts != expected_counts:
        msg = (
            "Paseo Mach-O inventory expected "
            f"{expected_counts[0]}/{expected_counts[1]}/{expected_counts[2]}, "
            f"got {actual_counts[0]}/{actual_counts[1]}/{actual_counts[2]}"
        )
        raise ValueError(msg)

    entitled_machos = tuple(
        path.relative_to(app).as_posix()
        for path, _ in machos
        if path.relative_to(app).as_posix() in EXPECTED_ENTITLED_MACHOS
    )
    entitlement_counts = (len(entitled_machos), len(machos) - len(entitled_machos))
    expected_entitlement_counts = (
        EXPECTED_ENTITLED_MACHO_COUNT,
        EXPECTED_UNENTITLED_MACHO_COUNT,
    )
    if (
        entitlement_counts != expected_entitlement_counts
        or entitled_machos != EXPECTED_ENTITLED_MACHOS
    ):
        msg = (
            "Paseo entitlement inventory expected "
            f"{expected_entitlement_counts[0]}/{expected_entitlement_counts[1]}, "
            f"got {entitlement_counts[0]}/{entitlement_counts[1]}: "
            f"{entitled_machos!r}"
        )
        raise ValueError(msg)

    executable_machos = tuple(
        path.relative_to(app).as_posix()
        for path, is_executable in machos
        if is_executable
    )
    if executable_machos != EXPECTED_EXECUTABLE_MACHOS:
        msg = f"unexpected Paseo executable inventory: {executable_machos!r}"
        raise ValueError(msg)

    frameworks = _relative_inventory(app, "*.framework")
    if frameworks != EXPECTED_FRAMEWORKS:
        msg = f"unexpected Paseo framework inventory: {frameworks!r}"
        raise ValueError(msg)
    helpers = _relative_inventory(app, "*.app")
    if helpers != EXPECTED_HELPERS:
        msg = f"unexpected Paseo helper inventory: {helpers!r}"
        raise ValueError(msg)

    for path, _ in machos:
        _validate_path_signature(
            path,
            require_entitlements=(
                path.relative_to(app).as_posix() in EXPECTED_ENTITLED_MACHOS
            ),
            runner=runner,
        )
    for relative_path in frameworks:
        _validate_path_signature(
            app / relative_path,
            require_entitlements=False,
            runner=runner,
        )
    for relative_path in helpers:
        _validate_path_signature(
            app / relative_path,
            require_entitlements=True,
            runner=runner,
        )
    _validate_path_signature(app, require_entitlements=True, runner=runner)
    _run_checked(
        runner,
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
    )


def main() -> None:
    """Validate one realized Paseo application bundle."""
    parser = argparse.ArgumentParser()
    parser.add_argument("app", type=Path)
    arguments = parser.parse_args()
    validate_bundle(arguments.app)


if __name__ == "__main__":  # pragma: no cover -- package-build entry point
    main()
