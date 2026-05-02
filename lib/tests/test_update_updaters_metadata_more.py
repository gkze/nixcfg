"""Additional tests for updater metadata and registry helpers."""

from __future__ import annotations

from abc import ABC, abstractmethod

import pytest
from pydantic import BaseModel

from lib.nix.models.flake_lock import FlakeLockNode
from lib.update.updaters import metadata as metadata_module
from lib.update.updaters import registry as registry_module
from lib.update.updaters.base import FlakeInputMetadataUpdater
from lib.update.updaters.metadata import (
    NO_METADATA,
    DownloadUrlMetadata,
    FlakeInputMetadata,
    GranolaFeedMetadata,
    PlatformAPIMetadata,
    ReleasePayloadMetadata,
    VersionInfo,
    deserialize_metadata,
    serialize_metadata,
)
from lib.update.updaters.registry import (
    UPDATERS,
    is_test_updater_class,
    register_updater,
)


def test_mapping_metadata_helpers_and_version_info_commit_paths() -> None:
    """Expose typed metadata through dict-like helpers and commit accessors."""
    node = FlakeLockNode(locked=None)
    flake_metadata = FlakeInputMetadata(node=node, commit="abc123")
    assert flake_metadata["node"] is node
    assert flake_metadata.get("commit") == "abc123"
    assert "node" in flake_metadata
    assert metadata_module.metadata_as_mapping(flake_metadata, context="flake") == {
        "node": node,
        "commit": "abc123",
    }
    assert metadata_module.metadata_get(flake_metadata, "commit") == "abc123"
    assert metadata_module.metadata_get_str(flake_metadata, "commit") == "abc123"

    class _AttrMetadata:
        commit = "ghi789"

    assert metadata_module.metadata_get(_AttrMetadata(), "commit") == "ghi789"
    assert metadata_module.metadata_get_str(_AttrMetadata(), "commit") == "ghi789"
    assert metadata_module.metadata_get(_AttrMetadata(), "missing") is None

    platform_metadata = PlatformAPIMetadata(
        platform_info={"x86_64-linux": {"sha256hash": "x"}},
        equality_fields={"build": "2026-03-01"},
    )
    assert platform_metadata["build"] == "2026-03-01"
    assert "commit" not in platform_metadata

    assert (
        metadata_module.require_metadata_str(
            {"url": "https://example.test"},
            "url",
            context="download metadata",
        )
        == "https://example.test"
    )
    with pytest.raises(TypeError, match="Expected mapping metadata"):
        metadata_module.metadata_as_mapping(1, context="bad metadata")

    assert VersionInfo(version="1", metadata=None).commit is None
    assert VersionInfo(version="1", metadata=flake_metadata).commit == "abc123"
    assert VersionInfo(version="1", metadata={"commit": "def456"}).commit == "def456"
    assert VersionInfo(version="1", metadata={"commit": 1}).commit is None
    assert (
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


def test_typed_metadata_coercion_helpers() -> None:
    """Coerce legacy metadata mappings into typed runtime metadata objects."""
    node = FlakeLockNode(locked=None)

    assert FlakeInputMetadata.from_metadata(None, context="flake") is None
    assert FlakeInputMetadata.from_metadata(
        FlakeInputMetadata(node=node, commit="abc123"),
        context="flake metadata",
    ) == FlakeInputMetadata(node=node, commit="abc123")

    flake_metadata = FlakeInputMetadata.from_metadata(
        {"node": node, "commit": "abc123"},
        context="flake metadata",
    )
    assert isinstance(flake_metadata, FlakeInputMetadata)
    assert flake_metadata.node is node
    assert flake_metadata.commit == "abc123"

    with pytest.raises(TypeError, match="invalid commit metadata"):
        FlakeInputMetadata.from_metadata(
            {"node": node, "commit": 1},
            context="flake metadata",
        )

    serialized_flake_metadata = FlakeInputMetadata.from_metadata(
        {
            "node": {
                "locked": {
                    "type": "github",
                    "owner": "owner",
                    "repo": "repo",
                    "rev": "abc",
                    "narHash": "sha256-test",
                }
            },
            "commit": "abc",
        },
        context="flake metadata",
    )
    assert isinstance(serialized_flake_metadata, FlakeInputMetadata)
    assert serialized_flake_metadata.commit == "abc"

    platform_metadata = PlatformAPIMetadata.from_metadata(
        {
            "platform_info": {"x86_64-linux": {"sha256hash": "x"}},
            "build": "2026-03-01",
            "commit": "abc123",
        },
        context="dummy metadata",
    )
    assert platform_metadata.platform_info == {"x86_64-linux": {"sha256hash": "x"}}
    assert platform_metadata.equality_fields == {"build": "2026-03-01"}
    assert platform_metadata.commit == "abc123"

    with pytest.raises(TypeError, match="Expected flake lock node"):
        FlakeInputMetadata.from_metadata({"node": 1}, context="flake metadata")

    with pytest.raises(TypeError, match="Expected platform_info mapping"):
        PlatformAPIMetadata.from_metadata({}, context="dummy metadata")


def test_serialize_and_deserialize_metadata_paths() -> None:
    """Cover typed, legacy, passthrough, and error metadata serialization paths."""

    class _Model(BaseModel):
        value: str

    assert serialize_metadata(None) is None
    assert serialize_metadata({"x": [1, 2]}) == {"x": [1, 2]}
    assert metadata_module._json_safe_value(_Model(value="x")) == {"value": "x"}
    assert metadata_module._json_safe_value(
        DownloadUrlMetadata(url="https://example.test")
    ) == {"url": "https://example.test"}
    assert metadata_module._json_safe_value(
        GranolaFeedMetadata(path="Granola-mac.zip", sha512="deadbeef")
    ) == {"path": "Granola-mac.zip", "sha512": "deadbeef"}
    release = ReleasePayloadMetadata(release={"version": "1.0.0"})
    serialized = serialize_metadata(release)
    assert isinstance(serialized, dict)
    serialized_map = serialized if isinstance(serialized, dict) else {}
    assert serialized_map["__kind__"] == "release_payload"

    with pytest.raises(TypeError, match="not JSON-serializable"):
        serialize_metadata(object())

    assert deserialize_metadata(None) is None
    assert deserialize_metadata("raw") == "raw"
    assert deserialize_metadata({"plain": True}) == {"plain": True}
    assert deserialize_metadata({"__kind__": "none", "payload": {}}) is NO_METADATA
    assert (
        deserialize_metadata({
            "__kind__": "download_url",
            "payload": {"url": "https://example.test"},
        })["url"]
        == "https://example.test"
    )
    assert (
        deserialize_metadata({
            "__kind__": "granola_feed",
            "payload": {"path": "Granola-mac.zip", "sha512": "deadbeef"},
        })["sha512"]
        == "deadbeef"
    )
    assert (
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
    assert isinstance(legacy, FlakeInputMetadata)

    with pytest.raises(TypeError, match="Unknown pinned version metadata kind"):
        deserialize_metadata({"__kind__": "unknown", "payload": {}})

    with pytest.raises(TypeError, match="payload must be an object"):
        deserialize_metadata({"__kind__": "download_url", "payload": "bad"})

    with pytest.raises(TypeError, match="invalid platform_api metadata"):
        deserialize_metadata({"__kind__": "platform_api", "payload": {}})


def test_dataclass_payload_rejects_non_instances() -> None:
    """Reject non-dataclass values and dataclass classes."""

    class _NotDataclass:
        pass

    with pytest.raises(TypeError, match="Expected dataclass instance"):
        metadata_module._dataclass_payload(_NotDataclass())

    with pytest.raises(TypeError, match="Expected dataclass instance"):
        metadata_module._dataclass_payload(DownloadUrlMetadata)


def test_register_updater_skips_name_less_and_abstract_classes() -> None:
    """Avoid registering helper classes that are unnamed or abstract."""

    class _NoName:
        pass

    assert register_updater(_NoName) is _NoName

    class _Abstract(ABC):
        name = "abstract-updater-test"

        @abstractmethod
        def method(self) -> None:
            raise NotImplementedError

    UPDATERS.pop("abstract-updater-test", None)
    assert register_updater(_Abstract) is _Abstract
    assert "abstract-updater-test" not in UPDATERS


def test_register_updater_allows_test_duplicates_and_detects_test_classes() -> None:
    """Allow test-only duplicates to replace existing registrations safely."""

    class _Existing:
        __module__ = "lib.update.updaters.demo"
        name = "test-only-updater"

    class _Replacement:
        __module__ = "lib.tests.test_demo"
        name = "test-only-updater"

    UPDATERS["test-only-updater"] = _Existing

    assert is_test_updater_class(_Replacement) is True
    assert register_updater(_Replacement) is _Replacement
    assert UPDATERS["test-only-updater"] is _Replacement

    UPDATERS.pop("test-only-updater", None)


def test_registry_does_not_implicitly_materialize_crate2nix_targets() -> None:
    """Crate2nix materialization must be declared on the updater class."""
    original = registry_module.UPDATERS.get("codex")

    try:

        @registry_module.register_updater
        class _PlainCodexUpdater(FlakeInputMetadataUpdater):
            name = "codex"
            input_name = "codex"

        assert _PlainCodexUpdater.materialize_when_current is False
        assert _PlainCodexUpdater.shows_materialize_artifacts_phase is False
        assert not hasattr(_PlainCodexUpdater, "stream_materialized_artifacts")
    finally:
        if original is None:
            registry_module.UPDATERS.pop("codex", None)
        else:
            registry_module.UPDATERS["codex"] = original
