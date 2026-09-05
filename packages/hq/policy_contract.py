"""Reviewed fail-closed semantic contract for HQ automatic mutation paths."""

from dataclasses import dataclass
from typing import Literal

BranchEncoding = Literal[
    "x86-jcc-rel32",
    "x86-jmp-rel8",
    "x86-jmp-rel32",
    "aarch64-tbz-imm14",
    "aarch64-cbz-imm19",
    "aarch64-b-imm26",
]


@dataclass(frozen=True, slots=True)
class MaskedBytes:
    """Machine bytes whose zero mask bits are relocatable operands."""

    pattern: bytes
    mask: bytes


@dataclass(frozen=True, slots=True)
class RelativeBranch:
    """One PC-relative branch operand within a semantic machine-code pattern."""

    offset: int
    encoding: BranchEncoding


@dataclass(frozen=True, slots=True)
class MachinePatch:
    """One exact-length semantic transformation of an automatic mutation path."""

    label: str
    original: MaskedBytes
    disabled: MaskedBytes
    original_branch: RelativeBranch | None = None
    disabled_branch: RelativeBranch | None = None


def _exact(pattern: bytes) -> MaskedBytes:
    return MaskedBytes(pattern=pattern, mask=b"\xff" * len(pattern))


def _masked_bytes(
    pattern: bytes,
    *relocatable_ranges: tuple[int, int],
) -> MaskedBytes:
    mask = bytearray(b"\xff" * len(pattern))
    for start, stop in relocatable_ranges:
        mask[start:stop] = b"\x00" * (stop - start)
    return MaskedBytes(
        pattern=bytes(value & keep for value, keep in zip(pattern, mask, strict=True)),
        mask=bytes(mask),
    )


def _aarch64_words(*words: tuple[int, int]) -> MaskedBytes:
    return MaskedBytes(
        pattern=b"".join(
            (word & mask).to_bytes(4, byteorder="little") for word, mask in words
        ),
        mask=b"".join(mask.to_bytes(4, byteorder="little") for _word, mask in words),
    )


UPDATER_URL = (
    b"https://github.com/indigoai-us/hq-desktop-app/releases/"
    b"latest/download/latest.json"
)
ORIGINAL_UPDATER_URL = UPDATER_URL
RELEASES_URL = (
    b"https://api.github.com/repos/indigoai-us/hq-desktop-app/releases?per_page=30"
)
ORIGINAL_RELEASES_URL = RELEASES_URL
DISABLED_UPDATER_URL = (
    b"https://updates.invalid/nix-managed-hq-updater-disabled/"
    b"nix-managed-no-update.json"
)
DISABLED_RELEASES_URL = (
    b"https://updates.invalid/nix-managed-hq-release-index-disabled/nix-owned.json"
)
UPDATER_URL_COUNT = 8
RELEASES_URL_COUNT = 4

_X86_64_AUTO_UPDATE_GATE = _exact(
    bytes.fromhex(
        "55 48 89 e5 41 57 41 56 41 55 41 54 53 48 81 ec 98 00 00 00 48 8d 7d 90"
    )
)
_DISABLED_X86_64_AUTO_UPDATE_GATE = _exact(bytes.fromhex("31 c0 c3") + (b"\x90" * 21))
_ARM64_AUTO_UPDATE_GATE = _exact(
    bytes.fromhex(
        "ff 43 03 d1 f8 5f 09 a9 f6 57 0a a9 f4 4f 0b a9 "
        "fd 7b 0c a9 fd 03 03 91 e8 23 01 91"
    )
)
_DISABLED_ARM64_AUTO_UPDATE_GATE = _exact(
    bytes.fromhex("00 00 80 52 c0 03 5f d6") + (bytes.fromhex("1f 20 03 d5") * 5)
)

