"""Nix store path type and utilities.

Aligned with store-path-v1 schema from NixOS/nix.

Store paths in JSON are represented as strings containing just the hash and
name portion (e.g. ``g1w7hy3qg1w7hy3qg1w7hy3qg1w7hy3q-foo``), without the
store directory prefix.  The hash is a 32-character Nix base-32 digest and the
name follows after a ``-`` separator.
"""

from typing import Annotated

from pydantic import StringConstraints

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_STORE_DIR: str = "/nix/store"
"""Default Nix store directory."""

NIX32_CHARS: str = "0123456789abcdfghijklmnpqrsvwxyz"
"""The Nix base-32 alphabet (note: no ``e``, ``o``, ``t``, ``u``)."""

STORE_PATH_PATTERN: str = r"^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$"
"""Regex pattern for a valid store path base name.

Aligned with store-path-v1 schema from NixOS/nix.
"""

# ---------------------------------------------------------------------------
# Annotated type
# ---------------------------------------------------------------------------

StorePath = Annotated[
    str,
    StringConstraints(
        min_length=34,
        pattern=STORE_PATH_PATTERN,
    ),
]
"""A Nix store path base name (hash + name, without the store directory).

Aligned with store-path-v1 schema from NixOS/nix.

Example valid value: ``g1w7hy3qg1w7hy3qg1w7hy3qg1w7hy3q-foo``
"""

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

_HASH_LEN = 32


def full_path(store_path: str, store_dir: str = DEFAULT_STORE_DIR) -> str:
    """Prepend the store directory to get a full filesystem path.

    >>> full_path("g1w7hy3qg1w7hy3qg1w7hy3qg1w7hy3q-foo")
    '/nix/store/g1w7hy3qg1w7hy3qg1w7hy3qg1w7hy3q-foo'
    """
    return f"{store_dir}/{store_path}"


def parse_store_path(store_path: str) -> tuple[str, str]:
    """Split a store path base name into its hash and name parts.

    The hash is the first 32 characters (Nix base-32 digest) and the name
    is everything after the ``-`` separator at position 32.

    >>> parse_store_path("g1w7hy3qg1w7hy3qg1w7hy3qg1w7hy3q-foo-1.0")
    ('g1w7hy3qg1w7hy3qg1w7hy3qg1w7hy3q', 'foo-1.0')
    """
    hash_part = store_path[:_HASH_LEN]
    # Skip the separator '-' at index 32
    name_part = store_path[_HASH_LEN + 1 :]
    return hash_part, name_part


def is_derivation(store_path: str) -> bool:
    """Return ``True`` if the store path refers to a derivation (``.drv``).

    >>> is_derivation("g1w7hy3qg1w7hy3qg1w7hy3qg1w7hy3q-foo.drv")
    True
    >>> is_derivation("g1w7hy3qg1w7hy3qg1w7hy3qg1w7hy3q-foo")
    False
    """
    return store_path.endswith(".drv")
