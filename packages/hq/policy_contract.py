"""Reviewed immutable byte contract for the HQ 0.10.155 executable."""

REVIEWED_VERSION = "0.10.155"

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
REVIEWED_EXECUTABLE_SHA256 = (
    "8e165cbb237c1781cd67bdf91636b04019d4be71861a8dfcbe75ffdfa7071d34"
)

# v0.10.155's Rust master automatic-update gate. Each replacement returns false
# at the architecture-specific function entry without disabling manual checks.
X86_64_AUTO_UPDATE_GATE = bytes.fromhex(
    "55 48 89 e5 41 57 41 56 41 55 41 54 53 48 81 ec 98 00 00 00 48 8d 7d 90"
)
DISABLED_X86_64_AUTO_UPDATE_GATE = bytes.fromhex("31 c0 c3") + (b"\x90" * 21)
ARM64_AUTO_UPDATE_GATE = bytes.fromhex(
    "ff 43 03 d1 f8 5f 09 a9 f6 57 0a a9 f4 4f 0b a9 "
    "fd 7b 0c a9 fd 03 03 91 e8 23 01 91"
)
DISABLED_ARM64_AUTO_UPDATE_GATE = bytes.fromhex("00 00 80 52 c0 03 5f d6") + (
    bytes.fromhex("1f 20 03 d5") * 5
)

# These call-site guards force the existing invalid-root exit before hq-core
# release or staging rescue can perform its first mutation.
X86_64_CORE_INSTALL_GUARD = bytes.fromhex("84 c0 0f 84 7d 01 00 00 48 8d 83 59")
DISABLED_X86_64_CORE_INSTALL_GUARD = bytes.fromhex(
    "84 c0 e9 7e 01 00 00 90 48 8d 83 59"
)
ARM64_CORE_INSTALL_GUARD = bytes.fromhex("c0 a0 37 94 40 03 00 36")
DISABLED_ARM64_CORE_INSTALL_GUARD = bytes.fromhex("c0 a0 37 94 1a 00 00 14")
X86_64_STAGING_CORE_INSTALL_GUARD = bytes.fromhex("84 c0 0f 84 f4 04 00 00 48 8d bd 98")
DISABLED_X86_64_STAGING_CORE_INSTALL_GUARD = bytes.fromhex(
    "84 c0 e9 f5 04 00 00 90 48 8d bd 98"
)
ARM64_STAGING_CORE_INSTALL_GUARD = bytes.fromhex("5c 2a 3a 94 40 16 00 34")
DISABLED_ARM64_STAGING_CORE_INSTALL_GUARD = bytes.fromhex("5c 2a 3a 94 b2 00 00 14")

# Skip only the CLI's startup legacy-marker repair, then continue at its
# initial-delay/read-only check loop.
X86_64_CLI_LEGACY_MARKER_RECOVERY = bytes.fromhex(
    "48 8d 3d 10 5a c1 01 48 8d 15 11 5b c1 01 "
    "be 0d 00 00 00 b9 4d 00 00 00 e8 a2 35 f7 00 "
    "e8 7d e1 41 00 eb 15"
)
DISABLED_X86_64_CLI_LEGACY_MARKER_RECOVERY = bytes.fromhex("e9 34 00 00 00") + (
    b"\x90" * 31
)
ARM64_CLI_LEGACY_MARKER_RECOVERY = bytes.fromhex(
    "60 bd 00 d0 00 48 10 91 62 bd 00 d0 42 c8 14 91 "
    "a1 01 80 52 a3 09 80 52 f0 e9 38 94 b3 86 0a 94 "
    "07 00 00 14"
)
DISABLED_ARM64_CLI_LEGACY_MARKER_RECOVERY = bytes.fromhex("0f 00 00 14") + (
    bytes.fromhex("1f 20 03 d5") * 8
)

AUTOMATIC_MUTATION_PATCHES = (
    (
        "x86_64 automatic-update gate",
        X86_64_AUTO_UPDATE_GATE,
        DISABLED_X86_64_AUTO_UPDATE_GATE,
    ),
    (
        "arm64 automatic-update gate",
        ARM64_AUTO_UPDATE_GATE,
        DISABLED_ARM64_AUTO_UPDATE_GATE,
    ),
    (
        "x86_64 hq-core install guard",
        X86_64_CORE_INSTALL_GUARD,
        DISABLED_X86_64_CORE_INSTALL_GUARD,
    ),
    (
        "arm64 hq-core install guard",
        ARM64_CORE_INSTALL_GUARD,
        DISABLED_ARM64_CORE_INSTALL_GUARD,
    ),
    (
        "x86_64 staging hq-core install guard",
        X86_64_STAGING_CORE_INSTALL_GUARD,
        DISABLED_X86_64_STAGING_CORE_INSTALL_GUARD,
    ),
    (
        "arm64 staging hq-core install guard",
        ARM64_STAGING_CORE_INSTALL_GUARD,
        DISABLED_ARM64_STAGING_CORE_INSTALL_GUARD,
    ),
    (
        "x86_64 CLI legacy-marker recovery",
        X86_64_CLI_LEGACY_MARKER_RECOVERY,
        DISABLED_X86_64_CLI_LEGACY_MARKER_RECOVERY,
    ),
    (
        "arm64 CLI legacy-marker recovery",
        ARM64_CLI_LEGACY_MARKER_RECOVERY,
        DISABLED_ARM64_CLI_LEGACY_MARKER_RECOVERY,
    ),
)
