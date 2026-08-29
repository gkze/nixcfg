"""Disable HumanLayer's native Tauri updater in its signed executable."""

import argparse
import hashlib
import sys
from pathlib import Path


class PatchError(RuntimeError):
    """The vendor executable does not match the reviewed updater contract."""


REVIEWED_EXECUTABLE_SHA256 = frozenset({
    # HumanLayer 0.164.0, aarch64-darwin.
    "90ee2763cbd9d8ca8128ac1cfee08c92cd7eda77f43730d402abde422f0b8e55",
    # HumanLayer 0.166.0, aarch64-darwin.
    "2a3032d9c7f1f5ddc383cb20e0a3d25eadf357b53a2a6a47db20731fc9950006",
})

VENDOR_ENDPOINT = (
    b"https://update.humanlayer.com/stable/{{target}}-{{arch}}/{{current_version}}"
)
# Keep Tauri's release-build HTTPS validation satisfied while routing checks to
# the reserved, non-resolving .invalid namespace.
DISABLED_ENDPOINT = (
    b"https://nix-owned.invalid/humanlayer/{{target}}-{{arch}}/{{current_version}}"
)
VENDOR_PUBLIC_KEY = (
    b"dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IDM1QzFDQTIy"
    b"MEYwRjU5MkQKUldRdFdROFBJc3JCTllIZEhnN3g0MktPVEEyN3lNc2ZhV2UxZFVj"
    b"aXVTaGkwUFM2Y21lTUdPQTkK"
)
# Public half of a one-off Ed25519 key whose private half was discarded.
DISABLED_PUBLIC_KEY = (
    b"dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IDIzMjBFMkRG"
    b"RTlGQjgzOEYKUldTUGcvdnAzK0lnSTJvVWpJZGRULzcwMjFnMnRBRHBic3gyYTJw"
    b"cUpQK1VtcDRZRldtWnNsRHQK"
)

# Endpoint replacement prevents new checks and downloads. Rotating the exact
# embedded verification key independently prevents an archive staged by a
# previous vendor build from reaching Tauri's installer.
PATCHES: tuple[tuple[str, bytes, bytes, int], ...] = (
    ("update endpoint", VENDOR_ENDPOINT, DISABLED_ENDPOINT, 1),
    ("update verification key", VENDOR_PUBLIC_KEY, DISABLED_PUBLIC_KEY, 1),
)


def _validate_patch_contract() -> None:
    for label, original, replacement, _expected_count in PATCHES:
        if len(original) != len(
            replacement
        ):  # pragma: no cover -- checked-in constants
            msg = f"HumanLayer {label} replacement changed executable length"
            raise AssertionError(msg)
        if original == replacement:  # pragma: no cover -- checked-in constants
            msg = f"HumanLayer {label} replacement did not change the executable"
            raise AssertionError(msg)


def validate_disabled_payload(payload: bytes) -> None:
    """Require both acquisition and installation gates to remain disabled."""
    for label, original, replacement, expected_count in PATCHES:
        if original in payload:
            msg = f"HumanLayer {label} still contains the vendor updater anchor"
            raise PatchError(msg)
        actual_count = payload.count(replacement)
        if actual_count != expected_count:
            msg = (
                f"HumanLayer disabled {label} inventory drifted: "
                f"expected {expected_count}, got {actual_count}"
            )
            raise PatchError(msg)


def patch_payload(payload: bytes, *, expected_sha256: str) -> bytes:
    """Return the exact-length, fail-closed updater transformation."""
    _validate_patch_contract()
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256 != expected_sha256:
        msg = (
            "HumanLayer executable SHA-256 drifted: "
            f"expected {expected_sha256}, got {actual_sha256}"
        )
        raise PatchError(msg)

    for label, original, _replacement, expected_count in PATCHES:
        actual_count = payload.count(original)
        if actual_count != expected_count:
            msg = (
                f"HumanLayer {label} inventory drifted: "
                f"expected {expected_count}, got {actual_count}"
            )
            raise PatchError(msg)

    patched = payload
    for _label, original, replacement, _expected_count in PATCHES:
        patched = patched.replace(original, replacement)
    validate_disabled_payload(patched)
    return patched


def patch_file(executable: Path) -> None:
    """Patch the reviewed executable in place while preserving its mode."""
    payload = executable.read_bytes()
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256 not in REVIEWED_EXECUTABLE_SHA256:
        msg = f"HumanLayer executable SHA-256 is not reviewed: {actual_sha256}"
        raise PatchError(msg)
    patched = patch_payload(payload, expected_sha256=actual_sha256)
    executable.write_bytes(patched)


def validate_disabled_file(executable: Path) -> None:
    """Validate a patched executable after bundle re-signing."""
    validate_disabled_payload(executable.read_bytes())


def main(argv: list[str] | None = None) -> int:
    """Run or verify the package-local HumanLayer updater patch."""
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
