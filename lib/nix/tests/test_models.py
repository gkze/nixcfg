"""Tests for lib.nix model types and utilities."""

# ruff: noqa: S101

import json

import pytest
from pydantic import TypeAdapter, ValidationError

from lib.nix.models.build_result import (
    FailedBuild,
    FailureStatus,
    SuccessfulBuild,
    SuccessStatus,
    is_hash_mismatch,
)
from lib.nix.models.flake_lock import FlakeLock, LockedRef
from lib.nix.models.hash import HashAlgorithm, NixHash, is_sri, make_sri, parse_sri
from lib.nix.models.sources import SourceEntry, SourcesFile
from lib.nix.models.store_path import (
    StorePath,
    full_path,
    is_derivation,
    parse_store_path,
)
from lib.update.paths import REPO_ROOT, package_file_map

# Reusable adapter for Annotated types that aren't full BaseModels.
_NixHashAdapter = TypeAdapter(NixHash)
_StorePathAdapter = TypeAdapter(StorePath)

# Paths to real project files used in integration-style tests.
FLAKE_LOCK_PATH = REPO_ROOT / "flake.lock"
FLAKE_LOCK_VERSION = 7

# =========================================================================
# NixHash tests
# =========================================================================


class TestNixHash:
    """Validation and parsing of SRI hash strings."""

    def test_valid_sri_hashes(self) -> None:
        """sha256, sha512, and sha1 SRI strings pass validation."""
        valid = [
            "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
            "sha512-IEqPxt2oLwoM7XvrjgikFlfBbvRosiioJ5vjMacDwzWW/RXBOxsH+aodO+pXeJygMa2Fx6cd1wNU7GMSOMo0RQ==",
            "sha1-W6wnJDVmJhzQw1rlC7EBuBNE8jI=",
        ]
        for sri in valid:
            result = _NixHashAdapter.validate_python(sri)
            assert result == sri

    def test_invalid_sri_hashes(self) -> None:
        """Missing algorithm prefix, bad base64 chars, and empty string all fail."""
        invalid = [
            "ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",  # no algorithm
            "sha256-not valid base64!!",  # spaces / illegal chars
            "",  # empty
        ]
        for sri in invalid:
            with pytest.raises(ValidationError):
                _NixHashAdapter.validate_python(sri)

    def test_parse_sri(self) -> None:
        """parse_sri correctly splits algorithm and digest."""
        algo, digest = parse_sri("sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=")
        assert algo is HashAlgorithm.sha256
        assert digest == "ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0="

    def test_parse_sri_invalid(self) -> None:
        """parse_sri raises ValueError on bad input."""
        with pytest.raises(ValueError, match="invalid SRI hash"):
            parse_sri("not-a-valid-sri")
        with pytest.raises(ValueError, match="invalid SRI hash"):
            parse_sri("")

    def test_make_sri(self) -> None:
        """make_sri constructs a valid SRI string from parts."""
        sri = make_sri(
            HashAlgorithm.sha256,
            "ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
        )
        assert sri == "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0="

    def test_make_sri_invalid_digest(self) -> None:
        """make_sri raises ValueError when the digest is invalid."""
        with pytest.raises(ValueError, match="invalid SRI hash"):
            make_sri(HashAlgorithm.sha256, "!!!bad!!!")

    def test_is_sri_valid(self) -> None:
        """is_sri returns True for well-formed SRI hash strings."""
        assert is_sri("sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=") is True
        assert (
            is_sri(
                "sha512-IEqPxt2oLwoM7XvrjgikFlfBbvRosiioJ5vjMacDwzWW/RXBOxsH+aodO+pXeJygMa2Fx6cd1wNU7GMSOMo0RQ==",
            )
            is True
        )

    def test_is_sri_invalid(self) -> None:
        """is_sri returns False for non-SRI strings."""
        assert is_sri("0000000000000000000000000000000000000000000000000000") is False
        assert is_sri("sha256:abc123") is False
        assert is_sri("") is False
        assert is_sri("not-a-hash") is False


# =========================================================================
# StorePath tests
# =========================================================================


class TestStorePath:
    """Validation and utilities for Nix store path base names."""

    # A valid Nix32 hash (32 chars from the Nix base-32 alphabet).
    VALID_HASH = "g1w7hy3qg1w7hy3qg1w7hy3qg1w7hy3q"

    def test_valid_store_path(self) -> None:
        """A valid Nix32 hash + name passes validation."""
        sp = f"{self.VALID_HASH}-hello-2.12.1"
        result = _StorePathAdapter.validate_python(sp)
        assert result == sp

    def test_invalid_store_path(self) -> None:
        """Too short, wrong chars, and missing dash all fail validation."""
        invalid = [
            "abc-short",  # way too short
            "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-bad",  # 'e' not in Nix32 alphabet
            f"{self.VALID_HASH}nodash",  # no dash separator at position 32
        ]
        for sp in invalid:
            with pytest.raises(ValidationError):
                _StorePathAdapter.validate_python(sp)

    def test_parse_store_path(self) -> None:
        """parse_store_path correctly splits hash and name parts."""
        sp = f"{self.VALID_HASH}-foo-1.0"
        hash_part, name_part = parse_store_path(sp)
        assert hash_part == self.VALID_HASH
        assert name_part == "foo-1.0"

    def test_full_path(self) -> None:
        """full_path prepends /nix/store/."""
        sp = f"{self.VALID_HASH}-foo"
        assert full_path(sp) == f"/nix/store/{sp}"

    def test_full_path_custom_store_dir(self) -> None:
        """full_path respects a custom store directory."""
        sp = f"{self.VALID_HASH}-foo"
        assert full_path(sp, "/custom/store") == f"/custom/store/{sp}"

    def test_is_derivation(self) -> None:
        """True for .drv paths, False for others."""
        assert is_derivation(f"{self.VALID_HASH}-foo.drv") is True
        assert is_derivation(f"{self.VALID_HASH}-foo") is False
        assert is_derivation(f"{self.VALID_HASH}-foo-1.0") is False


