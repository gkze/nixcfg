"""Disable HQ's automatic update/mutation paths in an authenticated binary."""

import argparse
import os
import re
import tempfile
from itertools import pairwise
from pathlib import Path

from policy_contract import (
    AUTOMATIC_MUTATION_PATCHES,
    DISABLED_RELEASES_URL,
    DISABLED_UPDATER_URL,
    RELEASES_URL,
    RELEASES_URL_COUNT,
    UPDATER_URL,
    UPDATER_URL_COUNT,
    MachinePatch,
    MaskedBytes,
    RelativeBranch,
)

_FULL_BYTE_MASK = 0xFF


def _validate_constants() -> None:
    if len(UPDATER_URL) != len(DISABLED_UPDATER_URL):
        msg = "HQ updater replacement length does not match its reviewed URL"
        raise RuntimeError(msg)
    if len(RELEASES_URL) != len(DISABLED_RELEASES_URL):
        msg = "HQ release-index replacement length does not match its reviewed URL"
        raise RuntimeError(msg)
    for patch in AUTOMATIC_MUTATION_PATCHES:
        lengths = {
            len(patch.original.pattern),
            len(patch.original.mask),
            len(patch.disabled.pattern),
            len(patch.disabled.mask),
        }
        if len(lengths) != 1:
            msg = (
                f"HQ {patch.label} replacement length does not match its reviewed code"
            )
            raise RuntimeError(msg)
        if (patch.original_branch is None) != (patch.disabled_branch is None):
            msg = f"HQ {patch.label} branch contract is incomplete"
            raise RuntimeError(msg)


def _masked_regex(masked: MaskedBytes) -> re.Pattern[bytes]:
    expression = b"".join(
        re.escape(bytes((value,))) if keep == _FULL_BYTE_MASK else b"."
        for value, keep in zip(masked.pattern, masked.mask, strict=True)
    )
    return re.compile(b"(?=(" + expression + b"))", re.DOTALL)


def _matches_masked(candidate: bytes, masked: MaskedBytes) -> bool:
    return all(
        actual & keep == expected & keep
        for actual, expected, keep in zip(
            candidate,
            masked.pattern,
            masked.mask,
            strict=True,
        )
    )


def _matching_offsets(payload: bytes, masked: MaskedBytes) -> list[int]:
    return [
        match.start()
        for match in _masked_regex(masked).finditer(payload)
        if _matches_masked(match.group(1), masked)
    ]


def _signed_field(value: int, width: int) -> int:
    sign_bit = 1 << (width - 1)
    return value - (1 << width) if value & sign_bit else value


def _decode_branch(candidate: bytes, base: int, branch: RelativeBranch) -> int:
    offset = branch.offset
    if branch.encoding == "x86-jcc-rel32":
        displacement = int.from_bytes(
            candidate[offset + 2 : offset + 6],
            byteorder="little",
            signed=True,
        )
        return base + offset + 6 + displacement
    if branch.encoding == "x86-jmp-rel8":
        displacement = int.from_bytes(
            candidate[offset + 1 : offset + 2],
            byteorder="little",
            signed=True,
        )
        return base + offset + 2 + displacement
    if branch.encoding == "x86-jmp-rel32":
        displacement = int.from_bytes(
            candidate[offset + 1 : offset + 5],
            byteorder="little",
            signed=True,
        )
        return base + offset + 5 + displacement

    word = int.from_bytes(candidate[offset : offset + 4], byteorder="little")
    if branch.encoding == "aarch64-tbz-imm14":
        width = 14
        shift = 5
    elif branch.encoding == "aarch64-cbz-imm19":
        width = 19
        shift = 5
    else:
        width = 26
        shift = 0
    field = (word >> shift) & ((1 << width) - 1)
    return base + offset + (_signed_field(field, width) << 2)


def _encode_branch(
    replacement: bytearray,
    *,
    base: int,
    target: int,
    branch: RelativeBranch,
) -> None:
    offset = branch.offset
    if branch.encoding == "x86-jmp-rel32":
        displacement = target - (base + offset + 5)
        try:
            encoded = displacement.to_bytes(4, byteorder="little", signed=True)
        except OverflowError as error:
            msg = "x86_64 replacement branch target is out of rel32 range"
            raise ValueError(msg) from error
        replacement[offset + 1 : offset + 5] = encoded
        return
    displacement = target - (base + offset)
    field = displacement >> 2
    if not -(1 << 25) <= field < 1 << 25:
        msg = "AArch64 replacement branch target is out of imm26 range"
        raise ValueError(msg)
    word = int.from_bytes(replacement[offset : offset + 4], byteorder="little")
    word = (word & 0xFC000000) | (field & 0x03FFFFFF)
    replacement[offset : offset + 4] = word.to_bytes(4, byteorder="little")


