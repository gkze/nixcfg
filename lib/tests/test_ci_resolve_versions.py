"""Tests for resolve-versions CI helper."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from lib.update.ci import resolve_versions as resolve_versions_module
from lib.update.ci.resolve_versions import (
    _deserialize_version_info,
    _serialize_version_info,
    load_pinned_versions,
    run,
)
from lib.update.updaters.base import VersionInfo

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


_SENTINEL_MAIN_EXIT_CODE = 7


def test_serialize_roundtrip() -> None:
    """VersionInfo survives a serialize → deserialize roundtrip."""
    info = VersionInfo(
        version="1.2.3", metadata={"commit": "abc123", "nested": {"a": 1}}
    )
    data = _serialize_version_info(info)
    restored = _deserialize_version_info(data)
    assert restored.version == info.version
    assert restored.metadata == info.metadata


def test_serialize_empty_metadata() -> None:
    """VersionInfo with empty metadata round-trips correctly."""
    info = VersionInfo(version="0.0.1", metadata={})
    data = _serialize_version_info(info)
    restored = _deserialize_version_info(data)
    assert restored.version == "0.0.1"
    assert restored.metadata == {}


def test_serialize_null_metadata() -> None:
    """Null metadata round-trips correctly."""
    data = _serialize_version_info(VersionInfo(version="ignored"))
    restored = _deserialize_version_info(data)
    assert data["metadata"] is None
    assert restored.metadata is None


def test_deserialize_missing_metadata() -> None:
    """Deserialize tolerates missing metadata key (defaults to {})."""
    data = _serialize_version_info(VersionInfo(version="2.0.0", metadata={}))
    data.pop("metadata", None)
    info = _deserialize_version_info(data)
    assert info.version == "2.0.0"
    assert info.metadata == {}


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
    assert set(result.keys()) == {"google-chrome", "vscode-insiders"}
    assert result["google-chrome"].version == "145.0.7632.76"
    assert result["google-chrome"].metadata["commit"] == "abc"
    assert result["vscode-insiders"].version == "1.99.0"


def test_serialize_to_json_string() -> None:
    """Serialized VersionInfo is JSON-safe (round-trips through json.dumps/loads)."""
    info = VersionInfo(version="3.0.0", metadata={"key": [1, 2, 3]})
    data = _serialize_version_info(info)
    json_str = json.dumps(data)
    restored_data = json.loads(json_str)
    restored = _deserialize_version_info(restored_data)
    assert restored.version == info.version
    assert restored.metadata == info.metadata


def test_run_strict_fails_when_any_updater_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict mode fails when at least one updater resolution fails."""
    output = tmp_path / "pinned-versions.json"
    monkeypatch.setattr(resolve_versions_module, "load_all_sources", lambda: None)

    async def _fake_resolve_all() -> tuple[dict[str, object], list[str]]:
        await asyncio.sleep(0)
        return {"ok": {"version": "1", "metadata": {}}}, ["failed-updater"]

    monkeypatch.setattr(resolve_versions_module, "_resolve_all", _fake_resolve_all)

    rc = run(output=output)

    assert rc == 1
    assert not output.exists()


def test_main_uses_typer_parsing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Main delegates argument parsing to the Typer app."""
    output = tmp_path / "pinned-versions.json"
    called: dict[str, object] = {}

    def _fake_run(*, output: Path, strict: bool = False) -> int:
        called["output"] = output
        called["strict"] = strict
        return _SENTINEL_MAIN_EXIT_CODE

    monkeypatch.setattr(resolve_versions_module, "run", _fake_run)

    rc = resolve_versions_module.main(["--output", str(output), "--strict"])

    assert rc == _SENTINEL_MAIN_EXIT_CODE
    assert called["output"] == output
    assert called["strict"] is True
