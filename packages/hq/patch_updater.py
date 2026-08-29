"""Disable HQ's automatic update/mutation paths in an authenticated binary."""

import argparse
import hashlib
import os
import tempfile
from pathlib import Path

from policy_contract import (
    AUTOMATIC_MUTATION_PATCHES,
    DISABLED_RELEASES_URL,
    DISABLED_UPDATER_URL,
    RELEASES_URL,
    RELEASES_URL_COUNT,
    REVIEWED_EXECUTABLE_SHA256,
    UPDATER_URL,
    UPDATER_URL_COUNT,
)


def _validate_constants() -> None:
    if len(UPDATER_URL) != len(DISABLED_UPDATER_URL):
        msg = "HQ updater replacement length does not match its reviewed URL"
        raise RuntimeError(msg)
    if len(RELEASES_URL) != len(DISABLED_RELEASES_URL):
        msg = "HQ release-index replacement length does not match its reviewed URL"
        raise RuntimeError(msg)
    for label, original, replacement in AUTOMATIC_MUTATION_PATCHES:
        if len(original) != len(replacement):
            msg = f"HQ {label} replacement length does not match its reviewed code"
            raise RuntimeError(msg)


def patch_payload(payload: bytes, *, expected_sha256: str) -> bytes:
    """Return the exact-length Nix-owned update-policy transformation."""
    _validate_constants()
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256 != expected_sha256:
        msg = (
            "HQ executable SHA-256 drifted: "
            f"expected {expected_sha256}, got {actual_sha256}"
        )
        raise ValueError(msg)

    updater_count = payload.count(UPDATER_URL)
    releases_count = payload.count(RELEASES_URL)
    if updater_count != UPDATER_URL_COUNT:
        msg = (
            "HQ updater URL inventory drifted: "
            f"expected {UPDATER_URL_COUNT}, got {updater_count}"
        )
        raise ValueError(msg)
    if releases_count != RELEASES_URL_COUNT:
        msg = (
            "HQ release-index URL inventory drifted: "
            f"expected {RELEASES_URL_COUNT}, got {releases_count}"
        )
        raise ValueError(msg)
    for label, original, _replacement in AUTOMATIC_MUTATION_PATCHES:
        actual_count = payload.count(original)
        if actual_count != 1:
            msg = f"HQ {label} inventory drifted: expected 1, got {actual_count}"
            raise ValueError(msg)

    patched = payload.replace(UPDATER_URL, DISABLED_UPDATER_URL).replace(
        RELEASES_URL,
        DISABLED_RELEASES_URL,
    )
    for _label, original, replacement in AUTOMATIC_MUTATION_PATCHES:
        patched = patched.replace(original, replacement)
    if len(patched) != len(payload):  # pragma: no cover -- constant invariant
        msg = "HQ updater patch unexpectedly changed the executable length"
        raise AssertionError(msg)
    if (  # pragma: no cover -- bytes.replace postcondition
        UPDATER_URL in patched or RELEASES_URL in patched
    ):
        msg = "HQ updater patch left an app-owned endpoint behind"
        raise AssertionError(msg)
    if any(  # pragma: no cover -- bytes.replace postcondition
        original in patched
        for _label, original, _replacement in AUTOMATIC_MUTATION_PATCHES
    ):
        msg = "HQ updater patch left an automatic mutation path behind"
        raise AssertionError(msg)
    return patched


def patch_file(
    executable: Path,
    *,
    expected_sha256: str = REVIEWED_EXECUTABLE_SHA256,
) -> None:
    """Atomically patch *executable* after all validation succeeds."""
    payload = executable.read_bytes()
    patched = patch_payload(payload, expected_sha256=expected_sha256)
    mode = executable.stat().st_mode & 0o777

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=executable.parent,
            prefix=f".{executable.name}.",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(patched)
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path.chmod(mode)
        temporary_path.replace(executable)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main() -> None:
    """Patch one reviewed HQ executable."""
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument(
        "--expected-sha256",
        default=REVIEWED_EXECUTABLE_SHA256,
    )
    arguments = parser.parse_args()
    patch_file(arguments.executable, expected_sha256=arguments.expected_sha256)


if __name__ == "__main__":  # pragma: no cover -- exercised through package build
    main()
