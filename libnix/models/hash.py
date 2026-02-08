"""Nix hash types and SRI string utilities.

Aligned with hash-v1 schema from NixOS/nix.
"""

import re
from enum import StrEnum
from typing import Annotated

from pydantic import StringConstraints

from ._generated import Algorithm as _GeneratedAlgorithm  # noqa: F401

_SRI_PATTERN = r"^(blake3|md5|sha1|sha256|sha512)-[A-Za-z0-9+/]+=*$"
_SRI_RE = re.compile(_SRI_PATTERN)


class HashAlgorithm(StrEnum):
    """Hash algorithms supported by Nix.

    Aligned with hash-v1 schema from NixOS/nix.
    """

    blake3 = "blake3"
    md5 = "md5"
    sha1 = "sha1"
    sha256 = "sha256"
    sha512 = "sha512"


NixHash = Annotated[
    str,
    StringConstraints(pattern=_SRI_PATTERN),
]
"""An SRI hash string as used throughout Nix for content addressing.

Aligned with hash-v1 schema from NixOS/nix.

Format: ``<algorithm>-<base64_digest>``

Examples::

    sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=
    sha512-IEqPxt2oLwoM7XvrjgikFlfBbvRosiioJ5vjMacDwzWW/RXBOxsH+aodO+pXeJygMa2Fx6cd1wNU7GMSOMo0RQ==
"""


def parse_sri(sri: str) -> tuple[HashAlgorithm, str]:
    """Split an SRI hash string into its algorithm and base64 digest.

    Args:
        sri: An SRI hash string, e.g. ``sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=``.

    Returns:
        A ``(HashAlgorithm, digest_b64)`` tuple.

    Raises:
        ValueError: If *sri* does not match the expected SRI pattern.
    """
    if not _SRI_RE.match(sri):
        msg = f"invalid SRI hash: {sri!r} (expected pattern {_SRI_PATTERN})"
        raise ValueError(msg)
    algo, digest = sri.split("-", 1)
    return HashAlgorithm(algo), digest


def is_sri(value: str) -> bool:
    """Return ``True`` if *value* is a well-formed SRI hash string.

    >>> is_sri("sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=")
    True
    >>> is_sri("0000000000000000000000000000000000000000000000000000")
    False
    """
    return _SRI_RE.match(value) is not None


def make_sri(algorithm: HashAlgorithm, digest_b64: str) -> NixHash:
    """Construct an SRI hash string from an algorithm and base64 digest.

    Args:
        algorithm: The hash algorithm.
        digest_b64: The base64-encoded digest (standard base64 with ``=`` padding).

    Returns:
        The assembled SRI string.

    Raises:
        ValueError: If the resulting string does not match the expected SRI pattern.
    """
    sri = f"{algorithm}-{digest_b64}"
    if not _SRI_RE.match(sri):
        msg = f"invalid SRI hash: {sri!r} (expected pattern {_SRI_PATTERN})"
        raise ValueError(msg)
    return sri
