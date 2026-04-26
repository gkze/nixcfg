"""Additional tests for resolve-versions CI helper."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel

from lib.nix.models.flake_lock import FlakeLockNode
from lib.update.ci import resolve_versions as rv
from lib.update.updaters.base import VersionInfo

if TYPE_CHECKING:
    from pathlib import Path


class _DemoModel(BaseModel):
    value: str


def test_make_json_safe_handles_models_and_tuples() -> None:
    """Convert nested models/tuples into JSON-safe structures."""
    payload = {
        "m": _DemoModel(value="ok"),
        "items": (1, "two", None),
    }

    result = object.__getattribute__(rv, "_make_json_safe")(payload)

    assert result == {"m": {"value": "ok"}, "items": [1, "two", None]}


def test_make_json_safe_rejects_unsupported_value() -> None:
    """Raise TypeError for values that cannot be serialized."""
    with pytest.raises(TypeError, match="not JSON-serializable"):
        object.__getattribute__(rv, "_make_json_safe")({"bad": {1, 2}})


def test_deserialize_validates_required_fields() -> None:
    """Reject malformed pinned-version entries."""
    deserialize = object.__getattribute__(rv, "_deserialize_version_info")

    with pytest.raises(TypeError, match="missing string 'version'"):
        deserialize({"version": 123, "metadata": {}})

    with pytest.raises(TypeError, match="invalid 'metadata'"):
        deserialize({"version": "1.2.3", "metadata": []})


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
    assert isinstance(node_obj, FlakeLockNode)
    if not isinstance(node_obj, FlakeLockNode):
        raise AssertionError("expected FlakeLockNode metadata")
    assert node_obj.locked is not None
    if node_obj.locked is None:
        raise AssertionError("expected locked flake metadata")
    assert node_obj.locked.owner == "sst"
    assert node_obj.locked.repo == "opencode"
    assert node_obj.locked.rev == "abc123"


def test_deserialize_rejects_invalid_node_metadata() -> None:
    """Reject pinned node payloads that fail flake lock validation."""
    deserialize = object.__getattribute__(rv, "_deserialize_version_info")

    with pytest.raises(TypeError, match="invalid node metadata"):
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


def test_load_pinned_versions_validates_top_level_shape(tmp_path: Path) -> None:
    """Reject non-object payloads and non-object entries."""
    path = tmp_path / "pinned.json"

    path.write_text(json.dumps(["bad"]), encoding="utf-8")
    with pytest.raises(TypeError, match="must be a JSON object"):
        rv.load_pinned_versions(path)

    path.write_text(json.dumps({"pkg": "bad"}), encoding="utf-8")
    with pytest.raises(TypeError, match="Invalid pinned versions entry"):
        rv.load_pinned_versions(path)


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

    class _Companion:
        companion_of = "needs-config"

        async def fetch_latest(self, _session: object) -> VersionInfo:
            raise AssertionError("companion versions resolve during source waves")

    monkeypatch.setattr(rv.aiohttp, "ClientSession", _Session)
    monkeypatch.setattr(rv, "resolve_active_config", lambda _x: {"k": "v"})
    monkeypatch.setattr(
        rv,
        "UPDATERS",
        {
            "needs-config": _NeedsConfig,
            "no-config": _NoConfigArg,
            "fails": _Fails,
            "companion": _Companion,
        },
    )

    results, failures = asyncio.run(object.__getattribute__(rv, "_resolve_all")())

    assert set(results) == {"needs-config", "no-config"}
    assert results["needs-config"]["version"] == "1.0.0"
    assert results["no-config"]["version"] == "2.0.0"
    assert failures == ["fails"]


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

    with pytest.raises(TypeError, match="unexpected version payload"):
        asyncio.run(object.__getattribute__(rv, "_resolve_all")())


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

    with pytest.raises(TypeError, match="totally unrelated"):
        asyncio.run(object.__getattribute__(rv, "_resolve_all")())


def test_resolve_all_reraises_multiple_taskgroup_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve grouped failures when multiple updaters error concurrently."""

    class _Session:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class _BadOne:
        def __init__(self, *, config: object) -> None:
            self.config = config

        async def fetch_latest(self, _session: object) -> object:
            return {"version": "bad-one"}

    class _BadTwo:
        def __init__(self, *, config: object) -> None:
            self.config = config

        async def fetch_latest(self, _session: object) -> object:
            return {"version": "bad-two"}

    monkeypatch.setattr(rv.aiohttp, "ClientSession", _Session)
    monkeypatch.setattr(rv, "resolve_active_config", lambda _x: {})
    monkeypatch.setattr(rv, "UPDATERS", {"one": _BadOne, "two": _BadTwo})

    with pytest.raises(ExceptionGroup) as excinfo:
        asyncio.run(object.__getattribute__(rv, "_resolve_all")())

    messages = {str(exc) for exc in excinfo.value.exceptions}
    assert messages == {
        "unexpected version payload for one: <class 'dict'>",
        "unexpected version payload for two: <class 'dict'>",
    }


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

    assert rc == 1
    assert not output.exists()


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

    assert rc == 0
    assert wrote["path"] == output
    assert isinstance(wrote["payload"], dict)


def test_main_accepts_allow_partial(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Expose partial-manifest mode explicitly via CLI parsing."""
    output = tmp_path / "pinned.json"
    called: dict[str, object] = {}

    def _fake_run(*, output: Path, strict: bool = True) -> int:
        called["output"] = output
        called["strict"] = strict
        return 0

    monkeypatch.setattr(rv, "run", _fake_run)

    rc = rv.main(["--output", str(output), "--allow-partial"])

    assert rc == 0
    assert called["output"] == output
    assert called["strict"] is False
