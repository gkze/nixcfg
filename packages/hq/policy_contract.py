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
UPDATER_URL_COUNT = 4
RELEASES_URL_COUNT = 2

# 0.10.288 keeps the push/sub frame but widens the config-buffer LEA to a
# disp32 addressing ([rbp-0xc0]); the semantic body (menubar.json read,
# autoUpdate key lookup, 0x8000000000000001 tag check, bool at +0x20) is
# unchanged and still returns the gate decision in al.
_X86_64_AUTO_UPDATE_GATE = _exact(
    bytes.fromhex(
        "55 48 89 e5 41 57 41 56 41 55 41 54 53 48 81 ec 98 00 00 00 "
        "48 8d bd 40 ff ff ff"
    )
)
_DISABLED_X86_64_AUTO_UPDATE_GATE = _exact(bytes.fromhex("31 c0 c3") + (b"\x90" * 24))
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
# 0.10.306 turns the trailing state LEA into a state-relative MOV
# (48 8b b3: mov rSI,[rbx+disp32]; the REX prefix and destination register stay
# relaxed via _relax_destination_register). Both sites now share that window,
# so each pattern extends by the following instructions' opcode neighborhood
# (a masked state-relative MOV, then a per-site LEA/MOV signature) with its
# displacement masked; 0.10.307 also relaxes the main follow LEA's ModRM byte
# because that site switched its destination encoding (0d -> bb) while the
# opcode neighborhood still separates the two sites.
def _relax_destination_register(
    masked: MaskedBytes, rex: int, modrm: int
) -> MaskedBytes:
    """Allow one instruction's REX prefix and ModRM reg field to vary."""
    mask = bytearray(masked.mask)
    pattern = bytearray(masked.pattern)
    mask[rex] = 0
    pattern[rex] = 0
    mask[modrm] &= 0xC7  # keep mod=10 and rm; the destination register is not policy
    pattern[modrm] &= 0xC7
    return MaskedBytes(pattern=bytes(pattern), mask=bytes(mask))


_X86_64_CORE_INSTALL_GUARD = _relax_destination_register(
    _masked_bytes(
        bytes.fromhex(
            "48 8d bb 00 00 00 00 e8 00 00 00 00 "
            "48 8b bb 00 00 00 00 48 8b b3 00 00 00 00 "
            "e8 00 00 00 00 84 c0 0f 84 00 00 00 00 "
            "48 8b b3 00 00 00 00 "
            "48 8b 93 00 00 00 00 "
            "48 8d 0d 00 00 00 00"
        ),
        (3, 7),
        (8, 12),
        (15, 19),
        (22, 26),
        (27, 31),
        (35, 39),
        (42, 46),
        (49, 53),
        (55, 56),
        (56, 60),
    ),
    rex=39,
    modrm=41,
)
_DISABLED_X86_64_CORE_INSTALL_GUARD = _relax_destination_register(
    _masked_bytes(
        bytes.fromhex(
            "48 8d bb 00 00 00 00 e8 00 00 00 00 "
            "48 8b bb 00 00 00 00 48 8b b3 00 00 00 00 "
            "e8 00 00 00 00 84 c0 e9 00 00 00 00 90 "
            "48 8b b3 00 00 00 00 "
            "48 8b 93 00 00 00 00 "
            "48 8d 0d 00 00 00 00"
        ),
        (3, 7),
        (8, 12),
        (15, 19),
        (22, 26),
        (27, 31),
        (34, 38),
        (42, 46),
        (49, 53),
        (55, 56),
        (56, 60),
    ),
    rex=39,
    modrm=41,
)
_X86_64_STAGING_CORE_INSTALL_GUARD = _relax_destination_register(
    _masked_bytes(
        bytes.fromhex(
            "48 8d bb 00 00 00 00 e8 00 00 00 00 "
            "48 8b bb 00 00 00 00 48 8b b3 00 00 00 00 "
            "e8 00 00 00 00 84 c0 0f 84 00 00 00 00 "
            "48 8b b3 00 00 00 00 "
            "48 8b 93 00 00 00 00 "
            "48 8b 8b 00 00 00 00"
        ),
        (3, 7),
        (8, 12),
        (15, 19),
        (22, 26),
        (27, 31),
        (35, 39),
        (42, 46),
        (49, 53),
        (56, 60),
    ),
    rex=39,
    modrm=41,
)
_DISABLED_X86_64_STAGING_CORE_INSTALL_GUARD = _relax_destination_register(
    _masked_bytes(
        bytes.fromhex(
            "48 8d bb 00 00 00 00 e8 00 00 00 00 "
            "48 8b bb 00 00 00 00 48 8b b3 00 00 00 00 "
            "e8 00 00 00 00 84 c0 e9 00 00 00 00 90 "
            "48 8b b3 00 00 00 00 "
            "48 8b 93 00 00 00 00 "
            "48 8b 8b 00 00 00 00"
        ),
        (3, 7),
        (8, 12),
        (15, 19),
        (22, 26),
        (27, 31),
        (34, 38),
        (42, 46),
        (49, 53),
        (56, 60),
    ),
    rex=39,
    modrm=41,
)

