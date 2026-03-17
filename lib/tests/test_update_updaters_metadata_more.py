"""Additional tests for updater metadata and registry helpers."""

from __future__ import annotations

from abc import ABC, abstractmethod

import pytest
from pydantic import BaseModel

from lib.nix.models.flake_lock import FlakeLockNode
from lib.tests._assertions import check
from lib.update.updaters import metadata as metadata_module
from lib.update.updaters.metadata import (
    NO_METADATA,
    DownloadUrlMetadata,
    FlakeInputMetadata,
    PlatformAPIMetadata,
    ReleasePayloadMetadata,
    VersionInfo,
    deserialize_metadata,
    serialize_metadata,
)
from lib.update.updaters.registry import UPDATERS, register_updater


def test_mapping_metadata_helpers_and_version_info_commit_paths() -> None:
    """Expose typed metadata through dict-like helpers and commit accessors."""
    node = FlakeLockNode(locked=None)
    flake_metadata = FlakeInputMetadata(node=node, commit="abc123")
    check(flake_metadata["node"] is node)
    check(flake_metadata.get("commit") == "abc123")
    check("node" in flake_metadata)

    platform_metadata = PlatformAPIMetadata(
        platform_info={"x86_64-linux": {"sha256hash": "x"}},
        equality_fields={"build": "2026-03-01"},
    )
    check(platform_metadata["build"] == "2026-03-01")
    check("commit" not in platform_metadata)

    check(VersionInfo(version="1", metadata=None).commit is None)
    check(VersionInfo(version="1", metadata=flake_metadata).commit == "abc123")
    check(VersionInfo(version="1", metadata={"commit": "def456"}).commit == "def456")
    check(VersionInfo(version="1", metadata={"commit": 1}).commit is None)
    check(
        VersionInfo(version="1", metadata=DownloadUrlMetadata(url="https://x")).commit
        is None
    )


def test_flake_input_metadata_validation_errors() -> None:
    """Reject malformed serialized flake metadata payloads."""
    with pytest.raises(TypeError, match="invalid node metadata"):
        FlakeInputMetadata.from_json({"node": "bad"})

    with pytest.raises(TypeError, match="invalid commit metadata"):
        FlakeInputMetadata.from_json({
            "node": {
                "locked": {
                    "type": "github",
                    "owner": "owner",
                    "repo": "repo",
                    "rev": "abc",
                    "narHash": "sha256-test",
                }
            },
            "commit": 1,
        })


def test_serialize_and_deserialize_metadata_paths() -> None:
    """Cover typed, legacy, passthrough, and error metadata serialization paths."""

    class _Model(BaseModel):
        value: str

    check(serialize_metadata(None) is None)
    check(serialize_metadata({"x": [1, 2]}) == {"x": [1, 2]})
    check(metadata_module._json_safe_value(_Model(value="x")) == {"value": "x"})
    check(
        metadata_module._json_safe_value(
            DownloadUrlMetadata(url="https://example.test")
        )
        == {"url": "https://example.test"}
    )

    release = ReleasePayloadMetadata(release={"version": "1.0.0"})
    serialized = serialize_metadata(release)
    check(isinstance(serialized, dict))
    serialized_map = serialized if isinstance(serialized, dict) else {}
    check(serialized_map["__kind__"] == "release_payload")

    with pytest.raises(TypeError, match="not JSON-serializable"):
        serialize_metadata(object())

    check(deserialize_metadata(None) is None)
    check(deserialize_metadata("raw") == "raw")
    check(deserialize_metadata({"plain": True}) == {"plain": True})
    check(deserialize_metadata({"__kind__": "none", "payload": {}}) is NO_METADATA)
    check(
        deserialize_metadata({
            "__kind__": "download_url",
            "payload": {"url": "https://example.test"},
        })["url"]
        == "https://example.test"
    )
    check(
        deserialize_metadata({
            "__kind__": "flake_input",
            "payload": {
                "node": {
                    "locked": {
                        "type": "github",
                        "owner": "owner",
                        "repo": "repo",
                        "rev": "abc",
                        "narHash": "sha256-test",
                    }
                }
            },
        })["node"]
        is not None
    )

    legacy = deserialize_metadata({
        "node": {
            "locked": {
                "type": "github",
                "owner": "owner",
                "repo": "repo",
                "rev": "abc",
                "narHash": "sha256-test",
            }
        }
    })
    check(isinstance(legacy, FlakeInputMetadata))

    with pytest.raises(TypeError, match="Unknown pinned version metadata kind"):
        deserialize_metadata({"__kind__": "unknown", "payload": {}})

    with pytest.raises(TypeError, match="payload must be an object"):
        deserialize_metadata({"__kind__": "download_url", "payload": "bad"})


def test_register_updater_skips_name_less_and_abstract_classes() -> None:
    """Avoid registering helper classes that are unnamed or abstract."""

    class _NoName:
        pass

    check(register_updater(_NoName) is _NoName)

    class _Abstract(ABC):
        name = "abstract-updater-test"

        @abstractmethod
        def method(self) -> None:
            raise NotImplementedError

    UPDATERS.pop("abstract-updater-test", None)
    check(register_updater(_Abstract) is _Abstract)
    check("abstract-updater-test" not in UPDATERS)
