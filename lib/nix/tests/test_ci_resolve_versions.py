"""Tests for resolve-versions CI helper."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from lib.update.ci.resolve_versions import (
    _deserialize_version_info,
    _serialize_version_info,
    load_pinned_versions,
)
from lib.update.updaters.base import VersionInfo

if TYPE_CHECKING:
    from pathlib import Path


def test_serialize_roundtrip() -> None:
    """VersionInfo survives a serialize → deserialize roundtrip."""
    info = VersionInfo(
        version="1.2.3", metadata={"commit": "abc123", "nested": {"a": 1}}
    )
    data = _serialize_version_info(info)
    restored = _deserialize_version_info(data)
    assert restored.version == info.version  # noqa: S101
    assert restored.metadata == info.metadata  # noqa: S101


def test_serialize_empty_metadata() -> None:
    """VersionInfo with empty metadata round-trips correctly."""
    info = VersionInfo(version="0.0.1", metadata={})
    data = _serialize_version_info(info)
    restored = _deserialize_version_info(data)
    assert restored.version == "0.0.1"  # noqa: S101
    assert restored.metadata == {}  # noqa: S101


def test_deserialize_missing_metadata() -> None:
    """Deserialize tolerates missing metadata key (defaults to {})."""
    data = {"version": "2.0.0"}
    info = _deserialize_version_info(data)
    assert info.version == "2.0.0"  # noqa: S101
    assert info.metadata == {}  # noqa: S101


def test_load_pinned_versions(tmp_path: Path) -> None:
    """Load a pinned-versions.json manifest into a dict of VersionInfo."""
    manifest = {
        "google-chrome": {
            "version": "145.0.7632.76",
            "metadata": {"commit": "abc"},
        },
        "vscode-insiders": {
            "version": "1.99.0",
            "metadata": {"platform_info": {"aarch64-darwin": {"version": "1.99.0"}}},
        },
    }
    path = tmp_path / "pinned-versions.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    result = load_pinned_versions(path)
    assert set(result.keys()) == {"google-chrome", "vscode-insiders"}  # noqa: S101
    assert result["google-chrome"].version == "145.0.7632.76"  # noqa: S101
    assert result["google-chrome"].metadata["commit"] == "abc"  # noqa: S101
    assert result["vscode-insiders"].version == "1.99.0"  # noqa: S101


def test_serialize_to_json_string() -> None:
    """Serialized VersionInfo is JSON-safe (round-trips through json.dumps/loads)."""
    info = VersionInfo(version="3.0.0", metadata={"key": [1, 2, 3]})
    data = _serialize_version_info(info)
    json_str = json.dumps(data)
    restored_data = json.loads(json_str)
    restored = _deserialize_version_info(restored_data)
    assert restored.version == info.version  # noqa: S101
    assert restored.metadata == info.metadata  # noqa: S101
