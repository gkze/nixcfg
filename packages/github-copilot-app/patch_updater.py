"""Disable GitHub Copilot's native Tauri updater in its signed executable."""

import argparse
import sys
from pathlib import Path


class PatchError(RuntimeError):
    """The vendor executable does not match the reviewed updater contract."""


VENDOR_PUBLIC_KEY = (
    b"dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IEJGM0Q0QkQ1"
    b"OTZGQzU0QkIKUldTN1ZQeVcxVXM5djJyOWJlOUN6RnRyNHpYMmJNcXkvU0Y4cUhO"
    b"SG1Zem10eFV0ck5UQnpjWTMK"
)
# Public half of a one-off Ed25519 key whose private half was discarded.
DISABLED_PUBLIC_KEY = (
    b"dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IDIzMjBFMkRG"
    b"RTlGQjgzOEYKUldTUGcvdnAzK0lnSTJvVWpJZGRULzcwMjFnMnRBRHBic3gyYTJw"
    b"cUpQK1VtcDRZRldtWnNsRHQK"
)

# The release lookup and asset route are the authenticated and unauthenticated
# acquisition paths. Every manifest filename is disabled too. The staging
# filenames are isolated so a payload and manifest left by a vendor build are
# never discovered by this build. Key rotation remains defense in depth.
PATCHES: tuple[tuple[str, bytes, bytes, int], ...] = (
    ("latest-release lookup", b"/releases/latest", b"/nix-owned/fails", 1),
    ("release-asset download", b"/releases/assets/", b"/nix-owned/asset/", 1),
    ("update manifest", b"latest.json", b"denied.json", 3),
    ("staged update payload", b"staged-update.bin", b"nix-owned-upd.bin", 1),
    (
        "staged update manifest",
        b"staged-manifest.json",
        b"nix-owned-state.json",
        1,
    ),
    ("update verification key", VENDOR_PUBLIC_KEY, DISABLED_PUBLIC_KEY, 2),
)


def _validate_patch_contract() -> None:
    for label, original, replacement, _expected_count in PATCHES:
        if len(original) != len(
            replacement
        ):  # pragma: no cover -- checked-in constants
            msg = f"Copilot {label} replacement changed executable length"
            raise AssertionError(msg)
        if original == replacement:  # pragma: no cover -- checked-in constants
            msg = f"Copilot {label} replacement did not change the executable"
            raise AssertionError(msg)


def validate_disabled_payload(payload: bytes) -> None:
    """Require every acquisition and installation gate to remain disabled."""
    for label, original, replacement, expected_count in PATCHES:
        if original in payload:
            msg = f"Copilot {label} still contains the vendor updater anchor"
            raise PatchError(msg)
        actual_count = payload.count(replacement)
        if actual_count != expected_count:
            msg = (
                f"Copilot disabled {label} inventory drifted: "
                f"expected {expected_count}, got {actual_count}"
            )
            raise PatchError(msg)


def patch_payload(payload: bytes) -> bytes:
    """Return the exact-length, fail-closed updater transformation."""
    _validate_patch_contract()
    for label, original, _replacement, expected_count in PATCHES:
        actual_count = payload.count(original)
        if actual_count != expected_count:
            msg = (
                f"Copilot {label} inventory drifted: "
                f"expected {expected_count}, got {actual_count}"
            )
            raise PatchError(msg)

    patched = payload
    for _label, original, replacement, _expected_count in PATCHES:
        patched = patched.replace(original, replacement)
    validate_disabled_payload(patched)
    return patched


def patch_file(executable: Path) -> None:
    """Patch the structurally validated executable while preserving its mode."""
    payload = executable.read_bytes()
    patched = patch_payload(payload)
    executable.write_bytes(patched)


def validate_disabled_file(executable: Path) -> None:
    """Validate a patched executable after bundle re-signing."""
    validate_disabled_payload(executable.read_bytes())


def main(argv: list[str] | None = None) -> int:
    """Run or verify the package-local Copilot updater patch."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("executable", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.check:
            validate_disabled_file(args.executable)
        else:
            patch_file(args.executable)
    except (OSError, PatchError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover -- packaged CLI guard
    raise SystemExit(main())
