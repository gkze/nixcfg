"""Additional tests for resolve-versions CI helper."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from pydantic import BaseModel

from lib.nix.models.flake_lock import FlakeLockNode
from lib.tests._assertions import check
from lib.update.ci import resolve_versions as rv
from lib.update.updaters.base import VersionInfo

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class _DemoModel(BaseModel):
    value: str


def test_make_json_safe_handles_models_and_tuples() -> None:
    """Convert nested models/tuples into JSON-safe structures."""
    payload = {
        "m": _DemoModel(value="ok"),
        "items": (1, "two", None),
    }

    result = object.__getattribute__(rv, "_make_json_safe")(payload)

    check(result == {"m": {"value": "ok"}, "items": [1, "two", None]})


def test_make_json_safe_rejects_unsupported_value() -> None:
    """Raise TypeError for values that cannot be serialized."""
    try:
        object.__getattribute__(rv, "_make_json_safe")({"bad": {1, 2}})
    except TypeError as exc:
        check("not JSON-serializable" in str(exc))
    else:
        raise AssertionError("expected TypeError")


def test_deserialize_validates_required_fields() -> None:
    """Reject malformed pinned-version entries."""
    deserialize = object.__getattribute__(rv, "_deserialize_version_info")

    try:
        deserialize({"version": 123, "metadata": {}})
    except TypeError as exc:
        check("missing string 'version'" in str(exc))
    else:
        raise AssertionError("expected TypeError for invalid version")

    try:
        deserialize({"version": "1.2.3", "metadata": []})
    except TypeError as exc:
        check("invalid 'metadata'" in str(exc))
    else:
        raise AssertionError("expected TypeError for invalid metadata")


def test_deserialize_rehydrates_flake_lock_node() -> None:
    """Rebuild flake lock node metadata into a typed model."""
    deserialize = object.__getattribute__(rv, "_deserialize_version_info")

    info = deserialize({
        "version": "1.2.3",
        "metadata": {
            "node": {
                "locked": {
                    "type": "github",
                    "owner": "sst",
                    "repo": "opencode",
                    "rev": "abc123",
                    "narHash": "sha256-test-hash",
                }
            }
        },
    })

    node_obj = info.metadata.get("node")
    check(isinstance(node_obj, FlakeLockNode))
    if not isinstance(node_obj, FlakeLockNode):
        raise AssertionError("expected FlakeLockNode metadata")
    check(node_obj.locked is not None)
    if node_obj.locked is None:
        raise AssertionError("expected locked flake metadata")
    check(node_obj.locked.owner == "sst")
    check(node_obj.locked.repo == "opencode")
    check(node_obj.locked.rev == "abc123")


def test_deserialize_rejects_invalid_node_metadata() -> None:
    """Reject pinned node payloads that fail flake lock validation."""
    deserialize = object.__getattribute__(rv, "_deserialize_version_info")

    try:
        deserialize({
            "version": "1.2.3",
            "metadata": {
                "node": {
                    "locked": {
                        "type": "github",
                        "owner": "sst",
                        "repo": "opencode",
                        # narHash is required
                    }
                }
            },
        })
    except TypeError as exc:
        check("invalid node metadata" in str(exc))
    else:
        raise AssertionError("expected TypeError for invalid node metadata")


def test_load_pinned_versions_validates_top_level_shape(tmp_path: Path) -> None:
    """Reject non-object payloads and non-object entries."""
    path = tmp_path / "pinned.json"

    path.write_text(json.dumps(["bad"]), encoding="utf-8")
    try:
        rv.load_pinned_versions(path)
    except TypeError as exc:
        check("must be a JSON object" in str(exc))
    else:
        raise AssertionError("expected TypeError for non-object payload")

    path.write_text(json.dumps({"pkg": "bad"}), encoding="utf-8")
    try:
        rv.load_pinned_versions(path)
    except TypeError as exc:
        check("Invalid pinned versions entry" in str(exc))
    else:
        raise AssertionError("expected TypeError for invalid entry")


def test_resolve_all_handles_fallback_init_and_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve all updaters and keep going when one fails."""

    class _Session:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class _NeedsConfig:
        def __init__(self, *, config: object) -> None:
            self.config = config

        async def fetch_latest(self, _session: object) -> VersionInfo:
            return VersionInfo(version="1.0.0", metadata={"cfg": bool(self.config)})

    class _NoConfigArg:
        def __init__(self) -> None:
            return None

        async def fetch_latest(self, _session: object) -> VersionInfo:
            return VersionInfo(version="2.0.0", metadata={})

    class _Fails:
        def __init__(self, *, config: object) -> None:
            self.config = config

        async def fetch_latest(self, _session: object) -> VersionInfo:
            msg = "boom"
            raise RuntimeError(msg)

    monkeypatch.setattr(rv.aiohttp, "ClientSession", _Session)
    monkeypatch.setattr(rv, "resolve_active_config", lambda _x: {"k": "v"})
    monkeypatch.setattr(
        rv,
        "UPDATERS",
        {
            "needs-config": _NeedsConfig,
            "no-config": _NoConfigArg,
            "fails": _Fails,
        },
    )

    results, failures = asyncio.run(object.__getattribute__(rv, "_resolve_all")())

    check(set(results) == {"needs-config", "no-config"})
    check(results["needs-config"]["version"] == "1.0.0")
    check(results["no-config"]["version"] == "2.0.0")
    check(failures == ["fails"])


