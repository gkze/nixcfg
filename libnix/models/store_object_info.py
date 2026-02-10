"""Nix store object info models.

Aligned with store-object-info-v2 schema from NixOS/nix.

The schema defines three variants that form an inheritance chain:

- :class:`StoreObjectInfo` -- intrinsic/base fields every store object has.
- :class:`ImpureStoreObjectInfo` -- adds non-intrinsic "impure" metadata
  (deriver, signatures, etc.) that may be absent in some contexts.
- :class:`NarInfo` -- adds binary-cache fields (download URL, compression,
  etc.) present only for objects served from a binary cache.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StoreObjectInfo(BaseModel):
    """Intrinsic metadata for a Nix store object.

    Aligned with store-object-info-v2 schema from NixOS/nix (``base`` variant).

    These fields are always meaningful regardless of how the store object is
    served or stored.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    version: Literal[2] = 2
    """Format version guard (always ``2``)."""

    path: str | None = Field(
        default=None,
        description=(
            "Store path to the given store object. "
            "May be absent when the path is used as a map key."
        ),
    )
    """Store path base name, or ``None`` when used as a map value."""

    nar_hash: str = Field(
        alias="narHash",
        description="SRI hash of the store object serialized as a Nix Archive (NAR).",
    )
    """SRI hash of the NAR serialization."""

    nar_size: int = Field(
        alias="narSize",
        ge=0,
        description="Size in bytes of the NAR serialization.",
    )
    """Size of the NAR serialization in bytes."""

    references: list[str] = Field(
        default_factory=list,
        description="Store paths this object references (may include itself).",
    )
    """Store paths referenced by this object."""

    ca: dict[str, str] | None = Field(
        default=None,
        description=(
            "Content address of the store object (method + hash), "
            "or None for input-addressed objects."
        ),
    )
    """Content address (``{method, hash}``), or ``None`` if input-addressed."""

    store_dir: str = Field(
        alias="storeDir",
        default="/nix/store",
        description="The store directory this object belongs to.",
    )
    """Store directory (e.g. ``/nix/store``)."""


class ImpureStoreObjectInfo(StoreObjectInfo):
    """Store object info extended with impure (non-intrinsic) metadata.

    Aligned with store-object-info-v2 schema from NixOS/nix (``impure`` variant).

    Impure fields capture information that is context-dependent and may not be
    included in every response (e.g. who built the object, trust signatures).
    """

    deriver: str | None = Field(
        default=None,
        description="Store path of the derivation that produced this object, if known.",
    )
    """Derivation store path, or ``None`` if unknown."""

    registration_time: int | None = Field(
        alias="registrationTime",
        default=None,
        description="Unix timestamp of when this object was registered in the store.",
    )
    """Registration timestamp (Unix epoch), or ``None`` if unknown."""

    ultimate: bool = Field(
        default=False,
        description=(
            "Whether this object is trusted because it was built locally "
            "rather than substituted."
        ),
    )
    """``True`` if the object was built locally (not substituted)."""

    signatures: list[str] = Field(
        default_factory=list,
        description="Cryptographic signatures attesting to this object's authenticity.",
    )
    """Signatures for input-addressed trust verification."""

    closure_size: int | None = Field(
        alias="closureSize",
        default=None,
        ge=0,
        description="Total NAR size of this object and its entire closure.",
    )
    """Closure NAR size in bytes (computed, not stored)."""


class NarInfo(ImpureStoreObjectInfo):
    """Store object info with binary cache (narinfo) download fields.

    Aligned with store-object-info-v2 schema from NixOS/nix (``narInfo`` variant).

    Extends :class:`ImpureStoreObjectInfo` with fields specific to objects
    served from a Nix binary cache, describing how to fetch the compressed
    archive.
    """

    url: str = Field(
        description="URL to download the compressed archive of this store object.",
    )
    """Download URL for the compressed archive."""

    compression: str = Field(
        description="Compression format of the archive (e.g. ``xz``, ``zstd``).",
    )
    """Compression algorithm used for the download archive."""

    download_hash: str = Field(
        alias="downloadHash",
        description="SRI hash of the compressed archive itself.",
    )
    """SRI hash of the compressed download."""

    download_size: int = Field(
        alias="downloadSize",
        ge=0,
        description="Size in bytes of the compressed archive.",
    )
    """Size of the compressed download in bytes."""

    closure_download_size: int | None = Field(
        alias="closureDownloadSize",
        default=None,
        ge=0,
        description=(
            "Total compressed download size for this object and its entire closure."
        ),
    )
    """Closure download size in bytes (computed, not stored)."""
