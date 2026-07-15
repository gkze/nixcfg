"""Tests for the T3 Code workspace updater."""

from __future__ import annotations

from types import ModuleType
from typing import TYPE_CHECKING

from lib.nix.models.sources import SourceEntry
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._updater_helpers import collect_events as _collect
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.events import UpdateEvent, UpdateEventKind
from lib.update.updaters import VersionInfo
from lib.update.updaters.core import UpdateContext

if TYPE_CHECKING:
    import pytest

HASH = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
NEW_HASH = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="


def _load_module() -> ModuleType:
    return load_repo_module(
        "packages/t3code-workspace/updater.py", "t3code_workspace_updater_test"
    )


def _source_entry(*, drv_hash: str | None = None) -> SourceEntry:
    payload: dict[str, object] = {
        "input": "t3code",
        "version": "main",
        "hashes": [
            {
                "hashType": "nodeModulesHash",
                "hash": HASH,
                "platform": "aarch64-darwin",
            }
        ],
    }
    if drv_hash is not None:
        payload["drvHash"] = drv_hash
    return SourceEntry.model_validate(payload)


def test_t3code_workspace_updater_tracks_only_aarch64_darwin() -> None:
    """The helper package should only run on its single supported platform."""
    updater_cls = _load_module().T3CodeWorkspaceUpdater

    assert updater_cls.input_name == "t3code"
    assert updater_cls.hash_type == "nodeModulesHash"
    assert updater_cls.platform_specific is True
    assert updater_cls.materialize_when_current is True
    assert updater_cls.native_only is True
    assert updater_cls.supported_platforms == ("aarch64-darwin",)


def test_t3code_workspace_fetch_hashes_uses_shared_fixed_output_hash_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hash the direct helper expression through the shared retrying Nix probe."""
    module = _load_module()
    updater = module.T3CodeWorkspaceUpdater()
    captured: dict[str, object] = {}

    async def _fake_compute(
        source: str,
        expr: str,
        *,
        env: dict[str, str],
        config: object,
    ):
        captured["source"] = source
        captured["expr"] = expr
        captured["env"] = env
        captured["config"] = config
        yield UpdateEvent.status(source, "retrying")
        yield UpdateEvent.value(source, HASH)

    monkeypatch.setattr(module, "compute_fixed_output_hash", _fake_compute)

    events = _run(_collect(updater.fetch_hashes(VersionInfo(version="main"), object())))

    assert captured["source"] == updater.name
    expr = captured["expr"]
    assert isinstance(expr, str)
    assert_nix_ast_equal(expr, module.T3CodeWorkspaceUpdater._workspace_expression())
    assert captured["env"] == {"FAKE_HASHES": "1"}
    assert captured["config"] is updater.config
    assert events[0].message == "retrying"
    assert events[-1].kind is UpdateEventKind.VALUE
    payload = events[-1].payload
    assert isinstance(payload, list)
    assert len(payload) == 1
    hash_entry = payload[0]
    assert hash_entry.hash == HASH
    assert hash_entry.platform == "aarch64-darwin"


def test_t3code_workspace_is_latest_uses_direct_fingerprint_expr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Latest checks should fingerprint the helper derivation directly."""
    module = _load_module()
    updater = module.T3CodeWorkspaceUpdater()
    current = SourceEntry.model_validate({
        "version": "main",
        "drvHash": "abc123",
        "hashes": [
            {
                "hashType": "nodeModulesHash",
                "hash": HASH,
                "platform": "aarch64-darwin",
            }
        ],
    })

    captured: dict[str, object] = {}

    async def _fake_fingerprint(source: str, expr: str, *, config: object):
        captured.update({"source": source, "expr": expr, "config": config})
        return "abc123"

    monkeypatch.setattr(module, "compute_expr_drv_fingerprint", _fake_fingerprint)

    assert _run(updater._is_latest(current, VersionInfo(version="main"))) is True
    assert captured["source"] == "t3code-workspace"
    assert "./packages/t3code-workspace/default.nix" in captured["expr"]


def test_t3code_workspace_is_latest_rejects_missing_metadata() -> None:
    """Latest checks should short-circuit when version or drv hash is absent."""
    updater = _load_module().T3CodeWorkspaceUpdater()

    assert _run(updater._is_latest(None, VersionInfo(version="main"))) is False
    assert (
        _run(updater._is_latest(_source_entry(), VersionInfo(version="other"))) is False
    )


