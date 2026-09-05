"""Validate the realized HQ app identity and updater policy."""

import argparse
import plistlib
import re
from pathlib import Path

from policy_contract import (
    AUTOMATIC_MUTATION_PATCHES,
    DISABLED_RELEASES_URL,
    DISABLED_UPDATER_URL,
    ORIGINAL_RELEASES_URL,
    ORIGINAL_UPDATER_URL,
    RELEASES_URL_COUNT,
    UPDATER_URL_COUNT,
    MachinePatch,
    MaskedBytes,
)

_FULL_BYTE_MASK = 0xFF


# Keep matching and branch decoding independent from patch_updater so the
# install check can detect an incorrect transformation of the shared contract.
def _masked_regex(masked: MaskedBytes) -> re.Pattern[bytes]:
    expression = b"".join(
        re.escape(bytes((value,))) if keep == _FULL_BYTE_MASK else b"."
        for value, keep in zip(masked.pattern, masked.mask, strict=True)
    )
    return re.compile(b"(?=(" + expression + b"))", re.DOTALL)


def _matching_offsets(payload: bytes, masked: MaskedBytes) -> list[int]:
    offsets: list[int] = []
    for match in _masked_regex(masked).finditer(payload):
        candidate = match.group(1)
        if all(  # pragma: no branch -- regex overapproximates partial-byte masks
            actual & keep == expected & keep
            for actual, expected, keep in zip(
                candidate,
                masked.pattern,
                masked.mask,
                strict=True,
            )
        ):
            offsets.append(match.start())
    return offsets


def _signed_field(value: int, width: int) -> int:
    sign_bit = 1 << (width - 1)
    return value - (1 << width) if value & sign_bit else value


def _validate_disabled_control_flow(
    executable: bytes,
    offset: int,
    patch: MachinePatch,
) -> None:
    branch = patch.disabled_branch
    if branch is None:
        return
    instruction_offset = offset + branch.offset
    if branch.encoding == "x86-jmp-rel32":
        displacement = int.from_bytes(
            executable[instruction_offset + 1 : instruction_offset + 5],
            byteorder="little",
            signed=True,
        )
        target = instruction_offset + 5 + displacement
    else:
        word = int.from_bytes(
            executable[instruction_offset : instruction_offset + 4],
            byteorder="little",
        )
        field = word & 0x03FFFFFF
        target = instruction_offset + (_signed_field(field, 26) << 2)
    if target <= offset + len(patch.disabled.pattern) or target >= len(executable):
        msg = (
            f"HQ disabled {patch.label} control flow drifted: branch target {target} "
            "does not continue within the executable after the reviewed mutation "
            "block"
        )
        raise ValueError(msg)


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
    for patch in AUTOMATIC_MUTATION_PATCHES:
        if _matching_offsets(executable, patch.original):
            msg = f"HQ executable retains automatic mutation path: {patch.label}"
            raise ValueError(msg)
        disabled_offsets = _matching_offsets(executable, patch.disabled)
        if len(disabled_offsets) != 1:
            msg = (
                f"HQ disabled automatic mutation signature {patch.label} "
                f"expected 1, got {len(disabled_offsets)}"
            )
            raise ValueError(msg)
        _validate_disabled_control_flow(executable, disabled_offsets[0], patch)


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
