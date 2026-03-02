"""Tests for lib.nix model types and utilities."""

import json
from typing import TYPE_CHECKING, cast

import pytest
from pydantic import TypeAdapter, ValidationError

from lib.nix.models.build_result import (
    FailedBuild,
    FailureStatus,
    SuccessfulBuild,
    SuccessStatus,
    is_hash_mismatch,
)
from lib.nix.models.derivation import Derivation
from lib.nix.models.flake_lock import FlakeLock, FlakeLockNode, LockedRef
from lib.nix.models.hash import HashAlgorithm, NixHash, is_sri, make_sri, parse_sri
from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry, SourcesFile
from lib.nix.models.store_path import (
    StorePath,
    full_path,
    is_derivation,
    parse_store_path,
)
from lib.tests._assertions import check, expect_instance, expect_not_none
from lib.update.paths import REPO_ROOT, package_file_map

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

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
            check(result == sri)

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
        check(algo is HashAlgorithm.sha256)
        check(digest == "ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=")

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
        check(sri == "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=")

    def test_make_sri_invalid_digest(self) -> None:
        """make_sri raises ValueError when the digest is invalid."""
        with pytest.raises(ValueError, match="invalid SRI hash"):
            make_sri(HashAlgorithm.sha256, "!!!bad!!!")

    def test_is_sri_valid(self) -> None:
        """is_sri returns True for well-formed SRI hash strings."""
        check(is_sri("sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=") is True)
        check(
            is_sri(
                "sha512-IEqPxt2oLwoM7XvrjgikFlfBbvRosiioJ5vjMacDwzWW/RXBOxsH+aodO+pXeJygMa2Fx6cd1wNU7GMSOMo0RQ==",
            )
            is True
        )

    def test_is_sri_invalid(self) -> None:
        """is_sri returns False for non-SRI strings."""
        check(is_sri("0000000000000000000000000000000000000000000000000000") is False)
        check(is_sri("sha256:abc123") is False)
        check(is_sri("") is False)
        check(is_sri("not-a-hash") is False)


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
        check(result == sp)

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
        check(hash_part == self.VALID_HASH)
        check(name_part == "foo-1.0")

    def test_full_path(self) -> None:
        """full_path prepends /nix/store/."""
        sp = f"{self.VALID_HASH}-foo"
        check(full_path(sp) == f"/nix/store/{sp}")

    def test_full_path_custom_store_dir(self) -> None:
        """full_path respects a custom store directory."""
        sp = f"{self.VALID_HASH}-foo"
        check(full_path(sp, "/custom/store") == f"/custom/store/{sp}")

    def test_is_derivation(self) -> None:
        """True for .drv paths, False for others."""
        check(is_derivation(f"{self.VALID_HASH}-foo.drv") is True)
        check(is_derivation(f"{self.VALID_HASH}-foo") is False)
        check(is_derivation(f"{self.VALID_HASH}-foo-1.0") is False)


# =========================================================================
# BuildResult tests
# =========================================================================


class TestBuildResult:
    """SuccessfulBuild, FailedBuild, and is_hash_mismatch helper."""

    def test_successful_build(self) -> None:
        """SuccessfulBuild has Literal[True] success field."""
        build = SuccessfulBuild(status=SuccessStatus.Built)
        check(build.success is True)
        check(build.status is SuccessStatus.Built)

    def test_successful_build_all_statuses(self) -> None:
        """All SuccessStatus values are accepted."""
        for status in SuccessStatus:
            build = SuccessfulBuild(status=status)
            check(build.success is True)

    def test_failed_build(self) -> None:
        """FailedBuild has Literal[False] success field."""
        build = FailedBuild(
            status=FailureStatus.PermanentFailure,
            errorMsg="something broke",
        )
        check(build.success is False)
        check(build.status is FailureStatus.PermanentFailure)
        check(build.error_msg == "something broke")

    def test_hash_mismatch_status(self) -> None:
        """is_hash_mismatch() returns True for HashMismatch status."""
        build = FailedBuild(
            status=FailureStatus.HashMismatch,
            errorMsg="hash mismatch in fixed-output derivation",
        )
        check(is_hash_mismatch(build) is True)

    def test_non_hash_mismatch_status(self) -> None:
        """is_hash_mismatch() returns False for other failure statuses."""
        build = FailedBuild(
            status=FailureStatus.TransientFailure,
            errorMsg="network error",
        )
        check(is_hash_mismatch(build) is False)