# x86-64 call/branch rel32 operands and memory displacements are linker/compiler
# layout, not policy. The complete opcode/register neighborhood remains exact.
_X86_64_CORE_INSTALL_GUARD = _masked_bytes(
    bytes.fromhex(
        "48 8d bb 00 00 00 00 e8 00 00 00 00 "
        "48 8b bb 00 00 00 00 48 8b b3 00 00 00 00 "
        "e8 00 00 00 00 84 c0 0f 84 00 00 00 00 "
        "48 8d 83 00 00 00 00"
    ),
    (3, 7),
    (8, 12),
    (15, 19),
    (22, 26),
    (27, 31),
    (35, 39),
    (42, 46),
)
_DISABLED_X86_64_CORE_INSTALL_GUARD = _masked_bytes(
    bytes.fromhex(
        "48 8d bb 00 00 00 00 e8 00 00 00 00 "
        "48 8b bb 00 00 00 00 48 8b b3 00 00 00 00 "
        "e8 00 00 00 00 84 c0 e9 00 00 00 00 90 "
        "48 8d 83 00 00 00 00"
    ),
    (3, 7),
    (8, 12),
    (15, 19),
    (22, 26),
    (27, 31),
    (34, 38),
    (42, 46),
)
_X86_64_STAGING_CORE_INSTALL_GUARD = _masked_bytes(
    bytes.fromhex(
        "48 8d bb 00 00 00 00 e8 00 00 00 00 "
        "48 8b bb 00 00 00 00 48 8b b3 00 00 00 00 "
        "e8 00 00 00 00 84 c0 0f 84 00 00 00 00 "
        "48 8d bd 00 00 00 00"
    ),
    (3, 7),
    (8, 12),
    (15, 19),
    (22, 26),
    (27, 31),
    (35, 39),
    (42, 46),
)
_DISABLED_X86_64_STAGING_CORE_INSTALL_GUARD = _masked_bytes(
    bytes.fromhex(
        "48 8d bb 00 00 00 00 e8 00 00 00 00 "
        "48 8b bb 00 00 00 00 48 8b b3 00 00 00 00 "
        "e8 00 00 00 00 84 c0 e9 00 00 00 00 90 "
        "48 8d bd 00 00 00 00"
    ),
    (3, 7),
    (8, 12),
    (15, 19),
    (22, 26),
    (27, 31),
    (34, 38),
    (42, 46),
)

_AARCH64_BRANCH26_MASK = 0xFC000000
_AARCH64_TBZ_MASK = 0xFFF8001F
_AARCH64_CBZ_MASK = 0xFF00001F
_AARCH64_ADRP_MASK = 0x9F00001F
_AARCH64_ADD_IMMEDIATE_MASK = 0xFFC003FF
_AARCH64_PAIR_OFFSET_MASK = 0xFFC07FFF
_AARCH64_EXACT_MASK = 0xFFFFFFFF

# Preserve each BL and following ADD instruction while converting only the
# reviewed forward conditional branch to an unconditional branch.
# HQ 0.10.198 uses x25 for the guarded state address; keep that register exact.
_ARM64_CORE_INSTALL_GUARD = _aarch64_words(
    (0x94000000, _AARCH64_BRANCH26_MASK),
    (0xA9400660, _AARCH64_PAIR_OFFSET_MASK),
    (0x94000000, _AARCH64_BRANCH26_MASK),
    (0x36000000, _AARCH64_TBZ_MASK),
    (0x91000279, _AARCH64_ADD_IMMEDIATE_MASK),
    (0x3900027F, _AARCH64_ADD_IMMEDIATE_MASK),
    (0x91000276, _AARCH64_ADD_IMMEDIATE_MASK),
    (0x14000000, _AARCH64_BRANCH26_MASK),
)
_DISABLED_ARM64_CORE_INSTALL_GUARD = _aarch64_words(
    (0x94000000, _AARCH64_BRANCH26_MASK),
    (0xA9400660, _AARCH64_PAIR_OFFSET_MASK),
    (0x94000000, _AARCH64_BRANCH26_MASK),
    (0x14000000, _AARCH64_BRANCH26_MASK),
    (0x91000279, _AARCH64_ADD_IMMEDIATE_MASK),
    (0x3900027F, _AARCH64_ADD_IMMEDIATE_MASK),
    (0x91000276, _AARCH64_ADD_IMMEDIATE_MASK),
    (0x14000000, _AARCH64_BRANCH26_MASK),
)
_ARM64_STAGING_CORE_INSTALL_GUARD = _aarch64_words(
    (0x94000000, _AARCH64_BRANCH26_MASK),
    (0xA9400660, _AARCH64_PAIR_OFFSET_MASK),
    (0x94000000, _AARCH64_BRANCH26_MASK),
    (0x34000000, _AARCH64_CBZ_MASK),
    (0x910003E8, _AARCH64_ADD_IMMEDIATE_MASK),
    (0x94000000, _AARCH64_BRANCH26_MASK),
    (0xA94063F7, _AARCH64_PAIR_OFFSET_MASK),
    (0x94000000, _AARCH64_BRANCH26_MASK),
)
_DISABLED_ARM64_STAGING_CORE_INSTALL_GUARD = _aarch64_words(
    (0x94000000, _AARCH64_BRANCH26_MASK),
    (0xA9400660, _AARCH64_PAIR_OFFSET_MASK),
    (0x94000000, _AARCH64_BRANCH26_MASK),
    (0x14000000, _AARCH64_BRANCH26_MASK),
    (0x910003E8, _AARCH64_ADD_IMMEDIATE_MASK),
    (0x94000000, _AARCH64_BRANCH26_MASK),
    (0xA94063F7, _AARCH64_PAIR_OFFSET_MASK),
    (0x94000000, _AARCH64_BRANCH26_MASK),
)