def test_t3code_workspace_is_latest_records_context_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful latest checks should reuse the computed fingerprint later in the run."""
    module = _load_module()
    updater = module.T3CodeWorkspaceUpdater()
    context = UpdateContext(current=_source_entry(drv_hash="abc123"))

    async def _fake_fingerprint(*_args: object, **_kwargs: object) -> str:
        return "abc123"

    monkeypatch.setattr(module, "compute_expr_drv_fingerprint", _fake_fingerprint)

    assert _run(updater._is_latest(context, VersionInfo(version="main"))) is True
    assert context.drv_fingerprint == "abc123"


def test_t3code_workspace_rechecks_node_modules_when_drv_fingerprint_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A matching drvHash must not hide stale workspace nodeModulesHash data."""
    module = _load_module()
    updater = module.T3CodeWorkspaceUpdater()
    captured: dict[str, object] = {}

    async def _fake_fingerprint(source: str, expr: str, *, config: object) -> str:
        captured.update({"fingerprint_source": source, "expr": expr, "config": config})
        return "abc123"

    async def _fake_compute(
        source: str,
        expr: str,
        *,
        env: dict[str, str],
        config: object,
    ):
        captured.update({
            "hash_source": source,
            "hash_expr": expr,
            "hash_env": env,
            "hash_config": config,
        })
        yield UpdateEvent.value(source, NEW_HASH)

    monkeypatch.setattr(module, "compute_expr_drv_fingerprint", _fake_fingerprint)
    monkeypatch.setattr(module, "compute_fixed_output_hash", _fake_compute)
    monkeypatch.setattr(
        "lib.update.nix.get_current_nix_platform",
        lambda: "aarch64-darwin",
    )

    events = _run(
        _collect(
            updater.update_stream(
                _source_entry(drv_hash="abc123"),
                object(),
                pinned_version=VersionInfo(version="main"),
            )
        )
    )

    result_payloads = [
        event.payload
        for event in events
        if event.kind is UpdateEventKind.RESULT and event.payload is not None
    ]
    assert len(result_payloads) == 1
    result = result_payloads[0]
    assert isinstance(result, SourceEntry)
    assert result.drv_hash == "abc123"
    assert result.hashes.entries[0].hash == NEW_HASH
    assert captured["fingerprint_source"] == "t3code-workspace"
    assert captured["hash_source"] == "t3code-workspace"
    assert captured["hash_expr"] == captured["expr"]
    assert captured["hash_env"] == {"FAKE_HASHES": "1"}
    assert captured["hash_config"] is updater.config


def test_t3code_workspace_finalize_result_uses_cached_context_fingerprint() -> None:
    """Finalize should attach the precomputed drv fingerprint without recomputing it."""
    updater = _load_module().T3CodeWorkspaceUpdater()
    context = UpdateContext(current=None, drv_fingerprint="abc123")

    events = _run(_collect(updater._finalize_result(_source_entry(), context=context)))

    assert events[0].kind is UpdateEventKind.STATUS
    assert events[-1].kind is UpdateEventKind.VALUE
    assert events[-1].payload.drv_hash == "abc123"


def test_t3code_workspace_finalize_result_computes_missing_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finalize should compute a drv fingerprint when the run context lacks one."""
    module = _load_module()
    updater = module.T3CodeWorkspaceUpdater()

    async def _fake_fingerprint(*_args: object, **_kwargs: object) -> str:
        return "abc123"

    monkeypatch.setattr(module, "compute_expr_drv_fingerprint", _fake_fingerprint)

    events = _run(_collect(updater._finalize_result(_source_entry())))

    assert events[-1].kind is UpdateEventKind.VALUE
    assert events[-1].payload.drv_hash == "abc123"


def test_t3code_workspace_finalize_result_warns_when_fingerprint_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finalize should preserve the result when drv fingerprinting is unavailable."""
    module = _load_module()
    updater = module.T3CodeWorkspaceUpdater()

    async def _fake_fingerprint(*_args: object, **_kwargs: object) -> str:
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(module, "compute_expr_drv_fingerprint", _fake_fingerprint)

    events = _run(_collect(updater._finalize_result(_source_entry())))

    assert events[1].kind is UpdateEventKind.STATUS
    assert "Warning: derivation fingerprint unavailable (boom)" in events[1].message
    assert events[-1].kind is UpdateEventKind.VALUE
    assert events[-1].payload.drv_hash is None