# =========================================================================
# FlakeLock tests
# =========================================================================


class TestFlakeLock:
    """Loading and inspecting flake.lock files."""

    @pytest.fixture
    def lock(self) -> FlakeLock:
        """Load the real flake.lock from the project root."""
        check(FLAKE_LOCK_PATH.exists(), f"flake.lock not found at {FLAKE_LOCK_PATH}")
        return FlakeLock.from_file(FLAKE_LOCK_PATH)

    def test_flake_lock_from_file(self, lock: FlakeLock) -> None:
        """Load the actual flake.lock; verify version=7, nodes exist, input_names returns a list."""
        check(lock.version == FLAKE_LOCK_VERSION)
        check(len(lock.nodes) > 0)
        names = lock.input_names
        check(isinstance(names, list))
        check(len(names) > 0)

    def test_flake_lock_root_node(self, lock: FlakeLock) -> None:
        """root_node property works and has inputs."""
        root = lock.root_node
        root = expect_not_none(root)
        root_inputs = expect_not_none(root.inputs)
        check(len(root_inputs) > 0)
        # Root node should not have locked/original
        check(root.locked is None)
        check(root.original is None)

    def test_flake_lock_get_locked(self, lock: FlakeLock) -> None:
        """get_locked returns LockedRef with narHash for a real input."""
        # Pick the first available input name.
        first_input = lock.input_names[0]
        locked = lock.get_locked(first_input)
        locked = expect_not_none(locked)
        check(isinstance(locked, LockedRef))
        check(locked.nar_hash.startswith("sha256-"))

    def test_flake_lock_get_locked_missing(self, lock: FlakeLock) -> None:
        """get_locked returns None for a non-existent input."""
        check(lock.get_locked("__nonexistent_input__") is None)

    def test_flake_lock_from_dict_and_no_root_inputs(self) -> None:
        """from_dict parses lock data and handles a root without inputs."""
        lock = FlakeLock.from_dict(
            {
                "nodes": {"root": {}},
                "version": FLAKE_LOCK_VERSION,
            },
        )
        check(lock.version == FLAKE_LOCK_VERSION)
        check(lock.input_names == [])

    def test_flake_lock_from_file_requires_object(self, tmp_path: Path) -> None:
        """from_file rejects non-object top-level JSON payloads."""
        lock_path = tmp_path / "flake.lock"
        lock_path.write_text("[]", encoding="utf-8")
        with pytest.raises(TypeError, match="top-level value must be a JSON object"):
            FlakeLock.from_file(lock_path)

    def test_flake_lock_get_locked_path_resolution_edges(self) -> None:
        """get_locked resolves follow paths and returns None for invalid branches."""
        sri = "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0="
        lock = FlakeLock.from_dict(
            {
                "nodes": {
                    "bridge": {
                        "inputs": {
                            "leaf": "target",
                            "multi": ["not", "a", "string"],
                        },
                    },
                    "root": {
                        "inputs": {
                            "badRef": ["bridge", "multi"],
                            "follows": ["bridge", "leaf"],
                            "ghostFinal": "missing-target",
                            "missingNode": ["missing-bridge", "leaf"],
                        },
                    },
                    "target": {
                        "locked": {
                            "narHash": sri,
                            "type": "github",
                        },
                    },
                },
                "version": FLAKE_LOCK_VERSION,
            },
        )

        locked = lock.get_locked("follows")
        locked = expect_not_none(locked)
        check(locked.nar_hash == sri)
        check(lock.get_locked("missingNode") is None)
        check(lock.get_locked("badRef") is None)
        check(lock.get_locked("ghostFinal") is None)

    def test_flake_lock_path_resolution_handles_none_node_name(self) -> None:
        """Path resolution exits early when the first path segment is missing."""
        lock = FlakeLock.model_construct(
            nodes={
                "root": FlakeLockNode.model_construct(
                    inputs={
                        "broken": cast("list[str]", [None, "leaf"]),
                    }
                ),
                "leaf": FlakeLockNode.model_construct(
                    locked=LockedRef.model_validate({
                        "narHash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                        "type": "github",
                    })
                ),
            },
            version=FLAKE_LOCK_VERSION,
        )
        check(lock.get_locked("broken") is None)