# The CLI recovery blocks load two relocated addresses, call two relocated
# functions, then branch past the mutation. Only instruction semantics and the
# continuation target are policy-bearing.
_X86_64_CLI_LEGACY_MARKER_RECOVERY = _masked_bytes(
    bytes.fromhex(
        "48 8d 3d 00 00 00 00 48 8d 15 00 00 00 00 "
        "be 0d 00 00 00 b9 4d 00 00 00 e8 00 00 00 00 "
        "e8 00 00 00 00 eb 00"
    ),
    (3, 7),
    (10, 14),
    (25, 29),
    (30, 34),
    (35, 36),
)
_DISABLED_X86_64_CLI_LEGACY_MARKER_RECOVERY = _masked_bytes(
    bytes.fromhex("e9 00 00 00 00") + (b"\x90" * 31),
    (1, 5),
)
_ARM64_CLI_LEGACY_MARKER_RECOVERY = _aarch64_words(
    (0x90000000, _AARCH64_ADRP_MASK),
    (0x91000000, _AARCH64_ADD_IMMEDIATE_MASK),
    (0x90000002, _AARCH64_ADRP_MASK),
    (0x91000042, _AARCH64_ADD_IMMEDIATE_MASK),
    (0x528001A1, _AARCH64_EXACT_MASK),
    (0x528009A3, _AARCH64_EXACT_MASK),
    (0x94000000, _AARCH64_BRANCH26_MASK),
    (0x94000000, _AARCH64_BRANCH26_MASK),
    (0x14000000, _AARCH64_BRANCH26_MASK),
)
_DISABLED_ARM64_CLI_LEGACY_MARKER_RECOVERY = _aarch64_words(
    (0x14000000, _AARCH64_BRANCH26_MASK),
    *((0xD503201F, _AARCH64_EXACT_MASK),) * 8,
)

AUTOMATIC_MUTATION_PATCHES = (
    MachinePatch(
        label="x86_64 automatic-update gate",
        original=_X86_64_AUTO_UPDATE_GATE,
        disabled=_DISABLED_X86_64_AUTO_UPDATE_GATE,
    ),
    MachinePatch(
        label="arm64 automatic-update gate",
        original=_ARM64_AUTO_UPDATE_GATE,
        disabled=_DISABLED_ARM64_AUTO_UPDATE_GATE,
    ),
    MachinePatch(
        label="x86_64 hq-core install guard",
        original=_X86_64_CORE_INSTALL_GUARD,
        disabled=_DISABLED_X86_64_CORE_INSTALL_GUARD,
        original_branch=RelativeBranch(33, "x86-jcc-rel32"),
        disabled_branch=RelativeBranch(33, "x86-jmp-rel32"),
    ),
    MachinePatch(
        label="arm64 hq-core install guard",
        original=_ARM64_CORE_INSTALL_GUARD,
        disabled=_DISABLED_ARM64_CORE_INSTALL_GUARD,
        original_branch=RelativeBranch(12, "aarch64-tbz-imm14"),
        disabled_branch=RelativeBranch(12, "aarch64-b-imm26"),
    ),
    MachinePatch(
        label="x86_64 staging hq-core install guard",
        original=_X86_64_STAGING_CORE_INSTALL_GUARD,
        disabled=_DISABLED_X86_64_STAGING_CORE_INSTALL_GUARD,
        original_branch=RelativeBranch(33, "x86-jcc-rel32"),
        disabled_branch=RelativeBranch(33, "x86-jmp-rel32"),
    ),
    MachinePatch(
        label="arm64 staging hq-core install guard",
        original=_ARM64_STAGING_CORE_INSTALL_GUARD,
        disabled=_DISABLED_ARM64_STAGING_CORE_INSTALL_GUARD,
        original_branch=RelativeBranch(12, "aarch64-cbz-imm19"),
        disabled_branch=RelativeBranch(12, "aarch64-b-imm26"),
    ),
    MachinePatch(
        label="x86_64 CLI legacy-marker recovery",
        original=_X86_64_CLI_LEGACY_MARKER_RECOVERY,
        disabled=_DISABLED_X86_64_CLI_LEGACY_MARKER_RECOVERY,
        original_branch=RelativeBranch(34, "x86-jmp-rel8"),
        disabled_branch=RelativeBranch(0, "x86-jmp-rel32"),
    ),
    MachinePatch(
        label="arm64 CLI legacy-marker recovery",
        original=_ARM64_CLI_LEGACY_MARKER_RECOVERY,
        disabled=_DISABLED_ARM64_CLI_LEGACY_MARKER_RECOVERY,
        original_branch=RelativeBranch(32, "aarch64-b-imm26"),
        disabled_branch=RelativeBranch(0, "aarch64-b-imm26"),
    ),
)
