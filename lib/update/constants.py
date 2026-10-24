"""Shared constants for update workflows."""

import base64
import hashlib

REQUIRED_TOOLS = ("nix",)
ALL_TOOLS = ("nix", "nix-prefetch-url", "uv")

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

FAKE_HASH = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def placeholder_hash_for(hash_type: str) -> str:
    """Return a distinct valid SRI so two FODs are not both ``lib.fakeHash``.

    Nix hash-mismatch rewriting cannot tell two ``sha256-AAAA…`` FODs
    apart (turso-cli srcHash plus vendorHash). Sequential probes keep
    one placeholder live at a time; this salt is the unique-position
    fallback when both still appear in one evaluation.
    """
    digest = hashlib.sha256(f"nixcfg.placeholder:{hash_type}".encode()).digest()
    return f"sha256-{base64.b64encode(digest).decode()}"
