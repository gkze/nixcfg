"""libnix.models -- Pydantic models for Nix data types.

Single import point for all public model types::

    from libnix.models import NixHash, StorePath, FlakeLock, BuildResult, ...
"""

# -- hash ------------------------------------------------------------------
# -- build_result ----------------------------------------------------------
from .build_result import (
    BuildResult,
    FailedBuild,
    FailureStatus,
    SuccessfulBuild,
    SuccessStatus,
    is_hash_mismatch,
)

# -- content_address -------------------------------------------------------
from .content_address import ContentAddress, ContentAddressMethod

# -- derivation ------------------------------------------------------------
from .derivation import Derivation, DerivationInputs, DerivationOutput

# -- flake_lock ------------------------------------------------------------
from .flake_lock import FlakeLock, FlakeLockNode, LockedRef, OriginalRef
from .hash import HashAlgorithm, NixHash, is_sri, make_sri, parse_sri

# -- sources ---------------------------------------------------------------
from .sources import (
    HashCollection,
    HashEntry,
    HashType,
    SourceEntry,
    SourcesFile,
)

# -- store_object_info -----------------------------------------------------
from .store_object_info import ImpureStoreObjectInfo, NarInfo, StoreObjectInfo

# -- store_path ------------------------------------------------------------
from .store_path import (
    DEFAULT_STORE_DIR,
    StorePath,
    full_path,
    is_derivation,
    parse_store_path,
)

__all__ = [
    "DEFAULT_STORE_DIR",
    # build_result
    "BuildResult",
    # content_address
    "ContentAddress",
    "ContentAddressMethod",
    # derivation
    "Derivation",
    "DerivationInputs",
    "DerivationOutput",
    "FailedBuild",
    "FailureStatus",
    # flake_lock
    "FlakeLock",
    "FlakeLockNode",
    "HashAlgorithm",
    "HashCollection",
    "HashEntry",
    "HashType",
    "ImpureStoreObjectInfo",
    "LockedRef",
    "NarInfo",
    # hash
    "NixHash",
    "OriginalRef",
    # sources
    "SourceEntry",
    "SourcesFile",
    # store_object_info
    "StoreObjectInfo",
    # store_path
    "StorePath",
    "SuccessStatus",
    "SuccessfulBuild",
    "full_path",
    "is_derivation",
    "is_hash_mismatch",
    "is_sri",
    "make_sri",
    "parse_sri",
    "parse_store_path",
]