# =========================================================================
# BuildResult tests
# =========================================================================


class TestBuildResult:
    """SuccessfulBuild, FailedBuild, and is_hash_mismatch helper."""

    def test_successful_build(self) -> None:
        """SuccessfulBuild has Literal[True] success field."""
        build = SuccessfulBuild(status=SuccessStatus.Built)
        assert build.success is True
        assert build.status is SuccessStatus.Built

    def test_successful_build_all_statuses(self) -> None:
        """All SuccessStatus values are accepted."""
        for status in SuccessStatus:
            build = SuccessfulBuild(status=status)
            assert build.success is True

    def test_failed_build(self) -> None:
        """FailedBuild has Literal[False] success field."""
        build = FailedBuild(
            status=FailureStatus.PermanentFailure,
            errorMsg="something broke",
        )
        assert build.success is False
        assert build.status is FailureStatus.PermanentFailure
        assert build.error_msg == "something broke"

    def test_hash_mismatch_status(self) -> None:
        """is_hash_mismatch() returns True for HashMismatch status."""
        build = FailedBuild(
            status=FailureStatus.HashMismatch,
            errorMsg="hash mismatch in fixed-output derivation",
        )
        assert is_hash_mismatch(build) is True

    def test_non_hash_mismatch_status(self) -> None:
        """is_hash_mismatch() returns False for other failure statuses."""
        build = FailedBuild(
            status=FailureStatus.TransientFailure,
            errorMsg="network error",
        )
        assert is_hash_mismatch(build) is False


# =========================================================================
# FlakeLock tests
# =========================================================================


class TestFlakeLock:
    """Loading and inspecting flake.lock files."""

    @pytest.fixture
    def lock(self) -> FlakeLock:
        """Load the real flake.lock from the project root."""
        assert FLAKE_LOCK_PATH.exists(), f"flake.lock not found at {FLAKE_LOCK_PATH}"
        return FlakeLock.from_file(FLAKE_LOCK_PATH)

    def test_flake_lock_from_file(self, lock: FlakeLock) -> None:
        """Load the actual flake.lock; verify version=7, nodes exist, input_names returns a list."""
        assert lock.version == FLAKE_LOCK_VERSION
        assert len(lock.nodes) > 0
        names = lock.input_names
        assert isinstance(names, list)
        assert len(names) > 0

    def test_flake_lock_root_node(self, lock: FlakeLock) -> None:
        """root_node property works and has inputs."""
        root = lock.root_node
        assert root is not None
        assert root.inputs is not None
        assert len(root.inputs) > 0
        # Root node should not have locked/original
        assert root.locked is None
        assert root.original is None

    def test_flake_lock_get_locked(self, lock: FlakeLock) -> None:
        """get_locked returns LockedRef with narHash for a real input."""
        # Pick the first available input name.
        first_input = lock.input_names[0]
        locked = lock.get_locked(first_input)
        assert locked is not None
        assert isinstance(locked, LockedRef)
        assert locked.nar_hash.startswith("sha256-")

    def test_flake_lock_get_locked_missing(self, lock: FlakeLock) -> None:
        """get_locked returns None for a non-existent input."""
        assert lock.get_locked("__nonexistent_input__") is None


# =========================================================================
# SourcesFile tests
# =========================================================================


class TestSourcesFile:
    """Loading and round-tripping per-package sources.json files."""

    @pytest.fixture
    def sources(self) -> SourcesFile:
        """Aggregate all per-package sources.json into a SourcesFile."""
        pkg_files = package_file_map("sources.json")
        assert pkg_files, "no per-package sources.json files found"
        entries = {
            name: SourceEntry.model_validate(json.loads(path.read_text()))
            for name, path in pkg_files.items()
        }
        return SourcesFile(entries=entries)

    def test_sources_file_load(self, sources: SourcesFile) -> None:
        """Aggregate per-package sources; verify entries exist."""
        assert len(sources.entries) > 0
        # Each entry should have hashes.
        for name, entry in sources.entries.items():
            assert entry.hashes is not None, f"entry {name!r} missing hashes"

    def test_sources_file_round_trip(self, sources: SourcesFile) -> None:
        """Load, to_dict, from_dict — verify structural equality."""
        as_dict = sources.to_dict()
        reloaded = SourcesFile.from_dict(as_dict)
        assert reloaded.to_dict() == as_dict
        assert set(reloaded.entries.keys()) == set(sources.entries.keys())