def test_resolve_all_rejects_non_version_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raise TypeError when updater returns an unexpected payload."""

    class _Session:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class _BadUpdater:
        def __init__(self, *, config: object) -> None:
            self.config = config

        async def fetch_latest(self, _session: object) -> object:
            return {"version": "not-a-model"}

    monkeypatch.setattr(rv.aiohttp, "ClientSession", _Session)
    monkeypatch.setattr(rv, "resolve_active_config", lambda _x: {})
    monkeypatch.setattr(rv, "UPDATERS", {"bad": _BadUpdater})

    try:
        asyncio.run(object.__getattribute__(rv, "_resolve_all")())
    except TypeError as exc:
        check("unexpected version payload" in str(exc))
    else:
        raise AssertionError("expected TypeError")


def test_resolve_all_reraises_type_error_not_related_to_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not fallback-init when TypeError does not mention config."""

    class _Session:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class _BadInit:
        def __init__(self, *, config: object) -> None:
            _ = config
            msg = "totally unrelated"
            raise TypeError(msg)

    monkeypatch.setattr(rv.aiohttp, "ClientSession", _Session)
    monkeypatch.setattr(rv, "resolve_active_config", lambda _x: {})
    monkeypatch.setattr(rv, "UPDATERS", {"bad": _BadInit})

    try:
        asyncio.run(object.__getattribute__(rv, "_resolve_all")())
    except TypeError as exc:
        check("totally unrelated" in str(exc))
    else:
        raise AssertionError("expected TypeError")


def test_run_returns_error_when_no_versions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return non-zero when nothing resolved."""
    output = tmp_path / "pinned.json"
    monkeypatch.setattr(rv, "load_all_sources", lambda: None)

    async def _resolve_none() -> tuple[dict[str, object], list[str]]:
        return {}, []

    monkeypatch.setattr(rv, "_resolve_all", _resolve_none)

    rc = rv.run(output=output, strict=False)

    check(rc == 1)
    check(not output.exists())


def test_run_writes_manifest_when_non_strict_with_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Write output and succeed when strict mode is disabled."""
    output = tmp_path / "pinned.json"
    monkeypatch.setattr(rv, "load_all_sources", lambda: None)

    async def _resolve_some() -> tuple[dict[str, object], list[str]]:
        return {
            "pkg": {
                "version": "1.2.3",
                "metadata": {},
            }
        }, ["failed"]

    monkeypatch.setattr(rv, "_resolve_all", _resolve_some)

    wrote: dict[str, object] = {}

    def _write_json(path: Path, payload: object) -> None:
        wrote["path"] = path
        wrote["payload"] = payload

    monkeypatch.setattr(rv.update_io, "atomic_write_json", _write_json)

    rc = rv.run(output=output, strict=False)

    check(rc == 0)
    check(wrote["path"] == output)
    check(isinstance(wrote["payload"], dict))