# =========================================================================
# Derivation tests
# =========================================================================


class TestDerivation:
    """Small behavior checks for helper properties."""

    def test_derivation_output_helper_properties(self) -> None:
        """output_names sorts keys; is_fixed_output reflects hashed outputs."""
        no_hash = Derivation.model_validate({
            "args": [],
            "builder": "/bin/sh",
            "env": {},
            "inputs": {"drvs": {}, "srcs": []},
            "name": "demo",
            "outputs": {
                "out": {"path": "/nix/store/x-demo"},
                "dev": {"path": "/nix/store/x-demo-dev"},
            },
            "system": "x86_64-linux",
            "version": 4,
        })
        check(no_hash.output_names == ["dev", "out"])
        check(no_hash.is_fixed_output is False)

        with_hash = Derivation.model_validate({
            "args": [],
            "builder": "/bin/sh",
            "env": {},
            "inputs": {"drvs": {}, "srcs": []},
            "name": "demo-fixed",
            "outputs": {
                "out": {
                    "hash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                    "method": "flat",
                }
            },
            "system": "x86_64-linux",
            "version": 4,
        })
        check(with_hash.is_fixed_output is True)


# =========================================================================
# SourcesFile tests
# =========================================================================


class TestSourcesFile:
    """Loading and round-tripping per-package sources.json files."""

    @pytest.fixture
    def sources(self) -> SourcesFile:
        """Aggregate all per-package sources.json into a SourcesFile."""
        pkg_files = package_file_map("sources.json")
        check(pkg_files, "no per-package sources.json files found")
        entries = {
            name: SourceEntry.model_validate(json.loads(path.read_text()))
            for name, path in pkg_files.items()
        }
        return SourcesFile(entries=entries)

    def test_sources_file_load(self, sources: SourcesFile) -> None:
        """Aggregate per-package sources; verify entries exist."""
        check(len(sources.entries) > 0)
        # Each entry should have hashes.
        for name, entry in sources.entries.items():
            check(entry.hashes is not None, f"entry {name!r} missing hashes")

    def test_sources_file_round_trip(self, sources: SourcesFile) -> None:
        """Load, to_dict, from_dict — verify structural equality."""
        as_dict = sources.to_dict()
        reloaded = SourcesFile.from_dict(as_dict)
        check(reloaded.to_dict() == as_dict)
        check(set(reloaded.entries.keys()) == set(sources.entries.keys()))

    def test_hash_collection_parsing_and_primary_hash_variants(self) -> None:
        """HashCollection handles mapping/list/input-shape edge cases."""
        h1 = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        h2 = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="

        entry = HashEntry.create("sha256", h1)
        entries_collection = HashCollection.from_value([entry])
        check(entries_collection.to_json() == [entry.to_dict()])
        check(entries_collection.primary_hash() == h1)

        mapping_collection = HashCollection.from_value({"darwin": h2, "linux": h2})
        check(mapping_collection.to_json() == {"darwin": h2, "linux": h2})
        check(mapping_collection.primary_hash() == h2)

        copied_shape = HashCollection.model_validate(mapping_collection).model_dump()
        check(
            copied_shape
            == {
                "entries": None,
                "mapping": {"darwin": h2, "linux": h2},
            }
        )

        with pytest.raises(TypeError, match="Hash mapping values must be strings"):
            HashCollection.model_validate({"darwin": 123})

        with pytest.raises(ValueError, match="SRI format"):
            HashCollection.from_value({"darwin": "not-a-sri-hash"})

        with pytest.raises(ValidationError, match="Hashes must be a list or dict"):
            HashCollection.model_validate(1)

        check(HashCollection().to_json() == {})

    def test_hash_entry_create_validates_optional_fields(self) -> None:
        """HashEntry.create validates optional string and urls kwargs."""
        h1 = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

        entry = HashEntry.create(
            "sha256",
            h1,
            urls={"linux": "https://example.invalid/src.tar.gz"},
        )
        check(
            entry.to_dict()["urls"] == {"linux": "https://example.invalid/src.tar.gz"}
        )

        with pytest.raises(TypeError, match="git_dep must be a string"):
            HashEntry.create("sha256", h1, git_dep=1)

        with pytest.raises(TypeError, match="urls must be a mapping"):
            HashEntry.create("sha256", h1, urls="https://example.invalid/src.tar.gz")

        with pytest.raises(
            TypeError, match="urls must contain only string keys and values"
        ):
            HashEntry.create(
                "sha256", h1, urls={1: "https://example.invalid/src.tar.gz"}
            )

        with pytest.raises(
            TypeError, match="urls must contain only string keys and values"
        ):
            HashEntry.create("sha256", h1, urls={"linux": 1})

        with pytest.raises(
            TypeError,
            match=r"Unexpected HashEntry\.create kwargs: extra",
        ):
            HashEntry.create("sha256", h1, extra=True)

    def test_hash_collection_parse_input_additional_paths(self) -> None:
        """HashCollection.parse_input handles model and invalid-key paths."""
        h1 = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        h2 = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
        parse_input = cast(
            "Callable[[object], dict[str, object]]",
            HashCollection.parse_input,
        )

        with pytest.raises(TypeError, match="Hash mapping keys must be strings"):
            parse_input({1: h1})

        parsed = parse_input(HashCollection(mapping={"linux": h1}))
        check(parsed == {"entries": None, "mapping": {"linux": h1}})

        distinct_mapping = HashCollection.from_value({"darwin": h1, "linux": h2})
        check(distinct_mapping.primary_hash() is None)

    def test_hash_collection_merge_paths(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Merge supports entries and mappings, rejects incompatible shapes."""
        h1 = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        h2 = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
        fake = "sha256-FAKEBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
        monkeypatch.setattr(HashCollection, "FAKE_HASH_PREFIX", "sha256-FAKE")

        entry_old = HashEntry.create("sha256", h1)
        entry_new = HashEntry.create("sha256", h2)
        entry_fake = HashEntry.create("sha256", fake)

        merged_entries = HashCollection(entries=[entry_old, entry_fake]).merge(
            HashCollection(entries=[entry_new]),
        )
        entries = expect_not_none(merged_entries.entries)
        check([e.hash for e in entries] == [h2])

        merged_mapping = HashCollection(mapping={"darwin": h1, "linux": fake}).merge(
            HashCollection(mapping={"linux": h2}),
        )
        check(merged_mapping.mapping == {"darwin": h1, "linux": h2})

        with pytest.raises(
            ValueError,
            match="Cannot merge hash entries with hash mapping",
        ):
            HashCollection(entries=[entry_old]).merge(
                HashCollection(mapping={"linux": h1})
            )

        with pytest.raises(
            ValueError,
            match="Cannot merge hash mapping with hash entries",
        ):
            HashCollection(mapping={"linux": h1}).merge(
                HashCollection(entries=[entry_old])
            )

        other = HashCollection(mapping={"linux": h2})
        empty_constructed = HashCollection.model_construct(entries=None, mapping=None)
        check(empty_constructed.merge(other) is other)
        check(empty_constructed.to_json() == {})

    def test_source_entry_sources_file_merge_load_save(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SourceEntry/SourcesFile merging, loading, and saving paths."""
        h1 = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        h2 = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="

        base_entry = SourceEntry.model_validate({
            "commit": "a" * 40,
            "drvHash": "drv-base",
            "hashes": {"linux": h1},
            "input": "base-input",
            "urls": {"upstream": "https://example.invalid/base.tar.gz"},
            "version": "1.0.0",
        })
        incoming_entry = SourceEntry.model_validate({
            "commit": "b" * 40,
            "drvHash": "drv-incoming",
            "hashes": {"linux": h2},
            "input": "incoming-input",
            "urls": {"mirror": "https://example.invalid/mirror.tar.gz"},
            "version": "2.0.0",
        })
        merged_entry = base_entry.merge(incoming_entry)
        check(merged_entry.version == "2.0.0")
        check(merged_entry.input == "incoming-input")
        check(
            merged_entry.urls
            == {
                "mirror": "https://example.invalid/mirror.tar.gz",
                "upstream": "https://example.invalid/base.tar.gz",
            }
        )

        with_schema = {
            "$schema": "https://example.invalid/sources.schema.json",
            "pkgA": {
                "hashes": {"linux": h1},
                "version": "1.0.0",
            },
        }
        parsed = SourcesFile.from_dict(with_schema)
        check("$schema" not in parsed.entries)
        check("pkgA" in parsed.entries)

        missing = tmp_path / "missing-sources.json"
        check(SourcesFile.load(missing).entries == {})

        existing = tmp_path / "sources.json"
        existing.write_text(json.dumps(with_schema), encoding="utf-8")
        loaded = SourcesFile.load(existing)
        check("pkgA" in loaded.entries)

        current = SourcesFile.from_dict(
            {
                "pkgA": {
                    "hashes": {"linux": h1},
                    "version": "1.0.0",
                },
            },
        )
        updates = SourcesFile.from_dict(
            {
                "pkgA": {
                    "hashes": {"linux": h2},
                    "version": "2.0.0",
                },
                "pkgB": {
                    "hashes": {"linux": h1},
                    "version": "1.0.0",
                },
            },
        )
        merged_file = current.merge(updates)
        check(set(merged_file.entries) == {"pkgA", "pkgB"})
        check(merged_file.entries["pkgA"].version == "2.0.0")

        captured: dict[str, object] = {}

        def _atomic_write_text(path: Path, payload: str, *, mkdir: bool) -> None:
            captured["mkdir"] = mkdir
            captured["path"] = path
            captured["payload"] = payload

        monkeypatch.setattr("lib.update.io.atomic_write_text", _atomic_write_text)
        out_path = tmp_path / "written-sources.json"
        merged_file.save(out_path)
        check(captured["path"] == out_path)
        check(captured["mkdir"] is True)
        payload = expect_instance(captured["payload"], str)
        check(payload.endswith("\n"))

    def test_sources_file_rejects_invalid_top_level_shapes(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SourcesFile validates key and top-level JSON object shape."""
        h1 = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

        with pytest.raises(
            TypeError,
            match=r"sources\.json top-level keys must be strings",
        ):
            SourcesFile.from_dict(
                cast("dict[str, object]", {1: {"hashes": {"linux": h1}}})
            )

        bad_json = tmp_path / "bad-sources.json"
        bad_json.write_text("[]", encoding="utf-8")
        with pytest.raises(
            TypeError,
            match=r"sources\.json top-level value must be a JSON object",
        ):
            SourcesFile.load(bad_json)

        monkeypatch.setattr("lib.nix.models.sources.json.loads", lambda _text: {1: {}})
        with pytest.raises(
            TypeError,
            match=r"sources\.json top-level keys must be strings",
        ):
            SourcesFile.load(bad_json)

    def test_sources_file_json_schema_structure(self) -> None:
        """Generated schema exposes hash collection as oneOf array/dict."""
        schema = SourcesFile.json_schema()
        defs_obj = expect_instance(schema.get("$defs"), dict)
        defs: dict[str, object] = {}
        for raw_key, value in defs_obj.items():
            key = expect_instance(raw_key, str)
            defs[key] = value

        hash_collection_def = expect_instance(defs.get("HashCollection"), dict)
        hash_collection_def_map: dict[str, object] = {}
        for raw_key, value in hash_collection_def.items():
            key = expect_instance(raw_key, str)
            hash_collection_def_map[key] = value

        one_of = expect_instance(hash_collection_def_map.get("oneOf"), list)
        check(one_of)
        first_variant_obj = one_of[0]
        first_variant_obj = expect_instance(first_variant_obj, dict)
        first_variant: dict[str, object] = {}
        for raw_key, value in first_variant_obj.items():
            key = expect_instance(raw_key, str)
            first_variant[key] = value
        check(first_variant["type"] == "array")
