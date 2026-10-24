"""Nix evaluation policy shared by update code and the stdlib-only CI bootstrap."""

NO_IMPORT_FROM_DERIVATION_ARGS: tuple[str, ...] = (
    "--option",
    "allow-import-from-derivation",
    "false",
)
"""Candidate evaluation must never realize a derivation to import its output."""

__all__ = ["NO_IMPORT_FROM_DERIVATION_ARGS"]
