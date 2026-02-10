"""Shared constants for update workflows."""

REQUIRED_TOOLS = ["nix"]
ALL_TOOLS = ["nix", "nix-prefetch-url"]

FIXED_OUTPUT_NOISE = (
    "error: hash mismatch in fixed-output derivation",
    "specified:",
    "got:",
    "error: Cannot build",
    "Reason:",
    "Output paths:",
    "error: Build failed due to failed dependency",
)

NIX_BUILD_FAILURE_TAIL_LINES = 20
