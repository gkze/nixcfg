"""Validate the realized HQ app identity and updater policy."""

import argparse
import plistlib
from pathlib import Path

from policy_contract import (
    AUTOMATIC_MUTATION_PATCHES,
    DISABLED_RELEASES_URL,
    DISABLED_UPDATER_URL,
    ORIGINAL_RELEASES_URL,
    ORIGINAL_UPDATER_URL,
    RELEASES_URL_COUNT,
    UPDATER_URL_COUNT,
)


def validate_artifact(
    *,
    info_plist: Path,
    main_executable: Path,
    expected_version: str,
) -> None:
    """Validate one realized HQ app without launching it."""
    with info_plist.open("rb") as plist_file:
        info = plistlib.load(plist_file)
    expected = {
        "CFBundleExecutable": "hq-sync-menubar",
        "CFBundleIdentifier": "ai.indigo.hq-sync-menubar",
        "CFBundleShortVersionString": expected_version,
        "CFBundleVersion": expected_version,
        "LSMinimumSystemVersion": "13.0",
        "LSUIElement": True,
    }
    for key, expected_value in expected.items():
        actual_value = info.get(key)
        if actual_value != expected_value:
            msg = f"{key} expected {expected_value!r}, got {actual_value!r}"
            raise ValueError(msg)

    executable = main_executable.read_bytes()
    for url in (ORIGINAL_UPDATER_URL, ORIGINAL_RELEASES_URL):
        if url in executable:
            msg = f"HQ executable retains app-owned update URL: {url!r}"
            raise ValueError(msg)
    for url, expected_count in (
        (DISABLED_UPDATER_URL, UPDATER_URL_COUNT),
        (DISABLED_RELEASES_URL, RELEASES_URL_COUNT),
    ):
        actual_count = executable.count(url)
        if actual_count != expected_count:
            msg = (
                f"HQ disabled update URL expected {expected_count}, got {actual_count}"
            )
            raise ValueError(msg)
    for label, original, replacement in AUTOMATIC_MUTATION_PATCHES:
        if original in executable:
            msg = f"HQ executable retains automatic mutation path: {label}"
            raise ValueError(msg)
        actual_count = executable.count(replacement)
        if actual_count != 1:
            msg = (
                f"HQ disabled automatic mutation signature {label} "
                f"expected 1, got {actual_count}"
            )
            raise ValueError(msg)


def main() -> None:
    """Validate one realized HQ app from its install check."""
    parser = argparse.ArgumentParser()
    parser.add_argument("info_plist", type=Path)
    parser.add_argument("main_executable", type=Path)
    parser.add_argument("expected_version")
    arguments = parser.parse_args()
    validate_artifact(
        info_plist=arguments.info_plist,
        main_executable=arguments.main_executable,
        expected_version=arguments.expected_version,
    )


if __name__ == "__main__":  # pragma: no cover -- package-build entry point
    main()
