"""Tests for the T3 Code workspace updater."""

from __future__ import annotations

from types import ModuleType, SimpleNamespace

import pytest

from lib.nix.models.sources import SourceEntry
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._updater_helpers import collect_events as _collect
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.events import UpdateEventKind
from lib.update.updaters.base import VersionInfo
from lib.update.updaters.core import UpdateContext

HASH = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


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
    assert updater_cls.native_only is True
    assert updater_cls.supported_platforms == ("aarch64-darwin",)


def test_t3code_workspace_fetch_hashes_uses_direct_helper_expr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hashing should bypass the missing public package attr and build the helper directly."""
    module = _load_module()
    updater = module.T3CodeWorkspaceUpdater()
    captured: dict[str, object] = {}

    async def _fake_compute(expr: str):
        captured["expr"] = expr
        return HASH

    monkeypatch.setattr(module, "_compute_workspace_hash", _fake_compute)

    events = _run(_collect(updater.fetch_hashes(VersionInfo(version="main"), object())))

    expr = captured["expr"]
    assert isinstance(expr, str)
    assert_nix_ast_equal(expr, module.T3CodeWorkspaceUpdater._workspace_expression())
    assert events[-1].kind is UpdateEventKind.VALUE
    payload = events[-1].payload
    assert isinstance(payload, list)
    assert len(payload) == 1
    hash_entry = payload[0]
    assert hash_entry.hash == HASH
    assert hash_entry.platform == "aarch64-darwin"


def test_t3code_workspace_build_args_wrap_expr_for_nix_build() -> None:
    """Workspace builds should route through the shared nix expression wrapper."""
    module = _load_module()

    args = module._workspace_build_args("pkgs.hello")

    assert args[:6] == ["nix", "build", "-L", "--no-link", "--impure", "--expr"]
    assert_nix_ast_equal(args[-1], module._build_nix_expr("pkgs.hello"))


def test_t3code_workspace_compute_hash_rejects_successful_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful fixed-output build means the fake-hash probe was bypassed."""
    module = _load_module()

    async def _fake_to_thread(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module.asyncio, "to_thread", _fake_to_thread)

    with pytest.raises(RuntimeError, match="Expected nix build to fail"):
        _run(module._compute_workspace_hash("pkgs.hello"))


def test_t3code_workspace_compute_hash_requires_hash_mismatch_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The helper should fail clearly when Nix output lacks a parseable hash."""
    module = _load_module()

    async def _fake_to_thread(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(returncode=1, stdout="stdout", stderr="stderr")

    monkeypatch.setattr(module.asyncio, "to_thread", _fake_to_thread)
    monkeypatch.setattr(module.HashMismatchError, "from_output", lambda *_args: None)

    with pytest.raises(RuntimeError, match="Could not find hash in nix output"):
        _run(module._compute_workspace_hash("pkgs.hello"))


def test_t3code_workspace_compute_hash_returns_sri_hash_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Already-SRI hashes should be returned without conversion."""
    module = _load_module()

    async def _fake_to_thread(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(returncode=1, stdout="", stderr="hash mismatch")

    monkeypatch.setattr(module.asyncio, "to_thread", _fake_to_thread)
    monkeypatch.setattr(
        module.HashMismatchError,
        "from_output",
        lambda *_args: SimpleNamespace(is_sri=True, hash=HASH),
    )

    assert _run(module._compute_workspace_hash("pkgs.hello")) == HASH


def test_t3code_workspace_compute_hash_converts_non_sri_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-SRI hashes should be normalized through the mismatch helper."""
    module = _load_module()

    async def _fake_to_thread(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(returncode=1, stdout="", stderr="hash mismatch")

    class _Mismatch:
        is_sri = False

        async def to_sri(self) -> str:
            return HASH

    monkeypatch.setattr(module.asyncio, "to_thread", _fake_to_thread)
    monkeypatch.setattr(
        module.HashMismatchError, "from_output", lambda *_args: _Mismatch()
    )

    assert _run(module._compute_workspace_hash("pkgs.hello")) == HASH


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