_AARCH64_BRANCH26_MASK = 0xFC000000
_AARCH64_TBZ_MASK = 0xFFF8001F
_AARCH64_CBZ_MASK = 0xFF00001F
_AARCH64_ADRP_MASK = 0x9F00001F
_AARCH64_LDP_STATE_MASK = 0xFFC003FF
_AARCH64_LDP_SP_MASK = 0xFFC003E0
_AARCH64_ADD_STATE_BASE_MASK = 0xFFC003E0
_AARCH64_ADD_ANY_MASK = 0xFFC00000
_AARCH64_STRB_STATE_MASK = 0xFFC003FF
_AARCH64_ADD_IMMEDIATE_MASK = 0xFFC003FF
_AARCH64_PAIR_OFFSET_MASK = 0xFFC07FFF
_AARCH64_EXACT_MASK = 0xFFFFFFFF

# Preserve each BL while converting only the reviewed forward conditional
# branch to an unconditional branch. The guarded state base (x19) and the
# continuation target stay exact; destination registers and immediate offsets
# are compiler layout, not policy.
# 0.10.307 replaces both pre-branch pair loads with state-relative ldr loads
# and moves the mutation block's reloads into plain loads; the reviewed branch
# and the adrp/add report call keep the same shape.
_ARM64_CORE_INSTALL_GUARD = _aarch64_words(
    (0x94000000, _AARCH64_BRANCH26_MASK),
    (0xF9400260, _AARCH64_LDP_STATE_MASK),
    (0xF9400261, _AARCH64_LDP_STATE_MASK),
    (0x94000000, _AARCH64_BRANCH26_MASK),
    (0x36000000, _AARCH64_TBZ_MASK),
    (0xF9400261, _AARCH64_LDP_STATE_MASK),
    (0xF9400262, _AARCH64_LDP_STATE_MASK),
    (0x90000103, _AARCH64_ADRP_MASK),
    (0x91000000, _AARCH64_ADD_ANY_MASK),
)
_DISABLED_ARM64_CORE_INSTALL_GUARD = _aarch64_words(
    (0x94000000, _AARCH64_BRANCH26_MASK),
    (0xF9400260, _AARCH64_LDP_STATE_MASK),
    (0xF9400261, _AARCH64_LDP_STATE_MASK),
    (0x94000000, _AARCH64_BRANCH26_MASK),
    (0x14000000, _AARCH64_BRANCH26_MASK),
    (0xF9400261, _AARCH64_LDP_STATE_MASK),
    (0xF9400262, _AARCH64_LDP_STATE_MASK),
    (0x90000103, _AARCH64_ADRP_MASK),
    (0x91000000, _AARCH64_ADD_ANY_MASK),
)
# The staging window's 0.10.306 build reloads two pairs after the reviewed
# branch, loads one sp-relative value, then calls before the continuation add;
# opcode classes and the sp base remain policy. The reviewed branch is a tbz
# (bit 0 of w0) in this build, replacing 0.10.288's cbz.
_ARM64_STAGING_CORE_INSTALL_GUARD = _aarch64_words(
    (0x94000000, _AARCH64_BRANCH26_MASK),
    (0xA9400260, _AARCH64_LDP_STATE_MASK),
    (0x94000000, _AARCH64_BRANCH26_MASK),
    (0x36000000, _AARCH64_TBZ_MASK),
    (0xA9400261, _AARCH64_LDP_STATE_MASK),
    (0xA9400263, _AARCH64_LDP_STATE_MASK),
    (0xF94003E0, _AARCH64_LDP_SP_MASK),
    (0x94000000, _AARCH64_BRANCH26_MASK),
    (0x910003E0, _AARCH64_ADD_STATE_BASE_MASK),
)
_DISABLED_ARM64_STAGING_CORE_INSTALL_GUARD = _aarch64_words(
    (0x94000000, _AARCH64_BRANCH26_MASK),
    (0xA9400260, _AARCH64_LDP_STATE_MASK),
    (0x94000000, _AARCH64_BRANCH26_MASK),
    (0x14000000, _AARCH64_BRANCH26_MASK),
    (0xA9400261, _AARCH64_LDP_STATE_MASK),
    (0xA9400263, _AARCH64_LDP_STATE_MASK),
    (0xF90003E0, _AARCH64_LDP_SP_MASK),
    (0x94000000, _AARCH64_BRANCH26_MASK),
    (0x910003E0, _AARCH64_ADD_STATE_BASE_MASK),
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
        original_branch=RelativeBranch(16, "aarch64-tbz-imm14"),
        disabled_branch=RelativeBranch(16, "aarch64-b-imm26"),
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
        original_branch=RelativeBranch(12, "aarch64-tbz-imm14"),
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