def _merge_disabled(source: bytes, patch: MachinePatch) -> bytearray:
    return bytearray(
        (original & ~keep) | (disabled & keep)
        for original, disabled, keep in zip(
            source,
            patch.disabled.pattern,
            patch.disabled.mask,
            strict=True,
        )
    )


def _build_replacement(payload: bytes, offset: int, patch: MachinePatch) -> bytes:
    source = payload[offset : offset + len(patch.original.pattern)]
    replacement = _merge_disabled(source, patch)
    if patch.original_branch is not None and patch.disabled_branch is not None:
        target = _decode_branch(source, offset, patch.original_branch)
        if target <= offset + len(source) or target >= len(payload):
            msg = (
                f"HQ {patch.label} control flow drifted: branch target {target} "
                "does not continue within the executable after the reviewed "
                "mutation block"
            )
            raise ValueError(msg)
        _encode_branch(
            replacement,
            base=offset,
            target=target,
            branch=patch.disabled_branch,
        )
        if (  # pragma: no cover -- encoder/decoder postcondition
            _decode_branch(bytes(replacement), offset, patch.disabled_branch) != target
        ):
            msg = f"HQ {patch.label} replacement branch changed its control-flow target"
            raise AssertionError(msg)
    result = bytes(replacement)
    if not _matches_masked(  # pragma: no cover -- masked merge postcondition
        result,
        patch.disabled,
    ):
        msg = f"HQ {patch.label} replacement violates its disabled instruction contract"
        raise AssertionError(msg)
    return result


def patch_payload(payload: bytes) -> bytes:
    """Return the exact-length Nix-owned update-policy transformation."""
    _validate_constants()
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

    resolved: list[tuple[int, MachinePatch, bytes]] = []
    for patch in AUTOMATIC_MUTATION_PATCHES:
        offsets = _matching_offsets(payload, patch.original)
        if len(offsets) != 1:
            msg = f"HQ {patch.label} inventory drifted: expected 1, got {len(offsets)}"
            raise ValueError(msg)
        offset = offsets[0]
        resolved.append((offset, patch, _build_replacement(payload, offset, patch)))

    ordered_ranges = sorted(
        (offset, offset + len(replacement), patch.label)
        for offset, patch, replacement in resolved
    )
    for previous, current in pairwise(ordered_ranges):
        if previous[1] > current[0]:
            msg = f"HQ semantic patch ranges overlap: {previous[2]} and {current[2]}"
            raise ValueError(msg)

    patched = bytearray(
        payload.replace(UPDATER_URL, DISABLED_UPDATER_URL).replace(
            RELEASES_URL,
            DISABLED_RELEASES_URL,
        )
    )
    for offset, _patch, replacement in resolved:
        patched[offset : offset + len(replacement)] = replacement
    result = bytes(patched)
    if len(result) != len(payload):  # pragma: no cover -- constant invariant
        msg = "HQ updater patch unexpectedly changed the executable length"
        raise AssertionError(msg)
    if (  # pragma: no cover -- bytes.replace postcondition
        UPDATER_URL in result or RELEASES_URL in result
    ):
        msg = "HQ updater patch left an app-owned endpoint behind"
        raise AssertionError(msg)
    for patch in AUTOMATIC_MUTATION_PATCHES:
        if _matching_offsets(  # pragma: no cover -- replacement opcode postcondition
            result,
            patch.original,
        ):
            msg = f"HQ updater patch left an automatic mutation path behind: {patch.label}"
            raise AssertionError(msg)
        disabled_offsets = _matching_offsets(result, patch.disabled)
        if (
            len(disabled_offsets) != 1
        ):  # pragma: no cover -- unique replacement invariant
            msg = (
                f"HQ updater patch produced {len(disabled_offsets)} disabled "
                f"signatures for {patch.label}"
            )
            raise AssertionError(msg)
    return result


def patch_file(executable: Path) -> None:
    """Atomically patch *executable* after all validation succeeds."""
    payload = executable.read_bytes()
    patched = patch_payload(payload)
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
    arguments = parser.parse_args()
    patch_file(arguments.executable)


if __name__ == "__main__":  # pragma: no cover -- exercised through package build
    main()
