"""Content-addressing types for Nix store objects.

Aligned with content-address-v1 schema from NixOS/nix.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ContentAddressMethod(StrEnum):
    """Method used to content-address a store object.

    - flat: Hash of a single file's contents.
    - nar: Hash of the NAR serialisation of a file system object.
    - text: Like flat, but the store object may only have references to other
      store objects that are encoded in its contents (self-references are not
      allowed).
    - git: Hash using the Git blob/tree format (experimental).
    """

    FLAT = "flat"
    NAR = "nar"
    TEXT = "text"
    GIT = "git"


class ContentAddress(BaseModel):
    """Content address of a Nix store object.

    Aligned with content-address-v1 schema from NixOS/nix.
    """

    model_config = ConfigDict(extra="forbid")

    method: ContentAddressMethod
    hash: str
    """SRI hash string (e.g. ``sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=``)."""
